# Known Limitations and Open Problems

This page is deliberately the most pessimistic one in the set. The other
pages document what has been proven; this one documents what has not,
including cases where a "solved" claim made it into project notes before
being caught and corrected. Read this page before trusting any "works" or
"solved" language elsewhere in this documentation — each item below links to
where the fuller detail lives.

## A recurring failure mode: overclaiming from too little evidence

This is the single highest-value lesson from this project, worth stating
before the specific gap list: more than once, a decoder or selector passed
every automated numeric sanity check while still being wrong in a way only
visible by actually looking at the rendered result.

1. **The unit-normal stride bug** (see
   [mesh-format.md](mesh-format.md#validation-trap-the-self-fulfilling-unit-normal-check)).
   A decoder that inferred vertex structure by scanning for "the next thing
   that looks like a unit normal" passed every count-based and range-based
   check — vertex/triangle counts matched, all indices were in range, zero
   degenerate triangles — while still producing visibly wrong geometry: thin
   spike triangles ("slivers") fanning across the whole mesh, invisible to
   any check that doesn't examine actual triangle shapes. Caught only by
   looking at a wireframe render.
2. **An appearance-key selector that "worked" by coincidence** (see
   [wardrobe.md](wardrobe.md#selecting-an-entry-item_appearancekey)). An
   early selector implementation correctly resolved one test item's
   appearance key purely by luck — a misread constant field happened to
   equal the correct value for that one item — and failed outright on a
   second, independent item. Caught only once a second example was tried;
   the project's standing rule since then is to require at least two
   independently verified examples before calling any selector claim
   proven.
3. **A part-selection bug that resolved the right material but the wrong
   mesh** (see [wardrobe.md](wardrobe.md)). A corrected selector picked the
   right *material* (a field that directly follows the lookup key) but the
   wrong *mesh list* — it silently returned a neighboring wardrobe entry's
   parts, most often the item's **hands** part rather than its garment,
   because of a genuine off-by-one in where an entry's part list was
   believed to start relative to its key. This produced renders that looked
   plausible (a render described at the time as "a wig" or "corset straps"
   was, in fact, the hands part rendered alone) for an extended period,
   because neighboring wardrobe entries frequently share the same rough
   shape-class of geometry. Caught only by deliberately auditing arbitrary
   items instead of re-confirming the same one or two already-checked
   examples.
4. **Phantom "absent" garment meshes** that were actually present stub
   meshes for a different, non-garment slot, misidentified because the
   part-selection bug above was looking at the wrong part in the first
   place — not an independent data gap, a downstream symptom of item 3.
5. **A "UV decode bug" that was actually a wrong-texture bug** (see
   [mesh-format.md](mesh-format.md#uv-values-outside-01-are-normal-not-corruption)).
   Negative-U and out-of-`[0,1]`-range UV values on two particular meshes
   were reported as a probable stride-detection bug reading UV data from
   the wrong offset. An exhaustive re-investigation found the mesh decoder
   correct on every count — UV really is always at byte offset 24, and
   `u` values outside `[0,1]` are a normal, deliberate texture-tiling
   convention seen on the project's own already-validated meshes. The
   actual cause of the visibly "scrambled" render was an unrelated, wrong
   texture being force-applied to a mesh whose UV layout didn't match it —
   a texture-binding bug with nothing to do with mesh decode.

The standing rule that came out of all five: **verify visually, on at least
two independent examples, and assume the appearance/mesh data is more
layered (hair, body, garment, and attachment meshes as separate composited
entries) than the simplest reading of the format suggests.** Numeric
validation (counts in range, no degenerate triangles, weights summing to
1.0) is necessary but never sufficient on its own.

## Open problems by area

### Mesh decode
- Tiny 3-vertex sprite/billboard/stub meshes have zero-length normals and
  fail the stride detector; not specially handled (they are correctly
  identifiable by their small on-disk size, ~200–350 bytes).
- Some very large, many-surface meshes need extra handling not yet
  implemented.
See [mesh-format.md](mesh-format.md#known-gaps).

### Item → body → garment selection
- **The `0x20` record grammar has 11 unparsed records** (out of ~4,200)
  sharing a further sub-variant not yet derived, where the word following an
  entry's key looks like a mesh DID rather than a block count.
- **Several fields in the wardrobe record grammar have no confirmed
  meaning**: the section-type byte's exact per-family semantics (which
  values correspond to which garment category is unnamed); the material
  group's `x` field (sometimes a shader-instance DID) and its `(A, B)` pair;
  the block-level `d` field; and the trailing type-1 key table's value
  column.
- **Which `0x20` record family serves which item slot** is not fully
  mapped — different item categories resolve through structurally distinct
  record families (confirmed for chest/dress, hats, legs, and head slots
  specifically; the general mapping from item category to record family is
  not yet derived for every slot).
- **Species codes beyond the confirmed five** (see [wardrobe.md](wardrobe.md#species--race-mapping))
  are inferred by elimination against the game's playable-race list rather
  than read from an authoritative source, and one code in particular has no
  plausible mapping at all. This mapping was derived from a small sample of
  items and should not be treated as generalized without a wider sweep
  against the game's own species enumeration data.
- **The human-body (Man) data hole** is real for specific garments within
  specific item families (see [wardrobe.md](wardrobe.md#the-humanman-body-a-genuine-data-hole-for-some-garments)),
  confirmed exhaustively for the examples checked, but the general
  *pattern* of which garments across the whole catalogue have this hole and
  which don't has not been swept broadly.
- **`0x01` "Setup" assembly records** (placement matrices tying skeletons,
  part meshes, and materials into composite bodies/props) are structurally
  identified but not parsed. Lower priority than it once was, since the
  skeleton and a default face/hair/beard are now reachable through the
  chargen (`0x47`) and skeleton (`0x04`) records directly, and held-item
  geometry through the separate `PhysObj` chain (see
  [weapons.md](weapons.md)) — what `0x01` records might still add is prop
  *placement* (the real drawn/sheathed weapon attach transforms, still
  hand-tuned — see below) and possibly a real per-body skin atlas.

### Weapons and held items
Previously an open area (no geometry pipeline at all for weapons, shields,
or class items); now **solved for the core chain** — see
[weapons.md](weapons.md) for the full writeup, verified end to end on 9
held items across two characters' outfits. What remains open:
- **Real attach transforms are not read from the client files.** The
  bone/rotation/offset used per attachment point is hand-tuned against a
  rendered T-pose, not decoded game data — see
  [weapons.md](weapons.md#open-gaps).
- **Sheathed vs. drawn is not modelled.** A held item always renders at
  one fixed, user-chosen attachment point; there's no automatic
  switch between a stowed and an in-combat pose.
- **Handedness/mirroring is verified for the specific weapons tested
  (axes), not swept across weapon shapes generally** — an asymmetrically
  modelled weapon could still look wrong when the same mesh is mirrored
  onto the off-hand bone.
- **Dyeable weapon/shield properties (the `0x1F` template's alternate
  38-byte, parent+override form) are unimplemented** — this is presumed
  to be the path shields need, since they dye like armour rather than
  rendering as a fixed-material weapon, but it hasn't been built or
  tested.
- **One part-mesh type in a multi-mesh skeleton trailer — a shadow- or
  billboard-style record — is not decodable** by `mesh_decode.py`; an
  item whose trailer includes one of these renders with that part
  missing.
- **Auras are deliberately not rendered** (they resolve fine through the
  same chain, but a tiny fx-mesh prop isn't worth showing standalone) —
  a scope decision, not a resolution failure.

### Textures and materials
- Whether the skin/cloth surface-DID routing rule (see
  [textures.md](textures.md#skin-vs-cloth-surface-routing)) generalizes
  identically across every garment/body combination, or varies per body
  family, is not fully confirmed.
- A real per-body skin atlas (varying correctly by race, sex, and skin
  tone) does not exist in the current rendering approach — skin is
  currently a small number of flat placeholder tones, not the game's actual
  per-race skin texture data.
- It's plausible that different meshes of nominally "the same" body type
  use subtly different regions or unwraps of what appears to be a shared
  diffuse (LOD variants, or per-race UV differences) — this would silently
  misplace texture detail in a way that doesn't look as obviously broken as
  a fully scrambled UV. Only spot-checked on a small number of mesh/texture
  pairs, not investigated in depth.
- Whether the 17-shader `0x2B` classification (see [shaders.md](shaders.md))
  is exhaustive across the whole item catalog, or more shaders turn up
  outside the one race/sex body it was sampled on, is unconfirmed. A
  specular/gloss-map hypothesis for why some plated armour sets read as
  "not shiny" is explicitly unverified — see
  [shaders.md](shaders.md#open).

### Sibling items / sleeve variants
- The open texture-binding problem where one shared material has more than
  one plausible candidate diffuse depending on which sibling mesh it's
  applied to is unresolved — see
  [wardrobe.md](wardrobe.md#sibling-items-and-sleeve-variants).
- Whether a separate skirt part-mesh is meant to be drawn alongside
  upper-body-only "modular" sibling meshes, or whether those items are
  meant to be worn over a base-body skirt, is unconfirmed.
- A specific reference-image color/pattern mismatch against one real
  in-game garment screenshot has not been investigated further; the render
  is internally coherent (not scrambled), so this reads as an
  un-investigated recolor or content-reissue variant rather than a
  rendering bug, but this has not been confirmed.

### Dyes
- **16 of 45 named dye RGB values are still missing** — all festival/event
  dyes not listed on the wiki's main catalogue page, requiring individual
  per-dye page scraping. See [dyes.md](dyes.md#dye-palette-rgb-values).
- The client's own floatCode → color ground-truth table (if a discrete
  table exists at all, versus being computed purely in-shader from the
  scalar) has not been located; all extracted dye RGB values are sourced
  from wiki-rendered swatches, not from the client's own data or math.
- The v1 render approach tints the entire alpha-masked cloth region
  uniformly; items that ship genuine per-dye wardrobe material variants
  (see [wardrobe.md](wardrobe.md#dye-variants-come-for-free)) should
  eventually switch texture per dye choice rather than tinting at render
  time.
- A secondary dye channel (for two-tone dyeable items) is not distinguished
  from the single alpha-mask model currently used.
- Skin-tone selection uses the same tint mechanism against an unconfirmed
  sampler/region, and currently offers a small number of preset tones
  rather than the game's actual per-race skin palette.

### Hair, face, beard
- Hair-color tinting and the face compositor's layered cosmetic system
  (eyebrows, complexion, mouth, war-paint layers, eye iris colour) are not
  implemented — no script in this repository composes chargen head/hair
  geometry at all. **Resolved separately**: which materials need an alpha
  *cutout* versus an alpha *tint mask* — previously stated as a blanket
  face/hair rule — is now known to be a per-shader property, not a
  per-body-part one; see [shaders.md](shaders.md) and [hair-face.md](hair-face.md).
- The Man-vs-Elf chargen table split is inferred (body scale plus
  beard-slot presence), not confirmed against the live character-creation
  screen.
- A handful of chargen records of unclear identity remain unidentified (an
  extra hobbit-height style set of unknown purpose; an untested facial-hair
  file for one body/sex combination).

### Animation and rigging
- `hkaInterleavedUncompressedAnimation` clips (a meaningful fraction of the
  clip archive) are not decoded — the container parses, track extraction
  does not.
- Quaternion quantization paths other than the one combination exercised by
  every real clip examined so far are implemented but unverified against
  actual data.
- Float animation tracks (a track type distinct from bone transform tracks)
  are skipped entirely.
- The per-submesh bone-palette region (distinct from per-vertex bone
  indices, which are known to index the skeleton directly) does not parse
  reliably on all blocks — not currently load-bearing, but a genuine
  unresolved detail of the mesh format.
- Clip-to-rig binding is confirmed for player and some NPC rigs but
  unconfirmed for the broader population of distinct monster skeletons.
- Whether cloth "physics" bones (cape, skirt chains) receive authored
  secondary motion from clip data directly, or require a runtime physics
  simulation layered on top, is unconfirmed — meaning even fully correct
  clip playback may still render a stiff, non-swinging cloak or skirt.
- Gait/pose classification (identifying which clip is "the" walk, run, or a
  given directional variant) is a heuristic, confidence-scored classifier
  based on ground-contact duty factor, independently verified by eye for
  only one rig's walk/run family so far — other rigs' equivalent clips are
  extrapolated by the same method without independent visual confirmation.
- Emote-name-to-clip resolution via a serial-number-reuse heuristic only
  resolves a minority of emote commands, and most of what it does resolve
  targets monster rigs rather than player rigs — the real linkage mechanism
  for the remainder is presumed to be a proper typed property reference,
  not yet implemented.
- Only a small fraction of all skeleton records yielded usable name tokens
  from a bone-name scan; most are small, unnamed prop/effect rigs. Any
  claim about "all rigs" derived from name-token scanning alone is
  unfounded — bulk conclusions should be drawn from the full parsed
  skeleton set, not from name matches.

## See also
- [overview.md](overview.md) — the honest status summary this page expands on
- [wardrobe.md](wardrobe.md) — the selector this page's off-by-one history lives downstream of
- [weapons.md](weapons.md) — the held-item chain and its remaining gaps in detail
- [mesh-format.md](mesh-format.md) — the sliver bug and decoder gaps
- [textures.md](textures.md) — texture-atlas subtleties still unresolved
- [shaders.md](shaders.md) — `0x2B` shader classification: what's resolved, and what's still open
- [dyes.md](dyes.md) — dye-palette and picker gaps
- [animation.md](animation.md) — rigging gaps in detail
