const test = require("node:test");
const assert = require("node:assert/strict");
const yields = require("../js/building_yields.js");

const RICH = "Mine: +2 iron (hills/mountains)";
const BOG = "Mine: +1 iron (bog iron)";
const LUMBER_NO = "Lumber Camp: not allowed (needs 20% forest/jungle)";

test("thresholds are the named 20% constants", function () {
  assert.equal(yields.MINE_HILLS_MOUNTAINS_MIN_PERCENT, 20);
  assert.equal(yields.LUMBER_FOREST_JUNGLE_MIN_PERCENT, 20);
});

test("mine at exactly 20% hills", function () {
  assert.equal(yields.mineLine([["hills", 20]]), RICH);
});

test("mine just below 20% hills", function () {
  assert.equal(yields.mineLine([["hills", 19.9]]), BOG);
  assert.equal(yields.mineLine([["hills", 19]]), BOG);
});

test("mine hills plus mountains at exactly 20% and just below", function () {
  assert.equal(yields.mineLine([["hills", 10], ["mountains", 10]]), RICH);
  assert.equal(yields.mineLine([["mountains", 10], ["hills", 9.9]]), BOG);
  assert.equal(yields.mineLine([["plains", 80], ["hills", 10], ["mountains", 9]]), BOG);
});

test("mine ignores forest and an empty mix", function () {
  assert.equal(yields.mineLine([["forest", 100]]), BOG);
  assert.equal(yields.mineLine([]), BOG);
  assert.equal(yields.mineLine(null), BOG);
});

test("lumber camp at exactly 20% forest", function () {
  assert.equal(yields.lumberLine([["forest", 20]]), "Lumber Camp: allowed (forest/jungle 20%)");
});

test("lumber camp just below 20% forest", function () {
  assert.equal(yields.lumberLine([["forest", 19.9]]), LUMBER_NO);
  assert.equal(yields.lumberLine([["forest", 19]]), LUMBER_NO);
});

test("lumber camp forest plus jungle at exactly 20% and just below", function () {
  assert.equal(
    yields.lumberLine([["forest", 12], ["jungle", 8]]),
    "Lumber Camp: allowed (forest/jungle 20%)"
  );
  assert.equal(yields.lumberLine([["jungle", 10], ["forest", 9.9]]), LUMBER_NO);
  assert.equal(yields.lumberLine([["hills", 50], ["forest", 10], ["jungle", 9]]), LUMBER_NO);
});

test("lumber camp reports the combined share above the threshold", function () {
  assert.equal(
    yields.lumberLine([["forest", 15], ["jungle", 10.5]]),
    "Lumber Camp: allowed (forest/jungle 25.5%)"
  );
});

test("lumber camp ignores hills and an empty mix", function () {
  assert.equal(yields.lumberLine([["hills", 40], ["mountains", 40]]), LUMBER_NO);
  assert.equal(yields.lumberLine([]), LUMBER_NO);
});

test("the two lines stay independent at the boundary", function () {
  const both = yields.linesFromMix([
    ["hills", 20],
    ["forest", 19.9],
  ]);
  assert.equal(both.mine, RICH);
  assert.equal(both.lumber, LUMBER_NO);
  const flipped = yields.linesFromMix([
    ["mountains", 19.9],
    ["jungle", 20],
  ]);
  assert.equal(flipped.mine, BOG);
  assert.equal(flipped.lumber, "Lumber Camp: allowed (forest/jungle 20%)");
});
