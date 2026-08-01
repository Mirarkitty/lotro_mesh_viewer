"""Decoder for LOTRO's Havok animation clips (0x05 records in client_anim.dat).

LOTRO does NOT ship Havok binary *packfiles* (magic 0x57E0E057) — the 0x05
records are Havok binary *tagfiles*: magic dwords 0xCAB00D1E 0xD011FACE,
preceded by a 13-byte LOTRO prefix:

    off 0  float32   duration (matches the hkaAnimation duration field)
    off 4  uint32    0x2C (constant, all clips surveyed)
    off 8  uint8     0x00
    off 9  uint32    remaining payload size (tagfile size + 1)
    off 13 tagfile   magic0=0xCAB00D1E magic1=0xD011FACE, then tag stream

The binary tagfile is self-describing: TAG_METADATA entries carry full class
reflection (name, parent, members with types), and objects are serialized as a
member-presence bitmap followed by only the present members. Struct arrays are
stored SoA (one bitmap, then each present member as a column). Wire-format
reference: Havok SDK hkBinaryTagfileCommon.h (tag ids) and the open-source
parser github.com/exyorha/hkxparse (HKXTagfileParser.cpp).

The hkaSplineCompressedAnimation payload ('data' byte array) holds per-block
compressed transform tracks: per track a 4-byte TransformMask, then NURBS
spline (degree<=3, uint8 knots) or static values per pos/rot/scale channel,
with quantized control points (8/16-bit scalars, 32/40/48-bit quaternions).
Decompression algorithm ported from PredatorCZ/HavokLib
(hka_spline_decompressor.cpp, GPLv3) — the NURBS evaluation follows
"The NURBS Book" alg. A2.1 / Basis_ITS1.

Public API:
    parse_tagfile(raw)  -> (root_object, all_objects)   # generic tagfile parse
    parse_packfile(raw) -> dict of hkaSplineCompressedAnimation fields
    decode_clip(did)    -> {duration, numFrames, numberOfTransformTracks,
                            tracks: [per-track list of per-frame
                                     {'t':(x,y,z), 'q':(x,y,z,w), 's':(x,y,z)}]}
"""

import math
import struct

TAGFILE_MAGIC = b"\x1e\x0d\xb0\xca\xce\xfa\x11\xd0"  # 0xCAB00D1E 0xD011FACE LE

# ---------------------------------------------------------------------------
# Binary tagfile parser
# ---------------------------------------------------------------------------

TAG_FILE_INFO = 1
TAG_METADATA = 2
TAG_OBJECT = 3
TAG_OBJECT_REMEMBER = 4
TAG_OBJECT_BACKREF = 5
TAG_OBJECT_NULL = 6
TAG_FILE_END = 7

T_VOID, T_BYTE, T_INT, T_REAL, T_VEC4, T_VEC8, T_VEC12, T_VEC16, \
    T_OBJECT, T_STRUCT, T_CSTRING = range(11)
T_MASK = 15
F_ARRAY = 16
F_TUPLE = 32


class _Stream:
    def __init__(self, buf, pos=0):
        self.buf = buf
        self.pos = pos

    def byte(self):
        b = self.buf[self.pos]
        self.pos += 1
        return b

    def bytes(self, n):
        b = self.buf[self.pos:self.pos + n]
        if len(b) != n:
            raise EOFError("tagfile truncated")
        self.pos += n
        return b

    def varint(self):
        # bit0 of first byte = sign, bits 1..6 = low 6 bits, bit7 = continue;
        # following bytes: 7 bits each, bit7 = continue.
        b = self.byte()
        neg = b & 1
        val = (b & 0x7E) >> 1
        shift = 6
        while b & 0x80:
            b = self.byte()
            val |= (b & 0x7F) << shift
            shift += 7
        return -val if neg else val

    def real(self):
        v = struct.unpack_from("<f", self.buf, self.pos)[0]
        self.pos += 4
        return v


class _Member:
    __slots__ = ("name", "type", "tuple_size", "class_name")


class _Type:
    __slots__ = ("name", "version", "parent", "members")


class TagfileParser:
    """Parses a Havok binary tagfile into dicts.

    Objects come out as dicts with a '__class__' key (list of class names,
    most-derived first). Object references are resolved to the same dict
    instances (cycles possible); null refs are None.
    """

    def __init__(self, raw):
        off = raw.find(TAGFILE_MAGIC)
        if off < 0:
            raise ValueError("no Havok tagfile magic found")
        self.prefix = raw[:off]
        self.s = _Stream(raw, off + 8)
        self.strings = ["", ""]          # pool; backrefs are -index
        self.types = [None]              # index 0 = void
        self.type_by_name = {}
        self.objects = {}                # remember-index -> dict
        self.next_index = 1
        self.version = None

    # -- primitives ---------------------------------------------------------
    def _string(self):
        n = self.s.varint()
        if n <= 0:
            return self.strings[-n]
        val = self.s.bytes(n).decode("latin1")
        self.strings.append(val)
        return val

    def _read_type(self):
        t = _Type()
        t.name = self._string()
        t.version = self.s.varint()
        t.parent = self.s.varint()
        t.members = []
        for _ in range(self.s.varint()):
            m = _Member()
            m.name = self._string()
            m.type = self.s.varint()
            m.tuple_size = self.s.varint() if m.type & F_TUPLE else 0
            m.class_name = self._string() \
                if (m.type & T_MASK) in (T_OBJECT, T_STRUCT) else ""
            t.members.append(m)
        return t

    def _all_members(self, tindex):
        out = []
        chain = []
        ti = tindex
        while ti:
            chain.append(self.types[ti])
            ti = self.types[ti].parent
        for t in reversed(chain):        # base-class members first
            out.extend(t.members)
        return out, [t.name for t in chain]

    # -- objects ------------------------------------------------------------
    def _get_object(self, index):
        if index not in self.objects:
            self.objects[index] = {}
        return self.objects[index]

    def _parse_struct(self, out, tindex):
        if tindex == 0:
            tindex = self.s.varint()
        members, class_names = self._all_members(tindex)
        out["__class__"] = class_names
        bitmap = self.s.bytes((len(members) + 7) // 8)
        for i, m in enumerate(members):
            if bitmap[i >> 3] & (1 << (i & 7)):
                out[m.name] = self._parse_field(m)

    def _parse_field(self, m):
        base = m.type & T_MASK
        if m.type == (F_TUPLE | T_BYTE):
            return self.s.bytes(m.tuple_size)
        if m.type == (F_ARRAY | T_BYTE):
            return self.s.bytes(self.s.varint())
        if m.type & (F_ARRAY | F_TUPLE):
            count = m.tuple_size if m.type & F_TUPLE else self.s.varint()
            return self._parse_array(m, count)
        return self._parse_value(base, m.class_name)

    def _parse_array(self, m, count):
        base = m.type & T_MASK
        if base == T_STRUCT:
            return self._parse_struct_array(m, count)
        prefix = -1
        if base == T_INT:
            prefix = self.s.varint()     # storage bit width hint; values are
        elif base == T_VEC4:             # still varints
            prefix = self.s.varint()     # floats actually stored per element
        return [self._parse_value(base, m.class_name, prefix)
                for _ in range(count)]

    def _parse_struct_array(self, m, count):
        # SoA: one member bitmap, then each present member as a column array.
        if m.class_name:
            tindex = self.type_by_name[m.class_name]
        else:
            tindex = self.s.varint()
        members, class_names = self._all_members(tindex)
        bitmap = self.s.bytes((len(members) + 7) // 8)
        items = [{"__class__": class_names} for _ in range(count)]
        for i, mem in enumerate(members):
            if bitmap[i >> 3] & (1 << (i & 7)):
                column = self._parse_array(mem, count) \
                    if (mem.type & (F_ARRAY | F_TUPLE)) or \
                       (mem.type & T_MASK) == T_STRUCT \
                    else None
                if column is None:
                    if mem.type == (F_ARRAY | T_BYTE) or \
                       mem.type == (F_TUPLE | T_BYTE):
                        column = [self._parse_field(mem) for _ in range(count)]
                    else:
                        prefix = -1
                        base = mem.type & T_MASK
                        if base == T_INT:
                            prefix = self.s.varint()
                        elif base == T_VEC4:
                            prefix = self.s.varint()
                        column = [self._parse_value(base, mem.class_name,
                                                    prefix)
                                  for _ in range(count)]
                for item, v in zip(items, column):
                    item[mem.name] = v
        return items

    def _parse_value(self, base, class_name, prefix=-1):
        s = self.s
        if base == T_BYTE:
            return s.byte()
        if base == T_INT:
            return s.varint()
        if base == T_REAL:
            return s.real()
        if base == T_VEC4:
            n = 4 if prefix < 0 else prefix
            v = [s.real() for _ in range(n)] + [0.0] * (4 - n)
            return tuple(v)
        if base == T_VEC8:
            return tuple(s.real() for _ in range(8))
        if base == T_VEC12:
            return tuple(s.real() for _ in range(12))
        if base == T_VEC16:
            return tuple(s.real() for _ in range(16))
        if base == T_OBJECT:
            idx = s.varint()
            return self._get_object(idx) if idx else None
        if base == T_STRUCT:
            out = {}
            self._parse_struct(out, self.type_by_name.get(class_name, 0))
            return out
        if base == T_CSTRING:
            return self._string()
        raise ValueError(f"unsupported field type {base}")

    # -- top level ----------------------------------------------------------
    def parse(self):
        while True:
            tag = self.s.varint()
            if tag == TAG_FILE_INFO:
                self.version = self.s.varint()
                if self.version == 4:
                    self.havok_version = self._string()
                elif self.version != 3:
                    raise ValueError(f"unsupported tagfile version "
                                     f"{self.version}")
            elif tag == TAG_METADATA:
                t = self._read_type()
                self.types.append(t)
                self.type_by_name[t.name] = len(self.types) - 1
            elif tag in (TAG_OBJECT, TAG_OBJECT_REMEMBER):
                if tag == TAG_OBJECT_REMEMBER:
                    obj = self._get_object(self.next_index)
                    self.next_index += 1
                else:
                    obj = {}
                self._parse_struct(obj, 0)
            elif tag == TAG_FILE_END:
                break
            else:
                raise ValueError(f"unsupported top-level tag {tag}")
        root = self.objects.get(1)
        return root, self.objects


def parse_tagfile(raw):
    """Parse a raw 0x05 record (LOTRO prefix + Havok binary tagfile).

    Returns (root_object, {remember_index: object_dict}).
    """
    return TagfileParser(raw).parse()


def find_objects(root, class_name):
    """All reachable objects whose class chain contains class_name."""
    seen = set()
    out = []
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if id(node) in seen:
                continue
            seen.add(id(node))
            if class_name in node.get("__class__", ()):
                out.append(node)
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return out


def parse_packfile(raw):
    """Parse a raw 0x05 record and return the animation object's fields.

    (Name kept for historical reasons — LOTRO clips turned out to be Havok
    binary *tagfiles*, not packfiles.) Returns the
    hkaSplineCompressedAnimation (or hkaInterleavedUncompressedAnimation)
    dict: duration, numberOfTransformTracks, numFrames, numBlocks,
    blockOffsets, data, quantization sizes, etc.
    """
    root, _ = parse_tagfile(raw)
    for cls in ("hkaSplineCompressedAnimation",
                "hkaInterleavedUncompressedAnimation",
                "hkaAnimation"):
        objs = find_objects(root, cls)
        if objs:
            return objs[0]
    raise ValueError("no animation object in tagfile")


# ---------------------------------------------------------------------------
# Spline-compressed track decompression
# (port of PredatorCZ/HavokLib hka_spline_decompressor.cpp, GPLv3)
# ---------------------------------------------------------------------------

_SQRT2_INV = 0.7071067811865476


def _read_quat32(data, o):
    cval = struct.unpack_from("<I", data, o)[0]
    r = ((cval >> 18) & 0x3FF) * (1.0 / 1023.0)
    r = 1.0 - r * r
    phi_theta = float(cval & 0x3FFFF)
    phi = math.floor(math.sqrt(phi_theta))
    theta = 0.0
    if phi > 0.0:
        theta = (math.pi / 4.0) * (phi_theta - phi * phi) / phi
        phi = (math.pi / 2.0 / 511.0) * phi
    mag = math.sqrt(max(0.0, 1.0 - r * r))
    x = math.sin(phi) * math.cos(theta) * mag
    y = math.sin(phi) * math.sin(theta) * mag
    z = math.cos(phi) * mag
    w = r
    if cval & 0x10000000:
        x = -x
    if cval & 0x20000000:
        y = -y
    if cval & 0x40000000:
        z = -z
    if cval & 0x80000000:
        w = -w
    return (x, y, z, w), o + 4


def _place_missing(a, b, c, missing, wsign):
    """Insert computed component sqrt(1-a²-b²-c²)·wsign at index `missing`."""
    w = math.sqrt(max(0.0, 1.0 - (a * a + b * b + c * c))) * wsign
    q = [a, b, c]
    q.insert(missing, w)
    return tuple(q)


def _read_quat40(data, o):
    val = int.from_bytes(data[o:o + 5], "little")
    frac = 0.000345436
    a = ((val & 0xFFF) - 2047) * frac
    b = (((val >> 12) & 0xFFF) - 2047) * frac
    c = (((val >> 24) & 0xFFF) - 2047) * frac
    missing = (val >> 36) & 3
    wsign = -1.0 if (val >> 38) & 1 else 1.0
    return _place_missing(a, b, c, missing, wsign), o + 5


def _read_quat48(data, o):
    x, y, z = struct.unpack_from("<3H", data, o)
    frac = 0.000043161
    missing = ((y >> 14) & 2) | ((x >> 15) & 1)
    wsign = -1.0 if z & 0x8000 else 1.0
    a = ((x & 0x7FFF) - 16383) * frac
    b = ((y & 0x7FFF) - 16383) * frac
    c = ((z & 0x7FFF) - 16383) * frac
    return _place_missing(a, b, c, missing, wsign), o + 6


def _read_quat(qtype, data, o):
    if qtype == 2:                                   # QT_32bit
        return _read_quat32(data, o)
    if qtype == 3:                                   # QT_40bit
        return _read_quat40(data, o)
    if qtype == 4:                                   # QT_48bit
        return _read_quat48(data, o)
    if qtype == 7:                                   # uncompressed
        return tuple(struct.unpack_from("<4f", data, o)), o + 16
    raise NotImplementedError(f"quaternion quantization type {qtype}")


def _find_knot_span(degree, value, n_points, knots):
    if value >= knots[n_points]:
        return n_points - 1
    low, high = degree, n_points
    mid = (low + high) // 2
    while value < knots[mid] or value >= knots[mid + 1]:
        if value < knots[mid]:
            high = mid
        else:
            low = mid
        mid = (low + high) // 2
    return mid


def _basis(span, degree, u, knots):
    """De Boor basis values N[0..degree] (NURBS Book alg. A2.1 variant)."""
    n = [1.0, 0.0, 0.0, 0.0, 0.0]
    for i in range(1, degree + 1):
        for j in range(i - 1, -1, -1):
            denom = knots[span + i - j] - knots[span - j]
            a = (u - knots[span - j]) / denom if denom else 0.0
            tmp = n[j] * a
            n[j + 1] += n[j] - tmp
            n[j] = tmp
    return n


def _eval_scalar(points, degree, knots, u):
    if len(points) == 1:
        return points[0]
    span = _find_knot_span(degree, u, len(points), knots)
    n = _basis(span, degree, u, knots)
    return sum(points[span - i] * n[i] for i in range(degree + 1))


def _eval_quat(points, degree, knots, u):
    span = _find_knot_span(degree, u, len(points), knots)
    n = _basis(span, degree, u, knots)
    q = [0.0, 0.0, 0.0, 0.0]
    for i in range(degree + 1):
        p = points[span - i]
        w = n[i]
        q[0] += p[0] * w
        q[1] += p[1] * w
        q[2] += p[2] * w
        q[3] += p[3] * w
    norm = math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2)
    if norm > 1e-8:
        q = [c / norm for c in q]
    return tuple(q)


def _align(o, alignment):
    r = o & (alignment - 1)
    return o + (alignment - r) if r else o


class _VecTrack:
    """One pos or scale track: per component either static value or spline."""
    __slots__ = ("comps", "degree", "knots")

    def value(self, u):
        return tuple(
            c if isinstance(c, float)
            else _eval_scalar(c, self.degree, self.knots, u)
            for c in self.comps)


class _QuatTrack:
    __slots__ = ("static", "points", "degree", "knots")

    def value(self, u):
        if self.static is not None:
            return self.static
        return _eval_quat(self.points, self.degree, self.knots, u)


def _parse_vec_track(data, o, mask_bits, qtype, default):
    """mask_bits: (static_flags, spline_flags) 3-bit each for x,y,z."""
    static, spline = mask_bits
    tr = _VecTrack()
    if spline:                       # any dynamic component -> spline header
        n_items, = struct.unpack_from("<H", data, o)
        degree = data[o + 2]
        o += 3
        tr.degree = degree
        tr.knots = data[o:o + n_items + degree + 2]
        o = _align(o + n_items + degree + 2, 4)
        extremes = [None] * 3
        comps = [None] * 3
        for i in range(3):
            bit = 1 << i
            if spline & bit:
                extremes[i] = struct.unpack_from("<2f", data, o)
                o += 8
                comps[i] = [0.0] * (n_items + 1)
            elif static & bit:
                comps[i] = struct.unpack_from("<f", data, o)[0]
                o += 4
            else:
                comps[i] = default
        if qtype == 0:                                       # 8-bit
            for t in range(n_items + 1):
                for i in range(3):
                    if spline & (1 << i):
                        mn, mx = extremes[i]
                        comps[i][t] = mn + (mx - mn) * (data[o] / 255.0)
                        o += 1
        elif qtype == 1:                                     # 16-bit
            for t in range(n_items + 1):
                for i in range(3):
                    if spline & (1 << i):
                        mn, mx = extremes[i]
                        v, = struct.unpack_from("<H", data, o)
                        comps[i][t] = mn + (mx - mn) * (v / 65535.0)
                        o += 2
        else:
            raise NotImplementedError(f"vector quantization type {qtype}")
        o = _align(o, 4)
        tr.comps = comps
    else:
        tr.degree = 0
        tr.knots = b""
        comps = []
        for i in range(3):
            if static & (1 << i):
                comps.append(struct.unpack_from("<f", data, o)[0])
                o += 4
            else:
                comps.append(default)
        tr.comps = comps
    return tr, o


def _parse_block(data, off, num_tracks, num_float_tracks):
    """Parse one compression block -> list of (pos, rot, scale) track objs."""
    masks = [tuple(data[off + 4 * i: off + 4 * i + 4])
             for i in range(num_tracks)]
    o = _align(off + 4 * num_tracks + num_float_tracks, 4)
    tracks = []
    for qt, pos_m, rot_m, scale_m in masks:
        pos_q = qt & 3
        rot_q = ((qt >> 2) & 0xF) + 2
        scale_q = (qt >> 6) & 3

        pos, o = _parse_vec_track(
            data, o, (pos_m & 7, (pos_m >> 4) & 7), pos_q, 0.0)

        rot = _QuatTrack()
        if rot_m & 0xF0:                                     # dynamic
            n_items, = struct.unpack_from("<H", data, o)
            degree = data[o + 2]
            o += 3
            rot.static = None
            rot.degree = degree
            rot.knots = data[o:o + n_items + degree + 2]
            o += n_items + degree + 2
            if rot_q == 4:                                   # 48-bit
                o = _align(o, 2)
            elif rot_q in (2, 7):                            # 32-bit / raw
                o = _align(o, 4)
            pts = []
            for _ in range(n_items + 1):
                q, o = _read_quat(rot_q, data, o)
                pts.append(q)
            rot.points = pts
        elif rot_m & 0x0F:                                   # static
            rot.static, o = _read_quat(rot_q, data, o)
        else:                                                # identity
            rot.static = (0.0, 0.0, 0.0, 1.0)
        o = _align(o, 4)

        scale, o = _parse_vec_track(
            data, o, (scale_m & 7, (scale_m >> 4) & 7), scale_q, 1.0)

        tracks.append((pos, rot, scale))
    return tracks


class SplineAnimation:
    """Decoded-on-demand hkaSplineCompressedAnimation."""

    def __init__(self, anim):
        self.duration = anim["duration"]
        self.num_tracks = anim["numberOfTransformTracks"]
        self.num_float_tracks = anim.get("numberOfFloatTracks", 0)
        self.num_frames = anim["numFrames"]
        self.num_blocks = anim["numBlocks"]
        self.max_frames_per_block = anim["maxFramesPerBlock"]
        self.block_duration = anim["blockDuration"]
        self.block_inv_duration = anim["blockInverseDuration"]
        self.frame_duration = anim["frameDuration"]
        self.block_offsets = anim["blockOffsets"]
        data = anim["data"]
        self.data = bytes(data) if not isinstance(data, bytes) else data
        self.blocks = [
            _parse_block(self.data, off, self.num_tracks,
                         self.num_float_tracks)
            for off in self.block_offsets]

    def sample_time(self, time):
        """Transforms for all tracks at time [0..duration] seconds.

        Block/local-time mapping per hkaSplineCompressedAnimation::
        getBlockAndTime (Havok SDK .inl): the spline parameter is the local
        block time scaled to frame units.
        """
        time = min(max(time, 0.0), self.duration)
        block = int(time * self.block_inv_duration)
        block = min(max(block, 0), self.num_blocks - 1)
        u = (time - block * self.block_duration) * \
            self.block_inv_duration * (self.max_frames_per_block - 1)
        out = []
        for pos, rot, scale in self.blocks[block]:
            out.append({"t": pos.value(u), "q": rot.value(u),
                        "s": scale.value(u)})
        return out

    def sample_frame(self, frame):
        """Transforms for all tracks at integer frame [0..numFrames-1]."""
        return self.sample_time(frame * self.frame_duration)


def decode_clip(did, dat=None):
    """Decode a client_anim.dat 0x05 record to per-bone transform tracks.

    Returns {duration, numFrames, numberOfTransformTracks,
             tracks: [track][frame] -> {'t': (x,y,z), 'q': (x,y,z,w),
                                        's': (x,y,z)}}
    """
    if dat is None:
        import config
        dat = config.anim()
    raw = dat.read_content(did)
    anim = parse_packfile(raw)
    if "hkaSplineCompressedAnimation" not in anim["__class__"]:
        raise NotImplementedError(
            f"clip 0x{did:08X} is {anim['__class__'][0]}, not spline")
    sa = SplineAnimation(anim)
    frames = [sa.sample_frame(f) for f in range(sa.num_frames)]
    # transpose to [track][frame]
    tracks = [[frames[f][t] for f in range(sa.num_frames)]
              for t in range(sa.num_tracks)]
    return {
        "duration": sa.duration,
        "numFrames": sa.num_frames,
        "numberOfTransformTracks": sa.num_tracks,
        "tracks": tracks,
    }


if __name__ == "__main__":
    import argparse, config
    ap = argparse.ArgumentParser(
        description="Decode a LOTRO Havok animation clip (0x05 record in "
                    "client_anim.dat) and print its summary.")
    ap.add_argument("did", nargs="?", default="0x050039EA",
                    help="clip DID (hex)")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)
    did = int(args.did, 16)
    clip = decode_clip(did)
    print(f"0x{did:08X}: duration={clip['duration']:.3f}s "
          f"frames={clip['numFrames']} "
          f"tracks={clip['numberOfTransformTracks']}")
