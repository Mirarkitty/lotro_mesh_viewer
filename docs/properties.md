# PropertiesSet Format and the Property Dictionary

This is the piece that turns "an item ID" into "which body-type records to
look at" — without it, nothing downstream (wardrobe lookup, mesh, texture)
has anywhere to start from. Reference implementation:
[`propset.py`](scripts/propset.md). Consumed by
[`selector.py`](scripts/selector.md) — see [wardrobe.md](wardrobe.md).

## Where an item's properties actually live

**An item's PropertiesSet is not stored at the item's own DID.** Items are
indexed in the game-logic archive as a `0x70` record (e.g. `0x70021A13` for
a particular dress item), but that record is only the item's **index /
`WState`** — it starts with a class tag (e.g. `795 = IClothing`) and does
not carry appearance data directly.

The actual property bag lives at **`itemDID + 0x09000000`** — the paired
**`0x79`** record. This offset constant is confirmed independently by two
sources: the `DATConstants.DBPROPERTIES_OFFSET` value used in the
`dmorcellet` DAT-utilities crib (see attribution below), and the
[LOTRO-Companion](https://github.com/LotroCompanion) `lotro-tools`
`CosmeticLoader.java`, which loads item cosmetic properties from
`itemId + 0x09000000`. So item `0x70021A13`'s properties live at
`0x79021A13`.

## Why this was hard: a closed type dictionary — that turns out to be game data

Turbine's `PropertiesSet` is a **generic, self-describing property bag**:
each property is a `(pid, value)` pair, and the value's binary shape depends
on a **type code looked up by `pid`** — the record itself does not say what
type each property is inline. The relevant type dictionary is normally part
of closed client tooling and not publicly documented, which initially looked
like a hard blocker.

**It isn't, because the dictionary is game data, not client code.** The
pid → `(name, type)` map is itself stored in the client, as a normal record:
the **master property DID `0x34000000`** in the game-logic archive, holding
tens of thousands of entries. Reading and decoding it once (and caching the
result) gives the full property dictionary. Once that table is available,
arbitrary `PropertiesSet` content — including nested arrays and structs —
decodes exactly.

**Attribution**: the wire-format understanding for this project's parser was
derived from `delta-lotro-dat-utils` by dmorcellet (a `.jar`, CFR-decompiled
for study; bundled inside a [LOTRO-Companion](https://github.com/LotroCompanion/lotro-companion)
SourceForge distribution). That jar is a crib only — **nothing about it is
needed at runtime**; every value this project's tools read comes from the
client's own data files. The jar was simply the fastest way to learn the
wire format instead of re-deriving it from scratch by fuzzing.

## Binary format

A resource's decompressed content (read via `read_content`, not
`read_asset` — see [dat-format.md](dat-format.md)) starts with its own DID:

```
resource   = u32 DID ; properties
properties = TSize count ; count x property(doublePid=True)
property   = u32 pid ; (if doublePid) u32 pid2 (must == pid) ; value(pid)
```

`value(pid)` is dispatched by `dictionary[pid].type` — **not inline** in the
stream, hence the need for the registry above:

| code | name | wire shape |
|---|---|---|
| 1 | STRING | Pascal string (VLE length + ISO-8859-1 bytes) |
| 2 | STRING_TOKEN | u32 |
| 3 | WAVE_FORM | variable |
| 4 | TIMESTAMP | f64 |
| 5 | TRI_STATE | u8 |
| 6 | VECTOR | 3 x f32 |
| 7 | INSTANCE_ID | u64 |
| 8 | ENUM_MAPPER | u32 |
| 9 | FLOAT | f32 |
| 10 | PROPERTY_ID | u32 |
| 11 | STRUCT | u8 skip + u8 nbFields, then nbFields x property(doublePid=True) |
| 12 | ARRAY | u32 nbItems, then nbItems x property(doublePid=False) |
| 13 | STRING_INFO | variable |
| 14 | BITFIELD_64 | u64 |
| 15 | INT | u32 |
| 16 | COLOR | 4 x u8 |
| 17 | POSITION | variable |
| 18 | BIT_FIELD32 | u32 |
| 19 | LONG64 | u64 |
| 20 | DATA_FILE | u32 (a DID reference) |
| 21 | BOOLEAN | u8 |
| 22 | BIT_FIELD | VLE bitcount + that many bytes |

`STRUCT` and `ARRAY` recurse: read the item count via the VLE reader below,
then loop decoding that many child properties — `doublePid=True` for STRUCT
fields, `doublePid=False` for ARRAY items. This distinction is easy to get
backwards; verify it carefully when porting.

### Variable-length encoding (VLE)

Used for array/struct sizes (`TSize` = skip 1 byte, then a VLE value) and
string lengths:

```
b = u8
if b == 0xE0: return u32                       # explicit 4-byte escape
if b < 0x80:  return b                          # 1-byte value
else: b2 = u8
      if (b & 0x40) == 0: return b2 | ((b & 0x7F) << 8)             # 2-byte value
      else: c = u16le; return ((b & 0x3F) << 24) | (b2 << 16) | c   # 3.5-byte value
```

### The master property record

The master-property record itself (`0x34000000`) is parsed by a separate,
simpler routine that does *not* go through the general property decoder
above (it is the thing that makes the general decoder possible in the first
place): it reads a name table keyed by pid, then a definition table
`(pid, type code, group, provider, dataId, flags, timeout, various per-entry
flags, child pids, required pids)`, and builds the `{pid: (name, typecode)}`
map consumed everywhere else.

## Load-bearing property IDs

Real numeric values, verified against client data:

- **`Item_WornAppearanceMapList`** — pid `0x10000D8E`, type **ARRAY** of
  **STRUCT**. One entry per (species, sex) body variant the item can be worn
  by. Fields of each struct:
  - **`Item_SpeciesOfWearer`** — pid `0x10000748`, `ENUM_MAPPER`. Race —
    see [wardrobe.md](wardrobe.md) for the species → race mapping.
  - **`Item_SexOfWearer`** — pid `0x1000132D`, `ENUM_MAPPER`. Sex is a **bit
    flag**: `4096` (`0x1000`) = male, `8192` (`0x2000`) = female.
  - **`Item_AppearanceKey`** — pid `0x10001229`, `ENUM_MAPPER`. The value
    used to select the correct draw entry inside the body's worn-appearance
    record — see [wardrobe.md](wardrobe.md).
  - **`Item_WornAppearance`** — pid `0x10001434`, type `DATA_FILE` — a
    `0x20` DID, the per-body-type wardrobe record (see
    [wardrobe.md](wardrobe.md)).
- **`PhysObj`** — pid `0x0000047F`, `DATA_FILE`. Base-body mesh DID /
  fallback.
- Item type tags seen at the head of `0x70` index records: `795 = IClothing`,
  `797 = IShield`, `799 = IWeapon`.

## Consumer usage

Loading an item's properties (via the `itemDID + 0x09000000` offset above)
returns `(did, {propertyName: value})`. On top of that, the wardrobe
selector walks `Item_WornAppearanceMapList` into a flat per-body-type list
of `{species, sex, key, worn_appearance}` records, plus the item's
`PhysObj` — the direct input to the wardrobe selector described in
[wardrobe.md](wardrobe.md).

## See also
- [dat-format.md](dat-format.md) — `read_content`, the reader this format is layered on
- [wardrobe.md](wardrobe.md) — what `Item_WornAppearance` / `Item_AppearanceKey` are used for
- [overview.md](overview.md) — pipeline position
- [scripts/propset.md](scripts/propset.md) — the reference implementation
- [limitations.md](limitations.md) — species-enum values not fully mapped to race names
