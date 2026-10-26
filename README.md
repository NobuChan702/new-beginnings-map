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

Layers → Province IDs draws those same ids on the map once you are zoomed in. The zoom cutoff is `provinceLabelMinZoom` in `js/config.js`.

## Claims

Edit `data/claims.json`. Keys are nation ids as strings. Values are player names. `{}` means nothing is taken.

```json
{
  "8": "Avery"
}
```

A taken nation gets a red hatch, a Taken marker at its capital, and “Claimed by Avery” in the popup. Commit and push that file. A rebuild is not required.

## Infrastructure

Edit `data/infrastructure.geojson`. Coordinates are image pixels, `[x, y]`, with x increasing east from the left edge and y increasing south from the top. Those numbers are the map longitude and latitude. The layer is vector, so roads and markers stay sharp when the map is zoomed past the raster.

`properties.kind` is `road`, `port`, `fort`, `trade_route`, or `supply_route`. `properties.name` is the label. Ports and forts are points. Roads, trade routes, and supply routes are lines. Supply routes draw as red dashed strokes.

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": { "kind": "road", "name": "Coast road" },
      "geometry": {
        "type": "LineString",
        "coordinates": [[4200, 2100], [4600, 2300]]
      }
    },
    {
      "type": "Feature",
      "properties": { "kind": "port", "name": "Harbor" },
      "geometry": { "type": "Point", "coordinates": [4800, 2500] }
    }
  ]
}
```

The committed file starts empty. Commit and push after editing. A rebuild is not required. Players toggle the layer under Layers → Roads and places.

## Zoom

Thresholds live in `js/config.js`.

- Below `politicalOpaqueUntil` the political map is solid.
- By `politicalFadeEnd` it has faded to `politicalTintOpacity` over the terrain, and province borders have faded in. That fade finishes before native zoom, while the picture is still sharp.
- Province id labels start at `provinceLabelMinZoom` and stay available through the closest zoom.
- `overzoomLevels` is how many steps past native resolution the map allows. Native resolution is `native_zoom` in `data/meta.json` (6 for the current 12000×5600 sheet). The source art is 5.5 km per pixel, so the raster cannot get sharper than that. Zoom 7 through 9 does not magnify the painting. It crossfades, across `flatFadeStartOffset` to `flatFadeEndOffset` past native zoom, to a flat board: solid nation colors, crisp province and nation outlines, and a flat opaque fog over the hidden west. Roads and routes stay vector strokes on top of that board.

Reload the page after editing the numbers. A rebuild is not required unless the image size or the province borders change. Borders live in `data/flat.geojson`, which the build writes. Each shared border is simplified and smoothed once, so the two neighbors use the same line and the fill matches the outline. Do not draw the hidden west into that file; the fog feature is a plain rectangle.

The view control can force Political, Terrain, or Depth while the photo is showing. Past native zoom every view becomes the same flat board.

## Minimap

The corner overview is `assets/overview.webp`, a low-resolution copy of the fogged political map. The build refreshes it. A red rectangle shows the current view. Click or drag the overview to move the main map. The control collapses, and it starts collapsed on narrow screens.

## Publish

`.github/workflows/pages.yml` deploys with GitHub Actions whenever `main` changes.

In the repository settings, open **Settings → Pages → Build and deployment** and set **Source** to **GitHub Actions**.

The site is https://nobuchan702.github.io/new-beginnings-map/

## Hidden west

The player map keeps the fog artwork from `political.png` and `terrain.png` for every pixel with x < 3592. Provinces that touch that line are left out of `data/world.json`, `data/lookup.png`, and the outline tiles. The depth layer uses the same fog art on that side, not the raw depth raster. The build stops if the western artwork still shows land against sea, or if a hidden province id or a `removed_americas` name would be written out.
