#!/usr/bin/env python3
"""explore.py — dig into the LOTRO data set from any starting point.

Takes a search term or a DID and prints everything the toolkit can resolve
from there as a tree: items -> per-body (race/sex) wardrobe bindings ->
dye-variant blocks -> entry parts with LOD values, materials -> diffuse
textures, surfaces -> shaders, mesh submeshes, and the REVERSE direction
too (which wardrobe entries and items use a given mesh or material).

Repeated subtrees are printed ONCE and tagged `[@N]`; later occurrences
print `-> @N` instead of re-expanding (the same material serves seven
bodies, the same surface every submesh — without this the tree explodes).

    python3 explore.py aurochs                # name search -> item trees
    python3 explore.py 0x7000DA5B             # item DID
    python3 explore.py 1879104091             # the same item, decimal
    python3 explore.py 0x20001E58             # worn-appearance record
    python3 explore.py 0x0600D250             # mesh: submeshes + who uses it
    python3 explore.py 0x30003119             # material: diffuse + who binds it
    python3 explore.py 0x310092EA             # surface: shader + materials
    python3 explore.py 0x41105076             # texture info
    python3 explore.py 0x2B0007A0             # shader classification

Options:
    --deep       also decode meshes (submesh count, strides, vertex counts,
                 sliver check) — slower
    --limit N    max items per name search (default 10)

Needs items_catalog.jsonl (build once with items_catalog.py) for name
search and reverse lookups; DID digs that don't need the catalog work
without it.
"""
import argparse
import json
import os
import sys

import config

# ---- tree printing ----------------------------------------------------------

class Node:
    def __init__(self, label):
        self.label = label
        self.children = []

    def add(self, label):
        n = Node(label)
        self.children.append(n)
        return n

    def render(self, prefix="", out=None):
        if out is None:
            out = []
            out.append(self.label)
        for i, c in enumerate(self.children):
            last = i == len(self.children) - 1
            out.append(prefix + ("└─ " if last else "├─ ") + c.label)
            c.render(prefix + ("   " if last else "│  "), out)
        return out


def _h(v):
    return "0x%08X" % v if v is not None else "--"


# ---- @N dedup references ----------------------------------------------------

class _Refs:
    """First expansion of an entity gets a [@N] tag; every later encounter
    prints a one-line `-> @N` back-reference instead of the subtree.
    `used` records which numbers were actually back-referenced, so
    finalize_refs() can drop the tags nothing points at."""

    def __init__(self):
        self.map = {}
        self.next = 1
        self.used = set()

    def get(self, kind, key):
        """(ref_number, first_time)"""
        k = (kind, key)
        if k in self.map:
            self.used.add(self.map[k])
            return self.map[k], False
        self.map[k] = self.next
        self.next += 1
        return self.map[k], True


REFS = _Refs()

_REF_RE = None

def finalize_refs(roots):
    """Post-process built trees before printing: strip `[@N]` tags nothing
    references, and renumber the surviving refs 1..k in appearance order.
    Must run ONCE over ALL trees of an invocation (refs cross trees)."""
    import re
    order = []

    def collect(n):
        for m in re.finditer(r"\[@(\d+)\]", n.label):
            num = int(m.group(1))
            if num in REFS.used and num not in order:
                order.append(num)
        for c in n.children:
            collect(c)

    for r in roots:
        collect(r)
    ren = {old: i + 1 for i, old in enumerate(order)}

    def fix(n):
        # first the definition tags (drop unused, renumber used via a
        # placeholder so the second pass can't double-rewrite them) ...
        n.label = re.sub(
            r"\s*\[@(\d+)\]",
            lambda m: ("  [%%%d%%]" % ren[int(m.group(1))])
                      if int(m.group(1)) in ren else "",
            n.label)
        # ... then the back-references (all used by definition)
        n.label = re.sub(
            r"@(\d+)\b",
            lambda m: "%%%s%%" % ren[int(m.group(1))]
                      if int(m.group(1)) in ren else m.group(0),
            n.label)
        n.label = re.sub(r"%(\d+)%", r"@\1", n.label)
        for c in n.children:
            fix(c)

    for r in roots:
        fix(r)


# ---- catalog access ---------------------------------------------------------

_catalog = None

def catalog(required=False):
    global _catalog
    if _catalog is None:
        fp = os.path.join(config.out_dir(), "items_catalog.jsonl")
        try:
            with open(fp) as f:
                _catalog = [json.loads(l) for l in f]
        except OSError:
            if required:
                sys.exit("this lookup needs %s - build it once with:  "
                         "python3 items_catalog.py" % fp)
            _catalog = []
    return _catalog


_dyes = None

def dye_name(float_code):
    """Dye name for an Item_ClothingColor floatCode ('Crimson (0.04)'), or
    just the code when the palette has no entry for it. float32 storage
    wobbles the value (0.6000000238...), so match with tolerance."""
    global _dyes
    if not float_code:
        return None
    if _dyes is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "dye_colors.json")) as f:
                _dyes = {v["floatCode"]: k for k, v in json.load(f).items()
                         if isinstance(v, dict) and "floatCode" in v}
        except (OSError, ValueError):
            _dyes = {}
    for fc, name in _dyes.items():
        if abs(fc - float_code) < 5e-3:
            return "%s (%.2f)" % (name, float_code)
    return "code %.2f (not in the scraped palette)" % float_code


SPECIES = {23: "Man", 65: "Elf", 73: "Dwarf", 81: "Hobbit", 114: "Beorning",
           117: "HighElf", 120: "Stoutaxe", 125: "sp125"}
SEX = {0x1000: "M", 0x2000: "F"}

def body_label(species, sex):
    return "%s-%s" % (SPECIES.get(species, species), SEX.get(sex, "?"))


# Part tags, chest-family semantics (docs/wardrobe.md). Tag meaning varies by
# record family, so these names are hints, not law.
TAG_NAMES = {0x1000000C: "garment", 0x10000001: "hands", 0x10000003: "legs-slot",
             0x10000006: "feet-slot", 0x1000000E: "hair", 0x10000002: "head",
             0x10000007: "beard"}
STUB_BYTES = 2000


# ---- shared, ref-deduped expansion helpers ----------------------------------

def mesh_entry(did):
    """(size, present) for a mesh DID from the mesh archives' directory."""
    e = config.mesh_chain().find_file(did)
    return (e[2], True) if e else (None, False)


def surface_node(parent, surf_did):
    """Surface subtree under `parent`: shader + material chain. Deduped."""
    n, first = REFS.get("surface", surf_did)
    if not first:
        parent.add("surface %s  -> @%d" % (_h(surf_did), n))
        return
    node = parent.add("surface %s  [@%d]" % (_h(surf_did), n))
    import shaders as sh
    import tex_extract as tx
    s = sh.surface_shader(surf_did)
    if s:
        name, alpha, dyeable, metallic, _note = sh.info(s)
        node.add("shader %s  %s%s%s%s" % (
            _h(s), name,
            "  ALPHA-TEST" if alpha else "",
            "  dyeable" if dyeable else "",
            "  metallic" if metallic else ""))
    try:
        mats = tx._compact_surface_materials(config.general().read_content(surf_did))
    except Exception:
        mats = None
    for m in (mats or []):
        material_node(node, m)
    if mats is None:
        d = tx.diffuse_for_surface(surf_did)
        if d:
            texture_node(node, d, "diffuse (legacy graph scan)")


def material_node(parent, mat_did):
    """Material subtree under `parent`: diffuse resolution. Deduped."""
    n, first = REFS.get("material", mat_did)
    if not first:
        parent.add("material %s  -> @%d" % (_h(mat_did), n))
        return
    node = parent.add("material %s  [@%d]" % (_h(mat_did), n))
    import tex_extract as tx
    d = tx.material_diffuse(mat_did)
    if d is not None:
        texture_node(node, d, "diffuse")
    else:
        node.add("diffuse: unresolved")


def texture_node(parent, tex_did, label="texture"):
    """One texture line. Deduped (dimensions shown once)."""
    n, first = REFS.get("texture", tex_did)
    if not first:
        parent.add("%s %s  -> @%d" % (label, _h(tex_did), n))
        return
    import tex_extract as tx
    info = tx.texture_info(tex_did)
    if info:
        w, h, fourcc, placeholder = info
        parent.add("%s %s  %dx%d %s%s  [@%d]" % (
            label, _h(tex_did), w, h, fourcc.decode(),
            "  (placeholder)" if placeholder else "", n))
    else:
        parent.add("%s %s  (not a decodable texture)  [@%d]"
                   % (label, _h(tex_did), n))


def mesh_deep_node(parent, mesh_did):
    """Decode a mesh into a subtree (submeshes, strides, surfaces). Deduped."""
    n, first = REFS.get("mesh", mesh_did)
    if not first:
        parent.add("-> @%d (already decoded above)" % n)
        return
    import mesh_decode as md
    import tex_extract as tx
    try:
        raw = md._read(mesh_did)
        blocks = md._find_vertex_blocks(raw)
        m = md.decode_mesh(mesh_did, with_textures=False)
        s = md.stats(m)
    except Exception as ex:
        parent.add("decode FAILED: %s" % str(ex)[:80])
        return
    parent.add("%dv %dt  %d submesh(es)  sliver_tris=%d  [@%d]" % (
        s["num_vertices"], s["num_triangles"], m["num_submeshes"],
        s["sliver_tris"], n))
    surfs = tx._mesh_surfaces(raw)
    # Submeshes sharing one surface are LOD variants of the same visual part;
    # compose.py's LOD dedup keeps the largest per surface when rendering.
    by_surf = {}
    for i, (g, (_vs, cnt, stride)) in enumerate(zip(m["groups"], blocks)):
        surf = surfs[i] if i < len(surfs) else None
        by_surf.setdefault(surf, []).append(cnt)
        sub = parent.add("submesh %d  %d verts  stride %d B" % (i, cnt, stride))
        if surf is not None:
            surface_node(sub, surf)
    for surf, counts in by_surf.items():
        if len(counts) > 1:
            parent.add("LOD group on surface %s: %d submeshes (%s verts) - "
                       "renderers keep the largest"
                       % (_h(surf), len(counts),
                          "/".join(str(c) for c in sorted(counts, reverse=True))))


def part_line(parent, part, deep=False):
    """One wardrobe part: tag name, mesh, size/stub/hole, LOD value."""
    tag = part.get("tag")
    tagname = TAG_NAMES.get(tag, _h(tag) if tag else "untagged")
    size, present = mesh_entry(part["mesh"])
    lod = part.get("lod")
    lodtxt = ("  lod=%g" % lod) if lod not in (None, 0.0) else ""
    if not present:
        desc = "NOT SHIPPED (indirection DID - see docs/limitations.md)"
    elif (size or 0) < STUB_BYTES:
        desc = "stub %d B (blanks that slot)" % size
    else:
        desc = "%d B" % size
    node = parent.add("part %-9s mesh %s  %s%s"
                      % (tagname, _h(part["mesh"]), desc, lodtxt))
    if deep and present and (size or 0) >= STUB_BYTES:
        mesh_deep_node(node, part["mesh"])
    return node


# ---- wardrobe entry rendering (shared by item and appearance digs) ----------

def entry_blocks_node(parent, entry, deep=False):
    """Render a wearable entry's blocks: block 0 in full (materials + parts
    with LODs), further blocks summarized as the dye variants they are."""
    blocks = entry["blocks"]
    if not blocks:
        parent.add("(no blocks)")
        return
    b0 = blocks[0]
    n0 = parent.add("block 0  q=%.2f  (%d material group%s, %d part%s)" % (
        b0["q"], len(b0["groups"]), "s" if len(b0["groups"]) != 1 else "",
        len(b0["parts"]), "s" if len(b0["parts"]) != 1 else ""))
    for g in b0["groups"]:
        if g["material"]:
            material_node(n0, g["material"])
    for p in b0["parts"]:
        part_line(n0, p, deep=deep)
    if len(blocks) > 1:
        qs = [b["q"] for b in blocks[1:]]
        extra = parent.add(
            "blocks 1-%d  q=%.2f..%.2f (dye/texture variants - q is the dye "
            "floatCode, docs/dyes.md)" % (len(blocks) - 1, min(qs), max(qs)))
        mats = []
        seen = set()
        for b in blocks[1:]:
            for g in b["groups"]:
                if g["material"] and g["material"] not in seen:
                    seen.add(g["material"])
                    mats.append(g["material"])
        for m in mats[:8]:
            material_node(extra, m)
        if len(mats) > 8:
            extra.add("... %d more variant materials" % (len(mats) - 8))
        # parts normally repeat identically across blocks; say so if not
        p0 = [p["mesh"] for p in b0["parts"]]
        if any([p["mesh"] for p in b["parts"]] != p0 for b in blocks[1:]):
            extra.add("NOTE: part lists differ between blocks")


def wearable_entry(app_did, key):
    """The parsed 0x20 entry for (app, key), or None."""
    import wearable2
    try:
        rec = wearable2.parse_record(config.general().read_content(app_did))
    except Exception:
        return None
    return next((e for e in wearable2.entries(rec) if e["key"] == key), None)


# ---- reverse lookups over the wardrobe records ------------------------------

_parsed_apps = None

def parsed_apps():
    """{app_did: parsed 0x20 record} over every appearance DID in the catalog
    (~70 distinct records cover every wearable)."""
    global _parsed_apps
    if _parsed_apps is not None:
        return _parsed_apps
    import wearable2
    _parsed_apps = {}
    apps = {b["app"] for r in catalog(required=True) for b in r["bodies"]}
    for a in sorted(apps):
        try:
            _parsed_apps[a] = wearable2.parse_record(
                config.general().read_content(a))
        except Exception:
            pass
    return _parsed_apps


def find_users(pred, what):
    """Wardrobe entries whose blocks satisfy pred(block) -> tree of
    app/key/items. pred gets each block dict from wearable2."""
    import wearable2
    hits = {}          # (app, key) -> reason string
    for a, rec in parsed_apps().items():
        for e in wearable2.entries(rec):
            for b in e["blocks"]:
                r = pred(b)
                if r:
                    hits[(a, e["key"])] = r
                    break
    root = Node("used by %d wardrobe entr%s%s" % (
        len(hits), "y" if len(hits) == 1 else "ies",
        " (%s)" % what if what else ""))
    if not hits:
        return root
    by_binding = {}
    for r in catalog():
        for b in r["bodies"]:
            by_binding.setdefault((b["app"], b["key"]), []).append((r, b))
    for (a, k), reason in sorted(hits.items()):
        items = by_binding.get((a, k), [])
        n = root.add("app %s  key %s  %s" % (_h(a), _h(k), reason))
        seen = set()
        shown = 0
        for r, b in items:
            nm = r.get("name") or "(unnamed)"
            if nm in seen:
                continue
            seen.add(nm)
            if shown >= 4:
                continue
            shown += 1
            n.add("item 0x%08X  %s  [%s]" % (
                r["did"], nm, body_label(b["species"], b["sex"])))
        if len(seen) > shown:
            n.add("... %d more distinct items (%d rows total)"
                  % (len(seen) - shown, len(items)))
    return root


# ---- per-type digs ----------------------------------------------------------

def dig_item(did, deep=False):
    import selector
    row = next((r for r in catalog() if r["did"] == did), None)
    name = (row.get("name") if row else None) or "(not in catalog)"
    root = Node("item %s  %s" % (_h(did), name))
    try:
        res = selector.resolve_item(did)
    except Exception as ex:
        root.add("resolve FAILED: %s" % str(ex)[:100])
        return root
    dn = dye_name(row.get("clothing_color")) if row else None
    if dn:
        root.add("default dye: %s  (Item_ClothingColor - a render-time tint, "
                 "docs/dyes.md)" % dn)
    if res["appearance_key"] is not None:
        root.add("appearance key %s (constant across bodies)"
                 % _h(res["appearance_key"]))
    if res["phys_obj"]:
        root.add("PhysObj %s (base-body fallback)" % _h(res["phys_obj"]))
    for b in res["bodies"]:
        n = root.add("body %-10s app %s  key %s" % (
            body_label(b["species"], b["sex"]),
            _h(b["worn_appearance"]), _h(b["key"])))
        e = wearable_entry(b["worn_appearance"], b["key"])
        if e is not None:
            # (app, key) pairs shared between races render identically:
            # reference instead of repeating the whole entry.
            rn, first = REFS.get("entry", (b["worn_appearance"], b["key"]))
            if not first:
                n.add("entry -> @%d (same record+key, identical binding)" % rn)
                continue
            n.label += "  [@%d]" % rn
            entry_blocks_node(n, e, deep=deep)
        else:
            # strict parse unavailable: fall back to the selector's flat view
            if b["material"]:
                material_node(n, b["material"])
            for p in (b["parts"] or []):
                part_line(n, p, deep=deep)
    return root


def dig_appearance(did, deep=False):
    import wearable2
    rec = wearable2.parse_record(config.general().read_content(did))
    es = wearable2.entries(rec)
    root = Node("worn-appearance %s  (%d entries)" % (_h(did), len(es)))
    by_binding = {}
    for r in catalog():
        for b in r["bodies"]:
            if b["app"] == did:
                by_binding.setdefault(b["key"], []).append(r)
    for e in es:
        n = root.add("entry key %s  (%d block%s)" % (
            _h(e["key"]), len(e["blocks"]),
            "s" if len(e["blocks"]) != 1 else ""))
        items = by_binding.get(e["key"], [])
        seen = set()
        for r in items[:4]:
            nm = r.get("name")
            if nm and nm not in seen:
                seen.add(nm)
                n.add("item 0x%08X  %s" % (r["did"], nm))
        if len(items) > 4:
            n.add("... %d more items" % (len(items) - 4))
        entry_blocks_node(n, e, deep=deep)
    return root


def dig_mesh(did, deep=False):
    root = Node("mesh %s" % _h(did))
    size, present = mesh_entry(did)
    if not present:
        root.add("NOT SHIPPED (indirection DID - data hole, see "
                 "docs/limitations.md)")
    elif (size or 0) < STUB_BYTES:
        root.add("stub (%d B placeholder)" % size)
    else:
        mesh_deep_node(root, did)       # a direct mesh query is always deep
    if catalog():
        root.children.append(find_users(
            lambda b: next(("as %s part" % TAG_NAMES.get(p["tag"], _h(p["tag"]))
                            for p in b["parts"] if p["mesh"] == did), None),
            "wardrobe part"))
    return root


def dig_material(did, deep=False):
    tmp = Node("")
    material_node(tmp, did)
    root = tmp.children[0]
    if catalog():
        root.children.append(find_users(
            lambda b: next(("bound in material group"
                            for g in b["groups"] if g["material"] == did), None),
            "material binding"))
    return root


def dig_surface(did, deep=False):
    tmp = Node("")
    surface_node(tmp, did)
    return tmp.children[0]


def dig_texture(did, deep=False):
    root = Node("texture %s" % _h(did))
    texture_node(root, did)
    import tex_extract as tx
    try:
        root.add("extracted -> %s" % tx.extract_texture(did))
    except Exception as ex:
        root.add("extract failed: %s" % str(ex)[:80])
    return root


def dig_shader(did, deep=False):
    import shaders as sh
    name, alpha, dyeable, metallic, note = sh.info(did)
    root = Node("shader %s  %s" % (_h(did), name))
    root.add("alpha-tested: %s (alpha is a %s)" % (
        alpha, "CUTOUT" if alpha else "tint/dye mask"))
    root.add("dyeable: %s   metallic: %s" % (dyeable, metallic))
    if note:
        root.add(note)
    return root


DIG_BY_TYPE = {0x70: dig_item, 0x20: dig_appearance, 0x06: dig_mesh,
               0x30: dig_material, 0x31: dig_surface, 0x41: dig_texture,
               0x2B: dig_shader}


def dig_did(did, deep=False):
    f = DIG_BY_TYPE.get(did >> 24)
    if f is None:
        sys.exit("no dig for DID type 0x%02X (know: %s)" % (
            did >> 24, ", ".join("0x%02X" % t for t in sorted(DIG_BY_TYPE))))
    return f(did, deep=deep)


# ---- entry point ------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Dig into the LOTRO data set from a name or any DID and "
                    "print the resolved tree (repeats shown once, then "
                    "referenced as @N).")
    ap.add_argument("query", help="search term, or a DID (hex 0x-prefixed of "
                                  "type 0x70/0x20/0x06/0x30/0x31/0x41/0x2B, "
                                  "or a decimal item id)")
    ap.add_argument("--deep", action="store_true",
                    help="decode meshes too (submeshes, strides, surfaces, "
                         "shaders) - slower")
    ap.add_argument("--limit", type=int, default=100,
                    help="max items for a name search (default %(default)s)")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)

    q = args.query.strip()
    if q.lower().startswith("0x") or q.isdigit():
        # hex, or decimal DIDs (LotroCompanion itemIds are decimal item DIDs)
        tree = dig_did(int(q, 16 if q.lower().startswith("0x") else 10),
                       deep=args.deep)
        finalize_refs([tree])
        print("\n".join(tree.render()))
        return

    # name search over the catalog
    ql = q.lower()
    hits = [r for r in catalog(required=True)
            if ql in (r.get("name") or "").lower()]
    # Dedupe only TRUE duplicates: same name AND the identical full set of
    # per-body (appearance, key) bindings. A shared name alone (reissues,
    # different-slot pieces with one name) is not enough to hide an item.
    seen = {}          # (name, all bindings) -> the row shown
    rows = []
    dupes = []
    for r in hits:
        sig = (r.get("name"),
               tuple(sorted((b["app"], b["key"]) for b in r["bodies"])))
        if sig in seen:
            dupes.append((r, seen[sig]))
            continue
        seen[sig] = r
        rows.append(r)
    if not rows:
        sys.exit("nothing in the catalog matches %r" % q)
    print("%d distinct item%s match %r%s\n" % (
        len(rows), "s" if len(rows) != 1 else "", q,
        " (showing %d)" % args.limit if len(rows) > args.limit else ""))
    trees = [dig_item(r["did"], deep=args.deep) for r in rows[:args.limit]]
    finalize_refs(trees)       # refs cross trees; resolve once over all
    for t in trees:
        print("\n".join(t.render()))
        print()
    # account for everything that matched but was not expanded, and why
    if dupes or len(rows) > args.limit:
        print("skipped:")
        for r in rows[args.limit:]:
            print("  0x%08X  %-44s over --limit %d"
                  % (r["did"], r.get("name") or "(unnamed)", args.limit))
        for r, kept in dupes:
            cc, kc = r.get("clothing_color"), kept.get("clothing_color")
            dyediff = ""
            if (cc or 0) != (kc or 0):
                dyediff = "; BUT default dye differs: %s vs %s" % (
                    dye_name(cc) or "none", dye_name(kc) or "none")
            print("  0x%08X  %-44s true duplicate of 0x%08X (same name + "
                  "identical bindings)%s%s"
                  % (r["did"], r.get("name") or "(unnamed)", kept["did"],
                     "" if rows.index(kept) < args.limit else " (itself skipped)",
                     dyediff))
        if len(rows) > args.limit:
            print("  (raise --limit or narrow the search term)")


if __name__ == "__main__":
    main()
