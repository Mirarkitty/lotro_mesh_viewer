# The outfit composer & the saved-outfit loader

The outfit composer is the full-avatar front-end built on the modules in
this repository — one page that exercises every solved subsystem at once:
item resolution, wardrobe binding, mesh decode, textures, shader-driven
rendering, dyes, hair/face, skeleton and animation.

It is included in this repository: run it with

```bash
python3 items_catalog.py     # once: the catalog powers all search
python3 outfit_app.py        # -> http://127.0.0.1:8723/
```

(`outfit_app.py` + `outfit.html`, backend in
[api_common.py](scripts/api_common.md) and
[charparts.py](scripts/charparts.md); the repo's simpler
[viewer](scripts/viewer.md) (`app.py`) covers the single-item subset.)

![The composer with a saved outfit loaded](img/outfit-loader.png)

*A saved character outfit ("toon" Inja, outfit `#2`) loaded and rendered:
wearable slots resolved with per-slot dyes applied, plus two held weapons
(dual axes) and a bow rendered via the held-item chain (see
[weapons.md](weapons.md)), skinned to the rig.*

## The saved-outfit loader

The loader imports outfits saved by
[LotroCompanion](https://github.com/LotroCompanion/lotro-companion) — the
character-management app many players already run. LotroCompanion stores
each character ("toon") under its data directory
(`~/.lotrocompanion/data/characters/toon-*/`), including every saved
wardrobe outfit as a list of per-slot item IDs plus each slot's chosen dye
color.

Using it:

1. **Pick the toon** in the `toon` dropdown (populated from the
   LotroCompanion data directory; the row is hidden when no companion data
   is found).
2. **Pick a saved outfit** in the dropdown next to it — outfits are listed
   as `#N — <chest item name> (piece count)`.
3. Each of the outfit's slot items is then resolved exactly like a manual
   pick: item DID → [PropertiesSet](properties.md) →
   [worn-appearance record](wardrobe.md) for the chosen body →
   [mesh decode](mesh-format.md) → [material/diffuse](textures.md) →
   [shader render hints](shaders.md), with the saved **per-slot dye colors**
   applied via the [alpha-mask dye model](dyes.md).
4. **Held items** (`MAIN_MELEE`, `OTHER_MELEE`, `RANGED`, `CLASS_ITEM` in
   LotroCompanion's slot naming) are imported into the `MainHand`/
   `OffHand`/`Ranged`/`ClassItem` rows below the wearable slots and
   rendered via the separate weapon chain (item → `PhysObj` → mesh — see
   [weapons.md](weapons.md)), rigid-bound to an attachment bone rather
   than skinned like a garment. Only aura slots (`*_AURA`) stay skipped
   and reported in the status line, e.g. `outfit: 5 pieces + 3 held — 1
   aura/unknown slot(s) skipped`.

Only the item IDs and dye names come from LotroCompanion; every byte of
geometry, texture and animation is decoded live from the client `.dat`
files by this repository's modules.

## Held items: weapons and class items

Below the seven wearable slot rows, four more rows —
`MainHand`/`OffHand`/`Ranged`/`ClassItem` — cover LotroCompanion's
`MAIN_MELEE`/`OTHER_MELEE`/`RANGED`/`CLASS_ITEM` slots. These are held
items, not garments: they don't participate in dyeing, the blanking rules
(a dress doesn't hide a sword), or per-body wardrobe selection — they
resolve through the separate chain documented in
[weapons.md](weapons.md) and render only when animation is on (the mesh
is rigid-bound to a bone in a posed skeleton; with animation off there is
no sensible unposed placement to show, and the row instead reads
`⚠ needs animate`).

Each row has its own **attachment dropdown**
(`hand_r`/`hand_l`/`hip_l`/`hip_r`/`back`) — where the game's own
drawn/sheathed attachment data isn't resolved from the client files (see
[weapons.md](weapons.md#open-gaps)), this is the user-facing
workaround: pick where the item visually attaches, per slot, independent
of its default. Held-item search isn't wired into the per-slot search
boxes (held items aren't in the item catalog — the catalog sweep only
covers wearables); they're populated exclusively by the LotroCompanion
importer, keyed by DID and shown by name from the saved outfit file.

Any hand holding a weapon also gets a **grip overlay** applied
automatically: locomotion clips leave the fingers open, so the viewer
curls the finger-chain bones over the clip pose each frame on any hand
whose held slot is attached to it — hand-tuned curl angles, not decoded
game data (see
[weapons.md](weapons.md#attachment-bones-rigid-binding-grip-overlay)).

When the character's own race has no rig this repository can render
(`body: null` in `companion_outfit`'s return — see
[scripts/api_common.md](scripts/api_common.md#lotrocompanion-saved-outfit-import)),
the loaded outfit still shows, on whichever body is currently selected,
with a note in the status line (`no rig for this race — shown on
<body>`) rather than silently rendering on the wrong body without saying
so.

On Linux, [lotro_extractor](https://github.com/Mirarkitty/lotro_extractor)
can produce the LotroCompanion-compatible character files natively
(LotroCompanion's own importer is Windows-only), plus the optional
`appearance_extracted.json` (per-character chargen styles and colors, which
LotroCompanion doesn't create yet) — placed in the repository directory or
via `$LOTRO_APPEARANCE_JSON`, it makes loaded characters render with their
real face, hair and coloring. See
[api_common.md](scripts/api_common.md) for the lookup details.

## What each control exercises

| Control | Module / format doc |
|---|---|
| `body` (race/sex) + `skin`/`hair` pickers | per-body [worn-appearance records](wardrobe.md), [hair & face](hair-face.md) |
| per-slot `search …` boxes | the catalog built by [items_catalog.py](scripts/items_catalog.md); also takes a bare DID — item `0x70…`, appearance `0x20…`, or LotroCompanion's decimal itemId |
| `set` search (whole armour sets) | catalog name-stem grouping over the same data |
| per-slot / set-wide `dye` dropdowns | the [dye system](dyes.md) (`alpha < 128` tint mask) |
| the `toon` / outfit dropdowns | the saved-outfit loader above |
| `anim`, `motion`, riding filters | [skeleton + clips](animation.md) via [havok_anim.py](scripts/havok_anim.md) / [export_skinned.py](scripts/export_skinned.md) |
| slot checkboxes (show/hide) | the slot-blanking mechanism in [wardrobe.md](wardrobe.md) |
| cutout/metal rendering | [shader classification](shaders.md) exported by [compose.py](scripts/compose.md) |
| `MainHand`/`OffHand`/`Ranged`/`ClassItem` rows + attachment dropdowns | the held-item chain and attachment bones in [weapons.md](weapons.md) |

## See also

- [overview.md](overview.md) — the pipeline all of this sits on
- [weapons.md](weapons.md) — the held-item (weapon/class item) format and
  attachment mechanism behind the rows above
- [scripts/viewer.md](scripts/viewer.md) — the released single-item viewer
- [limitations.md](limitations.md) — what still fails (human-body data
  holes, unfinished face compositor, weapon attach-transform gaps)
