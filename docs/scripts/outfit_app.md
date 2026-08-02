# outfit_app.py

[`outfit_app.py`](../../outfit_app.py) · [`outfit.html`](../../outfit.html)

## Purpose

`outfit_app.py` is the outfit composer server — a thin Flask layer over
[api_common.py](api_common.md) that serves `outfit.html` (the multi-slot
avatar front-end) on port 8723. It is the server counterpart to
[../outfit-composer.md](../outfit-composer.md), which walks the front-end
UI itself; this page documents the routes.

The repo's simpler single-item [viewer](viewer.md) (`app.py`) stays on port
8722 and covers rendering one item on one body at a time; `outfit_app.py`
composes a full multi-slot outfit (every equipment slot at once, dyes per
slot, saved-outfit import, animation) on the same underlying data-layer
modules and the same `decoded/`/`textures/` disk caches.

## Running it

```
python3 outfit_app.py [--host H] [--port P] [--game-dir DIR]
```

| Argument | Meaning |
|---|---|
| `--host` | bind host (default `$OUTFIT_HOST` or `127.0.0.1`) |
| `--port` | bind port (default `$OUTFIT_PORT` or `8723`) |
| `--game-dir` | LOTRO install directory |

```
python3 items_catalog.py     # once: builds items_catalog.jsonl, which /search etc. require
python3 outfit_app.py
# open http://127.0.0.1:8723/
```

To load saved outfits from LotroCompanion, its data must be at
`~/.lotrocompanion` (the default install location) or pointed to with
`$LOTRO_COMPANION_DIR` (the `.../data/characters` directory) — see
[api_common.py](api_common.md#lotrocompanion-saved-outfit-import) for the
directory-resolution details. Runs `threaded=True`, same concurrency model
as [viewer.md](viewer.md)'s `app.py`.

## Routes

| Route | Method | Params | Returns |
|---|---|---|---|
| `/` | GET | — | serves `outfit.html` |
| `/search` | GET | `q`, `slot` (optional) | JSON array of ranked item rows — `api.search(q, slot=slot)` |
| `/sets` | GET | `q`, `weight` (optional: `light`/`medium`/`heavy`) | JSON array of matching armour sets — `api.search_sets(q, weight=weight)` |
| `/setmates` | GET | `did` (hex) | `{stem, slots: {slot: [row...]}}` — `api.setmates(did)` |
| `/toons` | GET | — | `{base, toons}` — LotroCompanion base dir + `api.companion_toons()` |
| `/outfit` | GET | `toon`, `index` | `{body, slots, skipped, missing}` — `api.companion_outfit(toon, index)`; `{error}` + 400 on failure |
| `/item` | GET | `did` (hex) | one catalog row — `api.item_row(did)`; `{error: "unknown item"}` + 404 if not found |
| `/bodies` | GET | `item` (hex) | `[{app, key, present, label}]` — `api.bodies_for(item)` (live, not catalog-cached) |
| `/compose` | GET | `item`, `app` (hex) | `{file, overrides}` — `api.compose_cached` + `api.slot_overrides`; `{error}` + 500 on failure |
| `/compose_skinned` | GET | `item`, `app` (hex) | `{file, clip_did, overrides}` — `api.compose_skinned` + `api.slot_overrides`; `{error}` + 500 |
| `/compose_face` | GET | `body`, `head_item`/`head_app` (hex, optional), `hands` (`1`/`0`), `feet` (`1`/`0`), `head_style`, `hair_style` | `{file, clip_did}` — `api.compose_face`; `{error}` + 500 |
| `/clips` | GET | `body`, `riding` (`hide`/`only`), `dedupe` (`1`/`0`, default `1`), `motion` | JSON array of clip rows — `api.clips_for_body`; `{error}` + 500 |
| `/clip` | GET | `did` (int, base auto-detected), `body` | decoded clip JSON — `api.clip_cached`; `{error}` + 500 |
| `/clipname` | GET/POST | `did`, `name`, `body` (optional) | `{did, entry}` — `api.set_clip_name`; `{error}` + 500 |
| `/dyes` | GET | — | the full `dye_colors.json` table — `api.dyes()` |
| `/skintex` | GET | `tex` (hex), `rgb` (`"r,g,b"`) | `{file}` — `api.skin_tinted_texture`; `{error}` + 500 |
| `/dyedtex` | GET | `tex` (hex), `dye` | `{file}` — `api.dyed_texture`; `{error}` + 500 |
| `/decoded/<f>` | GET | — | static file from `api.DECODED` |
| `/textures/<f>` | GET | — | static file from `api.TEXTURES` |

Every route that can raise (composition, texture baking, outfit parsing)
catches `Exception` and returns `{"error": str(ex)[:200]}` with an HTTP
error status rather than a stack trace — the same defensive pattern
[viewer.md](viewer.md)'s `app.py` uses for its own on-demand routes.

### `/compose_face` — hands/feet flags

The `hands=1`/`feet=1` flags ask `api.compose_face` to also emit the body's
bare-hands/bare-feet mesh. The **caller** (the front-end) is expected to
request each only when no worn garment supplies or blanks that part *and*
no item occupies the slot — see `outfit.html`'s `need('Hands')`/
`need('Feet')` logic and [api_common.py](api_common.md#slot_overrides--override_tags--the-bare-part-tables)'s
`HANDS_MESH`/`FEET_MESH` tables. Dwarf/Hobbit have no bare-feet mesh in the
shipped data (`FEET_MESH` has no `Elf-M` entry either) — requesting `feet=1`
for a body with no table entry is a no-op (no feet part appended), not an
error.

### `/clips`

`riding=hide`/`riding=only` filters mounted clips **before** the row cap;
`dedupe=0` disables the motion-duplicate folding; `motion=` filters by the
foot/gait classification (`walk`/`run`/`fwd`/`back`/`strafe`/`standing`/
`kneel`/`loco`/`unclassified`). See
[api_common.py](api_common.md#clips_for_body) for the full selection logic
(duration-band quotas, classified-clip quota, name-union rules).

## See also

- [api_common.py](api_common.md) — every function backing these routes.
- [charparts.py](charparts.md) — the chargen compositor `/compose_face`
  calls into via `api.compose_face`.
- [items_catalog.py](items_catalog.md) — builds `items_catalog.jsonl`,
  required by `/search`, `/sets`, `/setmates`.
- [viewer.md](viewer.md) — the single-item subset of this server (`app.py`,
  port 8722).
- [../outfit-composer.md](../outfit-composer.md) — the front-end UI
  walkthrough (what each control does, the saved-outfit loader).
- [INDEX.md](INDEX.md) — full script index.
