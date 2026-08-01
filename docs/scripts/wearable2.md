# wearable2.py

[`wearable2.py`](../../wearable2.py)

## Purpose

`wearable2.py` (internally versioned "v7") is a **strict sequential
parser** for `0x20` worn-appearance records — the same record type
[selector.py](selector.md) and [tex_extract.py](tex_extract.md) reach into
with targeted byte-searches. Where those modules search for one specific
key or marker byte pattern, `wearable2.py` parses the record's full section
/ entry / block / group / part structure end to end, sequentially, with
assertions at every field — so it either fully validates a record or raises
with an exact byte offset and surrounding context.

This sits at the same pipeline stage as [selector.py](selector.md) (worn
appearance → mesh + material) but is the parser [compose.py](compose.md)
actually uses to walk one item's full entry (all dye blocks, all groups, all
parts) rather than just the base garment binding. See
[../wardrobe.md](../wardrobe.md) and [../dyes.md](../dyes.md) for the format
context.

## Record structure (derived 2026-07-31, validated byte-exact against full records)

```
u32 DID ; u32 0
u8 sectype ; vle count          -- first section (5/6: wearable entries)
count x entry:
    u32 key (0x1000xxxx)        -- Item_AppearanceKey
    u32 nblocks
    nblocks x block:
        f32 q                   -- dye floatCode (0.00, 0.01, ... steps; block0 usually 0.0/1.0)
        u32 gc ; f32 d
        gc x group { u32 x(0|0x2B DID) ; u32 material(0x30) ; u32 A ; u32 B ; f32 g }
        u32 pc ; f32 e
        pc x part { u32 tag(0x10) ; u32 mesh(0x06) ; f32 lod (ALWAYS present; often 0 on last) }
        u32 0                   -- block tail
then more sections: u8 type ; vle count ; payload
    type 1: count x { u32 key ; u32 val }
```

Each **entry** is one `Item_AppearanceKey` value (one item's binding into
this body's wardrobe); each entry can have multiple **blocks** — one per dye
variant (the `q` float is the dye floatCode, see [../dyes.md](../dyes.md)).
Each block has a **groups** list (material bindings, one per shader/material
pair) and a **parts** list (the actual mesh(es) drawn, each with a per-part
LOD float).

## CLI usage

```
python3 wearable2.py [did] [--game-dir DIR] [--out-dir DIR]
```

| Argument | Meaning |
|---|---|
| `did` | `0x20` worn-appearance DID, hex (default `0x20001E55`) |

Example:

```
python3 wearable2.py 0x20001E55
```

Output: the section-type summary line, then
`entries=<n> blocks/entry min=<a> max=<b> total=<c>`.

## Public API

| Function/class | Signature | Returns |
|---|---|---|
| `class R` | `R(d, p=0)` | byte-cursor reader: `u8/u32/f32/peek/vle` |
| `err` | `err(r, msg)` | raises `ValueError` with the current offset and the next 6 dwords for context |
| `parse_block` | `parse_block(r, i, bi)` | one block dict `{q, d, groups, e, parts}` |
| `parse_entry` | `parse_entry(r, i)` | one entry dict `{key, blocks}` |
| `parse_record` | `parse_record(content)` | `{did, sections, leftover}` for a full `0x20` record's raw content |
| `entries` | `entries(rec)` | the first non-`type-1` section's entry list (the wearable entries), or `[]` |

`parse_record` takes **content bytes** (from `DatFile.read_content`, not
`read_asset` — see [datfile.py](datfile.md)), consistent with this module's
strict/exact parsing style. `leftover` is `len(content) - final_cursor_pos`,
a built-in completeness check: a nonzero unexpected leftover after all
sections are consumed usually means a section type this parser doesn't
model was encountered without raising (in practice, the loop only stops
when `r.p >= len(content)`, so leftover should read 0 for records that parse
cleanly through every section).

## How it works internally

### VLE (same encoding as propset.py, reimplemented locally)

```
a = u8
if a == 0xE0: return u32
if (a & 0x80) == 0: return a
b = u8
if (a & 0x40) == 0: return b | ((a & 0x7F) << 8)
c = u16le
return ((a & 0x3F) << 24) | (b << 16) | c
```

### Fail-fast validation at every field

Every structural field has an explicit sanity check that calls `err()` on
violation, rather than silently accepting garbage and producing wrong
output downstream:
- `parse_block`: each group's `material` must be `0` or have high byte
  `0x30`; `partCount` must be `<= 200`; each part's `tag` must have high
  byte `0x10` (or be `< 0x100`, an escape hatch for some tag encodings);
  each part's `mesh` must have high byte `0x06`; the block must end with a
  zero-dword tail.
- `parse_entry`: the entry `key` must have high byte `0x10`; `nblocks` must
  be `<= 1000`.
- `parse_record`: the two dwords right after the DID must be `[0, ...]`
  (asserted); each top-level section's `stype` must be a known type (only
  type `1`, key/value pairs, and "not type 1" wearable-entry sections are
  handled — anything else raises `"unknown section type"`).

`err()`'s diagnostic includes the next 6 dwords read as hex, so a parse
failure on an unseen record layout variant shows exactly what byte pattern
broke the assumption, rather than a bare exception.

## Gotchas & lessons

- **This parser is strict on purpose.** Unlike [selector.py](selector.md)'s
  `resolve_binding` (byte-search for a known pattern, tolerant of
  surrounding unknown bytes) or [tex_extract.py](tex_extract.md)'s brute
  graph scans, `wearable2.py` asserts the *entire* record structure holds.
  That makes it the right tool to validate a record end-to-end or to walk
  ALL of an item's dye blocks/parts (which [compose.py](compose.md) needs),
  but it will raise on any record whose section-type or field layout
  deviates from this exact structure — it is not meant to be
  fault-tolerant.
- **The `lod` field is always present, even when its value is often 0 on
  the last part** — don't treat a trailing part missing its LOD float as a
  format variant; the field slot itself never disappears.
- **`q` (the dye floatCode) belongs to the *block*, not the entry** — an
  item can have multiple dye variants (multiple blocks) under the same
  `Item_AppearanceKey`; block 0 is usually the undyed base (`q` = 0.0 or
  1.0). See [../dyes.md](../dyes.md) for how `q` maps to an actual dye
  color.

## See also

- [../wardrobe.md](../wardrobe.md) — full item→appearance chain writeup.
- [../dyes.md](../dyes.md) — the dye floatCode (`q`) this parser exposes per block.
- [selector.py](selector.md) — the looser, single-key-lookup counterpart to this full-record parser.
- [compose.py](compose.md) — the primary consumer, walking `entries(rec)` and a chosen block's `parts`/`groups`.
- [datfile.py](datfile.md) — `read_content`, the correct way to fetch the bytes this module parses.
- [INDEX.md](INDEX.md) — full script index.
