# History maintenance

The political photo and `data/flat.geojson` stay the Cycle 0 painting. Conquests are drawn on top for the selected turn. Do not rebuild the tiles to the latest owner.

## How a turn is built

`js/history.js` folds `data/history/index.json` from the start through the selected file.

- **Transfers accumulate.** A province keeps the last `owner` it was given. A later file lists only the provinces that change on that date. It is not a full owner snapshot.
- **A province with no transfer yet is not historical.** The page uses `data/world.json` `nation_id`, which is the latest owner. That is why every province that ever changes hands needs a Cycle 0 transfer of its **painted** original owner. `scripts/check_history.py` rejects a later transfer that omits that row, and a Cycle 0 owner that does not match `flat.geojson`.
- **Occupations replace the whole list** when the snapshot includes the key. Repeat occupations that are still in force, including ones from earlier turns. Leave the key out to keep the previous list. `[]` clears them. Occupation does not change the owner.
- Markers replace the same way. Alliances and renames replace when the key is present.

## What to edit for a conquest

1. Add a `transfers` row on **that cycle's** snapshot: `{ "province": 2200, "owner": 21 }`. `owner` is the nation that holds it from this turn on.
2. If this is the first time that province changes hands, add a Cycle 0 row for the **baked** owner (the `nation_id` already in `data/flat.geojson`), not the new owner and not the current `world.json` owner.
3. If the snapshot includes `occupations`, copy forward every occupation that continues. Add new ones. Drop only occupations that ended.
4. `python3 tools/nb.py transfer` updates `world.json`, province slots, and sector controllers for the **current** file. It does not write history. After it, still add the history delta and the Cycle 0 baseline. Sector rows use the cycle number so earlier turns keep the previous controller.

```bash
python3 scripts/check_history.py
node --test tests/political_fill.test.js tests/building_yields.test.js
python3 -m unittest tests/test_history_baseline.py tests/test_nb.py
```

Reload with `js/map.js?v=9` and `js/political_fill.js?v=1` in `index.html`. Bump those query strings when the fill rules change. A tile rebuild is not required and would erase the Cycle 0 paint.

## What the player sees

- Fully annexed land is covered by an opaque plate in the **owner's own** nation colour, so the previous political fill cannot show through. Verillian gains use Verillian mint (`#3efec9`). Terrillia gains use Terrillia olive (`#414b0d`).
- From Cycle 1, Terrillia (Ulgaltel, nation 229) is a Verillian vassal. The usual vassal wash and junior stripes sit on that plate, the same as on Terrillia's other land. The plate is not the bloc leader's colour by itself.
- Cycle 0 keeps the baked colours. Moving the slider back removes the plate.
- Occupied, not annexed, land keeps the owner's colour. Stripes are the occupation layer (Layers → Occupied territory). Sanbreque's hold on Telmorela (1642, 1616) and Verillian's hold on Vosur (2242) stay occupations.
- The red claims hatch follows the history owner and is not the political fill. Turning claims off removes the hatch only.
- Terrain and depth hide the plate. Political and Auto show it. Auto leaves the plate opaque so the old paint cannot blend through the tint; the rest of the map still fades. Up close, the flat board takes over and uses the same history owner.

## Do not

- Do not paint a global "current owner" transfer across every turn. That overwrites Cycle 0 and Cycle 1.
- Do not put the new owner in `cycle-0.json`.
- Do not rebuild `political.png`, the political tiles, or `flat.geojson` so every zoom shows the latest conquest.
- Do not treat the claims hatch as the owner colour.
- Do not drop `occupations` from a later snapshot that still includes the key, or continuing occupations disappear.
- Popups still show current counts and areas on earlier turns. Alliances are only as historical as the snapshot `alliances` key. The footer "As of Cycle N" comes from `province_slots.json`. Those are separate from this colour fix.
