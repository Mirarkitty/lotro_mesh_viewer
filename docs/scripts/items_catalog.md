# items_catalog.py

[`items_catalog.py`](../../items_catalog.py)

## Purpose

`items_catalog.py` sweeps **every** item property record in the game data
into a single searchable file, `items_catalog.jsonl` — one JSON line per
wearable item. This is what powers [viewer.md](viewer.md)'s `/search` route
(the item-name search box in `index.html`) and, more extensively,
[outfit_app.py](outfit_app.md)'s `/search`, `/sets`, and `/setmates` routes
(via [api_common.py](api_common.md)): rather than resolving an item's
name/class/appearance on every search keystroke, the catalog is built once
(offline, takes minutes) and both servers just filter/score the pre-built
rows in memory.

It sits at the same pipeline stage as [propset.py](propset.md) — item →
PropertiesSet — but performs that decode across the whole `client_gamelogic.dat`
archive instead of one item at a time, plus resolves each item's display
name from `client_local_English.dat`.

This is this toolkit's own answer to "what item DID has the name I want" —
built entirely from the local client files, no external data needed. For an
alternative/complementary name→item lookup (or for character/inventory
data, which this toolkit doesn't handle at all), see the
[LotroCompanion](https://github.com/LotroCompanion) project's `lotro-data` /
`lotro-items-db`.

The sweep covers **wearables** (rows with per-body bindings and presence
flags) and **held items** — weapons and class items, recognized by a
`PhysObj` plus a held-range `Inventory_DefaultSlot` bit (`0x10000`+) with
no `Item_WornAppearanceMapList`. Held rows carry `held: true`, a
`held_slot` label (MainHand/OffHand/Ranged/Held) and their `physobj` DID;
their geometry resolves through
[weapon_resolve.py](weapon_resolve.md) ([../weapons.md](../weapons.md)),
not the wardrobe chain, and they have no `bodies`/presence flags (so the
composer's wearable search ignores them; [explore.py](explore.md) finds
them by name).

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
| `_record_presence` | `_record_presence(app_did)` | `{appearanceKey: renderable bool}` for one `0x20` worn-appearance record |
| `augment_presence` | `augment_presence(rows)` | adds `bodies[i]["present"]` to every catalog row in place; returns the count of items with ≥1 renderable body |
| `sweep` | `sweep(out_path=None)` | walks every `0x79` property record in `client_gamelogic.dat`, writes one JSONL row per wearable item, then runs `augment_presence` over the whole set before writing |

`item_row`'s output shape:

```
{did, name, item_class, quality, level, slot, equip_cat, material_type,
 icon, clothing_color, bodies: [{species, sex, key, app}]}
```

`sweep`'s final JSONL row shape adds the presence flag `augment_presence`
bakes in (see below): `bodies: [{species, sex, key, app, present}, ...]`.

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

### Presence augmentation (`augment_presence`)

After the sweep collects every wearable item's raw row, `sweep()` runs one
more pass — `augment_presence(rows)` — before writing the JSONL file: for
every `bodies[i]` entry on every row, it parses that body's `0x20`
worn-appearance record (`app_did`, cached per-record so the same 0x20 file
backing many items is only parsed once — `_record_presence`) and decides
whether that specific `(app, key)` combination is actually renderable, the
same rule [selector.py](selector.md)/[compose.py](compose.md)/
[viewer.md](viewer.md)'s `/bodies` route use elsewhere: look at the entry's
`0x1000000C` garment-tagged part (`GARMENT_TAG`, or any part if none carries
that tag), resolve its mesh through the chain, and require it to be a real
mesh (`> 2000` bytes, `_STUB_BYTES`) rather than a placeholder stub. The
result is written back onto the row as `bodies[i]["present"]`.

This is **required**, not optional decoration: [api_common.py](api_common.md)'s
`search`, `setmates`, and `sets_index` all filter on
`any(b.get("present") for b in r["bodies"])` — an item catalog built without
this pass (e.g. by calling `item_row`/writing JSONL directly, bypassing
`sweep`) will look empty to `/search` and `/sets` in
[outfit_app.py](outfit_app.md), even though the raw rows are present, because
every `present` key would be missing (falsy) rather than `False` or `True`.

This pass is also most of `sweep()`'s runtime: it parses one `0x20` record
per **distinct** worn-appearance DID across the whole catalog (not per row —
the per-`app_did` cache means a body shared by many items is only decoded
once), which is a substantial fraction of a multi-minute sweep on top of the
`0x79` property-record walk. Progress is logged every 5000 rows
(`presence %d/%d (records cached: %d)`), separately from the sweep's own
20000-row progress log.

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
  docstring calls this out explicitly, and the presence-augmentation pass
  (see above) adds a second, comparably-sized pass on top of the property
  sweep, so a full rebuild takes noticeably longer than the sweep alone.
- **Skipping `augment_presence` breaks search silently** — a hand-rolled
  catalog file that writes `item_row`'s output directly (without going
  through `sweep`) has no `present` key on any `bodies` entry, which reads
  as falsy everywhere [api_common.py](api_common.md) checks it; every
  `/search`/`/sets`/`/setmates` result would come back empty with no error,
  not an obviously "presence missing" symptom.

## See also

- [propset.py](propset.md) — the PropertiesSet parser this module drives at scale.
- [selector.py](selector.md) — resolves one item's exact worn-appearance binding (this module's `bodies` list is the same shape, minus per-body renderability).
- [wearable2.py](wearable2.md) — the `0x20` record parser `_record_presence` uses.
- [viewer.md](viewer.md) — the `/search` and `/bodies` routes that consume `items_catalog.jsonl` (single-item viewer).
- [api_common.py](api_common.md) — the outfit composer's `search`/`setmates`/`sets_index`, which all require the `present` flag this module bakes in.
- [../wardrobe.md](../wardrobe.md), [../properties.md](../properties.md) — format background.
- [INDEX.md](INDEX.md) — full script index.
