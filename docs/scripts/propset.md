# propset.py

[`propset.py`](../../propset.py)

## Purpose

`propset.py` is a faithful Python port of Turbine's PropertiesSet binary
deserializer — ported byte-for-byte from dmorcellet's closed
`delta-lotro-dat-utils-11.11.jar` (decompiled with CFR):
`DBPropertiesLoader`, `PropertyUtils`, `PropertyDefinitionsLoader`,
`BufferUtils`, `GeoLoader`, `LoaderUtils`.

This is the pipeline's first stage after DAT access: item → **PropertiesSet**
→ worn appearance → mesh + material → texture → dye → render. It replaces
older byte-scanning heuristics with a real, typed parse: property values are
read according to the type declared in the client's own property dictionary
(master-property DID `0x34000000`), so nested arrays/structs — notably
`Item_WornAppearanceMapList`, the field [selector.py](selector.md) depends
on — decode exactly rather than approximately. See
[../properties.md](../properties.md) for the full format writeup.

## CLI usage

```
python3 propset.py [item] [--all] [--json] [--game-dir DIR] [--out-dir DIR]
```

| Argument | Meaning |
|---|---|
| `item` | item DID, hex (default `0x70021A13`, the Sleeveless Summer Dress) |
| `--all` | print every property (default: only appearance-related ones — name contains `Appearance`, `Worn`, `PhysObj`, or `Wearer`) |
| `--json` | dump the full property dict as JSON to stdout |

Examples:

```
python3 propset.py                          # Sleeveless Summer Dress, appearance props only
python3 propset.py 0x7000DA5B --all         # Exquisite Dress, every property
python3 propset.py 0x70021A13 --json > summer_dress.json
```

## Public API

| Function/class | Signature | Returns |
|---|---|---|
| `class Reader` | `Reader(data, pos=0)` | low-level cursor: `u8/u16/u32/u64/f32/f64/skip/vle/tsize/pascal/utf16` |
| `read_value` | `read_value(r, t)` | one property's scalar value, dispatched on type code `t` |
| `registry` | `registry(gl=None)` | `{pid: (name, typecode)}`, the property dictionary, loaded once and cached |
| `parse_properties` | `parse_properties(content, reg)` | `(did, {propertyName: value})` for a resource record's raw content |
| `load_item` | `load_item(item_did, gl=None)` | `(did, {propertyName: value})` for one item — resolves the paired properties record automatically |

`parse_properties`'s value shapes: scalars as Python numbers/strings,
`STRUCT` → `dict{childName: value}`, `ARRAY` → list of `(childName, value)`
tuples.

## How it works internally

### Format summary (little-endian; a record's content starts with its self-DID)

```
resource   = uint32 DID ; properties
properties = TSize count ; count * property(doublePid=True)
property   = uint32 pid ; (if doublePid) uint32 pid2==pid ; value(pid)
```

`value(pid)` is read according to `dictionary[pid].type` — 22 known type
codes (`STRING`, `STRING_TOKEN`, `WAVE_FORM`, `TIMESTAMP`, `TRI_STATE`,
`VECTOR`, `INSTANCE_ID`, `ENUM_MAPPER`, `FLOAT`, `PROPERTY_ID`, `STRUCT`,
`ARRAY`, `STRING_INFO`, `BITFIELD_64`, `INT`, `COLOR`, `POSITION`,
`BIT_FIELD32`, `LONG64`, `DATA_FILE`, `BOOLEAN`, `BIT_FIELD`), each with its
own reader in `read_value`. `STRUCT` reads a field count then that many
nested `property(doublePid=True)` entries; `ARRAY` reads an item count then
that many nested `property(doublePid=False)` entries — the recursion lives
in `_decode_property`, which calls itself for both cases.

### VLE (variable-length encoding)

```
b = u8
if b == 0xE0: return u32
if b < 0x80:  return b
b2 = u8
if (b & 0x40) == 0: return b2 | ((b & 0x7F) << 8)
c = u16le
return ((b & 0x3F) << 24) | (b2 << 16) | c
```

`TSize` = skip 1 byte, then a VLE (used for the top-level property count and
for `ARRAY` counts embedded via `STRUCT`/`ARRAY` handling).

### The property dictionary (`_load_registry`)

Loaded once (cached module-global `_registry`) from the master-property
record, DID `0x34000000`, in `client_gamelogic.dat`. Reads a name table (pid
→ Pascal string) then a definitions table (pid → type code + flags +
optional default values + child/required-property lists), asserting several
internal consistency invariants along the way (`pid2 != pid`, bad type code,
child PID mismatch, `numLast2` not zero) — any of these raising means the
dictionary layout assumption has drifted from what the client actually
ships.

### Item property lookup (`load_item`)

An item's PropertiesSet is **not** stored at the item DID itself — it lives
at `item_did + DBPROPERTIES_OFFSET` (`0x09000000`), the paired `0x79`
record. This matches `DATConstants.DBPROPERTIES_OFFSET` as used by
lotro-tools' `CosmeticLoader.loadProperties(itemId + 0x09000000)`.
[items_catalog.py](items_catalog.md) walks `0x79` records directly for this
reason (see its module docstring) rather than starting from `0x70` item
DIDs.

## Gotchas & lessons

- **Type-driven, not scan-driven.** This module exists specifically to
  replace earlier byte-scanning heuristics that worked by luck on some
  items and broke on others (see [selector.py](selector.md)'s docstring,
  which cites exactly this: the old heuristic "worked for the Summer Dress
  by luck and FAILED for the Exquisite Dress"). Reading nested
  `ARRAY`/`STRUCT` values by their declared type is what makes
  `Item_WornAppearanceMapList` decode correctly for every item, not just
  the one it was reverse-engineered against.
- **`DBPROPERTIES_OFFSET` is a constant, easy to get wrong.** Adding
  `0x09000000` to the *item* DID, not subtracting it from the properties
  DID, is the direction that matters — `items_catalog.py`'s `item_row`
  subtracts it back off (`props_did - propset.DBPROPERTIES_OFFSET`) to
  recover the original item DID for its catalog rows.
- **Unknown property IDs are fatal, not skipped.** `_decode_property`
  raises `KeyError` on a `pid` absent from the registry rather than
  skipping it — correct because a skip would desync the rest of the byte
  stream (there's no per-property length prefix to skip over safely).

## See also

- [../properties.md](../properties.md) — full PropertiesSet format writeup.
- [selector.py](selector.md) — the primary consumer, via `Item_WornAppearanceMapList`.
- [items_catalog.py](items_catalog.md) — walks `0x79` records and calls `parse_properties` directly at scale.
- [datfile.py](datfile.md) — `read_content` is how this module's raw bytes are fetched.
- [INDEX.md](INDEX.md) — full script index.
