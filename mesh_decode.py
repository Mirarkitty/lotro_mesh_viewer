"""LOTRO client_mesh.dat GfxObj decoder -> renderable triangles.

Decodes the LOTRO GfxObj-descendant geometry format (DID type 0x06) into
positions + normals + triangles. STATIC meshes (Flags != 0x1000____) via decode_mesh;
SKINNED meshes (Flags 0x1000____) via decode_skinned -- both fully decoded and
validated (skinned validated visually as coherent garment surfaces). The skinned
vertex record carries packed bone indices/weights (skin data), which are skipped for
rendering (position+normal+triangles only). Full format in docs/mesh-format.md.

CLI:  python3 mesh_decode.py <mesh_did> [--json FILE] [--no-textures]
      Decodes one mesh, prints validation stats (see `stats`), optionally
      writes the viewer JSON.

Reference sample: 0x06001989 (static, Flags=0x00000001, 7186 bytes).

--- STATIC GfxObj byte layout (little-endian) ---
  0x00  uint32  Id            (self DID, type 0x06)
  0x04  uint32  Flags         (0x00000001 = static family here)
  0x08  uint32  numSurfaces   (S)
  0x0C  uint32  surfaceDID[S] (0x31______ render-property DIDs)
  ...   uint32  numTextures   (T)
  ...   uint32  textureDID[T] (0x30______ material/texture DIDs)
  ...   uint32  numVertices   (V)
  ...           vertex[V]     -- variable length, see below
  ...           <physics/BSP float block>  (not needed for rendering)
  ...   7*float bbox/sortcenter (min3, max3, radius)
  ...   uint32  0
  ...   uint32  numIndexBufs? (=1 on sample)
  ...   uint32  numIndices    (I, multiple of 3)
  ...   uint16  index[I]      (triangle list, values < V)
  ...           <trailing submesh/material descriptors>

  Vertex record (variable length, NO per-vertex key/count prefix):
    float32 pos[3]
    float32 normal[3]        (unit length -- used to validate the parse)
    float32 uv[2] * numUVs   (numUVs is 0 or 1 on the sample; determined by
                              scanning: consume UV pairs until the next 12 bytes
                              form a unit-length vector == next vertex normal)
"""
import struct, math

def _dat_handle():
    """The mesh archive chain (client_mesh.dat + aux), opened lazily so that
    importing this module never touches the filesystem."""
    import config
    return config.mesh_chain()


def _f(raw, o):
    return struct.unpack("<f", raw[o:o + 4])[0]


def _u32(raw, o):
    return struct.unpack("<I", raw[o:o + 4])[0]


def _vlen(raw, o):
    try:
        x, y, z = _f(raw, o), _f(raw, o + 4), _f(raw, o + 8)
    except struct.error:
        return 9.0
    return math.sqrt(x * x + y * y + z * z)


def _is_unit(raw, o):
    return 0.985 < _vlen(raw, o) < 1.015


def _sane_pos(raw, o, lim=1e4):
    # reject NaN/inf and garbage floats (bone data ~1e30); model-space positions are
    # small but some props reach tens of units, so the real discriminator is the
    # unit-length normal check in _is_vertex, not this magnitude bound.
    try:
        for j in range(3):
            v = _f(raw, o + j * 4)
            if not math.isfinite(v) or abs(v) >= lim:
                return False
        return True
    except struct.error:
        return False


def _is_vertex(raw, o):
    return _sane_pos(raw, o) and _is_unit(raw, o + 12)


def _vertex_mask(raw):
    """Vectorised `_is_vertex` over EVERY byte offset: returns a bytes of
    len(raw) where m[o] is truthy iff _is_vertex(raw, o).

    Why: the scan below probed _is_vertex per offset per candidate stride —
    ~3M Python calls (6 struct.unpacks each) and ~94% of compose() wall time
    for a big garment. The six floats a vertex probe reads sit at o, o+4 …
    o+20, i.e. ALL at the same offset residue mod 4, so one strided float32
    view per residue evaluates the whole predicate array-at-a-time. Results
    are bit-identical to the scalar predicate (guarded by test_mesh_decode.py).
    """
    import numpy as np
    n = len(raw)
    mask = np.zeros(n, dtype=bool)
    for r in range(4):
        a = np.frombuffer(raw, dtype="<f4", count=(n - r) // 4, offset=r)
        m = a.size - 5                      # need a[i]..a[i+5] in range
        if m <= 0:
            continue
        def w(k):                           # a[k : k+m], the k-th float of the probe
            return a[k:k + m]
        with np.errstate(invalid="ignore", over="ignore"):
            ok = np.ones(m, dtype=bool)
            for k in range(3):              # _sane_pos: finite and |v| < 1e4
                v = w(k)
                ok &= np.isfinite(v) & (np.abs(v) < 1e4)
            nx, ny, nz = w(3).astype(np.float64), w(4).astype(np.float64), w(5).astype(np.float64)
            ln = np.sqrt(nx * nx + ny * ny + nz * nz)
            ok &= (ln > 0.985) & (ln < 1.015)   # _is_unit on the normal
        mask[r:r + 4 * m:4] = ok
    return mask.tobytes()


_VBLK_CACHE = {}

def _find_vertex_blocks(raw):
    """Find each submesh's vertex block. A block is [uint32 count][count vertices]
    at a fixed stride (71 B for the dress-mesh vertex format). Returns list of
    (vert_start_offset, count, stride). The stride is auto-detected per block so
    other vertex formats still parse.

    Memoised on the record bytes: decode_mesh() and export_skinned.skin_arrays()
    both scan the SAME buffer for every part of a composed outfit, so without
    this the (expensive) scan ran twice per part."""
    ck = (len(raw), hash(raw))
    hit = _VBLK_CACHE.get(ck)
    if hit is not None:
        return hit
    n = len(raw)
    isv = _vertex_mask(raw)
    blocks = []
    o = 8
    while o < n - 40:
        c = _u32(raw, o)
        if 8 <= c <= 60000 and isv[o + 4]:      # isv[vs] is stride-invariant:
            vs = o + 4                          # hoisted out of the stride loop
            # Try EVERY plausible stride and accept the one whose consecutive
            # unit-normal run matches the declared count `c`. Crucial for the
            # multi-submesh variant where different submeshes use DIFFERENT vertex
            # strides (e.g. 0x060028FC mixes 76/71/61 B records): a shorter WRONG
            # stride can pass the 3-consecutive-vertex probe first, so we must not
            # give up on that block -- keep scanning larger strides for the one
            # that actually spans `c` records. Earlier code broke on the first
            # probe hit and dropped the whole block if its run didn't match,
            # silently losing every stride-76 submesh (index region then unfindable).
            chosen = None
            for s in range(44, 264):
                if not (vs + 2 * s + 24 <= n and isv[vs]
                        and isv[vs + s] and isv[vs + 2 * s]):
                    continue
                run = 0
                p = vs
                while p + 24 <= n and isv[p]:
                    run += 1
                    p += s
                if abs(run - c) <= 2 and run >= 3:   # count prefix matches the run
                    chosen = s
                    break
            if chosen:
                blocks.append((vs, c, chosen))
                o = vs + c * chosen
                continue
        o += 1
    if len(_VBLK_CACHE) > 64:
        _VBLK_CACHE.clear()
    _VBLK_CACHE[ck] = blocks
    return blocks


def _find_index_region(raw, counts):
    """Locate the start of the sequential index-buffer region: the offset from which
    a sequential parse of [uint32 count][count uint16] yields exactly len(counts)
    buffers whose max index == the matching submesh's vertex_count - 1 (submesh-local
    0-based indices). Returns (start_offset, list_of_triangle_lists)."""
    n = len(raw)
    ns = len(counts)

    def try_at(o0):
        o = o0
        bufs = []
        for k in range(ns):
            if o + 4 > n:
                return None
            c = _u32(raw, o)
            if not (3 <= c <= 200000 and c % 3 == 0 and o + 4 + 2 * c <= n):
                return None
            seg = struct.unpack("<%dH" % c, raw[o + 4:o + 4 + 2 * c])
            if max(seg) != counts[k] - 1:
                return None
            bufs.append([[seg[i], seg[i + 1], seg[i + 2]] for i in range(0, c, 3)])
            o = o + 4 + 2 * c
        return bufs

    # index region follows all vertex+bone sections; scan forward from a safe point
    for o in range(8, n - 8):
        b = try_at(o)
        if b is not None:
            return o, b
    return None, None


def _decode_gfxobj(raw):
    """Unified decoder for BOTH static and skinned LOTRO GfxObj meshes -- they are the
    SAME format, static being the 1-submesh case.

    Confirmed layout (validated VISUALLY: static 0x06001989 spindle, 0x06011F81/82;
    skinned 0x0600006B/D250/D54A garments):
      header [Id][Flags][numSurfaces + 0x31 DIDs][numTex + 0x30 DIDs] ...
      then, per submesh (geometry group), in order:
        uint32   vertexCount
        vertex[vertexCount]   FIXED per-vertex stride (56 B static, 71 B skinned dress):
            float32 pos[3]; float32 normal[3] (unit); ... uv (+ skinned: tangent frame
            and packed bone indices/weights, skipped for rendering)
        float32  bbox[6]                 submesh min[3], max[3]
        uint32   boneDataCount; uint32 boneData[boneDataCount]   (0 for static)
      then the index region, per submesh sequentially:
        uint32   indexCount              (multiple of 3)
        uint16   index[indexCount]       triangle list, SUBMESH-LOCAL 0-based
                                         (maxidx == vertexCount-1)

    Vertices are concatenated across submeshes; each submesh's local triangle indices
    are offset by the running vertex count -> one dense mesh. The per-vertex stride is
    auto-detected per block (unit-normal run must equal the [vertexCount] prefix), and
    the index region is found by requiring a sequential [count][u16] parse to yield one
    buffer per submesh with maxidx == vertexCount-1. Byte-packed tables can start at
    ODD offsets -- offsets are read exactly, never assumed 4-aligned.
    """
    did0 = _u32(raw, 0)
    flags = _u32(raw, 4)
    blocks = _find_vertex_blocks(raw)
    if not blocks:
        raise ValueError("0x%08X: no vertex blocks found" % did0)
    counts = [c for (_vs, c, _s) in blocks]
    _istart, bufs = _find_index_region(raw, counts)
    if bufs is None:
        raise ValueError("0x%08X: index region not found (submesh counts %s)"
                         % (did0, counts))

    verts, normals, uvs, tris = [], [], [], []
    groups = []
    voff = 0
    tri_start = 0
    for si, ((vs, c, s), tb) in enumerate(zip(blocks, bufs)):
        for i in range(c):
            b = vs + i * s
            verts.append([_f(raw, b), _f(raw, b + 4), _f(raw, b + 8)])
            normals.append([_f(raw, b + 12), _f(raw, b + 16), _f(raw, b + 20)])
            # per-vertex UV: two float32 at offset 24 (pos[3]+normal[3] = 24 bytes),
            # verified in-[0,1]-ish for both static and skinned dress records.
            uvs.append([_f(raw, b + 24), _f(raw, b + 28)])
        for t in tb:
            tris.append([t[0] + voff, t[1] + voff, t[2] + voff])
        groups.append({
            "submesh": si,
            "vert_start": voff, "vert_count": c,
            "tri_start": tri_start, "tri_count": len(tb),
        })
        voff += c
        tri_start += len(tb)
    return {
        "id": "0x%08X" % did0,
        "flags": "0x%08X" % flags,
        "num_submeshes": len(blocks),
        "vertices": verts,
        "normals": normals,
        "uvs": uvs,
        "triangles": tris,
        "groups": groups,
    }


def _read(did):
    """Raw decompressed record bytes for a mesh DID (asset framing)."""
    dat = _dat_handle()
    e = dat.find_file(did)
    if e is None:
        raise KeyError("DID 0x%08X not in the mesh archives" % did)
    _usize, raw = dat.read_asset(e[1], e[3])
    return raw


def decode_mesh(did, with_textures=True, texture_override=None):
    """Decode ANY LOTRO GfxObj mesh (static or skinned) -> {id, vertices, normals,
    uvs, triangles, groups} with dense 0..V-1 indices. Static and skinned share one
    format; this is the unified entry point.

    groups is a per-submesh list: {submesh, vert_start, vert_count, tri_start,
    tri_count, texture}. `texture` is the resolved diffuse 0x41 texture DID string
    (added when with_textures, resolved via tex_extract.mesh_textures) or None. The
    three.js viewer builds one geometry group per entry (tri_start*3, tri_count*3) so
    each submesh can carry its own texture map.

    texture_override: optional int DID applied as the diffuse to ALL submeshes,
    overriding auto-resolution. Used for meshes whose diffuse is bound at the
    appearance/outfit level rather than in the mesh's own surface graph (e.g. the dress
    body 0x0600D54A, whose surface references only the normal/gloss shader instance so
    its diffuse 0x41231998 -- verified by coherent UV mapping -- is not surface-local)."""
    m = _decode_gfxobj(_read(did))
    groups = m["groups"]
    if texture_override is not None:
        for g in groups:
            g["texture"] = "0x%08X" % texture_override
            g["texture_source"] = "override (outfit-level diffuse)"
    elif with_textures:
        try:
            from tex_extract import mesh_textures
            tmap = mesh_textures(did)
        except Exception as e:  # never silence -- record why textures are absent
            tmap = {}
            for g in groups:
                g["texture_error"] = str(e)
        for g in groups:
            t = tmap.get(g["submesh"])
            g["texture"] = ("0x%08X" % t) if t is not None else None
    return {"id": m["id"], "flags": m["flags"], "num_submeshes": m["num_submeshes"],
            "vertices": m["vertices"], "normals": m["normals"], "uvs": m["uvs"],
            "triangles": m["triangles"], "groups": groups}


def decode_skinned(did):
    """Alias for decode_mesh kept for compatibility -- also returns flags/num_submeshes.
    Skinned and static are the same format (see _decode_gfxobj)."""
    return _decode_gfxobj(_read(did))


def stats(m):
    V = m["vertices"]; T = m["triangles"]
    xs = [v[0] for v in V]; ys = [v[1] for v in V]; zs = [v[2] for v in V]
    used = set(i for t in T for i in t)
    maxidx = max((i for t in T for i in t), default=-1)
    deg = sum(1 for t in T if len(set(t)) < 3)
    nan = sum(1 for v in V for c in v if not math.isfinite(c))
    diag = math.dist((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))) if V else 0.0
    # Sliver check: a correct solid has all triangle edges << bbox diagonal. Slivers
    # (edges spanning ~the whole mesh) are the signature of a scrambled index/vertex
    # mapping that still passes in-range/degenerate checks. Flag edges > 0.5*diag.
    def _edges():
        for t in T:
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                if a < len(V) and b < len(V):
                    yield math.dist(V[a], V[b])
    edges = list(_edges())
    max_edge = max(edges) if edges else 0.0
    slivers = sum(1 for e in edges if e > 0.5 * diag) if diag else 0
    return {
        "num_vertices": len(V), "num_triangles": len(T),
        "bbox_min": [min(xs), min(ys), min(zs)],
        "bbox_max": [max(xs), max(ys), max(zs)],
        "max_index": maxidx, "indices_in_range": maxidx < len(V),
        "vertices_referenced": len(used), "degenerate_tris": deg, "nan_coords": nan,
        "bbox_diag": diag, "max_edge": max_edge, "sliver_tris": slivers,
    }


if __name__ == "__main__":
    import argparse, json, config
    ap = argparse.ArgumentParser(
        description="Decode a LOTRO GfxObj mesh (static or skinned) and print "
                    "validation statistics; optionally write the viewer JSON.")
    ap.add_argument("did", help="mesh DID (hex, e.g. 0x06001989)")
    ap.add_argument("--json", metavar="FILE",
                    help="write the decoded mesh as viewer JSON to FILE "
                         "('-' for stdout)")
    ap.add_argument("--no-textures", action="store_true",
                    help="skip diffuse-texture resolution (faster; groups get "
                         "texture=None)")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)

    m = decode_mesh(int(args.did, 16), with_textures=not args.no_textures)
    s = stats(m)
    print("mesh %s" % m["id"])
    for k, v in s.items():
        print("  %-20s %s" % (k, v))
    if args.json == "-":
        import sys
        json.dump(m, sys.stdout)
    elif args.json:
        import os
        d = os.path.dirname(args.json)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(m, f)
        print("wrote %s" % args.json)
