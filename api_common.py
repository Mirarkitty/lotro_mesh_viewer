"""api_common.py — shared API logic for the LOTRO viewers (app.py, outfit_app.py).

Framework-free: every function returns plain dicts/lists. The data layer stays
in the dedicated modules (datfile, propset, wearable2, selector, tex_extract,
mesh_decode, compose); this file is the item-catalog / search / presence /
composition glue both web frontends share.
"""
import os, threading, json, re, unicodedata, collections
import config

def fold(t):
    """Accent-fold + lowercase for search matching (Ordâkhai -> ordakhai)."""
    return "".join(c for c in unicodedata.normalize("NFD", t or "")
                   if unicodedata.category(c) != "Mn").lower()

HERE = os.path.dirname(os.path.abspath(__file__))   # shipped data files live here
DECODED = config.decoded_dir()
TEXTURES = config.textures_dir()

SPECIES = {23: "Man", 65: "Elf", 73: "Dwarf", 81: "Hobbit", 114: "Beorning",
           117: "HighElf", 120: "Stoutaxe", 125: "sp125"}
SEX = {0x1000: "M", 0x2000: "F"}
SLOT_BITS = {0x02: "Head", 0x04: "Chest", 0x08: "Legs", 0x10: "Hands",
             0x20: "Feet", 0x40: "Shoulders", 0x80: "Back"}
GARMENT_TAG = 0x1000000C

# Player rigs (client_general 0x04 hkaSkeleton) per body label.
# 551/552/557 are the named hbm/hbf/dwm rigs (62 bones). 553-556 are the four
# unprefixed 89-bone player rigs; 554 = Man-F VERIFIED by bind-pose render
# (Man-F dress binds at human proportions, un-mangled) -> DID-order assignment
# 553 = Man-M, 555 = Elf-M, 556 = Elf-F holds. See docs/animation.md.
RIGS = {
    "Man-M":    0x04000553,
    "Man-F":    0x04000554,
    "Elf-M":    0x04000555,
    "Elf-F":    0x04000556,
    "Hobbit-M": 0x04000551,
    "Hobbit-F": 0x04000552,
    "Dwarf-M":  0x04000557,
}

# rig DID -> VISUALLY VERIFIED locomotion clip used as the composer idle.
# Only add entries after looking at a render (docs/animation.md);
# bodies without an entry render the bind pose, static.
# NOTE: a clip_names.json entry whose name contains "walk" for the body's rig
# stem OVERRIDES this table (see default_clip) — name clips in the UI instead
# of editing here.
IDLE_CLIPS = {
    0x04000557: 83893544,   # dwm walk (0x05001D28, duty-factor verified 2026-07-31)
}

# body label -> clips_by_rig.json bucket stem. The four 89-bone player rigs
# (Man/Elf) share one anonymous clip pool: bucket "?" filtered to tracks==89,
# stem "pc89" (clips carry no annotation names, so clips_by_rig can't split
# them per rig; positionally any 89-track clip fits all four).
BODY_STEM = {"Hobbit-M": "hbm", "Hobbit-F": "hbf", "Dwarf-M": "dwm",
             "Man-M": "pc89", "Man-F": "pc89", "Elf-M": "pc89", "Elf-F": "pc89"}

# ---- clip naming (persistent human clip identifications) ---------------------
# clip_names.json is git-tracked and is THE place clip identifications live:
# {"<did decimal>": {"name": "walk_fwd", "rig": "dwm"|"hbm"|"hbf"|"pc89",
#                    "tags": [...]}}. Written by the /clipname route.
CLIP_NAMES = os.path.join(HERE, "clip_names.json")

def clip_names():
    try:
        with open(CLIP_NAMES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def set_clip_name(did, name, body=None):
    """Save/overwrite the user-assigned name for a clip; empty name clears it."""
    names = clip_names()
    key = str(int(did))
    if not name:
        names.pop(key, None)
    else:
        e = names.get(key) or {}
        e["name"] = name
        stem = BODY_STEM.get(body or "")
        if stem:
            e["rig"] = stem
        names[key] = e
    tmp = CLIP_NAMES + ".tmp"
    with open(tmp, "w") as f:
        json.dump(names, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, CLIP_NAMES)
    return names.get(key)

_clips_by_rig = None
def _rig_clips():
    global _clips_by_rig
    if _clips_by_rig is None:
        with open(os.path.join(HERE, "clips_by_rig.json")) as f:
            _clips_by_rig = json.load(f)
    return _clips_by_rig

def _idle_flags():
    """idle_flags.json (built offline by idle_scan.py): {did: {amp, idle}}."""
    try:
        with open(os.path.join(HERE, "idle_flags.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def _gait_flags():
    """gait_flags.json (built offline by gait_flags_scan.py, git-tracked):
    {did: {"gait": "walk_fwd?"|"run_back?"|"riding?"|None, "conf": float,
           "partial": bool, "transition": bool,
           "pose": "base-A"|"p:base-A"|None, ...metrics}}.
    partial = most tracks static (layered overlay clip, freezes the body in
    our single-clip player); transition = non-looping fragment (first/last
    pose far apart). pose = endpoint pair from the per-rig pose graph;
    "p:" prefix = partial clip clustered over its ANIMATED joints only
    (p:base-A = the overlay's animated bones start at bind pose);
    base-base = standing loop. Hints only — never overrides user-assigned names."""
    try:
        with open(os.path.join(HERE, "gait_flags.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def is_riding(row):
    """Riding/mounted clip. Two independent signals, either sufficient: the
    gait classifier's pelvis-high-and-feet-off-the-ground test, and the pose
    graph's high-root cluster series ("riding", "ridingA", …). The pose signal
    catches clips whose gait confidence fell short."""
    return (row.get("gait") == "riding?"
            or "riding" in (row.get("pose") or ""))


def _dup_groups(stem):
    """dup_scan.py output: did -> (representative did, group size). Clips whose
    MOTION matches (phase-normalised pose fingerprint) collapse to one row."""
    try:
        with open(os.path.join(HERE, "dup_groups_%s.json" % stem)) as f:
            groups = json.load(f)["groups"]
    except (OSError, ValueError, KeyError):
        return {}
    rep = {}
    for g in groups:
        if len(g) < 2:
            continue
        r = min(g)                      # lowest DID represents the group
        for did in g:
            rep[did] = (r, len(g))
    return rep


def _stance_flags():
    """stance_flags.json (built offline by stance_scan.py):
    {did: {stance: "sneak?"|"combat?"|"peaceful?"|None, conf, ...features}}.
    Hints only, like gait — see stance_scan.py for what each is worth."""
    try:
        with open(os.path.join(HERE, "stance_flags.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _foot_flags():
    """foot_flags.json (built offline by foot_scan.py): {did: {posture, conf,
    ...features}} where posture is "standing?"/"kneel?"/"shuffle?"/None — the
    non-locomotion half of the foot classification. gait_flags owns anything
    that IS locomotion; these two never both fire (see foot_scan.py)."""
    try:
        with open(os.path.join(HERE, "foot_flags.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Dropdown motion filter -> predicate on a row. Matched against the gait label
# (walk/run x fwd/back/strafe, from foot duty-cycle + swing direction) and the
# posture label (standing/kneel, from feet planted + leg geometry).
MOTION_FILTERS = {
    "loco":    lambda r: bool(r["gait"]) and r["gait"] != "riding?",
    "walk":    lambda r: (r["gait"] or "").startswith("walk"),
    "run":     lambda r: (r["gait"] or "").startswith("run"),
    "fwd":     lambda r: "fwd" in (r["gait"] or ""),
    "back":    lambda r: "back" in (r["gait"] or ""),
    "strafe":  lambda r: "strafe" in (r["gait"] or ""),
    "standing": lambda r: r["posture"] == "standing?",
    "kneel":   lambda r: r["posture"] == "kneel?",
    "unclassified": lambda r: not r["gait"] and not r["posture"],
}


def clips_for_body(body, cap=400, riding=None, dedupe=True, motion=None):
    """Candidate clips for a body's rig:
    [{did, duration, frames, name, idle, gait, pose, partial, transition,
      riding, dup_of, dup_n}].

    riding: None = all, "hide" = drop mounted clips, "only" = keep just those.
    Filtered BEFORE the cap, so "hide" spends the whole budget on ground
    clips instead of leaving 281 riding rows in the way (Man/Elf pool).

    dedupe: collapse motion-duplicate clips (dup_scan.py) to one row carrying
    dup_n; the members stay reachable by DID, they just stop padding the list.

    ORDER IS STABLE ACROSS NAMING: one flat ascending-DID list; names,
    [idle?] and gait hints are decorations only (a user auditions
    sequentially, so "next item" must not move when a clip gets named).
    Selection (which clips make the cap) is likewise computed WITHOUT looking
    at names — duration-band quotas (~30% of the cap reserved for idle-length
    2.5-10 s clips so the huge pc89 pool doesn't starve idle candidates) —
    then every human-named clip is unioned in, so naming a clip already in
    the list changes nothing and naming an off-list clip only inserts it.
    "auto:*" classifier names are deliberately NOT unioned: they are the same
    hint the [gait] decoration already shows, and unioning them pushed 281
    identical-looking "auto:riding" rows past the cap (734-entry dropdown)."""
    stem = BODY_STEM.get(body)
    if not stem:
        raise ValueError("unknown body %r" % body)
    d = _rig_clips()
    if stem == "pc89":
        pool = [c for c in d.get("?", []) if c["tracks"] == 89]
    else:
        pool = d.get(stem, [])
    names = clip_names()
    idle = _idle_flags()
    gait = _gait_flags()
    stance = _stance_flags()
    foot = _foot_flags()
    dups = _dup_groups(stem) if dedupe else {}
    out = []
    for c in pool:
        e = names.get(str(c["did"])) or {}
        g = gait.get(str(c["did"])) or {}
        s = stance.get(str(c["did"])) or {}
        ft = foot.get(str(c["did"])) or {}
        row = {"did": c["did"], "duration": c["duration"],
               "frames": c["frames"], "name": e.get("name"),
               "idle": bool((idle.get(str(c["did"])) or {}).get("idle")),
               "gait": g.get("gait"),
               "stance": s.get("stance"),
               "posture": ft.get("posture"),
               "pose": g.get("pose"),
               "partial": bool(g.get("partial")),
               "transition": bool(g.get("transition"))}
        row["riding"] = is_riding(row)
        rep, n = dups.get(c["did"], (None, 0))
        # a named member always wins the representative slot over a bare DID
        row["dup_of"] = rep
        row["dup_n"] = n
        out.append(row)
    if motion:
        pred = MOTION_FILTERS.get(motion)
        if pred:
            out = [c for c in out if pred(c)]
    if riding == "hide":
        out = [c for c in out if not c["riding"]]
    elif riding == "only":
        out = [c for c in out if c["riding"]]
    if dups:
        # ONE ROW PER MOTION GROUP. Names do not protect a row from folding.
        #
        # Two earlier versions got this wrong in opposite directions, both from
        # the same false premise — that the names in clip_names.json are all
        # deliberate human identifications. They are not: run_a..run_e,
        # walk_a..walk_d and friends were seeded mechanically from earlier
        # gait identifications, so "a" and "e" are enumeration
        # letters, not a considered statement that the two clips differ.
        # Protecting named rows on that basis left three clips all called
        # "defeat 2h" in the list, which is exactly the reported symptom.
        #
        # Folding run_a with run_e is also right on the measurements: across the
        # whole dwm run/walk family the pair sits at 0.046 while every other
        # pair is 0.087-0.34, i.e. eps 0.05 falls in a genuinely empty gap.
        #
        # The representative prefers a named member so the label survives the
        # fold, then the lowest DID for stability.
        best = {}
        for c in out:
            r = c["dup_of"]
            if r is None:
                continue
            rank = (0 if c["name"] else 1, c["did"])
            if r not in best or rank < best[r][0]:
                best[r] = (rank, c["did"])
        chosen = {v[1] for v in best.values()}
        out = [c for c in out if c["dup_of"] is None or c["did"] in chosen]
    key = lambda c: (c["duration"], c["did"])
    # CLASSIFIED-CLIP QUOTA, ahead of the duration bands.
    # The duration quotas alone are blind to the classifier, and on the 4943-clip
    # pc89 pool that meant 0 of 183 run_fwd? and 4 of 32 walk_fwd? clips reached
    # the 400-row list — a user could not find a walking-forward animation
    # at all. Anything the foot classifier actually identified now gets first
    # claim on the budget, spread evenly over the labels so one populous label
    # (183 run_fwd?) cannot crowd out a sparse one (8 walk_back?).
    sel = []
    named_lab = collections.defaultdict(list)
    for c in out:
        lab = c["gait"] or c["posture"]
        if lab:
            named_lab[lab].append(c)
    if named_lab:
        budget = min(cap // 2, sum(len(v) for v in named_lab.values()))
        per = max(1, budget // len(named_lab))
        for lab in sorted(named_lab):
            named_lab[lab].sort(key=key)
            sel += named_lab[lab][:per]
        sel = sel[:budget]
    picked0 = {c["did"] for c in sel}
    rest = [c for c in out if c["did"] not in picked0]
    room = cap - len(sel)
    loco = sorted([c for c in rest if 1.0 <= c["duration"] <= 2.5], key=key)
    idl = sorted([c for c in rest if 2.5 < c["duration"] <= 10.0], key=key)
    other = sorted([c for c in rest
                    if c["duration"] < 1.0 or c["duration"] > 10.0], key=key)
    qi = min(len(idl), room * 3 // 10)
    ql = min(len(loco), room - qi)
    qi = min(len(idl), room - ql)
    sel += loco[:ql] + idl[:qi]
    if len(sel) < cap:
        sel += other[:cap - len(sel)]
    picked = {c["did"] for c in sel}
    sel += [c for c in out if c["name"] and not c["name"].startswith("auto:")
            and c["did"] not in picked]
    sel.sort(key=lambda c: c["did"])
    return sel

def clip_cached(did, body):
    """Decoded clip JSON {did, duration, frames, fps, tracks} for live track
    swapping in the viewer (no mesh recompose). Cached decoded/clip_<did>.json."""
    did = int(did)
    fp = os.path.join(DECODED, "clip_%d.json" % did)
    if os.path.exists(fp):
        try:
            return json.load(open(fp))
        except ValueError:
            os.remove(fp)          # truncated cache (killed mid-write) — rebuild
    import export_skinned as _es
    rig = RIGS.get(body)
    nb = len(_es.skeleton_bones(rig)) if rig else None
    cj = _es.clip_json(did, nb)
    tmp = fp + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cj, f)
    os.replace(tmp, fp)            # atomic: no truncated caches on kill/restart
    return cj

def default_clip(rig, label):
    """Default clip DID for a body, in priority order:

    1. An EXPLICIT marker — a clip whose user-assigned name contains "default"
       for this rig stem. This outranks every heuristic below, deliberately: a
       user naming a clip "…good default" is a statement of intent, and the
       word-matching underneath cannot be allowed to overrule it. It could not
       even express it — the first such clip was named "walking half strafe
       good default" and the "strafe" exclusion below threw it out.
    2. Else a name containing "walk" (not back/strafe/rev), user-assigned names
       ahead of classifier "auto:*" ones, "fwd" winning within each tier.
    3. Else IDLE_CLIPS. None -> bind pose, static.
    """
    stem = BODY_STEM.get(label)
    explicit = sorted(int(k) for k, e in clip_names().items()
                      if e.get("rig") == stem
                      and "default" in (e.get("name") or "").lower())
    if explicit:
        return explicit[0]
    cands = []
    for k, e in clip_names().items():
        n = (e.get("name") or "").lower()
        if e.get("rig") != stem or "walk" not in n:
            continue
        if any(t in n for t in ("back", "strafe", "rev")):
            continue
        auto = 1 if n.startswith("auto:") else 0
        cands.append((auto, 0 if "fwd" in n else 1, len(n), int(k)))
    if cands:
        return min(cands)[3]
    return IDLE_CLIPS.get(rig)

_catalog = None
def catalog():
    global _catalog
    if _catalog is None:
        _catalog = []
        with open(os.path.join(config.out_dir(), "items_catalog.jsonl")) as f:
            for line in f:
                _catalog.append(json.loads(line))
    return _catalog

def slot_name(v):
    if not v: return None
    names = [n for bit, n in SLOT_BITS.items() if v & bit]
    return "+".join(names) if names else "0x%08X" % v

def _display(name):
    return re.sub(r"\[[a-z]+\]$", "", name or "")

def _bodies_out(bodies):
    return [dict(app=b["app"], species=b["species"], sex=b["sex"],
                 present=b.get("present", False),
                 label="%s-%s" % (SPECIES.get(b["species"], b["species"]),
                                  SEX.get(b["sex"], "?")))
            for b in bodies]

def _row(r):
    return dict(did=r["did"], name=_display(r["name"]), quality=r.get("quality"),
                item_class=r.get("item_class"), level=r.get("level"),
                slot=r.get("slot"), slot_name=slot_name(r.get("slot")),
                clothing_color=r.get("clothing_color"),
                bodies=_bodies_out(r["bodies"]))

def search(q, slot=None, limit=100):
    """Ranked item search. slot: optional slot name ("Legs") to filter by."""
    q = fold((q or "").strip())
    if len(q) < 2: return []
    terms = q.split()
    scored = []
    for r in catalog():
        n = fold(r.get("name"))
        if not all(t in n for t in terms): continue
        if not any(b.get("present") for b in r["bodies"]): continue
        if slot and slot_name(r.get("slot")) != slot: continue
        disp = _display(r["name"])
        d = fold(disp)
        rank = 0 if d == q else (1 if d.startswith(q) else 2)
        scored.append((rank, len(disp), disp, r))
    scored.sort(key=lambda x: x[:3])
    out, seen = [], set()
    for rank, _l, disp, r in scored:
        sig = (disp, r["bodies"][0]["key"] if r["bodies"] else 0)
        if sig in seen: continue
        seen.add(sig)
        out.append(_row(r))
        if len(out) >= limit: break
    return out

# Words that name the piece rather than the set; stripped from the tail when
# deriving a set stem ("Vanguard Blood-sworn's Skirt" -> "Vanguard Blood-sworn's").
_PIECE_WORDS = {"helm", "helmet", "hat", "hood", "cap", "circlet", "mask",
    "shoulders", "pauldrons", "shoulderpads", "mantle", "wings",
    "cloak", "cape", "back",
    "jacket", "tunic", "robe", "dress", "vest", "waistcoat", "chestplate",
    "breastplate", "armour", "armor", "coat", "hauberk", "shirt",
    "gloves", "gauntlets", "bracers", "cuffs",
    "leggings", "trousers", "pants", "skirt", "greaves", "legguards",
    "boots", "shoes", "sandals", "slippers",
    "of", "the", "chain", "scale", "leather", "quilted", "heavy", "light"}

def set_stem(name):
    words = _display(name).split()
    def norm(w):
        w = w.lower()
        for suf in ("'s", "’s"):
            if w.endswith(suf): w = w[:-2]
        return w
    while words and norm(words[-1]) in _PIECE_WORDS:
        words.pop()
    return " ".join(words)

def setmates(did):
    """Items sharing the picked item's set stem, grouped by slot."""
    me = next((r for r in catalog() if r["did"] == did), None)
    if not me: return {}
    stem = set_stem(me["name"])
    if len(stem) < 4: return {}
    out = {}
    for r in catalog():
        if not r["name"] or set_stem(r["name"]) != stem: continue
        if not any(b.get("present") for b in r["bodies"]): continue
        sn = slot_name(r.get("slot"))
        if not sn or "+" in sn: continue
        out.setdefault(sn, [])
        row = _row(r)
        if not any(x["name"] == row["name"] for x in out[sn]):
            out[sn].append(row)
    return dict(stem=stem, slots=out)

# ---- LotroCompanion outfit import -----------------------------------------
# LotroCompanion keeps one directory per character under
#   <base>/data/characters/toon-NNNNN/
# with summary.xml (name/server/class/race/sex) and outfits.xml. The outfit file
# is <outfits currentIndex><outfit index><element slot visible itemId itemName
# colorCode color/>. itemId is the item DID in DECIMAL, and `color` is the dye
# name, which matches dye_colors.json exactly (verified: all 8 names used across
# both local characters resolve). Outfits carry NO name, only an index — the
# label below is built from the chest piece, which is what makes one
# recognisable in a list.
COMPANION_DIRS = [
    os.environ.get("LOTRO_COMPANION_DIR") or "",
    os.path.expanduser("~/.lotrocompanion/data/characters"),
]

# LotroCompanion slot -> our slot. The rest (MAIN_MELEE, RANGED, *_AURA,
# CLASS_ITEM) are weapons/effects with no wearable mesh; they are reported as
# skipped rather than silently dropped.
COMPANION_SLOTS = {"HEAD": "Head", "SHOULDER": "Shoulders", "BREAST": "Chest",
                   "BACK": "Back", "HANDS": "Hands", "LEGS": "Legs",
                   "FEET": "Feet"}
COMPANION_RACE = {"man": "Man", "elf": "Elf", "dwarf": "Dwarf",
                  "hobbit": "Hobbit", "beorning": "Beorning",
                  "high_elf": "HighElf", "stout_axe": "Stoutaxe"}


# Character appearance, extracted from the live client by separate tooling
# (optional appearance_extracted.json; absent -> default styles/colors).
# Per avatar: head/hair/beard STYLE INDEX (which the composer was hardcoding to
# 0, so every character wore the same face), plus colour and texture-slot
# properties. The styles' apr_file DIDs match charparts.CHARGEN exactly
# (verified on two local characters), which is what confirms this indexes the
# same chargen tables the composer already reads.
APPEARANCE_FILES = [
    os.environ.get("LOTRO_APPEARANCE_JSON") or "",
    os.path.join(HERE, "appearance_extracted.json"),
]

_appearance = None
def appearances():
    """{cleaned avatar name: record}. Names carry a "[F]"/"[Fv]" suffix in the
    file; the outfit side knows characters by bare name, so key on both."""
    global _appearance
    if _appearance is not None:
        return _appearance
    _appearance = {}
    for f in APPEARANCE_FILES:
        if not f or not os.path.exists(f):
            continue
        try:
            with open(f) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        for a in d.get("avatars", []):
            n = (a.get("name") or "").strip()
            if not n:
                continue
            _appearance[n] = a
            _appearance[re.sub(r"\s*\[[^]]*\]$", "", n)] = a
        break
    return _appearance


def _rgb255(v):
    """Colour from appearance_extracted.json -> [r,g,b] 0-255. The file has used
    both encodings: raw 0-255 ints originally, normalised 0..1 floats after a
    later extraction fix. Detect rather than assume, so a
    regenerated file cannot silently render as near-black."""
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        return None
    c = list(v)[:3]
    if all(isinstance(x, (int, float)) for x in c) and max(c) <= 1.0:
        return [max(0, min(255, int(round(x * 255)))) for x in c]
    return [max(0, min(255, int(round(x)))) for x in c]


def appearance_colors(name):
    """{'skin': [r,g,b], 'hair': …, 'eyes': …, 'lips': …} for a character."""
    a = appearances().get(name or "")
    if not a:
        return {}
    out = {}
    for key, prop in (("skin", "AC_Color_Skin"), ("hair", "AC_Color_Hair"),
                      ("eyes", "AC_Color_Eyes"), ("lips", "AC_Color_Lips")):
        c = _rgb255(a.get(prop))
        if c:
            out[key] = c
    return out


def appearance_styles(name):
    """{'head': idx, 'hair': idx, 'beard': idx} for a character, or {}."""
    a = appearances().get(name or "")
    if not a:
        return {}
    out = {}
    for slot, v in (a.get("styles") or {}).items():
        if slot in ("head", "hair", "beard") and isinstance(v, dict):
            i = v.get("style_index")
            if isinstance(i, int):
                out[slot] = i
    return out


def companion_base():
    """First candidate that actually HOLDS outfit data. Existence of the
    directory is not enough: the server runs as a different user than the
    player, so `~/.lotrocompanion` resolves to the service account's own empty
    LotroCompanion profile and would shadow the real one."""
    import glob
    for d in COMPANION_DIRS:
        if d and glob.glob(os.path.join(d, "toon-*", "outfits.xml")):
            return d
    return None


def _companion_body(attrib):
    """summary.xml race/sex -> our body label, or None when we have no rig for
    it (Beorning has a species id but no player rig in RIGS)."""
    race = COMPANION_RACE.get((attrib.get("race") or "").lower())
    sex = {"FEMALE": "F", "MALE": "M"}.get(attrib.get("sex") or "")
    if not race or not sex:
        return None
    label = "%s-%s" % (race, sex)
    return label if label in RIGS else None


def companion_toons():
    """[{dir, name, server, class, race, sex, level, body, outfits:[...]}] for
    every character that has an outfits.xml. body is null when we cannot render
    that race/sex — the outfit still loads, onto whatever body is selected."""
    import glob
    import xml.etree.ElementTree as ET
    base = companion_base()
    if not base:
        return []
    out = []
    for d in sorted(glob.glob(os.path.join(base, "toon-*"))):
        fo = os.path.join(d, "outfits.xml")
        if not os.path.exists(fo):
            continue
        a = {}
        fs = os.path.join(d, "summary.xml")
        if os.path.exists(fs):
            try:
                a = ET.parse(fs).getroot().attrib
            except ET.ParseError:
                a = {}
        outfits = []
        try:
            root = ET.parse(fo).getroot()
        except ET.ParseError:
            continue
        for x in root:
            worn = [e for e in x if e.get("itemId")
                    and e.get("slot") in COMPANION_SLOTS]
            chest = next((e.get("itemName") for e in x
                          if e.get("slot") == "BREAST" and e.get("itemName")), None)
            idx = x.get("index")
            outfits.append({
                "index": int(idx) if idx is not None else len(outfits),
                "n": len(worn),
                # no name exists in the file; the chest piece is the handle
                "label": "#%s — %s (%d)" % (idx, chest or "empty", len(worn))
                         if worn else "#%s — empty" % idx,
            })
        out.append({"dir": os.path.basename(d),
                    "name": a.get("name") or os.path.basename(d),
                    "server": a.get("server"), "cls": a.get("class"),
                    "race": a.get("race"), "sex": a.get("sex"),
                    "level": a.get("level"),
                    "body": _companion_body(a),
                    "styles": appearance_styles(a.get("name") or ""),
                    "colors": appearance_colors(a.get("name") or ""),
                    "current": root.get("currentIndex"),
                    "outfits": outfits})
    return out


def companion_outfit(toon, index):
    """One outfit resolved against our catalog:
    {body, slots: {Head: {did, name, dye}, ...}, skipped: [...], missing: [...]}
    skipped = slots we do not render (weapons/auras); missing = wearable slots
    whose itemId is not in the catalog."""
    import xml.etree.ElementTree as ET
    base = companion_base()
    if not base:
        raise ValueError("no LotroCompanion data directory found")
    if not re.fullmatch(r"toon-\d+", toon or ""):
        raise ValueError("bad toon %r" % toon)     # keep the path inside base
    d = os.path.join(base, toon)
    root = ET.parse(os.path.join(d, "outfits.xml")).getroot()
    x = next((o for o in root if o.get("index") == str(index)), None)
    if x is None:
        raise ValueError("no outfit %s for %s" % (index, toon))
    body = None
    fs = os.path.join(d, "summary.xml")
    if os.path.exists(fs):
        try:
            body = _companion_body(ET.parse(fs).getroot().attrib)
        except ET.ParseError:
            pass
    cat = {r["did"]: r for r in catalog()}
    slots, skipped, missing = {}, [], []
    for e in x:
        did = e.get("itemId")
        if not did:
            continue
        slot = COMPANION_SLOTS.get(e.get("slot"))
        if not slot:
            skipped.append({"slot": e.get("slot"), "name": e.get("itemName")})
            continue
        did = int(did)
        row = cat.get(did)
        if not row:
            missing.append({"slot": slot, "did": did, "name": e.get("itemName")})
            continue
        slots[slot] = {"did": did, "name": _display(row["name"]),
                       "companion_name": e.get("itemName"),
                       "dye": e.get("color") or "",
                       # LotroCompanion's own per-slot show/hide toggle
                       "visible": (e.get("visible") or "true") == "true"}
    return {"body": body, "slots": slots, "skipped": skipped, "missing": missing}


def item_row(item_did):
    """One catalog row by DID, in the same shape /search returns. Used to
    restore an outfit from the URL hash without re-running a name search."""
    r = next((x for x in catalog() if x["did"] == item_did), None)
    return _row(r) if r else None


def bodies_for(item_did):
    """Live per-body garment presence for one item (fresh, not catalog-cached)."""
    import selector, wearable2
    row = next((r for r in catalog() if r["did"] == item_did), None)
    if not row: return []
    out, seen, cache = [], set(), {}
    for b in row["bodies"]:
        app = b["app"]
        if app not in cache:
            try:
                cache[app] = wearable2.parse_record(config.general().read_content(app))
            except Exception:
                cache[app] = None
        present = False
        if cache[app]:
            e = next((x for x in wearable2.entries(cache[app]) if x["key"] == b["key"]), None)
            if e:
                parts = e["blocks"][0]["parts"]
                gar = [p for p in parts if p["tag"] == GARMENT_TAG]
                for p in (gar if gar else parts):
                    f = config.mesh_chain().find_file(p["mesh"])
                    if f and f[2] > 2000:
                        present = True; break
        key = (app, b["key"])
        if key in seen: continue
        seen.add(key)
        out.append(dict(app=app, key=b["key"], present=present,
                        label="%s-%s" % (SPECIES.get(b["species"], b["species"]),
                                         SEX.get(b["sex"], "?"))))
    return out

def _dump_atomic(obj, fp):
    """Write a decoded/ cache file atomically. The servers run threaded, so two
    slots can compose the same file concurrently; a half-written file would be
    served to the browser as a truncated-JSON 500 (the failure clip_cached was
    already hardened against)."""
    tmp = "%s.%d.%d.tmp" % (fp, os.getpid(), threading.get_ident())
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, fp)


# Chest-family part tags that govern ANOTHER equipment slot. Same three-way
# mechanism the head family uses for hair (docs/hair-face.md):
#   tag absent    -> that slot's own item renders normally
#   tag = stub    -> that slot is BLANKED (a floor-length dress hides leggings)
#   tag = mesh    -> the chest item SUPPLIES that slot's geometry (hauberks)
# Tag->slot confirmed by where the meshes actually sit on the body, not by
# guesswork: on "Summerdays Tunic and Trousers" the 0x10000006 part spans
# z 0.00-0.10 (ground level = feet), 0x1000000C spans 0.09-1.51 (the whole
# garment), and a Legs item's own mesh spans 0.09-1.09.
# Survey of 172 distinct chest entries: 122 blank the legs, 8 blank the feet
# too (dresses/robes), 1 supplies real leg geometry (hauberk), 49 touch neither.
#
# 0x10000001 = HANDS. I first read its z-range (1.37-1.42, a thin band) as a
# collar; that was wrong, and wrong for an avoidable reason — meshes are in BIND
# pose, which is a T-pose, so the hands sit out at shoulder height in a narrow
# z band. The earlier format notes had it right (chest family: hands = 0x10000001) and the count
# settles it: across 235 Elf-F chest entries there is exactly ONE distinct
# 0x10000001 mesh, so it is a shared per-body part, not garment sleeves.
OVERRIDE_TAGS = {0x10000003: "Legs", 0x10000006: "Feet", 0x10000001: "Hands"}
STUB_BYTES = 2000

# Bare hands, per body. The chest garment supplies the arm skin down to the
# WRIST and the hands come from this separate part — so a garment carrying no
# 0x10000001 (e.g. Dwarf Quilted Waistcoat of Might) left the arms ending in
# stumps. This is the body's own hands mesh, used whenever no worn garment
# supplies them and no Hands item is equipped.
# Derived by surveying every chest entry per body and taking the most common
# NON-STUB 0x10000001; Man/Elf agree unanimously (142/142 entries), Dwarf and
# Hobbit carry a stub in most entries with one real mesh behind it.
HANDS_MESH = {
    "Dwarf-M":  0x060009AC,     # 48580 B, 51/168 entries
    "Elf-F":    0x06010563,     # 32573 B, 142/142
    "Elf-M":    0x060108C0,     # 57259 B, 142/142
    "Hobbit-F": 0x06000984,     # 49635 B, 56/142
    "Hobbit-M": 0x0600097C,     # 57971 B, 53/142
    "Man-F":    0x0600DAB6,     # 54280 B, 142/142
    "Man-M":    0x0600D4B0,     # 54680 B, 142/142
}

# Bare feet, found on the SECOND attempt. Two earlier versions were wrong and
# the reasons are worth keeping, because both looked fine from the inside:
#   1. "most common non-stub 0x10000006 per body" gave a garment's SHOES — that
#      statistic cannot tell a bare foot from footwear many garments share.
#   2. filtering on the skin surface but scanning only CHEST entries concluded
#      no bare feet exist anywhere. They do; they live in the Legs/Feet families.
#
# The test that actually works, and the same one that validates HANDS_MESH: a
# bare body part routes to the body's SKIN surface (the surface its bare-hands
# mesh uses, which matches the independently recorded per-body skin surfaces), footwear
# routes to a cloth surface. Then require foot GEOMETRY — z below ~0.15 rising
# no higher than ~0.30 — so a full leg does not qualify.
# The body's skin surface DID, which is what makes a part BARE. Taken from the
# bare-hands mesh of each body and matching the skin surfaces recorded
# independently in the format docs (Elf-F 0x310092EA, Dwarf-M 0x3100015D).
SKIN_SURFACE = {
    "Dwarf-M":  0x3100015D,
    "Elf-F":    0x310092EA,
    "Elf-M":    0x310092E8,
    "Hobbit-F": 0x3100015C,
    "Hobbit-M": 0x3100015A,
    "Man-F":    0x31008BAE,
    "Man-M":    0x31008BB0,
}

FEET_MESH = {
    "Dwarf-M":  0x0600F039,     # 67077 B, 858 verts, z 0.00..0.26
    "Elf-F":    0x060105AE,     # 30495 B, 430 verts, z -0.01..0.10
    "Hobbit-F": 0x0600EFF8,     # 82358 B, 1078 verts, z 0.00..0.24
    "Hobbit-M": 0x0600F029,     # 80909 B, 1074 verts, z 0.00..0.24
    "Man-F":    0x0600D911,     # 30364 B, 430 verts, z -0.01..0.10
    "Man-M":    0x0600D302,     # 34361 B, 493 verts, z -0.01..0.13
    # Elf-M: no skin-surfaced foot mesh found in the chest/legs/feet families.
    # Left out rather than filled with another body's — that is the mistake
    # this table already made once.
}


def slot_overrides(item_did, app_did):
    """Which OTHER slots this garment blanks or supplies:
    {"Legs": "blank"|"provided", ...}. Empty when it leaves them alone."""
    import selector, wearable2
    try:
        bodies, _ = selector.appearance_map(item_did)
        b = next((x for x in bodies if x["worn_appearance"] == app_did), None)
        if not b:
            return {}
        rec = wearable2.parse_record(config.general().read_content(app_did))
        e = next((x for x in wearable2.entries(rec) if x["key"] == b["key"]), None)
        if not e:
            return {}
        out = {}
        for p in e["blocks"][0]["parts"]:
            slot = OVERRIDE_TAGS.get(p["tag"])
            if not slot:
                continue
            f = config.mesh_chain().find_file(p["mesh"])
            if not f:
                continue
            out[slot] = "blank" if f[2] < STUB_BYTES else "provided"
        return out
    except Exception:
        return {}


def compose_cached(item_did, app_did):
    """Compose (or reuse cached) and return the decoded/ file name."""
    name = "compose_%08X_%08X" % (item_did, app_did)
    fp = os.path.join(DECODED, name + ".json")
    if not os.path.exists(fp):
        import compose as _c
        _c.compose(item_did, app_did, name)
    return name + ".json"

def compose_skinned(item_did, app_did):
    """Compose with per-vertex skin data + skeleton; returns the decoded/ file
    name (cached). Output = compose() JSON plus flat skinIndices/skinWeights
    (4 per vertex), rig, bones [{name,parent,t,q,s}], and "clip_did" = the
    body's default clip (user-named "walk" clip, else IDLE_CLIPS; null ->
    viewer shows bind pose, static).

    The clip PAYLOAD is deliberately NOT embedded: it is identical for every
    slot of an outfit, so embedding made ~80% of a 7-slot set's ~8 MB download
    seven redundant copies of the same ~0.9 MB clip. The viewer fetches it once
    from /clip. Geometry is clip-independent, so the clip DID is also out of
    the cache name — one composed mesh now serves every clip instead of being
    re-composed per default-clip change."""
    import selector
    import compose as _c
    import export_skinned as _es
    bodies, _ = selector.appearance_map(item_did)
    b = next((x for x in bodies if x["worn_appearance"] == app_did), None)
    if not b:
        raise ValueError("app 0x%08X not a body of item 0x%08X"
                         % (app_did, item_did))
    label = "%s-%s" % (SPECIES.get(b["species"], b["species"]),
                       SEX.get(b["sex"], "?"))
    rig = RIGS.get(label)
    if not rig:
        raise ValueError("no rig known for body %s" % label)
    name = "animS_%08X_%08X" % (item_did, app_did)
    fp = os.path.join(DECODED, name + ".json")
    if not os.path.exists(fp):
        bones = _es.skeleton_bones(rig)
        out = _c.compose(item_did, app_did, name,
                         skin_bones=len(bones), write=False)
        out["rig"] = "0x%08X" % rig
        out["body"] = label
        out["bones"] = bones
        _dump_atomic(out, fp)
    # clip_did is resolved per REQUEST, not baked into the cached geometry, so
    # renaming the default clip takes effect with no recompose.
    return name + ".json", default_clip(rig, label)

# body label -> charparts.CHARGEN key (chargen head/hair/beard APR files)
CHARGEN_KEY = {"Man-M": "man_m", "Man-F": "man_f", "Elf-M": "elf_m",
               "Elf-F": "elf_f", "Dwarf-M": "dwarf_m",
               "Hobbit-M": "hobbit_m", "Hobbit-F": "hobbit_f"}

def _hair_decision(head_item, head_app):
    """What hair the avatar shows given the equipped Head item, per the
    docs/hair-face.md rule (the item's 0x20 hair-slot part answers):
      part absent      -> ("chargen", None)  chargen hairstyle shows
      part = stub      -> ("none", None)     helmet hides hair
      part = real mesh -> ("fitted", did)    the item's fitted hair mesh."""
    if not head_item:
        return ("chargen", None)
    import selector, wearable2
    HAIR_TAG = 0x1000000E
    try:
        bodies, _ = selector.appearance_map(head_item)
        b = next((x for x in bodies if x["worn_appearance"] == head_app), None)
        if not b:
            return ("chargen", None)
        rec = wearable2.parse_record(config.general().read_content(head_app))
        entry = next((e for e in wearable2.entries(rec)
                      if e["key"] == b["key"]), None)
        if not entry:
            return ("chargen", None)
        parts = entry["blocks"][0]["parts"]
        hp = next((p for p in parts if p["tag"] == HAIR_TAG), None)
        if hp is None:
            return ("chargen", None)
        f = config.mesh_chain().find_file(hp["mesh"])
        if f and f[2] > 2000:
            return ("fitted", hp["mesh"])
        return ("none", None)
    except Exception:
        return ("chargen", None)

def compose_face(label, head_item=None, head_app=None, hands=False, feet=False,
                 head_style=0, hair_style=0):
    """Skinned default head (face) + hair for a body: chargen head style 0,
    plus hair (chargen style 0, the head item's fitted hair mesh, or nothing —
    _hair_decision), skinned to the body's rig with bones, same JSON shape as
    compose_skinned (clip payload NOT embedded — see there). Cached
    decoded/animF_*.json; returns (file name, default clip DID)."""
    import charparts
    import export_skinned as _es
    body = CHARGEN_KEY.get(label)
    rig = RIGS.get(label)
    if not body or not rig:
        raise ValueError("no chargen/rig known for body %r" % label)
    mode, fitted = _hair_decision(head_item, head_app)
    name = "animF_%s_h%d_%d_%s%s" % (body, head_style, hair_style,
                                     mode if mode != "fitted"
                                     else "f%08X" % fitted,
                                     ("_hands" if hands else "") +
                                     ("_feet" if feet else ""))
    fp = os.path.join(DECODED, name + ".json")
    if not os.path.exists(fp):
        bones = _es.skeleton_bones(rig)
        out = dict(id=name, vertices=[], normals=[], uvs=[], triangles=[],
                   groups=[], skinIndices=[], skinWeights=[])
        def _tag(part, n0):
            for g in out["groups"][n0:]:
                g["part"] = part          # viewer: alpha-cutout hair only
        n0 = len(out["groups"])
        charparts.append_style(out, body, "head", head_style,
                               skin_bones=len(bones),
                               rigid_bone=charparts.head_bone(bones))
        _tag("head", n0)
        n0 = len(out["groups"])
        if mode == "chargen":
            charparts.append_style(out, body, "hair", hair_style,
                                   skin_bones=len(bones),
                                   rigid_bone=charparts.head_bone(bones))
        elif mode == "fitted":
            charparts._append_mesh(out, fitted, skin_bones=len(bones),
                                   rigid_bone=charparts.head_bone(bones))
        _tag("hair", n0)
        if hands and HANDS_MESH.get(label):
            n0 = len(out["groups"])
            charparts._append_mesh(out, HANDS_MESH[label], skin_bones=len(bones))
            _tag("hands", n0)
        if feet and FEET_MESH.get(label):
            n0 = len(out["groups"])
            charparts._append_mesh(out, FEET_MESH[label], skin_bones=len(bones),
                                   only_surface=SKIN_SURFACE.get(label))
            _tag("feet", n0)
        charparts._sanitize(out)
        out["num_submeshes"] = len(out["groups"])
        out["rig"] = "0x%08X" % rig
        out["body"] = label
        out["bones"] = bones
        _dump_atomic(out, fp)
    return name + ".json", default_clip(rig, label)

def dyes():
    with open(os.path.join(HERE, "dye_colors.json")) as f:
        return json.load(f)

def skin_tinted_texture(tex_did, rgb):
    """Bake a head/body texture tinted by a character's skin colour.

    Uses the game's OWN convention, the same one dyed_texture relies on for
    garments: alpha is a TINT MASK, not opacity — low-alpha pixels take the
    colour, high-alpha pixels keep what the artist authored. Measured on the
    Elf-F head texture 0x4125A714: the face centre is 0%% high-alpha (entirely
    tintable, and near-white so it is clearly authored as a neutral skin map),
    while the eye and eyebrow patches in the same atlas are 45%% and 38%%
    high-alpha and so survive the tint. That is what lets one texture carry a
    skin-tintable face plus features that must not be tinted.
    """
    name = "skin_%08X_%02X%02X%02X.png" % (tex_did, rgb[0], rgb[1], rgb[2])
    fp = os.path.join(TEXTURES, name)
    if not os.path.exists(fp):
        from PIL import Image
        import tex_extract as tx
        im = Image.open(tx.extract_texture(tex_did)).convert("RGBA")
        px = im.load(); w, h = im.size
        fr, fg, fb = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a < 128:
                    px[x, y] = (int(r * fr), int(g * fg), int(b * fb), a)
        tmp = "%s.%d.%d.tmp" % (fp, os.getpid(), threading.get_ident())      # atomic; served concurrently
        im.convert("RGB").save(tmp, format="PNG")
        os.replace(tmp, fp)
    return name


def dyed_texture(tex_did, dye):
    """Bake (or reuse) the mask-aware dyed texture; returns the textures/ file name."""
    name = "dyed_%08X_%s.png" % (tex_did, "".join(c for c in dye if c.isalnum()))
    fp = os.path.join(TEXTURES, name)
    if not os.path.exists(fp):
        from PIL import Image
        rgb = dyes()[dye]["rgb"]
        import tex_extract as tx
        im = Image.open(tx.extract_texture(tex_did)).convert("RGBA")
        px = im.load(); w, h = im.size
        fr, fg, fb = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a < 128:
                    px[x, y] = (int(r * fr), int(g * fg), int(b * fb), a)
        tmp = "%s.%d.%d.tmp" % (fp, os.getpid(), threading.get_ident())    # atomic; served concurrently
        im.convert("RGB").save(tmp, format="PNG")
        os.replace(tmp, fp)
    return name

def list_meshes():
    import glob
    out = []
    for p in sorted(glob.glob(os.path.join(DECODED, "*.json"))):
        try:
            d = json.load(open(p))
            out.append({"id": d.get("id", os.path.basename(p)[:-5]),
                        "file": os.path.basename(p),
                        "verts": len(d.get("vertices", [])),
                        "tris": len(d.get("triangles", []))})
        except Exception as e:
            out.append({"id": os.path.basename(p), "file": os.path.basename(p),
                        "error": str(e)})
    return out


# ---- armour weight (Item_EquipmentCategory bits; lotro-data EquipmentCategory
#      cross-mapped to ArmourType labels: 18=LIGHT, 9=MEDIUM, 10=HEAVY) ----
def weight_of(equip_cat):
    if not equip_cat: return None
    if equip_cat & (1 << 10): return "heavy"
    if equip_cat & (1 << 9): return "medium"
    if equip_cat & (1 << 18): return "light"
    return None

def _stems(name):
    """Candidate set stems for an item name: prefix stem (piece words stripped
    from the tail) and the 'of <something>' suffix."""
    disp = _display(name)
    out = []
    st = set_stem(disp)
    if len(st) >= 4: out.append(st)
    low = disp.lower()
    i = low.find(" of ")
    if i > 0:
        suf = disp[i + 1:]          # "of the Gloaming"
        if len(suf) >= 7: out.append(suf)
    return out

_set_index = None
def sets_index():
    """{stem: {"slots": {slot: [row..]}, "weights": {..counts}}} for stems
    covering >= 3 distinct single-bit slots."""
    global _set_index
    if _set_index is not None: return _set_index
    acc = {}
    for r in catalog():
        if not r.get("name"): continue
        if not any(b.get("present") for b in r["bodies"]): continue
        sn = slot_name(r.get("slot"))
        if not sn or "+" in sn: continue
        for stem in _stems(r["name"]):
            e = acc.setdefault(stem, {"slots": {}, "weights": {}})
            rows = e["slots"].setdefault(sn, [])
            row = _row(r)
            row["weight"] = weight_of(r.get("equip_cat"))
            if not any(x["name"] == row["name"] for x in rows):
                rows.append(row)
            w = row["weight"]
            if w: e["weights"][w] = e["weights"].get(w, 0) + 1
    _set_index = {stem: e for stem, e in acc.items() if len(e["slots"]) >= 3}
    return _set_index

def search_sets(q, weight=None, limit=30):
    q = fold((q or "").strip())
    terms = q.split()
    out = []
    for stem, e in sets_index().items():
        if terms and not all(t in fold(stem) for t in terms): continue
        wts = e["weights"]
        w = max(wts, key=wts.get) if wts else None
        if weight and w != weight: continue
        out.append(dict(stem=stem, weight=w, nslots=len(e["slots"]),
                        slots=e["slots"]))
    out.sort(key=lambda x: (-x["nslots"], len(x["stem"]), x["stem"]))
    return out[:limit]
