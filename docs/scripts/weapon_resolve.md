# weapon_resolve.py

[`weapon_resolve.py`](../../weapon_resolve.py)

## Purpose

`weapon_resolve.py` resolves a held item (weapon, shield, or class item)
to its mesh DIDs. Held items don't go through the per-body-type
[wardrobe](../wardrobe.md) chain garments use — they carry a fixed mesh
via a different property (`PhysObj`), reached through an entity/template/
skeleton chain instead of a worn-appearance record. See
[../weapons.md](../weapons.md) for the full format writeup this module
implements (chain diagram, what was measured vs. assumed, and the dead
ends ruled out on the way — notably that `ExternalAppearanceID` is a
shared material stub, not the real chain).

## CLI usage

```
python3 weapon_resolve.py [items...] [--game-dir DIR]
```

| Argument | Meaning |
|---|---|
| `items` | item DIDs, hex, space-separated (default: two known weapons, `0x7002B62F` and `0x700220C3`) |

```
$ python3 weapon_resolve.py 0x7002B62F 0x700220C3
item 0x7002B62F -> physobj 0x470006D5 -> tmpl 0x1F000615 -> skel 0x040005DE
   mesh 0x06000B28 (mesh)
item 0x700220C3 -> physobj 0x47000128 -> tmpl 0x1F0000F9 -> skel 0x040000F3
   mesh 0x060002B8 (mesh)
```

(`mesh`/`aux` names which archive holds the mesh — `client_mesh.dat` or
`client_mesh_aux_1.datx`; `ABSENT` marks an unshipped DID.)

resolves "Weakened Axe of the Stalwart" (`0x7002B62F`) and "Etched Yew
Bow" (`0x700220C3`) and prints, per item, one summary line —
`item <DID> -> physobj <0x47 DID> -> tmpl <0x1F DID> -> skel <0x04 DID>`
— followed by one `mesh <0x06 DID> (mesh|aux)` line per resolved mesh,
where `mesh`/`aux` says which archive actually holds it —
`client_mesh.dat` or `client_mesh_aux_1.datx` (`ABSENT` if neither does).
A failed item prints `item <DID> FAILED: <reason>` rather than raising, so
a batch of DIDs can be swept without one bad item aborting the run.

## Public API

| Function | Signature | Returns |
|---|---|---|
| `parse_physobj` | `parse_physobj(buf, reg)` | `(template_did, {prop: value})` — decodes a `0x47` entity record's fixed header plus its doubled-pid property block |
| `template_skeleton` | `template_skeleton(buf)` | the `0x04` skeleton DID referenced by a `0x1F` template record, or `None` if the template's last `u32` isn't a `0x04`-typed entry |
| `skeleton_meshes` | `skeleton_meshes(buf)` | `list[int]` — the `0x06` mesh DIDs in a `0x04` record's trailer |
| `resolve_weapon` | `resolve_weapon(item_did)` | full chain result, see below |

`resolve_weapon(item_did)` returns:

```python
{
    "item": item_did,
    "item_class": <Item_Class value, or None>,
    "physobj": <0x47 DID>,
    "template": <0x1F DID>,
    "skeleton": <0x04 DID>,
    "meshes": [<0x06 DID>, ...],
    "mesh_where": {mesh_did: "mesh" | "aux" | "ABSENT", ...},
    "physobj_props": {propertyName: value, ...},  # render hints, no geometry
}
```

`item_class` is the item's own `Item_Class` property, read from the item's
top-level properties (not the `0x47` entity) before the template chain is
followed — `api_common.compose_weapon` uses it to decide hip-stow
orientation (`HIP_INVERT_CLASSES`, see [../weapons.md](../weapons.md#attachment-bones-rigid-binding-grip-overlay)).

It raises `ValueError` at the first broken link: no `PhysObj` property on
the item, a `0x47`-to-`0x47` parent chain (see below) that doesn't reach a
`0x1F` template within 8 hops, a template with no `0x04` entry (the
"0x20-referencing form" — see [../weapons.md](../weapons.md#open-gaps) on
the unimplemented dyeable-props template variant), or a `0x04` record with
no recognizable mesh trailer.

## How it works internally

### `parse_physobj` — the `0x47` entity record

```
[u32 classTag]        # 0x8B, 0x28B, ... — meaning not pursued further
[u32 selfDID]
[u32 7]                # constant seen on every record checked, meaning unknown
[u32 template]         # the 0x1F DID
[tsize nprops]          # u8 zero + vle count
nprops x doubled-pid property   # decoded via propset._decode_property
```

The trailing properties are render hints (imbue-streak vectors,
`Render_LODClass`, `ScriptSystem_FxOverlay_Physobj`), not geometry — see
[../weapons.md](../weapons.md#dead-ends). `resolve_weapon` returns them as
`physobj_props` for inspection, but nothing downstream reads them.

### Parent-`0x47` chains — daggers, two-handed swords

`parse_physobj`'s `template` return isn't always a `0x1F` DID. On some
weapons (daggers, two-handed swords observed) it's *another* `0x47` DID —
a shared base entity that several distinct items point at in common —
which itself has to be parsed the same way to find the real `0x1F`
template underneath. `resolve_weapon` loops on this: while the "template"
it just got back is still `0x47`-tagged, it calls `parse_physobj` again on
that DID and merges the base object's properties into the item's own
(`pprops.setdefault(k, v)` — the item's own property always wins over the
shared base's when both carry the same key), up to 8 hops before giving
up. This is what lets `item_class` above be read correctly even for items
whose own `0x47` entity doesn't carry it directly, only the shared base
does.

### `template_skeleton` — the `0x1F` template record

Rather than parsing a `0x1F` template's full internal grammar (not fully
derived), this reads only the record's **last** `u32` and checks its type
byte: if it's `0x04`-tagged (the "type 9" entry pattern:
`...09 00 00 00 | 01 00 00 00 | <0x04 DID>`), that's the skeleton DID.
Sufficient for every weapon template observed (the dominant size is 26
bytes); returns `None` for the 38-byte form that instead references a
parent `0x1F` plus a `0x20` appearance record with property overrides
(unimplemented — see [../weapons.md](../weapons.md#open-gaps)).

### `skeleton_meshes` — the `0x04` record's mesh trailer

The `0x04` record is a genuine Havok `hkaSkeleton` packfile (see
[../animation.md](../animation.md#container-format-havok-binary-tagfile))
for a real character skeleton, but for a weapon it's degenerate — present
mainly to carry a trailer appended after the Havok payload:
`[u8 count][count x u32 0x06 mesh DID]`. Rather than parsing the Havok
tagfile at all, `skeleton_meshes` walks backward from the end of the
buffer looking for a byte that equals a plausible count `n` (1..63) such
that the `n` `u32`s immediately following it are all `0x06`-tagged DIDs —
the first `n` (searched ascending) satisfying that is taken as the real
trailer. This is a heuristic scan, not a length-prefixed field read from a
known offset, because the Havok payload's own length isn't independently
parsed here.

### `_dats` — archive handles

Held items span four archives: `client_general.dat` (`0x1F` template,
`0x04` skeleton), `client_mesh.dat` and `client_mesh_aux_1.datx` (`0x06`
mesh, checked in that order via `mesh_where`), and `client_gamelogic.dat`
(`0x47` entity, and the item's own PropertiesSet via
[propset.py](propset.md)). `_dats()` returns all four, cached in
`config`.

## Gotchas & lessons

- **`ExternalAppearanceID` is a trap.** It's tempting to resolve a
  weapon's appearance the same way `Item_WornAppearance` resolves a
  garment's, since both look like `0x20`-family DATA_FILE properties on
  the item. For held items this is wrong — see
  [../weapons.md](../weapons.md#dead-ends) for the full story of why.
- **`load_item` returns a tuple**, not a dict — `resolve_weapon` does
  `_, props = load_item(item_did)`, matching [propset.py](propset.md)'s
  documented `(did, {propertyName: value})` shape. A caller expecting a
  bare dict will get a confusing unpacking error.
- **A `None` from `template_skeleton` is a real, expected outcome**, not
  necessarily a bug — it means the template uses the unimplemented
  38-byte dyeable-props form, not that the chain is broken.
- **The parent-`0x47` hop count is capped at 8, silently.** A chain that
  is genuinely longer than 8 hops (none observed) would fall through the
  `while` loop still `0x47`-tagged and raise the "no `0x1F` template"
  `ValueError` below rather than an explicit "hop limit exceeded" one —
  the two failure causes look identical from the exception message alone.

## See also

- [../weapons.md](../weapons.md) — the full format writeup: chain diagram,
  measured-vs-assumed evidence, dead ends, texture binding, attachment,
  and open gaps.
- [propset.py](propset.md) — `load_item`, `registry`, `_decode_property`,
  and `Reader`, all used directly by `parse_physobj`.
- [datfile.py](datfile.md) — `DatFile.read_content`/`find_file`, used to
  fetch each record and to answer `mesh_where`.
- [../animation.md](../animation.md) — the Havok tagfile container format
  a `0x04` record is built on (not parsed by this module — see
  `skeleton_meshes` above).
- [api_common.py](api_common.md#composition) — `compose_weapon`, which
  calls `resolve_weapon` and rigid-binds the resulting meshes to an
  attachment bone.
- [mesh_decode.py](mesh_decode.md) — decodes the `0x06` mesh DIDs this
  module resolves.
- [INDEX.md](INDEX.md) — full script index.
