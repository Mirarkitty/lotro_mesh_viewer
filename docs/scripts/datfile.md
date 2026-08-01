# datfile.py

[`datfile.py`](../../datfile.py)

## Purpose

`datfile.py` is the bottom layer of the whole toolkit: a reader for
Turbine's DAT container format, the block-structured archive with a B-tree
directory that every `client_*.dat`/`*.datx` file uses. Every other script
(mesh, texture, property, wardrobe decoding) ultimately calls through this
module (usually via [config.py](config.md)'s cached handles) to turn a DID
(32-bit data ID) into raw bytes. See [../dat-format.md](../dat-format.md)
for the full container-format writeup this module implements.

The high byte of a DID encodes its resource type — 0x06 = mesh, 0x25 = text,
0x30 = material, 0x41 = texture, etc. — which is how downstream code (e.g.
[mesh_decode.py](mesh_decode.md), [tex_extract.py](tex_extract.md)) knows
what kind of record it just fetched.

## CLI usage

```
python3 datfile.py <archive> info|walk|find|extract [DID] [-o FILE]
```

| Argument | Meaning |
|---|---|
| `archive` | path to a `client_*.dat`/`*.datx` file |
| `cmd` | `info` (header summary), `walk` (list all entries), `find` (look up one DID), `extract` (write a DID's true content bytes to a file) |
| `did` | DID for `find`/`extract`, hex (e.g. `0x06001989`) |
| `-o`, `--output FILE` | output file for `extract` (default `<DID>.bin`) |

Examples (run from inside the game install directory, or give a full path —
e.g. `"$LOTRO_DIR/client_mesh.dat"`):

```
python3 datfile.py client_mesh.dat info
python3 datfile.py client_mesh.dat walk | head
python3 datfile.py client_mesh.dat find 0x06001989
python3 datfile.py client_mesh.dat extract 0x06001989 -o spindle.bin
```

Note: this CLI takes the archive path directly (not `--game-dir`); it's a
low-level, single-file inspection tool, unlike the higher-level scripts that
resolve archives through [config.py](config.md).

## Public API

### `class DatFile`

One open archive handle.

| Member | Signature | Notes |
|---|---|---|
| `__init__` | `DatFile(filename)` | opens the file, validates the `PL`/`BT` magic, reads block size/archive size/version/root dir offset |
| `read_dir` | `read_dir(offset)` | returns `(subdirs, entries)` for one B-tree node — **all** entries unfiltered, so B-tree order holds |
| `walk` | `walk(visit, offset=None)` | calls `visit(entry)` for every entry with `size1 > 0`, depth-first over the whole directory tree |
| `find_file` | `find_file(target, off=None)` | B-tree lookup by DID; returns the entry tuple `(file_id, foff, size1, size2, ts, ver, flags)` or `None` |
| `read_file` | `read_file(offset, size)` | raw bytes at an arbitrary file offset |
| `read_content` | `read_content(did)` | **true file content** bytes for `did` (block-chain assembled, zlib-inflated if compressed) |
| `read_asset` | `read_asset(foff, size2)` | `(usize, decompressed_bytes)` using the legacy asset framing |

### `class DatChain`

Looks up DIDs across several archives (e.g. `client_mesh.dat` +
`client_mesh_aux_1.datx`).

| Member | Signature | Notes |
|---|---|---|
| `__init__` | `DatChain(*paths)` | opens one `DatFile` per path |
| `find_file` | `find_file(did)` | searches each archive in order; remembers which archive owns the hit so `read_asset` can route to it |
| `read_asset` | `read_asset(offset, size)` | routed to the owning archive; falls back to trying every archive if the offset/size pair wasn't seen via `find_file` first |
| `read_content` | `read_content(did)` | routed to whichever archive's `find_file` succeeds first |
| `walk` | `walk(visit)` | walks every member archive in sequence |

## How it works internally

### Header layout (fixed offsets from file start)

```
0x101  u16  0x4C50 ('PL')   magic
0x140  u16  0x5442 ('BT')   B-tree marker
0x144  u32  block size
0x148  u32  archive size
0x14C  u32  version
0x160  u32  root directory-node offset
```

### B-tree directory node (`read_dir`)

Each node starts with 8 zero bytes, then up to 62 `(blockSize, dirOffset)`
subdirectory pointer pairs (terminated by a zero `blockSize`), then at a
fixed offset (`offset + 8*63`) a `u32` entry count followed by that many
32-byte entry records: `(unk1, file_id, foff, size1, ts, ver, size2, unk2)`.
`unk1`'s low 16 bits are flags (bit 0 = compressed).

### Two payload framings — and why both exist

- **`read_content(did)`** — the exact ("true") file bytes, assembled by
  following the block chain (`[numExtraBlocks][legacy][firstChunk]` then
  `(size, offset)` pairs for any extra blocks) and zlib-inflating when the
  entry's compressed flag is set. Content always starts with the record's
  own DID. **Use this for anything parsed byte-exactly**: property sets,
  wardrobe records, materials.
- **`read_asset(offset, size)`** — the historical mesh/texture framing:
  `[next_ptr:u32][pad:u32][uncompressed_size:u32][body]` where `body` is
  usually a raw zlib stream. Kept because the mesh and texture decoders were
  validated against exactly these bytes.

`read_asset` reads to a whole archive block, which **over-reads small
records** — neighbouring records can bleed into the tail. This is a real,
observed bug source (see the compact-surface-record fix in
[tex_extract.py](tex_extract.md) and [../textures.md](../textures.md)) —
whenever a record is parsed byte-exactly (not brute-scanned), `read_content`
is the correct call.

### Locking

`DatFile` is **not** lock-free: every reader shares `self.f` (one open file
handle), so each seek+read sequence is guarded by `self.lock`, an `RLock`
(re-entrant because `read_content` calls `find_file`, which itself seeks).
This is what lets [viewer.md](viewer.md) (`app.py`) run Flask with
`threaded=True` — without it, two concurrent slot composes would interleave
their seeks on the same handle and silently return each other's bytes.

## Gotchas & lessons

- **`read_asset` over-reads.** It reads `size2 + 64` bytes from the file
  offset regardless of the record's true size, then finds a zlib magic
  (`0x78 0x01/0x9C/0xDA`) inside that window. For small records this window
  extends into a neighbouring record's bytes — safe for the mesh/texture
  formats it was validated against (which self-delimit via their own vertex
  counts / DXT fourcc), but a real hazard for anything else. See
  [tex_extract.py](tex_extract.md)'s compact-surface-record fix, which
  switched from `read_asset` to `read_content` specifically to avoid this.
- **Legacy block format is unimplemented.** `read_content` raises
  `NotImplementedError` if the block-chain header's `legacy` field is
  nonzero — not encountered in the archives this toolkit targets, but worth
  knowing if a `NotImplementedError` surfaces from deep in the pipeline.
- **`DatChain.read_asset`'s per-archive fallback loop swallows exceptions**
  (`except Exception: continue`) when the `(offset, size)` pair wasn't
  registered by a prior `find_file` call — this can mask a genuine decode
  error as a "no archive owns asset" `KeyError` instead. Not a bug that's
  been hit in practice, but a sharp edge if you call `read_asset` directly
  on a `DatChain` without going through `find_file` first.

## See also

- [config.py](config.md) — caches one `DatFile`/`DatChain` per archive; almost every other script reaches this module through it.
- [mesh_decode.py](mesh_decode.md), [tex_extract.py](tex_extract.md) — the primary `read_asset` consumers.
- [propset.py](propset.md), [selector.py](selector.md), [wearable2.py](wearable2.md) — the primary `read_content` consumers.
- [../dat-format.md](../dat-format.md) — full container format writeup.
- [INDEX.md](INDEX.md) — full script index.
