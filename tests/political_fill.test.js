const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

require("../js/history.js");
const fill = require("../js/political_fill.js");

const ROOT = path.join(__dirname, "..");

function readJson(rel) {
  return JSON.parse(fs.readFileSync(path.join(ROOT, rel), "utf8"));
}

const world = readJson("data/world.json");
const nations = new Map(world.nations.map(function (nation) {
  return [nation.id, nation];
}));
const index = readJson("data/history/index.json");
const snaps = index.map(function (name) {
  return readJson(path.join("data/history", name));
});
const flat = readJson("data/flat.geojson");
const baked = new Map();
flat.features.forEach(function (feature) {
  const props = feature.properties || {};
  if (props.kind === "province") baked.set(props.id, props);
});

const VERILLIAN = [2200, 2206];
const TERRILLIA = [2127, 2117];
const OCCUPIED = [1642, 1616];
const VOSUR_OCCUPIED = 2242;
const HOME = { verillian: 2201, terrillia: 2118 };

function fold(turn) {
  return global.NB_HISTORY.fold(snaps, turn, 3592);
}

function paintColor(ownerId, alliances) {
  const nation = nations.get(ownerId);
  if (!nation) return null;
  const row = (alliances || []).find(function (item) {
    return item.nation === nation.short_name;
  });
  if (!row || row.role !== "vassal") return nation.color;
  const leaderRow = (alliances || []).find(function (item) {
    return item.bloc === row.bloc && item.role === "leader";
  });
  const leader = leaderRow && world.nations.find(function (item) {
    return item.short_name === leaderRow.nation;
  });
  return leader ? leader.color : nation.color;
}

function decision(turn, provinceId, options) {
  const view = fold(turn);
  const bakedProps = baked.get(provinceId);
  const worldOwner = world.provinces[String(provinceId)].nation_id;
  const owner = fill.resolvedOwner(view.owners, provinceId, worldOwner);
  const nation = owner == null ? null : nations.get(owner);
  return fill.provincePaint(Object.assign({
    owner: owner,
    bakedNationId: bakedProps.nation_id,
    bakedColor: bakedProps.color,
    ownColor: nation ? nation.color : null,
    paintColor: owner == null ? null : paintColor(owner, view.alliances),
    mode: "political",
    flat: 0,
    politicalOpacity: 0.28,
    claimsOn: true,
    occupiedOn: true,
  }, options));
}

function ownersAt(turn, ids) {
  return ids.map(function (id) {
    return decision(turn, id).owner;
  });
}

test("resolved owner prefers a history transfer over the current world owner", function () {
  assert.equal(fill.resolvedOwner({ 2200: 276 }, 2200, 21), 276);
  assert.equal(fill.resolvedOwner({}, 2200, 21), 21);
  assert.equal(fill.resolvedOwner({ 2200: null }, 2200, 21), null);
});

test("slider 0 to 1 to 2 to 1 to 0 restores annexed owners", function () {
  const sequence = [0, 1, 2, 1, 0];
  const verillian = sequence.map(function (turn) {
    return ownersAt(turn, VERILLIAN).join(",");
  });
  const terrillia = sequence.map(function (turn) {
    return ownersAt(turn, TERRILLIA).join(",");
  });
  assert.deepEqual(verillian, ["276,210", "21,21", "21,21", "21,21", "276,210"]);
  assert.deepEqual(terrillia, ["368,368", "368,368", "229,229", "368,368", "368,368"]);
});

test("cycle 0 keeps the baked colour and later turns cover it", function () {
  VERILLIAN.forEach(function (id) {
    const start = decision(0, id);
    assert.equal(start.annexed, false);
    assert.equal(start.plate, false);
    assert.equal(start.hidesBaked, false);
    assert.equal(start.ownColor, baked.get(id).color);
  });
  [1, 2].forEach(function (turn) {
    VERILLIAN.forEach(function (id) {
      const painted = decision(turn, id);
      assert.equal(painted.owner, 21);
      assert.equal(painted.ownColor, "#3efec9");
      assert.equal(painted.plate, true);
      assert.equal(painted.hidesBaked, true);
      assert.equal(painted.plateOpacity, 1);
      assert.equal(painted.wash, false);
      assert.notEqual(painted.ownColor.toLowerCase(), baked.get(id).color.toLowerCase());
    });
  });
  TERRILLIA.forEach(function (id) {
    assert.equal(decision(0, id).hidesBaked, false);
    assert.equal(decision(1, id).hidesBaked, false);
    assert.equal(decision(1, id).ownColor, "#b0579a");
    const conquered = decision(2, id);
    assert.equal(conquered.owner, 229);
    assert.equal(conquered.ownColor, "#75813b");
    assert.equal(conquered.paintColor, "#3efec9");
    assert.equal(conquered.hidesBaked, true);
    assert.equal(conquered.wash, true);
    assert.ok(conquered.washOpacity > 0 && conquered.washOpacity < 1);
  });
});

test("going back to cycle 0 drops the conquest plate", function () {
  assert.equal(decision(2, 2200).hidesBaked, true);
  assert.equal(decision(2, 2127).hidesBaked, true);
  assert.equal(decision(0, 2200).hidesBaked, false);
  assert.equal(decision(0, 2127).hidesBaked, false);
  assert.equal(decision(1, 2127).owner, 368);
  assert.equal(decision(1, 2127).hidesBaked, false);
});

test("occupation does not change the owner plate", function () {
  OCCUPIED.concat([VOSUR_OCCUPIED]).forEach(function (id) {
    [0, 1, 2].forEach(function (turn) {
      const painted = decision(turn, id);
      assert.equal(painted.annexed, false);
      assert.equal(painted.hidesBaked, false);
      assert.equal(painted.occupationChangesOwner, false);
      assert.equal(painted.ownColor.toLowerCase(), baked.get(id).color.toLowerCase());
    });
  });
  const cycle0 = fold(0);
  const cycle1 = fold(1);
  const cycle2 = fold(2);
  const cycle1Again = fold(1);
  const back = fold(0);
  function occIds(view) {
    return view.occupations.map(function (row) {
      return row.province;
    }).sort(function (a, b) {
      return a - b;
    });
  }
  assert.deepEqual(occIds(cycle0), []);
  assert.deepEqual(occIds(cycle1), [1616, 1642]);
  assert.ok(occIds(cycle2).indexOf(1642) >= 0);
  assert.ok(occIds(cycle2).indexOf(1616) >= 0);
  assert.ok(occIds(cycle2).indexOf(2242) >= 0);
  assert.deepEqual(occIds(cycle1Again), [1616, 1642]);
  assert.deepEqual(occIds(back), []);
  assert.equal(cycle2.occupations.find(function (row) {
    return row.province === 1642;
  }).occupier, 23);
  assert.equal(decision(2, 1642).owner, 83);
  assert.equal(decision(2, 2242).owner, 275);
});

test("claim and occupation toggles do not change the political fill", function () {
  const on = decision(2, 2200, { claimsOn: true, occupiedOn: true });
  const off = decision(2, 2200, { claimsOn: false, occupiedOn: false });
  assert.equal(on.ownColor, off.ownColor);
  assert.equal(on.plateOpacity, off.plateOpacity);
  assert.equal(on.claimsChangeFill, false);
  assert.equal(off.claimsChangeFill, false);
  const occupiedOn = decision(2, 1642, { occupiedOn: true });
  const occupiedOff = decision(2, 1642, { occupiedOn: false });
  assert.equal(occupiedOn.ownColor, occupiedOff.ownColor);
  assert.equal(occupiedOn.plate, false);
  assert.equal(occupiedOff.plate, false);
});

test("borders sit above ownership, vassal, and occupation fills", function () {
  const photo = fill.borderPaneZ();
  const close = fill.flatBorderPaneZ();
  [357, 364, 380, 382, 384].forEach(function (cover) {
    assert.ok(photo > cover);
    assert.ok(close > cover);
  });
  assert.ok(close > photo);
  assert.ok(photo < 390);
  assert.ok(close < 390);
});

test("baseline NPC colour is not a solid history fill over Auto", function () {
  // Cycle 0 Kavoter: photo is still Kavoter, world.json is already Verillian.
  assert.equal(fill.shouldPaintHistoryOwnerFill(210, 210, 21), false);
  // Cycle 1 Zanhalter: not conquered yet, world.json is already Terrillia.
  assert.equal(fill.shouldPaintHistoryOwnerFill(368, 368, 229), false);
  // Cycle 0 Martellun beige, world already Verillian.
  assert.equal(fill.shouldPaintHistoryOwnerFill(276, 276, 21), false);
  // After conquest the photo is recolored; this overlay stays off.
  assert.equal(fill.shouldPaintHistoryOwnerFill(21, 210, 21), false);
  assert.equal(fill.shouldPaintHistoryOwnerFill(229, 368, 229), false);
});

test("coast fringe recolors old paint and leaves the sea and neighbours", function () {
  const kavoter = [205, 10, 147];
  const ocean = [52, 129, 151];
  const mint = [62, 254, 201];
  const fringe = [174, 49, 98];
  assert.equal(fill.recolorTilePixel(kavoter, kavoter, true, false), true);
  assert.equal(fill.recolorTilePixel(fringe, kavoter, false, true), true);
  assert.equal(fill.recolorTilePixel(ocean, kavoter, false, true), false);
  assert.equal(fill.recolorTilePixel(mint, kavoter, false, true), false);
  assert.equal(fill.recolorTilePixel(fringe, kavoter, false, false), false);
});

test("auto uses the political tint and still blocks the baked paint", function () {
  const faded = decision(2, 2200, { mode: "auto", flat: 0, politicalOpacity: 0.28 });
  assert.equal(faded.plateOpacity, 0);
  assert.equal(faded.autoTintOpacity, 0.28);
  assert.equal(faded.maskBaked, true);
  assert.equal(faded.hidesBaked, true);
  const home = decision(2, HOME.verillian, { mode: "auto", flat: 0, politicalOpacity: 0.28 });
  assert.equal(home.plate, false);
  assert.equal(home.autoTintOpacity, 0);
  assert.equal(home.maskBaked, false);
  assert.equal(faded.autoTintOpacity, 0.28);
  const solid = decision(2, 2200, { mode: "auto", flat: 0, politicalOpacity: 1 });
  assert.equal(solid.plateOpacity, 0);
  assert.equal(solid.autoTintOpacity, 1);
  assert.equal(solid.maskBaked, false);
  assert.equal(solid.hidesBaked, true);
  const political = decision(2, 2200, { mode: "political", flat: 0, politicalOpacity: 0.28 });
  assert.equal(political.plateOpacity, 1);
  assert.equal(political.autoTintOpacity, 0);
  assert.equal(political.maskBaked, false);
  assert.equal(political.hidesBaked, true);
  const mid = decision(2, 2127, { mode: "auto", flat: 0.25, politicalOpacity: 0.5 });
  assert.equal(mid.autoTintOpacity, 0.375);
  assert.equal(mid.maskBaked, true);
  assert.equal(mid.plateOpacity, 0);
  assert.equal(mid.washOpacity, 0.375);
});

test("terrain and depth hide the plate; the flat board takes over up close", function () {
  assert.equal(decision(2, 2200, { mode: "terrain" }).plateOpacity, 0);
  assert.equal(decision(2, 2200, { mode: "terrain" }).autoTintOpacity, 0);
  assert.equal(decision(2, 2200, { mode: "depth" }).plateOpacity, 0);
  assert.equal(decision(2, 2127, { mode: "terrain" }).hidesBaked, false);
  assert.equal(decision(2, 2200, { mode: "auto", flat: 0 }).hidesBaked, true);
  assert.equal(decision(2, 2200, { mode: "political", flat: 0.98 }).plateOpacity, 0);
  const home = decision(2, HOME.terrillia);
  assert.equal(home.annexed, false);
  assert.equal(home.plate, true);
  assert.equal(home.ownColor.toLowerCase(), "#75813b");
  assert.equal(home.hidesBaked, true);
  assert.equal(home.wash, true);
  assert.equal(decision(2, 2118, { mode: "terrain" }).plateOpacity, 0);
  assert.equal(decision(2, 2118, { mode: "terrain" }).hidesBaked, false);
  assert.equal(decision(2, 2118, { mode: "depth" }).plateOpacity, 0);
  assert.equal(decision(2, HOME.verillian).plate, false);
  assert.equal(decision(2, HOME.verillian).wash, false);
});

test("Terrillia core is plated in the current colour on every cycle", function () {
  [0, 1, 2, 3].forEach(function (turn) {
    const core = decision(turn, HOME.terrillia);
    assert.equal(core.owner, 229);
    assert.equal(core.annexed, false);
    assert.equal(core.ownColor.toLowerCase(), "#75813b");
    assert.equal(core.plate, true);
    assert.equal(core.hidesBaked, true);
    assert.notEqual(core.ownColor.toLowerCase(), baked.get(HOME.terrillia).color.toLowerCase());
  });
  assert.equal(decision(0, HOME.terrillia).wash, false);
  assert.equal(decision(3, HOME.terrillia).wash, true);
  assert.equal(decision(3, HOME.terrillia).paintColor.toLowerCase(), "#3efec9");
});

function territoryRows(turn) {
  const view = fold(turn);
  const ids = new Set();
  Object.keys(world.provinces).forEach(function (key) {
    ids.add(Number(key));
  });
  Object.keys(view.owners).forEach(function (key) {
    ids.add(Number(key));
  });
  const occupied = {};
  view.occupations.forEach(function (row) {
    occupied[row.province] = row.occupier;
  });
  return Array.from(ids).map(function (id) {
    const record = world.provinces[String(id)];
    return {
      id: id,
      owner: fill.resolvedOwner(view.owners, id, record ? record.nation_id : null),
      occupier: occupied[id] == null ? null : occupied[id],
    };
  });
}

function heldBy(turn, nationId) {
  return fill.controlledTerritory(territoryRows(turn))[String(nationId)] || [];
}

test("popup territory follows transfers and occupations, not the static list", function () {
  const base = nations.get(37).provinces.slice();
  assert.equal(base.length, 7);
  assert.deepEqual(heldBy(2, 37).filter(function (id) {
    return base.indexOf(id) < 0;
  }), []);
  const cycle3 = heldBy(3, 37);
  [2881, 2882, 3039, 3044, 3047, 3051].forEach(function (id) {
    assert.ok(cycle3.indexOf(id) >= 0);
    assert.equal(base.indexOf(id), -1);
  });
  assert.equal(cycle3.length, 13);
  assert.equal(heldBy(0, 111).length, 9);
  assert.deepEqual(heldBy(3, 111), [559]);
  [484, 492, 512, 527, 537, 540, 541, 547].forEach(function (id) {
    assert.ok(heldBy(3, 25).indexOf(id) >= 0);
  });
});

test("Bryn and Neth halls stripe both colours from cycle 2", function () {
  assert.equal(fill.confederationFill({ cycle: 0, provinceId: 160, partnerColor: "#c5b3a1" }), null);
  assert.equal(fill.confederationFill({ cycle: 1, provinceId: 18, partnerColor: "#c5b3a1" }), null);
  assert.deepEqual(fill.confederationHoldings(1), []);
  const bryn = fill.confederationFill({ cycle: 2, provinceId: 160, partnerColor: "#c5b3a1" });
  const neth = fill.confederationFill({ cycle: 3, provinceId: 18, partnerColor: "#c5b3a1" });
  assert.equal(bryn.label, "Bryn and Neth confederation");
  assert.equal(bryn.partnerId, 236);
  assert.equal(bryn.hall, "Bryn");
  assert.equal(neth.hall, "Neth");
  assert.equal(bryn.colors[0].toLowerCase(), "#c5b3a1");
  assert.equal(neth.colors[0].toLowerCase(), "#c5b3a1");
  assert.notEqual(bryn.colors[1].toLowerCase(), bryn.colors[0].toLowerCase());
  assert.notEqual(neth.colors[1].toLowerCase(), bryn.colors[1].toLowerCase());
  assert.deepEqual(fill.confederationHoldings(3).map(function (row) {
    return row.province;
  }), [160, 18]);
  const equals = fill.confederationFill({
    cycle: 3,
    provinceId: 160,
    ownerName: "Bryn",
    partnerColor: "#c5b3a1",
    alliances: [
      { bloc: "Bryn and Neth", nation: "Bryn", role: "confederation" },
      { bloc: "Bryn and Neth", nation: "Neth", role: "confederation" },
    ],
    memberColors: ["#111111", "#222222"],
  });
  assert.deepEqual(equals.colors, ["#111111", "#222222"]);
  const vassal = fill.confederationFill({
    cycle: 3,
    ownerName: "Ulgaltel",
    alliances: fold(3).alliances,
    memberColors: ["#3efec9", "#75813b"],
  });
  assert.equal(vassal, null);
});
