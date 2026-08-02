"""shaders.py — what LOTRO's 0x2B shader records are, and which ones cut out.

A mesh submesh binds a 0x31 surface; that surface record names a 0x2B shader
(see tex_extract._compact_surface_materials). The SHADER, not the texture,
decides how the texture's alpha channel is read:

    alpha-tested shader -> alpha is a CUTOUT (hair strands, filigree, straps)
    everything else     -> alpha is a TINT/DYE MASK, and the surface is opaque

That single fact reconciles two things that looked contradictory. The chargen
FACE atlas is 0% high-alpha across the face and 45%/38% on the eye and eyebrow
patches — a tint mask — and its head mesh binds 0x2B0009DE, which is NOT
alpha-tested. The HAIR of the same character binds 0x2B0009DF and 0x2B0009B7,
both alpha-tested, which is why hair renders with blocky opaque patches when
the cutout is ignored. The original motivating example, the Harvest-brew
Circlet, binds 0x2B000749 — alpha-tested.

CLI:  python3 shaders.py          print the classified shader table

HOW THESE WERE CLASSIFIED (reproducible, not guessed)
Each 0x2B record is a ~1 MB compiled shader blob whose string table lists its
uniforms. Counting occurrences of a uniform name across the blob is strongly
bimodal — a feature is either barely referenced (compiled out) or referenced in
every variant:
    c_AlphaTestThreshold        8 = absent      244 = alpha-test compiled in
    c_MaterialDyeColor          0 = undyeable   232/380 = dyeable
    c_SpecularMetallicAmount    0 = non-metal   224/228 = metallic
    c_MaterialSpecularColor     0 = flat        28 = specular
Sampled over 4783 Elf-F wearable entries: 16 distinct shaders, plus 0x2B0009B7
reached only through chargen hair.

Names below are OURS, not the game's — the blobs carry no shader name. They
describe the observed feature bits, so a shader we have not seen yet can be
classified by the same counts rather than by lookup.
"""

# shader DID -> (our name, alpha_tested, dyeable, metallic, note)
SHADERS = {
    0x2B0007A0: ("cloth_dyed",          False, True,  False, "the workhorse garment cloth; most-used by far"),
    0x2B0009DE: ("skin",                False, True,  False, "body + chargen face; alpha here is the TINT MASK"),
    0x2B0009DA: ("cloth_dyed_alt",      False, True,  False, "second opaque dyed cloth variant"),
    0x2B000A20: ("metal_dyed",          False, True,  True,  "armour; extra dye variants (380)"),
    0x2B0006EB: ("cloth_plain",         False, False, False, "opaque, undyeable"),
    0x2B0007D5: ("cloak_plain",         False, False, False, "no specular colour; mostly Back slot"),
    0x2B000712: ("cloth_flat",          False, True,  False, "dyed but no specular colour"),
    0x2B000A22: ("metal_dyed_alt",      False, True,  True,  "opaque metallic variant"),
    0x2B00081B: ("metal_dyed_alt2",     False, True,  True,  "opaque metallic variant"),

    # --- alpha-tested: these are the ones that must render with a cutout ---
    0x2B000749: ("cutout_dyed",         True,  True,  False, "Harvest-brew Circlet and kin"),
    0x2B0009DB: ("cutout_dyed_alt",     True,  True,  False, ""),
    0x2B000788: ("cutout_dyed_alt2",    True,  True,  False, ""),
    0x2B0009DF: ("cutout_hair",         True,  True,  False, "chargen hair; reduced specular power (30)"),
    0x2B0009B7: ("cutout_hair_plain",   True,  False, False, "chargen hair, undyeable"),
    0x2B0007A6: ("cutout_plain",        True,  False, False, "no dye, no specular colour"),
    0x2B000A1F: ("metal_cutout_dyed",   True,  True,  True,  "pierced/filigree metal"),
    0x2B000A21: ("metal_cutout_dyed_alt", True, True,  True,  ""),
}

ALPHA_TEST_COUNT = 244      # c_AlphaTestThreshold occurrences when compiled in
_UNKNOWN_CACHE = {}


def info(shader_did):
    """(name, alpha_tested, dyeable, metallic, note) — classifying an unknown
    shader by the same uniform counts rather than failing on it."""
    if shader_did in SHADERS:
        return SHADERS[shader_did]
    if shader_did in _UNKNOWN_CACHE:
        return _UNKNOWN_CACHE[shader_did]
    import re
    import tex_extract as tx
    raw = b""
    for dat in (tx._surf(), tx._gen()):
        try:
            raw = dat.read_content(shader_did) or b""
            if raw:
                break
        except Exception:
            pass
    n = lambda k: len(re.findall(b"c_" + k, raw))
    got = ("unknown_0x%08X" % shader_did,
           n(b"AlphaTestThreshold") >= ALPHA_TEST_COUNT // 2,
           n(b"MaterialDyeColor") > 0,
           n(b"SpecularMetallicAmount") > 0,
           "auto-classified from uniform counts")
    _UNKNOWN_CACHE[shader_did] = got
    return got


def is_alpha_tested(shader_did):
    return bool(info(shader_did)[1])


def surface_shader(surf_did):
    """The 0x2B shader a 0x31 surface binds, or None."""
    import struct
    import tex_extract as tx
    for dat in (tx._surf(), tx._gen()):
        try:
            r = dat.read_content(surf_did)
            if r and len(r) >= 22:
                _did, sh, _key, _n = struct.unpack_from("<4I", r, 0)
                if (sh >> 24) == 0x2B:
                    return sh
        except Exception:
            pass
    return None


def surface_alpha_tested(surf_did):
    sh = surface_shader(surf_did)
    return bool(sh and is_alpha_tested(sh))


if __name__ == "__main__":
    import argparse, config
    ap = argparse.ArgumentParser(
        description="Print the classified 0x2B shader table (name, alpha-test, "
                    "dyeable, metallic).")
    config.add_game_dir_argument(ap)
    config.apply_args(ap.parse_args())
    for did, (name, a, d, m, note) in sorted(SHADERS.items(),
                                             key=lambda kv: (not kv[1][1], kv[1][0])):
        print("0x%08X  %-22s %-11s %-9s %-9s %s"
              % (did, name, "ALPHA-TEST" if a else "opaque",
                 "dyeable" if d else "-", "metallic" if m else "-", note))
