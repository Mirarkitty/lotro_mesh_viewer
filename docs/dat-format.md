# DAT Container Format

The LOTRO client stores essentially everything in Turbine `.dat` archives:
`client_mesh.dat`, `client_general.dat`, `client_gamelogic.dat`,
`client_surface.dat`, `client_highres.dat`, `client_anim.dat`,
`client_cell_*.dat`, `client_local_*.dat` (per-language UI/quest/item string
tables), plus `.datx` auxiliary archives that extend some of the above
(`client_mesh_aux_1.datx`, `client_highres_aux_*.datx`, …). All of them share
one container format. Reference implementation:
[`datfile.py`](scripts/datfile.md), class `DatFile`. See [overview.md](overview.md)
for where this fits in the pipeline.

Archive sizes are large — the mesh archive alone runs into the low single
digits of gigabytes, and the animation archive several hundred megabytes — so
tooling reads should always go through the B-tree lookup below rather than
scanning the file.

⚠️ **`.datx` auxiliary archives matter.** Newer or overflow content (later
mesh waves, higher-resolution textures) is frequently stored only in the
`.datx` companion archive next to a `.dat` file, not in the base archive
itself. A presence check that only opens the base `.dat` file will report
content as missing when it in fact ships — this was diagnosed twice
independently in this project (once for whole garment meshes, once for hair
meshes) before the fix (a multi-archive chain lookup) was applied uniformly.
Always check the `.datx` companion(s) for a type before concluding a DID is
absent.

## Header

Fixed offsets from the start of the file (all little-endian `uint32` unless
noted):

| Offset | Field | Value |
|---|---|---|
| `0x101` | magic | `PL` (`0x4C50`) |
| `0x140` | magic | `BT` (`0x5442`) |
| `0x144` | `block_size` | `0x4400` (17408 bytes) — directory-node and block-chain granularity |
| `0x148` | total file size | |
| `0x14C` | version | |
| `0x160` | root directory offset | |

A reader should read the first 2048 bytes, assert both magic values, and
retain `block_size`, `size`, `version`, and `dir_off`.

## B-tree directory

The directory is a B-tree; each node fits in one block (a node is
approximately 2460 bytes, well under `block_size`, and is read
contiguously — there is no chaining within a single node). Node layout at a
given `offset`:

```
[8 bytes, must be zero]                         node prefix
up to 62 x <uint32,uint32> (block_size, dir_offset)   subdir pointers; stop at first 0
@ offset + 8*63:  <uint32> count                 number of file entries
count x 0x20-byte file entries:
    <8 x uint32> = unk1, file_id, file_offset, size1, timestamp, version, size2, unk2
```

`unk1` packs `[flags:u16][policy:u16]`; **`flags & 1` marks the entry as
zlib-compressed** (relevant to the `read_asset` path below). Reading a
directory node returns `(subdirs, entries)` unfiltered, preserving B-tree
order for lookup.

**Lookup** (`find_file(target_did)`) is a standard B-tree search: entries in
a node are sorted by `file_id`; if the target is less than `entries[i]`,
recurse into `subdirs[i]`; if the target exceeds every entry in the node,
recurse into the last subdir. A full-archive walk (visiting every entry with
`size1 > 0`) is an iterative depth-first traversal, used for archive-wide
surveys — e.g. counting every mesh record of a given type prefix.

**File data is stored CONTIGUOUSLY.** Some Turbine DAT format variants use a
per-block "next pointer" for block-chained files; in the archives this
project reads, that field is unused for allocated files — it is always zero.
Do **not** implement block-chained reads from that field for the mesh/texture
path; a plain contiguous `read(file_offset, size)` is correct. Treating a
zero next-pointer as "one more chained block of zero length" (or
accidentally chaining on it) produces garbage — this was the project's first
wrong turn in reading the container.

## Two decompression paths

The reference implementation has **two different readers**, because two
different record families use two different on-disk framings for their
content.

### `read_asset(file_offset, size2)` — mesh and texture records

```
[4B next-ptr = 0][4B pad = 0][4B uncompressed_size][zlib stream @ offset 12 ...]
```

Read `size2 + 64` bytes as a safety margin, then scan a small window
starting at offset 12 for a zlib header (`0x78` followed by `0x01`, `0x9C`,
or `0xDA`) and decompress from there with a streaming zlib decompressor
(self-delimiting — it is safe to decode past the declared size). If no zlib
header is found, fall back to treating the bytes as already-uncompressed
(some assets are stored uncompressed). This returns
`(uncompressed_size, decompressed_bytes)`.

Used for mesh (`0x06`) and texture (`0x41`) records — see
[mesh-format.md](mesh-format.md) and [textures.md](textures.md).

### `read_content(did)` — the general block-chain assembler

Needed for record types whose on-disk framing doesn't match the simple mesh
layout above — notably `PropertiesSet` records in the game-logic archive
(see [properties.md](properties.md)). This path mirrors the well-known
`DATArchive.loadEntry` logic from the `dmorcellet`
[delta-lotro-dat-utils](https://sourceforge.net/) tooling used as a crib for
this project (see [properties.md](properties.md) for full attribution):

```
header @ file_offset: [numExtraBlocks:u32][legacy:u32]
firstChunk = min(block_size - 8 - numExtraBlocks*8, size1) bytes, read here
if numExtraBlocks:
    a (size:u32, offset:u32) table follows firstChunk;
    each (size, offset) pair is a further contiguous run read from
    elsewhere in the archive and appended, until size1 total bytes
    are assembled
raw = the assembled bytes, truncated to size1
if entry.flags & 1:  content = zlib_decompress(raw[4:])   # raw[0:4] = uncompressed size
else:                content = raw                        # starts with the self-DID
```

`legacy != 0` should raise/reject — it hasn't been observed in the archives
this project reads.

Content decoded via `read_content` **starts with the record's own DID**
(`uint32` little-endian). Downstream parsers (property-set readers in
particular) rely on this to sanity-check they landed on the right record.

⚠️ **Note on `client_general.dat` `0x01` and `0x2B` records**: these are
stored *uncompressed* with a different header shape than mesh records, so
`read_asset`'s declared-size field is wrong for them (the DID and content
bytes downstream are still valid). Not yet reconciled into one unified
reader — a known rough edge, not currently a blocker for anything documented
here.

## DID type map (which archive holds what)

The high byte of a DID is a record-type tag; the low 24 bits are a
per-type serial. Types relevant to this documentation set:

| Archive | Type byte | Approx. count | What |
|---|---|---|---|
| `client_mesh.dat` | `0x06` | ~39,800 | GfxObj meshes (`0x0600____` mostly static-ish, `0x0601____` a smaller skinned-heavy subset) |
| `client_anim.dat` | `0x05` | ~25,400 | **Animation clips only** (no skeletons) — Havok tagfile format, see [animation.md](animation.md) |
| `client_general.dat` | `0x01` | ~1,000 | "Setup"/Model — placement matrices + skeleton + part meshes (composite-assembly primitive; structurally identified, not fully parsed) |
| `client_general.dat` | `0x20` | ~4,200 | Worn-appearance / per-body-type wardrobe records, see [wardrobe.md](wardrobe.md) |
| `client_general.dat` | `0x2B` | ~400 | Compiled shader instance (HLSL constant tables) |
| `client_general.dat` | `0x30` | ~24,000 | Material (references `0x40` mip-chain slots) |
| `client_general.dat` | `0x31` | ~13,700 | Surface / render-property (per-submesh, references `0x2B`/`0x30`) |
| `client_general.dat` | `0x40` | ~80,000 | Texture mip-chain (ordered list of `0x41` DIDs at successive resolutions) |
| `client_general.dat` | `0x1F` | ~29,000 | Held-item template: one per visual object, its last `u32` references a `0x04` skeleton/mesh-trailer record — see [weapons.md](weapons.md) |
| `client_general.dat` | `0x04` | ~28,500 | **Havok skeletons** (`hkaSkeleton`/`hkaBone`), named bone hierarchies — see [animation.md](animation.md); on a held-item `0x1F` template's target, a degenerate case of this same record type carries a `[u8 count][count x u32]` mesh-DID trailer instead of being used as a real character skeleton, see [weapons.md](weapons.md) |
| `client_surface.dat` / `client_highres.dat` | `0x41` | — | DXT-compressed texture payloads, see [textures.md](textures.md) |
| `client_gamelogic.dat` | `0x70` / `0x79` | ~380,000 each | Item index/`WState` (`0x70`) paired with its PropertiesSet (`0x79` = `0x70` + `0x09000000`) |
| `client_gamelogic.dat` | `0x34000000` | 1 (master) | The property-ID → name+type dictionary, see [properties.md](properties.md) |
| `client_gamelogic.dat` | `0x47` | ~40,000 | Avatar chargen / entity records — see [hair-face.md](hair-face.md); the same type tag is also used for a held item's `PhysObj` entity record (class tag, template ref, render-hint properties — no geometry), see [weapons.md](weapons.md) |
| `client_gamelogic.dat` | `0x0C` | ~29,000 | Other game entities/props |

`client_cell_*.dat` (terrain) and `client_local_*.dat` (UI/quest/item strings,
per language) exist and follow the same container format, but terrain
decoding is out of scope for this documentation set (string-table records are
used incidentally — see [animation.md](animation.md) for emote name lookups).

## See also
- [overview.md](overview.md) — project status and end-to-end pipeline
- [mesh-format.md](mesh-format.md) — what's inside a `0x06` record once decompressed
- [textures.md](textures.md) — what's inside a `0x41` record
- [weapons.md](weapons.md) — the `0x47`/`0x1F`/`0x04` held-item chain
- [properties.md](properties.md) — the `0x79` PropertiesSet format, read via `read_content`
- [scripts/datfile.md](scripts/datfile.md) — the reference implementation
