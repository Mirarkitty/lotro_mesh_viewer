#!/usr/bin/env python3
"""Export a skinned LOTRO mesh + skeleton + animation clip as one JSON for the
three.js /anim viewer.

    python3 export_skinned.py <mesh_did> <skel_json> <clip_did> <out_name> [compose_json]

  mesh_did      mesh DID (hex like 0x060028FD)
  skel_json     decoded/skel_*.json ({bones:[{name,parent,t,q,s}]}, exported
                from the 0x04 hkaSkeleton record; see docs/animation.md)
  clip_did      client_anim.dat 0x05 clip DID (hex or decimal)
  out_name      writes decoded/<out_name>.json
  compose_json  optional decoded/*.json whose groups carry per-submesh textures
                for the SAME mesh (group order = submesh order); textures are
                copied over, otherwise groups are untextured.

Output: {vertices, normals, uvs, triangles, groups, skinIndices (flat, 4/vertex),
skinWeights (flat, 4/vertex), bones:[{name,parent,t,q,s}],
clip:{did, duration, frames, fps, tracks:[[ [t3,q4,s3] per frame ] per bone]}}.

Per-vertex skin layout (verified 2026-07-31, zero bad weights on 5 meshes):
  stride 71: boneIdx u8[3] @56, weight f32[3] @59 (sum 1.0)
  stride 76: boneIdx u8[4] @56, weight f32[4] @60
Bone indices index the skeleton bone array directly. Track order in clips ==
skeleton bone order (track count == bone count).

Coordinates stay Z-up as stored; the viewer rotates the SkinnedMesh object.
"""
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mesh_decode
import havok_anim

# stride -> (n, idx_off, w_off): n bone-index bytes at idx_off, n float32
# weights at w_off. Validated against dwm rig (all-verts bone-name sanity):
#   61 = 56 static + 1 idx + 1 w   (rigid: caps -> head, packs -> thorax)
#   66 = 56 static + 2 idx + 2 w   (boots/greaves: knee/ankle/toe)
#   71 = 56 static + 3 idx + 3 w
#   75 = 60 static + 3 idx + 3 w   (cloaks: thorax/collar/cape chain; 4-byte
#                                   longer static part than the 56-byte family)
#   65 = 60 static + 1 idx + 1 w   (hair, rigid -> head: Man-F 0x0600DCAE)
#   70 = 60 static + 2 idx + 2 w   (hair, head/neck/ear_offset: Elf 0x0601134C)
#   80 = 60 static + 4 idx + 4 w   (hooded cloaks: cape chain + head/neck)
#   74/79/84 = 64 static + 2/3/4    (armour garments; 69=n1 predicted, unseen)
#   73 = 68 static + 1 idx + 1 w   (helms rigid -> head)
# Static prefix sizes seen: 56, 60, 64, 68 — never assume, always derive.
# Beware a false-positive candidate at io~35-39 (u8 0 + f32 1.0 in the
# tangent region reads as bone 'parent' w=1): require sensible bone NAMES.
#   76 = 56 static + 4 idx + 4 w
SKIN_LAYOUT = {61: (1, 56, 57), 66: (2, 56, 58), 71: (3, 56, 59),
               65: (1, 60, 61), 70: (2, 60, 62),
               75: (3, 60, 63), 76: (4, 56, 60), 80: (4, 60, 64),
               74: (2, 64, 66), 79: (3, 64, 67), 84: (4, 64, 68),
               73: (1, 68, 69)}


def _fin(x):
    return x if math.isfinite(x) else 0.0


def _san(seq):
    return [_fin(float(v)) for v in seq]


def skin_arrays(raw, nbones):
    """Per-vertex skin data for a whole GfxObj record, in block (== group ==
    vertex) order: returns (idx, w, bad) where idx/w are per-vertex lists of
    exactly 4 bone indices / weights (padded), aligned 1:1 with the vertex
    order that mesh_decode._decode_gfxobj produces. Unsupported (unskinned)
    strides fall back to bone 0 / weight 1 for the whole block."""
    blocks = mesh_decode._find_vertex_blocks(raw)
    idxs, ws = [], []
    bad = 0
    for (vs, count, stride) in blocks:
        if stride not in SKIN_LAYOUT:
            for _ in range(count):
                idxs.append([0, 0, 0, 0])
                ws.append([1.0, 0.0, 0.0, 0.0])
            bad += count
            continue
        n, io, wo = SKIN_LAYOUT[stride]
        for i in range(count):
            b = vs + i * stride
            idx = list(raw[b + io:b + io + n])
            w = list(struct.unpack_from("<%df" % n, raw, b + wo))
            if max(idx) >= nbones or not all(math.isfinite(x) for x in w) \
               or abs(sum(w) - 1.0) > 0.02:
                bad += 1
                idx, w = [0] * n, [1.0] + [0.0] * (n - 1)
            # pad to 4 slots for three.js skinIndex/skinWeight
            idxs.append(idx + [0] * (4 - n))
            ws.append(_san(w) + [0.0] * (4 - n))
    return idxs, ws, bad


def skeleton_bones(skel_did):
    """Decode a client_general 0x04 hkaSkeleton record to
    [{name, parent, t, q, s}], cached as decoded/skel_<did>.json.
    referencePose entries are flat 12-float hkQsTransforms:
    t[0:3]+pad, q[4:8] xyzw, s[8:11]+pad."""
    import config
    fp = os.path.join(config.decoded_dir(), "skel_%08X.json" % skel_did)
    if os.path.exists(fp):
        return json.load(open(fp))["bones"]
    raw = config.general().read_content(skel_did)
    i = raw.find(bytes.fromhex("1e0db0cacefa11d0"))  # tagfile magic
    if i < 0:
        raise ValueError("0x%08X: no Havok tagfile magic" % skel_did)
    root, _ = havok_anim.parse_tagfile(raw[i:])
    sk = havok_anim.find_objects(root, "hkaSkeleton")[0]
    bones = []
    for name_b, par, rp in zip(sk["bones"], sk["parentIndices"],
                               sk["referencePose"]):
        bones.append({"name": name_b["name"], "parent": par,
                      "t": _san(rp[0:3]), "q": _san(rp[4:8]),
                      "s": _san(rp[8:11])})
    json.dump({"did": "0x%08X" % skel_did, "bones": bones}, open(fp, "w"))
    return bones


def clip_json(clip_did, nbones=None):
    """Decode a 0x05 clip to the viewer JSON clip block
    {did, duration, frames, fps, tracks}. Warns if track count != nbones."""
    clip = havok_anim.decode_clip(clip_did)
    frames = clip["numFrames"]
    dur = clip["duration"]
    if nbones is not None and clip["numberOfTransformTracks"] != nbones:
        print("WARNING: %d tracks != %d bones"
              % (clip["numberOfTransformTracks"], nbones))
    tracks = [[[_san(fr["t"]), _san(fr["q"]), _san(fr["s"])] for fr in tr]
              for tr in clip["tracks"]]
    return {"did": "0x%08X" % clip_did, "duration": dur, "frames": frames,
            "fps": (frames - 1) / dur if dur > 0 else 30.0, "tracks": tracks}


def export(mesh_did, skel_json, clip_did, out_name, compose_json=None):
    raw = mesh_decode._read(mesh_did)
    mesh = mesh_decode._decode_gfxobj(raw)

    bones = json.load(open(skel_json))["bones"]
    nbones = len(bones)

    # --- per-vertex skin data, concatenated in block (== group) order ---
    idxs, ws, bad = skin_arrays(raw, nbones)
    skin_idx = [v for quad in idxs for v in quad]
    skin_w = [v for quad in ws for v in quad]
    if bad:
        print("WARNING: %d vertices had bad skin data (reset to bone 0)" % bad)

    # --- textures from an existing compose/verify JSON for the same mesh ---
    groups = mesh["groups"]
    if compose_json and os.path.exists(compose_json):
        cg = json.load(open(compose_json)).get("groups", [])
        if len(cg) == len(groups):
            for g, c in zip(groups, cg):
                if c.get("vert_count") == g["vert_count"]:
                    g["texture"] = c.get("texture")
        else:
            print("WARNING: compose group count %d != mesh %d; untextured"
                  % (len(cg), len(groups)))

    # --- clip ---
    clip = clip_json(clip_did, nbones)

    out = {
        "id": out_name,
        "num_submeshes": mesh["num_submeshes"],
        "vertices": [_san(v) for v in mesh["vertices"]],
        "normals": [_san(v) for v in mesh["normals"]],
        "uvs": [_san(v) for v in mesh["uvs"]],
        "triangles": mesh["triangles"],
        "groups": groups,
        "skinIndices": skin_idx,
        "skinWeights": skin_w,
        "bones": [{"name": b["name"], "parent": b["parent"],
                   "t": _san(b["t"]), "q": _san(b["q"]), "s": _san(b["s"])}
                  for b in bones],
        "clip": clip,
    }
    import config
    fp = os.path.join(config.decoded_dir(), out_name + ".json")
    json.dump(out, open(fp, "w"))
    print("wrote %s: %d verts, %d bones, clip 0x%08X %.3fs/%d frames"
          % (fp, len(out["vertices"]), nbones, clip_did,
             clip["duration"], clip["frames"]))


if __name__ == "__main__":
    import argparse, config
    ap = argparse.ArgumentParser(
        description="Export a skinned mesh + skeleton + animation clip as one "
                    "viewer JSON (decoded/<out_name>.json) for the /anim page.")
    ap.add_argument("mesh_did", help="mesh DID (hex, e.g. 0x060028FD)")
    ap.add_argument("skel_json",
                    help="skeleton JSON (decoded/skel_*.json; produce one with "
                         "skeleton_bones() from a 0x04 hkaSkeleton DID)")
    ap.add_argument("clip_did", help="client_anim.dat 0x05 clip DID (hex)")
    ap.add_argument("out_name", help="output name -> decoded/<out_name>.json")
    ap.add_argument("compose_json", nargs="?",
                    help="optional decoded/*.json for the SAME mesh whose "
                         "groups carry per-submesh textures to copy over")
    config.add_game_dir_argument(ap)
    args = ap.parse_args()
    config.apply_args(args)
    export(int(args.mesh_did, 0), args.skel_json, int(args.clip_did, 0),
           args.out_name, args.compose_json)
