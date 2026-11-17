/* Selected-turn political fill.
   The baked political paint (flat.geojson and the political tiles) stays the
   cycle-0 picture. This module decides how the page covers that paint:
   annexed land, and any province whose nation colour no longer matches the
   bake, including a nation's original core. Claims hatching and occupation
   stripes are other layers; they are not inputs. */
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
    // A nation can change colour without changing owner. The bake still has
    // the old pixels, so the core has to be plated the same way an annexation is.
    var recolor = !!(own && bakedColor && normColor(own) !== normColor(bakedColor));
    var plate = !!own && (annexed || recolor);
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

    // Who holds a province for the popup count and the click highlight.
    // A transfer sets owner. An occupation moves the hold to the occupier
    // without changing the owner the popup sentence still names.
    function controlledTerritory(rows) {
      var byNation = {};
      (rows || []).forEach(function (row) {
        if (!row) return;
        var id = asId(row.id);
        var occupier = row.occupier == null || row.occupier === "" ? null : asId(row.occupier);
        var owner = row.owner == null || row.owner === "" ? null : asId(row.owner);
        var holder = occupier != null ? occupier : owner;
        if (id == null || holder == null) return;
        var key = String(holder);
        if (!byNation[key]) byNation[key] = [];
        byNation[key].push(id);
      });
      Object.keys(byNation).forEach(function (key) {
        byNation[key].sort(function (a, b) {
          return a - b;
        });
      });
      return byNation;
    }

    // Bryn (province 160) and Neth (province 18) are unclaimed island halls.
    // From Cycle 2 they are confederated with The Iron North (nation 236).
    // Alliances do not list them: the halls are not nations, so a bloc row
    // cannot name them. A bloc of equals (role ally or confederation, no
    // leader and no vassal) is striped the same way when the data has one.
    var CONFEDERATION_FROM_CYCLE = 2;
    var CONFEDERATION_PARTNER = 236;
    var CONFEDERATION_HALLS = { 160: "Bryn", 18: "Neth" };
    var HALL_PALETTE = ["#2f6fbe", "#c45c26", "#1f8a70", "#8a4baf", "#b08900", "#a33b55", "#3d6b9a", "#6b7c3a"];

    function paletteColor(name) {
      var hash = 0;
      var text = String(name || "");
      for (var i = 0; i < text.length; i++) hash = (hash * 33 + text.charCodeAt(i)) >>> 0;
      return HALL_PALETTE[hash % HALL_PALETTE.length];
    }

    function confederationHoldings(cycle) {
      if (Number(cycle) < CONFEDERATION_FROM_CYCLE) return [];
      return [
        { province: 160, partner: CONFEDERATION_PARTNER, hall: "Bryn" },
        { province: 18, partner: CONFEDERATION_PARTNER, hall: "Neth" },
      ];
    }

    function allianceConfederation(spec) {
      var alliances = spec.alliances || [];
      var ownerName = spec.ownerName;
      if (!ownerName) return null;
      var bloc = null;
      var role = null;
      for (var i = 0; i < alliances.length; i++) {
        if (!alliances[i] || alliances[i].nation !== ownerName) continue;
        bloc = alliances[i].bloc;
        role = String(alliances[i].role || "").toLowerCase();
        break;
      }
      if (!bloc || (role !== "ally" && role !== "confederation")) return null;
      var members = [];
      for (var j = 0; j < alliances.length; j++) {
        if (!alliances[j] || alliances[j].bloc !== bloc) continue;
        var memberRole = String(alliances[j].role || "").toLowerCase();
        if (memberRole === "leader" || memberRole === "vassal") return null;
        if (memberRole !== "ally" && memberRole !== "confederation") return null;
        members.push(alliances[j]);
      }
      if (members.length < 2) return null;
      var colors = spec.memberColors || [];
      if (colors.length < 2) return null;
      return { label: String(bloc), colors: [colors[0], colors[1]], partnerId: null, hall: null };
    }

    function confederationFill(spec) {
      spec = spec || {};
      var fromAlliance = allianceConfederation(spec);
      if (fromAlliance) return fromAlliance;
      var cycle = Number(spec.cycle);
      if (!isFinite(cycle) || cycle < CONFEDERATION_FROM_CYCLE) return null;
      var hall = CONFEDERATION_HALLS[asId(spec.provinceId)];
      if (!hall) return null;
      var partner = spec.partnerColor || "#c5b3a1";
      var other = paletteColor(hall);
      if (normColor(partner) === normColor(other)) {
        other = HALL_PALETTE[(HALL_PALETTE.indexOf(other) + 1) % HALL_PALETTE.length];
      }
      return {
        label: "Bryn and Neth confederation",
        partnerId: CONFEDERATION_PARTNER,
        hall: hall,
        colors: [partner, other],
      };
    }

    return {
      resolvedOwner: resolvedOwner,
      provincePaint: provincePaint,
      recolorTilePixel: recolorTilePixel,
      shouldPaintHistoryOwnerFill: shouldPaintHistoryOwnerFill,
      borderPaneZ: borderPaneZ,
      flatBorderPaneZ: flatBorderPaneZ,
      controlledTerritory: controlledTerritory,
      confederationHoldings: confederationHoldings,
      confederationFill: confederationFill,
    };
});
