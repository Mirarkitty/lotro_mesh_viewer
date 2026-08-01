# Worn-Appearance (`0x20`) Records and the Item Selector

The `0x20` record type in the general-purpose archive is this engine's
analog of a per-item clothing table, except structured very differently:
instead of one record per item, it is **one record per body type**, shared
by every garment that body type can wear. Getting from "an item" to "this
item's entry in here" is the **selector** — the piece that turns a resolved
`Item_AppearanceKey` (see [properties.md](properties.md)) into an actual mesh
and material. Reference implementation:
[`wearable2.py`](scripts/wearable2.md) (grammar-based entry parser,
supersedes an earlier byte-anchor heuristic) and
[`selector.py`](scripts/selector.md) (the item-to-body resolution API).

## It's a shared wardrobe, not one garment per record

A given `0x20` record is roughly 100 KB and contains on the order of a
thousand draw entries for one body type. A single shared body mesh appears
in **many** entries, each bound to a **different** material — because the
record enumerates *every* garment that uses that body shape, not just one
item. Several body-type variants of one garment "family" (e.g. all sexes and
races of one dress) reference this same kind of shared wardrobe, sized for
their respective body type.

This means "resolve item → mesh + material" needs one more step beyond
knowing the `0x20` DID: picking the *one* entry among many that is this
specific item.

## The record grammar

The complete, byte-exact structure below was derived by strict sequential
parsing and validated against **4,181 of 4,192 records with zero leftover
bytes** (11 records share a further sub-variant of the format not yet
derived — entries where the word after the key looks like a mesh DID rather
than a block count).

```
u32  DID                      self-DID
u32  0
u8   sectionType              0/2/3/4/5/6 observed — same payload shape for
                              all; per-family meaning unresolved (garment
                              category suspected)
vle  count                    VLE as in properties.md
count x entry:
    u32  key                  Item_AppearanceKey (0x1000xxxx)
    u32  nblocks              number of variant blocks (1..53 observed)
    nblocks x block:
        f32  q                block selector: 0.00, 0.01, 0.02 ... steps —
                              almost certainly the DYE floatCode
                              (see dyes.md); block q=0/1.0 = base look
        u32  gc ; f32 d
        gc x group:           per-part-slot material bindings
            u32  x            0, or a 0x2B DID, or rarely other
            u32  material     0x30 DID, or 0 (null)
            u32  A ; u32  B   usually (2, 0x100000xx); also seen
                              (0x1000000C, 0x10000073) etc. — semantics
                              unresolved; B often mirrors part tags
            f32  g
        u32  pc ; f32 e
        pc x part:
            u32  tag          part-slot type (0x100000xx, or a small int
                              below 0x100)
            u32  mesh         0x06 GfxObj DID
            f32  lod          ALWAYS present (may be 0.0 on the last part)
        u32  0                block tail
(then a trailing section, same u8-type + vle-count framing:)
u8 0x01 ; vle count ; count x { u32 key ; u32 value }
                              trailing key table, count == entry count;
                              value semantics unresolved (often 0)
```

No trailer byte-patterns and no anchoring are needed — the record parses
start-to-end with this grammar alone.

### Part tags are per record family, not global

Do **not** treat any `0x100000xx` part tag as globally meaningful across
record families. Observed so far, per family:

| Family (example) | garment tag | hands tag | stub tags |
|---|---|---|---|
| dress/chest (section type 6) | `0x1000000C` | `0x10000001` | `0x10000003`, `0x10000006` |
| hats (section type 5) | `0x10000005` | — | — |
| legs (section type 4) | `0x10000003` | — | — (same value as a stub tag in the chest family) |

Head-slot records use their own tag vocabulary entirely — see
[hair-face.md](hair-face.md#head-slot-wardrobe-side). **Distinguish stub
parts by on-disk mesh size (roughly under 2 KB), not by tag value alone.**

### Dye variants come for free

An entry's blocks are keyed by `q` in exact 0.01 steps (0.00–0.52 observed
in one record, 53 blocks), each with its own per-block material and usually
zero parts (`pc = 0`, i.e. the block changes the material only). This
connects the wardrobe record format directly to the dye floatCode model in
[dyes.md](dyes.md), and likely supplies the per-dye material table needed
for exact per-dye texture selection instead of runtime tinting.

## Selecting an entry: `Item_AppearanceKey`

**The item's draw entry in a given body's `0x20` record is the one whose
`key` field equals the item's `Item_AppearanceKey`** (from the item's
PropertiesSet — see [properties.md](properties.md)).

Within that entry, take the base-look block (`q` near 0.0, or block 0), and
within that block, select the part whose `tag` matches the garment tag for
this record's family (e.g. `0x1000000C` for a dress-family record). The
`mesh` field of that part is the garment's own geometry; the `material`
field of the enclosing group (or the block's group list) is what to resolve
through the diffuse chain in [textures.md](textures.md).

### A historical off-by-one, corrected

An earlier version of the entry parser treated the byte pattern
`00 00 00 02 10 00 00 50` as an entry **trailer**, and took the part list
found *before* a key as belonging to that key. In the corrected grammar
above, that byte pattern is actually the `(A, B)` pair of a mid-entry
material group — the part list before a key belongs to the **previous**
entry, not the one following it.

The consequences of this bug are worth recording because they illustrate how
convincingly a wrong parse can look right:

- Every resolved *material* was correct (it directly follows the key), but
  every resolved *mesh list* was the **previous** entry's. Renders still
  looked plausible because neighbouring wardrobe entries usually share the
  same rough shape-class of mesh.
- On record families that don't use the `(2, 0x10000050)` pair pattern at
  all, the byte-anchor trailer never matched, part lists accumulated
  cumulatively across entries, and a size-based fallback returned the
  **same** (largest-seen) mesh for essentially every item in that family —
  the visible symptom that eventually triggered a full audit and rewrite.

The audit's broader lesson: **an off-by-one selector produced numerically
plausible, visually plausible-*looking* renders for an extended period**,
and was only caught by deliberately testing arbitrary items instead of
re-confirming the same one or two known-good examples. Any mesh DID
"verified" against the earlier (byte-anchor) parser should be treated as
unverified until re-checked against the grammar-based parser above.

## Verified end-to-end example

| Item | Appearance Key | Material | Diffuse | Look |
|---|---|---|---|---|
| Dress A | `0x10000765` | material X | diffuse Y | gray body + gold/brown floral trim |
| Dress B (different item) | `0x10000417` | material Z | diffuse W | cream bodice + green vine embroidery + brown sleeves/skirt |

Both materials map coherently (no UV scrambling), and the two items are
clearly distinct garments — confirming the selector actually discriminates
between items rather than defaulting to one shape. This distinction between
two independently-verified items, rather than trusting a single working
example, is what caught the off-by-one bug above and is treated as the
minimum verification bar for any selector claim in this project.

## Species → race mapping

`Item_SpeciesOfWearer` values map to the game's public race enumeration
(cross-referenced against [LotroCompanion's `lotro-data`](https://github.com/LotroCompanion/lotro-data)
species/race label tables):

| code | race |
|---|---|
| 23 | Man (human) |
| 65 | Elf |
| 73 | Dwarf |
| 81 | Hobbit |
| 114 | Beorning |

Additional codes `117`, `120`, and `125` appear in real item data and are
**not** in the confirmed five-value table above. By process of elimination
against the game's playable-race list and observed body reuse: `117` is
plausibly **High Elf** (reuses Elf bodies), `120` plausibly **Stout-axe**
(both sexes map to the Dwarf-male body — the data-level confirmation that no
separate female Dwarf body exists). `125` is **unresolved**; the only
remaining playable race candidate maps implausibly onto Man bodies. Treat
these three codes as unconfirmed pending a direct check against the game's
own species enumeration data. Sex is a bit flag on a separate property field
(`Item_SexOfWearer`: `0x1000` male, `0x2000` female — see
[properties.md](properties.md)) — sub-races collapse onto their parent
race's shared body records, which is why a small number of distinct wearer
(species, sex) combinations collapse onto a handful of actual `0x20` records
per garment family. Dwarves in particular ship **male-only** bodies for at
least the item families examined so far.

## The human/Man body: a genuine data hole for some garments

For some garment families, the Man-body (and occasionally Elf-male) entry's
garment part names a mesh DID that is **not a direct file anywhere in the
client's archives**. This was exhaustively checked, not assumed:

- not a file in **any** client `.dat`/`.datx` archive (full directory walk
  of every mesh-holding archive);
- not referenced in the decompressed content of any `0x01` "Setup" record,
  nor in any other `client_general`/`client_gamelogic` record — i.e. **no
  redirect table exists** anywhere in the client data;
- **no arithmetic DID transform** relates the missing DID to the present
  per-race DIDs for the same garment (diffs between present/absent pairs
  vary too widely to be a fixed offset).

So this is a genuine **per-garment data hole**, not a parse artifact and not
a solvable indirection through some other record type. Critically, this is
**not** "Man geometry is missing" in general — the Man-body `0x20` record
ships hundreds of unique garment meshes across its many draw entries, and
plenty of them (including full-length dresses) are present and decode
cleanly. The hole is specific to individual garments whose Man-body geometry
was, for whatever reason, never shipped for this content wave. Whether the
live game engine synthesizes the missing mesh at runtime from a base body
plus a per-garment morph, or simply never draws it, is undecidable from
static client data alone.

### Rendering a hole garment anyway: the shared-UV stand-in

All garment meshes of one body type share a common UV atlas layout. Applying
a hole garment's own diffuse texture to an *unrelated but present* full-length
garment mesh of the same body type produces a coherent-looking render
(trim and pattern details land in the right places), because the UV
coordinates line up even though the exact silhouette does not. A renderer
facing a data hole for one body/garment combination can fall back to: the
item's own mesh if shipped, otherwise a present full-length garment mesh
from the *same* `0x20` record, textured with the item's own diffuse, and
explicitly flagged as a stand-in (not the item's exact geometry). This gives
correct race, correct diffuse, and a coherent (shared) UV layout, at the
cost of exact silhouette accuracy.

## ⚠️ Aux-archive gotcha: apparent "holes" that are actually just `.datx` content

Two separate bugs, independently discovered in different parts of this
project, produced false "this data is missing" conclusions before being
traced to the same root cause: a presence check that only searched the base
`.dat` archive, never the `.datx` auxiliary archive that actually held the
content (see [dat-format.md](dat-format.md)'s note on `.datx` archives).
Once presence checks were fixed to search the full archive chain, content
previously believed absent — including entire body types' garment meshes,
and a body's default hair mesh — turned out to ship normally. **Always use a
multi-archive chain lookup for presence checks**, never a single-archive
open.

## Sibling items and sleeve variants

A single named "look" is not always one item with one mesh. The client
ships **sibling items** that share a display name and a material but bind
to genuinely different geometry. A concrete, reproducible example: one
dress "look" actually ships as three separate items — long-sleeved,
short-sleeved, and sleeveless — each with its **own** `Item_AppearanceKey`
and its **own** garment mesh, sharing only the material DID.

All three siblings, on one body type, were confirmed to be **full
floor-length meshes** once the off-by-one bug above was fixed — an earlier
"upper-body-only, needs a separately-assembled skirt" theory for the
shorter-sleeved siblings was an artifact of that bug and is not real. The
genuine difference between siblings is per-mesh cloth geometry: sleeveless
< short-sleeved < long-sleeved in vertex count, tracking the amount of
sleeve cloth actually modeled. Verified per-mesh detail (from false-color
per-submesh rendering): the sleeveless and short-sleeved meshes model arm
geometry only to the elbow, ending in open tubes (bare skin picks up from
there via the separate hands part); the long-sleeved mesh runs to the wrist
and ends in real hand/finger geometry, replacing the hands part entirely.

The corresponding open problem — resolving the *correct* diffuse when one
shared material has more than one plausible candidate texture depending on
which sibling mesh it's applied to — is a **texture-binding** problem, not a
mesh-decode problem; see [textures.md](textures.md) and
[mesh-format.md](mesh-format.md#uv-values-outside-01-are-normal-not-corruption)
for the investigation that established this. It's also worth noting from
the same investigation: the meshes' 8 submeshes were found to come in
identical-vertex-count *pairs* that spatially overlap — almost certainly LOD
duplicates that a renderer should deduplicate (keep one of each pair) rather
than draw both.

### Two DIDs, one look: item reissues

Separately from sleeve variants, it's possible for the *same* displayed item
name to resolve to **two different item DIDs** that nonetheless share the
same `Item_AppearanceKey` and material for every body type, differing only
in a body-type entry's species tag (e.g. `73` vs. an unconfirmed `120`) —
both pointing at the identical garment mesh. The most likely explanation
consistent with the data is a content reissue: an older item reintroduced
under a fresh DID with its appearance record essentially copy-pasted, but
touched enough during re-integration to pick up a renumbered or
re-tagged species value on one body entry. For rendering purposes, two DIDs
like this are the same garment and should be treated as such.

## Selector API summary

A selector built on this format typically exposes:

- **appearance map** — from an item's decoded PropertiesSet, return the
  per-body-type list of `{species, sex, key, worn_appearance}` plus the
  item's base `PhysObj`.
- **resolve binding** — given a body's `0x20` DID and an `Item_AppearanceKey`,
  parse the matching entry and return the resolved material, the
  tag-selected garment mesh, presence/absence flags, the attach (hands)
  part, and the full part list.
- **resolve item** — full resolution across every body-type entry for one
  item, flagging whether each body's garment mesh is a direct, decodable
  file.
- **renderable body** — pick a body whose garment mesh **is** a direct file
  (preferring the human body when available), explicitly not silently
  substituting a different race's body when the human data is a hole.

## See also
- [properties.md](properties.md) — where `Item_AppearanceKey` / `Item_WornAppearance` come from
- [textures.md](textures.md) — resolving the entry's material to a diffuse image
- [mesh-format.md](mesh-format.md) — decoding the entry's mesh DID (when present)
- [hair-face.md](hair-face.md) — the head-slot family's own tag vocabulary and stub mechanism
- [dyes.md](dyes.md) — the per-block `q` dye-floatCode connection
- [limitations.md](limitations.md) — remaining gaps downstream of the selector
- [scripts/wearable2.md](scripts/wearable2.md), [scripts/selector.md](scripts/selector.md) — reference implementations
