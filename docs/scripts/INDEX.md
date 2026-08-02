# Script Index

Per-tool reference documentation. Each page covers purpose, CLI usage, public
API, internals, and known gotchas for one script (or, for the viewer, one
closely-coupled group of files). For the format-level writeups these tools
implement, see the parent [docs/](../) directory
([overview.md](../overview.md), [dat-format.md](../dat-format.md),
[mesh-format.md](../mesh-format.md), [textures.md](../textures.md),
[shaders.md](../shaders.md), [properties.md](../properties.md),
[wardrobe.md](../wardrobe.md), [dyes.md](../dyes.md),
[animation.md](../animation.md), [hair-face.md](../hair-face.md),
[limitations.md](../limitations.md)).

| Script | Role | Docs |
|---|---|---|
| `config.py` | Shared config: game-dir resolution, cached archive handles, output caches, CLI plumbing | [config.md](config.md) |
| `datfile.py` | Turbine DAT container reader (`DatFile`, `DatChain`) + CLI | [datfile.md](datfile.md) |
| `mesh_decode.py` | Unified static+skinned GfxObj mesh decoder + validation stats + CLI | [mesh_decode.md](mesh_decode.md) |
| `tex_extract.py` | 0x41 DXT texture extraction + material-graph diffuse resolution + CLI | [tex_extract.md](tex_extract.md) |
| `shaders.py` | 0x2B shader classification (alpha-test cutout vs. tint mask, dyeable, metallic) + CLI | [shaders.md](shaders.md) |
| `propset.py` | Turbine PropertiesSet deserializer + property dictionary + CLI | [propset.md](propset.md) |
| `selector.py` | Item → per-body garment mesh/material selector + CLI | [selector.md](selector.md) |
| `wearable2.py` | Strict sequential 0x20 worn-appearance record parser + CLI | [wearable2.md](wearable2.md) |
| `compose.py` | Composes one wearable entry (item × body) into a textured viewer JSON + CLI | [compose.md](compose.md) |
| `items_catalog.py` | Sweeps all items into a searchable `items_catalog.jsonl`, then bakes per-body presence flags + CLI | [items_catalog.md](items_catalog.md) |
| `export_skinned.py` | Skinned mesh + skeleton + clip export for the animation viewer + CLI | [export_skinned.md](export_skinned.md) |
| `havok_anim.py` | Havok binary tagfile parser + spline-compressed animation decompressor + CLI | [havok_anim.md](havok_anim.md) |
| `app.py`, `index.html`, `anim.html` | The local three.js viewer server (Flask, stdlib fallback) and its two front-ends — single-item subset | [viewer.md](viewer.md) (`app.md` links here) |
| `charparts.py` | Chargen head/hair/beard (APR) compositor + CLI | [charparts.md](charparts.md) |
| `api_common.py` | Framework-free backend shared by both servers: catalog/search/sets, clip listing, LotroCompanion import, composition, dye/texture baking | [api_common.md](api_common.md) |
| `outfit_app.py`, `outfit.html` | The multi-slot outfit composer server + front-end (Flask, port 8723) | [outfit_app.md](outfit_app.md) |
| `screenshot.py` | Playwright headless visual-verification screenshots | [screenshot.md](screenshot.md) |

## Reading order

For someone new to the codebase, roughly following the pipeline is easiest:

1. [config.py](config.md) and [datfile.py](datfile.md) — how any tool
   reaches an archive and a DID's bytes.
2. [propset.py](propset.md) — how an item's typed properties are decoded.
3. [selector.py](selector.md) / [wearable2.py](wearable2.md) — how an item
   resolves to a specific mesh + material per body.
4. [mesh_decode.py](mesh_decode.md) — how a mesh DID becomes geometry.
5. [tex_extract.py](tex_extract.md) — how a material resolves to a diffuse
   texture PNG.
6. [shaders.py](shaders.md) — how a surface's shader decides alpha cutout
   vs. tint mask, and dyeable/metallic response.
7. [compose.py](compose.md) — assembling one full textured outfit.
8. [export_skinned.py](export_skinned.md) / [havok_anim.py](havok_anim.md) —
   the animation branch (skeleton + clip).
9. [items_catalog.py](items_catalog.md) — building the search index over
   every item.
10. [viewer.md](viewer.md) / [screenshot.py](screenshot.md) — rendering and
    verifying the result (single-item subset).
11. [charparts.py](charparts.md) — chargen head/hair/beard, the last piece
    of geometry needed for a full avatar.
12. [api_common.py](api_common.md) / [outfit_app.py](outfit_app.md) — the
    full multi-slot outfit composer that ties every previous stage together
    (see also [../outfit-composer.md](../outfit-composer.md)).
