# Season batch

`python3 tools/nb.py apply-batch FILE` applies one season of map edits from a CSV or JSON file. Rows run in order. The command checks the whole file before it writes. Unknown ids, nations that are not on the published map, and duplicate or conflicting rows are reported together, and no file is changed.

`--dry-run` prints one JSON object and writes nothing. Put it in either place:

```bash
python3 tools/nb.py --dry-run apply-batch season.json
python3 tools/nb.py apply-batch season.csv --dry-run
```

The summary has `dry_run`, `file`, `count`, `changes`, and `files`. `files` lists only paths whose contents would change. A trailing newline by itself is not a change.

## Armies and fleets

`python3 tools/nb.py armies FILE` replaces the public army and fleet markers from one export of the Game Sheet tab Armies & Fleets. The previous markers are dropped. Running the same file again writes nothing.

```bash
python3 tools/nb.py --dry-run armies armies.csv
python3 tools/nb.py armies armies.csv --dry-run
```

The dry-run JSON has `count`, `skipped_private`, `markers`, and `files`. Each marker is `row`, `nation`, `type` (`army` or `fleet`), `province`, `label` (`Small`, `Large`, or `Fighting`), `x`, and `y`.

Columns are matched by header, ignoring case, spaces, and underscores. Extra columns, such as a head-count, are ignored and are not stored.

| field | accepted headers |
| --- | --- |
| nation | `Nation`, `nation id`, `country`, `realm` |
| type | `Type`, `kind`, `unit`, `unit type` |
| location | `Location`, `province`, `province id`, `where` |
| size/status | `Size/Status`, `size`, `status`, `strength`, `condition` |
| Public | `Public`, `published`, `visible` |

`Public` is `yes` or `no` (`y`/`n`, `true`/`false`, `1`/`0`). Only `yes` rows are drawn. `no` rows are still checked. `type` is army or fleet (`navy` and `ship` count as fleet). `location` is a province id, `Province 1333`, or a name from `data/province_names.csv`. `nation` is an id or a nation name. The label keeps a single word, Small, Large, or Fighting, and drops any numbers in that cell. A cell that is only a number, or that names two of those words, is an error.

Every bad row is listed, and nothing is written. A good row is placed inside its province, clear of the border and the water, the same way settlement markers are. A second marker in that province is moved off the first. The map draws them on Layers → Battles & armies: a shield for an army, a ship for a fleet, filled with the nation colour. The label is only that word.

`data/armies.csv` is the stored list: `nation,type,province,label,x,y`. A sample sheet is [examples/armies.csv](../examples/armies.csv). It is not a live order.

Each op uses the same rules as the matching `nb.py` command. A transfer updates `data/world.json`, `data/province_slots.json`, and `data/sector_control.csv`. A rename updates `data/province_names.csv`. A religion updates `data/religions.csv`. A capital move uses the capital-move logic. A war updates `data/wars.csv`.

`data/wars.csv` did not exist before this command. It is the list of active wars. The header is `nation_a,nation_b,state`. `nation_a` is the lower nation id. `state` is `active`. Ending a war deletes that row. The map does not draw wars.

Sample files, not a live order: [examples/apply-batch.json](../examples/apply-batch.json) and [examples/apply-batch.csv](../examples/apply-batch.csv).

## Ops

| op | required | optional |
| --- | --- | --- |
| `transfer` | `province`, `nation` (new owner). `to` is an alias of `nation`. | `from` (current owner; checked when set), `cycle` (default 0) |
| `rename` | `province`, `name` | |
| `religion` | `target` (`nation:25`, `province:878`, or a bare id when it is only one of those), `name`. `religion` is an alias of `name`. | `color` (hex). Omit it in JSON to keep an existing colour. A blank colour clears it. |
| `capital` | `nation`, `province` the nation owns. `capital_move` is an alias. | `name` |
| `war` | `action` (`start` or `end`), `nation_a`, `nation_b` | |

`op` may also be spelled `type`. Two rows that name the same province transfer, the same province rename, the same religion target, the same nation's capital, or the same pair of nations at war are a duplicate when they match and a conflict when they do not. Either one aborts the batch.

A capital move sees transfers earlier in the file. The nation has to own the province at that point.

## JSON

A list, or an object with a `changes` list. Each change is one object. Extra keys are ignored.

```json
{
  "changes": [
    {"op": "transfer", "province": 1202, "nation": 25, "from": 26, "cycle": 0},
    {"op": "rename", "province": 894, "name": "Province of Khanbaliq"},
    {"op": "religion", "target": "nation:25", "name": "Example Faith", "color": "#2f6fbe"},
    {"op": "religion", "target": "province:878", "name": "Local Rite"},
    {"op": "capital", "nation": 75, "province": 1333, "name": "Lighthollow"},
    {"op": "war", "action": "start", "nation_a": 23, "nation_b": 25}
  ]
}
```

`changes` entries are numbered from 1 in error messages.

## CSV

The header must include `op`. Other columns are optional. Empty cells are ignored, except `color`: a blank colour cell clears the colour. Row numbers in errors are file line numbers (the header is line 1).

```csv
op,province,nation,from,cycle,name,target,color,nation_a,nation_b,action
transfer,1202,25,26,0,,,,,,
rename,894,,,,Province of Khanbaliq,,,,,
religion,,,,,Example Faith,nation:25,#2f6fbe,,,
religion,,,,,Local Rite,province:878,,,,
capital,1333,75,,,Lighthollow,,,,,
war,,,,,,,,23,25,start
```

## Dry-run summary

```json
{
  "dry_run": true,
  "file": "season.json",
  "count": 1,
  "changes": [
    {"row": 1, "op": "rename", "province": 894, "name": "Province of Khanbaliq"}
  ],
  "files": ["data/province_names.csv"]
}
```
