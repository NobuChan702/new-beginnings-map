// History snapshots for the New Beginnings map.
//
// index.json lists snapshot files in slider order. Folding a prefix of that
// list produces the map at that date:
//   transfers, reveals, and works accumulate
//   a colony marked integrated becomes ownership and stays that way
//   occupations and markers are replaced when a snapshot includes the key,
//     and kept when the key is left out
//   fog.provinces stay hidden until a later reveal lists them
//   infrastructure.places and infrastructure.roads accumulate by id
//     infrastructure.remove drops an id from that date on
//   trade.routes accumulate by id; trade.remove drops an id from that date on
//     a route is kept only when public is true
// Geometry with any point west of the fog line is dropped, not clipped.
(function (root) {
  "use strict";

  var WORK_ID_MIN = 9000;

  function labelOf(snap) {
    if (!snap) return "Start (Cycle 0)";
    if (snap.label) return String(snap.label);
    var cycle = Number(snap.cycle);
    if (!isFinite(cycle)) cycle = 0;
    if (snap.round != null && snap.round !== "") return "Cycle " + cycle + " · Round " + snap.round;
    if (cycle === 0) return "Start (Cycle 0)";
    return "Cycle " + cycle;
  }

  function num(value) {
    if (typeof value === "boolean") return null;
    var n = Number(value);
    return isFinite(n) ? n : null;
  }

  function has(obj, key) {
    return !!obj && Object.prototype.hasOwnProperty.call(obj, key);
  }

  function ringsFrom(value) {
    if (!Array.isArray(value) || !value.length || !Array.isArray(value[0])) return null;
    if (typeof value[0][0] === "number") return [value];
    if (Array.isArray(value[0][0]) && typeof value[0][0][0] === "number") return value;
    return null;
  }

  function cleanRings(value, hiddenWest) {
    var rings = ringsFrom(value);
    var west = Number(hiddenWest);
    if (!rings || !isFinite(west)) return null;
    var cleaned = [];
    for (var i = 0; i < rings.length; i++) {
      var ring = rings[i];
      if (!Array.isArray(ring) || ring.length < 3) return null;
      var out = [];
      for (var j = 0; j < ring.length; j++) {
        var pt = ring[j];
        if (!pt || pt.length < 2) return null;
        var x = Number(pt[0]);
        var y = Number(pt[1]);
        if (!isFinite(x) || !isFinite(y) || x < west) return null;
        out.push([x, y]);
      }
      var first = out[0];
      var last = out[out.length - 1];
      if (first[0] !== last[0] || first[1] !== last[1]) out.push([first[0], first[1]]);
      cleaned.push(out);
    }
    return cleaned;
  }

  function bboxOf(rings) {
    var minX = Infinity;
    var minY = Infinity;
    var maxX = -Infinity;
    var maxY = -Infinity;
    rings.forEach(function (ring) {
      ring.forEach(function (pt) {
        if (pt[0] < minX) minX = pt[0];
        if (pt[1] < minY) minY = pt[1];
        if (pt[0] > maxX) maxX = pt[0];
        if (pt[1] > maxY) maxY = pt[1];
      });
    });
    return [minX, minY, maxX, maxY];
  }

  function centroidOf(rings) {
    var ring = rings[0];
    var limit = ring.length;
    if (
      limit > 1 &&
      ring[0][0] === ring[limit - 1][0] &&
      ring[0][1] === ring[limit - 1][1]
    ) {
      limit -= 1;
    }
    var sx = 0;
    var sy = 0;
    for (var i = 0; i < limit; i++) {
      sx += ring[i][0];
      sy += ring[i][1];
    }
    if (!limit) return [0, 0];
    return [sx / limit, sy / limit];
  }

  function pointInRing(x, y, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0];
      var yi = ring[i][1];
      var xj = ring[j][0];
      var yj = ring[j][1];
      var intersect = (yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi;
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function pointInRings(x, y, rings) {
    if (!rings || !rings.length || !pointInRing(x, y, rings[0])) return false;
    for (var i = 1; i < rings.length; i++) {
      if (pointInRing(x, y, rings[i])) return false;
    }
    return true;
  }

  function workType(value) {
    var text = String(value || "").toLowerCase().replace(/[\s-]+/g, "_");
    if (text === "reclaimed" || text === "reclaimed_land") return "reclaimed";
    return "land_bridge";
  }

  function workStatus(value) {
    var text = String(value || "").toLowerCase().replace(/[\s-]+/g, "_");
    if (text === "building" || text === "under_construction" || text === "construction") return "building";
    if (text === "removed") return "removed";
    return "done";
  }

  var INFRA_TYPES = {
    capital: 1,
    city: 1,
    town: 1,
    fort: 1,
    port: 1,
    shipyard: 1,
    market: 1,
    farm: 1,
    mine: 1,
    lumber: 1,
  };

  function infraType(value) {
    var text = String(value || "").toLowerCase().replace(/[\s-]+/g, "_");
    if (text === "lumber_camp" || text === "lumbercamp") return "lumber";
    if (text === "harbour") return "port";
    return text;
  }

  function pointOk(x, y, west) {
    return x != null && y != null && x >= west && y >= 0;
  }

  function inheritDate(row, cycle, round) {
    var hasCycle = row.cycle != null && row.cycle !== "";
    var hasRound = row.round != null && row.round !== "";
    return {
      cycle: hasCycle ? num(row.cycle) : cycle,
      round: hasRound ? num(row.round) : hasCycle ? null : round,
    };
  }

  function absorbInfra(snap, places, roads, removed, west) {
    var block = snap.infrastructure;
    if (!block) return;
    var cycle = num(snap.cycle);
    if (cycle == null) cycle = 0;
    var round = snap.round == null || snap.round === "" ? null : num(snap.round);
    (block.remove || []).forEach(function (id) {
      if (id == null || id === "") return;
      var key = String(id);
      delete places[key];
      delete roads[key];
      removed[key] = true;
    });
    (block.places || []).forEach(function (row) {
      if (!row) return;
      var type = infraType(row.type || row.kind);
      if (!INFRA_TYPES[type]) return;
      var x = num(row.x);
      var y = num(row.y);
      var hasPoint = (row.x != null && row.x !== "") || (row.y != null && row.y !== "");
      if (hasPoint && !pointOk(x, y, west)) return;
      var province = row.province == null || row.province === "" ? null : num(row.province);
      if (province == null && !pointOk(x, y, west)) return;
      var dates = inheritDate(row, cycle, round);
      var id = row.id != null && row.id !== "" ? String(row.id) : type + "-" + (province != null ? province : x + "x" + y);
      delete removed[id];
      places[id] = {
        id: id,
        type: type,
        name: row.name ? String(row.name) : "",
        province: province,
        x: pointOk(x, y, west) ? x : null,
        y: pointOk(x, y, west) ? y : null,
        cycle: dates.cycle,
        round: dates.round,
      };
    });
    (block.roads || []).forEach(function (row) {
      if (!row) return;
      var provinces = [];
      (row.provinces || []).forEach(function (id) {
        var n = num(id);
        if (n != null) provinces.push(n);
      });
      var points = [];
      var badPoint = false;
      (row.points || row.coordinates || []).forEach(function (pt) {
        if (!pt || pt.length < 2) {
          badPoint = true;
          return;
        }
        var px = num(pt[0]);
        var py = num(pt[1]);
        if (!pointOk(px, py, west)) badPoint = true;
        else points.push([px, py]);
      });
      if (badPoint) return;
      if (provinces.length < 2 && points.length < 2) return;
      var dates = inheritDate(row, cycle, round);
      var id = row.id != null && row.id !== "" ? String(row.id) : "road-" + (provinces.join("-") || points.length);
      delete removed[id];
      roads[id] = {
        id: id,
        name: row.name ? String(row.name) : "",
        provinces: provinces,
        points: points,
        cycle: dates.cycle,
        round: dates.round,
      };
    });
  }

  function tradeEndpoint(value) {
    if (!value) return null;
    var nation = num(value.nation != null ? value.nation : value.nation_id);
    var settlement = value.settlement != null ? value.settlement : value.name;
    if (nation == null || settlement == null || String(settlement).trim() === "") return null;
    return { nation: nation, settlement: String(settlement) };
  }

  function absorbTrade(snap, routes, removed) {
    var block = snap.trade;
    if (!block) return;
    var cycle = num(snap.cycle);
    if (cycle == null) cycle = 0;
    var round = snap.round == null || snap.round === "" ? null : num(snap.round);
    (block.remove || []).forEach(function (id) {
      if (id == null || id === "") return;
      var key = String(id);
      delete routes[key];
      removed[key] = true;
    });
    (block.routes || []).forEach(function (row) {
      if (!row || row.public !== true) return;
      var kind = String(row.kind || "").toLowerCase();
      var status = String(row.status || "").toLowerCase();
      if (kind !== "land" && kind !== "sea") return;
      if (status !== "active" && status !== "disrupted") return;
      var from = tradeEndpoint(row.from);
      var to = tradeEndpoint(row.to);
      if (!from || !to) return;
      var dates = inheritDate(row, cycle, round);
      var id = row.id != null && row.id !== "" ? String(row.id) : kind + "-" + from.nation + "-" + to.nation + "-" + from.settlement;
      delete removed[id];
      routes[id] = {
        id: id,
        from: from,
        to: to,
        kind: kind,
        goods: row.goods ? String(row.goods) : "",
        status: status,
        public: true,
        cycle: dates.cycle,
        round: dates.round,
      };
    });
  }

  function copyWorkFields(prev, work) {
    if (work.name) prev.name = String(work.name);
    if (work.type) prev.type = workType(work.type);
    if (work.status) prev.status = workStatus(work.status);
    if (has(work, "owner")) prev.owner = work.owner == null || work.owner === "" ? null : num(work.owner);
  }

  function fold(snapshots, index, hiddenWest) {
    var list = Array.isArray(snapshots) ? snapshots : [];
    var west = Number(hiddenWest);
    if (!isFinite(west)) west = 3592;
    var end = list.length ? Math.max(0, Math.min(index | 0, list.length - 1)) : -1;
    var owners = {};
    var occupations = [];
    var colonies = {};
    var integrated = {};
    var revealedProvinces = {};
    var revealedSeas = {};
    var concealedProvinces = {};
    var seas = {};
    var works = {};
    var markers = [];
    var war = null;
    var infraPlaces = {};
    var infraRoads = {};
    var infraRemoved = {};
    var tradeRoutes = {};
    var tradeRemoved = {};

    for (var i = 0; i <= end; i++) {
      var snap = list[i] || {};
      if (has(snap, "war")) war = snap.war ? String(snap.war) : null;

      (snap.transfers || []).forEach(function (row) {
        if (!row) return;
        var id = num(row.province != null ? row.province : row.id);
        if (id == null || id < 1 || id >= WORK_ID_MIN) return;
        owners[id] = row.owner == null || row.owner === "" ? null : num(row.owner);
      });

      var reveals = snap.reveals || {};
      (reveals.provinces || []).forEach(function (id) {
        var n = num(id);
        if (n == null) return;
        revealedProvinces[n] = true;
        delete concealedProvinces[n];
      });
      (reveals.seas || []).forEach(function (id) {
        if (id == null || id === "") return;
        revealedSeas[String(id)] = true;
      });

      var fog = snap.fog || {};
      var conceal = fog.provinces || snap.conceal || [];
      conceal.forEach(function (id) {
        var n = num(id);
        if (n == null || revealedProvinces[n]) return;
        concealedProvinces[n] = true;
      });

      (snap.seas || []).forEach(function (sea) {
        if (!sea || sea.id == null || sea.id === "") return;
        var rings = cleanRings(sea.rings || (sea.geometry && sea.geometry.coordinates), west);
        if (!rings) return;
        seas[String(sea.id)] = {
          id: String(sea.id),
          name: sea.name ? String(sea.name) : String(sea.id),
          rings: rings,
          bbox: bboxOf(rings),
        };
      });

      (snap.works || []).forEach(function (work) {
        if (!work) return;
        var id = num(work.id);
        if (id == null || id < WORK_ID_MIN) return;
        var status = workStatus(work.status);
        if (status === "removed") {
          delete works[id];
          return;
        }
        var rings = work.rings ? cleanRings(work.rings, west) : null;
        if (!rings) {
          if (works[id] && !work.rings) copyWorkFields(works[id], work);
          return;
        }
        var type = workType(work.type);
        works[id] = {
          id: id,
          name: work.name ? String(work.name) : type === "reclaimed" ? "Reclaimed land" : "Land bridge",
          type: type,
          status: status === "building" ? "building" : "done",
          owner: work.owner == null || work.owner === "" ? null : num(work.owner),
          rings: rings,
          bbox: bboxOf(rings),
          centroid: centroidOf(rings),
          since: i,
        };
      });

      if (has(snap, "occupations")) {
        var nextOcc = [];
        (snap.occupations || []).forEach(function (row) {
          if (!row) return;
          var id = num(row.province != null ? row.province : row.id);
          var occupier = num(row.occupier != null ? row.occupier : row.owner);
          if (id == null || occupier == null) return;
          nextOcc.push({
            province: id,
            occupier: occupier,
            war: row.war ? String(row.war) : snap.war ? String(snap.war) : null,
          });
        });
        occupations = nextOcc;
      }

      if (has(snap, "markers")) {
        var nextMarks = [];
        (snap.markers || []).forEach(function (row) {
          if (!row) return;
          var x = num(row.x);
          var y = num(row.y);
          if (x == null || y == null || x < west) return;
          var kind = row.kind === "army" ? "army" : "battle";
          nextMarks.push({
            kind: kind,
            x: x,
            y: y,
            name: row.name ? String(row.name) : kind === "army" ? "Army" : "Battle",
            text: row.text ? String(row.text) : "",
            owner: row.owner == null || row.owner === "" ? null : num(row.owner),
          });
        });
        markers = nextMarks;
      }

      (snap.colonies || []).forEach(function (row) {
        if (!row || integrated[num(row.province != null ? row.province : row.id)]) return;
        var id = num(row.province != null ? row.province : row.id);
        if (id == null) return;
        var stage = String(row.stage || "outpost").toLowerCase().replace(/[\s-]+/g, "_");
        if (stage === "abandoned" || stage === "removed") {
          delete colonies[id];
          return;
        }
        if (stage === "integrated" || stage === "province") {
          var integratedOwner = num(row.owner);
          if (integratedOwner != null) owners[id] = integratedOwner;
          delete colonies[id];
          integrated[id] = true;
          return;
        }
        var owner = num(row.owner);
        if (owner == null) return;
        colonies[id] = {
          province: id,
          owner: owner,
          stage: stage === "colony" ? "colony" : "outpost",
        };
      });

      absorbInfra(snap, infraPlaces, infraRoads, infraRemoved, west);
      absorbTrade(snap, tradeRoutes, tradeRemoved);
    }

    var concealedSeas = [];
    Object.keys(seas).forEach(function (id) {
      if (!revealedSeas[id]) concealedSeas.push(seas[id]);
    });

    var workList = Object.keys(works)
      .map(function (key) {
        return works[key];
      })
      .sort(function (a, b) {
        return a.id - b.id;
      });

    var snapNow = end >= 0 ? list[end] : { cycle: 0, label: "Start (Cycle 0)" };
    var round = snapNow.round == null || snapNow.round === "" ? null : Number(snapNow.round);
    return {
      index: end < 0 ? 0 : end,
      snapshot: snapNow,
      label: labelOf(snapNow),
      cycle: Number(snapNow.cycle) || 0,
      round: isFinite(round) ? round : null,
      war: war,
      owners: owners,
      occupations: occupations,
      colonies: colonies,
      revealedProvinces: revealedProvinces,
      revealedSeas: revealedSeas,
      concealedProvinces: concealedProvinces,
      concealedSeas: concealedSeas,
      works: workList,
      markers: markers,
      infrastructure: {
        places: Object.keys(infraPlaces).map(function (key) {
          return infraPlaces[key];
        }),
        roads: Object.keys(infraRoads).map(function (key) {
          return infraRoads[key];
        }),
        removed: Object.keys(infraRemoved),
      },
      trade: {
        routes: Object.keys(tradeRoutes).map(function (key) {
          return tradeRoutes[key];
        }),
        removed: Object.keys(tradeRemoved),
      },
    };
  }

  function workIndex(snapshots, id, hiddenWest) {
    var target = num(id);
    if (target == null) return -1;
    var list = Array.isArray(snapshots) ? snapshots : [];
    for (var i = 0; i < list.length; i++) {
      var state = fold(list, i, hiddenWest);
      for (var j = 0; j < state.works.length; j++) {
        if (state.works[j].id === target) return i;
      }
    }
    return -1;
  }

  root.NB_HISTORY = {
    WORK_ID_MIN: WORK_ID_MIN,
    labelOf: labelOf,
    fold: fold,
    workIndex: workIndex,
    pointInRings: pointInRings,
    cleanRings: function (rings, hiddenWest) {
      return cleanRings(rings, hiddenWest);
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
