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
    capitalNames: new Map(),
    settlementRows: null,
    buildings: null,
    alliances: null,
    religions: null,
    provinceNames: new Map(),
    flags: new Map(),
    mode: "auto",
    overlays: { borders: true, claims: true, occupied: true, markers: true, infra: true, trade: true, sectors: true, alliances: false, religion: false, ids: false },
    labelLayer: null,
    map: null,
    layers: {},
    highlightLayer: null,
    highlightUrl: null,
    claimLayer: null,
    claimUrl: null,
    claimMarkers: null,
    infraLayer: null,
    infraKey: null,
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
    tradeRaw: null,
    tradeLayer: null,
    tradeRenderer: null,
    sectorFeatures: null,
    sectorRows: null,
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
      showPhoto(terrain, 1);
      setOn(political, false);
      setOn(depth, false);
      showPhoto(outlines, borders ? CFG.outlineOpacity : 0);
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
    const flatOn = state.mode !== "terrain" && flat > 0.02;
    const pane = map.getPane("flat");
    if (pane) pane.style.opacity = flatOn ? String(flat) : "0";
    setOn(state.flatLayer, flatOn);
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

  function syncTradeVisibility() {
    const map = state.map;
    if (!map) return;
    if (!state.overlays.trade) {
      if (state.tradeLayer && map.hasLayer(state.tradeLayer)) map.removeLayer(state.tradeLayer);
      return;
    }
    if (!state.tradeLayer || !map.hasLayer(state.tradeLayer)) renderTrade();
  }

  function makeTileLayer(folder, errorTile, pane) {
    const meta = state.meta;
    const options = {
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
    };
    if (pane) options.pane = pane;
    return L.tileLayer("tiles/" + folder + "/{z}/{x}/{y}.webp", options);
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
    map.createPane("rivers");
    const riverPane = map.getPane("rivers");
    riverPane.style.zIndex = "348";
    riverPane.style.pointerEvents = "none";
    state.riverRenderer = L.canvas({ pane: "rivers", padding: 0.25 });
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
    map.createPane("infra");
    const infraPane = map.getPane("infra");
    infraPane.style.zIndex = "390";
    state.infraRenderer = L.canvas({ pane: "infra", padding: 0.35 });
    map.createPane("trade");
    const tradePane = map.getPane("trade");
    tradePane.style.zIndex = "405";
    state.tradeRenderer = L.svg({ pane: "trade", padding: 0.5 });
    map.createPane("sector");
    const sectorPane = map.getPane("sector");
    sectorPane.style.zIndex = "358";
    state.sectorRenderer = L.svg({ pane: "sector", padding: 0.5 });
    map.createPane("sectorline");
    const sectorLinePane = map.getPane("sectorline");
    sectorLinePane.style.zIndex = "359";
    sectorLinePane.style.pointerEvents = "none";
    state.sectorLineRenderer = L.svg({ pane: "sectorline", padding: 0.5 });
    map.createPane("fog");
    const fogPane = map.getPane("fog");
    fogPane.style.zIndex = "480";
    fogPane.style.pointerEvents = "none";
    state.fogRenderer = L.svg({ pane: "fog", padding: 0.5 });
    map.createPane("alliance");
    const alliancePane = map.getPane("alliance");
    alliancePane.style.zIndex = "352";
    alliancePane.style.pointerEvents = "none";
    state.allianceRenderer = L.svg({ pane: "alliance", padding: 0.5 });
    map.createPane("religion");
    const religionPane = map.getPane("religion");
    religionPane.style.zIndex = "351";
    religionPane.style.pointerEvents = "none";
    state.religionRenderer = L.svg({ pane: "religion", padding: 0.5 });
    map.createPane("photoBorders");
    const photoBorders = map.getPane("photoBorders");
    photoBorders.style.zIndex = "356";
    photoBorders.style.pointerEvents = "none";
    map.createPane("marks");
    const marksPane = map.getPane("marks");
    marksPane.style.zIndex = "360";
    marksPane.style.pointerEvents = "none";
    marksPane.style.opacity = "0";
    state.marksRenderer = L.svg({ pane: "marks", padding: 0.5 });
    map.zoomControl.setPosition("topleft");
    state.layers.terrain = makeTileLayer("terrain", "assets/empty.webp");
    state.layers.terrain.setUrl("tiles/terrain/{z}/{x}/{y}.webp?v=3");
    state.layers.political = makeTileLayer("political", "assets/empty.webp");
    state.layers.depth = makeTileLayer("depth", "assets/empty.webp");
    state.layers.outlines = makeTileLayer("outlines", "assets/blank.webp", "photoBorders");
    L.control.scale({ imperial: false, metric: true, position: "bottomright", maxWidth: 160 }).addTo(map);
    installMinimap(map, meta);
    installLayers(map);
    state.labelLayer = L.layerGroup();
    map.on("zoom", applyMode);
    map.on("moveend zoomend", refreshProvinceLabels);
    map.on("moveend zoomend", function () {
      renderInfra();
      renderTrade();
      renderSectors();
      renderVillages();
      renderAlliances();
      renderReligions();
      renderRivers();
    });
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
          '<label><input type="checkbox" data-overlay="sectors" checked> Sectors</label>' +
          '<label><input type="checkbox" data-overlay="alliances"> Alliances</label>' +
          '<label><input type="checkbox" data-overlay="religion"> Religion</label>' +
          '<label><input type="checkbox" data-overlay="markers" checked> Battles &amp; armies</label>' +
          '<label><input type="checkbox" data-overlay="infra" checked> Infrastructure</label>' +
          '<label><input type="checkbox" data-overlay="trade" checked> Trade routes</label>' +
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
            syncTradeVisibility();
            renderSectors();
            renderVillages();
            renderAlliances();
            renderReligions();
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
    const feature = sectorIndex().get(nation.capital_province + "-A");
    if (feature) {
      const point = sectorMarkerPoint(feature);
      if (point) return point;
    }
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

  function loadCapitalNames(text) {
    const names = new Map();
    String(text || "")
      .split(/\r?\n/)
      .forEach(function (line, index) {
        if (!index || !line) return;
        const parts = line.split(",");
        if (parts.length < 4 || parts[3].trim().toLowerCase() !== "capital") return;
        const province = Number(parts[1]);
        const name = parts[2].trim();
        if (!isFinite(province) || !name) return;
        names.set(province, name);
      });
    return names;
  }

  function loadSettlementRows(text) {
    const rows = [];
    String(text || "")
      .split(/\r?\n/)
      .forEach(function (line, index) {
        if (!index || !line) return;
        const parts = line.split(",");
        if (parts.length < 6) return;
        const province = Number(parts[1]);
        const name = parts[2].trim();
        const type = parts[3].trim().toLowerCase();
        const sector = parts[5].trim();
        if (!isFinite(province) || !name || !sector) return;
        rows.push({ province: province, name: name, type: type, sector: sector });
      });
    return rows;
  }

  function loadBuildings(text) {
    const rows = [];
    String(text || "")
      .split(/\r?\n/)
      .forEach(function (line, index) {
        if (!index || !line.trim()) return;
        const parts = line.split(",");
        if (parts.length < 6) return;
        const nation = parts[0].trim();
        const province = Number(parts[1]);
        const sector = parts[2].trim();
        const building = parts[3].trim().toLowerCase();
        const cycle = Number(parts[4]);
        const status = parts[5].trim().toLowerCase();
        if (!nation || !isFinite(province) || !sector || building !== "fort") return;
        rows.push({
          nation: nation,
          province: province,
          sector: sector,
          building: building,
          cycle: isFinite(cycle) ? cycle : 0,
          status: status === "building" ? "building" : "done",
        });
      });
    return rows;
  }

  function loadAlliances(text) {
    const rows = [];
    String(text || "")
      .split(/\r?\n/)
      .forEach(function (line, index) {
        if (!index || !line.trim()) return;
        const parts = line.split(",");
        if (parts.length < 3) return;
        const bloc = parts[0].trim();
        const nation = parts[1].trim();
        const role = parts[2].trim().toLowerCase();
        if (!bloc || !nation) return;
        if (role !== "leader" && role !== "ally" && role !== "vassal") return;
        rows.push({ bloc: bloc, nation: nation, role: role });
      });
    return rows;
  }

  const ALLIANCE_COLORS = ["#2f6fbe", "#c45c26", "#1f8a70", "#8a4baf", "#b08900", "#a33b55", "#3d6b9a", "#6b7c3a", "#9a5b3c", "#3f6f8a"];

  function allianceColor(bloc) {
    let hash = 0;
    const text = String(bloc || "");
    for (let i = 0; i < text.length; i++) hash = (hash * 33 + text.charCodeAt(i)) >>> 0;
    return ALLIANCE_COLORS[hash % ALLIANCE_COLORS.length];
  }

  function allianceOf(nation) {
    if (!nation || !state.alliances) return null;
    for (let i = 0; i < state.alliances.length; i++) {
      if (state.alliances[i].nation === nation.short_name) return state.alliances[i];
    }
    return null;
  }

  function allianceLine(nation) {
    const row = allianceOf(nation);
    if (!row) return "";
    const role = row.role === "leader" ? "Leader" : row.role === "vassal" ? "Vassal" : "Ally";
    return '<p class="meta">' + esc(row.bloc) + " · " + role + "</p>";
  }

  function nationByName(name) {
    const want = String(name || "");
    for (let i = 0; i < state.nations.length; i++) {
      if (state.nations[i].short_name === want) return state.nations[i];
    }
    return null;
  }

  function capitalPlaceName(nation) {
    if (!nation) return "";
    const named = state.capitalNames && state.capitalNames.get(nation.capital_province);
    return named || nation.short_name;
  }

  function nationLabel(nation) {
    if (!nation) return "";
    const claim = claimOf(nation);
    if (!claim) return nation.short_name;
    return nation.short_name + " (" + claim.short + ")";
  }

  function buildLedger() {
    if (state.buildLedgerPromise) return state.buildLedgerPromise;
    state.buildLedgerPromise = fetch("data/province_slots.json", { cache: "no-store" })
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .catch(function () {
        return null;
      });
    return state.buildLedgerPromise;
  }

  function settlementWords(level) {
    if (level === "city" || level === "town" || level === "village") return level;
    if (level == null || level === "") return "";
    return String(level);
  }

  function limitNumbers(spec) {
    let built = null;
    let max = null;
    if (spec && typeof spec === "object") {
      if (spec.built != null && spec.built !== "" && isFinite(Number(spec.built))) built = Number(spec.built);
      if (spec.max != null && spec.max !== "" && isFinite(Number(spec.max))) max = Number(spec.max);
    } else if (spec != null && spec !== "" && isFinite(Number(spec))) {
      max = Number(spec);
    }
    return { built: built, max: max };
  }

  function slotsMount(id) {
    const prov = state.provinces.get(id);
    if (!provinceIsPublic(id, prov) || isConcealed(id)) return "";
    scheduleBuildSlots(id);
    return '<div class="nb-build" data-build="' + id + '">Loading building limits…</div>';
  }

  function scheduleBuildSlots(id) {
    const prov = state.provinces.get(id);
    if (!provinceIsPublic(id, prov) || isConcealed(id)) return;
    window.setTimeout(function () {
      fillBuildSlots(id);
    }, 0);
  }

  function fillBuildSlots(id) {
    buildLedger().then(function (data) {
      const node = document.querySelector('.nb-build[data-build="' + id + '"]');
      if (!node) return;
      const prov = state.provinces.get(id);
      if (!provinceIsPublic(id, prov) || isConcealed(id)) {
        node.remove();
        return;
      }
      if (!data) {
        node.textContent = "Building limits could not be loaded.";
        return;
      }
      const row = data.provinces && data.provinces[String(id)];
      if (!row) {
        node.remove();
        return;
      }
      const largest = settlementWords(row.largest_settlement);
      const limits = row.limits && typeof row.limits === "object" ? row.limits : null;
      let html = "";
      if (largest) html += '<p class="meta">Largest settlement: ' + esc(largest) + "</p>";
      const types = limits ? Object.keys(limits) : [];
      if (types.length) {
        html += "<ul>";
        types.forEach(function (type) {
          const count = limitNumbers(limits[type]);
          if (count.built == null && count.max == null) return;
          let line = esc(String(type));
          if (count.built != null && count.max != null) line += " " + count.built + "/" + count.max;
          else if (count.max != null) line += " up to " + count.max;
          else line += " " + count.built;
          html += "<li>" + line + "</li>";
        });
        html += "</ul>";
      }
      if (!html) {
        node.remove();
        return;
      }
      const cycle = data.meta && data.meta.cycle != null ? data.meta.cycle : "";
      if (cycle !== "") html += '<p class="meta nb-asof">As of Cycle ' + esc(String(cycle)) + "</p>";
      node.innerHTML = html;
    });
  }

  function loadProvinceNames(text) {
    const names = new Map();
    String(text || "")
      .split(/\r?\n/)
      .forEach(function (line, index) {
        if (!index || !line.trim()) return;
        const comma = line.indexOf(",");
        if (comma < 1) return;
        const id = Number(line.slice(0, comma).trim());
        const name = line.slice(comma + 1).trim();
        if (!isFinite(id) || !name) return;
        names.set(id, name);
      });
    return names;
  }

  function loadFlags(data) {
    const flags = new Map();
    if (!data || typeof data !== "object") return flags;
    Object.keys(data).forEach(function (key) {
      const src = String(data[key] || "");
      if (!src || src.indexOf("..") >= 0) return;
      flags.set(String(key), src);
    });
    return flags;
  }

  function flagSrc(nation) {
    if (!nation || !state.flags) return "";
    return state.flags.get(String(nation.id)) || "";
  }

  function flagHtml(nation) {
    const src = flagSrc(nation);
    if (!src) return "";
    return '<img class="nb-flag" alt="" width="36" height="24" loading="lazy" decoding="async" src="' + esc(src) + '">';
  }

  function subtitleHtml(nation) {
    if (!nation || !nation.subtitle) return "";
    return '<p class="nb-subtitle">' + esc(nation.subtitle) + "</p>";
  }

  function provinceTitle(id) {
    const named = state.provinceNames && state.provinceNames.get(Number(id));
    return named ? named + " (Province " + id + ")" : "Province " + id;
  }

  function nationHtml(nation, provinceId) {
    const shownId = state.provinces.has(provinceId) ? provinceId : nation.capital_province;
    const prov = state.provinces.get(shownId);
    const claim = claimOf(nation);
    const named = capitalPlaceName(nation);
    const distinct = named && named.toLowerCase() !== nation.short_name.toLowerCase();
    const capitalLine = (distinct ? esc(named) + " · " : "") + "Province " + nation.capital_province;
    return (
      '<div class="nb-pop"><h2>' +
      flagHtml(nation) +
      '<i style="background:' +
      esc(safeColor(nation.color)) +
      '"></i>' +
      esc(nationLabel(nation)) +
      "</h2>" +
      subtitleHtml(nation) +
      '<p class="tier">' +
      esc(labelTier(nation.tier)) +
      "</p>" +
      allianceLine(nation) +
      '<p class="meta">' +
      nation.province_count +
      " provinces · " +
      fmtArea(nation.area_km2) +
      '</p><p class="meta">Capital: ' +
      capitalLine +
      "</p>" +
(claim ? '<div class="claim">Claimed by ' + esc(claim.player) + "</div>" : "") +
      colonyLine(provinceId) +
      occupationSentence(provinceId) +
      (prov
        ? '<hr><p class="meta">' +
          esc(provinceTitle(shownId)) +
          " · " +
          fmtArea(prov.area_km2) +
          "</p>" +
          provinceFields(prov)
        : "") +
      slotsMount(shownId) +
      "</div>"
    );
  }

  function unclaimedHtml(id, prov) {
    return (
      '<div class="nb-pop"><h2>Unclaimed</h2><p class="meta">' +
      esc(provinceTitle(id)) +
      " · " +
      fmtArea(prov.area_km2) +
      "</p>" +
      colonyLine(id) +
      occupationSentence(id) +
      provinceFields(prov) +
      slotsMount(id) +
      "</div>"
    );
  }

  function hideFlagCard() {
    const card = document.getElementById("flag-card");
    if (!card) return;
    card.hidden = true;
    card.classList.remove("is-expanded");
  }

  function placeFlagCard() {
    const card = document.getElementById("flag-card");
    if (!card || card.hidden) return;
    const phone = window.matchMedia("(max-width: 720px)").matches;
    const top = phone ? "calc(var(--header-h) + 78px)" : "calc(var(--header-h) + 96px)";
    const spots = [
      { left: "max(12px, env(safe-area-inset-left))", right: "auto", top: top, bottom: "auto" },
      { left: "auto", right: "max(12px, env(safe-area-inset-right))", top: top, bottom: "auto" },
      { left: "max(12px, env(safe-area-inset-left))", right: "auto", top: "auto", bottom: "calc(92px + env(safe-area-inset-bottom))" },
      { left: "auto", right: "max(12px, env(safe-area-inset-right))", top: "auto", bottom: "calc(92px + env(safe-area-inset-bottom))" },
    ];
    function apply(spot) {
      card.style.left = spot.left;
      card.style.right = spot.right;
      card.style.top = spot.top;
      card.style.bottom = spot.bottom;
    }
    const pop = document.querySelector(".leaflet-popup");
    if (!pop) {
      apply(spots[0]);
      return;
    }
    const box = pop.getBoundingClientRect();
    const popup = { left: box.left - 10, right: box.right + 10, top: box.top - 10, bottom: box.bottom + 10 };
    let best = spots[0];
    let bestArea = Infinity;
    spots.forEach(function (spot) {
      apply(spot);
      const rect = card.getBoundingClientRect();
      const width = Math.min(rect.right, popup.right) - Math.max(rect.left, popup.left);
      const height = Math.min(rect.bottom, popup.bottom) - Math.max(rect.top, popup.top);
      const area = width > 0 && height > 0 ? width * height : 0;
      if (area < bestArea) {
        bestArea = area;
        best = spot;
      }
    });
    apply(best);
  }

  function showFlagCard(nation) {
    const src = flagSrc(nation);
    if (!src) {
      hideFlagCard();
      return;
    }
    let card = document.getElementById("flag-card");
    if (!card) {
      card = document.createElement("aside");
      card.id = "flag-card";
      card.className = "nb-flag-card";
      card.setAttribute("aria-label", "Nation flag");
      card.innerHTML = '<button type="button" class="nb-flag-card-close" aria-label="Close flag">×</button><img alt=""><p></p>';
      card.addEventListener("click", function (event) {
        if (event.target.closest(".nb-flag-card-close")) {
          hideFlagCard();
          return;
        }
        if (window.matchMedia("(max-width: 720px)").matches) card.classList.toggle("is-expanded");
        placeFlagCard();
      });
      document.body.appendChild(card);
    }
    const same = card.getAttribute("data-nation") === String(nation.id) && !card.hidden;
    card.setAttribute("data-nation", String(nation.id));
    const img = card.querySelector("img");
    if (img.getAttribute("src") !== src) img.setAttribute("src", src);
    img.alt = "";
    card.querySelector("p").textContent = nationLabel(nation);
    if (!same) card.classList.remove("is-expanded");
    card.hidden = false;
    window.requestAnimationFrame(placeFlagCard);
    window.setTimeout(placeFlagCard, 280);
  }

  function openPlacePopup(latlng, html, nationId) {
    openPopup(latlng, html);
    const nation = nationId == null || nationId === "" ? null : state.nationsById.get(Number(nationId));
    showFlagCard(nation);
  }

  function openPopup(latlng, html) {
    const pad = headerOffset();
    const maxWidth = Math.min(340, Math.max(220, window.innerWidth - 40));
    L.popup({
      className: "nb-popup",
      maxWidth: maxWidth,
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
    const names = [nation.short_name, nation.name, capitalPlaceName(nation)].concat(nation.former_names || []);
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
          button.textContent = entry.title ? entry.title + " (Province " + entry.id + ")" : "Province " + entry.id;
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
          const capital = capitalPlaceName(entry.nation);
          const distinct = capital && capital.toLowerCase() !== entry.nation.short_name.toLowerCase();
          button.textContent = distinct ? capital + " · " + nationLabel(entry.nation) : nationLabel(entry.nation);
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
          const src = flagSrc(entry.nation);
          if (src) {
            const flag = document.createElement("img");
            flag.className = "nb-flag";
            flag.alt = "";
            flag.width = 36;
            flag.height = 24;
            flag.loading = "lazy";
            flag.decoding = "async";
            flag.src = src;
            button.insertBefore(flag, button.firstChild);
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
        input.value = entry.title || String(entry.id);
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
      if (state.provinceNames) {
        state.provinceNames.forEach(function (name, id) {
          const prov = state.provinces.get(id);
          if (!provinceIsPublic(id, prov) || isConcealed(id)) return;
          const lower = String(name).toLowerCase();
          if (!lower.includes(query)) return;
          const entry = { kind: "province", id: id, prov: prov, title: name };
          if (lower === query) pinned.push(entry);
          else if (lower.startsWith(query)) starts.push(entry);
          else rest.push(entry);
        });
      }
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
    const sector = sectorAt(x, y);
    if (sector && sector.occupied) {
      clearHighlight();
      setNationHash(null);
      openPopup(event.latlng, sectorHtml(sector));
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
    if (kind === "capital") return "Capital";
    if (kind === "city") return "City";
    if (kind === "town") return "Town";
    if (kind === "village") return "Village";
    if (kind === "road") return "Road";
    if (kind === "port") return "Port";
    if (kind === "shipyard") return "Shipyard";
    if (kind === "fort") return "Fort";
    if (kind === "market") return "Market";
    if (kind === "farm") return "Farm";
    if (kind === "mine") return "Mine";
    if (kind === "lumber" || kind === "lumber_camp") return "Lumber camp";
    if (kind === "trade_route") return "Trade route";
    if (kind === "supply_route") return "Supply route";
    return kind ? String(kind) : "Place";
  }

  function infraTypeOf(value) {
    const text = String(value || "").toLowerCase().replace(/[\s-]+/g, "_");
    if (text === "lumber_camp" || text === "lumbercamp") return "lumber";
    if (text === "harbour") return "port";
    return text;
  }

  function infraMinZoom(type) {
    if (type === "capital") return 4;
    if (type === "city") return 5;
    if (type === "fort" || type === "shipyard") return 5;
    if (type === "village") return 7;
    return 6;
  }

  function infraDateVisible(entry) {
    if (!entry) return false;
    const hasCycle = entry.cycle != null && entry.cycle !== "";
    const hasRound = entry.round != null && entry.round !== "";
    if (!hasCycle && !hasRound) return true;
    const view = state.historyView || { cycle: 0, round: null };
    const cycle = hasCycle ? Number(entry.cycle) : 0;
    if (!isFinite(cycle)) return true;
    if (view.cycle > cycle) return true;
    if (view.cycle < cycle) return false;
    if (!hasRound) return true;
    const round = Number(entry.round);
    if (!isFinite(round)) return true;
    const current = view.round == null ? 0 : view.round;
    return current >= round;
  }

  function infraSvg(type) {
    const ink = "#0B1A4A";
    const paper = "#E6E6EA";
    const tan = "#c4a574";
    const red = "#BC0501";
    const common = 'viewBox="0 0 20 20" aria-hidden="true"';
    if (type === "capital") {
      return '<svg ' + common + ' width="18" height="18"><path fill="' + paper + '" stroke="' + ink + '" stroke-width="1" d="M10 1.3 12.5 7.1 18.7 7.6 13.7 11.6 15.2 17.8 10 14.5 4.8 17.8 6.3 11.6 1.3 7.6 7.5 7.1Z"/></svg>';
    }
    if (type === "city") {
      return '<svg ' + common + ' width="16" height="16"><circle cx="10" cy="10" r="8" fill="' + paper + '" stroke="' + ink + '" stroke-width="1.2"/><path fill="' + ink + '" d="M10 4.2 11.2 7.4 14.6 7.6 11.9 9.7 12.7 13 10 11.2 7.3 13 8.1 9.7 5.4 7.6 8.8 7.4Z"/></svg>';
    }
    if (type === "town") {
      return '<svg ' + common + ' width="14" height="14"><path fill="' + paper + '" stroke="' + ink + '" stroke-width="1.1" d="M3 9.2 10 3.4 17 9.2V17H3Z"/><path fill="' + tan + '" stroke="' + ink + '" stroke-width="1" d="M8 17v-4h4v4"/></svg>';
    }
    if (type === "village") {
      return '<svg ' + common + ' width="10" height="10"><path fill="' + paper + '" stroke="' + ink + '" stroke-width="1.6" d="M3.2 11.4 10 5.2 16.8 11.4V16.6H3.2Z"/></svg>';
    }
    if (type === "fort") {
      return '<svg ' + common + ' width="16" height="16"><path fill="' + paper + '" stroke="' + red + '" stroke-width="1.2" d="M3 7h2V4h2v3h2V4h2v3h2V4h2v3h2v10H3Z"/></svg>';
    }
    if (type === "port") {
      return '<svg ' + common + ' width="16" height="16"><circle cx="10" cy="10" r="8.2" fill="' + ink + '" stroke="' + paper + '" stroke-width="1"/><circle cx="10" cy="5.2" r="1.5" fill="none" stroke="' + paper + '" stroke-width="1.2"/><path fill="none" stroke="' + paper + '" stroke-width="1.3" stroke-linecap="round" d="M10 6.8v7.2M6.8 11.6c1.3 1.8 5.1 1.8 6.4 0M5 10.2h10"/></svg>';
    }
    if (type === "shipyard") {
      return '<svg ' + common + ' width="16" height="16"><path fill="' + tan + '" stroke="' + ink + '" stroke-width="1.1" d="M3 13 10 5l7 8Z"/><path fill="none" stroke="' + ink + '" stroke-width="1.1" d="M2 16h16M10 5v11"/></svg>';
    }
    if (type === "market") {
      return '<svg ' + common + ' width="14" height="14"><path fill="' + red + '" d="M3 8 10 3l7 5H3Z"/><path fill="' + tan + '" stroke="' + ink + '" stroke-width="1" d="M4 8h12v8H4Z"/></svg>';
    }
    if (type === "farm") {
      return '<svg ' + common + ' width="14" height="14"><path fill="none" stroke="' + tan + '" stroke-width="1.4" stroke-linecap="round" d="M6 17V8M10 17V6M14 17V9"/><path fill="' + tan + '" d="M6 8c-2-1-2.5-3.5-1-5 2 1 2.2 3.2 1 5Zm4-2c-2-1-2.5-3.5-1-5 2 1 2.2 3.2 1 5Zm4 3c-2-1-2.5-3.5-1-5 2 1 2.2 3.2 1 5Z"/></svg>';
    }
    if (type === "mine") {
      return '<svg ' + common + ' width="14" height="14"><path fill="none" stroke="' + paper + '" stroke-width="1.6" stroke-linecap="round" d="M4 16 13 5M13 5l3 1M13 5l-1 3"/><path fill="' + tan + '" stroke="' + ink + '" stroke-width="1" d="M3 12h6l2 5H5Z"/></svg>';
    }
    return '<svg ' + common + ' width="14" height="14"><path fill="' + tan + '" stroke="' + ink + '" stroke-width="1" d="M10 3 16 12H4Z"/><path fill="' + ink + '" d="M9 12h2v5H9Z"/></svg>';
  }

  function infraNationText(entry) {
    if (!entry || entry.nation == null || entry.nation === "") {
      return entry && entry.type && entry.type !== "road" ? "Unclaimed" : "";
    }
    const nation = state.nationsById.get(Number(entry.nation));
    return nation ? nationLabel(nation) : "";
  }

  function infraHtml(entry) {
    const type = entry.type || "road";
    const name = entry.name || kindLabel(type);
    const nation = infraNationText(entry);
    const nationLine = nation ? '<p class="meta">' + esc(nation) + "</p>" : "";
    const province = entry.province ? '<p class="meta">Province ' + entry.province + "</p>" : "";
    return (
      '<div class="nb-pop"><h2>' +
      esc(name) +
      '</h2><p class="tier">' +
      esc(kindLabel(type)) +
      "</p>" +
      nationLine +
      province +
      "</div>"
    );
  }

  function pointConcealed(x, y) {
    const meta = state.meta;
    if (!meta || x == null || y == null) return true;
    if (x < meta.hidden_west_of || y < 0 || x > meta.width || y > meta.height) return true;
    if (concealedSeaAt(x, y)) return true;
    const id = provinceAt(x, y);
    return !!(id && isConcealed(id));
  }

  function segmentConcealed(a, b) {
    const steps = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 48));
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      if (pointConcealed(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)) return true;
    }
    return false;
  }

  function catalogPlace(row) {
    if (!row) return null;
    const type = infraTypeOf(row.type || row.kind);
    if (!type || type === "road") return null;
    const province = row.province == null || row.province === "" ? null : Number(row.province);
    const x = row.x == null || row.x === "" ? null : Number(row.x);
    const y = row.y == null || row.y === "" ? null : Number(row.y);
    const id = row.id != null && row.id !== "" ? String(row.id) : type + "-" + (province != null ? province : x + "x" + y);
    let nation = null;
    if (row.nation != null && row.nation !== "") {
      const parsed = Number(row.nation);
      if (isFinite(parsed)) nation = parsed;
    }
    return {
      id: id,
      type: type,
      name: row.name ? String(row.name) : "",
      province: isFinite(province) ? province : null,
      nation: nation,
      x: isFinite(x) ? x : null,
      y: isFinite(y) ? y : null,
      cycle: row.cycle,
      round: row.round,
    };
  }

  function catalogRoad(row) {
    if (!row) return null;
    const provinces = [];
    (row.provinces || []).forEach(function (id) {
      const n = Number(id);
      if (isFinite(n)) provinces.push(n);
    });
    const points = [];
    (row.points || row.coordinates || []).forEach(function (pt) {
      if (!pt || pt.length < 2) return;
      const x = Number(pt[0]);
      const y = Number(pt[1]);
      if (isFinite(x) && isFinite(y)) points.push([x, y]);
    });
    if (provinces.length < 2 && points.length < 2) return null;
    const id = row.id != null && row.id !== "" ? String(row.id) : "road-" + (provinces.join("-") || points.length);
    let nation = null;
    if (row.nation != null && row.nation !== "") {
      const parsed = Number(row.nation);
      if (isFinite(parsed)) nation = parsed;
    }
    return {
      id: id,
      name: row.name ? String(row.name) : "",
      nation: nation,
      provinces: provinces,
      points: points,
      cycle: row.cycle,
      round: row.round,
    };
  }

  function placePoint(place) {
    const meta = state.meta;
    if (place.province != null) {
      const prov = state.provinces.get(place.province);
      if (!provinceIsPublic(place.province, prov) || isConcealed(place.province)) return null;
    }
    let x = place.x;
    let y = place.y;
    if (x == null || y == null) {
      const prov = place.province != null ? state.provinces.get(place.province) : null;
      if (!prov || !prov.xy) return null;
      x = prov.xy[0];
      y = prov.xy[1];
    }
    if (!meta || x < meta.hidden_west_of || y < 0 || x > meta.width || y > meta.height) return null;
    if (pointConcealed(x, y)) return null;
    return [x, y];
  }

  function roadEndIndex() {
    const sig = String(state.infraStamp || 0);
    if (state.roadEndIndex && state.roadEndIndexSig === sig) return state.roadEndIndex;
    const byProvince = new Map();
    settlementMarkerList().forEach(function (marker) {
      if (marker.type !== "city" && marker.type !== "town" && marker.type !== "capital") return;
      let list = byProvince.get(marker.province);
      if (!list) {
        list = [];
        byProvince.set(marker.province, list);
      }
      list.push(marker);
    });
    state.roadEndIndex = byProvince;
    state.roadEndIndexSig = sig;
    return byProvince;
  }

  function nearestRoadMarker(x, y, provinceId, nation) {
    const list = roadEndIndex().get(provinceId);
    if (!list) return null;
    let best = null;
    let bestD = Infinity;
    for (let i = 0; i < list.length; i++) {
      const marker = list[i];
      if (nation != null && marker.nation != null && Number(marker.nation) !== Number(nation)) continue;
      const dist = Math.hypot(marker.x - x, marker.y - y);
      if (dist < bestD) {
        bestD = dist;
        best = marker;
      }
    }
    return best ? { marker: best, dist: bestD } : null;
  }

  function segmentInNation(a, b, nation) {
    const dist = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (dist < 0.5) return true;
    const steps = Math.max(1, Math.ceil(dist / 2));
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const x = a[0] + (b[0] - a[0]) * t;
      const y = a[1] + (b[1] - a[1]) * t;
      if (pointConcealed(x, y)) return false;
      const pid = provinceAt(x, y);
      if (!pid) return false;
      const prov = state.provinces.get(pid);
      const owner = prov && prov.nation_id != null && prov.nation_id !== "" ? prov.nation_id : null;
      if (nation == null) {
        if (owner != null) return false;
      } else if (Number(owner) !== Number(nation)) {
        return false;
      }
    }
    return true;
  }

  // Pull an end onto the city, town, or capital in its province when that
  // segment stays on the nation's land. Ends already on the marker stay put.
  function anchorRoadEnd(stops, index, provinceId, nation, limit) {
    const found = nearestRoadMarker(stops[index][0], stops[index][1], provinceId, nation);
    if (!found || found.dist > limit || found.dist <= 0.5) return;
    const neighbor = stops[index === 0 ? 1 : stops.length - 2];
    const next = [found.marker.x, found.marker.y];
    if (!segmentInNation(neighbor, next, nation)) return;
    stops[index] = next;
  }

  function roadPoints(road) {
    const stops = [];
    let provinceEnds = null;
    if (road.provinces && road.provinces.length >= 2) {
      provinceEnds = [road.provinces[0], road.provinces[road.provinces.length - 1]];
      for (let i = 0; i < road.provinces.length; i++) {
        const id = road.provinces[i];
        const prov = state.provinces.get(id);
        if (!provinceIsPublic(id, prov) || !prov.xy || isConcealed(id)) return null;
        stops.push([prov.xy[0], prov.xy[1]]);
      }
    } else if (road.points && road.points.length >= 2) {
      road.points.forEach(function (pt) {
        stops.push(pt);
      });
    } else {
      return null;
    }
    if (stops.length >= 2) {
      const nation = road.nation == null || road.nation === "" ? null : road.nation;
      if (provinceEnds) {
        anchorRoadEnd(stops, 0, provinceEnds[0], nation, 400);
        anchorRoadEnd(stops, stops.length - 1, provinceEnds[1], nation, 400);
      } else {
        const startProvince = provinceAt(stops[0][0], stops[0][1]);
        const endProvince = provinceAt(stops[stops.length - 1][0], stops[stops.length - 1][1]);
        if (startProvince) anchorRoadEnd(stops, 0, startProvince, nation, 120);
        if (endProvince) anchorRoadEnd(stops, stops.length - 1, endProvince, nation, 120);
      }
    }
    for (let i = 0; i < stops.length; i++) {
      if (pointConcealed(stops[i][0], stops[i][1])) return null;
      if (i > 0 && segmentConcealed(stops[i - 1], stops[i])) return null;
    }
    return stops;
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
    renderSectors();
    renderAlliances();
    renderReligions();
    restyleFlatForHistory();
    applyMode();
  }

  function collectInfra() {
    const cacheKey = String(state.infraStamp || 0) + ":" + String(state.historyIndex || 0);
    if (state.infraCatalog && state.infraCatalogKey === cacheKey) return state.infraCatalog;
    const raw = state.infraRaw && !state.infraRaw.type ? state.infraRaw : { capitals: true, places: [], roads: [] };
    const showCapitals = raw.capitals !== false;
    const removed = {};
    const fromHistory = (state.historyView && state.historyView.infrastructure) || { places: [], roads: [], removed: [] };
    (fromHistory.removed || []).forEach(function (id) {
      removed[String(id)] = true;
    });
    const places = {};
    const roads = {};
    if (showCapitals) {
      state.nations.forEach(function (nation) {
        const id = "capital-" + nation.id;
        if (removed[id] || !nation.capital_xy || nation.capital_xy.length < 2) return;
        places[id] = {
          id: id,
          type: "capital",
          name: capitalPlaceName(nation),
          nation: nation.id,
          province: nation.capital_province,
          x: nation.capital_xy[0],
          y: nation.capital_xy[1],
          cycle: 0,
        };
      });
    }
    (raw.places || []).forEach(function (row) {
      const place = catalogPlace(row);
      if (!place || removed[place.id] || !infraDateVisible(place)) return;
      places[place.id] = place;
    });
    (raw.roads || []).forEach(function (row) {
      const road = catalogRoad(row);
      if (!road || removed[road.id] || !infraDateVisible(road)) return;
      roads[road.id] = road;
    });
    (fromHistory.places || []).forEach(function (place) {
      if (!place || removed[place.id] || !infraDateVisible(place)) return;
      places[place.id] = place;
    });
    (fromHistory.roads || []).forEach(function (road) {
      if (!road || removed[road.id] || !infraDateVisible(road)) return;
      roads[road.id] = road;
    });
    state.infraCatalog = { places: places, roads: roads };
    state.infraCatalogKey = cacheKey;
    return state.infraCatalog;
  }

  function buildingVisible(row) {
    const cycle = Number(row.cycle);
    if (!isFinite(cycle)) return false;
    const view = state.historyView && state.historyView.cycle != null ? Number(state.historyView.cycle) : 0;
    return cycle <= view;
  }

  function ringsOf(geom) {
    if (!geom) return [];
    if (geom.type === "Polygon") return [geom.coordinates];
    if (geom.type === "MultiPolygon") return geom.coordinates;
    return [];
  }

  function pointInRing(x, y, ring) {
    let inside = false;
    const count = ring.length;
    if (count < 3) return false;
    let j = count - 1;
    for (let i = 0; i < count; i++) {
      const xi = ring[i][0];
      const yi = ring[i][1];
      const xj = ring[j][0];
      const yj = ring[j][1];
      if (yi > y !== yj > y) {
        const denom = yj - yi;
        if (denom !== 0 && x < ((xj - xi) * (y - yi)) / denom + xi) inside = !inside;
      }
      j = i;
    }
    return inside;
  }

  function geomContains(x, y, geom) {
    const parts = ringsOf(geom);
    for (let p = 0; p < parts.length; p++) {
      const rings = parts[p];
      if (!rings || !rings.length || !pointInRing(x, y, rings[0])) continue;
      let inHole = false;
      for (let h = 1; h < rings.length; h++) {
        if (pointInRing(x, y, rings[h])) {
          inHole = true;
          break;
        }
      }
      if (!inHole) return true;
    }
    return false;
  }

  function segmentDistance(x, y, ax, ay, bx, by) {
    const dx = bx - ax;
    const dy = by - ay;
    const span = dx * dx + dy * dy;
    if (span === 0) return Math.hypot(x - ax, y - ay);
    const along = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / span));
    return Math.hypot(x - (ax + along * dx), y - (ay + along * dy));
  }

  function boundaryDistance(x, y, geom) {
    let best = null;
    const parts = ringsOf(geom);
    for (let p = 0; p < parts.length; p++) {
      const rings = parts[p];
      for (let r = 0; r < rings.length; r++) {
        const ring = rings[r];
        for (let i = 0; i < ring.length - 1; i++) {
          const dist = segmentDistance(x, y, ring[i][0], ring[i][1], ring[i + 1][0], ring[i + 1][1]);
          if (best == null || dist < best) best = dist;
        }
      }
    }
    return best == null ? 0 : best;
  }

  function geomCentroid(geom) {
    let area = 0;
    let sx = 0;
    let sy = 0;
    const parts = ringsOf(geom);
    for (let p = 0; p < parts.length; p++) {
      const ring = parts[p][0];
      if (!ring) continue;
      for (let i = 0; i < ring.length - 1; i++) {
        const x1 = ring[i][0];
        const y1 = ring[i][1];
        const x2 = ring[i + 1][0];
        const y2 = ring[i + 1][1];
        const cross = x1 * y2 - x2 * y1;
        area += cross;
        sx += (x1 + x2) * cross;
        sy += (y1 + y2) * cross;
      }
    }
    if (Math.abs(area) < 1e-9) {
      const ring = parts[0] && parts[0][0];
      return ring ? [ring[0][0], ring[0][1]] : [0, 0];
    }
    area *= 0.5;
    return [sx / (6 * area), sy / (6 * area)];
  }

  function geomBounds(geom) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    const parts = ringsOf(geom);
    for (let p = 0; p < parts.length; p++) {
      const rings = parts[p];
      for (let r = 0; r < rings.length; r++) {
        const ring = rings[r];
        for (let i = 0; i < ring.length; i++) {
          const x = ring[i][0];
          const y = ring[i][1];
          if (x < minX) minX = x;
          if (y < minY) minY = y;
          if (x > maxX) maxX = x;
          if (y > maxY) maxY = y;
        }
      }
    }
    return [minX, minY, maxX, maxY];
  }

  function sectorOnLand(x, y, provinceId) {
    return provinceAt(x, y) === provinceId;
  }

  function sectorPole(geom, provinceId) {
    const box = geomBounds(geom);
    const span = Math.max(box[2] - box[0], box[3] - box[1], 1);
    let step = span / 10;
    let best = null;
    let bestD = -1;
    let window = box;
    for (let pass = 0; pass < 5; pass++) {
      for (let x = window[0]; x <= window[2] + step * 0.01; x += step) {
        for (let y = window[1]; y <= window[3] + step * 0.01; y += step) {
          if (!geomContains(x, y, geom) || !sectorOnLand(x, y, provinceId)) continue;
          const dist = boundaryDistance(x, y, geom);
          if (dist > bestD) {
            bestD = dist;
            best = [x, y];
          }
        }
      }
      if (!best) break;
      step = Math.max(step / 3, 0.35);
      window = [best[0] - step * 4, best[1] - step * 4, best[0] + step * 4, best[1] + step * 4];
    }
    if (!best) return null;
    return { point: best, inradius: bestD };
  }

  function settlementMarkerList() {
    const sig = String(state.infraStamp || 0);
    if (state.settlementMarkers && state.settlementMarkerSig === sig) return state.settlementMarkers;
    const places = collectInfra().places;
    const byKey = new Map();
    Object.keys(places).forEach(function (id) {
      const place = places[id];
      if (!place || place.x == null || place.y == null) return;
      byKey.set(place.type + "|" + place.province + "|" + place.name, place);
    });
    const markers = [];
    (state.settlementRows || []).forEach(function (row) {
      let x = null;
      let y = null;
      let nation = null;
      if (row.type === "capital") {
        const owner = nationOwningCapital(row.province);
        if (!owner || !owner.capital_xy || owner.capital_xy.length < 2) return;
        x = owner.capital_xy[0];
        y = owner.capital_xy[1];
        nation = owner.id;
      } else {
        const place = byKey.get(row.type + "|" + row.province + "|" + row.name);
        if (!place) return;
        x = place.x;
        y = place.y;
        nation = place.nation;
      }
      markers.push({
        sector: row.sector,
        type: row.type,
        name: row.name,
        province: row.province,
        x: x,
        y: y,
        nation: nation,
      });
    });
    state.settlementMarkers = markers;
    state.settlementMarkerSig = sig;
    return markers;
  }

  function storedMarkerPoint(sectorId) {
    const markers = settlementMarkerList();
    for (let i = 0; i < markers.length; i++) {
      if (markers[i].sector === sectorId) return [markers[i].x, markers[i].y];
    }
    return null;
  }

  function sectorMarkerPoint(feature) {
    const id = feature.properties && feature.properties.id;
    if (!id) return null;
    const stored = storedMarkerPoint(id);
    if (stored) return stored;
    if (!state.sectorPoints) state.sectorPoints = new Map();
    if (state.sectorPoints.has(id)) return state.sectorPoints.get(id);
    const geom = feature.geometry;
    const provinceId = feature.properties.province;
    const pole = sectorPole(geom, provinceId);
    if (!pole) {
      state.sectorPoints.set(id, null);
      return null;
    }
    const centre = geomCentroid(geom);
    let chosen = pole.point;
    if (sectorOnLand(centre[0], centre[1], provinceId) && geomContains(centre[0], centre[1], geom)) {
      const clearance = boundaryDistance(centre[0], centre[1], geom);
      if (pole.inradius <= 0 || clearance >= 0.25 * pole.inradius) chosen = centre;
    }
    state.sectorPoints.set(id, chosen);
    return chosen;
  }

  function sectorIndex() {
    if (state.sectorById) return state.sectorById;
    const index = new Map();
    (state.sectorFeatures || []).forEach(function (feature) {
      const id = feature.properties && feature.properties.id;
      if (id) index.set(id, feature);
    });
    state.sectorById = index;
    return index;
  }

  function loadSectorLevels(data) {
    const levels = new Map();
    const provinces = data && data.provinces;
    if (!provinces) return levels;
    Object.keys(provinces).forEach(function (key) {
      const row = provinces[key];
      (row && row.sectors ? row.sectors : []).forEach(function (sector) {
        const level = sector && sector.level;
        if (level === "city" || level === "town" || level === "village" || level === "none") {
          levels.set(sector.sector, level);
        }
      });
    });
    return levels;
  }

  function nationOwningCapital(provinceId) {
    for (let i = 0; i < state.nations.length; i++) {
      if (state.nations[i].capital_province === provinceId) return state.nations[i];
    }
    return null;
  }

  function settlementLevel(feature) {
    const id = feature.properties.id;
    if (state.sectorLevels && state.sectorLevels.has(id)) return state.sectorLevels.get(id);
    if (feature.properties.letter !== "A") return "none";
    const nation = nationOwningCapital(feature.properties.province);
    if (nation) return nation.tier === "major" ? "city" : "town";
    return "village";
  }

  function settlementDisplayName(feature, level) {
    const id = feature.properties.id;
    const rows = state.settlementRows || [];
    let fallback = "";
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      if (row.sector !== id) continue;
      if (row.type === level) return row.name;
      if (row.type === "capital" && (level === "city" || level === "town")) return row.name;
      if (!fallback) fallback = row.name;
    }
    return fallback || kindLabel(level);
  }

  function addLedgerSettlements(group, grown, zoom, villages) {
    const index = sectorIndex();
    settlementMarkerList().forEach(function (marker) {
      const level = marker.type === "capital" ? "capital" : marker.type;
      if (villages) {
        if (level !== "village") return;
      } else if (level !== "city" && level !== "town" && level !== "capital") {
        return;
      }
      if (zoom < infraMinZoom(level)) return;
      const feature = index.get(marker.sector);
      const box = feature && feature.properties && feature.properties.bbox;
      if (box && (box[2] < grown.getWest() || box[0] > grown.getEast() || box[3] < grown.getSouth() || box[1] > grown.getNorth())) return;
      const provinceId = marker.province;
      if (isConcealed(provinceId)) return;
      const point = [marker.x, marker.y];
      if (!grown.contains(L.latLng(point[1], point[0]))) return;
      if (pointConcealed(point[0], point[1])) return;
      const size = level === "capital" ? 18 : level === "city" ? 16 : level === "town" ? 14 : 10;
      const name = marker.name;
      const nationId = marker.nation;
      const iconType = level === "capital" ? "capital" : level;
      const markerIcon = L.marker([point[1], point[0]], {
        icon: L.divIcon({
          className: "nb-infra nb-infra-" + iconType,
          html: infraSvg(iconType),
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        }),
        title: name,
        zIndexOffset: level === "capital" ? 260 : level === "village" ? 120 : 180,
        keyboard: true,
      });
      markerIcon.on("click", function (event) {
        L.DomEvent.stopPropagation(event);
        const html =
          level === "village"
            ? villageHtml({ name: name, nation: nationId, province: provinceId })
            : infraHtml({ type: iconType, name: name, nation: nationId, province: provinceId });
        openPlacePopup(event.latlng, html, nationId);
      });
      markerIcon.addTo(group);
    });
  }

  function sectorAnchors() {
    const sig = String(state.infraStamp || 0);
    if (state.sectorAnchors && state.sectorAnchorSig === sig) return state.sectorAnchors;
    const anchors = new Map();
    const index = sectorIndex();
    (state.buildings || []).forEach(function (row) {
      if (anchors.has(row.sector)) return;
      const feature = index.get(row.sector);
      if (!feature) return;
      const point = sectorMarkerPoint(feature);
      if (!point) return;
      anchors.set(row.sector, { x: point[0], y: point[1], rank: 0, province: feature.properties.province });
    });
    state.sectorAnchors = anchors;
    state.sectorAnchorSig = sig;
    return anchors;
  }

  function fortSvg(scaffold) {
    const ink = "#0B1A4A";
    const paper = "#E6E6EA";
    const tan = "#c4a574";
    const common = 'viewBox="0 0 20 20" aria-hidden="true"';
    if (scaffold) {
      return (
        '<svg ' +
        common +
        ' width="12" height="12"><path fill="none" stroke="' +
        tan +
        '" stroke-width="1.5" stroke-dasharray="2 1.4" d="M6 5h1.6V2.4H9.2V5h1.6V2.4h1.6V5h1.6V2.4H16v12.2H6Z"/><path fill="none" stroke="' +
        ink +
        '" stroke-width="1.1" stroke-dasharray="1.4 1.3" d="M8.4 17.2v-3.4h3.2v3.4"/></svg>'
      );
    }
    return (
      '<svg ' +
      common +
      ' width="12" height="12"><path fill="' +
      paper +
      '" stroke="' +
      ink +
      '" stroke-width="1.3" d="M5.2 6.2h2V3h2v3.2h2V3h2v3.2h2V3h2v3.2h1.6V17H5.2Z"/><path fill="' +
      tan +
      '" stroke="' +
      ink +
      '" stroke-width="1" d="M8.6 17v-3.6h2.8V17"/></svg>'
    );
  }

  function fortHtml(row) {
    const nation = nationByName(row.nation);
    const nationText = nation ? nationLabel(nation) : row.nation;
    return (
      '<div class="nb-pop"><h2>Fort</h2><p class="meta">' +
      esc(nationText) +
      '</p><p class="meta">' +
      esc(row.sector) +
      "</p></div>"
    );
  }

  function addSectorForts(group, grown, zoom) {
    if (zoom < 6) return;
    const anchors = sectorAnchors();
    const used = {};
    (state.buildings || []).forEach(function (row) {
      if (!buildingVisible(row)) return;
      const anchor = anchors.get(row.sector);
      if (!anchor) return;
      if (isConcealed(row.province) || isConcealed(anchor.province)) return;
      if (pointConcealed(anchor.x, anchor.y)) return;
      if (!grown.contains(L.latLng(anchor.y, anchor.x))) return;
      const slot = used[row.sector] || 0;
      used[row.sector] = slot + 1;
      const scaffold = row.status === "building";
      const marker = L.marker([anchor.y, anchor.x], {
        icon: L.divIcon({
          className: "nb-infra nb-infra-tower" + (scaffold ? " nb-infra-scaffold" : ""),
          html: fortSvg(scaffold),
          iconSize: [12, 12],
          iconAnchor: [-10 - slot * 14, 12],
        }),
        title: "Fort",
        zIndexOffset: 480,
        keyboard: true,
      });
      marker.on("click", function (event) {
        L.DomEvent.stopPropagation(event);
        openPopup(event.latlng, fortHtml(row));
      });
      marker.addTo(group);
    });
  }

  function renderInfra() {
    const map = state.map;
    if (!map) return;
    const zoom = map.getZoom();
    const band = zoom < 4 ? 0 : zoom < 5 ? 4 : zoom < 6 ? 5 : 6;
    const view = map.getBounds();
    const pad = state.infraPad;
    if (
      pad &&
      state.infraLayer &&
      pad.band === band &&
      pad.history === state.historyIndex &&
      pad.stamp === state.infraStamp &&
      pad.bounds.contains(view)
    ) {
      return;
    }
    const grown = view.pad(0.45);
    state.infraPad = {
      band: band,
      history: state.historyIndex,
      stamp: state.infraStamp || 0,
      bounds: grown,
    };
    if (state.infraLayer && map.hasLayer(state.infraLayer)) map.removeLayer(state.infraLayer);
    const group = L.layerGroup();
    const items = collectInfra();
    if (zoom >= infraMinZoom("road")) {
      Object.keys(items.roads).forEach(function (id) {
        const road = items.roads[id];
        if (road.points && road.points.length >= 2) {
          let minX = Infinity;
          let minY = Infinity;
          let maxX = -Infinity;
          let maxY = -Infinity;
          for (let i = 0; i < road.points.length; i++) {
            const x = road.points[i][0];
            const y = road.points[i][1];
            if (x < minX) minX = x;
            if (y < minY) minY = y;
            if (x > maxX) maxX = x;
            if (y > maxY) maxY = y;
          }
          if (maxX < grown.getWest() || minX > grown.getEast() || maxY < grown.getSouth() || minY > grown.getNorth()) return;
        }
        const stops = roadPoints(road);
        if (!stops) return;
        const line = L.polyline(
          stops.map(function (pt) {
            return [pt[1], pt[0]];
          }),
          {
            pane: "infra",
            renderer: state.infraRenderer,
            color: "#c4a574",
            weight: 1.75,
            opacity: 0.95,
            dashArray: "7 5",
            lineCap: "butt",
            lineJoin: "round",
            interactive: true,
          }
        );
        line.on("click", function (event) {
          L.DomEvent.stopPropagation(event);
          openPopup(event.latlng, infraHtml({ type: "road", name: road.name || "Road", nation: road.nation }));
        });
        line.addTo(group);
      });
    }
    Object.keys(items.places).forEach(function (id) {
      const place = items.places[id];
      if (place.type === "village" || place.type === "city" || place.type === "town" || place.type === "capital") return;
      if (zoom < infraMinZoom(place.type)) return;
      const xy = placePoint(place);
      if (!xy || !grown.contains(L.latLng(xy[1], xy[0]))) return;
      const size = place.type === "capital" ? 18 : place.type === "city" || place.type === "fort" || place.type === "port" || place.type === "shipyard" ? 16 : 14;
      const marker = L.marker([xy[1], xy[0]], {
        icon: L.divIcon({
          className: "nb-infra nb-infra-" + place.type,
          html: infraSvg(place.type),
          iconSize: [size, size],
          iconAnchor: place.type === "capital" ? [size / 2, 0] : [size / 2, size / 2],
        }),
        title: place.name || kindLabel(place.type),
        zIndexOffset: place.type === "capital" ? 260 : 180,
        keyboard: true,
      });
      marker.on("click", function (event) {
        L.DomEvent.stopPropagation(event);
        openPlacePopup(event.latlng, infraHtml(place), place.nation);
      });
      marker.addTo(group);
    });
    addLedgerSettlements(group, grown, zoom, false);
    addSectorForts(group, grown, zoom);
    state.infraLayer = group;
    syncInfraVisibility();
  }

  function villageHtml(entry) {
    const name = entry.name || "Village";
    const nation = infraNationText(entry) || "Unclaimed";
    return '<div class="nb-pop"><h2>' + esc(name) + '</h2><p class="tier">Village</p><p class="meta">' + esc(nation) + "</p></div>";
  }

  function renderVillages() {
    const map = state.map;
    if (!map) return;
    if (!state.overlays.sectors || map.getZoom() < 7) {
      dropLayer(state.villageLayer);
      state.villageLayer = null;
      state.villagePad = null;
      return;
    }
    const view = map.getBounds();
    const sig = String(state.historyIndex || 0) + ":" + String(state.infraStamp || 0);
    const pad = state.villagePad;
    if (pad && pad.sig === sig && state.villageLayer && pad.bounds.contains(view)) return;
    dropLayer(state.villageLayer);
    const grown = view.pad(0.45);
    const group = L.layerGroup();
    addLedgerSettlements(group, grown, map.getZoom(), true);
    state.villageLayer = group;
    state.villagePad = { sig: sig, bounds: grown, empty: !group.getLayers().length };
    if (!state.villagePad.empty) group.addTo(map);
  }

  function applyInfra(collection) {
    if (collection) state.infraRaw = collection;
    state.infraStamp = (state.infraStamp || 0) + 1;
    state.infraPad = null;
    state.infraCatalog = null;
    state.tradeGeom = null;
    state.tradePad = null;
    renderInfra();
    renderVillages();
  }

  function tradeEndpoint(value) {
    if (!value) return null;
    const nation = Number(value.nation != null ? value.nation : value.nation_id);
    const settlement = value.settlement != null ? value.settlement : value.name;
    if (!isFinite(nation) || settlement == null || String(settlement).trim() === "") return null;
    return { nation: nation, settlement: String(settlement) };
  }

  function catalogRoute(row) {
    if (!row || row.public !== true) return null;
    const kind = String(row.kind || "").toLowerCase();
    const status = String(row.status || "").toLowerCase();
    if (kind !== "land" && kind !== "sea") return null;
    if (status !== "active" && status !== "disrupted") return null;
    const from = tradeEndpoint(row.from);
    const to = tradeEndpoint(row.to);
    if (!from || !to) return null;
    const goods = row.goods == null ? "" : String(row.goods);
    if (!goods.trim()) return null;
    const id = row.id != null && row.id !== "" ? String(row.id) : kind + "-" + from.nation + "-" + to.nation + "-" + from.settlement;
    return {
      id: id,
      from: from,
      to: to,
      kind: kind,
      goods: goods,
      status: status,
      public: true,
      cycle: row.cycle,
      round: row.round,
    };
  }

  function collectTrade() {
    const raw = state.tradeRaw || { routes: [] };
    const fromHistory = (state.historyView && state.historyView.trade) || { routes: [], removed: [] };
    const removed = {};
    (fromHistory.removed || []).forEach(function (id) {
      removed[String(id)] = true;
    });
    const routes = {};
    (raw.routes || []).forEach(function (row) {
      const route = catalogRoute(row);
      if (!route || removed[route.id] || !infraDateVisible(route)) return;
      routes[route.id] = route;
    });
    (fromHistory.routes || []).forEach(function (route) {
      if (!route || route.public !== true || removed[route.id] || !infraDateVisible(route)) return;
      routes[route.id] = route;
    });
    return routes;
  }

  function findSettlement(endpoint) {
    const want = String(endpoint.settlement || "").trim().toLowerCase();
    const nation = Number(endpoint.nation);
    if (!want || !isFinite(nation)) return null;
    const places = collectInfra().places;
    let found = null;
    Object.keys(places).forEach(function (id) {
      const place = places[id];
      if (Number(place.nation) !== nation) return;
      if (String(place.name || "").trim().toLowerCase() !== want) return;
      found = place;
    });
    return found;
  }

  function findPort(nationId) {
    const places = collectInfra().places;
    let found = null;
    Object.keys(places).forEach(function (id) {
      const place = places[id];
      if (Number(place.nation) !== Number(nationId) || place.type !== "port") return;
      found = place;
    });
    return found;
  }

  function resolveTradeEnd(endpoint, kind) {
    if (!endpoint) return null;
    let place = findSettlement(endpoint);
    if (kind === "sea" && (!place || place.type !== "port")) place = findPort(endpoint.nation);
    if (!place) return null;
    const xy = placePoint(place);
    return xy ? { place: place, xy: xy } : null;
  }

  function landCell(lx, ly) {
    const meta = state.meta;
    const lut = state.lut;
    if (!meta || !lut) return false;
    if (lx < 0 || ly < 0 || lx >= meta.lookup_width || ly >= meta.lookup_height) return false;
    if (lx * meta.lookup_scale < meta.hidden_west_of) return false;
    const id = lut[ly * meta.lookup_width + lx] || 0;
    return !!(id && state.provinces.has(id) && !isConcealed(id));
  }

  function seaCell(lx, ly) {
    const meta = state.meta;
    const lut = state.lut;
    if (!meta || !lut) return false;
    if (lx < 0 || ly < 0 || lx >= meta.lookup_width || ly >= meta.lookup_height) return false;
    if (lx * meta.lookup_scale < meta.hidden_west_of) return false;
    if ((lut[ly * meta.lookup_width + lx] || 0) !== 0) return false;
    const x = lx * meta.lookup_scale + meta.lookup_scale / 2;
    const y = ly * meta.lookup_scale + meta.lookup_scale / 2;
    if (y < 0 || x > meta.width || y > meta.height) return false;
    return !concealedSeaAt(x, y);
  }

  function nearestCell(lx, ly, walk, radius) {
    if (walk(lx, ly)) return [lx, ly];
    for (let r = 1; r <= radius; r++) {
      for (let dy = -r; dy <= r; dy++) {
        for (let dx = -r; dx <= r; dx++) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
          if (walk(lx + dx, ly + dy)) return [lx + dx, ly + dy];
        }
      }
    }
    return null;
  }

  function segmentSamples(a, b) {
    const dist = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const steps = Math.max(1, Math.ceil(dist / 2));
    const out = [];
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    }
    return out;
  }

  function segmentOnSurface(a, b, walk) {
    const scale = state.meta.lookup_scale;
    const samples = segmentSamples(a, b);
    for (let i = 0; i < samples.length; i++) {
      if (!walk(Math.floor(samples[i][0] / scale), Math.floor(samples[i][1] / scale))) return false;
    }
    return true;
  }

  function surfaceKind(x, y) {
    const scale = state.meta.lookup_scale;
    const lx = Math.floor(x / scale);
    const ly = Math.floor(y / scale);
    if (pointConcealed(x, y)) return "hide";
    if (seaCell(lx, ly)) return "sea";
    if (landCell(lx, ly)) return "land";
    return "bad";
  }

  function pathAllowed(points, mode) {
    if (!points || points.length < 2 || !state.meta) return false;
    for (let i = 1; i < points.length; i++) {
      const flags = segmentSamples(points[i - 1], points[i]).map(function (pt) {
        return surfaceKind(pt[0], pt[1]);
      });
      if (flags.some(function (flag) { return flag === "hide" || flag === "bad"; })) return false;
      if (mode === "land") {
        if (flags.some(function (flag) { return flag !== "land"; })) return false;
        continue;
      }
      const first = i === 1;
      const last = i === points.length - 1;
      if (!first && !last) {
        if (flags.some(function (flag) { return flag !== "sea"; })) return false;
        continue;
      }
      if (first) {
        const seaAt = flags.indexOf("sea");
        if (seaAt < 0) return false;
        if (flags.slice(0, seaAt).some(function (flag) { return flag !== "land"; })) return false;
        if (flags.slice(seaAt).some(function (flag) { return flag !== "sea"; })) return false;
      } else {
        let lastSea = -1;
        flags.forEach(function (flag, index) {
          if (flag === "sea") lastSea = index;
        });
        if (lastSea < 0) return false;
        if (flags.slice(0, lastSea + 1).some(function (flag) { return flag !== "sea"; })) return false;
        if (flags.slice(lastSea + 1).some(function (flag) { return flag !== "land"; })) return false;
      }
    }
    return true;
  }

  function perpDist(point, a, b) {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len = Math.hypot(dx, dy);
    if (len < 1e-6) return Math.hypot(point[0] - a[0], point[1] - a[1]);
    return Math.abs((point[0] - a[0]) * dy - (point[1] - a[1]) * dx) / len;
  }

  function simplifySurface(points, tolerance, walk) {
    if (!points || points.length < 3) return points ? points.slice() : [];
    const keep = new Uint8Array(points.length);
    keep[0] = 1;
    keep[points.length - 1] = 1;
    const stack = [[0, points.length - 1]];
    while (stack.length) {
      const span = stack.pop();
      const start = span[0];
      const end = span[1];
      let maxD = 0;
      let index = -1;
      for (let i = start + 1; i < end; i++) {
        const dist = perpDist(points[i], points[start], points[end]);
        if (dist > maxD) {
          maxD = dist;
          index = i;
        }
      }
      const shortcut = maxD <= tolerance && segmentOnSurface(points[start], points[end], walk);
      if (!shortcut && index !== -1) {
        keep[index] = 1;
        if (index - start > 1) stack.push([start, index]);
        if (end - index > 1) stack.push([index, end]);
      }
    }
    const out = [];
    for (let i = 0; i < points.length; i++) if (keep[i]) out.push(points[i]);
    return out;
  }

  function bfsCells(start, goal, walk, pad) {
    const meta = state.meta;
    const westCell = Math.ceil(meta.hidden_west_of / meta.lookup_scale);
    const minX = Math.max(westCell, Math.min(start[0], goal[0]) - pad);
    const maxX = Math.min(meta.lookup_width - 1, Math.max(start[0], goal[0]) + pad);
    const minY = Math.max(0, Math.min(start[1], goal[1]) - pad);
    const maxY = Math.min(meta.lookup_height - 1, Math.max(start[1], goal[1]) + pad);
    if (start[0] < minX || goal[0] < minX || start[0] > maxX || goal[0] > maxX) return null;
    if (start[1] < minY || goal[1] < minY || start[1] > maxY || goal[1] > maxY) return null;
    const spanX = maxX - minX + 1;
    const spanY = maxY - minY + 1;
    const count = spanX * spanY;
    if (count < 1 || count > 450000) return null;
    const parent = new Int32Array(count);
    parent.fill(-1);
    function at(x, y) {
      return (y - minY) * spanX + (x - minX);
    }
    const source = at(start[0], start[1]);
    parent[source] = source;
    const qx = new Int32Array(count);
    const qy = new Int32Array(count);
    let head = 0;
    let tail = 0;
    qx[tail] = start[0];
    qy[tail] = start[1];
    tail += 1;
    const dirs = [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
      [1, 1],
      [1, -1],
      [-1, 1],
      [-1, -1],
    ];
    let found = false;
    while (head < tail) {
      const x = qx[head];
      const y = qy[head];
      head += 1;
      if (x === goal[0] && y === goal[1]) {
        found = true;
        break;
      }
      for (let d = 0; d < dirs.length; d++) {
        const nx = x + dirs[d][0];
        const ny = y + dirs[d][1];
        if (nx < minX || ny < minY || nx > maxX || ny > maxY) continue;
        if (!walk(nx, ny)) continue;
        if (dirs[d][0] && dirs[d][1] && (!walk(x + dirs[d][0], y) || !walk(x, y + dirs[d][1]))) continue;
        const next = at(nx, ny);
        if (parent[next] !== -1) continue;
        parent[next] = at(x, y);
        qx[tail] = nx;
        qy[tail] = ny;
        tail += 1;
      }
    }
    if (!found) return null;
    const cells = [];
    let cur = at(goal[0], goal[1]);
    let guard = 0;
    while (guard < count) {
      cells.push([minX + (cur % spanX), minY + Math.floor(cur / spanX)]);
      if (cur === source) break;
      cur = parent[cur];
      if (cur < 0) return null;
      guard += 1;
    }
    if (cur !== source) return null;
    cells.reverse();
    return cells;
  }

  function surfacePath(ax, ay, bx, by, mode) {
    const meta = state.meta;
    if (!meta || !state.lut) return null;
    const scale = meta.lookup_scale;
    const walk = mode === "sea" ? seaCell : landCell;
    const radius = mode === "sea" ? 8 : 6;
    const start = nearestCell(Math.floor(ax / scale), Math.floor(ay / scale), walk, radius);
    const goal = nearestCell(Math.floor(bx / scale), Math.floor(by / scale), walk, radius);
    if (!start || !goal) return null;
    const sep = Math.hypot(goal[0] - start[0], goal[1] - start[1]);
    let pad = Math.max(40, Math.ceil(sep));
    let cells = null;
    for (let attempt = 0; attempt < 3 && !cells; attempt++) {
      cells = bfsCells(start, goal, walk, pad);
      pad *= 2;
    }
    if (!cells || !cells.length) return null;
    let points = cells.map(function (cell) {
      return [cell[0] * scale + scale / 2, cell[1] * scale + scale / 2];
    });
    points = simplifySurface(points, 10, walk);
    if (mode === "sea") points = [[ax, ay]].concat(points, [[bx, by]]);
    else {
      if (Math.hypot(points[0][0] - ax, points[0][1] - ay) > 0.5) {
        if (!segmentOnSurface([ax, ay], points[0], walk)) return null;
        points.unshift([ax, ay]);
      }
      const last = points[points.length - 1];
      if (Math.hypot(last[0] - bx, last[1] - by) > 0.5) {
        if (!segmentOnSurface(last, [bx, by], walk)) return null;
        points.push([bx, by]);
      }
    }
    return pathAllowed(points, mode) ? points : null;
  }

  function buildRoadGraph() {
    const items = collectInfra();
    const nodes = new Map();
    function ensure(x, y) {
      const key = Math.round(x) + "," + Math.round(y);
      let node = nodes.get(key);
      if (!node) {
        node = { key: key, x: x, y: y, edges: [] };
        nodes.set(key, node);
      }
      return node;
    }
    Object.keys(items.roads).forEach(function (id) {
      const stops = roadPoints(items.roads[id]);
      if (!stops || stops.length < 2) return;
      const a = ensure(stops[0][0], stops[0][1]);
      const b = ensure(stops[stops.length - 1][0], stops[stops.length - 1][1]);
      if (a === b) return;
      let length = 0;
      for (let i = 1; i < stops.length; i++) {
        length += Math.hypot(stops[i][0] - stops[i - 1][0], stops[i][1] - stops[i - 1][1]);
      }
      a.edges.push({ to: b, points: stops, length: length });
      const reversed = stops.slice().reverse();
      b.edges.push({ to: a, points: reversed, length: length });
    });
    const parent = new Map();
    function find(key) {
      let root = parent.get(key);
      if (root == null) {
        parent.set(key, key);
        root = key;
      }
      if (root !== key) {
        root = find(root);
        parent.set(key, root);
      }
      return root;
    }
    function unite(a, b) {
      const ra = find(a);
      const rb = find(b);
      if (ra !== rb) parent.set(ra, rb);
    }
    nodes.forEach(function (node) {
      find(node.key);
      node.edges.forEach(function (edge) {
        unite(node.key, edge.to.key);
      });
    });
    return { nodes: nodes, find: find };
  }

  function snapNode(nodes, x, y, maxDist) {
    let best = null;
    let bestD = maxDist;
    nodes.forEach(function (node) {
      const dist = Math.hypot(node.x - x, node.y - y);
      if (dist <= bestD) {
        best = node;
        bestD = dist;
      }
    });
    return best;
  }

  function dijkstra(start) {
    const dist = new Map();
    const prev = new Map();
    dist.set(start.key, 0);
    const heap = [];
    function push(node, weight) {
      heap.push({ node: node, d: weight });
      let i = heap.length - 1;
      while (i > 0) {
        const parentIndex = (i - 1) >> 1;
        if (heap[parentIndex].d <= heap[i].d) break;
        const tmp = heap[parentIndex];
        heap[parentIndex] = heap[i];
        heap[i] = tmp;
        i = parentIndex;
      }
    }
    function pop() {
      const top = heap[0];
      const last = heap.pop();
      if (heap.length && last) {
        heap[0] = last;
        let i = 0;
        for (;;) {
          const left = i * 2 + 1;
          const right = left + 1;
          let small = i;
          if (left < heap.length && heap[left].d < heap[small].d) small = left;
          if (right < heap.length && heap[right].d < heap[small].d) small = right;
          if (small === i) break;
          const tmp = heap[small];
          heap[small] = heap[i];
          heap[i] = tmp;
          i = small;
        }
      }
      return top;
    }
    push(start, 0);
    while (heap.length) {
      const cur = pop();
      if (cur.d !== dist.get(cur.node.key)) continue;
      cur.node.edges.forEach(function (edge) {
        const next = cur.d + edge.length;
        const old = dist.get(edge.to.key);
        if (old == null || next < old) {
          dist.set(edge.to.key, next);
          prev.set(edge.to.key, { from: cur.node.key, points: edge.points });
          push(edge.to, next);
        }
      });
    }
    return { dist: dist, prev: prev };
  }

  function roadPath(start, goal) {
    if (!start || !goal) return null;
    if (start.key === goal.key) return [[start.x, start.y]];
    const found = dijkstra(start);
    if (!found.prev.has(goal.key)) return null;
    const parts = [];
    let key = goal.key;
    let guard = 0;
    while (key !== start.key && guard < 100000) {
      const step = found.prev.get(key);
      if (!step) return null;
      parts.push(step.points);
      key = step.from;
      guard += 1;
    }
    if (key !== start.key) return null;
    parts.reverse();
    const out = [];
    parts.forEach(function (pts) {
      for (let i = 0; i < pts.length; i++) {
        if (i === 0 && out.length) continue;
        out.push(pts[i]);
      }
    });
    return out.length >= 2 ? out : null;
  }

  function concatPaths(parts) {
    const out = [];
    parts.forEach(function (pts) {
      if (!pts) return;
      for (let i = 0; i < pts.length; i++) {
        if (out.length && Math.hypot(out[out.length - 1][0] - pts[i][0], out[out.length - 1][1] - pts[i][1]) < 0.5) continue;
        out.push(pts[i]);
      }
    });
    return out.length >= 2 ? out : null;
  }

  function routeHidden(points) {
    for (let i = 0; i < points.length; i++) {
      if (pointConcealed(points[i][0], points[i][1])) return true;
      if (i > 0 && segmentConcealed(points[i - 1], points[i])) return true;
    }
    return false;
  }

  function anchorEnds(points, a, b) {
    if (!points || points.length < 2) return points;
    const out = points.slice();
    if (Math.hypot(out[0][0] - a[0], out[0][1] - a[1]) > 1 && Math.hypot(out[0][0] - a[0], out[0][1] - a[1]) <= 36 && segmentOnSurface(a, out[0], landCell)) out.unshift(a);
    const last = out[out.length - 1];
    if (Math.hypot(last[0] - b[0], last[1] - b[1]) > 1 && Math.hypot(last[0] - b[0], last[1] - b[1]) <= 36 && segmentOnSurface(last, b, landCell)) out.push(b);
    return out;
  }

  function borderStitch(graph, snapA, snapB, a, b) {
    const rootA = graph.find(snapA.key);
    const rootB = graph.find(snapB.key);
    const listA = [];
    const listB = [];
    graph.nodes.forEach(function (node) {
      const root = graph.find(node.key);
      if (root === rootA) listA.push(node);
      else if (root === rootB) listB.push(node);
    });
    if (!listA.length || !listB.length) return null;
    const pairs = [];
    function consider(na, nb) {
      const gap = Math.hypot(na.x - nb.x, na.y - nb.y);
      pairs.push({ a: na, b: nb, gap: gap });
    }
    if (listA.length * listB.length <= 20000) {
      for (let i = 0; i < listA.length; i++) {
        for (let j = 0; j < listB.length; j++) consider(listA[i], listB[j]);
      }
    } else {
      function nearest(list, x, y, limit) {
        return list
          .slice()
          .sort(function (p, q) {
            return Math.hypot(p.x - x, p.y - y) - Math.hypot(q.x - x, q.y - y);
          })
          .slice(0, limit);
      }
      [
        [nearest(listA, b[0], b[1], 16), nearest(listB, b[0], b[1], 16)],
        [nearest(listA, a[0], a[1], 16), nearest(listB, a[0], a[1], 16)],
      ].forEach(function (pair) {
        pair[0].forEach(function (na) {
          pair[1].forEach(function (nb) {
            consider(na, nb);
          });
        });
      });
    }
    pairs.sort(function (p, q) {
      return p.gap - q.gap;
    });
    const seen = {};
    const picked = [];
    const minGap = pairs.length ? pairs[0].gap : 0;
    for (let i = 0; i < pairs.length && picked.length < 8; i++) {
      if (pairs[i].gap > minGap * 1.35 + 16) break;
      const key = pairs[i].a.key + ">" + pairs[i].b.key;
      if (seen[key]) continue;
      seen[key] = true;
      picked.push(pairs[i]);
    }
    const distA = dijkstra(snapA).dist;
    const distB = dijkstra(snapB).dist;
    let best = null;
    for (let i = 0; i < picked.length; i++) {
      const hop = surfacePath(picked[i].a.x, picked[i].a.y, picked[i].b.x, picked[i].b.y, "land");
      if (!hop) continue;
      let hopLen = 0;
      for (let k = 1; k < hop.length; k++) hopLen += Math.hypot(hop[k][0] - hop[k - 1][0], hop[k][1] - hop[k - 1][1]);
      const alongA = distA.get(picked[i].a.key);
      const alongB = distB.get(picked[i].b.key);
      if (alongA == null || alongB == null) continue;
      const score = alongA + hopLen + alongB;
      if (!best || score < best.score) best = { pair: picked[i], hop: hop, score: score };
    }
    if (!best) return null;
    const left = roadPath(snapA, best.pair.a);
    const right = roadPath(best.pair.b, snapB);
    if (!left || !right) return null;
    return concatPaths([left, best.hop, right]);
  }

  function landRoutePoints(a, b, graph) {
    const snapA = graph ? snapNode(graph.nodes, a[0], a[1], 32) : null;
    const snapB = graph ? snapNode(graph.nodes, b[0], b[1], 32) : null;
    if (snapA && snapB) {
      let along = null;
      if (graph.find(snapA.key) === graph.find(snapB.key)) along = roadPath(snapA, snapB);
      else along = borderStitch(graph, snapA, snapB, a, b);
      if (along) {
        const anchored = anchorEnds(along, a, b);
        if (anchored && !routeHidden(anchored)) return anchored;
      }
    }
    return surfacePath(a[0], a[1], b[0], b[1], "land");
  }

  function tradeGeometry() {
    const sig = String(state.infraStamp || 0) + ":" + String(state.historyIndex || 0) + ":" + String(state.tradeStamp || 0);
    if (state.tradeGeom && state.tradeGeomSig === sig) return state.tradeGeom;
    const routes = collectTrade();
    const ids = Object.keys(routes);
    const lines = [];
    if (ids.length && state.lut) {
      let graph = null;
      ids.forEach(function (id) {
        const route = routes[id];
        const from = resolveTradeEnd(route.from, route.kind);
        const to = resolveTradeEnd(route.to, route.kind);
        if (!from || !to) return;
        let points = null;
        if (route.kind === "sea") points = surfacePath(from.xy[0], from.xy[1], to.xy[0], to.xy[1], "sea");
        else {
          if (!graph) graph = buildRoadGraph();
          points = landRoutePoints(from.xy, to.xy, graph);
        }
        if (!points || points.length < 2) return;
        lines.push({ route: route, points: points });
      });
    }
    state.tradeGeom = lines;
    state.tradeGeomSig = sig;
    return lines;
  }

  function tradeStyle(route) {
    if (route.status === "disrupted") return { core: "#BC0501", glow: "#f6c1be", dash: "7 6" };
    if (route.kind === "sea") return { core: "#7ecbff", glow: "#d7f1ff", dash: null };
    return { core: "#e2b340", glow: "#fff1c2", dash: null };
  }

  function tradeHtml(route) {
    const fromNation = state.nationsById.get(Number(route.from.nation));
    const toNation = state.nationsById.get(Number(route.to.nation));
    const fromLabel = fromNation ? nationLabel(fromNation) : "Unclaimed";
    const toLabel = toNation ? nationLabel(toNation) : "Unclaimed";
    const title = route.from.settlement + " and " + route.to.settlement;
    const kind = route.kind === "sea" ? "Sea route" : "Land route";
    const status = route.status === "disrupted" ? "Disrupted" : "Active";
    return (
      '<div class="nb-pop"><h2>' +
      esc(title) +
      '</h2><p class="tier">' +
      esc(kind) +
      '</p><p class="meta">' +
      esc(fromLabel) +
      " and " +
      esc(toLabel) +
      '</p><p class="meta">' +
      esc(route.goods) +
      '</p><p class="meta">' +
      esc(status) +
      "</p></div>"
    );
  }

  function tradeLatLngs(points) {
    return points.map(function (pt) {
      return [pt[1], pt[0]];
    });
  }

  function renderTrade() {
    const map = state.map;
    if (!map) return;
    if (!state.overlays.trade) {
      if (state.tradeLayer && map.hasLayer(state.tradeLayer)) map.removeLayer(state.tradeLayer);
      return;
    }
    const sig = String(state.infraStamp || 0) + ":" + String(state.historyIndex || 0) + ":" + String(state.tradeStamp || 0);
    const view = map.getBounds();
    const pad = state.tradePad;
    if (pad && state.tradeLayer && map.hasLayer(state.tradeLayer) && pad.sig === sig && pad.bounds.contains(view)) return;
    const grown = view.pad(0.45);
    const lines = tradeGeometry();
    state.tradePad = { sig: sig, bounds: grown };
    if (state.tradeLayer && map.hasLayer(state.tradeLayer)) map.removeLayer(state.tradeLayer);
    const group = L.layerGroup();
    lines.forEach(function (line) {
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (let i = 0; i < line.points.length; i++) {
        const x = line.points[i][0];
        const y = line.points[i][1];
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
      if (maxX < grown.getWest() || minX > grown.getEast() || maxY < grown.getSouth() || minY > grown.getNorth()) return;
      const style = tradeStyle(line.route);
      const latlngs = tradeLatLngs(line.points);
      const cls = "nb-trade nb-trade-" + line.route.kind + " nb-trade-" + line.route.status;
      function stroke(extra) {
        const options = {
          pane: "trade",
          renderer: state.tradeRenderer,
          color: extra.color,
          weight: extra.weight,
          opacity: extra.opacity,
          lineCap: "round",
          lineJoin: "round",
          smoothFactor: 0,
          interactive: !!extra.interactive,
          className: cls,
        };
        if (extra.dash) options.dashArray = extra.dash;
        return L.polyline(latlngs, options);
      }
      stroke({ color: style.glow, weight: 7, opacity: 0.4, dash: style.dash, interactive: false }).addTo(group);
      stroke({ color: style.core, weight: 2.25, opacity: 0.95, dash: style.dash, interactive: false }).addTo(group);
      const hit = stroke({ color: style.core, weight: 14, opacity: 0.02, dash: null, interactive: true });
      hit.on("click", function (event) {
        L.DomEvent.stopPropagation(event);
        openPopup(event.latlng, tradeHtml(line.route));
      });
      hit.addTo(group);
    });
    state.tradeLayer = group;
    group.addTo(map);
  }

  function renderRivers() {
    const map = state.map;
    if (!map) return;
    const features = (state.rivers && state.rivers.features) || [];
    if (!features.length) {
      dropLayer(state.riverLayer);
      state.riverLayer = null;
      state.riverPad = null;
      return;
    }
    const view = map.getBounds();
    const pad = state.riverPad;
    if (pad && state.riverLayer && pad.bounds.contains(view)) return;
    const grown = view.pad(0.35);
    dropLayer(state.riverLayer);
    const group = L.layerGroup();
    features.forEach(function (feature) {
      const coords = feature.geometry && feature.geometry.coordinates;
      if (!coords || coords.length < 2) return;
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (let i = 0; i < coords.length; i++) {
        const x = coords[i][0];
        const y = coords[i][1];
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
      if (maxX < grown.getWest() || minX > grown.getEast() || maxY < grown.getSouth() || minY > grown.getNorth()) return;
      const flow = Math.max(1, Number((feature.properties && feature.properties.flow) || 1));
      const mouth = Math.min(3.6, 0.95 + Math.log10(flow) * 0.72);
      const source = Math.max(0.75, mouth * 0.36);
      const slices = coords.length > 8 ? 3 : 1;
      const step = Math.ceil(coords.length / slices);
      for (let slice = 0; slice < slices; slice++) {
        const part = coords.slice(slice * step, Math.min(coords.length, (slice + 1) * step + 1));
        if (part.length < 2) continue;
        const along = slices === 1 ? 1 : slice / (slices - 1);
        L.polyline(
          part.map(function (pt) {
            return [pt[1], pt[0]];
          }),
          {
            pane: "rivers",
            renderer: state.riverRenderer,
            color: "#5f9fbe",
            weight: source + (mouth - source) * along,
            opacity: 0.92,
            lineCap: "round",
            lineJoin: "round",
            smoothFactor: 0,
            interactive: false,
          }
        ).addTo(group);
      }
    });
    state.riverLayer = group;
    state.riverPad = { bounds: grown };
    if (group.getLayers().length) group.addTo(map);
  }

  function applyTrade(collection) {
    if (collection) state.tradeRaw = collection;
    state.tradeStamp = (state.tradeStamp || 0) + 1;
    state.tradeGeom = null;
    state.tradePad = null;
    renderTrade();
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

  function installOccPatterns(pairs, svg) {
    svg = svg || document.querySelector(".leaflet-history-pane svg");
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

  function loadSectorControl(text) {
    const rows = new Map();
    String(text || "")
      .split(/\r?\n/)
      .forEach(function (line, index) {
        if (!index || !line) return;
        const parts = line.split(",");
        const id = (parts[0] || "").trim();
        if (!id || id === "sector_id") return;
        const raw = parts.length > 1 ? parts[1].trim() : "";
        const cycle = Number(parts[2]);
        const controller = raw === "" ? null : Number(raw);
        if (!rows.has(id)) rows.set(id, []);
        rows.get(id).push({ controller: Number.isFinite(controller) ? controller : null, cycle: Number.isFinite(cycle) ? cycle : 0 });
      });
    return rows;
  }

  function activeController(id, owner) {
    const list = (state.sectorRows && state.sectorRows.get(id)) || [];
    const cycle = state.historyView && state.historyView.cycle != null ? Number(state.historyView.cycle) : 0;
    let chosen = null;
    list.forEach(function (row) {
      if (row.cycle > cycle) return;
      if (!chosen || row.cycle >= chosen.cycle) chosen = row;
    });
    if (!chosen) return owner == null ? null : owner;
    return chosen.controller;
  }

  function sectorSummary() {
    const sig = String(state.historyIndex || 0) + ":" + String(state.sectorStamp || 0);
    if (state.sectorSummary && state.sectorSummarySig === sig) return state.sectorSummary;
    const byId = new Map();
    const byProvince = new Map();
    (state.sectorFeatures || []).forEach(function (feature) {
      const pid = Number(feature.properties.province);
      const prov = state.provinces.get(pid);
      if (!provinceIsPublic(pid, prov) || isConcealed(pid)) return;
      const owner = legalOwnerId(pid, prov);
      const controller = activeController(feature.properties.id, owner);
      byId.set(feature.properties.id, { owner: owner, controller: controller, occupied: controller !== owner });
      let group = byProvince.get(pid);
      if (!group) {
        group = { owner: owner, counts: new Map() };
        byProvince.set(pid, group);
      }
      const key = controller == null ? "none" : String(controller);
      group.counts.set(key, (group.counts.get(key) || 0) + 1);
    });
    state.sectorSummary = { byId: byId, byProvince: byProvince };
    state.sectorSummarySig = sig;
    return state.sectorSummary;
  }

  function pointInRing(x, y, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const yi = ring[i][1];
      const yj = ring[j][1];
      if (yi > y === yj > y) continue;
      const xi = ring[i][0];
      const xj = ring[j][0];
      if (x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function pointInGeo(geom, x, y) {
    if (!geom) return false;
    const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
    for (let p = 0; p < polys.length; p++) {
      const rings = polys[p];
      if (!rings || !rings.length || !pointInRing(x, y, rings[0])) continue;
      let hole = false;
      for (let h = 1; h < rings.length; h++) {
        if (pointInRing(x, y, rings[h])) {
          hole = true;
          break;
        }
      }
      if (!hole) return true;
    }
    return false;
  }

  function sectorAt(x, y) {
    if (!state.overlays.sectors || !state.map || state.map.getZoom() < 6) return null;
    const summary = sectorSummary();
    const features = state.sectorFeatures || [];
    for (let i = 0; i < features.length; i++) {
      const feature = features[i];
      const box = feature.properties && feature.properties.bbox;
      if (!box || x < box[0] || x > box[2] || y < box[1] || y > box[3]) continue;
      if (!pointInGeo(feature.geometry, x, y)) continue;
      const info = summary.byId.get(feature.properties.id);
      if (!info) continue;
      return {
        id: feature.properties.id,
        owner: info.owner,
        controller: info.controller,
        occupied: info.occupied,
      };
    }
    return null;
  }

  function allianceMembers() {
    const members = new Map();
    const colors = {};
    (state.alliances || []).forEach(function (row) {
      const nation = nationByName(row.nation);
      if (!nation) return;
      if (!colors[row.bloc]) colors[row.bloc] = allianceColor(row.bloc);
      members.set(nation.id, { bloc: row.bloc, role: row.role, color: colors[row.bloc] });
    });
    return members;
  }

  function renderAlliances() {
    const map = state.map;
    if (!map) return;
    if (!state.overlays.alliances) {
      dropLayer(state.allianceLayer);
      state.allianceLayer = null;
      state.alliancePad = null;
      return;
    }
    const view = map.getBounds();
    const sig =
      String(state.historyIndex || 0) +
      ":" +
      (state.flatById ? "f" : "n") +
      ":" +
      (state.overlays.borders ? "b" : "o") +
      ":" +
      String((state.alliances || []).length);
    const pad = state.alliancePad;
    if (pad && pad.sig === sig && pad.bounds.contains(view) && (state.allianceLayer || pad.empty)) return;
    dropLayer(state.allianceLayer);
    const grown = view.pad(0.45);
    const members = allianceMembers();
    const features = [];
    if (members.size && state.flatById) {
      state.provinces.forEach(function (prov, id) {
        if (!provinceIsPublic(id, prov) || isConcealed(id)) return;
        if (prov.bbox && sectorOutside(prov.bbox, grown)) return;
        const owner = legalOwnerId(id, prov);
        const member = owner == null ? null : members.get(owner);
        if (!member) return;
        const feature = provinceFeature(id, { fill: member.color });
        if (feature) features.push(feature);
      });
    }
    const bounds = grown;
    if (!features.length) {
      state.allianceLayer = null;
      state.alliancePad = { sig: sig, bounds: bounds, empty: true };
      return;
    }
    const borders = state.overlays.borders;
    state.allianceLayer = L.geoJSON(
      { type: "FeatureCollection", features: features },
      {
        pane: "alliance",
        renderer: state.allianceRenderer,
        interactive: false,
        smoothFactor: 0,
        style: function (feature) {
          return {
            stroke: !!borders,
            color: "#16140f",
            weight: borders ? 1 : 0,
            opacity: 0.95,
            lineJoin: "round",
            fill: true,
            fillColor: feature.properties.fill,
            fillOpacity: 1,
            interactive: false,
          };
        },
      }
    );
    state.allianceLayer.addTo(map);
    state.alliancePad = { sig: sig, bounds: bounds, empty: false };
  }

  function loadReligions(text) {
    const nations = new Map();
    const provinces = new Map();
    String(text || "")
      .split(/\r?\n/)
      .forEach(function (line, index) {
        if (!index || !line.trim()) return;
        const parts = line.split(",");
        if (parts.length < 3) return;
        const target = parts[0].trim().toLowerCase();
        const id = Number(parts[1].trim());
        const name = parts[2].trim();
        const color = parts.length > 3 ? parts[3].trim() : "";
        if ((target !== "nation" && target !== "province") || !isFinite(id) || !name) return;
        const row = { name: name, color: color };
        if (target === "province") provinces.set(id, row);
        else nations.set(id, row);
      });
    return { nations: nations, provinces: provinces };
  }

  function religionColor(row) {
    const color = row && row.color;
    if (color && /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(color)) return color;
    return allianceColor(row && row.name);
  }

  function religionOfProvince(id, prov) {
    const table = state.religions;
    if (!table) return null;
    if (table.provinces && table.provinces.has(id)) return table.provinces.get(id);
    const owner = legalOwnerId(id, prov);
    if (owner != null && table.nations && table.nations.has(owner)) return table.nations.get(owner);
    return null;
  }

  function renderReligions() {
    const map = state.map;
    if (!map) return;
    if (!state.overlays.religion) {
      dropLayer(state.religionLayer);
      state.religionLayer = null;
      state.religionPad = null;
      return;
    }
    const view = map.getBounds();
    const sig =
      String(state.historyIndex || 0) +
      ":" +
      (state.flatById ? "f" : "n") +
      ":" +
      (state.overlays.borders ? "b" : "o");
    const pad = state.religionPad;
    if (pad && pad.sig === sig && pad.bounds.contains(view) && (state.religionLayer || pad.empty)) return;
    dropLayer(state.religionLayer);
    const grown = view.pad(0.45);
    const features = [];
    if (state.religions && state.flatById) {
      state.provinces.forEach(function (prov, id) {
        if (!provinceIsPublic(id, prov) || isConcealed(id)) return;
        if (prov.bbox && sectorOutside(prov.bbox, grown)) return;
        const row = religionOfProvince(id, prov);
        if (!row) return;
        const feature = provinceFeature(id, { fill: religionColor(row), religion: row.name });
        if (feature) features.push(feature);
      });
    }
    if (!features.length) {
      state.religionLayer = null;
      state.religionPad = { sig: sig, bounds: grown, empty: true };
      return;
    }
    const borders = state.overlays.borders;
    state.religionLayer = L.geoJSON(
      { type: "FeatureCollection", features: features },
      {
        pane: "religion",
        renderer: state.religionRenderer,
        interactive: false,
        smoothFactor: 0,
        style: function (feature) {
          return {
            stroke: !!borders,
            color: "#16140f",
            weight: borders ? 1 : 0,
            opacity: 0.95,
            lineJoin: "round",
            fill: true,
            fillColor: feature.properties.fill,
            fillOpacity: 0.72,
            interactive: false,
          };
        },
      }
    );
    state.religionLayer.addTo(map);
    state.religionPad = { sig: sig, bounds: grown, empty: false };
  }

  function sectorOutside(box, bounds) {
    return box[2] < bounds.getWest() || box[0] > bounds.getEast() || box[3] < bounds.getSouth() || box[1] > bounds.getNorth();
  }

  function sectorStyle(feature) {
    const props = feature.properties || {};
    if (props.role === "halo") {
      return {
        color: "#f6f0e2",
        weight: 3.2,
        opacity: 0.75,
        fill: false,
        interactive: false,
      };
    }
    if (props.role === "edge") {
      return {
        color: "#1a120c",
        weight: 1.35,
        opacity: 0.95,
        fill: false,
        interactive: false,
      };
    }
    if (props.occupied) {
      const style = occStyle(feature);
      style.interactive = props.scope !== "province";
      return style;
    }
    const flat = flatAmount(state.map ? state.map.getZoom() : 6);
    return {
      stroke: false,
      fill: true,
      fillColor: props.controller == null ? "#E6E6EA" : nationColor(props.controller),
      fillOpacity: flat > 0.85 ? 0 : 0.58,
      interactive: true,
    };
  }

  function sectorHtml(props) {
    const ownerNation = props.owner != null && state.nationsById.get(props.owner);
    const ctrlNation = props.controller != null && state.nationsById.get(props.controller);
    const ownerText = ownerNation ? nationLabel(ownerNation) : "Unclaimed";
    const ctrlText = props.controller == null ? "Unclaimed" : ctrlNation ? nationLabel(ctrlNation) : "nation " + props.controller;
    return (
      '<div class="nb-pop"><h2>' +
      esc(props.id) +
      '</h2><p class="tier">Sector</p><p class="meta">Controlled by ' +
      esc(ctrlText) +
      '</p><p class="meta">Owned by ' +
      esc(ownerText) +
      "</p>" +
      (props.occupied ? '<p class="claim">Occupied</p>' : "") +
      slotsMount(props.province || props.id) +
      "</div>"
    );
  }

  function onSectorClick(feature, layer) {
    if (!feature || !feature.properties || feature.properties.scope === "province" || feature.properties.role === "edge") return;
    layer.on("click", function (event) {
      L.DomEvent.stopPropagation(event);
      openPopup(event.latlng, sectorHtml(feature.properties));
    });
  }

  function renderSectors() {
    const map = state.map;
    if (!map) return;
    if (!state.overlays.sectors || !state.sectorFeatures) {
      dropLayer(state.sectorFillLayer);
      dropLayer(state.sectorLineLayer);
      state.sectorFillLayer = null;
      state.sectorLineLayer = null;
      state.sectorPad = null;
      return;
    }
    const zoom = map.getZoom();
    const parts = zoom >= 6;
    const view = map.getBounds();
    const flatBand = flatAmount(zoom) > 0.85 ? "s" : "t";
    const sig =
      (parts ? "p" : "w") +
      ":" +
      flatBand +
      ":" +
      (state.flatById ? "f" : "n") +
      ":" +
      String(state.historyIndex || 0) +
      ":" +
      String(state.sectorStamp || 0);
    const pad = state.sectorPad;
    if (pad && pad.sig === sig && pad.bounds.contains(view) && (state.sectorFillLayer || state.sectorLineLayer || pad.empty)) return;
    const summary = sectorSummary();
    const grown = view.pad(0.45);
    const fills = [];
    const lines = [];
    const halos = [];
    const edges = [];
    const pairs = [];
    if (parts) {
      state.sectorFeatures.forEach(function (feature) {
        const box = feature.properties.bbox;
        if (!box || sectorOutside(box, grown)) return;
        const info = summary.byId.get(feature.properties.id);
        if (!info) return;
        fills.push({
          type: "Feature",
          properties: {
            id: feature.properties.id,
            province: feature.properties.province,
            owner: info.owner,
            occupier: info.controller,
            controller: info.controller,
            occupied: info.occupied,
          },
          geometry: feature.geometry,
        });
        halos.push({
          type: "Feature",
          properties: { role: "halo", id: feature.properties.id },
          geometry: feature.geometry,
        });
        edges.push({
          type: "Feature",
          properties: { role: "edge", id: feature.properties.id },
          geometry: feature.geometry,
        });
        if (info.occupied) pairs.push({ owner: nationColor(info.owner), occupier: nationColor(info.controller) });
      });
    } else {
      summary.byProvince.forEach(function (group, pid) {
        const keys = Array.from(group.counts.keys());
        const ownerKey = group.owner == null ? "none" : String(group.owner);
        if (keys.length === 1 && keys[0] === ownerKey) return;
        let occupier = null;
        let best = -1;
        keys.forEach(function (key) {
          if (key === ownerKey) return;
          const count = group.counts.get(key);
          if (count > best) {
            best = count;
            occupier = key === "none" ? null : Number(key);
          }
        });
        const feature = provinceFeature(pid, {
          scope: "province",
          id: pid,
          owner: group.owner,
          occupier: occupier,
          occupied: true,
        });
        if (!feature) return;
        fills.push(feature);
        pairs.push({ owner: nationColor(group.owner), occupier: nationColor(occupier) });
      });
    }
    halos.forEach(function (feature) {
      lines.push(feature);
    });
    edges.forEach(function (feature) {
      lines.push(feature);
    });
    dropLayer(state.sectorFillLayer);
    dropLayer(state.sectorLineLayer);
    state.sectorFillLayer = null;
    state.sectorLineLayer = null;
    state.sectorPad = { sig: sig, bounds: grown, empty: !fills.length && !lines.length };
    if (fills.length) {
      state.sectorFillLayer = L.geoJSON(
        { type: "FeatureCollection", features: fills },
        {
          pane: "sector",
          renderer: state.sectorRenderer,
          smoothFactor: 0,
          style: sectorStyle,
          onEachFeature: onSectorClick,
        }
      );
      state.sectorFillLayer.addTo(map);
      if (pairs.length) {
        installOccPatterns(pairs, document.querySelector(".leaflet-sector-pane svg"));
        state.sectorFillLayer.setStyle(sectorStyle);
      }
    }
    if (lines.length) {
      state.sectorLineLayer = L.geoJSON(
        { type: "FeatureCollection", features: lines },
        {
          pane: "sectorline",
          renderer: state.sectorLineRenderer,
          smoothFactor: 0,
          interactive: false,
          style: sectorStyle,
        }
      );
      state.sectorLineLayer.addTo(map);
    }
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
    renderSectors();
    renderAlliances();
    renderReligions();
    restyleFlatForHistory();
    if (state.infraRaw) applyInfra();
    if (state.tradeRaw) renderTrade();
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
    const [world, claims, infra, trade, settlements, sectors, sectorControl, buildings, alliances, religions, provinceNames, flags, slots, rivers, lut] = await Promise.all([
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
      fetch("data/infrastructure.json?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.json() : { capitals: true, places: [], roads: [] };
        })
        .catch(function () {
          return { capitals: true, places: [], roads: [] };
        }),
      fetch("data/trade_routes.json?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.json() : { routes: [] };
        })
        .catch(function () {
          return { routes: [] };
        }),
      fetch("data/settlements.csv?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.text() : "";
        })
        .catch(function () {
          return "";
        }),
      fetch("data/sectors.geojson")
        .then(function (res) {
          return res.ok ? res.json() : { features: [] };
        })
        .catch(function () {
          return { features: [] };
        }),
      fetch("data/sector_control.csv?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.text() : "";
        })
        .catch(function () {
          return "";
        }),
      fetch("data/buildings.csv?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.text() : "";
        })
        .catch(function () {
          return "";
        }),
      fetch("data/alliances.csv?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.text() : "";
        })
        .catch(function () {
          return "";
        }),
      fetch("data/religions.csv?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.text() : "";
        })
        .catch(function () {
          return "";
        }),
      fetch("data/province_names.csv?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.text() : "";
        })
        .catch(function () {
          return "";
        }),
      fetch("data/flags.json?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.json() : {};
        })
        .catch(function () {
          return {};
        }),
      fetch("data/province_slots.json?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.json() : null;
        })
        .catch(function () {
          return null;
        }),
      fetch("data/rivers.geojson?t=" + stamp, { cache: "no-store" })
        .then(function (res) {
          return res.ok ? res.json() : { features: [] };
        })
        .catch(function () {
          return { features: [] };
        }),
      loadLookup(meta),
    ]);
    state.lut = lut;
    indexWorld(world);
    state.claims = normalizeClaims(claims);
    state.capitalNames = loadCapitalNames(settlements);
    state.settlementRows = loadSettlementRows(settlements);
    state.buildings = loadBuildings(buildings);
    state.alliances = loadAlliances(alliances);
    state.religions = loadReligions(religions);
    state.provinceNames = loadProvinceNames(provinceNames);
    state.flags = loadFlags(flags);
    state.rivers = rivers && rivers.features ? rivers : { features: [] };
    renderRivers();
    state.sectorFeatures = (sectors && sectors.features) || [];
    state.sectorById = null;
    state.sectorPoints = new Map();
    state.sectorLevels = loadSectorLevels(slots);
    state.sectorAnchors = null;
    state.sectorRows = loadSectorControl(sectorControl);
    state.historySnapshots = await loadHistory();
    installTimeline();
    goToHistory(Math.max(0, state.historySnapshots.length - 1), { force: true, silent: true });
    state.map.on("click", onMapClick);
    state.map.on("popupclose", hideFlagCard);
    if (state.concealedKey == null) {
      state.concealedKey = "";
      applyClaims();
    }
    applyInfra(infra);
    applyTrade(trade);
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
