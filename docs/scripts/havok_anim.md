# havok_anim.py

[`havok_anim.py`](../../havok_anim.py)

## Purpose

`havok_anim.py` decodes LOTRO's `0x05` animation clip records from
`client_anim.dat`: a generic Havok binary **tagfile** parser, plus a
spline-compressed-animation decompressor built on top of it. It is the
lowest layer of the animation branch of the pipeline — mesh + skeleton +
**clip** — feeding [export_skinned.py](export_skinned.md)'s `clip_json` and
`skeleton_bones`. See [../animation.md](../animation.md) for the full
skeleton/clip format writeup and walk-cycle identification notes.

Reference sample used in the CLI default: clip `0x050039EA`.

## Key finding: tagfiles, not packfiles

LOTRO does **not** ship Havok binary *packfiles* (magic `0x57E0E057`) — the
`0x05` records are Havok binary **tagfiles** (magic dwords `0xCAB00D1E
0xD011FACE`), preceded by a 13-byte LOTRO-specific prefix:

```
off 0  float32   duration (matches the hkaAnimation duration field)
off 4  uint32    0x2C (constant, all clips surveyed)
off 8  uint8     0x00
off 9  uint32    remaining payload size (tagfile size + 1)
off 13 tagfile   magic0=0xCAB00D1E magic1=0xD011FACE, then tag stream
```

The binary tagfile is self-describing: `TAG_METADATA` entries carry full
class reflection (name, parent, members with types), and objects are
serialized as a member-presence bitmap followed by only the present
members. Struct arrays are stored **SoA** (structure-of-arrays: one bitmap,
then each present member as its own column array, not row-by-row).

Reference material cited in the source: the Havok SDK
`hkBinaryTagfileCommon.h` (tag ids), the open-source parser
`github.com/exyorha/hkxparse` (`HKXTagfileParser.cpp`), and — for the
spline decompression algorithm specifically — `PredatorCZ/HavokLib`
(`hka_spline_decompressor.cpp`, GPLv3), following "The NURBS Book" alg.
A2.1 / Basis_ITS1 for the NURBS basis evaluation.

## CLI usage

```
python3 havok_anim.py [did] [--game-dir DIR] [--out-dir DIR]
```

| Argument | Meaning |
|---|---|
| `did` | clip DID, hex (default `0x050039EA`) |

Example:

```
python3 havok_anim.py 0x050039EA
```

Output: one summary line — `duration`, `numFrames`, and transform-track
count.

## Public API

| Function | Signature | Returns |
|---|---|---|
| `parse_tagfile` | `parse_tagfile(raw)` | `(root_object, all_objects)` — generic tagfile parse; `raw` is a full `0x05` record (LOTRO prefix + tagfile) |
| `find_objects` | `find_objects(root, class_name)` | all reachable object dicts whose class chain contains `class_name` |
| `parse_packfile` | `parse_packfile(raw)` | dict of `hkaSplineCompressedAnimation` (or `hkaInterleavedUncompressedAnimation`/`hkaAnimation`) fields — name kept for historical reasons; LOTRO clips turned out to be tagfiles, not packfiles |
| `decode_clip` | `decode_clip(did, dat=None)` | `{duration, numFrames, numberOfTransformTracks, tracks}` |
| `class TagfileParser` | `TagfileParser(raw)` | the underlying parser class; `.parse()` returns `(root, objects)` |
| `class SplineAnimation` | `SplineAnimation(anim)` | decoded-on-demand wrapper; `.sample_time(t)` / `.sample_frame(f)` return per-track `{t, q, s}` |

`decode_clip`'s `tracks` shape: `tracks[track_index][frame_index] ->
{'t': (x,y,z), 'q': (x,y,z,w), 's': (x,y,z)}` — track order matches
skeleton bone order (see [export_skinned.py](export_skinned.md)).
`decode_clip` currently only supports `hkaSplineCompressedAnimation`
clips; anything else raises `NotImplementedError`.

## How it works internally

### Tagfile parser (`TagfileParser`)

A recursive-descent reader over a varint-and-tag-typed stream:
- `TAG_FILE_INFO` reads the tagfile version (3 or 4; version 4 also carries
  a Havok-version string).
- `TAG_METADATA` reads one class definition (`_read_type`): name, version,
  parent-type index, and a member list (each member: name, type code,
  optional tuple size, optional referenced class name).
- `TAG_OBJECT` / `TAG_OBJECT_REMEMBER` parse one object's fields
  (`_parse_struct`): base-class members first (`_all_members` walks the
  parent chain and concatenates oldest-first), a presence bitmap sized to
  the member count, then only the present members' values.
- Object references (`T_OBJECT` fields) resolve through `_get_object`,
  which returns the SAME dict instance for a given remember-index —
  allowing reference cycles and shared substructure, matching Havok's
  actual object graph.
- Arrays: `F_ARRAY`/`F_TUPLE` flag bits on a member type select fixed-size
  vs. varint-prefixed-size array parsing; `T_STRUCT` arrays go through
  `_parse_struct_array`, the SoA path — one shared member-presence bitmap,
  then each present member serialized as its own column (special-cased for
  byte arrays/tuples, which are read row-by-row instead of columnar).

### Spline-compressed animation decompression

`SplineAnimation` decodes an `hkaSplineCompressedAnimation` object's `data`
byte blob lazily, block by block (`_parse_block`), where each block covers
a contiguous span of frames and holds, per transform track, a 4-byte
`TransformMask` (quantization type nibbles for position/rotation/scale)
followed by either a static value or a NURBS spline (degree ≤3, `uint8`
knots) per channel:

- **Position/scale tracks** (`_parse_vec_track`): per-axis, either fully
  static (one `float32`), a spline (quantized 8-bit or 16-bit control
  points scaled between stored min/max extremes), or a constant default
  (0.0 for position, 1.0 for scale) if the axis carries no data at all.
- **Rotation tracks** (`_QuatTrack`): identity, static, or spline, using
  one of several **quaternion quantization formats** decoded by
  `_read_quat`: `QT_32bit` (`_read_quat32` — a packed radius + spherical
  phi/theta encoding with 4 sign bits), `QT_40bit`/`QT_48bit`
  (`_read_quat40`/`_read_quat48` — 3 explicit components plus a 2-bit
  "which component was omitted" field and a sign bit, reconstructed via
  `_place_missing`: `w = sqrt(1 - a² - b² - c²) · wsign`), or raw
  uncompressed `float32×4`.
- Spline evaluation (`_eval_scalar`/`_eval_quat`) uses a De Boor
  basis-function evaluation (`_basis`, `_find_knot_span`) — standard NURBS
  curve evaluation, not a Catmull-Rom or other alternative.

`sample_time(t)` maps a clock time to `(block, local spline parameter u)`
per Havok's `hkaSplineCompressedAnimation::getBlockAndTime` semantics: block
index = `time * block_inv_duration` (clamped), and `u` is the local block
time scaled into frame units via `max_frames_per_block`. `sample_frame(f)`
is a thin wrapper converting an integer frame to its corresponding time.
`decode_clip` samples every integer frame `0..numFrames-1` and transposes
the result from `[frame][track]` to `[track][frame]` for the final output.

## Gotchas & lessons

- **"Packfile" in the API is a misnomer kept for history.** `parse_packfile`
  operates on a tagfile, not a Havok packfile — LOTRO's `0x05` records
  looked like they might be packfiles before investigation, and the name
  stuck even after the format was correctly identified as a tagfile. Don't
  be misled by the function name when reading or extending this code.
- **Unsupported tagfile version or top-level tag raises immediately** — the
  parser only understands versions 3 and 4, and only the 7 known top-level
  tags; anything else is a hard `ValueError`, by design (silently skipping
  an unrecognized tag would desync the stream irrecoverably, since tags
  have no fixed length prefix at this level).
- **Quaternion quantization has 4 distinct wire formats** (32/40/48-bit
  packed, plus uncompressed), each with its own bit-packing scheme and its
  own "missing component" reconstruction rule for the 40/48-bit forms —
  get the wrong `qtype` mapped to the wrong reader and the resulting
  rotations will be subtly wrong (not obviously garbage), so any change
  here should be validated against a known-good clip's rendered animation,
  not just against the numeric ranges.
- **`decode_clip` only handles spline-compressed clips.** Interleaved
  uncompressed animations are recognized by `find_objects`/`parse_packfile`
  but `decode_clip` explicitly raises `NotImplementedError` if the found
  animation object's class isn't `hkaSplineCompressedAnimation` — a real
  gap if a future clip DID turns out to use the uncompressed format.

## See also

- [../animation.md](../animation.md) — full skeleton/clip format writeup, walk-cycle identification.
- [export_skinned.py](export_skinned.md) — consumes `decode_clip` and `parse_tagfile` (for skeletons).
- [datfile.py](datfile.md) — `read_content`, how the raw `0x05` bytes are fetched (via `config.anim()`).
- [INDEX.md](INDEX.md) — full script index.
