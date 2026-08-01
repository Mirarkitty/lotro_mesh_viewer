"""items_catalog.py — sweep all item property records into a searchable catalog.

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

def item_row(props_did, props):
    ml = props.get("Item_WornAppearanceMapList")
    if not ml: return None
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

def sweep(out_path=None):
    """Walk every 0x79 property record and write one JSON line per wearable
    item to `out_path` (default: items_catalog.jsonl in the output root, where
    app.py's /search route expects it)."""
    if out_path is None:
        out_path = os.path.join(config.out_dir(), "items_catalog.jsonl")
    gl = _gl()
    reg = propset.registry(gl)
    dids = []
    gl.walk(lambda e: dids.append(e[0]) if (e[0] >> 24) == 0x79 else None)
    print("0x79 property records:", len(dids))
    n_ok = n_wear = n_fail = 0
    with open(out_path, "w") as f:
        for i, did in enumerate(dids):
            try:
                d, props = propset.parse_properties(gl.read_content(did), reg)
                n_ok += 1
                row = item_row(did, props)
                if row:
                    f.write(json.dumps(row) + "\n")
                    n_wear += 1
            except Exception:
                n_fail += 1
            if i % 20000 == 0:
                print("  %d/%d  parsed=%d wearable=%d fail=%d" % (i, len(dids), n_ok, n_wear, n_fail), flush=True)
    print("DONE parsed=%d wearable=%d fail=%d -> %s" % (n_ok, n_wear, n_fail, out_path))

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
