# compose.py

[`compose.py`](../../compose.py)

## Purpose

`compose.py` is the "first-cut avatar compositor" — it takes one wearable
**entry** (an item worn on a specific body) and merges all of that entry's
present parts (garment + hands + ...) into a single, textured viewer JSON.
This is the final assembly stage before rendering: item → PropertiesSet →
worn appearance → mesh + material → texture → dye → **render**. It is the
module [viewer.md](viewer.md)'s `/compose` route calls on demand when a user
picks an item + body in the browser UI.

It performs four things beyond plain mesh decoding:
1. **LOD dedup** — per part, per surface DID, keep only the largest-vertex
   submesh (multiple LOD levels of the same surface can be present in one
   mesh record; only one should render).
2. **Skin/cloth texture routing** — per submesh, based on the GfxObj's own
   surface DID: known skin surfaces get a flat skin-tone placeholder PNG;
   cloth surfaces get the entry's resolved material diffuse.
3. **Shader/render-hint export** — per submesh, resolves the surface's
   `0x2B` shader (via [shaders.py](shaders.md)) and attaches
   `{shader, shader_did, alpha_test, metallic, dyeable}` to the group so a
   downstream renderer can distinguish an alpha cutout from an opaque tint
   mask, and cloth from metal, instead of treating every surface as flat
   opaque cloth.
4. Optional **skin (bone) array export** for animated rendering, sliced in
   lockstep with the vertex arrays it emits.

## CLI usage

```
python3 compose.py <item_did_hex> <worn_appearance_hex> <outname> [--game-dir DIR] [--out-dir DIR]
```

| Argument | Meaning |
|---|---|
| `item` | item DID, hex (e.g. `0x7000DA5B`) |
| `worn_appearance` | the body's `0x20` worn-appearance DID, hex (from [selector.py](selector.md)'s output, e.g. `0x20001E58`) |
| `outname` | output name → `decoded/<outname>.json` |

Example (from the module docstring):

```
python3 compose.py 0x7000DA5B 0x20001E58 compose_exq_dwarfM
```

## Public API

| Function | Signature | Returns |
|---|---|---|
| `compose` | `compose(item_did, app_did, outname, skin_bones=None, write=True)` | the composed viewer-JSON dict; also writes `decoded/<outname>.json` when `write=True` |
| `is_skin` | `is_skin(surf, all_surfs)` | `True` if `surf` should be textured with the skin-tone placeholder rather than the garment diffuse |
| `skin_png` | `skin_png(path)` | writes a tiny flat-color PNG (the skin-tone placeholder) atomically to `path` |
| `_shader_fields` | `_shader_fields(surf_did)` | `{shader, shader_did, alpha_test, metallic, dyeable}` for one surface's `0x2B` shader (empty dict if the shader can't be resolved) — see [shaders.md](shaders.md) |

`compose`'s `skin_bones` parameter: when set (a skeleton bone count), the
output also carries flat `skinIndices`/`skinWeights` (4 per vertex,
[export_skinned.py](export_skinned.md)'s `skin_arrays` layout), sliced in
lockstep with the vertex arrays. `write=False` skips the `decoded/` dump and
just returns the dict — used when a caller (e.g. an animation-export helper)
wants to add bones + clip data before writing.

Output shape: `{id, vertices, normals, uvs, triangles, groups, num_submeshes}`
(plus `skinIndices`/`skinWeights` if `skin_bones` was given). Each group
carries `texture` (a texture DID string, `"skintone"`, or `None`), a
`texture_source` string recording which part/surface it came from — useful
for debugging a wrong-looking render — and, when the submesh's surface
resolves to a known `0x2B` shader, the render hints from `_shader_fields`:
`shader` (the classified name, e.g. `"cutout_hair"`), `shader_did`,
`alpha_test`, `metallic`, and `dyeable` (all booleans except the two DID/name
strings). See [scripts/shaders.md](shaders.md) for the reference
implementation and [../shaders.md](../shaders.md) for the format-level
writeup these hints come from.

## How it works internally

1. Looks up the item's body entry via `selector.appearance_map`, matching
   `worn_appearance == app_did`.
2. Parses the **full** `0x20` record via `wearable2.parse_record` and finds
   the entry whose `key` matches the selector's key, then takes `blocks[0]`
   — the first dye block (the undyed base, per [wearable2.py](wearable2.md)).
3. Resolves the block's material (`tx.material_diffuse`) to get the cloth
   texture, and pre-generates the flat skin-tone placeholder PNG.
4. **Guards against unshipped garments up front**: if the block has a
   `0x1000000C`-tagged (garment) part and NONE of those parts' meshes are
   both present and larger than the 2000-byte stub-size threshold, it
   raises immediately rather than silently composing an incomplete outfit.
5. Iterates every part in the block (deliberately **not** filtered by tag —
   see Gotchas), decoding each present, non-stub mesh
   ([mesh_decode.py](mesh_decode.md)'s `decode_mesh`, `with_textures=False`
   since routing is done locally here) and its surface list
   (`tex_extract._mesh_surfaces`).
6. LOD dedup: for each decoded mesh's groups, keeps only the group with the
   largest `vert_count` per distinct surface DID (`best = {}` keyed by
   surface).
7. For each surviving group, appends its vertices/normals/UVs/triangles
   (index-offset by the running vertex count) into the merged output, and
   decides its texture via `is_skin` → skin-tone, else the entry's cloth
   texture, else (for newer items with no material groups) resolves the
   submesh's own surface diffuse directly (`_surface_diffuse`, cached in
   `_SURF_DIFFUSE_CACHE`).
8. Sanitizes non-finite/huge floats to `0.0` in vertices/normals/UVs — plain
   Python `json.dump` writes `NaN`/`Infinity`, which JavaScript's
   `JSON.parse` rejects, so any non-finite coordinate would otherwise break
   the browser viewer silently.
9. Raises if no groups ended up shipped at all (a full data hole for this
   item on this body), otherwise writes `decoded/<outname>.json`.

### Skin surface identification (`is_skin` / `SKIN_SURFS` / `CLOTH_SURFS`)

`CLOTH_SURFS = {0x31002DCD}` — a **global** cloth surface confirmed shared
across both Dwarf and Elf meshes. `SKIN_SURFS = {0x3100015D, 0x310092EA}` —
per-body skin surfaces (Dwarf-M, Elf-F). `is_skin` treats a surface as skin
if it's a known skin surface, OR if it's the "other" surface in a
two-surface garment mesh where the other one is a known cloth surface (the
common sleeveless/short-sleeved garment case: one surface for cloth, the
partner surface for exposed skin).

## Gotchas & lessons

- **Do not filter parts by tag when composing a full outfit.** The
  `0x1000000C`/`0x10000003`/etc. tag values are per **record family**:
  `0x10000003` is a 3-vertex placeholder stub in the chest family but is
  THE GARMENT mesh in the legs family. Filtering by a fixed tag set (as
  [selector.py](selector.md) does for its narrower single-garment lookup)
  would silently drop legitimate parts here. `compose` instead includes
  every part and relies on the **size guard** (`e[2] < 2000` on-disk bytes
  → skip) to drop placeholder stubs regardless of their tag.
- **The stub-size guard (2000 bytes) is load-bearing twice**: once as a
  fast per-part skip during iteration, and once as the up-front "is this
  garment shipped at all" check before doing any work — both use the same
  threshold, so keep them in sync if it's ever retuned.
- **NaN/Inf sanitization matters for the browser, not just Python.**
  `json.dump` happily serializes `NaN`, but the browser's `JSON.parse` in
  [viewer.md](viewer.md)'s `index.html`/`anim.html` does not accept it —
  without the sanitize step here, a single non-finite float anywhere in a
  composed mesh would break the whole page's fetch.
- **PNG writes are atomic** (`skin_png` writes to a `.d.tmp` suffixed path,
  then `os.replace`s it) because `textures/` is served live by
  [viewer.md](viewer.md) with `threaded=True` while a compose may be
  in-flight.
- **`_shader_fields` fails soft.** If the surface's shader can't be resolved
  (unknown DID, archive lookup failure), it returns `{}` rather than
  raising, so a group missing `shader`/`alpha_test`/etc. means "shader
  unresolved," not "surface has no shader" — the two are not
  distinguishable from the output alone.
- **The real skin atlas is not implemented here.** The skin-tone
  placeholder is explicitly a placeholder — the module docstring notes the
  real skin ATLAS comes from the body's `0x01` Setup record, future work
  not yet done; the flat tone just makes sleeveless/short-sleeved garments
  read correctly in the meantime.

## See also

- [selector.py](selector.md) — resolves the item→body binding this module composes.
- [wearable2.py](wearable2.md) — the strict record parser this module reads blocks/parts/groups from.
- [mesh_decode.py](mesh_decode.md) — decodes each individual part mesh.
- [tex_extract.py](tex_extract.md) — resolves material/surface diffuses.
- [shaders.py](shaders.md) — classifies the `0x2B` shader `_shader_fields` reads.
- [export_skinned.py](export_skinned.md) — the `skin_bones` code path's sibling for full animation export.
- [viewer.md](viewer.md) — the `/compose` route that calls this module on demand.
- [../wardrobe.md](../wardrobe.md), [../dyes.md](../dyes.md), [../shaders.md](../shaders.md) — format background.
- [INDEX.md](INDEX.md) — full script index.
