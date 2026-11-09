/* Selected-turn political fill for provinces whose owner has changed.
   The baked political paint (flat.geojson and the political tiles) stays the
   cycle-0 picture. This module decides how the page covers that paint.
   Claims hatching and occupation stripes are other layers; they are not inputs. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.NB_FILL = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function normColor(color) {
    return String(color || "").toLowerCase();
  }

  function asId(value) {
    if (value == null || value === "") return null;
    var n = Number(value);
    return isFinite(n) ? n : null;
  }

  function sameId(a, b) {
    return asId(a) === asId(b);
  }

  // Folded transfers win. A province with no transfer yet keeps world.json,
  // which is the latest owner in that file, not the painted original owner.
  function resolvedOwner(historyOwners, provinceId, worldOwner) {
    var owners = historyOwners || {};
    if (Object.prototype.hasOwnProperty.call(owners, provinceId) || Object.prototype.hasOwnProperty.call(owners, String(provinceId))) {
      var stored = owners[provinceId];
      if (stored === undefined) stored = owners[String(provinceId)];
      return stored == null || stored === "" ? null : stored;
    }
    return worldOwner == null || worldOwner === "" ? null : worldOwner;
  }

  function provincePaint(spec) {
    spec = spec || {};
    var owner = spec.owner == null || spec.owner === "" ? null : spec.owner;
    var own = spec.ownColor || null;
    var paint = spec.paintColor || own;
    var bakedColor = spec.bakedColor || "";
    var mode = spec.mode || "auto";
    var flat = Number(spec.flat);
    if (!isFinite(flat)) flat = 0;
    if (flat < 0) flat = 0;
    if (flat > 1) flat = 1;
    var politicalOpacity = Number(spec.politicalOpacity);
    if (!isFinite(politicalOpacity)) politicalOpacity = 1;
    var show = mode === "political" || mode === "auto";
    var annexed = owner != null && !sameId(owner, spec.bakedNationId);
    var plate = annexed && !!own;
    var wash = false;
    if (paint) {
      if (plate && own && normColor(paint) !== normColor(own)) wash = true;
      if (!plate && bakedColor && normColor(paint) !== normColor(bakedColor)) wash = true;
    }
    // Political view keeps an opaque plate over the old paint. Auto view must
    // use the same alpha as the political photo (terrain shows through) and
    // remove that photo underneath. A translucent plate on top of the old
    // paint would bring the previous owner back.
    var photo = 1 - flat;
    var plateOpacity = 0;
    var autoTintOpacity = 0;
    if (mode === "political" && plate) plateOpacity = flat >= 0.98 ? 0 : 1;
    if (mode === "auto" && plate && flat < 0.98) autoTintOpacity = politicalOpacity * photo;
    if (autoTintOpacity < 0) autoTintOpacity = 0;
    var maskBaked = mode === "auto" && plate && autoTintOpacity > 0.01 && autoTintOpacity < 0.99;
    var washOpacity = 0;
    if (show && wash) washOpacity = politicalOpacity * photo;
    if (washOpacity < 0) washOpacity = 0;
    return {
      owner: owner,
      ownColor: own,
      paintColor: paint,
      annexed: annexed,
      plate: plate,
      wash: wash,
      plateOpacity: plateOpacity,
      autoTintOpacity: autoTintOpacity,
      maskBaked: maskBaked,
      washOpacity: washOpacity,
      hidesBaked: plateOpacity >= 0.99 || autoTintOpacity >= 0.99 || maskBaked,
      claimsChangeFill: false,
      occupationChangesOwner: false,
    };
  }

    // A pixel in the fringe around an annexed polygon is recolored only when
    // it still looks like the baked owner. Ocean and a neighbouring nation
    // stay as they are. The province interior is always recolored.
    function recolorTilePixel(rgb, baked, interior, inHalo) {
      if (interior) return true;
      if (!inHalo || !baked || !rgb) return false;
      var dr = rgb[0] - baked[0];
      var dg = rgb[1] - baked[1];
      var db = rgb[2] - baked[2];
      return dr * dr + dg * dg + db * db <= 100 * 100;
    }

    // The political photo already shows the baked nation. A history-pane fill
    // is not used to repaint that colour. world.json may already name a later
    // owner; that mismatch must not lay a solid NPC fill over Auto terrain.
    // Conquest is recolored in the photo, not by this overlay.
    function shouldPaintHistoryOwnerFill(selectedOwner, bakedNation, worldNation) {
      void selectedOwner;
      void bakedNation;
      void worldNation;
      return false;
    }

    // Photo borders and the close-up vector borders have to sit above every
    // fill that repaints a province: ownership plates (357), vassal stripes
    // (364), occupation (380), alliance (382), and religion (384).
    // Infrastructure stays above the lines at 390.
    function borderPaneZ() {
      return 386;
    }

    function flatBorderPaneZ() {
      return 387;
    }

    return {
      resolvedOwner: resolvedOwner,
      provincePaint: provincePaint,
      recolorTilePixel: recolorTilePixel,
      shouldPaintHistoryOwnerFill: shouldPaintHistoryOwnerFill,
      borderPaneZ: borderPaneZ,
      flatBorderPaneZ: flatBorderPaneZ,
    };
});
