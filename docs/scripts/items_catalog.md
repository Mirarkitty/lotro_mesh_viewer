# items_catalog.py

[`items_catalog.py`](../../items_catalog.py)

## Purpose

`items_catalog.py` sweeps **every** item property record in the game data
into a single searchable file, `items_catalog.jsonl` — one JSON line per
wearable item. This is what powers [viewer.md](viewer.md)'s `/search` route
(the item-name search box in `index.html`): rather than resolving an item's
name/class/appearance on every search keystroke, the catalog is built once
(offline, takes minutes) and the viewer just filters/scores the pre-built
rows in memory.

It sits at the same pipeline stage as [propset.py](propset.md) — item →
PropertiesSet — but performs that decode across the whole `client_gamelogic.dat`
archive instead of one item at a time, plus resolves each item's display
name from `client_local_English.dat`.

## CLI usage

```
python3 items_catalog.py [--item DID] [-o FILE] [--game-dir DIR] [--out-dir DIR]
```

| Argument | Meaning |
|---|---|
| `--item DID` | parse just this item DID (hex) and print its catalog row as JSON, instead of doing a full sweep |
| `-o`, `--output FILE` | catalog output path (default `items_catalog.jsonl` in the output root) |

Examples:

```
python3 items_catalog.py                       # full sweep (~minutes), writes items_catalog.jsonl
python3 items_catalog.py --item 0x7000DA5B      # single-item test, prints the row
```

## Public API

| Function | Signature | Returns |
|---|---|---|
| `resolve_name` | `resolve_name(si)` | resolved display-name string for a `STRING_INFO` value `{token, dataId}`, or `None`/the input unchanged if not a dict |
| `item_row` | `item_row(props_did, props)` | one catalog row dict, or `None` if the item has no `Item_WornAppearanceMapList` (i.e. isn't a wearable) |
| `sweep` | `sweep(out_path=None)` | walks every `0x79` property record in `client_gamelogic.dat`, writes one JSONL row per wearable item |

`item_row`'s output shape:

```
{did, name, item_class, quality, level, slot, equip_cat, material_type,
 icon, clothing_color, bodies: [{species, sex, key, app}]}
```

`did` here is the recovered **item** DID (properties DID minus
`propset.DBPROPERTIES_OFFSET`), matching the DID format the rest of the
toolkit expects (e.g. what [selector.py](selector.md) or the `/compose`
route takes as `item`).

## How it works internally

### Sweep

`sweep()` walks `client_gamelogic.dat` collecting every DID with high byte
`0x79` (property records — see [propset.py](propset.md)'s note that an
item's properties live at `itemDID + 0x09000000`, i.e. a `0x79` DID). For
each, it calls `propset.parse_properties` with the pre-loaded registry, and
writes a JSONL row only if `item_row` finds `Item_WornAppearanceMapList`
(non-wearable items — quest items, currency, etc. — are dropped). Progress
is logged every 20000 records; parse failures are counted but don't abort
the sweep.

### Name resolution (`resolve_name`)

An item's display name arrives from `propset` as a `STRING_INFO` value —
either an inline literal string, or `{token, dataId}` pointing at a
`client_local_<language>.dat` `0x25` text record. `resolve_name` looks up
that record, and rather than parsing its exact binary layout, does a
**lenient scan**: find the `token` (as a packed `u32`) in the record's raw
bytes, then walk forward up to 24 bytes trying every possible skip amount
for a plausible VLE char-count byte followed by that many UTF-16LE code
units, accepting the first candidate whose decoded text consists only of
printable characters (ordinal 31–0x3000) or a small allowed punctuation set
(`" -'’"`). The module's docstring records the believed-but-not-fully-pinned
`0x25` layout this scan works around:
`[DID u32][u32 count][per entry: u16 flag?, u32 token, u32 zero?, u32 ?, vle charcount, utf16 chars]`.

## Gotchas & lessons

- **Name resolution is a heuristic scan, not an exact parse** — unlike
  [propset.py](propset.md), [wearable2.py](wearable2.md), or
  [tex_extract.py](tex_extract.md)'s compact-surface fix, the `0x25` text
  record format here is not pinned exactly; `resolve_name` can fail
  (returns `None`) on names with unusual characters or an unexpected
  layout, and does so silently (no error surfaced per-item) — a systematic
  gap in the format understanding, not a per-item bug.
- **Non-wearable items are dropped entirely**, not stored with `bodies:
  []` — a request for `--item <did>` on something without
  `Item_WornAppearanceMapList` prints `null`, which can look like a parse
  failure when it's actually correct behavior for e.g. a crafting
  material.
- **Full sweep failures are swallowed per-item** (`except Exception:
  n_fail += 1`) so one malformed record doesn't abort a multi-minute sweep
  — but this means the final `fail=N` count is the only signal that
  something didn't parse; there's no per-DID failure log to go back and
  inspect which items were skipped.
- **`sweep()` is meant for detached/background use** given the runtime
  (~minutes over the whole `client_gamelogic.dat` archive) — the module
  docstring calls this out explicitly.

## See also

- [propset.py](propset.md) — the PropertiesSet parser this module drives at scale.
- [selector.py](selector.md) — resolves one item's exact worn-appearance binding (this module's `bodies` list is the same shape, minus per-body renderability).
- [viewer.md](viewer.md) — the `/search` and `/bodies` routes that consume `items_catalog.jsonl`.
- [../wardrobe.md](../wardrobe.md), [../properties.md](../properties.md) — format background.
- [INDEX.md](INDEX.md) — full script index.
