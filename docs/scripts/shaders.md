# shaders.py

[`shaders.py`](../../shaders.py)

## Purpose

`shaders.py` classifies `0x2B` shader records — the record a `0x31` surface
names alongside its `0x30` material (see [tex_extract.py](tex_extract.md)'s
compact-surface parse) — into a small set of named render behaviors:
whether the surface's diffuse alpha channel is an **alpha-test cutout** or a
**tint/dye mask**, and whether the surface is **dyeable** and/or
**metallic**. See [../shaders.md](../shaders.md) for the full format
writeup and the worked face-vs-hair example this resolves.

## CLI usage

```
python3 shaders.py [--game-dir DIR]
```

Prints the classified shader table (name, alpha-test, dyeable, metallic,
note), sorted alpha-tested-first then by name.

## Public API

| Function | Signature | Returns |
|---|---|---|
| `info` | `info(shader_did)` | `(name, alpha_tested, dyeable, metallic, note)` for a shader DID — from the `SHADERS` table if known, else auto-classified |
| `is_alpha_tested` | `is_alpha_tested(shader_did)` | `bool` — shorthand for `info(shader_did)[1]` |
| `surface_shader` | `surface_shader(surf_did)` | the `0x2B` shader DID a `0x31` surface binds, or `None` |
| `surface_alpha_tested` | `surface_alpha_tested(surf_did)` | `bool` — resolves a surface straight to its alpha-test flag in one call |

`SHADERS` (module-level dict): `{shader_did: (name, alpha_tested, dyeable,
metallic, note)}` for the 17 shaders classified so far — see
[../shaders.md](../shaders.md#the-17-shaders) for the full table.

## How it works internally

- `surface_shader` reads a `0x31` surface's true (22-byte) content directly
  — `[self DID][shader 0x2B DID][slot key][nMaterials][material 0x30
  DID][u16]` — and returns the shader DID from byte offset 4, the same
  compact layout [textures.md](../textures.md) documents.
- `info` looks a shader DID up in the hardcoded `SHADERS` table first. For a
  DID not in that table, it fetches the shader's raw ~1&nbsp;MB compiled
  blob and counts occurrences of `c_AlphaTestThreshold`, `c_MaterialDyeColor`,
  and `c_SpecularMetallicAmount` in it (`re.findall`), applying the same
  presence thresholds used to build the table (roughly: alpha-tested if
  `c_AlphaTestThreshold` occurs at least half as often as it does in a known
  alpha-tested shader). Unknown-shader results are cached in-process
  (`_UNKNOWN_CACHE`) since re-scanning a ~1&nbsp;MB blob per call would be
  wasteful for a hot render path.
- `ALPHA_TEST_COUNT = 244` is the reference "compiled in" occurrence count
  for `c_AlphaTestThreshold`, calibrated against the sampled shader set (see
  [../shaders.md](../shaders.md#how-the-shaders-were-classified)).

## The classification method

Each `0x2B` record's compiled shader blob has no name field, but its string
table lists the HLSL uniforms it references, and counting a uniform's
occurrences across the blob is strongly bimodal: a feature is either barely
referenced (compiled out of that shader variant, low count) or referenced
throughout every code path (compiled in, high count) — with a wide gap
between the two populations. That gap is what makes a simple threshold on
raw occurrence counts a reliable classifier without per-shader tuning; see
[../shaders.md](../shaders.md#how-the-shaders-were-classified) for the
measured counts.

## Gotchas & lessons

- **The 17-name table is derived, not authoritative.** Names like
  `cloth_dyed` and `cutout_hair` are assigned by this project from the
  observed feature bits — the game ships no shader names anywhere in the
  record. Treat the names as a convenience label, and the boolean flags
  (`alpha_tested`/`dyeable`/`metallic`) as the actual data.
- **`info()` on an unseen shader DID does not fail** — it auto-classifies
  using the same uniform-count method, returning a `unknown_0x%08X` name.
  This means a shader outside the sampled set still gets usable render
  hints, but those hints are unverified for that specific DID (only the
  *method* was validated, on the 17 sampled shaders).
- **`surface_shader`/`info` both try `client_surface.dat` then
  `client_general.dat`** (via `tex_extract._surf()`/`tex_extract._gen()`)
  and swallow per-archive lookup failures — a shader or surface DID that
  exists in neither archive silently returns `None` rather than raising.
- **This module only decides alpha semantics and material response
  hints — it does not itself change how anything renders.** `compose.py`
  attaches its output to each submesh group (see
  [compose.md](compose.md#public-api)); a renderer has to actually read
  `alpha_test`/`metallic` and act on them. As of this writing, this
  repository's own viewer (`index.html`) does not yet do so — see
  [viewer.md](viewer.md).

## See also

- [../shaders.md](../shaders.md) — the full format writeup, the classification method, and the worked reconciliation example
- [tex_extract.py](tex_extract.md) — the compact 22-byte surface parse `surface_shader` reads
- [compose.py](compose.md) — exports `shaders.info()`'s output per submesh via `_shader_fields`
- [../textures.md](../textures.md) — the material chain a shader sits in
- [../dyes.md](../dyes.md) — the tint-mask convention for non-alpha-tested shaders
- [INDEX.md](INDEX.md) — full script index
