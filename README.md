# New Beginnings – World Map

Static map for the New Beginnings map roleplay. Players open one link on a phone or a computer. There is no account.

The game host is Blake. The public address is [https://nobuchan702.github.io/new-beginnings-map/](https://nobuchan702.github.io/new-beginnings-map/).

## Preview locally

From the repository root, after a build:

```bash
python3 -m http.server 8000
```

Open http://localhost:8000/

## Update the map

Raw inputs live in `source/` on your machine. That folder is gitignored. It holds the full rasters, including the hidden west, so it stays out of git.

Put these files in `source/`:

| File | What it is |
| --- | --- |
| `political.png` | Fogged political map: nation colors, borders, ocean depth, Terra Incognita |
| `terrain.png` | Fogged terrain map with borders |
| `province_ids.png` | Province id raster. Each province's `rgb` in `provinces.json` is the pixel color |
| `depth_zones.png` | `0` land, `80` shallow, `160` mid, `240` deep |
| `provinces.json` | Province metadata |
| `nations.yaml` | Nations: id, name, tier (`type`), color, capital, province list |
| `terrain_by_province.json` | Terrain, mix, river, coast, area |

Every raster must be the same pixel size. Scale is read from the data (5.5 km per pixel).

Install the build tools once:

```bash
pip install -r scripts/requirements.txt
```

Regenerate tiles and filtered data:

```bash
python3 scripts/build_map.py
```

Commit the regenerated files:

- `tiles/`
- `data/world.json`
- `data/lookup.png`
- `data/meta.json`
- `data/flat.geojson`
- `assets/overview.webp`
- `assets/empty.webp` and `assets/blank.webp`

Leave `source/` untracked. Push to `main` (or open a pull request). GitHub Actions deploys the site. The workflow does not rebuild tiles, because the source rasters are not in the repository.

`nations.yaml` may contain a `removed_americas` section and a spare `palette`. The build ignores both and does not write them.

## Search

The header box finds a nation by name. Type a province id, such as `1459`, to fly to that province and open its popup (`Province 1459`). Only provinces on the published side of the map resolve. A hidden id produces no result.

A nation may list old names in `former_names`. Search and a `#nation=` link still find those, and the map shows the current `short_name`. Holy Empire of Sanbreque (nation 23) is still found by Haltor. Verillian Empire (nation 21) is still found by Selmira. Its capital is Verillia: that name is the capital row in `data/settlements.csv`, and the capital star, its popup, and search use it. Basileia Rhomania (nation 25) is still found by Haldortel. Its capital is Constantinople, the city formerly named Dortelhal, in province 499. Province 562 is a city named Haldortel again. Kingdom of Foxglade (nation 128) is still found by Galzan. Holy Order of Eternea (nation 75) is still found by Liitham. Atlantis (nation 154) is still found by Ulselryn. The Sevarian League (nation 30) is still found by Arkas. Its capital is Batrade, the coastal city formerly named Kasarkasarkas, in province 116. Province 109 is a city named Arkas again.

Layers → Province IDs draws those same ids on the map once you are zoomed in. The zoom cutoff is `provinceLabelMinZoom` in `js/config.js`.

## Claims

Edit `data/claims.json`. Keys are nation ids as strings. `{}` means nothing is taken. `player` is the full name, shown as “Claimed by …” in the popup. `short` is the name in brackets after the nation on the map label, in the popup title, and in search. If `short` is left off, the page uses `player`, dropping a trailing parenthesis such as `(Grand Albion)`. A plain string still works and is treated as `player`.

```json
{
  "25": { "player": "Tom (Grand Albion)", "short": "Tom" },
  "23": { "player": "Sofia", "short": "Sofia" }
}
```

A taken nation is labeled like `Basileia Rhomania (Tom)` at its capital and in search. An unclaimed nation stays plain. The nation also gets a red hatch. Commit and push that file. A rebuild is not required. The fogged west is unchanged: these names are published nations, and the claim markers still drop out on a concealed province.

## Infrastructure

Edit `data/infrastructure.json`. The layer is Layers → Infrastructure, on by default. It is vector, so the icons and roads stay sharp past the raster. `"capitals": true` draws a star at every published nation capital, using the capital already stored on the nation.

Cycle 0 is already filled in. Every published province has a major city and a medium town. On a capital province the major city is the capital star, not a second marker. Each nation has roads between its own settlements, and each coastal nation has one port. Nothing crosses a border or the sea. Names and coordinates are stored in the file so they stay fixed. `python3 scripts/build_settlements.py` regenerates that catalog and writes `data/settlements.csv`. Regenerating replaces the cycle-0 places and roads, and drops the sector column, so run `python3 scripts/build_villages.py` afterwards.

A place is a `city`, `town`, `fort`, `port`, `shipyard`, `market`, `farm`, `mine`, or `lumber` camp. Give it a `province` id and the marker sits on that province's center, or give it `x` and `y` in map pixels. `nation` is the nation id. The popup shows the place name, its type, and that nation, with the player's short name in brackets when the nation is claimed. A road is a polyline through province centers: list those ids in `provinces`, in order. `points` is the same line written out as coordinates when it does not follow province centers. A road needs two provinces or two points. Cycle-0 roads are `points` that follow land.

```json
{
  "capitals": true,
  "places": [
    { "id": "fort-span", "type": "fort", "name": "Span Fort", "province": 1202, "cycle": 1 }
  ],
  "roads": [
    { "id": "coast-road", "name": "Coast road", "provinces": [562, 1201], "cycle": 1, "round": 2 }
  ]
}
```

`cycle` and `round` hold a feature until the history slider reaches that date. Leave both off and it is on the map from the start. A feature dated Cycle 1 appears for the whole of Cycle 1 and stays afterward. Add `round` when it should wait for that round inside the cycle. Cycle 0 treats a missing round as round 0, so a round 2 road is hidden on Start.

What appears depends on zoom. Markers and roads outside the current view are not drawn, so the full catalog does not have to sit in the page at once.

- Capitals from zoom 4.
- Major cities from zoom 5. Forts and shipyards, when a later cycle adds them, from zoom 5.
- Towns, ports, roads, markets, farms, mines, and lumber camps from zoom 6.

Forts that belong to a sector live in `data/buildings.csv`, not in the place list. The file ships with a header and no rows. A fort is drawn on this same Infrastructure layer from zoom 6, a small tower set just beside that sector's settlement so it does not cover the marker. It appears when the history slider reaches `cycle_built` and stays afterward. `status` is `done` or `building`. A fort that is still `building` is a faded scaffold instead of the finished tower. The popup reads Fort, the nation (with the player's short name in brackets when the nation is claimed), and the sector id. Concealed provinces and anything west of x = 3592 are left off.

```csv
nation,province,sector_id,building,cycle_built,status
Basileia Rhomania,562,562-A,fort,1,done
```

`nation` is the nation's short name, the same form as `data/settlements.csv`. `building` is `fort`. `province` and `sector_id` have to name a published sector that already has a settlement.

Roads are thin dashed tan lines. The other marks are small medieval icons in the map's navy, silver, and tan. Nothing in this layer is drawn in the fog: a concealed province, an unrevealed sea, or anything west of x = 3592 is left off, including a road that would only cross that ground. Commit and push after editing. A rebuild is not required.

A snapshot can add works for its own date. See History. Those entries follow the same types, the same zoom, and the same fog rule.

## Trade routes

Edit `data/trade_routes.json`. The layer is Layers → Trade routes, on by default. Cycle 0 ships with an empty list. A route appears when the history slider reaches its `cycle`, and its `round` when that is set, the same way a road does. Leave both off and it is on the map from the start. The lines are drawn at every zoom. Routes outside the current view are skipped.

Only a route with `"public": true` is published. `python3 scripts/check_history.py` fails the build if a route in this file, or in a snapshot, omits that flag or sets it to false. The page also drops a route that is not public.

`from` and `to` each name a nation id and a settlement. The settlement is a capital, city, town, or port already on the map. `kind` is `land` or `sea`. `goods` is the cargo text. `status` is `active` or `disrupted`.

A land route follows the roads between its two settlements. Where those roads do not meet, it crosses the land between the nearest road ends, and if that is not possible it follows a land path instead. It does not cross the sea. A sea route runs from port to port and stays on the water. When the named settlement is not itself a port, the nation's port is used. A route with no path, or one that would enter the fog, is left off the map.

Land routes are thin gold lines. Sea routes are thin blue lines. Each has a soft glow and no arrow. A disrupted route is red and dashed. The popup names the two settlements, the kind of route, the two nations with the player's short name in brackets when the nation is claimed, the goods, and the status.

```json
{
  "routes": [
    {
      "id": "grain-run",
      "public": true,
      "kind": "land",
      "status": "active",
      "goods": "Grain and wool",
      "from": { "nation": 25, "settlement": "Constantinople" },
      "to": { "nation": 26, "settlement": "Qua" },
      "cycle": 1
    }
  ]
}
```

Commit and push after editing. A rebuild is not required. A snapshot can add or remove routes for its own date. See History.

## Sectors

Every published province is split into sectors so a war can take part of it. The shapes are in `data/sectors.geojson`. Who holds them is in `data/sector_control.csv`. The layer is Layers → Sectors, on by default.

`python3 scripts/build_sectors.py` cuts each province with a Voronoi diagram. The seeds are the province's city (the capital, on a capital province), its town, and one interior point. The cells are clipped to the province. A province under 80,000 km² keeps the city and the town, so two sectors. A province under 20,000 km² stays one piece. Ids are stable: `<province>-A` is the city, `B` the town, and `C` the interior. Running the script again rewrites both files and sets every controller back to the province owner at cycle 0, so copy `sector_control.csv` aside first if it already records conquests.

The control file is three columns:

```csv
sector_id,controller,cycle
562-A,25,0
562-B,26,1
```

`controller` is a nation id. Leave it empty for unclaimed. `cycle` is the history date. There is no round column. A row applies when the slider reaches that cycle and stays in force. A later cycle for the same sector replaces it. With no row yet, the sector stays with the province owner.

From zoom 6, each sector has a faint outline. A sector held by its province's owner keeps the owner's existing colour. A sector held by someone else uses the same occupation style as an occupied province: the owner's wash, the holder's diagonal stripe, and the holder's outline. Below zoom 6 those split sectors are drawn as one province in that same occupation style, using the holder who has the most sectors. Layers → Occupied territory is unchanged, and a whole-province occupation still draws exactly as it does today, on top of the sectors. Claimed-nations stripes are also unchanged. Turn Layers → Sectors off and the outlines, the sector occupation, and the villages all go away. The provinces then draw as they did before sectors.

Every sector has a settlement. Sector A keeps the province's major city, which is the capital star on a capital province. The town stays in the sector its marker already falls in. A sector with neither gets a village. `data/settlements.csv` is `nation,province,name,type,port,sector_id`. Village rows use type `village`. The names are built from the owning nation's syllables, the same way the cities and towns are. `python3 scripts/build_villages.py` fills `sector_id` and appends the villages. It does not move the cities, towns, or roads.

Villages are drawn only while Layers → Sectors is on, and only from zoom 7, so they do not crowd the towns at zoom 6. The marker is a smaller house. The popup shows the name, Village, and the nation, with the player's short name in brackets when that nation is claimed. A concealed province or a point west of x = 3592 is left off.

Sector control does not change the province owner. Taking every sector still shows occupation, not a transfer. A transfer stays a history `transfers` entry. Concealed provinces, and anything west of x = 3592, are left off the layer.

## Alliances

Layers → Alliances colours each bloc in one shared colour. It is off until you turn it on. Occupation stripes, claimed-nation hatch, and the nation colours underneath stay as they are: the wash is an overlay, and turning it off removes only that wash.

Membership is `data/alliances.csv`. The file ships with a header and no rows.

```csv
bloc,nation,role
Northern Crown,Basileia Rhomania,leader
Northern Crown,Atlantis,ally
Fox Pact,Kingdom of Foxglade,vassal
```

`bloc` is the shared name. `nation` is the nation's current short name, the same form as `data/settlements.csv`. `role` is `leader`, `ally`, or `vassal`. Every nation in a bloc uses that bloc's colour, including vassals. A nation can be in only one bloc. The colour follows the province owner on the history slider. A concealed province, and anything west of x = 3592, is left off. The popup names the bloc and the role while the nation is listed.

## History

The bar at the bottom of the map is the timeline. Dates live in `data/history/`. `index.json` is the slider order, earliest first. The page opens on the last file.

```json
["cycle-0.json", "cycle-1.json"]
```

`cycle-0.json` is the published map as it stands today. It has no transfers, occupations, colonies, or works. Later files are one date each. Copy the pattern, add the file name to `index.json`, commit, and push. A tile rebuild is not required.

```json
{
  "id": "cycle-1-round-2",
  "cycle": 1,
  "round": 2,
  "label": "Cycle 1 · Round 2",
  "war": "War of the Example",
  "transfers": [{ "province": 1201, "owner": 25 }],
  "occupations": [{ "province": 1202, "occupier": 25, "war": "War of the Example" }],
  "colonies": [{ "province": 1236, "owner": 25, "stage": "outpost" }],
  "reveals": { "provinces": [1147], "seas": ["test-reach"] },
  "fog": { "provinces": [1148] },
  "markers": [
    { "kind": "battle", "x": 6500, "y": 2500, "name": "Battle of the Span", "text": "Fought on the bridge." }
  ],
  "works": [],
  "seas": [],
  "infrastructure": {
    "places": [{ "id": "fort-span", "type": "fort", "name": "Span Fort", "province": 1202 }],
    "roads": [{ "id": "coast-road", "name": "Coast road", "provinces": [562, 1201] }],
    "remove": []
  },
  "trade": {
    "routes": [
      {
        "id": "grain-run",
        "public": true,
        "kind": "land",
        "status": "active",
        "goods": "Grain and wool",
        "from": { "nation": 25, "settlement": "Constantinople" },
        "to": { "nation": 26, "settlement": "Qua" }
      }
    ],
    "remove": []
  }
}
```

`label` is the slider text. Without it the map writes `Cycle 1 · Round 2`, or `Start (Cycle 0)` for cycle 0.

These fields accumulate as the slider moves forward:

- `transfers` change who owns a province. `owner` is a nation id. `null` makes it unclaimed. Ownership stays until a later transfer. Occupation does not change the owner.
- `reveals.provinces` and `reveals.seas` lift fog. Once revealed, a province or sea stays revealed.
- `works` add man-made land. A later snapshot can update the same id. `"status": "removed"` takes it off the map.
- `infrastructure.places` and `infrastructure.roads` add forts, ports, roads, and the other works from Infrastructure. An entry with no `cycle` starts on this snapshot's date. The same `id` in a later snapshot updates it. `infrastructure.remove` lists ids to take off the map from that date on, including a capital star (`capital-25` is Basileia Rhomania's).
- `trade.routes` add routes for that date. The fields match Trade routes, and `"public": true` is required. An entry with no `cycle` starts on this snapshot's date. The same `id` in a later snapshot updates it. `trade.remove` lists ids to take off the map from that date on.
- `seas` add a named sea zone. The rings are the zone. It stays fogged until `reveals.seas` lists its id.
- `colonies` update one province at a time. A province you leave out keeps its previous stage.

These fields replace the active list when the snapshot includes the key, and stay as they were when the key is absent. Use `[]` to clear them.

- `occupations` are provinces held in a war. The owner does not change. Players can hide the stripes with Layers → Occupied territory. The popup reads `Owned by X, occupied by Y (war: name)`.
- `markers` are battles and armies. `kind` is `battle` or `army`. `x` and `y` are map pixels. `text` is the popup. Layers → Battles & armies toggles them.

A colony `stage` is `outpost`, `colony`, `integrated`, or `abandoned`. An outpost is a light tint and a dotted outline in the colonizer's color. A colony is the same treatment, a little stronger. `integrated` becomes normal ownership and keeps that owner. `abandoned` removes the colony.

`fog.provinces` hides published provinces until a later snapshot reveals them. Cycle 0 leaves this empty, so the eastern map the site already shows stays visible. A hidden province draws in the fog color. Clicking it says Unexplored, and search will not find it. Revealing an id lifts that fog from that date onward.

The slider also filters `data/infrastructure.json`, `data/trade_routes.json`, and `data/sector_control.csv`. A place, road, or route with `cycle` and `round` appears once the slider reaches that date and stays afterward. A sector row applies from its `cycle` the same way, and a later row for that sector replaces it. Sector control does not transfer the province.

### Man-made land

Works are new land drawn on top of the sea. The painted tiles do not change. Use province ids from 9000 up. `type` is `land_bridge` or `reclaimed`. `status` is `done` or `building` (`under construction` also works). `building` draws a dashed outline. `owner` is a nation id.

`rings` is one polygon in map pixels, `[x, y]`, the same coordinates as infrastructure. The first ring is the outline. Further rings are holes. The shape has to sit entirely at x ≥ 3592. A point on the wrong side of that line drops the whole work.

```json
{
  "id": 9001,
  "name": "Constantinople Span",
  "type": "land_bridge",
  "status": "building",
  "owner": 25,
  "rings": [[[6472, 2368], [6488, 2368], [6488, 2648], [6472, 2648], [6472, 2368]]]
}
```

The polygon is drawn at every zoom. Close up, it is the real outline. Zoomed out, a thin bridge keeps a stroke about 3.5 screen pixels wide so it does not disappear, and a very small work also gets a dot at its center. Search the id, for example `9001`. If that land does not exist on the current date, the slider moves to the date it was built and the map opens it. Province IDs, once you are zoomed in, labels these the same way as other provinces.

### What must stay off the map

Snapshot files, `data/infrastructure.json`, `data/trade_routes.json`, `data/sectors.geojson`, `data/sector_control.csv`, `data/settlements.csv`, `data/buildings.csv`, and `data/alliances.csv` are public. Do not put a western province id, a western name, a western nation, or a coordinate with x < 3592 in them. The page drops geometry that crosses the fog line, and a click west of it only says that the western lands are unexplored. `python3 scripts/check_history.py` checks the history files, the infrastructure catalog, the trade routes, the sectors, the settlements, the buildings, and the alliances before deploy. A trade route that is not `"public": true` fails that check. So does a sector whose controller is not a published nation, a sector with no settlement, a village that is not inside its sector, a fort in a sector that has no settlement, or an alliance nation that is not on the published map.

## Zoom

Thresholds live in `js/config.js`.

- Below `politicalOpaqueUntil` the political map is solid.
- By `politicalFadeEnd` it has faded to `politicalTintOpacity` over the terrain, and province borders have faded in. That fade finishes before native zoom, while the picture is still sharp.
- Province id labels start at `provinceLabelMinZoom` and stay available through the closest zoom.
- `overzoomLevels` is how many steps past native resolution the map allows. Native resolution is `native_zoom` in `data/meta.json` (6 for the current 12000×5600 sheet). The source art is 5.5 km per pixel, so the raster cannot get sharper than that. In Auto, Political, and Depth, zoom 7 through 9 crossfades, across `flatFadeStartOffset` to `flatFadeEndOffset` past native zoom, to a flat board: solid nation colors, crisp province and nation outlines, and a flat opaque fog over the hidden west. Terrain does not make that crossfade. The terrain painting stays on at every zoom, including the closest, still using the fogged terrain artwork. Infrastructure stays vector on top: capitals from zoom 4, major cities from zoom 5, and towns, ports, and roads from zoom 6.

Reload the page after editing the numbers. A rebuild is not required unless the image size or the province borders change. Borders live in `data/flat.geojson`, which the build writes. Each shared border is simplified and smoothed once, so the two neighbors use the same line and the fill matches the outline. Do not draw the hidden west into that file; the fog feature is a plain rectangle.

The view control can force Political, Terrain, or Depth. Past native zoom, Political and Depth become the same flat board as Auto. Terrain stays on the terrain painting.

## Minimap

The corner overview is `assets/overview.webp`, a low-resolution copy of the fogged political map. The build refreshes it. A red rectangle shows the current view. Click or drag the overview to move the main map. The control collapses, and it starts collapsed on narrow screens.

## Publish

`.github/workflows/pages.yml` deploys with GitHub Actions whenever `main` changes.

In the repository settings, open **Settings → Pages → Build and deployment** and set **Source** to **GitHub Actions**.

The site is https://nobuchan702.github.io/new-beginnings-map/

## Hidden west

The player map keeps the fog artwork from `political.png` and `terrain.png` for every pixel with x < 3592. Provinces that touch that line are left out of `data/world.json`, `data/lookup.png`, and the outline tiles. The depth layer uses the same fog art on that side, not the raw depth raster. The build stops if the western artwork still shows land against sea, or if a hidden province id or a `removed_americas` name would be written out.
