#!/usr/bin/env python3
"""explore.py — dig into the LOTRO data set from any starting point.

Takes a search term or a DID and prints everything the toolkit can resolve
from there as a tree: items -> per-body wardrobe bindings -> entry parts
(garment/hands/stubs), materials -> diffuse textures, surfaces -> shaders,
mesh submeshes, and the REVERSE direction too (which wardrobe entries and
items use a given mesh or material).

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
import struct
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


# ---- shared resolution helpers ----------------------------------------------

def mesh_entry(did):
    """(size, present) for a mesh DID from the mesh archives' directory."""
    e = config.mesh_chain().find_file(did)
    return (e[2], True) if e else (None, False)


def surface_info(node, surf_did):
    """Append shader + material chain of a 0x31 surface to `node`."""
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
        material_info(node.add("material %s" % _h(m)), m)
    if mats is None:
        d = tx.diffuse_for_surface(surf_did)
        if d:
            texture_line(node, d, "diffuse (legacy graph scan)")


def material_info(node, mat_did):
    """Append a 0x30 material's diffuse resolution to `node`."""
    import tex_extract as tx
    d = tx.material_diffuse(mat_did)
    if d is not None:
        texture_line(node, d, "diffuse")
    else:
        node.add("diffuse: unresolved")


def texture_line(node, tex_did, label="texture"):
    import tex_extract as tx
    info = tx.texture_info(tex_did)
    if info:
        w, h, fourcc, placeholder = info
        node.add("%s %s  %dx%d %s%s" % (label, _h(tex_did), w, h,
                                        fourcc.decode(),
                                        "  (placeholder)" if placeholder else ""))
    else:
        node.add("%s %s  (not a decodable texture)" % (label, _h(tex_did)))


def mesh_summary(node, mesh_did, deep=False):
    """One line (or a deep subtree) for a mesh DID."""
    size, present = mesh_entry(mesh_did)
    if not present:
        node.add("NOT SHIPPED (indirection DID - data hole, see docs/limitations.md)")
        return
    if size is not None and size < STUB_BYTES:
        node.add("stub (%d B placeholder)" % size)
        return
    if not deep:
        node.add("%d B on disk (use --deep for submeshes)" % size)
        return
    import mesh_decode as md
    try:
        raw = md._read(mesh_did)
        blocks = md._find_vertex_blocks(raw)
        m = md.decode_mesh(mesh_did, with_textures=False)
        s = md.stats(m)
    except Exception as ex:
        node.add("decode FAILED: %s" % str(ex)[:80])
        return
    node.add("%dv %dt  %d submesh(es)  sliver_tris=%d" % (
        s["num_vertices"], s["num_triangles"], m["num_submeshes"],
        s["sliver_tris"]))
    import tex_extract as tx
    surfs = tx._mesh_surfaces(raw)
    for i, (g, (_vs, cnt, stride)) in enumerate(zip(m["groups"], blocks)):
        sub = node.add("submesh %d  %d verts  stride %d B" % (i, cnt, stride))
        if i < len(surfs):
            surface_info(sub.add("surface %s" % _h(surfs[i])), surfs[i])


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
    # map (app, key) back to items
    by_binding = {}
    for r in catalog():
        for b in r["bodies"]:
            by_binding.setdefault((b["app"], b["key"]), []).append(r)
    for (a, k), reason in sorted(hits.items()):
        items = by_binding.get((a, k), [])
        n = root.add("app %s  key %s  %s" % (_h(a), _h(k), reason))
        seen = set()
        shown = 0
        for r in items:
            nm = r.get("name") or "(unnamed)"
            if nm in seen:
                continue
            seen.add(nm)
            if shown >= 4:
                continue
            shown += 1
            n.add("item 0x%08X  %s  [%s]" % (r["did"], nm,
                                             body_label(*[next((b[x] for b in r["bodies"]
                                                                if b["app"] == a and b["key"] == k), None)
                                                          for x in ("species", "sex")])))
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
    if res["appearance_key"] is not None:
        root.add("appearance key %s (constant across bodies)" % _h(res["appearance_key"]))
    if res["phys_obj"]:
        root.add("PhysObj %s (base-body fallback)" % _h(res["phys_obj"]))
    for b in res["bodies"]:
        n = root.add("body %-10s app %s  key %s" % (
            body_label(b["species"], b["sex"]), _h(b["worn_appearance"]), _h(b["key"])))
        if b["material"]:
            m = n.add("material %s" % _h(b["material"]))
            if b["diffuse"]:
                texture_line(m, b["diffuse"], "diffuse")
        for p in (b["parts"] or []):
            tag = TAG_NAMES.get(p["tag"], _h(p["tag"]) if p["tag"] else "untagged")
            pn = n.add("part %-9s mesh %s  %s" % (
                tag, _h(p["mesh"]),
                ("%d B" % p["size"]) if p["size"] else
                ("PRESENT" if p["present"] else "NOT SHIPPED")))
            if deep and p["present"] and (p["size"] or 0) >= STUB_BYTES:
                mesh_summary(pn, p["mesh"], deep=True)
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
            _h(e["key"]), len(e["blocks"]), "s" if len(e["blocks"]) != 1 else ""))
        items = by_binding.get(e["key"], [])
        seen = set()
        for r in items[:4]:
            nm = r.get("name")
            if nm and nm not in seen:
                seen.add(nm)
                n.add("item 0x%08X  %s" % (r["did"], nm))
        if len(items) > 4:
            n.add("... %d more items" % (len(items) - 4))
        b0 = e["blocks"][0]
        for g in b0["groups"]:
            if g["material"]:
                material_info(n.add("material %s (block0, q=%.2f)"
                                    % (_h(g["material"]), b0["q"])), g["material"])
        for p in b0["parts"]:
            tag = TAG_NAMES.get(p["tag"], _h(p["tag"]))
            size, present = mesh_entry(p["mesh"])
            pn = n.add("part %-9s mesh %s  %s" % (
                tag, _h(p["mesh"]),
                ("stub %d B" % size) if (size or 0) < STUB_BYTES and present
                else ("%d B" % size) if present else "NOT SHIPPED"))
            if deep and present and (size or 0) >= STUB_BYTES:
                mesh_summary(pn, p["mesh"], deep=True)
    return root


def dig_mesh(did, deep=False):
    root = Node("mesh %s" % _h(did))
    mesh_summary(root, did, deep=True)      # a direct mesh query is always deep
    if catalog():
        root.children.append(find_users(
            lambda b: next(("as %s part" % TAG_NAMES.get(p["tag"], _h(p["tag"]))
                            for p in b["parts"] if p["mesh"] == did), None),
            "wardrobe part"))
    return root


def dig_material(did, deep=False):
    root = Node("material %s" % _h(did))
    material_info(root, did)
    if catalog():
        root.children.append(find_users(
            lambda b: next(("bound in material group"
                            for g in b["groups"] if g["material"] == did), None),
            "material binding"))
    return root


def dig_surface(did, deep=False):
    root = Node("surface %s" % _h(did))
    surface_info(root, did)
    return root


def dig_texture(did, deep=False):
    root = Node("texture %s" % _h(did))
    texture_line(root, did)
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
                    "print the resolved tree.")
    ap.add_argument("query", help="search term, or a DID (hex 0x-prefixed of "
                                  "type 0x70/0x20/0x06/0x30/0x31/0x41/0x2B, "
                                  "or a decimal item id)")
    ap.add_argument("--deep", action="store_true",
                    help="decode meshes too (submeshes, strides, surfaces, "
                         "shaders) - slower")
    ap.add_argument("--limit", type=int, default=10,
                    help="max items for a name search (default %(default)s)")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)

    q = args.query.strip()
    if q.lower().startswith("0x"):
        tree = dig_did(int(q, 16), deep=args.deep)
        print("\n".join(tree.render()))
        return
    if q.isdigit():
        # decimal DIDs (LotroCompanion itemIds are plain-decimal item DIDs)
        tree = dig_did(int(q), deep=args.deep)
        print("\n".join(tree.render()))
        return

    # name search over the catalog
    ql = q.lower()
    hits = [r for r in catalog(required=True)
            if ql in (r.get("name") or "").lower()]
    # dedupe by (name, first binding) like the viewers do
    seen = set()
    rows = []
    for r in hits:
        sig = (r.get("name"), r["bodies"][0]["key"] if r["bodies"] else 0)
        if sig in seen:
            continue
        seen.add(sig)
        rows.append(r)
    if not rows:
        sys.exit("nothing in the catalog matches %r" % q)
    print("%d distinct item%s match %r%s\n" % (
        len(rows), "s" if len(rows) != 1 else "", q,
        " (showing %d)" % args.limit if len(rows) > args.limit else ""))
    for r in rows[:args.limit]:
        print("\n".join(dig_item(r["did"], deep=args.deep).render()))
        print()


if __name__ == "__main__":
    main()
