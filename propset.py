"""Faithful Python port of LOTRO's Turbine PropertiesSet binary deserializer.

Ported byte-for-byte from dmorcellet's closed `delta-lotro-dat-utils-11.11.jar`
(decompiled with CFR): DBPropertiesLoader, PropertyUtils, PropertyDefinitionsLoader,
BufferUtils, GeoLoader, LoaderUtils. This REPLACES the old byte-scanning heuristics
with a real parse: property values are read by the type declared in the client's
property dictionary (master-property DID 0x34000000), so nested arrays / structs
(e.g. Item_WornAppearanceMapList) decode exactly.

Format summary (little-endian; a resource record's content starts with its self-DID):
  resource   = uint32 DID ; properties
  properties = TSize count ; count * property(doublePid=True)
  property   = uint32 pid ; (if doublePid) uint32 pid2==pid ; value(pid)
  value(pid) = read by dictionary[pid].type:
      STRING(1)      pascalString (VLE len + ISO-8859-1)
      STRING_TOKEN(2) u32 ; WAVE_FORM(3) see reader ; TIMESTAMP(4) f64
      TRI_STATE(5) u8 ; VECTOR(6) 3*f32 ; INSTANCE_ID(7) u64 ; ENUM_MAPPER(8) u32
      FLOAT(9) f32 ; PROPERTY_ID(10) u32 ; STRING_INFO(13) see reader
      BITFIELD_64(14) u64 ; INT(15) u32 ; COLOR(16) 4*u8 ; POSITION(17) see reader
      BIT_FIELD32(18) u32 ; LONG64(19) u64 ; DATA_FILE(20) u32(=a DID) ;
      BOOLEAN(21) u8 ; BIT_FIELD(22) VLE bitcount + bytes
      STRUCT(11): u8 skip + u8 nbFields ; nbFields * property(doublePid=True)
      ARRAY(12):  u32 nbItems           ; nbItems  * property(doublePid=False)
VLE: b=u8; if b==0xE0 -> u32; if b<0x80 -> b; else b2=u8; if (b&0x40)==0 ->
     b2|(b&0x7F)<<8; else c=u16le; (b&0x3F)<<24 | b2<<16 | c.  TSize = skip 1 + VLE.
"""
import struct
import config

# PropertyType.code (non-DDO) -> name, from PropertyType.class
STRING=1; STRING_TOKEN=2; WAVE_FORM=3; TIMESTAMP=4; TRI_STATE=5; VECTOR=6
INSTANCE_ID=7; ENUM_MAPPER=8; FLOAT=9; PROPERTY_ID=10; STRUCT=11; ARRAY=12
STRING_INFO=13; BITFIELD_64=14; INT=15; COLOR=16; POSITION=17; BIT_FIELD32=18
LONG64=19; DATA_FILE=20; BOOLEAN=21; BIT_FIELD=22


class Reader:
    def __init__(self, data, pos=0):
        self.d = data; self.p = pos
    def u8(self):
        v = self.d[self.p]; self.p += 1; return v
    def u16(self):
        v = struct.unpack_from("<H", self.d, self.p)[0]; self.p += 2; return v
    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v
    def u64(self):
        v = struct.unpack_from("<Q", self.d, self.p)[0]; self.p += 8; return v
    def f32(self):
        v = struct.unpack_from("<f", self.d, self.p)[0]; self.p += 4; return v
    def f64(self):
        v = struct.unpack_from("<d", self.d, self.p)[0]; self.p += 8; return v
    def skip(self, n):
        self.p += n
    def vle(self):
        a = self.u8()
        if a == 0xE0: return self.u32()
        if (a & 0x80) == 0: return a
        b = self.u8()
        if (a & 0x40) == 0: return b | ((a & 0x7F) << 8)
        c = self.u16()
        return ((a & 0x3F) << 24) | (b << 16) | c
    def tsize(self):
        self.u8(); return self.vle()
    def pascal(self):
        n = self.vle(); s = self.d[self.p:self.p + n]; self.p += n
        return s.decode("iso-8859-1")
    def utf16(self):
        n = self.vle(); b = self.d[self.p:self.p + 2 * n]; self.p += 2 * n
        return b.decode("utf-16-le")


# ---- value readers (PropertyUtils.readPropertyValue, non-network) ----
def _read_string_info(r):
    out = None
    is_literal = r.u8()
    if is_literal: out = r.utf16()
    else:
        token = r.u32(); dataId = r.u32()
        out = {"token": token, "dataId": dataId}
    has_strings = r.u8()
    if has_strings:
        r.pascal(); r.pascal(); r.pascal()
        nrep = r.vle()
        for _ in range(nrep):
            dt = r.u8(); r.u32()      # dataType, replacementToken
            if dt != 1: r.u8()
            if dt == 4: r.vle()
            elif dt == 1: _read_string_info(r)
            elif dt == 2: r.f32()
    else:
        r.u8(); r.u8()               # remainder 1,0
    return out

def _read_wave_form(r):
    t = r.u32()
    if t == 10:
        for _ in range(10): r.f32()
        r.f32(); r.u8()
        pairs = r.u32()
        for _ in range(pairs * 2): r.f32()
    elif t == 1:
        r.f32()
    elif t > 1:
        for _ in range(10): r.f32()
    return None

def _read_position(r):
    flags = r.u8()
    if flags == 0: return None
    if flags & 1: r.u8()
    if flags & 2: r.u8(); r.u8()
    if flags & 4: r.u16()
    if flags & 8: r.u16()
    if flags & 0x10: r.f32(); r.f32(); r.f32()
    if flags & 0x20: r.f32(); r.f32(); r.f32(); r.f32()
    return None

def _read_bitfield(r):
    bitcount = r.vle()
    nbytes = bitcount // 8 + (1 if bitcount % 8 else 0)
    r.skip(nbytes); return None

# type code -> reader for the "scalar" part (STRUCT/ARRAY return their count)
def read_value(r, t):
    if t == STRING: return r.pascal()
    if t == STRING_TOKEN: return r.u32()
    if t == WAVE_FORM: return _read_wave_form(r)
    if t == TIMESTAMP: return r.f64()
    if t == TRI_STATE: return r.u8()
    if t == VECTOR: return (r.f32(), r.f32(), r.f32())
    if t == INSTANCE_ID: return r.u64()
    if t == ENUM_MAPPER: return r.u32()
    if t == FLOAT: return r.f32()
    if t == PROPERTY_ID: return r.u32()
    if t == STRUCT:
        r.u8(); return r.u8()          # skip 1, then nbFields (readStructProperty)
    if t == ARRAY: return r.u32()      # nbItems
    if t == STRING_INFO: return _read_string_info(r)
    if t == BITFIELD_64: return r.u64()
    if t == INT: return r.u32()
    if t == COLOR: return (r.u8(), r.u8(), r.u8(), r.u8())
    if t == POSITION: return _read_position(r)
    if t == BIT_FIELD32: return r.u32()
    if t == LONG64: return r.u64()
    if t == DATA_FILE: return r.u32()  # a DID
    if t == BOOLEAN: return r.u8()
    if t == BIT_FIELD: return _read_bitfield(r)
    raise ValueError("unmanaged property type %d" % t)


# ---- property dictionary (master property, DID 0x34000000) ----
_registry = None   # pid -> (name, typecode)

def _load_registry(gl):
    r = Reader(gl.read_content(0x34000000))
    did = r.u32(); r.skip(8)
    if did != 0x34000000:
        raise ValueError("master property DID mismatch: 0x%08X" % did)
    reg = {}
    nstr = r.tsize()
    names = {}
    for _ in range(nstr):
        pid = r.u32(); names[pid] = r.pascal()
    r.skip(2)
    ndef = r.tsize()
    for _ in range(ndef):
        pid = r.u32()
        pid2 = r.u32()
        if pid2 != pid: raise ValueError("def PID mismatch")
        tcode = r.u32()
        if not (1 <= tcode <= 22): raise ValueError("bad type code %d" % tcode)
        r.u32(); r.u32()                 # group, provider
        dataId = r.u32()
        r.u32()                          # ePatchFlags
        v5 = r.u32(); flags = (v5 >> 8) & 0xFF
        if flags & 8: read_value(r, tcode)
        if flags & 0x10: read_value(r, tcode)
        if flags & 0x20: read_value(r, tcode)
        r.f32()                          # predictionTimeout
        r.u8(); r.u8(); r.u8(); r.u8()   # inheritance/datFileType/propagation/caching
        r.skip(1)
        nch = r.vle()
        for _ in range(nch):
            c1 = r.u32(); c2 = r.u32()
            if c1 != c2: raise ValueError("child PID mismatch")
        nreq = r.u32()
        for _ in range(nreq): r.u32()
        z = r.u32()
        if z != 0: raise ValueError("numLast2 not zero: %d" % z)
        reg[pid] = (names.get(pid, str(pid)), tcode)
    return reg, names

def registry(gl=None):
    """The property dictionary {pid: (name, typecode)}, loaded once from the
    master-property record (DID 0x34000000) in client_gamelogic.dat."""
    global _registry
    if _registry is None:
        gl = gl or config.gamelogic()
        _registry, _ = _load_registry(gl)
    return _registry


# ---- the recursive property decoder (DBPropertiesLoader.decodeProperty) ----
def _decode_property(r, reg, double_pid):
    pid = r.u32()
    if pid == 0: return None
    if double_pid:
        p2 = r.u32()
        if p2 != pid: raise ValueError("property ID mismatch 0x%X != 0x%X" % (pid, p2))
    ent = reg.get(pid)
    if ent is None: raise KeyError("unknown property id %d" % pid)
    name, t = ent
    val = read_value(r, t)
    if t == ARRAY:
        n = val; items = []
        for _ in range(n):
            items.append(_decode_property(r, reg, False))
        val = items
    elif t == STRUCT:
        n = val; d = {}
        for _ in range(n):
            k, v = _decode_property(r, reg, True)
            d[k] = v
        val = d
    return name, val

def parse_properties(content, reg):
    """content = a resource record's bytes (starts with self-DID). Returns
    (did, {propertyName: value}). Values: scalars as python numbers/strings,
    STRUCT -> dict{childName: value}, ARRAY -> list of (childName, value)."""
    r = Reader(content)
    did = r.u32()
    n = r.tsize()
    props = {}
    for _ in range(n):
        kv = _decode_property(r, reg, True)
        if kv: props[kv[0]] = kv[1]
    return did, props


# The item's PropertiesSet is stored at itemDID + DBPROPERTIES_OFFSET (the paired
# 0x79 record), NOT at the 0x70 item DID itself. (DATConstants.DBPROPERTIES_OFFSET,
# used by lotro-tools CosmeticLoader: loadProperties(itemId + 0x09000000).)
DBPROPERTIES_OFFSET = 0x09000000

def load_item(item_did, gl=None):
    """Parse an item's PropertiesSet -> (did, {propertyName: value})."""
    gl = gl or config.gamelogic()
    reg = registry(gl)
    return parse_properties(gl.read_content(item_did + DBPROPERTIES_OFFSET), reg)


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(
        description="Parse a LOTRO item's PropertiesSet (the typed property "
                    "record at itemDID + 0x09000000) and print its properties.")
    ap.add_argument("item", nargs="?", default="0x70021A13",
                    help="item DID (hex, default: the Sleeveless Summer Dress)")
    ap.add_argument("--all", action="store_true",
                    help="print every property (default: only the appearance-"
                         "related ones)")
    ap.add_argument("--json", action="store_true",
                    help="dump the full property dict as JSON to stdout")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)

    gl = config.gamelogic()
    reg = registry(gl)
    did, props = load_item(int(args.item, 16), gl)
    if args.json:
        print(json.dumps({"did": "0x%08X" % did, "properties": props},
                         indent=1, default=str))
    else:
        print("dictionary entries:", len(reg))
        print("item 0x%08X  (%d properties)" % (did, len(props)))
        for k in sorted(props):
            v = props[k]
            if args.all or "Appearance" in k or "Worn" in k \
               or "PhysObj" in k or "Wearer" in k:
                print("  ", k, "=", json.dumps(v, default=str)[:400])
