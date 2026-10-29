/* Province building yields from terrain shares (world.json mix pairs).
   The same 20% rules are described in data/province_slots.json meta.rules.
   limits check only compares built counts with each max; it does not score iron. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NBYields = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  var MINE_HILLS_MOUNTAINS_MIN_PERCENT = 20;
  var LUMBER_FOREST_JUNGLE_MIN_PERCENT = 20;
  var MINE_IRON_WITH_HILLS = 2;
  var MINE_IRON_BOG = 1;
  var MINE_TERRAINS = { hills: true, mountains: true };
  var LUMBER_TERRAINS = { forest: true, jungle: true };

  function terrainShare(mix, wanted) {
    var rows = Array.isArray(mix) ? mix : [];
    var sum = 0;
    for (var i = 0; i < rows.length; i++) {
      var pair = rows[i];
      if (!pair || pair.length < 2) continue;
      var name = String(pair[0] || "")
        .trim()
        .toLowerCase();
      if (!wanted[name]) continue;
      var pct = Number(pair[1]);
      if (!isFinite(pct)) continue;
      sum += pct;
    }
    return sum;
  }

  function formatShare(value) {
    var n = Number(value);
    if (!isFinite(n)) return "0";
    if (Math.abs(n - Math.round(n)) < 0.05) return String(Math.round(n));
    return n.toFixed(1);
  }

  function mineLine(mix) {
    var share = terrainShare(mix, MINE_TERRAINS);
    if (share >= MINE_HILLS_MOUNTAINS_MIN_PERCENT) {
      return "Mine: +" + MINE_IRON_WITH_HILLS + " iron (hills/mountains)";
    }
    return "Mine: +" + MINE_IRON_BOG + " iron (bog iron)";
  }

  function lumberLine(mix) {
    var share = terrainShare(mix, LUMBER_TERRAINS);
    if (share >= LUMBER_FOREST_JUNGLE_MIN_PERCENT) {
      return "Lumber Camp: allowed (forest/jungle " + formatShare(share) + "%)";
    }
    return "Lumber Camp: not allowed (needs " + LUMBER_FOREST_JUNGLE_MIN_PERCENT + "% forest/jungle)";
  }

  function linesFromMix(mix) {
    return {
      mine: mineLine(mix),
      lumber: lumberLine(mix),
      hillsMountains: terrainShare(mix, MINE_TERRAINS),
      forestJungle: terrainShare(mix, LUMBER_TERRAINS),
    };
  }

  return {
    MINE_HILLS_MOUNTAINS_MIN_PERCENT: MINE_HILLS_MOUNTAINS_MIN_PERCENT,
    LUMBER_FOREST_JUNGLE_MIN_PERCENT: LUMBER_FOREST_JUNGLE_MIN_PERCENT,
    MINE_IRON_WITH_HILLS: MINE_IRON_WITH_HILLS,
    MINE_IRON_BOG: MINE_IRON_BOG,
    terrainShare: terrainShare,
    formatShare: formatShare,
    mineLine: mineLine,
    lumberLine: lumberLine,
    linesFromMix: linesFromMix,
  };
});
