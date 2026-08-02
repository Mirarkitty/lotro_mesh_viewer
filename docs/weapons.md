# Weapons and Held Items

Weapons, shields, and class items (Rune-keeper satchels, Minstrel
instruments, Loremaster books, …) are not worn on a body the way a
garment is — they are rigidly parented to a hand/back/hip bone. This page
covers the geometry chain that resolves one of these items to a mesh, how
it differs from the [wardrobe](wardrobe.md) chain garments use, the dead
ends that were ruled out on the way, and what is still open. Reference
implementation: [`weapon_resolve.py`](scripts/weapon_resolve.md); composer
integration: [`api_common.compose_weapon`](scripts/api_common.md#composition)
and [outfit-composer.md](outfit-composer.md).

## The geometry chain

```
item 0x70 -> PropertiesSet (item + 0x09000000, see properties.md)
  -> `PhysObj` property: a 0x47 DID (client_gamelogic.dat)
     -> 0x47 entity record: [u32 classTag][u32 selfDID][u32 7]
        [u32 template DID (0x1F)][tsize nprops][doubled-pid properties]
     -> 0x1F template (client_general.dat): last u32 in the record is a
        0x04 DID (a "type 9" entry: ...09 00 00 00 | 01 00 00 00 | <DID>)
        -> 0x04 record (client_general.dat): an actual Havok hkaSkeleton
           packfile whose TRAILER is [u8 count][count x u32 mesh DIDs]
           -> 0x06 GfxObj meshes (client_mesh.dat or
              client_mesh_aux_1.datx) — decoded the same way as any other
              mesh, see mesh-format.md
```

This is a different chain from the wardrobe one: garments go
`item -> Item_WornAppearanceMapList -> 0x20 worn-appearance record ->
mesh`, keyed per (species, sex). Held items go
`item -> PhysObj -> 0x47 -> 0x1F -> 0x04 -> mesh`, with no per-body-type
branching at all — a weapon's mesh is the same regardless of who's
holding it. `weapon_resolve.py`'s `resolve_weapon(item_did)` implements
every step in one call.

**Parent-`0x47` chains.** Some weapons' `0x47` entity doesn't point at a
`0x1F` template directly — its "template" field is itself another `0x47`
DID, a shared base object (seen on daggers and two-handed swords).
`resolve_weapon` follows this chain automatically, up to 8 hops, re-running
`parse_physobj` on each parent and merging that parent's properties into
the child's via `dict.setdefault` (the child's own properties win on any
key both carry) until it lands on an actual `0x1F` template; a chain that
doesn't bottom out within 8 hops raises rather than looping forever.
`resolve_weapon`'s result also now exposes `item_class` (the item's own
`Item_Class` property), read before the chain is followed — used
downstream to decide hip-stow orientation (see "Attachment" below).

The `0x04` record here is genuinely a Havok skeleton packfile (the same
container format documented in [animation.md](animation.md#container-format-havok-binary-tagfile))
— but for a weapon it's a degenerate one, present only to carry the mesh
trailer; `weapon_resolve.skeleton_meshes` never parses it as an actual
Havok object, it walks the trailer bytes directly. `template_skeleton`
finds the `0x04` reference by reading the template's last `u32` and
checking its type byte, rather than parsing the `0x1F` record's full
internal structure — sufficient for every template observed, but not a
complete grammar for `0x1F` (see "What was measured vs. assumed" below).

## What was measured vs. assumed

**Measured, verified end to end on all 9 held items** across two real
characters' outfits (axes, bows, a rune-stone legendary item, a
Rune-keeper satchel, and even the weapon aura, which resolves to a small
fx mesh): 9/9 resolved via `weapon_resolve.py`, and the decoded meshes
match their real-world shapes by eye — bows come out as 1.9–2.2 m curved
staves, axes as handle+head, the rune-stone as a ~15 cm pebble.

A broader sweep of the `0x1F` template population (~29,000 records, close
to the ~28,500 count of `0x04` skeleton records) is consistent with one
template + skeleton pair per visual object, and a sample of 400 `0x04`
records all ended in the mesh-DID trailer with count usually 1 — but this
is a statistical consistency check, not a claim that every `0x1F`/`0x04`
pair in the archive was individually walked and confirmed.

**Assumed, not yet checked**: that the chain generalizes to every item
category tagged as a held slot (shields, all class-item types) the same
way it was confirmed for weapons specifically. Shields are documented as
`OTHER_MELEE` in LotroCompanion's slot naming but behave like armour for
dyeing purposes (see "Open gaps" below) — that dual nature hasn't been
probed against this chain independently.

## Dead ends

Documented failures are part of this project's culture (see
[limitations.md](limitations.md)'s failure-mode log) — these are the
specific wrong turns taken before the chain above was found, kept here so
they aren't retried:

- **`ExternalAppearanceID` (pid family `0x20000004`) is a shared material
  stub, not the weapon's geometry.** Every weapon item checked carries the
  same `0x20000004` value in this property — a red flag on its own for
  something meant to be per-item appearance. The record it points to is 72
  bytes, has no strings, and parses as a wearable entry with exactly one
  draw entry and **zero parts**: its payload is a material DID plus two
  more keys, i.e. a material *binding*, not geometry. No `0x06` mesh DID
  appears anywhere in it. This ruled out the naive "weapons use the same
  `0x20` appearance mechanism as garments, just with a lower-numbered
  family" hypothesis — the real chain (above) doesn't touch `0x20` at all.
- **`setup_hunt`-style scans for weapon DIDs inside `0x01` Setup records
  were byte-coincidences.** A DID's bytes turning up inside a `0x01`
  record's vertex-index table looked promising until checked closely — the
  hits were inside numeric tables where any 4-byte value has a real chance
  of matching by chance, not a genuine cross-reference. The same applied
  to a "parallel numbering" idea (that the low word of a skeleton `0x04`
  DID's low bits might relate to a mesh DID's low bits) — unrelated.
- **The `0x47` record's own properties are render hints, not more
  geometry.** Decoding the doubled-pid property block on the weapon's
  `0x47` entity turns up things like imbue-streak vectors,
  `Render_LODClass`, and `ScriptSystem_FxOverlay_Physobj` (a pointer to a
  *second*, translucent `0x47` overlay object, presumably for imbued/
  legendary glow effects) — worth knowing they're there, since a decoder
  scanning this record for "the mesh DID" could plausibly latch onto one
  of these by mistake, but none of them carry geometry.

## Texture binding

Weapon `0x31` surfaces use the same 22-byte compact record format as
garment surfaces (see
[textures.md](textures.md#material-chain-mesh--surface--shadermaterial--texture)),
with one field difference: garment surfaces carry a `0x10` slot key in the
third `u32`, weapon surfaces carry a small plain integer instead (seen: 1).
`tex_extract._compact_surface_materials` accepts both forms — see
[scripts/tex_extract.md](scripts/tex_extract.md). Weapon shader
`0x2B0007BF` classifies through the uniform-count fallback in
[shaders.md](shaders.md) (opaque, undyeable) rather than one of the named
patterns.

## Worn class items: the torch-`PhysObj` trap

Not every "held" slot item is actually held. Some class items — Rune-keeper
satchels, kinship-provided instruments — carry an
`Item_WornAppearanceMapList` exactly like a garment, keyed per (species,
sex) the same way a chest piece is. Their `PhysObj` still resolves through
the chain above, but it doesn't lead to the item's real appearance: it's
the *generic ground-prop model* used for a dropped/world representation of
the item (measured on a Rune-satchel: the `PhysObj` chain terminates in a
torch mesh, unrelated to a satchel in every dimension). Resolving these
items purely through `weapon_resolve` would render the wrong geometry with
no error to catch it — the chain succeeds end to end, it just succeeds at
the wrong object.

`compose_weapon` (`api_common.py`) checks for this before touching the
`PhysObj` chain at all: `_worn_app_for(item_did, label)` looks up the
item's `Item_WornAppearanceMapList` entry for the target body the same way
a garment's is looked up (`selector.appearance_map`), and if one exists,
`compose_weapon` delegates straight to `compose_skinned` — the ordinary
wearable pipeline — instead of resolving `PhysObj`. Only items with **no**
worn-appearance entry (true held props: weapons, most class items) fall
through to the `PhysObj`/`0x47`/`0x1F`/`0x04` chain. This means a
"class item" row in the composer's held slots can render via either
pipeline depending on the specific item, silently and correctly, without
the caller needing to know which.

## Attachment: bones, rigid binding, grip overlay

A held item needs a bone and a local offset, not skin weights — the
player rigs already expose two dedicated attachment bones,
`rweapon`/`lweapon` (see [animation.md](animation.md#bone-vocabulary)),
plus `hipgirdle` and `midback` used for hip/back carries. `ATTACH` in
`api_common.py` maps an attachment name to a bone plus a local
rotate/offset pre-transform:

```python
ATTACH = {
    "hand_r": {"bone": "rweapon",   "rot": (0, 0, 0),    "off": (0, 0, 0)},
    "hand_l": {"bone": "lweapon",   "rot": (0, 180, 0),  "off": (0, 0, 0)},
    # hang handle-up, blade/head down (+Y -> -Z), then turn the blade plane
    # flat against the body pointing backward (+X -> -Y)
    "hip_l":  {"bone": "hipgirdle", "rot": (-90, 0, -90), "off": (-0.18, 0.05, 0)},
    "hip_r":  {"bone": "hipgirdle", "rot": (-90, 0, -90), "off": (0.18, 0.05, 0)},
    "back":   {"bone": "midback",   "rot": (90, 25, 0),  "off": (0, -0.15, 0.15)},
}
```

`compose_weapon` rigid-binds every vertex of the resolved mesh(es) to the
attachment bone (`charparts._append_mesh(..., rigid_bone=bidx)` — the same
mechanism already used for helm/head parts, see
[scripts/charparts.md](scripts/charparts.md)), then bakes the vertices
into model space at that bone's bind-pose transform so the mesh follows
the bone through any clip without needing per-frame vertex work at render
time.

The bind pose is a T-pose with **identity world rotation** on the hand
attachment bones (measured on Man-F: model Z-up, arms along ±X). Weapon
meshes are modelled grip-at-origin with the handle along local +Y — the
fist's natural grip axis — so the in-hand attachment for `hand_r` is the
identity transform; `hand_l` needs only a 180° rotation about the handle
so the blade/head faces outward on the left side, with no mesh mirroring
required (chirality is preserved). `hipgirdle` is not identity-oriented
the same way — its local Y points forward along the body rather than up —
which is exactly the axis distinction the next paragraph is about.

**The hip transforms were flipping about the wrong axis, and the fix is
the axis lesson worth keeping.** An earlier version of `hip_l`/`hip_r`
used a straight `(180, 0, 0)` rotation, which implicitly treated the
attachment bone's local Y as pointing *up* — the same assumption that
happens to be right for a limb bone, but `hipgirdle`'s local Y points
*forward* along the body, not up. A 180°-about-X flip under that wrong
assumption swung the weapon around the wrong axis entirely, rather than
just hanging it upside down. The corrected transform, `(-90, 0, -90)`,
is derived explicitly from where the weapon's own local axes need to end
up in bone space: `+Y -> -Z` hangs the weapon handle-up, blade/head down,
and `+X -> -Y` then turns the blade's flat plane against the body,
pointing backward, instead of jutting out sideways. Hip and back carries
remain hand-tuned rotate/offset values, not data read from the client —
see "Open gaps" below.

**Weapon-type-aware stowing (`HIP_INVERT_CLASSES`).** The transform above
is the sword convention — hilt up, blade down — but axes and maces stow
head-up at the belt instead, handle hanging down. `HIP_INVERT_CLASSES =
{12, 30}` (`Item_Class` 12 = one-hand axe, 30 = mace/club/hammer) flags
which items need the inverted orientation; `compose_weapon` reads
`item_class` off `resolve_weapon`'s result (the parent-`0x47`-chain-aware
value described above) and, for a hip attachment on a flagged class,
flips the weapon 180° about its own blade axis and shifts it down so the
head — not the grip — sits at the attach point. Only the hip attachments
are affected; hand and back carries are unchanged by item class.

**Grip overlay.** Locomotion clips pose the hand but leave the fingers in
their open clip shape, which looks wrong wrapped around a hilt. The viewer
curls the finger-chain bones (`{r,l}{fingers,pointer,thumb}_{base,mid,tip}`)
over the clip pose every frame on whichever hand's held slot is attached
to it, via `applyGrip()` at the end of `applyPose()` in `outfit.html` — a
small additional rotation about each bone's local Y axis, mirrored per
side. The per-bone curl angles are hand-tuned to look plausible, not
extracted from any game data.

## Dyeing held items

Held slots in the composer now get a per-slot dye dropdown, the same
control the wearable slots have. What it actually changes depends on
which pipeline rendered the item:

- **Worn class items** (routed through `compose_skinned` — see "Worn
  class items" above) dye exactly like a garment: their `cloth_dyed`
  shader honors the [alpha-mask dye model](dyes.md), confirmed on the
  Rune-satchel's own texture.
- **True held items** (the `PhysObj` chain) dye through the same generic
  mechanism now — `outfit.html`'s `composeWeapon` tracks `clothMats`/
  `skinMats` for a held mesh's submeshes exactly as `composeSlot` does for
  a garment, and applies the chosen dye's baked texture the same way. In
  practice most weapon shaders classify as undyeable (see "Texture
  binding" above), so picking a dye on a plain sword mostly has nothing
  to visibly change — the mechanism is uniform, the *effect* isn't,
  because the underlying shader data isn't.

The set-wide **"dye all"** control stays wearables-only deliberately: it
loops over the seven wearable slots only, never the held ones, because
most weapon shaders can't take a dye and running it across held rows too
would silently do nothing for the common case while implying it should.
Dyeing a held item is still possible — just per-slot, from its own
dropdown, not swept in by the bulk control.

## Open gaps

- **Real attach transforms are not read from the dat files.** The
  rotate/offset values in `ATTACH` above were tuned by eye against a
  rendered T-pose, not derived from client data. The `0x01` Setup records
  (structurally identified, not parsed — see
  [limitations.md](limitations.md)) are the most likely place the game's
  own placement matrices live, but that hasn't been confirmed.
- **Handedness/mirroring beyond the single `hand_l` 180° case is
  unconfirmed for the general population of weapons.** `MAIN_MELEE` and
  `OTHER_MELEE` can carry the *same* item DID in a real outfit (dual-wield),
  which means the same mesh has to bind convincingly to both a right- and
  a left-hand bone — verified visually as *plausible* for the axes tested,
  not swept across weapon shapes broadly (a curved weapon modelled
  asymmetrically could still look wrong mirrored this way).
- **Sheathed vs. drawn is only partly addressed.** The hip stow
  orientation is now weapon-type-aware (`HIP_INVERT_CLASSES` — swords
  hang hilt-up/blade-down, axes and maces hang head-up at the belt, see
  "Attachment" above), so a stowed weapon at least *hangs the right way
  up* for its type. What's still not modelled: the game switches a
  weapon between a stowed hip/back placement and an in-hand drawn one
  automatically by combat stance; this toolkit always renders at one
  fixed attachment point chosen by the user from a dropdown
  (`hand_r`/`hand_l`/`hip_l`/`hip_r`/`back`), with no automatic
  switching and no per-item "this is how *this* weapon sheathes"
  transform — the hip/back offsets are shared, hand-tuned constants, not
  per-weapon data.
- **Dyeable weapon/shield properties are unimplemented for the `PhysObj`
  chain specifically.** Worn class items (Rune-satchels, …) already dye
  correctly, because they render through the ordinary wearable
  `cloth_dyed` pipeline (see "Worn class items" and "Dyeing held items"
  above) — that part of this gap is closed. What remains open is the
  `PhysObj`-chain weapons/shields: a `0x1F` template form was observed
  that's 38 bytes rather than the dominant 26-byte size and references a
  *parent* `0x1F` template plus a `0x20` appearance record carrying
  property overrides — the likely path for shields, which dye like
  armour rather than rendering as fixed-material weapons, but this form
  wasn't needed for (and hasn't been exercised by) any of the plain
  weapons resolved so far, and most weapon shaders classify as
  undyeable regardless (see "Dyeing held items" above).
- **One part-mesh in a multi-mesh skeleton trailer decoded with "no
  vertex blocks."** Multi-mesh trailers seen so far are separate parts
  (different bounding boxes), not LOD levels; one of three parts in a
  probed multi-part item is a shadow- or billboard-style record that
  `mesh_decode.py` can't parse (see
  [mesh-format.md](mesh-format.md#known-gaps)) — that item currently
  renders with a part missing rather than failing outright.
- **Auras (`MAIN_HAND_AURA`/`OFF_HAND_AURA`/`RANGED_AURA`) are
  intentionally not rendered.** They resolve fine through this same chain
  (verified: the weapon aura tested came back as a small fx mesh) but stay
  in `skipped` in `companion_outfit` — a tiny glow-effect prop isn't worth
  showing standalone, so this is a deliberate scope cut, not a resolution
  failure.

## See also

- [scripts/weapon_resolve.md](scripts/weapon_resolve.md) — the reference
  implementation: CLI and public API for this page's chain.
- [scripts/api_common.md](scripts/api_common.md#composition) —
  `compose_weapon`, the `ATTACH`/`HELD_ATTACH` tables, and how held items
  surface through the LotroCompanion import.
- [outfit-composer.md](outfit-composer.md) — the held-slot UI rows,
  attachment dropdown, and grip overlay in the composer front-end.
- [wardrobe.md](wardrobe.md) — the parallel, per-body-type chain garments
  use instead of this one.
- [mesh-format.md](mesh-format.md) — mesh decode, shared by garments and
  held items alike.
- [textures.md](textures.md) — the material chain the compact `0x31`
  surface variant above is a corner case of.
- [animation.md](animation.md#bone-vocabulary) — the `rweapon`/`lweapon`
  attach bones and the rigid-binding mechanism this page's attachment
  section builds on.
- [dat-format.md](dat-format.md#did-type-map-which-archive-holds-what) —
  where `0x1F` and `0x47` sit in the archive/type map.
- [limitations.md](limitations.md#weapons-and-held-items) — the short,
  pessimistic version of the open-gaps list above.
