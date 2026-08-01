"""Strict sequential parser for 0x20 worn-appearance records (v7).

Structure (derived 2026-07-31, validated byte-exact against full records):
  u32 DID ; u32 0
  u8 sectype ; vle count          -- first section (5/6: wearable entries)
  count x entry:
      u32 key (0x1000xxxx)        -- Item_AppearanceKey
      u32 nblocks
      nblocks x block:
          f32 q                   -- dye floatCode (0.00,0.01,... steps); block0 usually 0.0/1.0
          u32 gc ; f32 d
          gc x group { u32 x(0|0x2B DID) ; u32 material(0x30) ; u32 A ; u32 B ; f32 g }
          u32 pc ; f32 e
          pc x part { u32 tag(0x10) ; u32 mesh(0x06) ; f32 lod (ALWAYS present; often 0 on last) }
          u32 0                   -- block tail
  then more sections: u8 type ; vle count ; payload
      type 1: count x { u32 key ; u32 val }
"""
import struct, sys

class R:
    def __init__(self,d,p=0): self.d=d; self.p=p
    def u8(self): v=self.d[self.p]; self.p+=1; return v
    def u32(self): v=struct.unpack_from("<I",self.d,self.p)[0]; self.p+=4; return v
    def f32(self): v=struct.unpack_from("<f",self.d,self.p)[0]; self.p+=4; return v
    def peek(self,off=0):
        if self.p+off+4>len(self.d): return None
        return struct.unpack_from("<I",self.d,self.p+off)[0]
    def vle(self):
        a=self.u8()
        if a==0xE0: return self.u32()
        if (a&0x80)==0: return a
        b=self.u8()
        if (a&0x40)==0: return b|((a&0x7F)<<8)
        c=struct.unpack_from("<H",self.d,self.p)[0]; self.p+=2
        return ((a&0x3F)<<24)|(b<<16)|c

def err(r,msg):
    raise ValueError("@0x%X: %s (next: %s)"%(r.p,msg,
        ["0x%08X"%(r.peek(i*4) or 0) for i in range(6)]))

def parse_block(r,i,bi):
    q=r.f32()
    gc=r.u32(); d=r.f32()
    groups=[]
    for gi in range(gc):
        x=r.u32(); material=r.u32()
        if material!=0 and (material>>24)!=0x30: err(r,"e%d b%d g%d bad material 0x%08X"%(i,bi,gi,material))
        A=r.u32(); B=r.u32(); g=r.f32()
        groups.append(dict(x=x,material=material,A=A,B=B,g=g))
    pc=r.u32()
    if pc>200: err(r,"e%d b%d absurd partCount %d"%(i,bi,pc))
    e=r.f32()
    parts=[]
    for j in range(pc):
        tag=r.u32()
        if (tag>>24)!=0x10 and tag>=0x100: err(r,"e%d b%d p%d bad tag 0x%08X"%(i,bi,j,tag))
        mesh=r.u32()
        if (mesh>>24)!=0x06: err(r,"e%d b%d p%d bad mesh 0x%08X"%(i,bi,j,mesh))
        lod=r.f32()
        parts.append(dict(tag=tag,mesh=mesh,lod=lod))
    z=r.u32()
    if z!=0: err(r,"e%d b%d bad block tail 0x%08X"%(i,bi,z))
    return dict(q=q,d=d,groups=groups,e=e,parts=parts)

def parse_entry(r,i):
    key=r.u32()
    if (key>>24)!=0x10: err(r,"e%d bad key 0x%08X"%(i,key))
    nb=r.u32()
    if nb>1000: err(r,"e%d absurd nblocks %d"%(i,nb))
    blocks=[parse_block(r,i,bi) for bi in range(nb)]
    return dict(key=key,blocks=blocks)

def parse_record(content):
    r=R(content)
    did=r.u32()
    z=r.u32()
    if z!=0: err(r,"nonzero after DID")
    sections=[]
    while r.p<len(content):
        stype=r.u8(); count=r.vle()
        if stype!=1:
            entries=[parse_entry(r,i) for i in range(count)]
            sections.append(dict(type=stype,count=count,entries=entries))
        elif stype==1:
            pairs=[(r.u32(),r.u32()) for _ in range(count)]
            sections.append(dict(type=stype,count=count,pairs=pairs))
        else:
            err(r,"unknown section type %d (count %d)"%(stype,count))
    return dict(did=did,sections=sections,leftover=len(content)-r.p)

def entries(rec):
    for s in rec["sections"]:
        if s["type"] != 1: return s["entries"]
    return []

if __name__=="__main__":
    import argparse, config
    ap = argparse.ArgumentParser(
        description="Strictly parse a 0x20 worn-appearance record and print "
                    "its section/entry/block structure summary.")
    ap.add_argument("did", nargs="?", default="0x20001E55",
                    help="0x20 worn-appearance DID (hex)")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)
    did=int(args.did,16)
    rec=parse_record(config.general().read_content(did))
    print("0x%08X: %s leftover=%d"%(rec["did"],
        ["type%d x%d"%(s["type"],s["count"]) for s in rec["sections"]],rec["leftover"]))
    es=entries(rec)
    nb=[len(e["blocks"]) for e in es]
    print("entries=%d blocks/entry min=%d max=%d total=%d"%(len(es),min(nb),max(nb),sum(nb)))
