"""Resolve a held item (weapon/class item) to its mesh DIDs.

Chain (established 2026-08-02, see docs/weapons.md):
  item 0x70 -> propset (item+0x09000000) -> PhysObj (0x47 entity, gamelogic)
    -> 0x47 record: class tag (0x8B, 0x28B, ...), self DID, u32 7,
       template DID (0x1F), tsize nprops, then doubled-pid
       properties (registry-typed)
    -> 0x1F template (client_general): last u32 is a 0x04 DID (type-9 entry)
    -> 0x04 Havok-skeleton record (client_general): trailer is
       u8 count + count * u32 mesh DIDs (0x06, client_mesh or mesh_aux_1)
The ExternalAppearanceID (0x20000004) on weapon items is a shared material
stub and carries no geometry — PhysObj is the real appearance chain.
"""
import struct

import config
from datfile import DatFile
from propset import load_item, registry, Reader, _decode_property


def _dats():
    """(general, mesh, mesh_aux, gamelogic) archive handles, cached in config."""
    return (config.general(), config.open_dat("client_mesh.dat"),
            config.open_dat("client_mesh_aux_1.datx"), config.gamelogic())


def parse_physobj(buf, reg):
    """0x47 entity record -> (template_did, {prop: value})."""
    r = Reader(buf)
    r.u32()                       # class tag (0x8B, 0x28B, ...)
    r.u32()                       # self DID
    r.u32()                       # always 7, meaning unknown
    template = r.u32()
    n = r.tsize()                 # u8 zero + vle count
    props = {}
    for _ in range(n):
        kv = _decode_property(r, reg, True)
        if kv:
            props[kv[0]] = kv[1]
    return template, props


def template_skeleton(buf):
    """0x1F template record -> 0x04 DID (or None for 0x20-referencing forms)."""
    last = struct.unpack_from("<I", buf, len(buf) - 4)[0]
    return last if (last >> 24) == 0x04 else None


def skeleton_meshes(buf):
    """0x04 record -> list of 0x06 mesh DIDs from the trailer."""
    n = None
    # trailer: u8 count, then count u32 DIDs; find count by walking back
    for cnt in range(1, 64):
        pos = len(buf) - 4 * cnt - 1
        if pos < 0:
            break
        if buf[pos] == cnt and all(
            (struct.unpack_from("<I", buf, pos + 1 + 4 * i)[0] >> 24) == 0x06
            for i in range(cnt)
        ):
            n = cnt
    if n is None:
        raise ValueError("no mesh trailer found")
    pos = len(buf) - 4 * n - 1
    return [struct.unpack_from("<I", buf, pos + 1 + 4 * i)[0] for i in range(n)]


def resolve_weapon(item_did):
    """item DID -> dict with the full chain and mesh DIDs."""
    gen, mesh, aux, gl = _dats()
    _, props = load_item(item_did)
    phys = props.get("PhysObj")
    if not phys:
        raise ValueError("item 0x%08X has no PhysObj" % item_did)
    template, pprops = parse_physobj(gl.read_content(phys), registry(gl))
    skel = template_skeleton(gen.read_content(template))
    if skel is None:
        raise ValueError("template 0x%08X carries no 0x04 entry" % template)
    meshes = skeleton_meshes(gen.read_content(skel))
    return {
        "item": item_did,
        "physobj": phys,
        "template": template,
        "skeleton": skel,
        "meshes": meshes,
        "mesh_where": {m: ("mesh" if mesh.find_file(m) else
                           "aux" if aux.find_file(m) else "ABSENT")
                       for m in meshes},
        "physobj_props": pprops,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Resolve held items (weapons/class items) to their mesh "
                    "DIDs via the PhysObj -> template -> skeleton chain.")
    ap.add_argument("items", nargs="*", default=["0x7002B62F", "0x700220C3"],
                    help="item DIDs (hex; defaults are two known weapons)")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)
    dids = [int(a, 16) for a in args.items]
    for d in dids:
        try:
            r = resolve_weapon(d)
            print("item 0x%08X -> physobj 0x%08X -> tmpl 0x%08X -> skel 0x%08X"
                  % (d, r["physobj"], r["template"], r["skeleton"]))
            for m in r["meshes"]:
                print("   mesh 0x%08X (%s)" % (m, r["mesh_where"][m]))
        except Exception as e:
            print("item 0x%08X FAILED: %s" % (d, e))
