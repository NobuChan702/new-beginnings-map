(function () {
  "use strict";

  const CFG = Object.assign(
    {
      minZoom: 0,
      overzoomLevels: 3,
      politicalOpaqueUntil: 2,
      politicalFadeEnd: 4.25,
      politicalTintOpacity: 0.28,
      outlinesFadeStart: 2.75,
      outlinesFadeEnd: 4.5,
      outlineOpacity: 0.9,
      provinceLabelMinZoom: 5,
      flatFadeStartOffset: 0,
      flatFadeEndOffset: 1,
    },
    window.NB_CONFIG || {}
  );

  const state = {
    meta: null,
    lut: null,
    nations: [],
    nationsById: new Map(),
    provinces: new Map(),
    claims: new Map(),
    mode: "auto",
    overlays: { borders: true, claims: true, occupied: true, markers: true, infra: true, ids: false },
    labelLayer: null,
    map: null,
    layers: {},
    highlightLayer: null,
    highlightUrl: null,
    claimLayer: null,
    claimUrl: null,
    claimMarkers: null,
    infraLayer: null,
    flatLayer: null,
    flatRenderer: null,
    flatBorderOn: null,
    flatCollection: null,
    flatById: null,
    historySnapshots: [],
    historyIndex: 0,
    historyView: null,
    historyRenderer: null,
    fogRenderer: null,
    changeLayer: null,
    occLayer: null,
    workLayer: null,
    workMarkers: null,
    fogLayer: null,
    battleLayer: null,
    infraRaw: null,
    historyStyled: null,
    markLayer: null,
    marksRenderer: null,
    imageBounds: null,
    activeNationId: null,
  };

  const highlightCanvas = document.createElement("canvas");
  const claimCanvas = document.createElement("canvas");

  function setStatus(text, isError) {
    const el = document.getElementById("status");
    if (!text) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = text;
    el.style.borderColor = isError ? "#BC0501" : "rgba(230,230,234,0.35)";
  }

  function headerOffset() {
    const header = document.querySelector("header");
    const height = header ? header.offsetHeight : 58;
    document.documentElement.style.setProperty("--header-h", height + "px");
    return height;
  }

  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function fmtArea(n) {
    return Math.round(Number(n) || 0).toLocaleString("en-US") + "\u00a0km\u00b2";
  }

  function labelTier(tier) {
    if (!tier) return "";
    if (String(tier).toUpperCase() === "NPC") return "NPC";
    const text = String(tier);
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function labelTerrain(name) {
    const text = String(name || "unknown").replace(/_/g, " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function labelWater(value) {
    if (value === "shallow") return "Shallow";
    if (value === "mid") return "Mid";
    if (value === "deep") return "Deep";
    return value ? String(value) : "Unknown";
  }

  function formatPct(value) {
    const n = Number(value);
    if (!isFinite(n)) return "";
    if (Math.abs(n - Math.round(n)) < 0.05) return String(Math.round(n));
    return n.toFixed(1);
  }

  function safeColor(color) {
    return /^#[0-9a-fA-F]{6}$/.test(color || "") ? color : "#E6E6EA";
  }

  function hexToRgb(color) {
    const hex = safeColor(color).slice(1);
    return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
  }

  function smoothstep(t) {
    const x = Math.min(1, Math.max(0, t));
    return x * x * (3 - 2 * x);
  }

  function fade(zoom, start, end, from, to) {
    if (zoom <= start) return from;
    if (zoom >= end) return to;
    const t = smoothstep((zoom - start) / (end - start));
    return from + (to - from) * t;
  }

  function politicalOpacity(zoom) {
    return fade(zoom, CFG.politicalOpaqueUntil, CFG.politicalFadeEnd, 1, CFG.politicalTintOpacity);
  }

  function outlineOpacity(zoom) {
    return fade(zoom, CFG.outlinesFadeStart, CFG.outlinesFadeEnd, 0, CFG.outlineOpacity);
  }

  function zoomCeiling(meta) {
    const extra = Number(CFG.overzoomLevels);
    const levels = Number.isFinite(extra) ? extra : 3;
    return meta.native_zoom + levels;
  }

  function flatAmount(zoom) {
    if (!state.flatLayer || !state.meta) return 0;
    const native = state.meta.native_zoom;
    const start = native + Number(CFG.flatFadeStartOffset || 0);
    const endOffset = CFG.flatFadeEndOffset == null ? 1 : Number(CFG.flatFadeEndOffset);
    const end = Math.max(start + 0.05, native + endOffset);
    return fade(zoom, start, end, 0, 1);
  }

  function flatStyle(feature) {
    const props = (feature && feature.properties) || {};
    const kind = props.kind;
    const borders = state.overlays.borders;
    if (kind === "sea" || kind === "fog") {
      return { stroke: false, fill: true, fillColor: props.color || "#0B1A4A", fillOpacity: 1, interactive: false };
    }
    if (kind === "province") {
      const look = historyLook(props.id);
      if (look.fog) {
        return { stroke: false, fill: true, fillColor: fogColor(), fillOpacity: 1, interactive: false };
      }
      return {
        stroke: !!borders,
        color: look.stroke || "#16140f",
        weight: borders ? look.weight || 1 : 0,
        opacity: 0.95,
        dashArray: look.dash || null,
        lineJoin: "round",
        fill: true,
        fillColor: look.fill || props.color || "#8d8678",
        fillOpacity: 1,
        interactive: false,
      };
    }
    return {
      stroke: !!borders,
      color: "#E6E6EA",
      weight: borders ? 2.5 : 0,
      opacity: 1,
      lineJoin: "round",
      lineCap: "round",
      fill: false,
      interactive: false,
    };
  }

  function setOn(layer, on) {
    const map = state.map;
    if (!layer || !map) return;
    const has = map.hasLayer(layer);
    if (on && !has) layer.addTo(map);
    else if (!on && has) map.removeLayer(layer);
  }

  function stackRasters() {
    const map = state.map;
    const order = ["depth", "terrain", "political", "outlines"];
    order.forEach(function (name) {
      const layer = state.layers[name];
      if (layer && map.hasLayer(layer)) layer.bringToFront();
    });
  }

  function showPhoto(layer, opacity) {
    if (!layer) return;
    if (opacity <= 0.01) {
      // Leave an already-mounted photo in place at opacity 0 so zooming back
      // does not wait on tiles, and so the flat board is what the player sees.
      if (state.map.hasLayer(layer)) layer.setOpacity(0);
      return;
    }
    setOn(layer, true);
    layer.setOpacity(opacity);
  }

  function applyMode() {
    const map = state.map;
    if (!map) return;
    const zoom = map.getZoom();
    const borders = state.overlays.borders;
    const flat = flatAmount(zoom);
    const photo = 1 - flat;
    const political = state.layers.political;
    const terrain = state.layers.terrain;
    const depth = state.layers.depth;
    const outlines = state.layers.outlines;
    if (state.mode === "political") {
      showPhoto(political, photo);
      setOn(terrain, false);
      setOn(depth, false);
      showPhoto(outlines, borders ? CFG.outlineOpacity * photo : 0);
    } else if (state.mode === "terrain") {
      showPhoto(terrain, photo);
      setOn(political, false);
      setOn(depth, false);
      showPhoto(outlines, borders ? CFG.outlineOpacity * photo : 0);
    } else if (state.mode === "depth") {
      showPhoto(depth, photo);
      setOn(political, false);
      setOn(terrain, false);
      showPhoto(outlines, borders ? CFG.outlineOpacity * 0.7 * photo : 0);
    } else {
      showPhoto(terrain, photo);
      showPhoto(political, politicalOpacity(zoom) * photo);
      setOn(depth, false);
      const amount = outlineOpacity(zoom) * photo;
      showPhoto(outlines, borders ? amount : 0);
    }
    stackRasters();
    if (state.claimLayer && map.hasLayer(state.claimLayer)) state.claimLayer.setOpacity(photo);
    if (state.highlightLayer && map.hasLayer(state.highlightLayer)) state.highlightLayer.setOpacity(photo);
    const pane = map.getPane("flat");
    if (pane) pane.style.opacity = flat > 0.02 ? String(flat) : "0";
    setOn(state.flatLayer, flat > 0.02);
    const marks = map.getPane("marks");
    if (marks) marks.style.opacity = flat > 0.02 ? String(flat) : "0";
    setOn(state.markLayer, state.overlays.claims && flat > 0.02);
    if (state.overlays.claims && flat > 0.02) installHatchPattern();
    if (state.flatLayer && state.flatBorderOn !== borders) {
      state.flatLayer.setStyle(flatStyle);
      state.flatBorderOn = borders;
      state.historyStyled = null;
      restyleFlatForHistory();
    }
    refreshHistoryZoom();
    document.getElementById("depth-legend").hidden = state.mode !== "depth" || flat > 0.98;
  }

  function syncClaimVisibility() {
    const map = state.map;
    if (!map) return;
    if (state.overlays.claims) {
      if (state.claimLayer && !map.hasLayer(state.claimLayer)) state.claimLayer.addTo(map);
      if (state.claimMarkers && !map.hasLayer(state.claimMarkers)) state.claimMarkers.addTo(map);
    } else {
      if (state.claimLayer && map.hasLayer(state.claimLayer)) map.removeLayer(state.claimLayer);
      if (state.claimMarkers && map.hasLayer(state.claimMarkers)) map.removeLayer(state.claimMarkers);
    }
  }

  function syncInfraVisibility() {
    const map = state.map;
    if (!map || !state.infraLayer) return;
    setOn(state.infraLayer, state.overlays.infra);
  }

  function makeTileLayer(folder, errorTile) {
    const meta = state.meta;
    return L.tileLayer("tiles/" + folder + "/{z}/{x}/{y}.webp", {
      tileSize: meta.tile_size,
      noWrap: true,
      minZoom: CFG.minZoom,
      maxZoom: zoomCeiling(meta),
      maxNativeZoom: meta.native_zoom,
      className: "nb-raster",
      bounds: state.imageBounds,
      errorTileUrl: errorTile,
      updateWhenZooming: false,
      keepBuffer: 2,
    });
  }

  function initMap(meta) {
    state.meta = meta;
    state.imageBounds = L.latLngBounds([0, 0], [meta.height, meta.width]);
    const maxZoom = zoomCeiling(meta);
    // Keep infinite: true. Leaflet's Simple CRS uses a longitude/latitude
    // projection whose world is only -180..180. With infinite turned off,
    // every tile of this 12000px sheet is rejected and the map stays blank.
    const crs = L.extend({}, L.CRS.Simple, {
      transformation: new L.Transformation(1, 0, 1, 0),
      scale: function (zoom) {
        return Math.pow(2, zoom - meta.native_zoom);
      },
      zoom: function (scale) {
        return Math.log(scale) / Math.LN2 + meta.native_zoom;
      },
      distance: function (a, b) {
        const dx = b.lng - a.lng;
        const dy = b.lat - a.lat;
        return Math.sqrt(dx * dx + dy * dy) * meta.km_per_px * 1000;
      },
      infinite: true,
    });
    const map = L.map("map", {
      crs: crs,
      minZoom: CFG.minZoom,
      maxZoom: maxZoom,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 80,
      attributionControl: false,
      zoomControl: true,
      maxBounds: state.imageBounds.pad(0.15),
      maxBoundsViscosity: 0.85,
      fadeAnimation: true,
      zoomAnimation: true,
      inertia: true,
    });
    state.map = map;
    window.nbMap = map;
    map.createPane("flat");
    const flatPane = map.getPane("flat");
    flatPane.style.zIndex = "350";
    flatPane.style.pointerEvents = "none";
    flatPane.style.opacity = "0";
    state.flatRenderer = L.canvas({ pane: "flat", padding: 0.5 });
    map.createPane("history");
    const historyPane = map.getPane("history");
    historyPane.style.zIndex = "380";
    historyPane.style.pointerEvents = "none";
    state.historyRenderer = L.svg({ pane: "history", padding: 0.75 });
    map.createPane("fog");
    const fogPane = map.getPane("fog");
    fogPane.style.zIndex = "480";
    fogPane.style.pointerEvents = "none";
    state.fogRenderer = L.svg({ pane: "fog", padding: 0.5 });
    map.createPane("marks");
    const marksPane = map.getPane("marks");
    marksPane.style.zIndex = "360";
    marksPane.style.pointerEvents = "none";
    marksPane.style.opacity = "0";
    state.marksRenderer = L.svg({ pane: "marks", padding: 0.5 });
    map.zoomControl.setPosition("topleft");
    state.layers.terrain = makeTileLayer("terrain", "assets/empty.webp");
    state.layers.political = makeTileLayer("political", "assets/empty.webp");
    state.layers.depth = makeTileLayer("depth", "assets/empty.webp");
    state.layers.outlines = makeTileLayer("outlines", "assets/blank.webp");
    L.control.scale({ imperial: false, metric: true, position: "bottomright", maxWidth: 160 }).addTo(map);
    installMinimap(map, meta);
    installLayers(map);
    state.labelLayer = L.layerGroup();
    map.on("zoom", applyMode);
    map.on("moveend zoomend", refreshProvinceLabels);
    map.fitBounds(state.imageBounds, {
      paddingTopLeft: [12, headerOffset() + 8],
      paddingBottomRight: [12, 12],
      animate: false,
    });
    applyMode();
    headerOffset();
    window.addEventListener("resize", function () {
      headerOffset();
      map.invalidateSize();
    });
  }

  function installLayers(map) {
    const Control = L.Control.extend({
      options: { position: "topright" },
      onAdd: function () {
        const el = L.DomUtil.create("div", "nb-layers");
        el.innerHTML =
          '<button type="button" class="nb-layers-toggle" aria-expanded="false" aria-controls="layer-panel">Layers</button>' +
          '<div id="layer-panel" class="nb-layers-panel" hidden>' +
          "<p>View</p>" +
          '<label><input type="radio" name="nb-view" value="auto" checked> Auto</label>' +
          '<label><input type="radio" name="nb-view" value="political"> Political</label>' +
          '<label><input type="radio" name="nb-view" value="terrain"> Terrain</label>' +
          '<label><input type="radio" name="nb-view" value="depth"> Depth</label>' +
          "<p>Overlays</p>" +
          '<label><input type="checkbox" data-overlay="borders" checked> Province borders</label>' +
          '<label><input type="checkbox" data-overlay="claims" checked> Claimed nations</label>' +
          '<label><input type="checkbox" data-overlay="occupied" checked> Occupied territory</label>' +
          '<label><input type="checkbox" data-overlay="markers" checked> Battles &amp; armies</label>' +
          '<label><input type="checkbox" data-overlay="infra" checked> Roads and places</label>' +
          '<label><input type="checkbox" data-overlay="ids"> Province IDs</label>' +
          "</div>";
        L.DomEvent.disableClickPropagation(el);
        L.DomEvent.disableScrollPropagation(el);
        const panel = el.querySelector(".nb-layers-panel");
        const button = el.querySelector(".nb-layers-toggle");
        button.addEventListener("click", function () {
          const open = panel.hasAttribute("hidden");
          if (open) panel.removeAttribute("hidden");
          else panel.setAttribute("hidden", "");
          button.setAttribute("aria-expanded", String(open));
        });
        el.querySelectorAll('input[name="nb-view"]').forEach(function (input) {
          input.addEventListener("change", function () {
            if (!input.checked) return;
            state.mode = input.value;
            applyMode();
          });
        });
        el.querySelectorAll("input[data-overlay]").forEach(function (input) {
          input.addEventListener("change", function () {
            state.overlays[input.dataset.overlay] = input.checked;
            applyMode();
            syncClaimVisibility();
            syncHistoryOverlays();
            syncInfraVisibility();
            refreshProvinceLabels();
          });
        });
        return el;
      },
    });
    new Control().addTo(map);
  }

  function installMinimap(map, meta) {
    const Control = L.Control.extend({
      options: { position: "topright" },
      onAdd: function () {
        const root = L.DomUtil.create("div", "nb-minimap");
        root.innerHTML =
          '<button type="button" class="nb-mini-toggle" aria-expanded="true">Overview</button>' +
          '<div class="nb-mini-frame" aria-label="Overview map. Click or drag to move the main map.">' +
          '<img alt="" draggable="false" src="' + esc(meta.overview || "assets/overview.webp") + '">' +
          '<div class="nb-mini-rect"></div>' +
          "</div>";
        L.DomEvent.disableClickPropagation(root);
        L.DomEvent.disableScrollPropagation(root);
        const frame = root.querySelector(".nb-mini-frame");
        const rect = root.querySelector(".nb-mini-rect");
        const button = root.querySelector(".nb-mini-toggle");
        const image = root.querySelector("img");
        frame.style.aspectRatio = meta.width + " / " + meta.height;

        function collapsed(next) {
          root.classList.toggle("is-collapsed", next);
          button.setAttribute("aria-expanded", String(!next));
          frame.setAttribute("aria-hidden", String(next));
          if (!next) requestAnimationFrame(updateRect);
        }
        if (window.matchMedia("(max-width: 720px)").matches) collapsed(true);
        button.addEventListener("click", function (event) {
          L.DomEvent.stop(event);
          collapsed(!root.classList.contains("is-collapsed"));
        });

        function updateRect() {
          if (root.classList.contains("is-collapsed")) return;
          const fw = frame.clientWidth;
          const fh = frame.clientHeight;
          if (!fw || !fh) return;
          const bounds = map.getBounds();
          const west = bounds.getWest();
          const east = bounds.getEast();
          const south = bounds.getSouth();
          const north = bounds.getNorth();
          let left = (west / meta.width) * fw;
          let top = (south / meta.height) * fh;
          let width = ((east - west) / meta.width) * fw;
          let height = ((north - south) / meta.height) * fh;
          if (width < 8) {
            left -= (8 - width) / 2;
            width = 8;
          }
          if (height < 8) {
            top -= (8 - height) / 2;
            height = 8;
          }
          left = Math.max(0, Math.min(left, fw - 4));
          top = Math.max(0, Math.min(top, fh - 4));
          width = Math.max(4, Math.min(width, fw - left));
          height = Math.max(4, Math.min(height, fh - top));
          rect.style.left = left + "px";
          rect.style.top = top + "px";
          rect.style.width = width + "px";
          rect.style.height = height + "px";
        }

        function eventToWorld(event) {
          const box = frame.getBoundingClientRect();
          const u = (event.clientX - box.left) / box.width;
          const v = (event.clientY - box.top) / box.height;
          return {
            x: Math.min(meta.width, Math.max(0, u * meta.width)),
            y: Math.min(meta.height, Math.max(0, v * meta.height)),
          };
        }

        let drag = null;
        frame.addEventListener("pointerdown", function (event) {
          if (root.classList.contains("is-collapsed")) return;
          const world = eventToWorld(event);
          const center = map.getCenter();
          drag = {
            id: event.pointerId,
            sx: event.clientX,
            sy: event.clientY,
            start: world,
            center: { x: center.lng, y: center.lat },
            onRect: event.target === rect,
            moved: false,
          };
          frame.setPointerCapture(event.pointerId);
          event.preventDefault();
        });
        frame.addEventListener("pointermove", function (event) {
          if (!drag || event.pointerId !== drag.id) return;
          const dx = event.clientX - drag.sx;
          const dy = event.clientY - drag.sy;
          if (dx * dx + dy * dy > 9) drag.moved = true;
          if (!drag.moved) return;
          const world = eventToWorld(event);
          const nx = drag.center.x + (world.x - drag.start.x);
          const ny = drag.center.y + (world.y - drag.start.y);
          map.panTo([ny, nx], { animate: false });
        });
        function endDrag(event) {
          if (!drag || (event && event.pointerId !== drag.id)) return;
          const info = drag;
          drag = null;
          if (!info.moved && !info.onRect) {
            map.panTo([info.start.y, info.start.x], { animate: true });
          }
        }
        frame.addEventListener("pointerup", endDrag);
        frame.addEventListener("pointercancel", endDrag);
        map.on("move zoom resize", updateRect);
        image.addEventListener("load", updateRect);
        requestAnimationFrame(updateRect);
        return root;
      },
    });
    new Control().addTo(map);
  }

  function provinceAt(x, y) {
    const meta = state.meta;
    const lut = state.lut;
    if (!lut || x < meta.hidden_west_of || y < 0 || x >= meta.width || y >= meta.height) return 0;
    const lx = Math.floor(x / meta.lookup_scale);
    const ly = Math.floor(y / meta.lookup_scale);
    if (lx < 0 || ly < 0 || lx >= meta.lookup_width || ly >= meta.lookup_height) return 0;
    if (lx * meta.lookup_scale < meta.hidden_west_of) return 0;
    const id = lut[ly * meta.lookup_width + lx] || 0;
    return state.provinces.has(id) ? id : 0;
  }

  function paintMask(canvas, ids, rgbaFor) {
    const lut = state.lut;
    const width = canvas.width;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const image = ctx.createImageData(width, canvas.height);
    const data = image.data;
    const flag = new Uint8Array(65536);
    ids.forEach(function (id) {
      if (id > 0 && id < flag.length) flag[id] = 1;
    });
    for (let i = 0; i < lut.length; i++) {
      const id = lut[i];
      if (!id || !flag[id]) continue;
      const color = rgbaFor(i, width);
      const offset = i * 4;
      data[offset] = color[0];
      data[offset + 1] = color[1];
      data[offset + 2] = color[2];
      data[offset + 3] = color[3];
    }
    ctx.putImageData(image, 0, 0);
  }

  function ensureOverlay(kind, canvas) {
    return new Promise(function (resolve) {
      canvas.toBlob(function (blob) {
        if (!blob) {
          resolve();
          return;
        }
        const url = URL.createObjectURL(blob);
        if (kind === "highlight") {
          if (!state.highlightLayer) {
            state.highlightLayer = L.imageOverlay(url, state.imageBounds, { interactive: false, opacity: 1, zIndex: 3 });
            state.highlightLayer.addTo(state.map);
          } else {
            state.highlightLayer.setUrl(url);
            if (!state.map.hasLayer(state.highlightLayer)) state.highlightLayer.addTo(state.map);
          }
          if (state.highlightUrl) URL.revokeObjectURL(state.highlightUrl);
          state.highlightUrl = url;
        } else {
          if (!state.claimLayer) {
            state.claimLayer = L.imageOverlay(url, state.imageBounds, { interactive: false, opacity: 1, zIndex: 2 });
          } else {
            state.claimLayer.setUrl(url);
          }
          if (state.claimUrl) URL.revokeObjectURL(state.claimUrl);
          state.claimUrl = url;
          syncClaimVisibility();
        }
        resolve();
      }, "image/png");
    });
  }

  function showHighlight(ids, rgb) {
    ids = (ids || []).filter(function (id) {
      return !isConcealed(id);
    });
    if (!state.lut || !ids.length) return;
    highlightCanvas.width = state.meta.lookup_width;
    highlightCanvas.height = state.meta.lookup_height;
    paintMask(highlightCanvas, ids, function () {
      return [rgb[0], rgb[1], rgb[2], 110];
    });
    ensureOverlay("highlight", highlightCanvas);
  }

  function clearHighlight() {
    state.activeNationId = null;
    if (state.highlightLayer && state.map.hasLayer(state.highlightLayer)) {
      state.map.removeLayer(state.highlightLayer);
    }
  }

  function paintClaims() {
    const ids = [];
    state.nations.forEach(function (nation) {
      if (!state.claims.has(String(nation.id))) return;
      nation.provinces.forEach(function (id) {
        if (!isConcealed(id)) ids.push(id);
      });
    });
    claimCanvas.width = state.meta.lookup_width;
    claimCanvas.height = state.meta.lookup_height;
    if (!ids.length) {
      if (state.claimLayer && state.map.hasLayer(state.claimLayer)) state.map.removeLayer(state.claimLayer);
      return;
    }
    const width = claimCanvas.width;
    paintMask(claimCanvas, ids, function (index) {
      const x = index % width;
      const y = (index / width) | 0;
      const stripe = ((x + y) & 7) < 3;
      return [188, 5, 1, stripe ? 150 : 40];
    });
    ensureOverlay("claims", claimCanvas);
  }

  function nationPoint(nation) {
    if (nation.capital_xy && nation.capital_xy.length === 2) return nation.capital_xy;
    const box = nation.bbox;
    return [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2];
  }

  function provinceFields(prov) {
    const mix = (prov.mix || [])
      .map(function (pair) {
        return labelTerrain(pair[0]) + " " + formatPct(pair[1]) + "%";
      })
      .join(", ");
    const water = prov.water ? labelWater(prov.water) : prov.coastal ? "Unknown" : "None";
    return (
      "<dl><dt>Terrain</dt><dd>" +
      esc(mix || labelTerrain(prov.terrain)) +
      "</dd><dt>River</dt><dd>" +
      (prov.river ? "Yes" : "No") +
      "</dd><dt>Coast</dt><dd>" +
      (prov.coastal ? "Yes" : "No") +
      "</dd><dt>Nearby water</dt><dd>" +
      esc(water) +
      "</dd></dl>"
    );
  }

  function claimOf(nation) {
    if (!nation) return null;
    return state.claims.get(String(nation.id)) || null;
  }

  function nationLabel(nation) {
    if (!nation) return "";
    const claim = claimOf(nation);
    if (!claim) return nation.short_name;
    return nation.short_name + " (" + claim.short + ")";
  }

  function nationHtml(nation, provinceId) {
    const prov = state.provinces.get(provinceId) || state.provinces.get(nation.capital_province);
    const claim = claimOf(nation);
    return (
      '<div class="nb-pop"><h2><i style="background:' +
      esc(safeColor(nation.color)) +
      '"></i>' +
      esc(nationLabel(nation)) +
      '</h2><p class="tier">' +
      esc(labelTier(nation.tier)) +
      '</p><p class="meta">' +
      nation.province_count +
      " provinces · " +
      fmtArea(nation.area_km2) +
      '</p><p class="meta">Capital: Province ' +
      nation.capital_province +
      "</p>" +
(claim ? '<div class="claim">Claimed by ' + esc(claim.player) + "</div>" : "") +
      colonyLine(provinceId) +
      occupationSentence(provinceId) +
      (prov
        ? '<hr><p class="meta">Province ' +
          (state.provinces.has(provinceId) ? provinceId : nation.capital_province) +
          " · " +
          fmtArea(prov.area_km2) +
          "</p>" +
          provinceFields(prov)
        : "") +
      "</div>"
    );
  }

  function unclaimedHtml(id, prov) {
    return (
      '<div class="nb-pop"><h2>Unclaimed</h2><p class="meta">Province ' +
      id +
      " · " +
      fmtArea(prov.area_km2) +
      "</p>" +
      colonyLine(id) +
      occupationSentence(id) +
      provinceFields(prov) +
      "</div>"
    );
  }

  function openPopup(latlng, html) {
    const pad = headerOffset();
    L.popup({
      className: "nb-popup",
      maxWidth: 320,
      autoPanPaddingTopLeft: L.point(16, pad + 12),
      autoPanPaddingBottomRight: L.point(16, 78),
    })
      .setLatLng(latlng)
      .setContent(html)
      .openOn(state.map);
  }

  function setNationHash(nation) {
    if (nation) {
      const next = "#nation=" + encodeURIComponent(nation.short_name);
      if (location.hash !== next) history.replaceState(null, "", next);
    } else if (location.hash.indexOf("#nation=") === 0) {
      history.replaceState(null, "", location.pathname + location.search);
    }
  }

  function flyToBox(box, maxZoom) {
    const bounds = L.latLngBounds([box[1], box[0]], [box[3], box[2]]);
    const mini = document.querySelector(".nb-minimap");
    const right = mini && !mini.classList.contains("is-collapsed") ? mini.offsetWidth + 24 : 20;
    state.map.fitBounds(bounds, {
      paddingTopLeft: [20, headerOffset() + 12],
      paddingBottomRight: [right, 84],
      maxZoom: maxZoom,
      animate: true,
    });
  }

  function flyToNation(nation) {
    flyToBox(nation.bbox, 5);
  }

  function provinceIsPublic(id, prov) {
    if (!prov || !state.meta) return false;
    if (prov.bbox && prov.bbox[0] < state.meta.hidden_west_of) return false;
    if (prov.xy && prov.xy[0] < state.meta.hidden_west_of) return false;
    return state.provinces.has(id);
  }

  function provinceFill(prov) {
    if (prov.nation_id && state.nationsById.has(prov.nation_id)) {
      return hexToRgb(state.nationsById.get(prov.nation_id).color);
    }
    return [230, 230, 234];
  }

  function selectProvince(id, options) {
    options = options || {};
    const prov = state.provinces.get(id);
    if (!provinceIsPublic(id, prov) || !prov.bbox || !prov.xy || isConcealed(id)) return;
    const colony = colonyOf(id);
    const owner = legalOwnerId(id, prov);
    const nation = colony
      ? state.nationsById.get(colony.owner)
      : owner && state.nationsById.get(owner);
    showHighlight([id], nation ? hexToRgb(nation.color) : provinceFill(prov));
    setNationHash(null);
    if (options.fly) flyToBox(prov.bbox, 6);
    if (options.popup) {
      const latlng = L.latLng(prov.xy[1], prov.xy[0]);
      openPopup(latlng, nation ? nationHtml(nation, id) : unclaimedHtml(id, prov));
    }
  }

  function refreshProvinceLabels() {
    const map = state.map;
    if (!map || !state.labelLayer) return;
    state.labelLayer.clearLayers();
    const enabled = state.overlays.ids && map.getZoom() >= (CFG.provinceLabelMinZoom || 5);
    if (!enabled) {
      if (map.hasLayer(state.labelLayer)) map.removeLayer(state.labelLayer);
      return;
    }
    if (!map.hasLayer(state.labelLayer)) state.labelLayer.addTo(map);
    const bounds = map.getBounds();
    const west = bounds.getWest();
    const east = bounds.getEast();
    const south = bounds.getSouth();
    const north = bounds.getNorth();
    let drawn = 0;
    state.provinces.forEach(function (prov, id) {
      if (drawn >= 180) return;
      if (!provinceIsPublic(id, prov) || !prov.xy || !prov.bbox) return;
      const box = prov.bbox;
      if (box[2] < west || box[0] > east || box[3] < south || box[1] > north) return;
      const label = String(id);
      const width = Math.max(22, label.length * 8 + 8);
      const marker = L.marker([prov.xy[1], prov.xy[0]], {
        icon: L.divIcon({
          className: "prov-id-label",
          html: "<span>" + label + "</span>",
          iconSize: [width, 16],
          iconAnchor: [Math.round(width / 2), 8],
        }),
        interactive: false,
        keyboard: false,
      });
      marker.addTo(state.labelLayer);
      drawn += 1;
    });
    const works = (state.historyView && state.historyView.works) || [];
    works.forEach(function (work) {
      if (drawn >= 180) return;
      const box = work.bbox;
      if (!box || box[2] < west || box[0] > east || box[3] < south || box[1] > north) return;
      const label = String(work.id);
      const width = Math.max(22, label.length * 8 + 8);
      L.marker([work.centroid[1], work.centroid[0]], {
        icon: L.divIcon({
          className: "prov-id-label",
          html: "<span>" + label + "</span>",
          iconSize: [width, 16],
          iconAnchor: [Math.round(width / 2), 8],
        }),
        interactive: false,
        keyboard: false,
      }).addTo(state.labelLayer);
      drawn += 1;
    });
  }

  function selectNation(nation, options) {
    options = options || {};
    state.activeNationId = nation.id;
    showHighlight(nation.provinces, hexToRgb(nation.color));
    if (options.hash !== false) setNationHash(nation);
    if (options.fly) flyToNation(nation);
    if (options.popup) {
      const xy = options.latlng
        ? [options.latlng.lng, options.latlng.lat]
        : nationPoint(nation);
      const latlng = options.latlng || L.latLng(xy[1], xy[0]);
      const provinceId = options.provinceId || provinceAt(latlng.lng, latlng.lat) || nation.capital_province;
      openPopup(latlng, nationHtml(nation, provinceId));
    }
  }

  function nationAliases(nation) {
    const names = [nation.short_name, nation.name].concat(nation.former_names || []);
    const claim = claimOf(nation);
    if (claim) names.push(claim.short, claim.player, nationLabel(nation));
    return names;
  }

  function findNation(token) {
    let raw = token;
    try {
      raw = decodeURIComponent(token);
    } catch (err) {
      raw = token;
    }
    raw = String(raw || "").trim();
    if (!raw) return null;
    if (/^\d+$/.test(raw) && state.nationsById.has(Number(raw))) return state.nationsById.get(Number(raw));
    const query = raw.toLowerCase();
    return (
      state.nations.find(function (nation) {
        return nationAliases(nation).some(function (name) {
          return String(name).toLowerCase() === query;
        });
      }) || null
    );
  }

  function nationFromHash() {
    const match = location.hash.match(/^#nation=(.+)$/);
    if (!match) return null;
    return findNation(match[1]);
  }

  function openHash(fly) {
    const nation = nationFromHash();
    if (!nation) return;
    const input = document.getElementById("nation-search");
    input.value = nationLabel(nation);
    selectNation(nation, { fly: fly !== false, popup: true, hash: false });
  }

  function setupSearch() {
    const input = document.getElementById("nation-search");
    const results = document.getElementById("search-results");
    let active = 0;
    let current = [];

    function hide() {
      results.hidden = true;
      results.innerHTML = "";
      current = [];
    }

    function render(list) {
      results.innerHTML = "";
      current = list;
      active = 0;
      if (!list.length) {
        results.hidden = true;
        return;
      }
      list.forEach(function (entry, index) {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        if (entry.kind === "province") {
          button.textContent = "Province " + entry.id;
          const tier = document.createElement("span");
          tier.className = "tier";
tier.textContent = provinceSearchLabel(entry.id, entry.prov);
          button.appendChild(tier);
        } else if (entry.kind === "work") {
          button.textContent = entry.work.id + " · " + entry.work.name;
          const tier = document.createElement("span");
          tier.className = "tier";
          tier.textContent = workTypeLabel(entry.work.type);
          button.appendChild(tier);
        } else {
          button.textContent = nationLabel(entry.nation);
          const tier = document.createElement("span");
          tier.className = "tier";
          tier.textContent = labelTier(entry.nation.tier);
          button.appendChild(tier);
          if (state.claims.has(String(entry.nation.id))) {
            const taken = document.createElement("span");
            taken.className = "taken";
            taken.textContent = "Taken";
            button.appendChild(taken);
          }
        }
        if (index === 0) button.classList.add("is-active");
        button.addEventListener("click", function () {
          choose(entry);
        });
        item.appendChild(button);
        results.appendChild(item);
      });
      results.hidden = false;
    }

    function move(delta) {
      if (!current.length) return;
      active = (active + delta + current.length) % current.length;
      Array.prototype.forEach.call(results.querySelectorAll("button"), function (button, index) {
        button.classList.toggle("is-active", index === active);
      });
      const chosen = results.querySelectorAll("button")[active];
      if (chosen) chosen.scrollIntoView({ block: "nearest" });
    }

    function choose(entry) {
      hide();
      if (entry.kind === "province") {
        input.value = String(entry.id);
        selectProvince(entry.id, { fly: true, popup: true });
        return;
      }
if (entry.kind === "work") {
        input.value = String(entry.work.id);
        showWork(entry.work, entry.index);
        return;
      }
      input.value = nationLabel(entry.nation);
      selectNation(entry.nation, { fly: true, popup: true, provinceId: entry.nation.capital_province });
    }

    input.addEventListener("input", function () {
      const query = input.value.trim().toLowerCase();
      if (!query) {
        hide();
        return;
      }
      const items = [];
      if (/^\d+$/.test(query)) {
        const id = Number(query);
        if (id >= window.NB_HISTORY.WORK_ID_MIN) {
          const found = findWorkAny(id);
          if (found) items.push({ kind: "work", work: found.work, index: found.index });
        } else {
          if (state.nationsById.has(id)) items.push({ kind: "nation", nation: state.nationsById.get(id) });
          const prov = state.provinces.get(id);
          if (provinceIsPublic(id, prov) && !isConcealed(id)) items.push({ kind: "province", id: id, prov: prov });
        }
      }
      const pinned = [];
      const starts = [];
      const rest = [];
      state.nations.forEach(function (nation) {
        const name = nation.short_name.toLowerCase();
        const aliases = nationAliases(nation).map(function (alias) {
          return String(alias).toLowerCase();
        });
        const id = String(nation.id);
        if (id === query) return;
        if (!aliases.some(function (alias) { return alias.includes(query); })) return;
        const entry = { kind: "nation", nation: nation };
        const exactFormer = (nation.former_names || []).some(function (alias) {
          return String(alias).toLowerCase() === query;
        });
        if (exactFormer || name === query) pinned.push(entry);
        else if (name.startsWith(query) || aliases.some(function (alias) { return alias.startsWith(query); })) starts.push(entry);
        else rest.push(entry);
      });
      render(items.concat(pinned, starts, rest).slice(0, 8));
    });

    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        move(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        move(-1);
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (current[active]) choose(current[active]);
        else if (current[0]) choose(current[0]);
      } else if (event.key === "Escape") {
        hide();
      }
    });

    document.addEventListener("click", function (event) {
      if (!event.target.closest(".search")) hide();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "/" && document.activeElement !== input) {
        event.preventDefault();
        input.focus();
      }
    });
  }

  function setupInfo() {
    const panel = document.getElementById("info");
    const button = document.getElementById("info-toggle");
    if (window.matchMedia("(max-width: 720px)").matches) {
      panel.classList.add("is-collapsed");
      button.setAttribute("aria-expanded", "false");
    }
    button.addEventListener("click", function () {
      const collapsed = panel.classList.toggle("is-collapsed");
      button.setAttribute("aria-expanded", String(!collapsed));
    });
  }

  function onMapClick(event) {
    const meta = state.meta;
    const x = event.latlng.lng;
    const y = event.latlng.lat;
    if (x < meta.hidden_west_of) {
      clearHighlight();
      setNationHash(null);
      openPopup(event.latlng, unexploredHtml(true));
      return;
    }
    if (concealedSeaAt(x, y)) {
      clearHighlight();
      setNationHash(null);
      openPopup(event.latlng, unexploredHtml(false));
      return;
    }
    const id = state.lut ? provinceAt(x, y) : 0;
    if (id && isConcealed(id)) {
      clearHighlight();
      setNationHash(null);
      openPopup(event.latlng, unexploredHtml(false));
      return;
    }
    const work = workAt(x, y);
    if (work) {
      clearHighlight();
      setNationHash(null);
      openPopup(event.latlng, workHtml(work));
      return;
    }
    if (!id) {
      state.map.closePopup();
      return;
    }
    const prov = state.provinces.get(id);
    const colony = colonyOf(id);
    const transferred = hasOwnerOverride(id);
    if (colony || transferred) {
      state.activeNationId = null;
      setNationHash(null);
      const ownerId = colony ? colony.owner : legalOwnerId(id, prov);
      const nation = ownerId && state.nationsById.get(ownerId);
      showHighlight([id], nation ? hexToRgb(nation.color) : [230, 230, 234]);
      openPopup(event.latlng, nation ? nationHtml(nation, id) : unclaimedHtml(id, prov));
      return;
    }
    if (prov && prov.nation_id && state.nationsById.has(prov.nation_id)) {
      const nation = state.nationsById.get(prov.nation_id);
      selectNation(nation, { fly: false, popup: true, latlng: event.latlng, provinceId: id });
      return;
    }
    state.activeNationId = null;
    setNationHash(null);
    showHighlight([id], [230, 230, 234]);
    openPopup(event.latlng, unclaimedHtml(id, prov));
  }

  function indexWorld(world) {
    state.nations = (world.nations || []).slice().sort(function (a, b) {
      return a.short_name.localeCompare(b.short_name);
    });
    state.nationsById = new Map();
    state.nations.forEach(function (nation) {
      state.nationsById.set(nation.id, nation);
    });
    state.provinces = new Map();
    Object.keys(world.provinces || {}).forEach(function (key) {
      state.provinces.set(Number(key), world.provinces[key]);
    });
  }

  function playerShort(value) {
    const text = String(value || "").trim();
    const stripped = text.replace(/\s*\([^)]*\)\s*$/, "").trim();
    return stripped || text;
  }

  function normalizeClaims(raw) {
    const out = new Map();
    if (!raw || typeof raw !== "object") return out;
    Object.keys(raw).forEach(function (key) {
      const value = raw[key];
      let player = "";
      let shortName = "";
      if (typeof value === "string") {
        player = value.trim();
      } else if (value && typeof value === "object") {
        player = String(value.player || value.name || "").trim();
        shortName = String(value.short || "").trim();
      }
      if (!player) return;
      if (!shortName) shortName = playerShort(player);
      out.set(String(key), { player: player, short: shortName });
    });
    return out;
  }

  function applyClaims() {
    if (state.claimMarkers) {
      state.map.removeLayer(state.claimMarkers);
    }
    state.claimMarkers = L.layerGroup();
    state.nations.forEach(function (nation) {
const claim = claimOf(nation);
      if (!claim || isConcealed(nation.capital_province)) return;
      const xy = nationPoint(nation);
      const label = nationLabel(nation);
      const width = Math.max(48, Math.ceil(label.length * 7.4) + 18);
      const marker = L.marker([xy[1], xy[0]], {
        icon: L.divIcon({
          className: "claim-marker",
          html: "<span>" + esc(label) + "</span>",
          iconSize: [width, 18],
          iconAnchor: [Math.round(width / 2), 9],
        }),
        keyboard: true,
        title: "Claimed by " + claim.player,
        zIndexOffset: 400,
      });
      marker.bindTooltip("Claimed by " + claim.player, { direction: "top", className: "nb-tip" });
      marker.on("click", function (event) {
        L.DomEvent.stopPropagation(event);
        selectNation(nation, { fly: false, popup: true, latlng: event.latlng, provinceId: nation.capital_province });
      });
      marker.addTo(state.claimMarkers);
    });
    paintClaims();
    syncClaimVisibility();
    rebuildMarks();
  }

  function installHatchPattern() {
    const svg = document.querySelector(".leaflet-marks-pane svg");
    if (!svg || svg.querySelector("#nb-claim-hatch")) return;
    const ns = "http://www.w3.org/2000/svg";
    const defs = document.createElementNS(ns, "defs");
    const pattern = document.createElementNS(ns, "pattern");
    pattern.setAttribute("id", "nb-claim-hatch");
    pattern.setAttribute("patternUnits", "userSpaceOnUse");
    pattern.setAttribute("width", "8");
    pattern.setAttribute("height", "8");
    pattern.setAttribute("patternTransform", "rotate(45)");
    const wash = document.createElementNS(ns, "rect");
    wash.setAttribute("width", "8");
    wash.setAttribute("height", "8");
    wash.setAttribute("fill", "rgba(188,5,1,0.16)");
    const stripe = document.createElementNS(ns, "path");
    stripe.setAttribute("d", "M0 0 H8");
    stripe.setAttribute("stroke", "#BC0501");
    stripe.setAttribute("stroke-width", "3");
    pattern.appendChild(wash);
    pattern.appendChild(stripe);
    defs.appendChild(pattern);
    svg.insertBefore(defs, svg.firstChild);
  }

  function rebuildMarks() {
    const map = state.map;
    if (!map) return;
    if (state.markLayer) {
      map.removeLayer(state.markLayer);
      state.markLayer = null;
    }
    if (!state.flatCollection) return;
    const claimed = new Set();
    state.claims.forEach(function (_name, id) {
      const nation = state.nationsById.get(Number(id));
      if (nation) claimed.add(nation.id);
    });
    if (!claimed.size) return;
    const features = [];
    state.flatCollection.features.forEach(function (feature) {
      const props = feature.properties || {};
      if (props.kind === "province" && claimed.has(props.nation_id) && !isConcealed(props.id)) {
        features.push({ type: "Feature", properties: { role: "claim" }, geometry: feature.geometry });
      }
    });
    if (!features.length) return;
    state.markLayer = L.geoJSON(
      { type: "FeatureCollection", features: features },
      {
        pane: "marks",
        renderer: state.marksRenderer,
        smoothFactor: 0,
        interactive: false,
        bubblingMouseEvents: false,
        style: function () {
          return {
            stroke: false,
            fill: true,
            fillColor: "url(#nb-claim-hatch)",
            fillOpacity: 1,
            interactive: false,
          };
        },
      }
    );
  }

  function kindLabel(kind) {
    if (kind === "road") return "Road";
    if (kind === "port") return "Port";
    if (kind === "fort") return "Fort";
    if (kind === "trade_route") return "Trade route";
    if (kind === "supply_route") return "Supply route";
    return kind ? String(kind) : "Place";
  }

  function applyFlat(collection) {
    if (!collection || collection.type !== "FeatureCollection" || !collection.features || !state.map) return;
    state.flatCollection = collection;
    if (state.flatLayer) state.map.removeLayer(state.flatLayer);
    state.flatLayer = L.geoJSON(collection, {
      pane: "flat",
      renderer: state.flatRenderer,
      smoothFactor: 0,
      interactive: false,
      bubblingMouseEvents: false,
      style: flatStyle,
    });
    state.flatBorderOn = state.overlays.borders;
    state.flatById = new Map();
    state.flatLayer.eachLayer(function (layer) {
      const id = layer.feature && layer.feature.properties && layer.feature.properties.id;
      if (id) state.flatById.set(id, layer);
    });
    rebuildMarks();
    renderHistory();
    restyleFlatForHistory();
    applyMode();
  }

  function applyInfra(collection) {
    if (collection) state.infraRaw = collection;
    const raw = state.infraRaw && state.infraRaw.type === "FeatureCollection" ? state.infraRaw : { type: "FeatureCollection", features: [] };
    const data = {
      type: "FeatureCollection",
      features: (raw.features || []).filter(function (feature) {
        return infraVisible(feature && feature.properties);
      }),
    };
    if (state.infraLayer && state.map) state.map.removeLayer(state.infraLayer);
    state.infraLayer = L.geoJSON(data, {
      renderer: L.svg({ padding: 0.5 }),
      smoothFactor: 0,
      style: function (feature) {
        const kind = feature.properties && feature.properties.kind;
        if (kind === "trade_route") return { color: "#E6E6EA", weight: 2.5, opacity: 0.95, dashArray: "8 6", lineCap: "butt" };
        if (kind === "supply_route") return { color: "#BC0501", weight: 2.5, opacity: 0.95, dashArray: "2 7", lineCap: "butt" };
        if (kind === "fort") return { color: "#BC0501", weight: 2, fillColor: "#0B1A4A", fillOpacity: 1 };
        return { color: "#E6E6EA", weight: 2.5, opacity: 0.95, lineJoin: "round", lineCap: "round" };
      },
      pointToLayer: function (feature, latlng) {
        const kind = feature.properties && feature.properties.kind;
        return L.circleMarker(latlng, {
          radius: kind === "fort" ? 7 : 6,
          weight: 2,
          color: kind === "fort" ? "#BC0501" : "#E6E6EA",
          fillColor: "#0B1A4A",
          fillOpacity: 1,
        });
      },
      onEachFeature: function (feature, layer) {
        const props = feature.properties || {};
        const name = props.name || kindLabel(props.kind);
        layer.bindPopup(
          '<div class="nb-pop"><h2>' + esc(name) + '</h2><p class="meta">' + esc(kindLabel(props.kind)) + "</p></div>",
          { className: "nb-popup", maxWidth: 280 }
        );
      },
    });
    syncInfraVisibility();
  }

  function loadLookup(meta) {
    return new Promise(function (resolve, reject) {
      const image = new Image();
      image.onload = function () {
        if (image.width !== meta.lookup_width || image.height !== meta.lookup_height) {
          reject(new Error("Province lookup size does not match the map."));
          return;
        }
        const canvas = document.createElement("canvas");
        canvas.width = image.width;
        canvas.height = image.height;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(image, 0, 0);
        const pixels = ctx.getImageData(0, 0, image.width, image.height).data;
        const lut = new Uint16Array(image.width * image.height);
        for (let i = 0, p = 0; i < pixels.length; i += 4, p++) {
          lut[p] = pixels[i] + (pixels[i + 1] << 8);
        }
        resolve(lut);
      };
      image.onerror = function () {
        reject(new Error("Province lookup failed to load."));
      };
      image.src = "data/lookup.png";
    });
  }

  function nationColor(id) {
    const nation = id && state.nationsById.get(id);
    return nation ? safeColor(nation.color) : "#E6E6EA";
  }

  function lighten(color, amount) {
    const rgb = hexToRgb(color);
    const mixed = rgb.map(function (channel) {
      return Math.round(channel + (255 - channel) * amount);
    });
    return (
      "#" +
      mixed
        .map(function (channel) {
          return channel.toString(16).padStart(2, "0");
        })
        .join("")
    );
  }

  function fogColor() {
    if (state.fogColor) return state.fogColor;
    const features = state.flatCollection && state.flatCollection.features;
    if (features) {
      for (let i = 0; i < features.length; i++) {
        const props = features[i].properties || {};
        if (props.kind === "fog" && props.color) {
          state.fogColor = props.color;
          return state.fogColor;
        }
      }
    }
    return "#dac092";
  }

  function isConcealed(id) {
    const view = state.historyView;
    return !!(view && view.concealedProvinces && view.concealedProvinces[id]);
  }

  function hasOwnerOverride(id) {
    const view = state.historyView;
    return !!(view && view.owners && Object.prototype.hasOwnProperty.call(view.owners, id));
  }

  function legalOwnerId(id, prov) {
    prov = prov || state.provinces.get(id);
    if (hasOwnerOverride(id)) return state.historyView.owners[id];
    return prov && prov.nation_id ? prov.nation_id : null;
  }

  function colonyOf(id) {
    const view = state.historyView;
    if (!view || !view.colonies) return null;
    return view.colonies[id] || null;
  }

  function occupationOf(id) {
    const rows = (state.historyView && state.historyView.occupations) || [];
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].province === id) return rows[i];
    }
    return null;
  }

  function historyLook(id) {
    if (!state.historyView || isConcealed(id)) {
      return isConcealed(id) ? { fog: true } : {};
    }
    const colony = colonyOf(id);
    if (colony) {
      const color = nationColor(colony.owner);
      return {
        fill: lighten(color, colony.stage === "outpost" ? 0.62 : 0.38),
        stroke: color,
        dash: "1 5",
        weight: 2,
      };
    }
    if (hasOwnerOverride(id)) {
      const owner = state.historyView.owners[id];
      return { fill: owner == null ? "#E6E6EA" : nationColor(owner) };
    }
    return {};
  }

  function colonyLine(id) {
    const colony = colonyOf(id);
    if (!colony) return "";
    const nation = state.nationsById.get(colony.owner);
    const stage = colony.stage === "colony" ? "Colony" : "Outpost";
    const name = nation ? nationLabel(nation) : "nation " + colony.owner;
    return '<p class="meta">' + esc(stage + " of " + name) + "</p>";
  }

  function occupationSentence(id) {
    const occ = occupationOf(id);
    if (!occ || isConcealed(id)) return "";
    const owner = legalOwnerId(id);
    const ownerNation = owner && state.nationsById.get(owner);
    const occupier = state.nationsById.get(occ.occupier);
    let text = "Owned by " + (ownerNation ? nationLabel(ownerNation) : "nobody");
    text += ", occupied by " + (occupier ? nationLabel(occupier) : "nation " + occ.occupier);
    if (occ.war) text += " (war: " + occ.war + ")";
    return '<p class="claim">' + esc(text) + "</p>";
  }

  function provinceSearchLabel(id, prov) {
    const colony = colonyOf(id);
    if (colony) return colony.stage === "colony" ? "Colony" : "Outpost";
    const owner = legalOwnerId(id, prov);
    const nation = owner && state.nationsById.get(owner);
    return nation ? nationLabel(nation) : "Unclaimed";
  }

  function workTypeLabel(type) {
    return type === "reclaimed" ? "Reclaimed land" : "Land bridge";
  }

  function workHtml(work) {
    const owner = work.owner && state.nationsById.get(work.owner);
    const building = work.status === "building";
    return (
      '<div class="nb-pop"><h2><i style="background:' +
      esc(nationColor(work.owner)) +
      '"></i>' +
      esc(work.name) +
      '</h2><p class="tier">' +
      esc(workTypeLabel(work.type)) +
      (building ? " · under construction" : "") +
      '</p><p class="meta">Province ' +
      work.id +
      '</p><p class="meta">Owned by ' +
      esc(owner ? nationLabel(owner) : work.owner ? "nation " + work.owner : "nobody") +
      "</p></div>"
    );
  }

  function markerHtml(marker) {
    const owner = marker.owner && state.nationsById.get(marker.owner);
    const kind = marker.kind === "army" ? "Army" : "Battle";
    return (
      '<div class="nb-pop"><h2>' +
      esc(marker.name) +
      '</h2><p class="tier">' +
      kind +
      "</p>" +
      (owner ? '<p class="meta">' + esc(nationLabel(owner)) + "</p>" : "") +
      (marker.text ? "<p>" + esc(marker.text) + "</p>" : "") +
      "</div>"
    );
  }

  function unexploredHtml(western) {
    const text = western ? "The western lands are unexplored." : "This part of the world is unexplored.";
    return '<div class="nb-pop"><h2>Unexplored</h2><p>' + text + "</p></div>";
  }

  function workScreen(work) {
    const box = work && work.bbox;
    if (!state.map || !box) return { weight: 3.5, marker: true, minor: 0, major: 0 };
    const a = state.map.latLngToContainerPoint([box[1], box[0]]);
    const b = state.map.latLngToContainerPoint([box[3], box[2]]);
    const width = Math.abs(b.x - a.x);
    const height = Math.abs(b.y - a.y);
    const minor = Math.min(width, height);
    const major = Math.max(width, height);
    const thin = minor < 8;
    return {
      weight: thin ? 3.5 : 1.75,
      marker: thin && major < 18,
      minor: minor,
      major: major,
    };
  }

  function changeStyle(feature) {
    const props = feature.properties || {};
    const cover = 1 - flatAmount(state.map ? state.map.getZoom() : 0);
    if (props.role === "colony") {
      const color = nationColor(props.owner);
      return {
        color: color,
        weight: 2,
        opacity: cover,
        dashArray: "1 5",
        lineCap: "butt",
        fill: true,
        fillColor: lighten(color, props.stage === "outpost" ? 0.62 : 0.38),
        fillOpacity: 0.74 * cover,
        interactive: false,
      };
    }
    const visible = cover > 0.03;
    return {
      color: "#16140f",
      weight: 1,
      opacity: visible ? 0.9 : 0,
      fill: true,
      fillColor: props.owner == null ? "#E6E6EA" : nationColor(props.owner),
      fillOpacity: visible ? 0.9 * cover : 0,
      interactive: false,
    };
  }

  function workStyle(feature) {
    const props = feature.properties || {};
    const screen = workScreen(props);
    const building = props.status === "building";
    return {
      color: "#16140f",
      weight: screen.weight,
      opacity: 1,
      dashArray: building ? "6 5" : null,
      lineCap: "round",
      lineJoin: "round",
      fill: true,
      fillColor: nationColor(props.owner),
      fillOpacity: building ? 0.4 : 0.94,
      interactive: false,
    };
  }

  function occPatternId(ownerHex, occupierHex) {
    return "nb-occ-" + String(ownerHex).slice(1).toLowerCase() + "-" + String(occupierHex).slice(1).toLowerCase();
  }

  function installOccPatterns(pairs) {
    const svg = document.querySelector(".leaflet-history-pane svg");
    if (!svg) return;
    const ns = "http://www.w3.org/2000/svg";
    let defs = svg.querySelector("defs");
    if (!defs) {
      defs = document.createElementNS(ns, "defs");
      svg.insertBefore(defs, svg.firstChild);
    }
    (pairs || []).forEach(function (pair) {
      const id = occPatternId(pair.owner, pair.occupier);
      if (svg.querySelector("#" + id)) return;
      const pattern = document.createElementNS(ns, "pattern");
      pattern.setAttribute("id", id);
      pattern.setAttribute("patternUnits", "userSpaceOnUse");
      pattern.setAttribute("width", "8");
      pattern.setAttribute("height", "8");
      pattern.setAttribute("patternTransform", "rotate(45)");
      const wash = document.createElementNS(ns, "rect");
      wash.setAttribute("width", "8");
      wash.setAttribute("height", "8");
      wash.setAttribute("fill", pair.owner);
      const stripe = document.createElementNS(ns, "path");
      stripe.setAttribute("d", "M0 0 H8");
      stripe.setAttribute("stroke", pair.occupier);
      stripe.setAttribute("stroke-width", "3");
      pattern.appendChild(wash);
      pattern.appendChild(stripe);
      defs.appendChild(pattern);
    });
  }

  function occStyle(feature) {
    const props = feature.properties || {};
    const owner = nationColor(props.owner);
    const occupier = nationColor(props.occupier);
    return {
      stroke: true,
      color: occupier,
      weight: 2,
      opacity: 0.95,
      fill: true,
      fillColor: "url(#" + occPatternId(owner, occupier) + ")",
      fillOpacity: 1,
      interactive: false,
    };
  }

  function dropLayer(layer) {
    if (layer && state.map && state.map.hasLayer(layer)) state.map.removeLayer(layer);
  }

  function provinceFeature(id, props) {
    const layer = state.flatById && state.flatById.get(id);
    const prov = state.provinces.get(id);
    if (!layer || !layer.feature || !provinceIsPublic(id, prov)) return null;
    return { type: "Feature", properties: props, geometry: layer.feature.geometry };
  }

  function markerHidden(marker) {
    if (concealedSeaAt(marker.x, marker.y)) return true;
    const id = state.lut ? provinceAt(marker.x, marker.y) : 0;
    return !!(id && isConcealed(id));
  }

  function renderHistory() {
    const map = state.map;
    if (!map) return;
    dropLayer(state.changeLayer);
    dropLayer(state.occLayer);
    dropLayer(state.workLayer);
    dropLayer(state.workMarkers);
    dropLayer(state.fogLayer);
    dropLayer(state.battleLayer);
    state.changeLayer = null;
    state.occLayer = null;
    state.workLayer = null;
    state.workMarkers = null;
    state.workMarkerKey = null;
    state.fogLayer = null;
    state.battleLayer = null;
    state.occPairs = [];
    const view = state.historyView;
    if (!view) return;

    const changes = [];
    Object.keys(view.colonies).forEach(function (key) {
      const colony = view.colonies[key];
      if (view.concealedProvinces[colony.province]) return;
      const feature = provinceFeature(colony.province, {
        role: "colony",
        id: colony.province,
        owner: colony.owner,
        stage: colony.stage,
      });
      if (feature) changes.push(feature);
    });
    Object.keys(view.owners).forEach(function (key) {
      const id = Number(key);
      if (view.colonies[id] || view.concealedProvinces[id]) return;
      const prov = state.provinces.get(id);
      const nextOwner = view.owners[id];
      const original = prov && prov.nation_id ? prov.nation_id : null;
      if (nextOwner === original) return;
      const feature = provinceFeature(id, { role: "transfer", id: id, owner: nextOwner });
      if (feature) changes.push(feature);
    });
    if (changes.length) {
      state.changeLayer = L.geoJSON(
        { type: "FeatureCollection", features: changes },
        {
          pane: "history",
          renderer: state.historyRenderer,
          smoothFactor: 0,
          interactive: false,
          style: changeStyle,
        }
      );
      state.changeLayer.addTo(map);
    }

    const occupations = [];
    view.occupations.forEach(function (occ) {
      if (view.concealedProvinces[occ.province]) return;
      const prov = state.provinces.get(occ.province);
      if (!provinceIsPublic(occ.province, prov)) return;
      const feature = provinceFeature(occ.province, {
        role: "occupation",
        id: occ.province,
        occupier: occ.occupier,
        owner: legalOwnerId(occ.province, prov),
      });
      if (!feature) return;
      occupations.push(feature);
      state.occPairs.push({
        owner: nationColor(feature.properties.owner),
        occupier: nationColor(occ.occupier),
      });
    });
    if (occupations.length) {
      state.occLayer = L.geoJSON(
        { type: "FeatureCollection", features: occupations },
        {
          pane: "history",
          renderer: state.historyRenderer,
          smoothFactor: 0,
          interactive: false,
          style: occStyle,
        }
      );
      if (state.overlays.occupied) state.occLayer.addTo(map);
      installOccPatterns(state.occPairs);
      state.occLayer.setStyle(occStyle);
    }

    if (view.works.length) {
      state.workLayer = L.geoJSON(
        {
          type: "FeatureCollection",
          features: view.works.map(function (work) {
            return {
              type: "Feature",
              properties: {
                role: "work",
                id: work.id,
                name: work.name,
                type: work.type,
                status: work.status,
                owner: work.owner,
                bbox: work.bbox,
              },
              geometry: { type: "Polygon", coordinates: work.rings },
            };
          }),
        },
        {
          pane: "history",
          renderer: state.historyRenderer,
          smoothFactor: 0,
          interactive: false,
          style: workStyle,
        }
      );
      state.workLayer.addTo(map);
    }

    const fogFeatures = [];
    Object.keys(view.concealedProvinces).forEach(function (key) {
      const feature = provinceFeature(Number(key), { role: "fog" });
      if (feature) fogFeatures.push(feature);
    });
    view.concealedSeas.forEach(function (sea) {
      fogFeatures.push({
        type: "Feature",
        properties: { role: "sea", id: sea.id },
        geometry: { type: "Polygon", coordinates: sea.rings },
      });
    });
    if (fogFeatures.length) {
      state.fogLayer = L.geoJSON(
        { type: "FeatureCollection", features: fogFeatures },
        {
          pane: "fog",
          renderer: state.fogRenderer,
          smoothFactor: 0,
          interactive: false,
          style: function () {
            return { stroke: false, fill: true, fillColor: fogColor(), fillOpacity: 1, interactive: false };
          },
        }
      );
      state.fogLayer.addTo(map);
    }

    state.battleLayer = L.layerGroup();
    view.markers.forEach(function (marker) {
      if (markerHidden(marker)) return;
      const icon = L.divIcon({
        className: "nb-event nb-event-" + marker.kind,
        html: "<span>" + (marker.kind === "army" ? "Army" : "Battle") + "</span>",
        iconSize: marker.kind === "army" ? [52, 18] : [58, 18],
        iconAnchor: [26, 9],
      });
      const pin = L.marker([marker.y, marker.x], {
        icon: icon,
        keyboard: true,
        title: marker.name,
        zIndexOffset: 700,
      });
      pin.on("click", function (event) {
        L.DomEvent.stopPropagation(event);
        clearHighlight();
        setNationHash(null);
        openPopup(event.latlng, markerHtml(marker));
      });
      pin.addTo(state.battleLayer);
    });
    if (state.overlays.markers) state.battleLayer.addTo(map);
    refreshWorkMarkers();
  }

  function refreshWorkMarkers() {
    const map = state.map;
    if (!map) return;
    const works = (state.historyView && state.historyView.works) || [];
    const key = works
      .filter(function (work) {
        return workScreen(work).marker;
      })
      .map(function (work) {
        return work.id;
      })
      .join(",");
    if (key === state.workMarkerKey && (state.workMarkers || !key)) return;
    state.workMarkerKey = key;
    dropLayer(state.workMarkers);
    state.workMarkers = null;
    if (!key) return;
    const group = L.layerGroup();
    works.forEach(function (work) {
      if (!workScreen(work).marker) return;
      L.circleMarker([work.centroid[1], work.centroid[0]], {
        pane: "history",
        renderer: state.historyRenderer,
        radius: 5,
        weight: 2,
        color: "#16140f",
        fillColor: nationColor(work.owner),
        fillOpacity: work.status === "building" ? 0.45 : 1,
        interactive: false,
      }).addTo(group);
    });
    state.workMarkers = group.addTo(map);
  }

  function refreshHistoryZoom() {
    if (state.changeLayer) state.changeLayer.setStyle(changeStyle);
    if (state.workLayer) state.workLayer.setStyle(workStyle);
    if (state.occPairs && state.occPairs.length) installOccPatterns(state.occPairs);
    refreshWorkMarkers();
  }

  function syncHistoryOverlays() {
    setOn(state.occLayer, !!state.overlays.occupied);
    setOn(state.battleLayer, !!state.overlays.markers);
  }

  function restyleFlatForHistory() {
    if (!state.flatById) return;
    const next = new Set();
    const view = state.historyView;
    if (view) {
      Object.keys(view.owners).forEach(function (id) {
        next.add(Number(id));
      });
      Object.keys(view.colonies).forEach(function (id) {
        next.add(Number(id));
      });
      Object.keys(view.concealedProvinces).forEach(function (id) {
        next.add(Number(id));
      });
    }
    const prev = state.historyStyled || new Set();
    prev.forEach(function (id) {
      if (next.has(id)) return;
      const layer = state.flatById.get(id);
      if (layer) layer.setStyle(flatStyle(layer.feature));
    });
    next.forEach(function (id) {
      const layer = state.flatById.get(id);
      if (layer) layer.setStyle(flatStyle(layer.feature));
    });
    state.historyStyled = next;
  }

  function pointSegDistance(px, py, ax, ay, bx, by) {
    const dx = bx - ax;
    const dy = by - ay;
    const len = dx * dx + dy * dy;
    if (!len) return Math.hypot(px - ax, py - ay);
    let t = ((px - ax) * dx + (py - ay) * dy) / len;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }

  function workAt(x, y) {
    const works = (state.historyView && state.historyView.works) || [];
    const history = window.NB_HISTORY;
    for (let i = works.length - 1; i >= 0; i--) {
      const box = works[i].bbox;
      if (x < box[0] || x > box[2] || y < box[1] || y > box[3]) continue;
      if (history.pointInRings(x, y, works[i].rings)) return works[i];
    }
    if (!state.map) return null;
    const click = state.map.latLngToContainerPoint([y, x]);
    for (let i = works.length - 1; i >= 0; i--) {
      const screen = workScreen(works[i]);
      if (screen.minor >= 12) continue;
      const ring = works[i].rings[0];
      for (let p = 0; p < ring.length - 1; p++) {
        const a = state.map.latLngToContainerPoint([ring[p][1], ring[p][0]]);
        const b = state.map.latLngToContainerPoint([ring[p + 1][1], ring[p + 1][0]]);
        if (pointSegDistance(click.x, click.y, a.x, a.y, b.x, b.y) <= 8) return works[i];
      }
      if (screen.marker) {
        const center = state.map.latLngToContainerPoint([works[i].centroid[1], works[i].centroid[0]]);
        if (Math.hypot(click.x - center.x, click.y - center.y) <= 12) return works[i];
      }
    }
    return null;
  }

  function concealedSeaAt(x, y) {
    const seas = (state.historyView && state.historyView.concealedSeas) || [];
    for (let i = seas.length - 1; i >= 0; i--) {
      const box = seas[i].bbox;
      if (x < box[0] || x > box[2] || y < box[1] || y > box[3]) continue;
      if (window.NB_HISTORY.pointInRings(x, y, seas[i].rings)) return seas[i];
    }
    return null;
  }

  function findWorkAny(id) {
    const west = state.meta ? state.meta.hidden_west_of : 3592;
    const index = window.NB_HISTORY.workIndex(state.historySnapshots, id, west);
    if (index < 0) return null;
    const folded = window.NB_HISTORY.fold(state.historySnapshots, index, west);
    const work = folded.works.find(function (item) {
      return item.id === id;
    });
    return work ? { work: work, index: index } : null;
  }

  function showWork(work, index) {
    const visible = state.historyView && state.historyView.works.some(function (item) {
      return item.id === work.id;
    });
    if (!visible) goToHistory(index);
    const shown =
      (state.historyView &&
        state.historyView.works.find(function (item) {
          return item.id === work.id;
        })) ||
      work;
    clearHighlight();
    setNationHash(null);
    if (shown.bbox) flyToBox(shown.bbox, 8);
    openPopup(L.latLng(shown.centroid[1], shown.centroid[0]), workHtml(shown));
  }

  function infraVisible(props) {
    if (!props) return true;
    const hasCycle = props.cycle != null && props.cycle !== "";
    const hasRound = props.round != null && props.round !== "";
    if (!hasCycle && !hasRound) return true;
    const view = state.historyView || { cycle: 0, round: null };
    const cycle = hasCycle ? Number(props.cycle) : 0;
    if (!isFinite(cycle)) return true;
    if (view.cycle > cycle) return true;
    if (view.cycle < cycle) return false;
    if (!hasRound) return true;
    const round = Number(props.round);
    if (!isFinite(round)) return true;
    const current = view.round == null ? 0 : view.round;
    return current >= round;
  }

  function goToHistory(index, options) {
    options = options || {};
    const snaps = state.historySnapshots || [];
    const max = Math.max(0, snaps.length - 1);
    const next = snaps.length ? Math.max(0, Math.min(Number(index) || 0, max)) : 0;
    if (!options.force && state.historyView && next === state.historyIndex) return;
    state.historyIndex = next;
    const west = state.meta ? state.meta.hidden_west_of : 3592;
    state.historyView = window.NB_HISTORY.fold(snaps, next, west);
    const range = document.getElementById("timeline-range");
    const label = document.getElementById("timeline-label");
    const prev = document.getElementById("timeline-prev");
    const nextButton = document.getElementById("timeline-next");
    if (range) {
      range.max = String(max);
      range.value = String(next);
      range.disabled = snaps.length < 2;
      range.setAttribute("aria-valuetext", state.historyView.label);
    }
    if (label) label.textContent = state.historyView.label;
    if (prev) prev.disabled = next <= 0;
    if (nextButton) nextButton.disabled = next >= max;
    renderHistory();
    restyleFlatForHistory();
    if (state.infraRaw) applyInfra();
    const concealedKey = Object.keys((state.historyView && state.historyView.concealedProvinces) || {})
      .sort()
      .join(",");
    if (concealedKey !== state.concealedKey && state.nations.length) {
      state.concealedKey = concealedKey;
      applyClaims();
    }
    refreshProvinceLabels();
    if (!options.silent && state.map) state.map.closePopup();
  }

  function installTimeline() {
    if (state.timelineReady) return;
    state.timelineReady = true;
    const range = document.getElementById("timeline-range");
    const prev = document.getElementById("timeline-prev");
    const nextButton = document.getElementById("timeline-next");
    if (!range || !prev || !nextButton) return;
    document.body.classList.add("has-timeline");
    range.addEventListener("input", function () {
      goToHistory(Number(range.value));
    });
    prev.addEventListener("click", function () {
      goToHistory(state.historyIndex - 1);
    });
    nextButton.addEventListener("click", function () {
      goToHistory(state.historyIndex + 1);
    });
  }

  async function loadHistory() {
    const fallback = [{ id: "cycle-0", cycle: 0, label: "Start (Cycle 0)" }];
    try {
      const stamp = Date.now();
      const res = await fetch("data/history/index.json?t=" + stamp, { cache: "no-store" });
      if (!res.ok) return fallback;
      const index = await res.json();
      const files = Array.isArray(index) ? index : index.snapshots || [];
      const snaps = [];
      for (let i = 0; i < files.length; i++) {
        const name = String(files[i] || "");
        if (!/^[\w.-]+\.json$/.test(name)) continue;
        const file = await fetch("data/history/" + name + "?t=" + stamp, { cache: "no-store" });
        if (!file.ok) continue;
        snaps.push(await file.json());
      }
      return snaps.length ? snaps : fallback;
    } catch (err) {
      console.error(err);
      return fallback;
    }
  }

  async function main() {
    setupInfo();
    setupSearch();
    headerOffset();
    setStatus("Loading map…", false);
    const meta = await fetch("data/meta.json").then(function (res) {
      if (!res.ok) throw new Error("meta");
      return res.json();
    });
    initMap(meta);
    const stamp = Date.now();
    const flatPromise = fetch("data/flat.geojson")
      .then(function (res) {
        if (!res.ok) throw new Error("flat");
        return res.json();
      })
      .catch(function () {
        return null;
      });
    const [world, claims, infra, lut] = await Promise.all([
      fetch("data/world.json").then(function (res) {
        if (!res.ok) throw new Error("world");
        return res.json();
      }),
      fetch("data/claims.json?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.json() : {};
        })
        .catch(function () {
          return {};
        }),
      fetch("data/infrastructure.geojson?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.json() : { type: "FeatureCollection", features: [] };
        })
        .catch(function () {
          return { type: "FeatureCollection", features: [] };
        }),
      loadLookup(meta),
    ]);
    state.lut = lut;
    indexWorld(world);
    state.claims = normalizeClaims(claims);
    state.historySnapshots = await loadHistory();
    installTimeline();
    goToHistory(Math.max(0, state.historySnapshots.length - 1), { force: true, silent: true });
    state.map.on("click", onMapClick);
    if (state.concealedKey == null) {
      state.concealedKey = "";
      applyClaims();
    }
    applyInfra(infra);
    window.addEventListener("hashchange", function () {
      openHash(true);
    });
    if (location.hash.indexOf("#nation=") === 0) openHash(true);
    setStatus("", false);
    const flat = await flatPromise;
    if (flat) applyFlat(flat);
  }

  main().catch(function (err) {
    console.error(err);
    setStatus("The map data failed to load.", true);
  });
})();
