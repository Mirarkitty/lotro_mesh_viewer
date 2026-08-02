"""charparts.py — character head/face, hair, and beard parts (chargen APR files).

How hair/face selection works (derived 2026-08-01, see docs/hair-face.md):

  Each playable (race, sex) has an avatar chargen record (0x47, class tag 0x28B)
  in client_gamelogic.dat whose AppearanceUI_Controls carry APRControl entries:
      AppearanceUI_APRKey  0x10000009 -> HEAD/FACE   (value prop AC_Mesh_Head)
      AppearanceUI_APRKey  0x10000032 -> HAIR        (Av_Avatar_Hair_Style)
      AppearanceUI_APRKey  0x10000018 -> BEARD       (Av_Avatar_Beard_Style)
  Each AppearanceUI_APRFile is a 0x20 record (client_general) with ONE entry
  (key = the slot key above) whose BLOCKS are the styles: block i's part list has
  exactly one part (tag 0x10000002 head / 0x1000000E hair / 0x10000007 beard)
  pointing at one 0x06 GfxObj. The block selector q encodes the style id
  (0.00, 0.01 ... with generation jumps at 0.30x/0.40x). A 198 B stub mesh =
  the "bald"/none option.

  Textures: chargen blocks carry NO material groups; each part mesh binds its
  diffuse through its own 22 B 0x31 surface -> 0x30 material chain
  (tex_extract.mesh_textures). Hair diffuses are grayscale — the game tints
  them with AC_Color_Hair; heads get a skin-tone face texture (the in-game
  face is further composited: eyebrows/complexion/mouth layers, compositor
  file 0x33000002 — not implemented here).

Usage:
  python3 charparts.py styles man_m hair          # list hairstyles
  python3 charparts.py render man_m hair 2 out    # decode style -> decoded/out.json
  python3 charparts.py avatar 0x7000DA5B 0x20001E58 dwarf_m 1 0 1 out
      # garment compose + head/hair/beard of that race -> decoded/out.json
"""
import sys, json, math
import config
import selector, wearable2, mesh_decode as M

# (race, sex) -> chargen APR file DIDs, from the 0x47 avatar records
# 0x47000604-0x610 (AppearanceUI_Controls / Appearance_InstanceList).
# Race identification: scale + beard availability + head z-height
# (hobbit 0.9, dwarf 1.1 male-only+beard, man beard-on-male, elf no beard).
CHARGEN = {
    "man_m":    dict(avatar=0x4700060A, head=0x20001255, hair=0x20001254, beard=0x20001256),
    "man_f":    dict(avatar=0x4700060B, head=0x20001258, hair=0x20001257, beard=0x2000327E),
    "elf_m":    dict(avatar=0x4700060E, head=0x20001259, hair=0x2000125A),
    "elf_f":    dict(avatar=0x4700060F, head=0x2000125C, hair=0x2000125B),
    "dwarf_m":  dict(avatar=0x47000610, head=0x2000125D, hair=0x2000125E, beard=0x2000125F),
    "hobbit_m": dict(avatar=0x47000606, head=0x20002AE3, hair=0x20002B03),
    "hobbit_f": dict(avatar=0x47000607, head=0x20002AD0, hair=0x20002AB2),
}

STUB_SIZE = 2000   # on-disk size below which a part mesh is a placeholder stub


def styles(body, slot):
    """List the styles of one chargen APR file. body = CHARGEN key,
    slot = 'head'|'hair'|'beard'. Returns [{index, q, mesh, size, present}]."""
    apr = CHARGEN[body][slot]
    rec = wearable2.parse_record(config.general().read_content(apr))
    entry = wearable2.entries(rec)[0]
    out = []
    for i, b in enumerate(entry["blocks"]):
        p = b["parts"][0] if b["parts"] else None
        ent = config.mesh_chain().find_file(p["mesh"]) if p else None
        size = ent[2] if ent else 0
        out.append(dict(index=i, q=b["q"], mesh=p["mesh"] if p else None,
                        size=size, present=size >= STUB_SIZE))
    return out


def head_bone(bones):
    """Index of the head bone in a skeleton bone list (rigid-bind fallback)."""
    for i, b in enumerate(bones):
        n = b["name"].lower()
        if n == "head" or n.endswith("_head"):
            return i
    return 0


def _append_mesh(out, mesh_did, texture=None, skin_bones=None, rigid_bone=None,
                 only_surface=None):
    """Decode mesh_did (own-surface textures unless `texture` given) and append
    its LOD-0 submeshes to the compose-format dict `out`.

    skin_bones: skeleton bone count — also append flat skinIndices/skinWeights
    (4 per vertex, export_skinned layout) in lockstep with the vertices.
    rigid_bone: bone index to rigid-bind any vertex whose stride carries no
    known skin data (export_skinned falls those back to bone 0 = 'parent',
    which would freeze the part at the origin; head parts belong on the head).
    only_surface: keep ONLY submeshes bound to this surface DID. Used to pull
    the BARE FOOT out of a sandal mesh — the game ships no pure bare-foot mesh,
    but a sandal is a skin-surfaced foot plus cloth-surfaced straps, so keeping
    the skin submesh alone yields exactly the naked foot."""
    from tex_extract import _mesh_surfaces
    import tex_extract as tx
    m = M.decode_mesh(mesh_did, with_textures=(texture is None),
                      texture_override=texture)
    e = config.mesh_chain().find_file(mesh_did)
    raw = config.mesh_chain().read_asset(e[1], e[3])[1]
    skin_i = skin_w = None
    if skin_bones:
        import export_skinned as _es
        skin_i, skin_w, bad = _es.skin_arrays(raw, skin_bones)
        if bad and rigid_bone is not None:
            # only rewrite the fallback pattern skin_arrays produces
            for k in range(len(skin_i)):
                if skin_i[k] == [0, 0, 0, 0] and skin_w[k][0] == 1.0:
                    skin_i[k] = [rigid_bone, 0, 0, 0]
            print("charparts: mesh 0x%08X: %d/%d verts without skin data "
                  "rigid-bound to bone %d" % (mesh_did, bad, len(skin_i),
                                              rigid_bone))
    try:
        surfs = _mesh_surfaces(raw)
    except Exception:
        surfs = list(range(len(m["groups"])))
    # LOD dedup: largest submesh per surface DID (same rule as compose.py)
    best = {}
    for g in m["groups"]:
        s = surfs[g["submesh"]] if g["submesh"] < len(surfs) else g["submesh"]
        if only_surface is not None and s != only_surface:
            continue
        if s not in best or g["vert_count"] > best[s]["vert_count"]:
            best[s] = g
    for s, g in best.items():
        v0 = len(out["vertices"])
        vs = slice(g["vert_start"], g["vert_start"] + g["vert_count"])
        out["vertices"].extend(m["vertices"][vs])
        out["normals"].extend(m["normals"][vs])
        out["uvs"].extend(m["uvs"][vs])
        if skin_i is not None:
            for quad in skin_i[vs]: out["skinIndices"].extend(quad)
            for quad in skin_w[vs]: out["skinWeights"].extend(quad)
        t0 = len(out["triangles"])
        for t in m["triangles"][g["tri_start"]:g["tri_start"] + g["tri_count"]]:
            out["triangles"].append([i - g["vert_start"] + v0 for i in t])
        tex = g.get("texture")
        if tex:
            try:
                tx.extract_texture(int(tex, 16))
            except Exception:
                pass
        import compose as _cmp
        out["groups"].append(dict(
            submesh=len(out["groups"]), vert_start=v0, vert_count=g["vert_count"],
            tri_start=t0, tri_count=g["tri_count"], texture=tex,
            **_cmp._shader_fields(s if isinstance(s, int) and s > 0xFFFF else None),
            texture_source="charparts: mesh 0x%08X surf %s" % (mesh_did,
                ("0x%08X" % s) if isinstance(s, int) and s > 0xFFFF else s)))


def _sanitize(out):
    bad = 0
    for arr in (out["vertices"], out["normals"], out["uvs"]):
        for v in arr:
            for i, x in enumerate(v):
                if not math.isfinite(x) or abs(x) > 1e6:
                    v[i] = 0.0; bad += 1
    if bad:
        print("charparts: sanitized %d non-finite floats" % bad)


def append_style(out, body, slot, index, skin_bones=None, rigid_bone=None):
    """Append one chargen style mesh (head/hair/beard) to a compose-format
    dict, optionally with per-vertex skin data (see _append_mesh). Stub
    styles (none/bald) append nothing. Returns the mesh DID or None."""
    st = styles(body, slot)[index]
    if not st["present"]:
        return None
    _append_mesh(out, st["mesh"], skin_bones=skin_bones, rigid_bone=rigid_bone)
    return st["mesh"]


def render_part(body, slot, index, outname, write=True):
    """Decode ONE style (e.g. one hairstyle) into a standalone viewer JSON."""
    st = styles(body, slot)[index]
    if not st["present"]:
        raise ValueError("%s %s style %d is a stub (none/bald option)" % (body, slot, index))
    out = dict(id=outname, vertices=[], normals=[], uvs=[], triangles=[], groups=[])
    _append_mesh(out, st["mesh"])
    _sanitize(out)
    out["num_submeshes"] = len(out["groups"])
    if write:
        json.dump(out, open("decoded/%s.json" % outname, "w"))
        print("wrote decoded/%s.json: %dv %dt (mesh 0x%08X)" % (
            outname, len(out["vertices"]), len(out["triangles"]), st["mesh"]))
    return out


def compose_avatar(item_did, app_did, body, outname,
                   head=0, hair=0, beard=None, write=True):
    """compose.compose(item, app) + this body's head/hair/beard styles merged
    into one viewer JSON. head/hair/beard are style indices (None = skip)."""
    import compose
    out = compose.compose(item_did, app_did, outname, write=False)
    for slot, idx in (("head", head), ("hair", hair), ("beard", beard)):
        if idx is None or slot not in CHARGEN[body]:
            continue
        st = styles(body, slot)[idx]
        if not st["present"]:
            continue
        _append_mesh(out, st["mesh"])
    _sanitize(out)
    out["num_submeshes"] = len(out["groups"])
    if write:
        json.dump(out, open("decoded/%s.json" % outname, "w"))
        print("wrote decoded/%s.json: %d groups %dv %dt" % (
            outname, len(out["groups"]), len(out["vertices"]), len(out["triangles"])))
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "styles":
        for s in styles(sys.argv[2], sys.argv[3]):
            print("%3d q=%.3f mesh=0x%08X size=%8d %s" % (
                s["index"], s["q"], s["mesh"] or 0, s["size"],
                "" if s["present"] else "STUB"))
    elif cmd == "render":
        render_part(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
    elif cmd == "avatar":
        item, app = int(sys.argv[2], 16), int(sys.argv[3], 16)
        body = sys.argv[4]
        head, hairi, beard = int(sys.argv[5]), int(sys.argv[6]), sys.argv[7]
        beard = None if beard in ("none", "-") else int(beard)
        compose_avatar(item, app, body, sys.argv[8], head=head, hair=hairi, beard=beard)
