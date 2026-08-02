# Textures and the Material Chain

Two separate problems: (1) decode a `0x41` texture record to pixels, and
(2) walk from a mesh, or from an item's wardrobe binding, to the *correct*
`0x41` record — there are several plausible-but-wrong chains here, documented
below as dead ends so they aren't retried. Reference implementation:
[`tex_extract.py`](scripts/tex_extract.md).

## `0x41` texture record format

Records live in `client_surface.dat` and `client_highres.dat` (the same DAT
container as everything else — check both archives, surface first).

After `read_asset` decompresses the record:

1. Locate the ASCII fourcc `DXT1` / `DXT3` / `DXT5` inside the decompressed
   bytes.
2. The **8 bytes immediately before** the fourcc are `width, height`
   (`uint32` little-endian each).
3. Compressed block data starts at **`fourcc_offset + 8`** (4 fourcc bytes
   plus one trailing `uint32` — apparently a version/flags dword not
   otherwise used).
4. **Validate by exact expected size before accepting the match**: DXT1 size
   is `(w/4)*(h/4)*8` bytes; DXT3/DXT5 size is `(w/4)*(h/4)*16` bytes. This
   size check is load-bearing — it rejects fourcc-*looking* byte runs
   elsewhere in the record (compiled shader blobs are full of coincidental
   4-byte matches that happen to spell `DXT1` etc.).

Two record header variants exist upstream of the fourcc (one carries a
type/count dword before width/height, the other carries the record's own
self-DID there) — irrelevant to the decoder, since it anchors on the fourcc
position rather than the record start.

**Decode**: wrap the exact compressed block bytes in a minimal 128-byte DDS
header (`DDS `, size 124, `CAPS|HEIGHT|WIDTH|PIXELFORMAT|LINEARSIZE` flags,
the fourcc, and a plain `CAPS` block) and hand the result to any DXT-capable
image decoder (e.g. Pillow via `Image.open`).

**Placeholders**: small DXT tiles (≤8 px on a side, typically 4×4) are
unassigned-slot stand-ins, not real diffuse data. A texture is a
placeholder if `width <= 8 or height <= 8`; a real garment diffuse is
defined as **≥64 px** on its longest side.

## Material chain: mesh → surface → shader/material → texture

A mesh's own header lists one `0x31` **surface** DID per submesh (see
[mesh-format.md](mesh-format.md)). From there:

```
mesh (0x06) -> per-submesh surface (0x31, ~22 bytes true content — see below)
  -> shader instance (0x2B, compiled HLSL, constant tables incl. a
     color/dye sampler) + material (0x30)
     -> texture-slot mip-chains (0x40), each an ordered list of
        0x41 DIDs at successive resolutions; the FIRST 0x40 slot is
        the diffuse
```

Records at this level are unaligned `PropertiesSet`-family structures (see
[properties.md](properties.md) for the *typed* parse used for item
properties); the material graph is walked by exact per-record-type parsing
where the true content is known, and by bounded brute-force scanning
otherwise (every `uint32` value whose high byte matches a target record type
and whose low 24 bits resolve via a directory lookup in the right archive is
kept as a candidate — garbage doesn't resolve, because DIDs are sparse).

### `0x31` surfaces are ~22 bytes true content — a major over-read trap

A long-standing reading of `0x31` surface records as "~1076 bytes, carrying
two shader passes" was **wrong**, and the error explains a whole family of
downstream mis-resolutions. The true content of a surface record, read via
the exact block-chain reader (not the mesh/texture `read_asset` path — see
[dat-format.md](dat-format.md)), is:

```
[self DID][shader 0x2B DID][slot key 0x10000050][u32 nMaterials=1]
[material 0x30 DID][u16 1]
```

— **22 bytes**, not ~1076. The larger reads seen previously were
`read_asset` over-reading an entire DAT block; everything past byte 22 was
**neighbor records** (other surfaces, materials, even fragments of wardrobe
entries) — i.e. garbage that happened to parse as plausible-looking
structure. Any diffuse-resolution logic that scans "the surface's local
graph" for the largest resolvable texture will, on the over-read bytes, pick
up an unrelated neighboring item's texture. The fix is to read surfaces (and
materials, and `0x40` mip-chain slots) via the exact block-chain reader, not
`read_asset`, whenever exact byte layout is known.

**Material true format** (verified example, 200 bytes): a slot list of
`{u32 slotId, u32 slotId2, u32 type=4, 0x40 DID, params...}`; the **first**
`0x40` slot is the diffuse, as established below.

### Full-body diffuse via the appearance record

A mesh's own surface diffuse (reached by walking the mesh's own `0x31`
surface → material → texture chain) is only that mesh's **default/fallback**
texture — frequently flat, near-uniform gray, not a real tintable base. The
real per-item, full-garment texture comes from the **worn-appearance `0x20`
record's draw-entry material** (see [wardrobe.md](wardrobe.md) for how that
material is selected) via the same `0x30 → 0x40 → 0x41` chain, but bound at
the *outfit* level rather than through the mesh's own material graph:

```
appearance 0x20 -> draw entry -> material (0x30) -> diffuse resolver -> 0x41 DXT -> pixels
```

Because all body-part meshes of one body type share a single UV layout, one
full-body texture maps correctly onto multiple distinct meshes of that body
(e.g. an upper-body mesh and a separate torso mesh both wearing the same
material) — confirmed by rendering both with the same texture and checking
the seams and pattern alignment by eye.

### Newest-generation items: zero-material-group wardrobe entries

Some newer items' `0x20` wardrobe entries carry **zero material groups** for
their part (see [wardrobe.md](wardrobe.md) for the entry grammar). These
still resolve correctly through the **standard chain**: the mesh's own
`0x31` surface binds the item's material *directly* — there is nothing
special-cased about newer items, it is the same 22-byte surface → material →
first-mip-slot chain described above. An initial investigation into these
items' diffuse resolution wrongly concluded a new binding mechanism was
needed, purely because the 22-byte-vs-1076-byte over-read bug (above) was
still active at the time and caused the surface reader to pick up a
neighboring item's texture. Once the surface reader was fixed to use exact
block-chain content, the zero-group items resolved correctly with no new
code path.

## Dead ends: heuristics that looked plausible but picked the wrong texture

Recorded so they are not retried.

- **"Largest texture across ALL mip-chain slots of a material."** This picks
  up a **shared prop/normal atlas** used across many unrelated materials
  (because that shared atlas happens to be the largest texture reachable
  from the material), instead of the garment's own diffuse. Fixed by taking
  the largest mip inside the **first** slot only (smallest byte offset in
  the slot list) — this is what the "first slot is the diffuse" rule above
  encodes, and it was re-verified against materials that previously
  mis-resolved to the shared atlas.
- **"Use the mesh's own surface material for a full-body item render."**
  This points at a **shared armor-texture atlas** (chainmail, gorgets,
  gauntlets, etc.), not the specific garment's diffuse. It resolves cleanly
  and deterministically — and is simply the wrong texture for a full-body
  item render. It is, however, the *correct* texture to use as a per-mesh
  default/fallback when no item-level material applies (see the
  zero-material-group case above).
- **"Two shader instances per submesh, pick the largest resolvable texture
  across both."** A dress submesh commonly references two `0x2B` shader
  instances — one carrying the diffuse, one carrying only small
  normal/gloss mask tiles. Resolving the whole local graph of both and
  keeping the *largest* non-placeholder texture reliably lands on the
  diffuse-bearing instance, because compiled shader blobs' coincidental
  fourcc-like hits tend to be small. This heuristic is retained as a
  fallback for cases where the exact 22-byte surface parse is unavailable.

## Skin vs. cloth surface routing

Garment meshes carry **exactly two alternating per-submesh surfaces**, and
these correspond to a skin/cloth split needed for correctly compositing a
dressed body:

- **The "skin" surface** covers arm and neckline geometry left bare by a
  given garment (with full hand/finger geometry on sleeveless variants,
  hands/neck only on long-sleeved ones). Its texture must come from the
  **body** (a skin-tone material), not from the item.
- **The "cloth" surface** is textured from the item's own material diffuse.

The specific surface DIDs used for skin vs. cloth appear to differ by body
type/family rather than being global constants — treat this as something to
re-derive per body rather than hardcode two universal values. A compositor
should merge a wardrobe entry's parts, deduplicate LOD (the same surface
appears at multiple LODs — keep the largest-vertex submesh per surface DID
to avoid z-fighting), and route each submesh's texture by its surface DID
rather than by any single "the" diffuse for the whole mesh.

This was visually verified across several sleeve-length variants of one
garment family: a sleeveless variant renders with bare skin arms and hands,
a short-sleeved variant shows puff sleeves over bare forearms, and a
long-sleeved variant shows full sleeves ending in skin hands — each matching
the expected in-game silhouette.

Open: whether a real per-body skin **atlas** (varying by race/sex/skin-tone)
exists and can be substituted for a flat placeholder skin color; see
[limitations.md](limitations.md).

## Rendering details

- **UV / V-flip**: DXT/DDS data is top-down. A three.js-style renderer
  should disable the texture's automatic Y-flip and instead choose the
  V-axis convention explicitly. **Raw V (no flip) is the visually confirmed
  correct orientation** for worn-appearance meshes (bodice lands on the
  chest, skirt panels align correctly) — an earlier project note had this
  backwards; it is corrected here.
- **Z-up → Y-up**: see [mesh-format.md](mesh-format.md) — apply a fixed
  rotation at load time for a Y-up renderer.
- **Per-submesh material groups**: retain `tri_start`/`tri_count`/`texture`
  per submesh (see [mesh-format.md](mesh-format.md)'s `groups`) so a
  renderer can build one geometry group per submesh with its own
  material/texture binding.

## The `0x2B` shader decides cutout vs. tint mask

The `0x2B` shader a surface names (see "22 bytes, not ~1076" above) isn't
inert: counting occurrences of its compiled blob's uniform names classifies
it as alpha-tested (alpha = **cutout**, e.g. hair, filigree, straps) or not
(alpha = **tint mask**, the convention used above and in
[dyes.md](dyes.md)). 17 shaders are classified this way, 8 of them
alpha-tested, and the classification is exported per submesh
(`{shader, shader_did, alpha_test, metallic, dyeable}`) for a renderer to
consume. Full writeup, the classification method, and the worked
face-vs-hair reconciliation: **[shaders.md](shaders.md)**.

## See also
- [mesh-format.md](mesh-format.md) — the mesh geometry a texture is mapped onto
- [wardrobe.md](wardrobe.md) — how the correct material for a specific item is selected
- [dyes.md](dyes.md) — the dye tint applied on top of the diffuse's dyeable region
- [shaders.md](shaders.md) — the `0x2B` shader classification: cutout vs. tint mask, metallic
- [dat-format.md](dat-format.md) — DID type map (`0x40`/`0x41`/`0x30`/`0x31`/`0x2B`)
- [scripts/tex_extract.md](scripts/tex_extract.md) — the reference implementation
- [limitations.md](limitations.md) — texture-atlas subtleties still unresolved
