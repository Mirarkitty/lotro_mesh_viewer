# api_common.py

[`api_common.py`](../../api_common.py)

## Purpose

`api_common.py` is the framework-free backend shared by both web front-ends:
every function returns plain dicts/lists, no Flask objects anywhere. It is
the glue layer between the data-layer modules ([datfile.py](datfile.md),
[propset.py](propset.md), [wearable2.py](wearable2.md),
[selector.py](selector.md), [tex_extract.py](tex_extract.md),
[mesh_decode.py](mesh_decode.md), [compose.py](compose.md)) and the two
HTTP servers: [`outfit_app.py`](outfit_app.md) (the multi-slot outfit
composer) uses nearly all of it; the simpler single-item
[viewer](viewer.md) (`app.py`) uses only the item-catalog/search/compose
subset.

It covers four areas:

1. **Item catalog / search / sets** — ranked name search, per-item-set
   grouping, and per-item setmates over `items_catalog.jsonl`.
2. **Animation clip listing** — candidate clip lists per body rig, built
   from prebuilt classifier data (motion filters, dedupe, quota selection).
3. **LotroCompanion saved-outfit import** — reading a player's saved
   character outfits and (optionally) their extracted chargen appearance.
4. **Composition & texture baking** — cached calls into
   [compose.py](compose.md)/[charparts.py](charparts.md)/
   [export_skinned.py](export_skinned.md) to produce skinned garment and
   face meshes, plus dyed/skin-tinted texture baking.

## Catalog / search / sets / setmates

| Function | Signature | Returns |
|---|---|---|
| `fold` | `fold(t)` | accent-folded, lowercased string for search matching (`Ordâkhai` → `ordakhai`) |
| `catalog` | `catalog()` | the full parsed `items_catalog.jsonl` (loaded once, memoized) |
| `search` | `search(q, slot=None, limit=100)` | ranked, deduped item rows matching a name query, optionally filtered to one slot; requires ≥1 body `present`. A bare DID also works — item DID (`0x70…`), appearance DID (`0x20…`, returns every item sharing that record, rows tagged `matched: "item"/"appearance"`), with or without `0x`, or the decimal itemId form LotroCompanion's `outfits.xml` uses; an unmatched DID falls through to text search |
| `set_stem` | `set_stem(name)` | the item's set name with piece words (`helm`, `gloves`, `of`, `heavy`, …) stripped off the tail |
| `setmates` | `setmates(did)` | `{stem, slots: {slotName: [row...]}}` — other items sharing this item's set stem, grouped by slot |
| `sets_index` | `sets_index()` | `{stem: {slots, weights}}` over the whole catalog, for stems covering ≥3 distinct single-bit slots (memoized) |
| `search_sets` | `search_sets(q, weight=None, limit=30)` | ranked list of matching armour sets, each with its dominant armour weight and per-slot item lists |
| `weight_of` | `weight_of(equip_cat)` | `"light"`/`"medium"`/`"heavy"`/`None` from the `Item_EquipmentCategory` bitmask |
| `item_row` | `item_row(item_did)` | one catalog row by DID, in the same shape `search` returns — used to restore an outfit from a URL hash without a name search |
| `bodies_for` | `bodies_for(item_did)` | **live** (not catalog-cached) per-body renderability for one item |

`search` and `setmates` both filter on `bodies[i]["present"]`, which is
**not computed here** — it comes pre-baked from
[items_catalog.py](items_catalog.md)'s `augment_presence()` sweep pass (see
that page). `bodies_for`, by contrast, recomputes presence live by parsing
the item's `0x20` worn-appearance record on the spot — used where the
catalog's cached flag isn't good enough (e.g. right after a catalog rebuild,
or to double-check one item).

## Animation clip listing

### Shipped data files this reads

All of the following are prebuilt, git-tracked JSON files that ship with
the repository (`os.path.join(HERE, ...)`, i.e. next to `api_common.py`).
**The scan scripts that produce them are not in this repository** — treat
them as prebuilt, regenerable data with the specific classifier tooling
external:

| File | Loader | Produced by (external) | Content |
|---|---|---|---|
| `clips_by_rig.json` | `_rig_clips()` | (external rig-clip scan) | per-rig-stem clip lists: `{did, duration, frames, tracks}` |
| `clip_names.json` | `clip_names()` | **this module** (`/clipname` route → `set_clip_name`) | user-assigned clip names — the one file in this list this repo itself writes |
| `idle_flags.json` | `_idle_flags()` | `idle_scan.py` (not shipped) | `{did: {amp, idle}}` |
| `gait_flags.json` | `_gait_flags()` | `gait_flags_scan.py` (not shipped) | `{did: {gait, conf, partial, transition, pose, ...}}` — see [animation.md](../animation.md) for the gait-classification method |
| `stance_flags.json` | `_stance_flags()` | `stance_scan.py` (not shipped) | `{did: {stance, conf, ...}}` |
| `foot_flags.json` | `_foot_flags()` | `foot_scan.py` (not shipped) | `{did: {posture, conf, ...}}` |
| `dup_groups_<stem>.json` | `_dup_groups(stem)` | `dup_scan.py` (not shipped) | motion-duplicate clip groups per rig stem (`dwm`/`hbf`/`hbm`/`pc89`) |

`clip_names.json` is the only one of these this project's own code writes
to (`set_clip_name`, atomically — see Gotchas); the rest are read-only
inputs, all guarded by a bare `except (OSError, ValueError): return {}` so a
missing or corrupt classifier file degrades to "no hints" rather than a
crash.

### `clips_for_body`

```
clips_for_body(body, cap=400, riding=None, dedupe=True, motion=None)
```

Returns candidate clips for a body's rig as a flat list of rows:
`{did, duration, frames, name, idle, gait, pose, partial, transition,
riding, dup_of, dup_n}`, always sorted by ascending DID.

- `body` maps to a `clips_by_rig.json` bucket via `BODY_STEM` — the three
  named 62-bone rigs (`hbm`/`hbf`/`dwm`) get their own bucket; all four
  89-bone Man/Elf rigs share one anonymous pool (bucket `"?"`, filtered to
  `tracks == 89`, stem `"pc89"`) because those clips carry no annotation
  names that would let the pool be split per rig — positionally, any
  89-track clip fits all four bodies.
- `riding`: `None` = all, `"hide"` = drop mounted clips **before** the cap
  (so `"hide"` spends the whole budget on ground clips instead of losing it
  to the ~281 riding rows in the Man/Elf pool), `"only"` = keep just those.
  `is_riding(row)` fires on either of two independent signals: the gait
  classifier's `gait == "riding?"`, or the pose-graph's high-root pose
  cluster (`"riding" in pose`) — the pose signal catches clips whose gait
  confidence fell short.
- `MOTION_FILTERS`: the dropdown's `motion=` predicates — `loco`, `walk`,
  `run`, `fwd`, `back`, `strafe`, `standing`, `kneel`, `unclassified` —
  matched against the gait label (from foot duty-cycle + swing direction)
  and posture label (from feet-planted + leg geometry).

**`ORDER IS STABLE ACROSS NAMING`**: the returned list is one flat
ascending-DID list; names, the `idle` flag, and gait hints are decorations
only. A user auditions clips sequentially, so naming a clip must not move
"next item" in the list — selection (which clips make the `cap`) is
computed **without** looking at names, then every human-named clip is
unioned in afterward (naming a clip already in the list changes nothing;
naming an off-list clip only inserts it). `"auto:*"` classifier names are
deliberately **not** unioned this way — they're the same hint the `[gait]`
decoration already shows, and unioning them pushed 281 identical-looking
`"auto:riding"` rows past the cap in the 734-entry dropdown.

**Dedupe (`dedupe=True`)**: `dup_scan.py`'s output folds clips whose motion
(a phase-normalised pose fingerprint) matches into one row carrying `dup_n`
(group size); members stay reachable by DID, they just stop padding the
list. **Names do not protect a row from folding** — this was deliberately
revisited after two earlier, opposite-direction bugs from the same false
premise (that every name in `clip_names.json` is a considered human
identification). It is not: `run_a..run_e`, `walk_a..walk_d` and similar
were seeded mechanically from earlier gait identifications, so the letter
suffix is an enumeration index, not a claim that the two clips differ.
Protecting named rows on that basis once left three clips all labeled
"defeat 2h" in the same list — exactly the folding bug this rule now
prevents. The representative for a fold prefers a named member (so the
label survives), then the lowest DID, for stability. The fold threshold
(`eps 0.05`) was chosen because it falls in a genuinely empty gap across the
whole dwm run/walk family (closest same-family pair sits at 0.046, every
other pair 0.087–0.34).

**Classified-clip quota**: duration-band quotas alone are blind to the
classifier — on the 4943-clip `pc89` pool that meant 0 of 183 `run_fwd?` and
only 4 of 32 `walk_fwd?` clips reached a 400-row cap, i.e. a user could not
find a walking-forward animation at all. So anything the foot classifier
actually labeled gets first claim on up to half the cap, spread evenly
across labels (so one populous label like 183 `run_fwd?` rows can't starve a
sparse one like 8 `walk_back?`), *ahead of* the duration-band selection.
After that, the remaining room is split ~30% idle-length (2.5–10 s) clips
and the rest locomotion-length (1.0–2.5 s) clips, then anything left over,
so the large `pc89` pool doesn't starve idle candidates either.

### Default clip / caching

| Function | Signature | Purpose |
|---|---|---|
| `clip_cached` | `clip_cached(did, body)` | decoded clip JSON `{did, duration, frames, fps, tracks}`, cached at `decoded/clip_<did>.json`, for live track-swapping in the viewer without recomposing the mesh |
| `default_clip` | `default_clip(rig, label)` | default clip DID for a body — see priority order below |

`default_clip` priority order: (1) an **explicit** user marker — any clip
named with `"default"` for the body's rig stem, which outranks every
heuristic below deliberately (a user naming a clip "…good default" is a
statement of intent the word-matching below cannot override — the first
such clip was named "walking half strafe good default" and the strafe
exclusion in tier 2 would otherwise have thrown it out); (2) else a
user-assigned or `"auto:*"` name containing `"walk"` (excluding
`back`/`strafe`/`rev`), with user-named clips outranking `auto:*` ones and
`"fwd"` winning within a tier; (3) else `IDLE_CLIPS[rig]`; otherwise `None`
(bind pose, static). `clip_names.json` entries with a name containing
`"walk"` for the body's rig stem **override** the `IDLE_CLIPS` table
entirely — name clips in the UI instead of editing `IDLE_CLIPS`.

## LotroCompanion saved-outfit import

LotroCompanion keeps one directory per character under
`<base>/data/characters/toon-NNNNN/`, with `summary.xml`
(name/server/class/race/sex) and `outfits.xml`
(`<outfits currentIndex><outfit index><element slot visible itemId
itemName colorCode color/>`). `itemId` is the item DID in **decimal**;
`color` is the dye name, matching `dye_colors.json` exactly.

| Function | Signature | Returns |
|---|---|---|
| `companion_base` | `companion_base()` | first `COMPANION_DIRS` entry that actually holds `toon-*/outfits.xml`, or `None` |
| `companion_toons` | `companion_toons()` | `[{dir, name, server, cls, race, sex, level, body, styles, colors, current, outfits}]` for every character with an `outfits.xml` |
| `companion_outfit` | `companion_outfit(toon, index)` | `{body, slots: {slot: {did, name, companion_name, dye, visible}}, skipped, missing}` for one saved outfit resolved against the catalog |

`COMPANION_DIRS = [$LOTRO_COMPANION_DIR, ~/.lotrocompanion/data/characters]`
— `companion_base` requires a directory to actually **hold** outfit data,
not merely exist: since the server process runs as a different OS user than
the player, a bare `~/.lotrocompanion` existence check would resolve to the
service account's own empty profile and shadow the real one.

`COMPANION_SLOTS` maps LotroCompanion's slot names (`HEAD`, `SHOULDER`,
`BREAST`, `BACK`, `HANDS`, `LEGS`, `FEET`) to this project's slot names;
everything else (`MAIN_MELEE`, `RANGED`, `*_AURA`, `CLASS_ITEM`) is a
weapon/effect with no wearable mesh and is reported in `skipped` rather than
silently dropped. `companion_outfit` also reports `missing` — a worn
`itemId` that isn't in the catalog at all.

### Character appearance (optional)

`APPEARANCE_FILES = [$LOTRO_APPEARANCE_JSON,
<repo>/appearance_extracted.json]` — an optional file, produced by separate
tooling **not in this repository**, that extracts a live character's
chargen head/hair/beard style index plus skin/hair/eye/lip colors from the
running client. Absent → the composer defaults every avatar to style 0 and
the manual skin/hair swatches.

| Function | Signature | Returns |
|---|---|---|
| `appearances` | `appearances()` | `{cleaned name: record}` from the appearance file (memoized); keyed on both the raw name (which may carry a `[F]`/`[Fv]` suffix) and the bracket-stripped name |
| `appearance_colors` | `appearance_colors(name)` | `{'skin'/'hair'/'eyes'/'lips': [r,g,b] 0-255}` |
| `appearance_styles` | `appearance_styles(name)` | `{'head'/'hair'/'beard': style index}` |
| `_rgb255` | `_rgb255(v)` | normalizes a color value that may be raw 0-255 ints *or* 0..1 floats — detected by `max(c) <= 1.0`, since the appearance-extraction file has shipped both encodings across its history and a regenerated file must not silently render near-black |

The style indices in the appearance file are confirmed to index the same
chargen tables [charparts.py](charparts.md) reads (`CHARGEN`) because their
`apr_file` DIDs match `charparts.CHARGEN` exactly, verified on two local
characters.

## Composition

| Function | Signature | Returns |
|---|---|---|
| `compose_cached` | `compose_cached(item_did, app_did)` | decoded/ file name for a cached (or freshly built) [compose.py](compose.md) call |
| `compose_skinned` | `compose_skinned(item_did, app_did)` | `(file name, default clip DID)` — compose plus per-vertex skin data + skeleton |
| `compose_face` | `compose_face(label, head_item=None, head_app=None, hands=False, feet=False, head_style=0, hair_style=0)` | `(file name, default clip DID)` — a skinned default head/hair (and optionally bare hands/feet) for a body |

`compose_skinned` resolves the body from `item_did`'s worn-appearance map
(erroring if `app_did` isn't actually one of the item's bodies), looks up
`RIGS[label]`, and caches to `decoded/animS_<item>_<app>.json`. Its output
is `compose_cached`'s JSON plus flat `skinIndices`/`skinWeights` (4 per
vertex), `rig`, and `bones` (`[{name, parent, t, q, s}, ...]`).

**The clip payload is deliberately not embedded** in that cached file: a
clip is identical for every slot of one outfit, so embedding it would turn
~80% of a 7-slot set's ~8 MB download into seven redundant copies of the
same ~0.9 MB clip. Instead, the viewer fetches the clip once from
`/clip` and geometry stays clip-independent — one composed mesh now serves
every clip instead of needing a recompose on every default-clip change.
`clip_did` is resolved **per request** via `default_clip`, not baked into
the cache, so renaming a clip's default status takes effect without a
recompose.

`compose_face` figures out what hair to show via `_hair_decision`
(`head_item`/`head_app`, the equipped Head item, if any): no hair-slot part
in the item's `0x20` entry → chargen hair; a stub mesh → no hair (helmet
hides it); a real mesh → that item's fitted hair. See
[../hair-face.md](../hair-face.md) for the underlying three-state
mechanism. It composes the chosen chargen `head_style`/`hair_style` (via
[charparts.append_style](charparts.md)) plus, if `hands`/`feet` are
requested, the body's bare-hands/bare-feet mesh from `HANDS_MESH`/
`FEET_MESH` below — each submesh group is tagged `part: "head"/"hair"/
"hands"/"feet"` so the front-end can pick out, e.g., which groups get a
skin tint. Cached at `decoded/animF_<body>_h<style>_<style>_<mode>[_hands]
[_feet].json`.

`CHARGEN_KEY` maps this module's body labels (`"Man-M"`, …) to
[charparts.py](charparts.md)'s `CHARGEN` dict keys (`"man_m"`, …).

## `slot_overrides` + `OVERRIDE_TAGS` + the bare-part tables

```
slot_overrides(item_did, app_did) -> {"Legs": "blank"|"provided", ...}
```

Reports which *other* equipment slots a chest garment blanks or supplies
geometry for — same three-way part-tag mechanism the head family uses for
hair ([../hair-face.md](../hair-face.md)): tag absent → that slot's own
item renders normally; tag = stub (< `STUB_BYTES` = 2000 B) → that slot is
blanked (a floor-length dress hides leggings); tag = real mesh → the chest
item supplies that slot's geometry (hauberks).

```python
OVERRIDE_TAGS = {0x10000003: "Legs", 0x10000006: "Feet", 0x10000001: "Hands"}
```

Tag→slot was confirmed by where each mesh actually sits on the body (z
height in bind pose), not guesswork — a survey of 172 distinct chest
entries found 122 blank the legs, 8 blank the feet too (dresses/robes), 1
supplies real leg geometry (a hauberk), 49 touch neither. The `0x10000001`
(hands) tag was initially misread as a collar from its thin z-band (1.37 to
1.42) — that band is actually the bind-pose T-pose hand height, and the
count settles it: across 235 Elf-F chest entries there is exactly one
distinct `0x10000001` mesh, i.e. a shared per-body part, not garment
sleeves.

```python
HANDS_MESH  = {...}   # per body: the bare-hands mesh, used when no worn
                       # garment supplies hands and no Hands item is equipped
SKIN_SURFACE = {...}  # per body: the skin-surface DID that makes a part BARE
FEET_MESH   = {...}   # per body: the bare-feet mesh (Elf-M has none in the
                       # shipped data — left out rather than borrowing
                       # another body's, a mistake this table already made
                       # once)
```

`HANDS_MESH` exists because a chest garment supplies arm skin only down to
the wrist — hands come from a separate part, so a garment carrying no
`0x10000001` (e.g. Dwarf Quilted Waistcoat of Might) would otherwise leave
the arms ending in stumps. Derived by surveying every chest entry per body
and taking the most common non-stub `0x10000001` mesh (Man/Elf agree
unanimously across all 142 entries each; Dwarf/Hobbit carry a stub in most
entries with one real mesh behind it).

`FEET_MESH` was found on the **second** attempt — two earlier approaches
were wrong in instructive ways: "most common non-stub `0x10000006` per
body" found a garment's *shoes*, not a bare foot (that statistic can't tell
the two apart); and filtering on the skin surface while scanning only
*chest* entries concluded no bare feet exist anywhere — they do, in the
Legs/Feet families. The test that actually works (shared with
`HANDS_MESH`): a bare body part routes to the body's `SKIN_SURFACE`
(matching the independently recorded per-body skin surfaces in the format
docs), footwear routes to a cloth surface; then require foot geometry (z
below ~0.15, rising no higher than ~0.30) so a full leg doesn't qualify.

## Dyes / texture baking

| Function | Signature | Returns |
|---|---|---|
| `dyes` | `dyes()` | the `dye_colors.json` table |
| `dyed_texture` | `dyed_texture(tex_did, dye)` | cached `textures/dyed_<TEXHEX>_<dyename>.png` file name |
| `skin_tinted_texture` | `skin_tinted_texture(tex_did, rgb)` | cached `textures/skin_<TEXHEX>_<RRGGBB>.png` file name |

Both baking functions use the same low-alpha-is-tintable convention as
[../dyes.md](../dyes.md): pixels with alpha < 128 get multiplied by the
target RGB, pixels with alpha ≥ 128 are kept as-authored. `skin_tinted_texture`
documents the measurement behind this for faces specifically: on the Elf-F
head texture (`0x4125A714`) the face centre is 0% high-alpha (fully
tintable, near-white — clearly authored as a neutral skin map) while the
eye/eyebrow patches in the same atlas are 45%/38% high-alpha and so survive
the tint — one texture carries a skin-tintable face plus features that must
never be tinted.

## Gotchas

- **Atomic cache writes.** `clip_cached`, `compose_skinned`/`compose_face`
  (via `_dump_atomic`), `dyed_texture`, and `skin_tinted_texture` all write
  to a `.tmp` (or, for `_dump_atomic`, a PID+thread-suffixed `.tmp`) file
  and `os.replace()` it into place, never writing the final path directly.
  Both servers run `threaded=True`, so two requests can race to compose the
  same file concurrently; a half-written file would otherwise be served to
  the browser as truncated JSON (a 500) or a corrupt PNG. `clip_cached` is
  additionally hardened to **read** its own truncated cache: a `ValueError`
  on `json.load` deletes the file and rebuilds rather than serving garbage
  forever (the failure mode a kill-mid-write cache file used to hit before
  the write side was made atomic).
- **The clip payload is not embedded in composed mesh JSON** (see
  `compose_skinned` above) — a caller expecting one self-contained file per
  composed mesh will not find animation data there; it must separately
  fetch `/clip`.
- **`clips_for_body`'s dedupe intentionally does not protect named rows**
  from folding — see the dedupe section above; this was a deliberate
  reversal of an earlier, more "protective" version that produced worse
  results.
- **`auto:*` classifier names never get unioned back into a capped clip
  list** — only human-assigned names do (see the classified-clip quota
  section) — a caller wondering why a classifier-labeled clip is missing
  from a capped list should check whether it's an `auto:*` name.
- All of the classifier data files (`idle_flags.json`, `gait_flags.json`,
  `stance_flags.json`, `foot_flags.json`, `dup_groups_*.json`) are read-only
  **hints** here — the scan scripts that produced them are not shipped in
  this repository (see the data-files table above), so they cannot be
  regenerated or extended without that external tooling. `clip_names.json`
  is the sole exception: it's written by this project's own `/clipname`
  route and is expected to grow over time as clips get named through the
  UI.

## See also

- [outfit_app.py](outfit_app.md) — the Flask server whose routes are thin
  wrappers over nearly every function on this page.
- [charparts.py](charparts.md) — the chargen head/hair/beard compositor
  `compose_face` calls into.
- [compose.py](compose.md) — the single-entry compositor `compose_cached`/
  `compose_skinned` wrap and cache.
- [items_catalog.py](items_catalog.md) — builds `items_catalog.jsonl` and
  bakes the per-body `present` flags `search`/`setmates`/`sets_index` all
  require.
- [export_skinned.py](export_skinned.md) — skeleton/clip export
  `compose_skinned`/`clip_cached`/`compose_face` call into.
- [../animation.md](../animation.md) — the gait/idle/dup classification
  concepts `gait_flags.json`/`idle_flags.json`/`dup_groups_*.json` encode.
- [../hair-face.md](../hair-face.md) — the three-state hair mechanism
  `_hair_decision` and `OVERRIDE_TAGS`' Hands tag both implement.
- [../dyes.md](../dyes.md) — the alpha-tint-mask math shared by
  `dyed_texture`/`skin_tinted_texture`.
- [INDEX.md](INDEX.md) — full script index.
