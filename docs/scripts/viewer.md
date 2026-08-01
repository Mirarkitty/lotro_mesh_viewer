# viewer.py — app.py, index.html, anim.html

[`app.py`](../../app.py) · [`index.html`](../../index.html) · [`anim.html`](../../anim.html)

## Purpose

The viewer is the render stage of the pipeline: item → PropertiesSet → worn
appearance → mesh + material → texture → dye → **render**. `app.py` is a
small local HTTP server (Flask if installed, a stdlib `http.server`
fallback otherwise) that serves the two three.js front-ends
(`index.html` — static mesh/item browser; `anim.html` — skinned animation
playback) plus the `decoded/`/`textures/` caches, and — when Flask is
available — a small set of on-demand API routes that call directly into
[selector.py](selector.md), [wearable2.py](wearable2.md),
[compose.py](compose.md), and [tex_extract.py](tex_extract.md) so a user can
search for an item by name and see it rendered without running any CLI tool
by hand.

## Running it

```
python3 app.py [--host H] [--port P] [--game-dir DIR] [--out-dir DIR]
```

| Argument | Meaning |
|---|---|
| `--host` | bind host (default `$VIEWER_HOST` or `127.0.0.1`) |
| `--port` | bind port (default `$VIEWER_PORT` or `8722`) |
| `--game-dir` | LOTRO install directory |
| `--out-dir` | output root for `decoded/`/`textures/` |

```
python3 app.py
# open http://127.0.0.1:8722/        (mesh + item viewer)
# open http://127.0.0.1:8722/anim    (animation viewer)
```

Item search (`/search`) requires `items_catalog.jsonl` to already exist —
build it once with [items_catalog.py](items_catalog.md).

Flask, if installed, is run with `threaded=True` — safe because
[datfile.py](datfile.md)'s `DatFile` handles are internally lock-protected
(see that page's Locking section), so concurrent requests (e.g. two
`/compose` calls) can share the same cached archive handles from
[config.py](config.md) without corrupting each other's reads.

## Routes (Flask mode)

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | serves `index.html` |
| `/anim` | GET | serves `anim.html` |
| `/list` | GET | JSON list of everything in `decoded/`: `{id, file, verts, tris}` per mesh (or `{id, file, error}` if a JSON file fails to load) |
| `/decoded/<f>` | GET | static file from `decoded/` |
| `/textures/<f>` | GET | static file from `textures/` |
| `/search?q=` | GET | fuzzy item-name search over `items_catalog.jsonl` (min 2 chars); returns up to 100 ranked, deduped hits |
| `/bodies?item=<hex>` | GET | per-body renderability for one item — `{app, key, present, label}` list |
| `/dyes` | GET | the full `dye_colors.json` table |
| `/dyedtex?tex=<hex>&dye=<name>` | GET | dye-tinted PNG for a texture, generated and cached on first request |
| `/compose?item=<hex>&app=<hex>` | GET | on-demand [compose.py](compose.md) call; returns `{file}` (or `{error}`, HTTP 500) |

Stdlib fallback mode (no Flask) only serves `/`, `/list`, `/decoded/<f>`,
and `/textures/<f>` — no search/compose/dye routes.

### `/search`

Filters `items_catalog.jsonl` rows by requiring every whitespace-split query
term to be a substring of the lowercased item name, and requiring at least
one body entry to be `present` (i.e. actually renderable — computed once,
lazily cached per worn-appearance record in `_wrec_cache`, via the same
`entry_renderable` logic `/bodies` uses). Results are ranked (exact match,
then prefix match, then substring) and deduplicated by `(display_name,
first_body_key)` — display names have a trailing `[bracket]` suffix
stripped for the ranking/dedup key via `_re.sub(r"\[[a-z]+\]$", "", name)`.

### `/bodies`

For one item DID, parses each candidate body's `0x20` record (cached in
`_wrec_cache`, keyed by the worn-appearance DID) and reports whether that
body's entry is renderable (`entry_renderable`: true only if the entry's
main `GARMENT_TAG` (`0x1000000C`) part — or, if none, any part — is present
and larger than the 2000-byte stub-size threshold, mirroring
[selector.py](selector.md)'s and [compose.py](compose.md)'s stub-size
convention).

### `/dyedtex`

Loads the base PNG (extracting it via [tex_extract.py](tex_extract.md) if
not already cached), and for every pixel with alpha `< 128` — the low-alpha
convention marking dyeable cloth — multiplies its RGB by the chosen dye's
normalized `(r,g,b)` from `dye_colors.json`, then flattens to opaque RGB and
caches the result as `dyed_<TEXHEX>_<dyename>.png`. See
[../dyes.md](../dyes.md) for the render-math background.

### `/compose`

Caches by `compose_<ITEMHEX>_<APPHEX>.json` filename — if that file already
exists in `decoded/`, it's returned immediately without recomputation;
otherwise it calls `compose.compose(item, app_did, name)` and reports any
exception's message (truncated to 200 chars) as a 500 JSON error.

## index.html — the mesh/item viewer

A single-page three.js (r0.160, loaded via CDN import map — no bundler)
app. Renders via `THREE.WebGLRenderer`, `OrbitControls`, and an IBL
environment (`RoomEnvironment` via `PMREMGenerator`) plus a 3-light rig
(key/fill/rim) tuned for three r155+'s physically-based light intensities.

**Controls:**

| Control | Effect |
|---|---|
| item search box (`#q`) | debounced (250ms) `/search` query; click a result to load it |
| body dropdown (`#body`) | choose which species/sex body to render the selected item on; disabled options (✗) are non-renderable bodies |
| dye dropdown (`#dye`) | applies a dye via `/dyedtex`; persisted to `localStorage` |
| skin dropdown (`#skin`) | flat skin-tone color choices (`fair/tan/brown/dark`); persisted to `localStorage` |
| dev mesh picker (`#pick`) | raw `decoded/*.json` file picker, for meshes not reached via item search |
| reload button (`#reload`) | re-fetch `/list` and reload the current mesh in place |
| wireframe checkbox | toggles `MeshStandardMaterial.wireframe` on all materials |
| spin checkbox | auto-rotates the loaded mesh |
| flipV checkbox | toggles UV V-axis handling (see Gotchas) |

**Rendering pipeline** (`load(file)`): fetches `/decoded/<file>`, builds a
`BufferGeometry` from `vertices`/`triangles`/`normals` (computed if absent)
and `uvs`, rotates the geometry `-90°` about X (LOTRO is Z-up, three.js is
Y-up), and builds **one material per distinct texture DID** referenced
across `d.groups`, mapped via `geometry.addGroup(tri_start*3, tri_count*3,
materialIndex)` so each submesh renders with its own texture. A group whose
`texture === "skintone"` gets a flat-color material (updated live by
`applyDye`'s skin-color logic); groups with no texture get a plain blue
fallback material.

**Live polling** (`poll()`, every 5s): re-fetches `/list`; if the currently
loaded mesh's vertex/triangle counts changed on disk (a decoder re-run), it
reloads the SAME file in place (`doFit=false`) without resetting the
camera — lets you iterate on a decoder and watch the render update without
manually reselecting or losing your viewing angle.

## anim.html — the animation viewer

Same three.js setup/lighting rig as `index.html`, extended for skinned,
posed rendering.

**Controls:**

| Control | Effect |
|---|---|
| play/pause button | toggles clip playback |
| time slider (`#tslider`) | scrub to a specific point in the clip (0–1000 mapped to `0..duration`); scrubbing pauses playback |
| bind pose checkbox | freezes the skeleton at its rest pose instead of the animated pose |
| wireframe checkbox | same as index.html |

Loads `?f=<decoded/*.json filename>` (default
`anim_dress_83893544.json`) — the output of
[export_skinned.py](export_skinned.md). Builds a `THREE.Bone` hierarchy
directly from `d.bones` (parented via each bone's `parent` index, `-1` =
root), builds a `SkinnedMesh` with `skinIndex`/`skinWeight` attributes from
`d.skinIndices`/`d.skinWeights`, `mesh.bind()`s the skeleton, and rotates
the **mesh object** `-90°` about X (not the geometry, unlike `index.html`)
so bones and geometry share the same transform space.

Per-frame pose (`applyPose(t)`): linear-interpolates position/scale and
`slerpQuaternions` for rotation between the two nearest clip frames for
every bone, driven by a `requestAnimationFrame` loop that renders every
frame unconditionally (no dirty-flag optimization, unlike `index.html`'s
on-demand rendering — animation always needs continuous redraws).

Exposes `window.animState()`, `window.setTime(t)`, and `window.setView(ang)`
— hooks specifically for scripted screenshot tooling (see
[screenshot.py](screenshot.md)) to pause at a known time and orbit the
camera to a known angle before capturing.

## Gotchas & lessons

- **`flipV` / UV orientation is a real, previously-confirmed sticking
  point.** The code comment states outright: `flipV=true` (raw V, no flip)
  is the *correct* orientation for worn-appearance meshes, confirmed
  visually (brown bodice sits on the chest, skirt aligns correctly) — the
  `v -> 1-v` flip is the WRONG default for these meshes despite being what
  you'd expect for typical DDS/DXT top-down texture data. `anim.html` does
  not expose a flipV toggle at all and always uses raw V — keep this
  consistent if a mesh ever needs the alternate orientation there.
- **`/compose` and `/dyedtex` cache by filename, forever** — neither route
  invalidates its cached output file if the underlying decoded mesh or
  texture changes; delete the corresponding `decoded/compose_*.json` or
  `textures/dyed_*.png` by hand to force recomputation.
- **The stdlib fallback has no search/compose/dye support** — running
  without Flask installed silently degrades to a raw mesh browser; there's
  no error surfaced in the UI telling the user why the search box does
  nothing (because the search box's fetch to `/search` will 404 with the
  stdlib handler).
- **`index.html`'s on-demand rendering vs `anim.html`'s continuous
  rendering** is a deliberate difference, not an oversight — a static mesh
  viewer benefits from idling at 0% CPU between camera moves; an animation
  viewer cannot, since the pose itself changes every frame during
  playback.
- **`/dyedtex`'s per-pixel Python loop** (`for y ... for x ...`) is O(w×h)
  pure-Python — fine for the small garment textures this toolkit targets,
  but would need vectorizing (à la [mesh_decode.py](mesh_decode.md)'s
  `_vertex_mask`) if ever pointed at large highres textures.

## See also

- [config.py](config.md) — the cached archive handles that make `threaded=True` safe.
- [compose.py](compose.md), [selector.py](selector.md), [wearable2.py](wearable2.md) — the modules `/compose` and `/bodies` call into.
- [items_catalog.py](items_catalog.md) — builds `items_catalog.jsonl`, required for `/search`.
- [export_skinned.py](export_skinned.md) — produces the JSON `anim.html` renders.
- [tex_extract.py](tex_extract.md) — texture extraction underlying `/dyedtex`.
- [screenshot.py](screenshot.md) — headless automation of this viewer for visual verification.
- [../dyes.md](../dyes.md) — dye render-math background for `/dyedtex`.
- [INDEX.md](INDEX.md) — full script index.
