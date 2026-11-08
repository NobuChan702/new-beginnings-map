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
    assert.equal(conquered.ownColor, "#414b0d");
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
  assert.equal(home.plate, false);
  assert.equal(home.wash, true);
  assert.equal(decision(2, HOME.verillian).plate, false);
  assert.equal(decision(2, HOME.verillian).wash, false);
});
