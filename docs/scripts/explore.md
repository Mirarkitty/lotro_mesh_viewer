# explore.py

[`explore.py`](../../explore.py)

## Purpose

The "dig from anywhere" tool: give it a search term or any DID and it
prints everything the toolkit can resolve from that starting point as a
tree — the whole pipeline (item → [PropertiesSet](../properties.md) →
[worn appearance](../wardrobe.md) → parts →
[mesh](../mesh-format.md) → [surface](../textures.md) →
[shader](../shaders.md) → [material → diffuse](../textures.md)) in one
command, including the **reverse** direction: which wardrobe entries and
items use a given mesh or material.

It's both a practical lookup tool and a worked example of driving the
library modules ([selector.py](selector.md), [wearable2.py](wearable2.md),
[mesh_decode.py](mesh_decode.md), [tex_extract.py](tex_extract.md),
[shaders.py](shaders.md)) from code.

The tree is **deduplicated**: the first expansion of any entity (material,
surface, texture, decoded mesh, wardrobe entry) is tagged `[@N]`, and every
later occurrence prints `-> @N` instead of repeating the subtree — one
material serving seven bodies, or two races sharing one wardrobe
record+key, appears once. Per-body listings cover every race/sex the item
binds; entry parts show their **LOD values** (`lod=10`), dye-variant
blocks show their **dye floatCode** (`q=`, [dyes.md](../dyes.md)) with
blocks 1..N collapsed to a range summary, and `--deep` mesh decoding
groups **LOD chains** (submeshes sharing one surface — renderers keep the
largest).

## CLI usage

```
python3 explore.py <query> [--deep] [--limit N] [--game-dir DIR]
```

| Argument | Meaning |
|---|---|
| `query` | a name search term, or a DID: hex `0x`-prefixed of type `0x70` item / `0x20` worn-appearance / `0x06` mesh / `0x30` material / `0x31` surface / `0x41` texture / `0x2B` shader — or a decimal item id (the form LotroCompanion's `outfits.xml` uses) |
| `--deep` | also decode meshes (submesh count, vertex strides, per-submesh surface → shader → diffuse, sliver check) — slower |
| `--limit N` | max items shown for a name search (default 100) |
| `--game-dir DIR` | LOTRO install directory (default `$LOTRO_DIR` or probing) |

Name search and the reverse lookups need `items_catalog.jsonl`
([items_catalog.py](items_catalog.md), run once); pure DID digs work
without it.

## Examples

Search by name — every matching item resolved to its per-body bindings:

```
$ python3 explore.py 0x7000DA5B
item 0x7000DA5B  Exquisite Dress[vs]
├─ appearance key 0x10000417 (constant across bodies)
├─ body Man-M      app 0x20001E54  key 0x10000417  [@1]
│  └─ block 0  q=1.00  (1 material group, 3 parts)
│     ├─ material 0x30003119  [@2]
│     │  └─ diffuse 0x410DD7A5  1024x1024 DXT5  [@3]
│     ├─ part garment   mesh 0x0600D550  147478 B  lod=10
│     ├─ part feet-slot mesh 0x0600D65C  stub 196 B (blanks that slot)  lod=10
│     └─ part legs-slot mesh 0x0600D665  stub 198 B (blanks that slot)
├─ body Man-F      app 0x20001E55  key 0x10000417  [@4]
│  └─ block 0  q=1.00  (1 material group, 3 parts)
│     ├─ material 0x30003119  -> @2
│     ├─ part garment   mesh 0x0600D9E4  163619 B  lod=10
...
```

A name search (`python3 explore.py aurochs`) prints the same tree for
every matching item, and ends with a `skipped:` section accounting for
every match that was NOT expanded and why — beyond `--limit`, or a
true duplicate of an item already shown, with the DID of the row that
stands in for it. Duplicate means same name AND the identical full set of
per-body (appearance, key) bindings — a shared name alone never hides an
item (the catalog holds ~96 name groups whose same-named members bind
differently).

Start from a **mesh** DID — submeshes with their full shader/material
chain, then everyone who wears it:

```
$ python3 explore.py 0x0600D250
mesh 0x0600D250
├─ 1902v 2322t  4 submesh(es)  sliver_tris=0
├─ submesh 0  1069 verts  stride 71 B
│  └─ surface 0x310001D4
│     ├─ shader 0x2B0007A0  cloth_dyed  dyeable
│     └─ material 0x30000270
│        └─ diffuse 0x4100169F  1024x512 DXT5
...
└─ used by 13 wardrobe entries (wardrobe part)
   ├─ app 0x20001E54  key 0x100000AF  as garment part
   │  ├─ item 0x7004976E  Hero's Breastplate[e]  [Man-M]
   │  └─ ... 30 more distinct items (74 rows total)
...
```

Other starting points: `0x20…` dumps a whole wardrobe record entry by
entry (with the items bound to each key), `0x30…` resolves a material's
diffuse and lists every entry binding it, `0x31…`/`0x2B…` show the
shader classification, `0x41…` prints texture info and extracts the PNG.

## How it works

- Item digs go through [`selector.resolve_item`](selector.md) — the same
  code path the composer uses, so what the tree shows is what renders.
- Wardrobe digs use [`wearable2.parse_record`](wearable2.md) (the strict
  parser) and label parts with the chest-family tag names from
  [../wardrobe.md](../wardrobe.md) — tag semantics vary per record
  family, so the labels are hints.
- Reverse lookups parse every worn-appearance record referenced by the
  catalog once (~70 distinct records cover all wearables) and scan their
  entry part/material lists.
- `--deep` mesh decoding reports the sliver-triangle self-check
  ([../mesh-format.md](../mesh-format.md)) — `sliver_tris=0` is the
  expected value for a correct decode.
- Unshipped garment meshes print `NOT SHIPPED (indirection DID)` — the
  human-body data hole documented in
  [../limitations.md](../limitations.md), reported rather than papered
  over.

## See also

- [../overview.md](../overview.md) — the pipeline this tool walks
- [selector.md](selector.md) / [wearable2.md](wearable2.md) — the
  resolution machinery underneath
- [items_catalog.md](items_catalog.md) — the catalog that powers name
  search and reverse lookups
