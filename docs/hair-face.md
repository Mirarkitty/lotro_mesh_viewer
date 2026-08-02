# Head, Hair, and Beard Selection (Chargen)

How hairstyles, faces/heads, and beards resolve to real meshes, per race and
sex. This covers two things: how head-slot **items** (helmets, hats, hoods)
interact with hair via the wardrobe format, and how a character's own
**chargen** choice of head/hair/beard resolves to actual geometry when no
item overrides it.

## The chargen chain

Hair, head (face), and beard are **character choices, not item data** — they
resolve through dedicated avatar chargen records, not the item pipeline
described in [wardrobe.md](wardrobe.md):

```
playable (race, sex)
  -> 0x47 avatar entity record (game-logic archive; parses with the same
     typed PropertiesSet reader as items, see properties.md, after
     skipping a 16-byte header: [u32 classTag][u32 selfDID][u32][u32],
     then the usual TSize count + properties)
  -> AppearanceUI_Controls : per-slot AppearanceUI_APRControl structs
       AppearanceUI_APRKey   = slot key   (head / hair / beard slot ids)
       AppearanceUI_APRFile  = a 0x20 record (general archive)
       AppearanceUI_ControlScriptPropertyName = the avatar property this
         control drives (e.g. the head-mesh property; a hair-style
         analog exists too)
  -> the 0x20 APR file has ONE entry (key = slot key) whose BLOCKS are the
     STYLES: block i has exactly one part {tag, mesh} -> one 0x06 GfxObj
```

The `0x20` grammar reused here is exactly the [standard wardrobe
grammar](wardrobe.md#the-record-grammar) — the insight specific to chargen
files is that the **block axis enumerates styles** (where in item records it
enumerates dye variants — see [dyes.md](dyes.md)). The block selector `q`
steps in the usual 0.00, 0.01, ... pattern with occasional larger jumps
(e.g. a `0.30x` jump marking a later style wave added to the game, `0.40x` a
still newer one); a companion field on the control
(`AppearanceUI_EndOldOptionsControlValue`) matches the pre-jump block count,
confirming that **style id and block ordinal are the same axis** here (`q`
is encoding a style index, not a dye code, in this record family). A tiny
(~200 byte) stub mesh used as a style entry means "none" (bald, for hair) —
the same stub mechanism used for hidden garment slots elsewhere.

Part tags observed in chargen files: head = one tag, hair = another (shared
with the wardrobe head-family's own hair tag — see below), beard = a third.
As throughout this format, **part tags are meaningful only within their
record family** — do not assume a tag value carries the same meaning across
different `0x20` record families.

### The player chargen table

Each playable race/sex combination has its own avatar entity record and its
own set of head/hair/(where applicable) beard style files, with varying
style counts per body (observed range: roughly a dozen to five dozen styles
per slot per body). Beard slots exist only on male Man and Dwarf bodies in
the confirmed data. Race identification for these avatar records is
**inferred, not name-resolved** — the records carry no direct race-name
property — from a combination of signals: body scale factor, head-mesh
Z-range (a rough proxy for character height), presence/absence of a beard
slot, DID ordering that matches the corresponding animation rig ordering
(see [animation.md](animation.md)), and visual inspection of a rendered head
against known race appearance. The Man-vs-Elf split in particular rests
mainly on the beard-slot signal and should be treated as
high-confidence-but-unverified until compared directly against the game's
own character-creation screen.

Each race/sex pair's chargen data exists in two parallel forms — an older
"toggle" file and a newer file with a slightly larger style count — both
parsing identically; the newer file simply carries additional styles added
since the older one was authored.

### Textures

Chargen style meshes carry **zero material groups** of their own — each
style mesh binds its diffuse through the same 22-byte surface → material
chain used everywhere else (see [textures.md](textures.md)). Hair diffuse
textures are **grayscale strand atlases**, tinted at runtime by a
character-hair-color property (the same style of tint math as dye — see
[dyes.md](dyes.md)). Head meshes carry a real skin-tone face diffuse with
eyes, brows, and lips painted directly into the texture.

In-game, the face is further composited from separate eyebrow/complexion/
mouth/war-paint layers via a dedicated compositor record family, driven by
per-character texture-selection properties. That compositing is **not**
implemented in this project's rendering path — only the base face diffuse is
rendered, which is visually complete (a real face) but not layered with
cosmetic customization on top.

⚠️ **Texture alpha is a tint mask on the face, but a cutout on hair — this
is a per-shader property, not a blanket face/hair rule.** An earlier version
of this page treated all face and hair materials as alpha-tint-mask-only,
because rendering with an alpha cutout enabled unconditionally punches out
facial skin and leaves floating eyes/mouth geometry. That was correct for
the *face* only by coincidence: [shaders.md](shaders.md) found that whether
alpha is a cutout or a tint mask is decided by the `0x2B` shader a surface
binds, not by which body part the surface belongs to. The chargen face
binds a non-alpha-tested shader (`0x2B0009DE`) — alpha there really is a
tint mask, exactly as before. Chargen hair binds alpha-tested shaders
(`0x2B0009DF`/`0x2B0009B7`) — its alpha genuinely is a cutout, and a
renderer that treats it as a tint mask (rendering hair fully opaque) gets
the accepted-but-suboptimal "blocky strand edges instead of soft cutout
edges" result this page previously described as a fixed limitation. See
[shaders.md](shaders.md) for the classification method and the full
alpha-tested shader table. This project's shader classification and its
per-submesh `alpha_test` export cover garment materials (via `compose.py` —
see [scripts/compose.md](scripts/compose.md)); chargen head/hair
composition itself, described below, is not implemented by any script in
this repository — see "Open" at the end of this page.

## Head-slot items (wardrobe side)

Head-slot items (helmets, hats, hoods) do **not** resolve through the same
`0x20` record family as body/chest garments — they resolve through their
own dedicated head-family record, with its own tag vocabulary. Observed tag
roles in one head-family record (734 entries, block-0 parts):

| tag | role |
|---|---|
| (one tag value) | the headpiece itself |
| (a second tag value) | **the hair slot** |
| (a third tag value) | unidentified — a small paired mesh at temple height (ears? under-helm hair tufts?), not guessed further |
| (remaining tags) | stubs only |

### Three hair states, controlled structurally, not by a flag

The hair slot of a head-slot item's `0x20` entry resolves to one of three
states:

| state | mechanism | result |
|---|---|---|
| open headwear | no hair-slot part present in the entry at all | hair slot untouched — the avatar's own chargen hair renders |
| closed helm | hair-slot part points at a tiny (~200 byte) stub mesh | hair is blanked (hidden) |
| baked replacement | hair-slot part points at a real mesh | a fitted "hair under this headwear" mesh renders instead of the avatar's own hairstyle |

⚠️ **There is no hide/suppress flag anywhere in item properties for this.**
A property-level search for hide/suppress/visibility flags across a closed
helmet vs. an open hat that shows hair found no such property — every
difference between the two items was economy/stat/icon metadata only. The
behavior lives **entirely** in which of the three states above the item's
`0x20` entry encodes. This is structurally identical to how a dress "hides"
legs by pointing that slot at a stub mesh rather than via any flag.

The non-stub hair mesh referenced by headwear-provided "fitted hair" does
**not** appear in any chargen hairstyle file — it is a dedicated
fitted/default "hair under a hat" mesh authored specifically for that
purpose, not one of the avatar's selectable chargen styles.

**Hair is a property of the body type, not the item** in the "avatar's own
hair" case: exactly one hair mesh per body serves as the default shown/
stub-hidden mesh in the wardrobe layer, distinct from the full chargen
style library described above, which only comes into play when explicitly
composing a chosen chargen hairstyle onto a character rather than relying on
the wardrobe layer's single default.

### Face: not in item data

No face/head mesh appears in any head-slot item's wardrobe entry — the face
belongs to the base body/chargen layer described above, never to item data.
An earlier approach to sourcing a renderable face cropped one out of
whole-body character meshes that happen to include head geometry baked in
with clothing; this is unnecessary once dedicated chargen head GfxObjs (see
above) are used directly, but is documented here as a viable fallback: cut
at the **neck bone**, never by a raw bounding box (a bbox cut has been
observed to drag in unrelated nearby geometry, such as a stray shoulder
piece next to the head).

## Composing a full avatar head

Head, hair, and beard meshes are authored **in body space** — they seat
correctly at the neck with no additional placement transform needed, exactly
like garment parts. A composite avatar render merges a chosen chargen head
style, hairstyle (respecting the three-state hair rule above when a headwear
item is equipped), and beard style (where applicable) onto an already-dressed
body.

**Head/hair meshes are skinned** using the same vertex-stride family as
worn garments (see [animation.md](animation.md) for the full stride table)
— no separate skinning mechanism is needed. This is what allows a composed
face to show idle nods and eye motion driven by the same skeleton and
animation clip as the rest of the body.

## Verified

Visually confirmed on multiple body types: individual hairstyle meshes
render as layered strand shells with correct texturing; individual head
meshes render as full heads with ears, neck, and a real textured face
(eyes, brows, nose, lips visible); and a fully composed avatar (dressed
body + chargen head + hairstyle + beard) renders with face, hair, and a
long braided beard all correctly seated on the body, reproduced
independently on two different race/sex body types.

## Open

- Hair tint (the character hair-color property) and the face compositor
  layer system are not implemented — only base diffuses render. The
  alpha-cutout-vs-tint-mask distinction discussed above is resolved at the
  *format* level (see [shaders.md](shaders.md)), but no script in this
  repository currently composes chargen head/hair geometry at all, so
  neither the shader classification nor the hair-color property has
  anywhere to plug into yet.
- The Man-vs-Elf chargen table split, inferred from body scale and
  beard-slot presence rather than name-resolved, is not confirmed against
  the live game's character-creation screen.
- A small number of chargen records of unclear identity (an eighth
  hobbit-height style set; an untested Man-female facial-hair file) remain
  unidentified.
- A large number of non-player-character `0x47` avatar records (NPCs,
  mounts) use the same format and are unexplored.

## See also
- [wardrobe.md](wardrobe.md) — the shared `0x20` grammar and per-family tag semantics
- [properties.md](properties.md) — the typed PropertiesSet reader used for `0x47` records
- [textures.md](textures.md) — the surface → material → diffuse chain style meshes bind through
- [shaders.md](shaders.md) — the alpha cutout vs. tint mask classification that resolves the caveat above
- [dyes.md](dyes.md) — the tint-mask mechanism shared with hair-color tinting
- [animation.md](animation.md) — skinning a composed head/hair/beard set to the body's rig
- [limitations.md](limitations.md) — open items in chargen selection
