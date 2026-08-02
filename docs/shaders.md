# Shaders (`0x2B`) — Cutout vs. Tint Mask

The missing piece in the material chain (see [textures.md](textures.md)): a
mesh submesh's `0x31` surface names a `0x2B` **shader** record alongside its
`0x30` material. The shader is not inert bookkeeping — **the shader, not the
texture, decides how the diffuse's alpha channel is read**:

- alpha-tested shader → alpha is a **CUTOUT** (hair strands, filigree,
  straps — pixels below a threshold are punched out, not blended)
- everything else → alpha is a **TINT/DYE MASK** and the surface renders
  opaque (the convention documented in [dyes.md](dyes.md) and
  [hair-face.md](hair-face.md))

Reference implementation: [`shaders.py`](scripts/shaders.md). Render hints
are exported per submesh by `compose._shader_fields`, using the same helper
for garments and chargen parts alike.

## Why this matters: a contradiction it reconciles

A chargen face atlas measures **0%** high-alpha across the face proper and
45%/38% on the eye/eyebrow patches (see [hair-face.md](hair-face.md)) —
clearly a tint mask — and its head mesh binds shader `0x2B0009DE`, which
classifies **opaque** (not alpha-tested). The **hair** of the same character
binds `0x2B0009DF` / `0x2B0009B7`, both **alpha-tested** — which is exactly
why hair rendered with blocky, dark opaque patches when the cutout was
ignored and alpha was blended (or ignored) instead of tested. A circlet-style
headpiece that motivated this investigation binds `0x2B000749`, also
alpha-tested. All five surfaces above were spot-checked against extracted
data and land where the classification predicts.

This is the general form of a caveat that appears elsewhere in this
documentation as "texture alpha is a tint mask, not opacity": that statement
was never true for *every* surface, only for surfaces bound to a
non-alpha-tested shader — and there was no way to tell those apart until the
shader itself was classified.

## How the shaders were classified

Each `0x2B` record is a roughly 1&nbsp;MB compiled HLSL blob with **no name
field**, but its string table lists the uniforms it references. Counting how
often a uniform name occurs across the blob is strongly **bimodal** for the
uniforms that matter — a feature is either barely referenced (compiled out
of that shader variant) or referenced throughout every code path:

| uniform | absent (low count) | present (high count) |
|---|---|---|
| `c_AlphaTestThreshold` | 8 | 244 (alpha-test compiled in) |
| `c_MaterialDyeColor` | 0 | 232 of 380 samples (dyeable) |
| `c_SpecularMetallicAmount` | 0 | 224 of 228 samples (metallic) |
| `c_MaterialSpecularColor` | 0 | 28 (specular) |

The gap between "absent" and "present" for each uniform is wide enough that
a simple presence test (occurrence count over roughly half the "present"
mode's count) reliably separates the two populations — no fitting or
per-shader tuning is needed.

`shaders.info()` classifies an **unseen** shader DID the same way: it reads
the shader's blob and applies the same count thresholds, rather than failing
on it — so the classification method generalizes past the sampled set of
shaders below, at the cost of not being independently verified for any
shader outside that set.

Sampled over several thousand wearable entries on one race/sex body: **16
distinct shaders**, plus one (`0x2B0009B7`) reachable only through chargen
hair — **17 named, 8 of them alpha-tested**. The names in the table below
are assigned by this project (`cloth_dyed`, `skin`, `metal_dyed`,
`cutout_hair`, …), derived from the observed feature bits — the game ships
no shader names anywhere in the record.

## The 17 shaders

| DID | name | alpha | dye | metal | note |
|---|---|---|---|---|---|
| `0x2B0007A0` | `cloth_dyed` | opaque | yes | no | the workhorse garment cloth, most-used by far |
| `0x2B0009DE` | `skin` | opaque | yes | no | body + chargen face; alpha here is the tint mask |
| `0x2B0009DA` | `cloth_dyed_alt` | opaque | yes | no | second opaque dyed cloth variant |
| `0x2B000A20` | `metal_dyed` | opaque | yes | yes | armour; extra dye variants (380 sampled) |
| `0x2B0006EB` | `cloth_plain` | opaque | no | no | opaque, undyeable |
| `0x2B0007D5` | `cloak_plain` | opaque | no | no | no specular colour; mostly the Back slot |
| `0x2B000712` | `cloth_flat` | opaque | yes | no | dyed but no specular colour |
| `0x2B000A22` | `metal_dyed_alt` | opaque | yes | yes | opaque metallic variant |
| `0x2B00081B` | `metal_dyed_alt2` | opaque | yes | yes | opaque metallic variant |
| `0x2B000749` | `cutout_dyed` | **ALPHA-TEST** | yes | no | a circlet/wreath-style headpiece and kin |
| `0x2B0009DB` | `cutout_dyed_alt` | **ALPHA-TEST** | yes | no | |
| `0x2B000788` | `cutout_dyed_alt2` | **ALPHA-TEST** | yes | no | |
| `0x2B0009DF` | `cutout_hair` | **ALPHA-TEST** | yes | no | chargen hair; reduced specular power (30) |
| `0x2B0009B7` | `cutout_hair_plain` | **ALPHA-TEST** | no | no | chargen hair, undyeable |
| `0x2B0007A6` | `cutout_plain` | **ALPHA-TEST** | no | no | no dye, no specular colour |
| `0x2B000A1F` | `metal_cutout_dyed` | **ALPHA-TEST** | yes | yes | pierced/filigree metal |
| `0x2B000A21` | `metal_cutout_dyed_alt` | **ALPHA-TEST** | yes | yes | |

`surface_shader(surf_did)` reads the shader DID straight out of the 22-byte
compact surface record (see [textures.md](textures.md)'s "`0x31` surfaces
are ~22 bytes" section); `surface_alpha_tested(surf_did)` is the one-call
hook a renderer needs to decide cutout vs. tint mask per submesh.

## What's exported, and what a renderer should do with it

`compose._shader_fields(surface)` (see [scripts/compose.md](scripts/compose.md))
attaches `{shader, shader_did, alpha_test, metallic, dyeable}` to every
emitted submesh group. A renderer consuming this should:

- `alpha_test` → treat the diffuse's alpha as a **cutout** (e.g. three.js
  `material.alphaTest = 0.5`, `transparent = false`) rather than as opacity
  blending — a cutout avoids depth-sort artefacts and works from both faces,
  which alpha blending does not.
- `metallic` → raise metalness / lower roughness for a metal material
  response instead of the flat cloth default.
- Key materials by `(texture, shader, alpha_test, metallic)`, not by texture
  alone — two submeshes can share a texture but need different render
  modes, and collapsing them onto one material silently drops that
  distinction.

See [scripts/viewer.md](scripts/viewer.md) for the current state of this
repository's own viewer: the fields above are exported by `compose.py`, but
`index.html` does not yet read them.

## Open

- Whether the 17-shader set is exhaustive across the whole item catalog
  (sampled on one race/sex body's entries only) or more shaders turn up on
  other bodies/slots is unconfirmed.
- `c_MaterialSpecularColor` (specular-highlight strength) is decoded but not
  yet fed into any render path in this repository.
- **Forged/Engraved-style plated armour "not shiny" — unconfirmed
  hypothesis.** These sets classify `cloth_dyed`/`cloth_dyed_alt`/
  `cutout_dyed_alt` (see the table above) — **not metallic** — so a renderer
  using this classification correctly renders them matte; that part is
  certain, not a bug. Separately, their diffuse alpha runs near-opaque (mean
  ~247 on a sampled plated-hauberk texture — see [dyes.md](dyes.md)), too
  high to be doing dye-mask work. A gloss/specular map living in an alpha
  channel, or in a further material slot, is a plausible *alternative*
  reading of that high alpha, but this has **not been verified**.
  `tex_extract.material_diffuse` currently reads only the first `0x40` slot
  of a `0x30` material record; checking later slots of a plated-armour
  material against a cloth material's slots for a hidden specular/gloss map
  is a concrete next step, not yet done.
- The face compositor layers (eyebrows/complexion/mouth/scars/war paint) and
  eye iris colour are a separate, still-open problem (see
  [hair-face.md](hair-face.md)) — shader classification only fixed *how
  alpha is read*, not what pixels are authored into a texture.

## See also
- [textures.md](textures.md) — the surface → material → texture chain this plugs into, including the 22-byte compact surface format
- [hair-face.md](hair-face.md) — the face/hair tint-mask measurement this reconciles
- [dyes.md](dyes.md) — the tint-mask convention shared with garment dye, and the plated-armour dye-mask measurement the shine hypothesis builds on
- [mesh-format.md](mesh-format.md) — the mesh submesh a surface (and therefore a shader) is bound to
- [scripts/shaders.md](scripts/shaders.md) — the reference implementation
- [limitations.md](limitations.md) — open items across the whole project
