"""LOTRO item -> garment mesh(es)+material SELECTOR -- CORRECT parse (2026-07-22).

Supersedes the earlier byte-scanning heuristic (which read the wrong AppearanceKey
and mis-parsed the 0x20 record's "slot" field, so it worked for the Summer Dress by
luck and FAILED for the Exquisite Dress). This version parses the item's Turbine
PropertiesSet exactly (see propset.py -- a faithful port of dmorcellet's closed
delta-lotro-dat-utils jar) and then reads the per-body worn-appearance binding.

Chain (both dresses verified to resolve to DISTINCT coherent garments):

  item 0x70______  (client_gamelogic)
    -> its PropertiesSet lives at itemDID + 0x09000000 (the paired 0x79 record)
    -> Item_WornAppearanceMapList : ARRAY(12) of STRUCT, one per (species,sex) body.
       Each struct (real property IDs from the client dictionary 0x34000000):
         Item_SpeciesOfWearer (0x10000748, ENUM_MAPPER)   race enum (23=human, ...)
         Item_SexOfWearer     (0x1000132D, ENUM_MAPPER)   4096=male, 8192=female
         Item_AppearanceKey   (0x10001229, ENUM_MAPPER)   the SELECTOR value
         Item_WornAppearance  (0x10001434, DATA_FILE)     the 0x20 wardrobe DID
       (also top-level PhysObj = 0x0000047F, DATA_FILE, the base-body fallback.)

  worn-appearance 0x20 record (client_general) = a per-body-type table of draw
    entries.  THE SELECTOR: the item's entry is the ONE draw entry whose *slot* field
    == Item_AppearanceKey.  That entry's fixed tail is:
        <key:u32> 01000000 <1.0f> 01000000 <10 zero bytes> <material:0x30> 02000000 10000050
    and its head (just before the two zero dwords preceding the key) is
        <mesh:0x06> [ 0x41200000  0x10000003  <attachMesh:0x06> ]
    giving the item's garment mesh, optional attach mesh, and material.  The material's
    diffuse texture is tex_extract.material_diffuse (0x30 -> first 0x40 slot -> 0x41).

Result (n=2, distinct):
  Summer Dress   0x70021A13  key 0x10000765 -> material 0x300043D2 -> tex 0x41105076
  Exquisite Dress 0x7000DA5B key 0x10000417 -> material 0x30003119 -> tex 0x410DD7A5
"""
import struct
import config
import propset
from tex_extract import material_diffuse

# Archive handles (lazy; config caches one handle per archive).
def _gen():  return config.general()
def _mesh(): return config.mesh_chain()

# fixed tail right after the slot key of a selected draw entry: 1, float 1.0
_BIND_TAIL = struct.pack("<I", 1) + struct.pack("<f", 1.0)
_TRAILER = struct.pack("<II", 0x00000002, 0x10000050)  # material sits just before this


def appearance_map(item_did):
    """Parse the item's Item_WornAppearanceMapList properly.
    Returns list of dicts: {species, sex, key (Item_AppearanceKey int),
    worn_appearance (0x20 DID)} -- one per body-type entry, in file order."""
    _did, props = propset.load_item(item_did)
    ml = props.get("Item_WornAppearanceMapList")
    if not ml:
        raise ValueError("0x%08X: no Item_WornAppearanceMapList" % item_did)
    out = []
    for _name, e in ml:
        out.append(dict(species=e.get("Item_SpeciesOfWearer"),
                        sex=e.get("Item_SexOfWearer"),
                        key=e.get("Item_AppearanceKey"),
                        worn_appearance=e.get("Item_WornAppearance")))
    return out, props.get("PhysObj")


# A draw entry's part list uses each part's own Flags value as its tag. The GARMENT
# is the complex-skinned part tagged 0x1000000C; the others are HANDS (0x10000001)
# and 3-vertex placeholder STUBS (0x10000006 / 0x10000003). Verified across all 7
# Exquisite + all 7 Summer body entries: the 0x1000000C part is always the dress body
# and the only multi-submesh garment mesh.
GARMENT_TAG = 0x1000000C
HANDS_TAG = 0x10000001
# On-disk (compressed) size below which a present part is a 3-vertex placeholder STUB
# (observed ~196-350 B) rather than real geometry; real garments are tens of KB.
_STUB_SIZE = 2000


def resolve_binding(app_did, key):
    """In a 0x20 worn-appearance record, find the draw entry whose slot == `key`
    (an Item_AppearanceKey) and return
    {material, mesh, mesh_present, attach, meshes, parts, garment_part}.

    A draw entry is a LIST of part-meshes: a header [00000000][partCount][1.0f] then,
    per part, [tag 0x10______][meshDID 0x06______][10.0f 0x41200000] (the last part
    omits the trailing 10.0f, then 00000000 00000000), followed by the <key>, its fixed
    tail, the <material 0x30>, and the 00000002 10000050 trailer. Each part's `tag` is
    that mesh's own Flags field.

    THE GARMENT is the part tagged GARMENT_TAG (0x1000000C, the complex-skinned type) --
    NOT the nearest part to the key. Earlier code walked a couple of DIDs backward from
    the key and returned the HANDS part (tag 0x10000001, e.g. 0x0600DAB6) or a 3-vertex
    stub instead of the dress body.

    `mesh` is the garment DID (may be an unshipped INDIRECTION DID for human/some bodies
    -- see mesh_present); `mesh_present` says whether that DID is a direct file in
    client_mesh.dat. `parts` is the full ordered part list ({tag,mesh,size,present}) so
    a caller can render the whole outfit. Returns None if the key is not bound here."""
    c = _gen().read_content(app_did)
    pat = struct.pack("<I", key) + _BIND_TAIL
    i = c.find(pat)
    if i == -1:
        return None
    # material = the 0x30 dword just before the entry trailer that follows the key
    ti = c.find(_TRAILER, i)
    material = None
    if ti != -1:
        mv = struct.unpack_from("<I", c, ti - 4)[0]
        if (mv >> 24) == 0x30:
            material = mv
    # parts: the region from the PREVIOUS entry's trailer up to the key holds this
    # entry's part list. Collect all 0x06 mesh DIDs (with their preceding 0x10 tag).
    pt = c.rfind(_TRAILER, 0, i)
    start = pt + 8 if pt != -1 else max(0, i - 600)
    parts = []
    o = start
    while o < i - 3:
        w = struct.unpack_from("<I", c, o)[0]
        if (w >> 24) == 0x06:
            tag = struct.unpack_from("<I", c, o - 4)[0] if o >= start + 4 else 0
            e = _mesh().find_file(w)
            parts.append(dict(tag=(tag if (tag >> 24) == 0x10 else None),
                              mesh=w, size=(e[2] if e else None),
                              present=e is not None))
        o += 4
    meshes = [p["mesh"] for p in parts]
    # garment = the GARMENT_TAG part. Do NOT substitute a different part if its DID is
    # an unshipped indirection -- report it honestly via mesh_present so a caller can
    # choose a body whose garment IS a direct file.
    gp = next((p for p in parts if p["tag"] == GARMENT_TAG), None)
    if gp is None:
        # unusual layout: fall back to the largest present non-stub part
        pres = sorted((p for p in parts if p["present"] and p["size"]),
                      key=lambda p: -p["size"])
        gp = next((p for p in pres if p["size"] > _STUB_SIZE), None)
    garment = gp["mesh"] if gp else None
    garment_present = bool(gp and gp["present"])
    hands = next((p["mesh"] for p in parts if p["tag"] == HANDS_TAG and p["present"]
                  and p["size"] and p["size"] > _STUB_SIZE), None)
    return dict(material=material, mesh=garment, mesh_present=garment_present,
                attach=hands, meshes=meshes, parts=parts, garment_part=gp)


def resolve_item(item_did):
    """Full, correct resolution. Returns dict with per-body garment bindings:
      {item, phys_obj, appearance_key, bodies:[{species,sex,worn_appearance,mesh,
       attach,material,diffuse,mesh_present}]}.
    appearance_key is reported once if constant across bodies (typical), else None."""
    entries, phys = appearance_map(item_did)
    keys = {e["key"] for e in entries}
    bodies = []
    seen = set()
    for e in entries:
        sig = (e["worn_appearance"], e["key"])
        if sig in seen:
            continue
        seen.add(sig)
        b = resolve_binding(e["worn_appearance"], e["key"])
        mat = b["material"] if b else None
        bodies.append(dict(
            species=e["species"], sex=e["sex"],
            worn_appearance=e["worn_appearance"], key=e["key"],
            mesh=b["mesh"] if b else None, attach=b["attach"] if b else None,
            material=mat, diffuse=material_diffuse(mat) if mat else None,
            parts=b["parts"] if b else None,
            # garment DID present as a direct file? False means it's an unshipped
            # INDIRECTION DID (human/some bodies) -- a separate unsolved problem, NOT
            # a missing garment. Such bodies are simply skipped for direct rendering.
            mesh_present=bool(b and b["mesh_present"])))
    return dict(item=item_did, phys_obj=phys,
                appearance_key=(next(iter(keys)) if len(keys) == 1 else None),
                bodies=bodies)


def renderable_body(res):
    """Pick a body whose primary garment mesh is shipped in client_mesh.dat (so it can
    be rendered), preferring human (species 23). Returns the body dict or None.

    NOTE: for many garments the sp23/human garment mesh is an unshipped INDIRECTION
    DID (a genuine per-garment data hole -- see human_render below), so this typically
    lands on a NON-human body (Elf/Dwarf/Hobbit). Do NOT relabel that as 'human'."""
    cand = [b for b in res["bodies"] if b["mesh_present"]]
    if not cand:
        return None
    cand.sort(key=lambda b: (b["species"] != 23,))
    return cand[0]


# Species enum (LOTRO lore/enums/Species.xml, verified 2026-07-22):
SPECIES = {23: "Man", 65: "Elf", 73: "Dwarf", 81: "Hobbit", 114: "Beorning"}


def _garment_dids_in_record(app_did):
    """Every 0x1000000C-tagged 0x06 garment DID in a 0x20 worn-appearance record
    (byte-packed / unaligned scan). One 0x20 record = one body type's whole wardrobe;
    ~150 distinct garment meshes shared across ~1080 draw entries (garments are grouped
    into shared body-SHAPE classes, differentiated only by material/texture)."""
    c = _gen().read_content(app_did)
    tag = struct.pack("<I", GARMENT_TAG)
    out, idx = [], 0
    while True:
        j = c.find(tag, idx)
        if j == -1:
            break
        m = struct.unpack_from("<I", c, j + 4)[0]
        if (m >> 24) == 0x06:
            out.append(m)
        idx = j + 4
    return out


def human_standin_mesh(app_did, min_z=1.2):
    """Human (Man) garment meshes for MANY specific garments are unshipped indirection
    DIDs -- a per-garment data HOLE (exhaustively confirmed: not a file in any .dat, not
    referenced by any 0x01/0x47 record, no redirect table, no DID transform). BUT Man
    clothing geometry as a whole DOES ship (dozens of present full-length Man-female
    garment meshes per body record), and all garment meshes of one body type share a
    common UV atlas -- so any garment's diffuse maps coherently onto any same-body mesh.

    This returns a PRESENT full-length garment mesh DID from the same body's `0x20`
    record, to use as a STAND-IN Man body for rendering the item's own diffuse when the
    item's exact Man mesh is a hole. It is a documented fallback (correct RACE + shared
    UV), NOT the item's exact geometry. Returns (mesh_did, verts) or (None, 0)."""
    import mesh_decode as _md
    present = []
    seen = set()
    for d in _garment_dids_in_record(app_did):
        if d in seen:
            continue
        seen.add(d)
        e = _mesh().find_file(d)
        if e:
            present.append((d, e[2]))  # (did, on-disk size)
    present.sort(key=lambda x: -x[1])  # biggest first: full dresses are large
    for d, _sz in present:
        try:
            m = _md.decode_mesh(d)
        except Exception:
            continue
        s = _md.stats(m)
        if s["sliver_tris"] == 0:
            zr = s["bbox_max"][2] - s["bbox_min"][2]
            if zr >= min_z:
                return d, s["num_vertices"]
    return None, 0


def human_render(res):
    """Resolve a renderable HUMAN (Man) body for `res` (output of resolve_item).

    Returns a dict {species, sex, worn_appearance, key, mesh, mesh_present, standin,
    diffuse}:
      - If the item's own Man garment mesh IS shipped: standin=False, mesh = that DID.
      - Else (the common case -- unshipped indirection hole): standin=True, mesh = a
        present full-length Man-female garment mesh from the same wardrobe record, to be
        rendered with the item's own `diffuse` via the shared per-body UV atlas.
    Prefers the female (sx 8192) Man body; falls back to any Man body. None if no Man
    body entry exists at all."""
    man = [b for b in res["bodies"] if b["species"] == 23]
    if not man:
        return None
    man.sort(key=lambda b: (b["sex"] != 8192,))  # prefer female
    b = man[0]
    if b["mesh_present"]:
        return dict(species=23, sex=b["sex"], worn_appearance=b["worn_appearance"],
                    key=b["key"], mesh=b["mesh"], mesh_present=True, standin=False,
                    diffuse=b["diffuse"])
    sm, _v = human_standin_mesh(b["worn_appearance"])
    return dict(species=23, sex=b["sex"], worn_appearance=b["worn_appearance"],
                key=b["key"], mesh=sm, mesh_present=False, standin=True,
                diffuse=b["diffuse"])


def _h(v):
    return "0x%08X" % v if v is not None else "--"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Resolve a LOTRO item DID to its per-body garment mesh, "
                    "material and diffuse texture bindings.")
    ap.add_argument("item", nargs="?", default="0x70021A13",
                    help="item DID (hex, default: the Sleeveless Summer Dress)")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)
    item = int(args.item, 16)
    r = resolve_item(item)
    print("item %s  PhysObj %s  Item_AppearanceKey %s"
          % (_h(r["item"]), _h(r["phys_obj"]), _h(r["appearance_key"])))
    mats = set()
    for b in r["bodies"]:
        mats.add(b["material"])
        print("  body %s sp%-3s sx%-4s -> garment %s%s hands %s material %s diffuse %s"
              % (_h(b["worn_appearance"]), b["species"], b["sex"], _h(b["mesh"]),
                 "" if b["mesh_present"] else "(INDIRECTION-unshipped)", _h(b["attach"]),
                 _h(b["material"]), _h(b["diffuse"])))
    print("distinct garment materials:", sorted(_h(m) for m in mats))
