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
    overlays: { borders: true, claims: true, infra: true, ids: false },
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
      return {
        stroke: !!borders,
        color: "#16140f",
        weight: borders ? 1 : 0,
        opacity: 0.9,
        lineJoin: "round",
        fill: true,
        fillColor: props.color || "#8d8678",
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
    }
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
      if (state.claims.has(String(nation.id))) ids.push.apply(ids, nation.provinces);
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

  function nationHtml(nation, provinceId) {
    const prov = state.provinces.get(provinceId) || state.provinces.get(nation.capital_province);
    const who = state.claims.get(String(nation.id));
    return (
      '<div class="nb-pop"><h2><i style="background:' +
      esc(safeColor(nation.color)) +
      '"></i>' +
      esc(nation.short_name) +
      '</h2><p class="tier">' +
      esc(labelTier(nation.tier)) +
      '</p><p class="meta">' +
      nation.province_count +
      " provinces · " +
      fmtArea(nation.area_km2) +
      '</p><p class="meta">Capital: Province ' +
      nation.capital_province +
      "</p>" +
      (who ? '<div class="claim">Claimed by ' + esc(who) + "</div>" : "") +
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
      autoPanPaddingBottomRight: L.point(16, 16),
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
      paddingBottomRight: [right, 24],
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
    if (!provinceIsPublic(id, prov) || !prov.bbox || !prov.xy) return;
    showHighlight([id], provinceFill(prov));
    setNationHash(null);
    if (options.fly) flyToBox(prov.bbox, 6);
    if (options.popup) {
      const latlng = L.latLng(prov.xy[1], prov.xy[0]);
      if (prov.nation_id && state.nationsById.has(prov.nation_id)) {
        openPopup(latlng, nationHtml(state.nationsById.get(prov.nation_id), id));
      } else {
        openPopup(latlng, unclaimedHtml(id, prov));
      }
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
        return nation.short_name.toLowerCase() === query || nation.name.toLowerCase() === query;
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
    input.value = nation.short_name;
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
          const nation = entry.prov.nation_id && state.nationsById.get(entry.prov.nation_id);
          tier.textContent = nation ? nation.short_name : "Unclaimed";
          button.appendChild(tier);
        } else {
          button.textContent = entry.nation.short_name;
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
      input.value = entry.nation.short_name;
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
        if (state.nationsById.has(id)) items.push({ kind: "nation", nation: state.nationsById.get(id) });
        const prov = state.provinces.get(id);
        if (provinceIsPublic(id, prov)) items.push({ kind: "province", id: id, prov: prov });
      }
      const starts = [];
      const rest = [];
      state.nations.forEach(function (nation) {
        const name = nation.short_name.toLowerCase();
        const full = nation.name.toLowerCase();
        const id = String(nation.id);
        if (id === query) return;
        if (!(name.includes(query) || full.includes(query))) return;
        const entry = { kind: "nation", nation: nation };
        if (name.startsWith(query)) starts.push(entry);
        else rest.push(entry);
      });
      render(items.concat(starts, rest).slice(0, 8));
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
    if (!state.lut) return;
    const x = event.latlng.lng;
    const y = event.latlng.lat;
    if (x < meta.hidden_west_of) {
      clearHighlight();
      setNationHash(null);
      openPopup(event.latlng, '<div class="nb-pop"><h2>Unexplored</h2><p>The western lands are unexplored.</p></div>');
      return;
    }
    const id = provinceAt(x, y);
    if (!id) {
      state.map.closePopup();
      return;
    }
    const prov = state.provinces.get(id);
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

  function normalizeClaims(raw) {
    const out = new Map();
    if (!raw || typeof raw !== "object") return out;
    Object.keys(raw).forEach(function (key) {
      const value = raw[key];
      if (typeof value !== "string") return;
      const name = value.trim();
      if (!name) return;
      out.set(String(key), name);
    });
    return out;
  }

  function applyClaims() {
    if (state.claimMarkers) {
      state.map.removeLayer(state.claimMarkers);
    }
    state.claimMarkers = L.layerGroup();
    state.nations.forEach(function (nation) {
      const who = state.claims.get(String(nation.id));
      if (!who) return;
      const xy = nationPoint(nation);
      const marker = L.marker([xy[1], xy[0]], {
        icon: L.divIcon({
          className: "claim-marker",
          html: "<span>Taken</span>",
          iconSize: [48, 16],
          iconAnchor: [24, 8],
        }),
        keyboard: true,
        title: "Claimed by " + who,
        zIndexOffset: 400,
      });
      marker.bindTooltip("Claimed by " + who, { direction: "top", className: "nb-tip" });
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
      if (props.kind === "province" && claimed.has(props.nation_id)) {
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
    rebuildMarks();
    applyMode();
  }

  function applyInfra(collection) {
    const data = collection && collection.type === "FeatureCollection" ? collection : { type: "FeatureCollection", features: [] };
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
    state.map.on("click", onMapClick);
    applyClaims();
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
