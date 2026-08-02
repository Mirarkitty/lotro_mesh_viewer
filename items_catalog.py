"""items_catalog.py — sweep all item property records into a searchable catalog.

Covers WEARABLES (items with an Item_WornAppearanceMapList; per-body rows with
garment-presence flags) and HELD items (weapons/class items; held=True rows
whose geometry resolves via weapon_resolve.py instead).

Walks client_gamelogic.dat for 0x79 records (item properties live at
itemDID + 0x09000000), parses each with propset, and writes one JSON line per
item that has an Item_WornAppearanceMapList (i.e. wearables) to
items_catalog.jsonl:
  {did, name, item_class, quality, level, slot, icon, clothing_color,
   bodies: [{species, sex, key, app}]}

Names: STRING_INFO {token, dataId} resolved from client_local_English.dat
0x25 text records: [DID u32][u32 count][per entry: u16 flag?, u32 token,
u32 zero?, u32 ?, vle charcount, utf16 chars] — parsed leniently by scanning
for the token and reading the UTF-16 run after it.

Run: python3 items_catalog.py            (full sweep, ~minutes, detached use)
     python3 items_catalog.py --item 0x7000DA5B (single-item test)
"""
import sys, json, struct, os
import config
import propset

# Archive handles (lazy; config caches one handle per archive).
def _gl():  return config.gamelogic()
def _loc(): return config.local("English")

def resolve_name(si):
    """si = {'token','dataId'} from STRING_INFO; find token in the 0x25 record
    and return the UTF-16 string that follows."""
    if not isinstance(si, dict): return si
    try:
        c = _loc().read_content(si["dataId"])
    except Exception:
        return None
    pat = struct.pack("<I", si["token"])
    i = c.find(pat)
    if i < 0: return None
    p = i + 4
    # skip zero/meta u32s until a plausible VLE char count then UTF-16 text
    for skip in range(0, 24, 1):
        q = p + skip
        if q + 2 > len(c): break
        n = c[q]
        if 0 < n < 0x80 and q + 1 + 2 * n <= len(c):
            try:
                t = c[q + 1:q + 1 + 2 * n].decode("utf-16-le")
                if t and all(31 < ord(ch) < 0x3000 or ch in " -'’" for ch in t):
                    return t
            except Exception:
                pass
    return None

# Inventory_DefaultSlot bits for HELD slots (wearable slots live in the low
# byte, see SLOT_BITS in api_common). Observed: main-hand axe 0x10000,
# ranged bow 0x40000; the rest of the range is labeled generically.
HELD_SLOT_BITS = {0x10000: "MainHand", 0x20000: "OffHand", 0x40000: "Ranged",
                  0x80000: "Held", 0x100000: "Held", 0x200000: "Held",
                  0x400000: "Held", 0x800000: "Held"}

def item_row(props_did, props):
    ml = props.get("Item_WornAppearanceMapList")
    if not ml:
        # Held item (weapon/class item)? No worn-appearance map, but a
        # PhysObj and a held-range default slot. Geometry resolves through
        # the separate PhysObj chain (weapon_resolve.py, docs/weapons.md),
        # so these rows carry held=True and no bodies.
        slot = props.get("Inventory_DefaultSlot") or 0
        held_bits = slot & 0xFFF0000
        if props.get("PhysObj") and held_bits:
            hs = next((n for b, n in HELD_SLOT_BITS.items() if held_bits & b),
                      "Held")
            return dict(
                did=props_did - propset.DBPROPERTIES_OFFSET,
                name=resolve_name(props.get("Name")),
                held=True, held_slot=hs,
                item_class=props.get("Item_Class"),
                quality=props.get("Item_Quality"),
                level=props.get("Item_Level"),
                slot=slot,
                equip_cat=props.get("Item_EquipmentCategory"),
                icon=props.get("Icon_Layer_ImageDID"),
                physobj=props.get("PhysObj"),
                bodies=[])
        return None
    bodies = []
    for _n, e in ml:
        bodies.append(dict(species=e.get("Item_SpeciesOfWearer"),
                           sex=e.get("Item_SexOfWearer"),
                           key=e.get("Item_AppearanceKey"),
                           app=e.get("Item_WornAppearance")))
    return dict(
        did=props_did - propset.DBPROPERTIES_OFFSET,
        name=resolve_name(props.get("Name")),
        item_class=props.get("Item_Class"),
        quality=props.get("Item_Quality"),
        level=props.get("Item_Level"),
        slot=props.get("Inventory_DefaultSlot"),
        equip_cat=props.get("Item_EquipmentCategory"),
        material_type=props.get("Item_MaterialType"),
        icon=props.get("Icon_Layer_ImageDID"),
        clothing_color=props.get("Item_ClothingColor"),
        bodies=bodies)

GARMENT_TAG = 0x1000000C
_STUB_BYTES = 2000

def _record_presence(app_did):
    """{appearanceKey: renderable} for one 0x20 worn-appearance record.
    renderable = the entry's 0x1000000C garment part (or, when no part carries
    that tag, any part) resolves to a shipped mesh > 2 KB. The viewers filter
    search results on these flags, so the sweep bakes them in per body."""
    import wearable2
    try:
        rec = wearable2.parse_record(config.general().read_content(app_did))
    except Exception:
        return {}
    out = {}
    for e in wearable2.entries(rec):
        parts = e["blocks"][0]["parts"] if e["blocks"] else []
        gar = [p for p in parts if p["tag"] == GARMENT_TAG]
        ok = False
        for p in (gar if gar else parts):
            f = config.mesh_chain().find_file(p["mesh"])
            if f and f[2] > _STUB_BYTES:
                ok = True
                break
        out[e["key"]] = ok
    return out


def augment_presence(rows):
    """Add bodies[i]["present"] to every catalog row (0x20 records parsed
    once each, cached). Returns the number of items with >= 1 renderable
    body."""
    cache = {}
    n_render = 0
    for i, r in enumerate(rows):
        any_ok = False
        for b in r["bodies"]:
            app = b["app"]
            if app not in cache:
                cache[app] = _record_presence(app)
            b["present"] = bool(cache[app].get(b["key"], False))
            any_ok = any_ok or b["present"]
        if any_ok:
            n_render += 1
        if i % 5000 == 0:
            print("  presence %d/%d (records cached: %d)"
                  % (i, len(rows), len(cache)), flush=True)
    return n_render


def sweep(out_path=None):
    """Walk every 0x79 property record and write one JSON line per wearable
    item to `out_path` (default: items_catalog.jsonl in the output root, where
    app.py's /search route expects it), then add the per-body garment-presence
    flags the search/set routes filter on."""
    if out_path is None:
        out_path = os.path.join(config.out_dir(), "items_catalog.jsonl")
    gl = _gl()
    reg = propset.registry(gl)
    dids = []
    gl.walk(lambda e: dids.append(e[0]) if (e[0] >> 24) == 0x79 else None)
    print("0x79 property records:", len(dids))
    n_ok = n_fail = 0
    rows = []
    for i, did in enumerate(dids):
        try:
            d, props = propset.parse_properties(gl.read_content(did), reg)
            n_ok += 1
            row = item_row(did, props)
            if row:
                rows.append(row)
        except Exception:
            n_fail += 1
        if i % 20000 == 0:
            print("  %d/%d  parsed=%d items=%d fail=%d"
                  % (i, len(dids), n_ok, len(rows), n_fail), flush=True)
    n_render = augment_presence(rows)
    with open(out_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print("DONE parsed=%d items=%d fail=%d renderable-wearables=%d -> %s"
          % (n_ok, len(rows), n_fail, n_render, out_path))

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Build the searchable wearable-item catalog "
                    "(items_catalog.jsonl) from client_gamelogic.dat, or test "
                    "the parse on a single item.")
    ap.add_argument("--item", metavar="DID",
                    help="parse just this item DID (hex) and print its row")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="catalog output path (default: items_catalog.jsonl "
                         "in the output root)")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)
    if args.item:
        did = int(args.item, 16)
        d, props = propset.load_item(did)
        print(json.dumps(item_row(d, props), indent=1))
    else:
        sweep(args.output)
