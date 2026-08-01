# selector.py

[`selector.py`](../../selector.py)

## Purpose

`selector.py` is the item → garment mesh(es) + material **selector**: given
an item DID, it resolves, per body type (species × sex), which mesh(es) and
material that item actually draws. This is the pipeline stage between
PropertiesSet parsing and mesh decoding: item → PropertiesSet → **worn
appearance** → mesh + material → texture → dye → render.

It supersedes an earlier byte-scanning heuristic that read the wrong
`AppearanceKey` and mis-parsed the `0x20` record's "slot" field — that
heuristic happened to work for the Summer Dress but failed for the
Exquisite Dress. This version parses the item's Turbine PropertiesSet
exactly via [propset.py](propset.md) and then reads the correct per-body
worn-appearance binding. Both dresses are verified to resolve to distinct,
coherent garments. See [../wardrobe.md](../wardrobe.md) for the full
item→appearance chain writeup.

## Chain summary

```
item 0x70______  (client_gamelogic)
  -> PropertiesSet at itemDID + 0x09000000 (see propset.py)
  -> Item_WornAppearanceMapList: ARRAY of STRUCT, one per (species, sex) body
       Item_SpeciesOfWearer (race enum, e.g. 23 = human)
       Item_SexOfWearer     (4096 = male, 8192 = female)
       Item_AppearanceKey   (the SELECTOR value)
       Item_WornAppearance  (the 0x20 wardrobe DID)
     (top-level PhysObj = base-body fallback)

worn-appearance 0x20 record (client_general) = per-body-type draw-entry table.
  THE SELECTOR: the draw entry whose slot field == Item_AppearanceKey.
  -> garment mesh, optional attach mesh, material (0x30)
  -> material's diffuse texture via tex_extract.material_diffuse
```

Verified results (n=2, distinct):

| Item | DID | AppearanceKey | Material | Diffuse |
|---|---|---|---|---|
| Sleeveless Summer Dress | `0x70021A13` | `0x10000765` | `0x300043D2` | `0x41105076` |
| Exquisite Dress | `0x7000DA5B` | `0x10000417` | `0x30003119` | `0x410DD7A5` |

## CLI usage

```
python3 selector.py [item] [--game-dir DIR] [--out-dir DIR]
```

| Argument | Meaning |
|---|---|
| `item` | item DID, hex (default `0x70021A13`, the Sleeveless Summer Dress) |

Examples:

```
python3 selector.py                      # Summer Dress, all bodies
python3 selector.py 0x7000DA5B           # Exquisite Dress, all bodies
```

Output: one line per body listing the worn-appearance DID, species, sex,
garment mesh (flagged `(INDIRECTION-unshipped)` if not a direct file),
hands/attach mesh, material, and resolved diffuse; then the distinct set of
garment materials across all bodies.

## Public API

| Function | Signature | Returns |
|---|---|---|
| `appearance_map` | `appearance_map(item_did)` | `(list_of_dicts, phys_obj)` — `Item_WornAppearanceMapList` parsed to `{species, sex, key, worn_appearance}` per body entry |
| `resolve_binding` | `resolve_binding(app_did, key)` | `{material, mesh, mesh_present, attach, meshes, parts, garment_part}` or `None` if `key` isn't bound in the record |
| `resolve_item` | `resolve_item(item_did)` | `{item, phys_obj, appearance_key, bodies:[...]}` — full per-body resolution |
| `renderable_body` | `renderable_body(res)` | one body dict whose garment mesh is a shipped direct file (preferring species 23/human), or `None` |
| `human_standin_mesh` | `human_standin_mesh(app_did, min_z=1.2)` | `(mesh_did, vertex_count)` or `(None, 0)` — a present full-length Man garment mesh usable as a stand-in body |
| `human_render` | `human_render(res)` | `{species, sex, worn_appearance, key, mesh, mesh_present, standin, diffuse}` for a renderable Man body, real or stand-in |

`resolve_item`'s `bodies` entries: `{species, sex, worn_appearance, key,
mesh, attach, material, diffuse, mesh_present, parts}`. `mesh_present=False`
means the DID is an **unshipped indirection DID**, not a missing garment —
see Gotchas.

## How it works internally

### `GARMENT_TAG` — identifying the garment part among a draw entry's parts

A `0x20` draw entry is a list of part-meshes, each tagged with that part
mesh's own `Flags` value. `GARMENT_TAG = 0x1000000C` marks the complex
skinned part — the dress body itself. `HANDS_TAG = 0x10000001` marks a
hands part. Other tags are 3-vertex placeholder **stubs**. This was verified
across all 7 Exquisite + all 7 Summer body entries: the `0x1000000C` part is
always the dress body and the only multi-submesh garment mesh — so
`resolve_binding` picks the garment by **tag**, not by proximity to the key
byte offset. An earlier version walked a couple of DIDs backward from the
key and returned the HANDS part or a 3-vertex stub instead of the actual
dress body.

`_STUB_SIZE = 2000`: on-disk (compressed) size below which a present part
is a placeholder stub rather than real geometry (observed stubs are
~196–350 bytes; real garments are tens of KB).

### `resolve_binding` — parsing one draw entry

A draw entry is `[00000000][partCount][1.0f]` then, per part,
`[tag 0x10______][meshDID 0x06______][10.0f 0x41200000]` (the last part
omits the trailing `10.0f`, followed by two zero dwords), then `<key>`, its
fixed tail (`_BIND_TAIL = u32(1) + f32(1.0)`), `<material 0x30>`, and the
`_TRAILER = (0x00000002, 0x10000050)` entry terminator. The function finds
the entry by searching for `struct.pack("<I", key) + _BIND_TAIL`, locates
the material as the `0x30` dword immediately before the following
`_TRAILER`, and recovers the part list by scanning backward from the key to
the previous entry's trailer.

### Human stand-in fallback

Many specific garments' Man (human) mesh is an **unshipped indirection
DID** — a confirmed per-garment data hole (not a file in any `.dat`, not
referenced by any `0x01`/`0x47` record, no redirect table, no DID
transform). But Man clothing geometry as a whole does ship — dozens of
present full-length Man-female garment meshes exist per body record — and
all garment meshes of one body type share a common UV atlas, so any
garment's diffuse maps coherently onto any same-body mesh.
`human_standin_mesh` collects every `0x1000000C`-tagged mesh DID present in
the body's `0x20` record (`_garment_dids_in_record`), sorts largest-first,
and decodes each candidate (via [mesh_decode.py](mesh_decode.md)) until it
finds one with zero sliver triangles and a Z-range `>= min_z` (1.2 by
default) — a full-length dress, not a short accessory. `human_render` uses
this as a documented fallback (correct race + shared UV atlas), explicitly
**not** the item's exact geometry.

## Gotchas & lessons

- **Report `mesh_present` honestly; never substitute silently.**
  `resolve_binding` explicitly does NOT swap in a different part when the
  tagged garment's DID is an unshipped indirection — it reports the DID and
  lets the caller (`renderable_body`, `human_render`) decide how to handle
  it. Mislabeling a stand-in body as "human" or silently picking a
  different race would hide the actual data hole.
- **`renderable_body` prefers human but frequently can't use it.** For many
  garments the human garment mesh is exactly the unshipped-indirection case
  above, so `renderable_body` typically lands on a non-human body (Elf,
  Dwarf, Hobbit). The docstring explicitly warns: do not relabel that
  result as "human."
- **Species enum** (`SPECIES`, verified against `LOTRO lore/enums/Species.xml`):
  `23=Man, 65=Elf, 73=Dwarf, 81=Hobbit, 114=Beorning`.
- **One `0x20` record covers ~150 distinct garment meshes** shared across
  ~1080 draw entries — garments are grouped into shared body-SHAPE classes,
  differentiated only by material/texture, which is exactly what makes the
  human-stand-in trick (above) valid.

## See also

- [../wardrobe.md](../wardrobe.md) — full item→appearance chain writeup.
- [propset.py](propset.md) — parses `Item_WornAppearanceMapList` that this module reads.
- [wearable2.py](wearable2.md) — a stricter, fuller sequential parser of the same `0x20` records (all entries/blocks, not just one key lookup).
- [mesh_decode.py](mesh_decode.md) — decodes the mesh DIDs this module resolves.
- [tex_extract.py](tex_extract.md) — `material_diffuse`, used to resolve the final diffuse texture.
- [compose.py](compose.md) — the next pipeline stage, composing a full textured outfit from this module's + wearable2's output.
- [INDEX.md](INDEX.md) — full script index.
