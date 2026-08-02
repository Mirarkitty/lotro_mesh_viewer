"""Turbine DAT archive reader (LOTRO client_*.dat / *.datx files).

A DAT archive is a block-structured container with a B-tree directory mapping
32-bit data IDs ("DIDs") to file entries. The high byte of a DID encodes the
resource type (0x06 = mesh, 0x25 = text, 0x30 = material, 0x41 = texture, ...).

Header (at fixed offsets from the start of the file):
    0x101  u16  0x4C50 ('PL')   magic
    0x140  u16  0x5442 ('BT')   B-tree marker
    0x144  u32  block size
    0x148  u32  archive size
    0x14C  u32  version
    0x160  u32  root directory-node offset

Two distinct payload framings exist and BOTH are used by the tools:

* ``read_content(did)`` -- the exact ("true") file bytes, assembled from the
  block chain and zlib-inflated when the entry's compressed flag is set.
  Content always starts with the record's own DID. Use this for property
  sets, wardrobe records, materials and anything parsed byte-exactly:
  ``read_asset`` over-reads small records to a whole archive block, which
  makes neighbouring records bleed into the tail (a real, observed bug
  source -- see docs/textures.md).
* ``read_asset(offset, size)`` -- the historical mesh/texture framing:
  ``[next_ptr:u32][pad:u32][uncompressed_size:u32][body]`` where body is
  usually a raw zlib stream. Kept because the mesh and texture decoders were
  validated against exactly these bytes.

CLI:  python3 datfile.py <archive> info|walk|find|extract [DID] [-o FILE]
"""
import struct, os, threading

class DatFile:
    """One archive, one file handle PER THREAD.

    A shared handle has a shared file position, so two concurrent readers
    interleave their seeks and each gets the other's bytes. Locking every
    seek+read sequence (the previous fix) is correct in principle but proved
    fragile: these archives are opened by several modules and the read paths
    recurse, so it only takes one unguarded pair to corrupt a read — the
    observed symptom being `AssertionError: dir node prefix not zero` from
    read_dir under load, which then surfaced in the viewer as a slot silently
    falling back to an unskinned mesh that never animates.

    Thread-local handles remove the shared position altogether, so no lock
    discipline is needed and concurrency is kept. `self.lock` is retained (as a
    no-op RLock) only so any caller still using it keeps working."""
    def __init__(self, filename):
        self.filename=filename
        self.file_size=os.stat(filename).st_size
        self.lock=threading.RLock()
        self._local=threading.local()
        self.f=open(filename,"rb")
        buf=self.f.read(2048)
        assert self._d(buf,0x101)==0x4C50, "not a Turbine PL dat"
        assert self._d(buf,0x140)==0x5442, "no BT magic"
        self.block_size=self._d(buf,0x144)
        self.size=self._d(buf,0x148)
        self.version=self._d(buf,0x14C)
        self.dir_off=self._d(buf,0x160)
    @property
    def f(self):
        """This thread's own handle (opened on first use)."""
        h=getattr(self._local,"h",None)
        if h is None:
            h=self._local.h=open(self.filename,"rb")
        return h

    @f.setter
    def f(self,v):
        self._local.h=v

    @staticmethod
    def _d(buf,o): return struct.unpack("<L",buf[o:o+4])[0]

    def read_dir(self, offset):
        """Return (subdirs, entries) — ALL entries (unfiltered) so B-tree order holds."""
        with self.lock:
            f=self.f; f.seek(offset)
            assert f.read(8)==b"\0"*8, "dir node prefix not zero @0x%X"%offset
            subdirs=[]
            for i in range(62):
                bs,doff=struct.unpack("<LL",f.read(8))
                if bs==0: break
                subdirs.append(doff)
            f.seek(offset+8*63)
            count=struct.unpack("<L",f.read(4))[0]
            subdirs=subdirs[:count+1]
            entries=[]
            for i in range(count):
                unk1,file_id,foff,size1,ts,ver,size2,unk2=struct.unpack("<8L",f.read(0x20))
                # unk1 = [flags:u16][policy:u16]; isCompressed = flags & 1
                flags=unk1 & 0xFFFF
                entries.append((file_id,foff,size1,size2,ts,ver,flags))
            return subdirs, entries

    def walk(self, visit, offset=None):
        if offset is None: offset=self.dir_off
        stack=[offset]
        while stack:
            subdirs,entries=self.read_dir(stack.pop())
            for e in entries:
                if e[2]>0: visit(e)   # size1>0
            stack.extend(subdirs)

    def find_file(self, target, off=None):
        """B-tree lookup by DID. Returns entry tuple (file_id,foff,size1,size2,ts,ver) or None."""
        if off is None: off=self.dir_off
        subdirs,entries=self.read_dir(off)
        for i,e in enumerate(entries):
            if e[0]==target: return e
            if target < e[0]:
                return self.find_file(target, subdirs[i]) if subdirs else None
        return self.find_file(target, subdirs[len(entries)]) if subdirs else None

    def read_file(self, offset, size):
        with self.lock:
            self.f.seek(offset); return self.f.read(size)

    def read_content(self, did):
        """Return the TRUE file content bytes for `did`, matching dmorcellet's
        DATArchive.loadEntry exactly (block-chain assembly + optional zlib).
        Content STARTS with the self-DID (uint32 LE). Raises KeyError if absent.

        On-disk layout per file: [numExtraBlocks:u32][legacy:u32] then firstChunk;
        extra blocks are (size:u32, offset:u32) pairs read from elsewhere in the
        archive. If FileEntry flags&1 (compressed): content = zlib(raw[4:]) where
        raw[0:4] is the uncompressed size; else content = raw as-is."""
        import zlib
        e=self.find_file(did)
        if not e: raise KeyError("0x%08X not in %s"%(did,self.filename))
        foff,size,blockSize=e[1],e[2],e[3]
        flags=e[6] if len(e)>6 else 0
        with self.lock:
            f=self.f; f.seek(foff)
            hdr=f.read(8)
            numExtra,legacy=struct.unpack("<II",hdr)
            if legacy!=0:
                raise NotImplementedError("old block format (0x%08X)"%did)
            firstChunkSize=blockSize-8-numExtra*8
            if firstChunkSize>size: firstChunkSize=size
            raw=bytearray(f.read(firstChunkSize))
            if numExtra:
                tbl=f.read(numExtra*8)
                idx=firstChunkSize
                for i in range(numExtra):
                    bsz,boff=struct.unpack("<II",tbl[i*8:i*8+8])
                    f.seek(boff)
                    raw+=f.read(min(bsz,size-idx)); idx+=min(bsz,size-idx)
            raw=bytes(raw[:size])
        if flags & 1:
            return zlib.decompress(raw[4:])
        return raw

    def read_asset(self, foff, size2):
        """Return (usize, decompressed_bytes). Record: [nextptr:4][pad:4][usize:4][body].
        body is usually a zlib stream; some assets store it uncompressed."""
        import zlib
        with self.lock:
            self.f.seek(foff); rec=self.f.read(size2+64)
        usize=struct.unpack("<I", rec[8:12])[0]
        zi=None
        for i in range(12,12+8):
            if rec[i]==0x78 and rec[i+1] in (0x01,0x9C,0xDA): zi=i; break
        if zi is not None:
            try:
                return usize, zlib.decompressobj().decompress(rec[zi:])
            except zlib.error:
                pass
        return usize, rec[12:12+usize]   # uncompressed fallback


class DatChain:
    """Look up DIDs across several archives (e.g. client_mesh.dat +
    client_mesh_aux_1.datx). find_file remembers the owning archive so
    read_asset(offset, size) hits the right file."""
    def __init__(self, *paths):
        self.dats = [DatFile(p) for p in paths]
        self._owner = {}
    def find_file(self, did):
        for d in self.dats:
            e = d.find_file(did)
            if e:
                self._owner[(e[1], e[3])] = d
                self._owner_did = d
                return e
        return None
    def read_asset(self, offset, size):
        d = self._owner.get((offset, size))
        if d is None:
            for d2 in self.dats:
                try:
                    return d2.read_asset(offset, size)
                except Exception:
                    continue
            raise KeyError("no archive owns asset @%d" % offset)
        return d.read_asset(offset, size)
    def read_content(self, did):
        for d in self.dats:
            if d.find_file(did):
                return d.read_content(did)
        raise KeyError("DID 0x%08X in no archive" % did)
    def walk(self, visit):
        for d in self.dats:
            d.walk(visit)


def _main():
    import argparse, sys
    ap = argparse.ArgumentParser(
        description="Inspect a Turbine DAT archive: header info, directory "
                    "walk, DID lookup, raw content extraction.")
    ap.add_argument("archive", help="path to a client_*.dat file")
    ap.add_argument("cmd", choices=["info", "walk", "find", "extract"],
                    help="info: header summary; walk: list all entries; "
                         "find: look up one DID; extract: write a DID's "
                         "true content bytes to a file")
    ap.add_argument("did", nargs="?",
                    help="DID for find/extract (hex, e.g. 0x06001989)")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="output file for extract (default: <DID>.bin)")
    args = ap.parse_args()

    d = DatFile(args.archive)
    if args.cmd == "info":
        n = [0]
        d.walk(lambda e: n.__setitem__(0, n[0] + 1))
        print("%s: version=%d block_size=%d size=%d entries=%d"
              % (args.archive, d.version, d.block_size, d.size, n[0]))
    elif args.cmd == "walk":
        d.walk(lambda e: print("0x%08X  size=%-8d ts=%d ver=%d flags=0x%X"
                               % (e[0], e[2], e[4], e[5], e[6])))
    else:
        if not args.did:
            sys.exit("find/extract need a DID argument")
        did = int(args.did, 16)
        e = d.find_file(did)
        if e is None:
            sys.exit("0x%08X: not in this archive" % did)
        print("0x%08X  offset=0x%X size=%d blocksize=%d flags=0x%X"
              % (e[0], e[1], e[2], e[3], e[6]))
        if args.cmd == "extract":
            out = args.output or "0x%08X.bin" % did
            with open(out, "wb") as f:
                f.write(d.read_content(did))
            print("wrote", out)


if __name__ == "__main__":
    _main()
