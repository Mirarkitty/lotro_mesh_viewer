"""LOTRO 0x41 texture (DDS/DXT) extractor + mesh->texture material resolution.

Two public entry points:
  extract_texture(did) -> "textures/0x<DID>.png"
      Decode ANY 0x41 texture asset (from client_surface.dat or client_highres.dat)
      to RGBA PNG. Handles DXT1/DXT3/DXT5 and BOTH 0x41 header variants.
  mesh_textures(meshDID) -> {submesh_index: textureDID}
      Resolve each submesh of a GfxObj mesh to its diffuse texture DID by walking the
      material graph (mesh header 0x31 surfaces -> the 0x2B/0x30 records they reference
      -> brute-resolved 0x41 texture DIDs) and picking the largest non-placeholder.

0x41 record format (verified on 0x41231998 = 512x512 DXT1, 0x41000081 = 4x4 DXT3):
  Somewhere in the record is an ASCII fourcc 'DXT1'/'DXT3'/'DXT5'. The 8 bytes BEFORE
  the fourcc are width,height (uint32 LE). The compressed block data begins at
  fourcc_offset + 8 (4 fourcc bytes + one trailing dword). Exact block size:
    DXT1 = (w/4)*(h/4)*8 ; DXT3/DXT5 = (w/4)*(h/4)*16.
  We wrap those exact bytes in a 128-byte DDS header and decode with Pillow.
Two header variants differ only in what precedes width/height (a type/count dword, or
the self-DID) -- irrelevant because we anchor on the fourcc, not the record start.

Scope note (per-body-part textures, investigated 2026-07-21): full worn-appearance
meshes are COMPOSITE -- one UV set per submesh covers bare skin AND garment cloth, so a
single diffuse must paint skin+cloth+trim together. The reliably-resolvable piece is the
garment overlay (e.g. dress bodice 0x41231998 via the diffuse-bound 0x2B0007A0 shader
instance). The per-body-part BASE diffuse (skin/cloth) is NOT reliably resolvable from
the mesh's own material graph: the surface's 0x30 materials -> 0x40 -> a shared
armor-texture library (breastplates/cloaks/normal-maps), not this garment. It is bound
in the appearance 0x20 record's variable-length per-mesh entries (item 0x70021A13 ->
0x20001E54..5A), which are not yet reliably parsed. See docs/textures.md
for the DID evidence.

CLI:  python3 tex_extract.py <texture_did>              extract one 0x41 texture
      python3 tex_extract.py mesh <mesh_did>            per-submesh diffuses
      python3 tex_extract.py appearance <app> <mesh>    wardrobe-entry diffuses
"""
import threading
import struct, io, os
import config

# Archive handles: all lazy (config caches one handle per archive) so importing
# this module never touches the filesystem.
def _surf():   return config.surface_chain()
def _hires():  return config.highres_chain()
def _gen():    return config.general()
def _mesh():   return config.mesh_chain()


def _find_tex_archive(did):
    """Return the DatFile that holds a 0x41 texture DID, or None."""
    if _surf().find_file(did):  return _surf()
    if _hires().find_file(did): return _hires()
    return None


def _make_dds(w, h, fourcc, blocks):
    """Minimal 128-byte DDS header wrapping raw DXT blocks."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000  # CAPS|HEIGHT|WIDTH|PIXELFORMAT|LINEARSIZE
    bpb = 8 if fourcc == b"DXT1" else 16
    linsize = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bpb
    hdr = b"DDS " + struct.pack("<18I", 124, flags, h, w, linsize, 0, 0, *([0] * 11))
    pf = struct.pack("<8I", 32, 0x4, struct.unpack("<I", fourcc)[0], 0, 0, 0, 0, 0)
    caps = struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    return hdr + pf + caps + blocks


def parse_texture(raw):
    """Parse a decompressed 0x41 record -> (w, h, fourcc, block_bytes) or None.
    Anchors on the DXT fourcc; validates by exact DXT block size (guards against
    landing on a fourcc-like byte run in a non-texture record)."""
    for fourcc in (b"DXT1", b"DXT5", b"DXT3"):
        fi = raw.find(fourcc)
        if fi < 8:
            continue
        w, h = struct.unpack("<II", raw[fi - 8:fi])
        if not (0 < w <= 8192 and 0 < h <= 8192):
            continue
        bpb = 8 if fourcc == b"DXT1" else 16
        need = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bpb
        data_off = fi + 8   # 4 fourcc bytes + one trailing dword
        blocks = raw[data_off:data_off + need]
        if len(blocks) == need:
            return w, h, fourcc, blocks
    return None


def texture_info(did):
    """Return (w, h, fourcc, is_placeholder) for a 0x41 DID, or None if not a texture.
    Placeholder = tiny (<=8px) DXT tile used as an unassigned-slot stand-in."""
    d = _find_tex_archive(did)
    if d is None:
        return None
    e = d.find_file(did)
    _u, raw = d.read_asset(e[1], e[3])
    p = parse_texture(raw)
    if p is None:
        return None
    w, h, fourcc, _blocks = p
    return w, h, fourcc, (w <= 8 or h <= 8)


def extract_texture(did):
    """Decode any 0x41 texture DID to textures/0x<DID>.png. Returns the PNG path."""
    from PIL import Image
    d = _find_tex_archive(did)
    if d is None:
        raise KeyError("0x%08X not in client_surface/client_highres" % did)
    e = d.find_file(did)
    _u, raw = d.read_asset(e[1], e[3])
    p = parse_texture(raw)
    if p is None:
        raise ValueError("0x%08X: no DXT payload found (%d bytes)" % (did, len(raw)))
    w, h, fourcc, blocks = p
    im = Image.open(io.BytesIO(_make_dds(w, h, fourcc, blocks)))
    im.load()
    out = os.path.join(config.textures_dir(), "0x%08X.png" % did)
    # atomic: /textures/ is served while these are being written (threaded=True)
    tmp = "%s.%d.%d.tmp" % (out, os.getpid(), threading.get_ident())
    im.convert("RGBA").save(tmp, format="PNG")
    os.replace(tmp, out)
    return out


# ---- material graph resolution -----------------------------------------------

def _resolvable(raw, hi_types, dat):
    """All distinct u32 LE values in raw whose high byte is in hi_types and that
    resolve as a file in `dat`. Byte-scan (records are unaligned PropertiesSets)."""
    out = []
    seen = set()
    for i in range(len(raw) - 3):
        v = struct.unpack("<I", raw[i:i + 4])[0]
        hi = v >> 24
        if hi in hi_types and 0 < (v & 0xFFFFFF) < 0x400000 and v not in seen:
            if dat.find_file(v):
                seen.add(v)
                out.append(v)
    return out


def _brute_textures(raw):
    """All distinct 0x41 DIDs in raw that resolve in surface OR highres."""
    out = []
    seen = set()
    for i in range(len(raw) - 3):
        v = struct.unpack("<I", raw[i:i + 4])[0]
        if (v >> 24) == 0x41 and 0 < (v & 0xFFFFFF) < 0x400000 and v not in seen:
            if _find_tex_archive(v):
                seen.add(v)
                out.append(v)
    return out


def _mesh_surfaces(raw):
    """Parse the mesh header -> list of per-submesh 0x31 surface DIDs.
    Header: [Id][Flags][numSurf][numSurf x 0x31 surfaceDID][...]."""
    numsurf = struct.unpack("<I", raw[8:12])[0]
    surfs = []
    if 0 < numsurf <= 64:
        ok = True
        cand = []
        for i in range(numsurf):
            v = struct.unpack("<I", raw[12 + 4 * i:16 + 4 * i])[0]
            if (v >> 24) != 0x31:
                ok = False
                break
            cand.append(v)
        if ok:
            surfs = cand
    return surfs


# A real garment diffuse is at least this many px on its longest side; smaller DXT
# tiles reachable from a surface (32x32 masks, 4x4 unassigned-slot placeholders) are
# not diffuse maps and must not be mistaken for one.
MIN_DIFFUSE_PX = 64


def _compact_surface_materials(raw):
    """Parse a compact 0x31 surface record (TRUE content bytes via read_content):
        [self DID 0x31][shader 0x2B][slot key 0x10][u32 nmat][nmat x 0x30 material][u16 flags]
    (22 bytes for nmat=1). Found 2026-07-31 on the Ordakhai coat -- and it turned
    out this is THE 0x31 format, old and new alike: 0x310001D4 (the 'legacy ~1076 B
    2-pass surface' of earlier notes) is also 22 bytes; the ~1 KB reads were
    read_asset over-reading the whole dat block, i.e. neighbor-record garbage.
    For newest-generation items (meshes in client_mesh_aux_1.datx, wearable 0x20
    entries with ZERO material groups) this surface->material link is the ONLY
    texture binding. Returns the material DID list, or None if raw is not this
    format (then the historical brute graph scan runs as fallback)."""
    if len(raw) < 22:
        return None
    did, sh, key, n = struct.unpack_from("<4I", raw, 0)
    if (did >> 24) != 0x31 or (sh >> 24) != 0x2B:
        return None
    # third field: a 0x10 slot key on garment surfaces, a small int (seen: 1)
    # on held-item surfaces (weapon meshes) — same layout otherwise
    if (key >> 24) != 0x10 and key > 0xFF:
        return None
    if not (1 <= n <= 8) or len(raw) != 16 + 4 * n + 2:
        return None
    mats = list(struct.unpack_from("<%dI" % n, raw, 16))
    if any((m >> 24) != 0x30 for m in mats):
        return None
    return mats


def diffuse_for_surface(surf_did):
    """Given a 0x31 surface DID, brute-resolve its texture graph and return the
    diffuse texture DID = the largest DXT texture (>= MIN_DIFFUSE_PX on its longest
    side) reachable from the surface record and the 0x2B/0x30/0x31 records it
    references. Returns None if none qualifies.

    2026-07-31: surfaces that parse as the compact format (which is apparently ALL
    of them, e.g. 0x310001D4 = 22 true bytes) now resolve EXACTLY via their own
    material's first 0x40 slot (material_diffuse). 0x310001D4 -> 0x4100169F now
    (its own material 0x30000270), not the old brute-scan result 0x41231998 --
    which came from neighbor-record garbage and was already flagged wrong for
    garment routing in kb. Note: a mesh surface's own material is still only the
    mesh's default/fallback texture; per-item garments bind their material at the
    wearable 0x20 entry level when material groups are present."""
    gen = _gen()
    e = gen.find_file(surf_did)
    if not e:
        return None
    # New-generation compact surface (TRUE bytes via read_content -- read_asset
    # over-reads a 22-byte record to a whole 1076-byte dat block full of NEIGHBOR
    # records, and the largest-texture heuristic below then picked a neighbor's
    # texture; found 2026-07-31 on the Ordakhai coat, the "blue plumage" bug).
    # Parse it exactly and go through the material chain -- no brute scan.
    mats = _compact_surface_materials(gen.read_content(surf_did))
    if mats is not None:
        for m in mats:
            t = material_diffuse(m)
            if t is not None:
                return t
        return None
    # LEGACY multi-pass surfaces: brute graph scan over the read_asset bytes,
    # kept byte-identical to the historically verified behavior.
    _u, raw = gen.read_asset(e[1], e[3])
    # records in the surface's local graph: the surface itself + the 0x2B/0x30/0x31
    # records it directly references (these carry the texture bindings).
    graph = [surf_did] + _resolvable(raw, {0x2B, 0x30, 0x31}, gen)
    cand = {}
    for did in graph:
        g = gen.find_file(did)
        if not g:
            continue
        _uu, rr = gen.read_asset(g[1], g[3])
        for tdid in _brute_textures(rr):
            info = texture_info(tdid)
            if info is None:
                continue
            w, h, _fc, placeholder = info
            if placeholder or max(w, h) < MIN_DIFFUSE_PX:
                continue
            cand[tdid] = w * h
    if not cand:
        return None
    return max(cand, key=cand.get)


def mesh_textures(mesh_did):
    """Return {submesh_index: textureDID} for a GfxObj mesh: resolve each submesh's
    0x31 surface to its diffuse texture. Submesh count is taken from the header's
    surface list (one surface per submesh). Falls back to a whole-mesh single diffuse
    (submesh 0) if the header surface list can't be parsed."""
    e = _mesh().find_file(mesh_did)
    if not e:
        raise KeyError("mesh 0x%08X not found" % mesh_did)
    _u, raw = _mesh().read_asset(e[1], e[3])
    surfs = _mesh_surfaces(raw)
    out = {}
    cache = {}
    for i, s in enumerate(surfs):
        if s not in cache:
            cache[s] = diffuse_for_surface(s)
        if cache[s] is not None:
            out[i] = cache[s]
    return out


# ---- appearance (0x20 worn-appearance record) resolution ---------------------
# CRACKED 2026-07-21. A `0x20` worn-appearance record is a flat list of draw entries,
# each delimited by the constant marker 0x1000000C:
#   +0x00  uint32  0x1000000C            entry marker (constant)
#   +0x04  uint32  meshDID (0x06______)  the mesh drawn
#   (+0x08 uint32  0x10000003 ; +0x0C uint32 attachMeshDID)   OPTIONAL attach block
#   ...    uint32  slotDID (0x10______)  wear/bone slot; float 1.0; small flags
#   ...    uint32  shaderDID (0x2B)      OPTIONAL (often inherited/absent)
#   ...    uint32  materialDID (0x30)    the material -> textures
#   ...    floats/flags up to the next 0x1000000C
# Entries are variable length (~0x40/0x4C+ bytes). CRUCIAL: a given body mesh appears in
# MANY entries, each bound to a DIFFERENT 0x30 material -- the record is a per-body-type
# WARDROBE (the shared body shape drawn with every garment that uses it). Each material
# textures the mesh COHERENTLY (verified visually), so `mesh -> its list of garment
# materials` is well-defined; picking the ONE that is a specific item (e.g. the summer
# dress) needs the item's wardrobe selector, which is not yet pinned.

def parse_appearance_entries(appearance_did):
    """Walk a 0x20 record -> list of {mesh, materials:[0x30...], shaders:[0x2B...]}, one
    per 0x1000000C-delimited entry, in file order. Byte-scan within each entry (records
    are unaligned)."""
    gen = _gen()
    e = gen.find_file(appearance_did)
    if not e:
        raise KeyError("appearance 0x%08X not found" % appearance_did)
    _u, raw = gen.read_asset(e[1], e[3])
    n = len(raw)
    MARK = b"\x0c\x00\x00\x10"
    starts = []
    i = raw.find(MARK)
    while i != -1:
        starts.append(i)
        i = raw.find(MARK, i + 4)
    entries = []
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else n
        mesh_did = struct.unpack("<I", raw[s + 4:s + 8])[0]
        if (mesh_did >> 24) != 0x06 or not _mesh().find_file(mesh_did):
            continue
        mats, shaders = [], []
        p = s + 8
        while p <= end - 4:
            v = struct.unpack("<I", raw[p:p + 4])[0]
            hi = v >> 24
            if 0 < (v & 0xFFFFFF) < 0x400000 and gen.find_file(v):
                if hi == 0x30:
                    mats.append(v)
                elif hi == 0x2B:
                    shaders.append(v)
            p += 1
        entries.append({"mesh": mesh_did, "materials": mats, "shaders": shaders})
    return entries


def _material_40_slots(rm):
    """Ordered (byte-offset, 0x40 DID) list for a decompressed 0x30 material record.
    The 0x40 texture-slot records appear at increasing offsets in slot order
    [diffuse, normal/gloss, ...]; the FIRST slot is the garment DIFFUSE. (A material
    may chain a second sub-material at ~0x3FC; its slots follow, so the very first
    0x40 by offset is the primary sub-material's diffuse.)"""
    out = []
    seen = set()
    for i in range(len(rm) - 3):
        v = struct.unpack("<I", rm[i:i + 4])[0]
        if (v >> 24) == 0x40 and 0 < (v & 0xFFFFFF) < 0x400000 and v not in seen:
            if _gen().find_file(v):
                seen.add(v)
                out.append((i, v))
    return out


def _largest_tex_in_40(d40):
    """Largest non-placeholder DXT texture DID inside one 0x40 mip-chain record (the
    full-resolution mip), or None."""
    gen = _gen()
    if not gen.find_file(d40):
        return None
    # true bytes: a 0x40 record is ~50 B; read_asset over-reads the whole dat
    # block and a bigger neighbor texture could shadow the slot's own top mip
    rf = gen.read_content(d40)
    best, ba = None, 0
    for tdid in _brute_textures(rf):
        info = texture_info(tdid)
        if info is None:
            continue
        w, h, _fc, placeholder = info
        if placeholder or max(w, h) < MIN_DIFFUSE_PX:
            continue
        if w * h > ba:
            ba, best = w * h, tdid
    return best


def material_diffuse(mat_did):
    """A 0x30 material references texture slots via 0x40 mip-chain records, in slot
    order [diffuse, normal/gloss, ...]. Return the diffuse texture DID = the largest
    non-placeholder DXT texture inside the FIRST 0x40 slot (smallest byte offset).

    NOTE (2026-07-21): picking the *largest texture across ALL slots* (the old rule) is
    WRONG -- later slots hold a shared prop/normal atlas that can be bigger than the
    diffuse (e.g. dress material 0x300043D2: slot0 0x41105076 = the dress fabric, but
    slot3 0x4100BFC7 1024x1024 = a shared wooden-crate atlas; armour material 0x300045B2:
    slot0 = gold breastplate, largest = the same crate atlas). Verified visually that the
    first slot is the coherent garment diffuse for both the dress and armour materials."""
    gen = _gen()
    if not gen.find_file(mat_did):
        return None
    rm = gen.read_content(mat_did)   # true bytes (read_asset over-reads small records)
    for _off, d40 in _material_40_slots(rm):
        t = _largest_tex_in_40(d40)
        if t is not None:
            return t
    return None


def appearance_diffuses(appearance_did, mesh_did):
    """All coherent garment diffuse texture DIDs for `mesh_did` in a 0x20 appearance
    record (one per wardrobe entry that binds this mesh and has textures installed).
    Returns list of textureDIDs (deduped, file order). The exact summer-dress entry is
    one of these; each textures the mesh coherently."""
    out = []
    seen = set()
    for ent in parse_appearance_entries(appearance_did):
        if ent["mesh"] != mesh_did:
            continue
        for m in ent["materials"]:
            d = material_diffuse(m)
            if d is not None and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def _main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Extract LOTRO 0x41 DXT textures to PNG and resolve "
                    "mesh/appearance material chains to diffuse textures.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("texture", help="extract one 0x41 texture DID to PNG")
    p.add_argument("did", help="texture DID (hex, e.g. 0x41231998)")

    p = sub.add_parser("mesh", help="resolve and extract each submesh's "
                                    "diffuse texture for a 0x06 mesh DID")
    p.add_argument("did", help="mesh DID (hex)")

    p = sub.add_parser("appearance",
                       help="all coherent garment diffuses binding a mesh "
                            "inside a 0x20 worn-appearance record")
    p.add_argument("appearance_did", help="0x20 worn-appearance DID (hex)")
    p.add_argument("mesh_did", help="0x06 mesh DID (hex)")

    for sp in sub.choices.values():
        config.add_game_dir_argument(sp)
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)

    if args.cmd == "appearance":
        adid = int(args.appearance_did, 16)
        mdid = int(args.mesh_did, 16)
        difs = appearance_diffuses(adid, mdid)
        print("appearance 0x%08X mesh 0x%08X -> %d coherent garment diffuses:"
              % (adid, mdid, len(difs)))
        for d in difs:
            print("   0x%08X %s -> %s" % (d, texture_info(d)[:3], extract_texture(d)))
    elif args.cmd == "mesh":
        mdid = int(args.did, 16)
        mt = mesh_textures(mdid)
        print("mesh 0x%08X submesh->texture:" % mdid,
              {k: "0x%08X" % v for k, v in mt.items()})
        for v in sorted(set(mt.values())):
            print("  extracted", extract_texture(v), texture_info(v))
    elif args.cmd == "texture":
        did = int(args.did, 16)
        print("info 0x%08X:" % did, texture_info(did))
        print("extracted", extract_texture(did))
    else:
        ap.print_help()


if __name__ == "__main__":
    _main()
