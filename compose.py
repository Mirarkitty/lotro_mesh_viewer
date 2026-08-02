"""compose.py — first-cut avatar compositor (step 1+2 of the plan + skin routing).

Composes one wearable ENTRY (item x body) into a single viewer JSON:
- all present parts of the entry's base block (garment + hands + ...), merged
- LOD dedup: per part, per surface, keep only the largest-vertex submesh
- texture routing per submesh by the GfxObj's own surface DID:
    skin surfaces  (SKIN_SURFS, e.g. 0x3100015D) -> flat skin-tone placeholder
    cloth surfaces -> the entry material's diffuse (tex_extract.material_diffuse)
  The real skin ATLAS comes from the body (later, with the 0x01 Setup work);
  the placeholder makes sleeveless/short-sleeved garments read correctly.

Usage: python3 compose.py <item_did_hex> <worn_app_hex> <outname>
   e.g. python3 compose.py 0x7000DA5B 0x20001E58 compose_exq_dwarfM
"""
import threading
import sys, json, struct, zlib, os
import config
import selector, wearable2, mesh_decode as M, tex_extract as tx
from tex_extract import _mesh_surfaces

CLOTH_SURFS = {0x31002DCD}         # global cloth surface (Dwarf + Elf meshes verified)
SKIN_SURFS = {0x3100015D, 0x310092EA}  # per-body skin surfaces (Dwarf-M, Elf-F)
def is_skin(surf, all_surfs):
    """Skin = known skin surface, or the partner of a known cloth surface in a
    two-surface garment mesh (the common case)."""
    if surf in SKIN_SURFS: return True
    if surf in CLOTH_SURFS: return False
    return len(set(all_surfs)) == 2 and any(x in CLOTH_SURFS for x in all_surfs)
SKIN_RGB = (224, 189, 157)         # placeholder skin tone

def skin_png(path):
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(SKIN_RGB) * 8
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + \
          chunk(b"IDAT", zlib.compress(row * 8)) + chunk(b"IEND", b"")
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())      # atomic; served concurrently
    open(tmp, "wb").write(png)
    os.replace(tmp, path)

def _shader_fields(surf_did):
    """Render hints for one submesh, from its surface's 0x2B shader (shaders.py).
    Exported so the viewer can stop treating every surface as opaque cloth."""
    try:
        import shaders
        sh = shaders.surface_shader(surf_did) if surf_did else None
        if not sh:
            return {}
        name, alpha, dyeable, metallic, _ = shaders.info(sh)
        return {"shader": name, "shader_did": "0x%08X" % sh,
                "alpha_test": bool(alpha), "metallic": bool(metallic),
                "dyeable": bool(dyeable)}
    except Exception:
        return {}


_SURF_DIFFUSE_CACHE = {}
def _surface_diffuse(surf_did):
    if surf_did not in _SURF_DIFFUSE_CACHE:
        t = None
        try:
            t = tx.diffuse_for_surface(surf_did)
            if t: tx.extract_texture(t)
        except Exception:
            t = None
        _SURF_DIFFUSE_CACHE[surf_did] = t
    return _SURF_DIFFUSE_CACHE[surf_did]

def compose(item_did, app_did, outname, skin_bones=None, write=True):
    """skin_bones: skeleton bone count; when set, also emit flat
    skinIndices/skinWeights (4 per vertex, export_skinned.skin_arrays layout)
    sliced in lockstep with the vertices. write=False skips the decoded/ dump
    and just returns the dict (api_common.compose_skinned adds bones+clip)."""
    mesh_db = config.mesh_chain()
    bodies, _ = selector.appearance_map(item_did)
    b = next(x for x in bodies if x["worn_appearance"] == app_did)
    rec = wearable2.parse_record(config.general().read_content(app_did))
    entry = next(e for e in wearable2.entries(rec) if e["key"] == b["key"])
    block = entry["blocks"][0]
    mat = next((g["material"] for g in block["groups"] if g["material"]), None)
    cloth_tex = tx.material_diffuse(mat) if mat else None
    if cloth_tex:
        tx.extract_texture(cloth_tex)
    skin_png(os.path.join(config.textures_dir(), "skintone.png"))

    gar = [p for p in block["parts"] if p["tag"] == 0x1000000C]
    if gar and not any((lambda f: f and f[2] > 2000)(mesh_db.find_file(p["mesh"])) for p in gar):
        raise ValueError("garment mesh not shipped for this body (data hole) - pick another body")
    out = dict(id=outname, vertices=[], normals=[], uvs=[], triangles=[], groups=[])
    if skin_bones:
        out["skinIndices"], out["skinWeights"] = [], []
    for part in block["parts"]:
        # do NOT filter by tag: tag semantics are per record family (0x10000003
        # is a stub in the chest family but THE GARMENT in the legs family).
        # The size guard below drops the 3-vertex placeholder stubs.
        e = mesh_db.find_file(part["mesh"])
        if not e:
            continue
        if e[2] < 2000:                       # stub-size guard
            continue
        m = M.decode_mesh(part["mesh"], with_textures=False)
        raw = mesh_db.read_asset(e[1], e[3])[1]
        surfs = _mesh_surfaces(raw)
        skin_i = skin_w = None
        if skin_bones:
            import export_skinned as _es
            skin_i, skin_w, bad = _es.skin_arrays(raw, skin_bones)
            if bad:
                print("compose: part 0x%08X: %d verts with bad/absent skin "
                      "data (bone 0)" % (part["mesh"], bad))
        # LOD dedup: keep the largest submesh per surface DID
        best = {}
        for g in m["groups"]:
            s = surfs[g["submesh"]] if g["submesh"] < len(surfs) else None
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
            if is_skin(s, surfs):
                tex = "skintone"
            elif cloth_tex:
                tex = "0x%08X" % cloth_tex
            else:
                # entry has no material groups (newer items): resolve the
                # submesh surface graph directly
                t = _surface_diffuse(s)
                tex = "0x%08X" % t if t else None
            out["groups"].append(dict(
                submesh=len(out["groups"]), vert_start=v0, vert_count=g["vert_count"],
                tri_start=t0, tri_count=g["tri_count"], texture=tex,
                # the surface's shader decides how this submesh must RENDER —
                # alpha as a cutout vs a tint mask, and metal vs cloth response
                **_shader_fields(s),
                texture_source="compose: part 0x%08X surf 0x%08X" % (part["mesh"], s or 0)))
    if not out["groups"]:
        raise ValueError("no shipped meshes for this item on this body (data hole) - pick another body")
    # sanitize: json.dump writes NaN/Inf which JSON.parse rejects; zero them out
    import math
    bad = 0
    for arr in (out["vertices"], out["normals"], out["uvs"]):
        for v in arr:
            for i, x in enumerate(v):
                if not math.isfinite(x) or abs(x) > 1e6:
                    v[i] = 0.0; bad += 1
    if bad:
        print("compose: sanitized %d non-finite floats in %s" % (bad, outname))
    out["num_submeshes"] = len(out["groups"])
    if write:
        fp = os.path.join(config.decoded_dir(), "%s.json" % outname)
        json.dump(out, open(fp, "w"))
        print("wrote %s: %d parts-> %d groups %dv %dt" % (
            fp, len(block["parts"]), len(out["groups"]),
            len(out["vertices"]), len(out["triangles"])))
    return out

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Compose one wearable entry (item x body) into a single "
                    "textured viewer JSON in the decoded/ cache.")
    ap.add_argument("item", help="item DID (hex, e.g. 0x7000DA5B)")
    ap.add_argument("worn_appearance",
                    help="the body's 0x20 worn-appearance DID (hex, from "
                         "selector.py output, e.g. 0x20001E58)")
    ap.add_argument("outname", help="output name -> decoded/<outname>.json")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)
    compose(int(args.item, 16), int(args.worn_appearance, 16), args.outname)
