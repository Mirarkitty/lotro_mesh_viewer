# Animation: Skeletons, Clips, and Skinning

Where the client's rigs and motion data live, how a mesh vertex binds to a
skeleton bone, and how a real walk cycle was identified among hundreds of
unnamed locomotion clips. This is the most recently completed layer of the
pipeline — a full posed, animated render exists end-to-end (skeleton export,
a from-scratch Havok clip decoder, and skin weights for every vertex stride
seen so far), though specific gaps remain (see the end of this page and
[limitations.md](limitations.md)).

## Two record types

| Type | Archive | Approx. count | What it is |
|---|---|---|---|
| `0x05` | `client_anim.dat` | ~25,400 | **Animation clips only** — no skeleton data |
| `0x04` | `client_general.dat` | ~28,500 | **Havok skeletons** (`hkaSkeleton`/`hkaBone`) |

An exhaustive scan of every `0x05` record (not a sample) found:

- **~24,800** `hkaSplineCompressedAnimation` clips (spline-compressed
  transform tracks)
- **~600** `hkaInterleavedUncompressedAnimation` clips (uncompressed
  per-frame tracks — tagfile structure parses, but track extraction is not
  implemented; see "Open" below)
- a handful of non-animation records with a distinct type marker, ignored

Every clip is a Havok binary **tagfile** (not a packfile — see "Container
format" below) exposing `duration`, `numberOfTransformTracks`,
`numberOfFloatTracks`, frame count, block duration, and per-track
annotation data.

**Clips are not anonymous.** Most clips carry per-track bone names in their
annotation tracks — e.g. a track named for a specific hip-girdle bone on a
specific monster rig — which directly identifies the rig a clip targets, at
least where annotations are present (a minority of clips, notably some dance
emotes, ship no annotations).

⚠️ **No `hkaAnimationBinding`, `hkaAnimationContainer`, `hkaSkeletonMapper`,
or `hkaMeshBinding` record exists anywhere in the archive** (checked across
every `0x05` record). Havok's normal clip-to-skeleton binding layer is
simply not shipped in this data. The binding from a clip to the rig it
animates has to come from decode-time evidence instead — track count
matched against a candidate skeleton's bone count, corroborated by
annotation bone names where present (see "Clip-to-rig binding" below).

## Skeletons

`0x04` records are `hkaSkeleton` + `hkaBone` structures in the same Havok
tagfile container as clips, with `parentIndices`, a `referencePose` (bind
pose — used to derive inverse-bind matrices), `lockTranslation`,
`boneIndex`, and `autobone` fields. Record sizes range from a few hundred
bytes (the vast majority — tiny one/two-bone prop rigs) up to roughly 25 KB
for the largest character rigs; real character rigs are the few-kilobyte
records, easily distinguished from the mass of tiny prop rigs by size alone.

Bone names, where present, are **plaintext ASCII**. Observed naming-family
prefixes: avatar (player-character rigs), NPC, monster, static/destructible
props, scenery/scripted objects, and effects rigs.

### Character rig table

| Rig | Bind-pose bones | Notes |
|---|---|---|
| Hobbit male | 62 | |
| Hobbit female | 62 | |
| Dwarf male | 62 | |
| Human male / female (NPC-labeled) | 36 | also exist as separate lower-detail NPC variants |
| Beorning | ~38+ | has tail and paw bones — the bear form |
| Man male / female | 89 | **unprefixed** bone names (see below) |
| Elf male / female | 89 | **unprefixed** bone names (see below) |

⚠️ **A naive bone count from named-bone scanning undercounts badly.**
Scanning only for bones named directly under a rig's stem (e.g.
`<stem>_<bonename>`) misses chain bones that live under their own
sub-stems (cape and skirt physics chains in particular). The true bone
count for the "62-bone" rigs above, read from the actual `hkaSkeleton`
structure rather than inferred from name-token matches, is 62 — not the
smaller figure an earlier name-token-only scan produced. **Use the parsed
skeleton's actual bone array length, never a name-token count, when sizing
a bone/weight array for skinning.**

### The "missing" Elf/Man player rigs

Four 89-bone player-grade rigs — full physics chains (cape, skirt), weapon
attach points, and detailed face bones — carry **unprefixed** bone names
(plain names like "parent", "root", "hipgirdle" with no race stem), which is
why an initial name-token scan concluded no elf or human player rig existed
at all. They were found by bind-pose height instead: two clearly distinct
height pairs among the unprefixed 89-bone rigs correspond to (male, female)
pairs for Man and Elf respectively. The specific Man-vs-Elf assignment
between the two pairs is inferred from DID ordering relative to the named
rigs, not yet confirmed against mesh proportions — treat it as
provisional.

### Bone vocabulary

Representative bone names seen across character rigs: `root`, `parent`,
`ground`, `hipgirdle`, `lowerback`, `midback`, `lumbarback`, `rootback`,
`thorax`, `neck`, `head`, `jaw`, `leye`/`reye`, `blinky`, `brow`, `cheek`,
`lcollar`/`lscapula`, `lshoulder`, `lelbow`, `lulna`, `lwrist`,
`lweapon`/`rweapon`, `lfemur`, `lknee`, `lankle`, `lfoot`/`lball`, `ltoe`
(mirrored on the right side).

Two details matter for a renderer:

1. **`lweapon`/`rweapon` are weapon attach points** — where cosmetic
   weapons hang.
2. **Dynamic cloth bones exist**: cape chains (base/mid/tip per side),
   skirt-front chains, and a dedicated `physics` bone. This is how cloaks
   and dress skirts move in-game, and it means a purely static bind-pose
   render will never look correct for a long garment, however accurate the
   mesh and texture are — the cloth needs either authored secondary motion
   from clips or a runtime physics simulation on top (unconfirmed which,
   see "Open" below).

The face rig is minimal (jaw, eyes, brow, cheek, a blink bone) —
expression is mostly texture/morph driven, not bone driven.

## Container format: Havok binary tagfile

Both `0x05` clips and `0x04` skeletons are Havok binary **tagfiles**, not
packfiles. (An earlier reading of the magic bytes as `0x11FACECA` was a
misread of the tagfile magic `0xCAB00D1E 0xD011FACE`, little-endian bytes
`1e0db0ca cefa11d0`.) Layout of a `0x05` record:

```
off 0   float32  duration            (== hkaAnimation.duration)
off 4   uint32   numberOfTransformTracks
off 8   uint8    0x00
off 9   uint32   tagfile size + 1
off 13  tagfile  magic, then tag stream (tagfile version 3, no hk version string)
```

The tagfile is fully self-describing — `TAG_METADATA` entries carry class
name, parent class, and typed member lists — so a generic tagfile parser
reads both clips and skeletons without any hardcoded Havok class layout.
Wire-format elements: varints (bit 0 = sign, then 6+7+7... payload bits),
pooled strings, per-object member-presence bitmaps, and struct arrays stored
struct-of-arrays. The tagfile parsing approach used here was ported from the
open-source [`exyorha/hkxparse`](https://github.com/exyorha/hkxparse)
`HKXTagfileParser`; tag IDs come from the Havok SDK's
`hkBinaryTagfileCommon.h` (an SDK build era consistent with tagfile version
3, i.e. Havok roughly version 6.x).

## `hkaSplineCompressedAnimation` payload

Representative field values from one decoded clip: `duration=1.0`,
`numberOfTransformTracks=44`, `numFrames=61`, `numBlocks=1`,
`maxFramesPerBlock=256`, a mask-and-quantization-size field equal to
`4 x tracks`, a block duration, a block inverse-duration, a per-frame
duration of `1/60`, a `blockOffsets` array, a `floatBlockOffsets` array, and
a compressed data payload.

Per block, per track: a 4-byte mask (quantization type and static/spline
flag per position/rotation/scale channel), then per channel either one or
more static float values, or a NURBS spline: a 3-byte header
(`u16 numItems, u8 degree` — **3 bytes, not 4**, a specific porting trap to
watch for), a knot-vector byte array, and quantized control points.
Position and scale channels are 8- or 16-bit values normalized within a
per-component float min/max range; rotation channels are quantized
quaternions at 32-, 40-, or 48-bit precision — **every clip observed so far
uses 40-bit** (three 12-bit components at a fixed scale factor, a 2-bit
missing-component index, and a 1-bit sign). Decompression logic follows
[`PredatorCZ/HavokLib`](https://github.com/PredatorCZ/HavokLib)'s
`hka_spline_decompressor.cpp`; block/time mapping follows the Havok SDK's
`hkaSplineCompressedAnimation::getBlockAndTime`.

**Validation**: decoded quaternion control points are exactly unit length;
decoded translations are bounded to plausible in-world magnitudes (well
under, e.g., two meters); decoded scales are uniformly 1.0; duration and
track counts match the record's own header fields. This numeric validation
was done before anything was rendered, and later confirmed by an actual
posed render (see "First posed render" below) — consistent with this
project's general rule that numeric checks alone are not sufficient
evidence (see [limitations.md](limitations.md)).

## Clip-to-rig binding

With no explicit binding record shipped, binding is established by:

1. **Track count against skeleton bone count.** Where testable, a clip's
   track count exactly equals its target rig's bone count (e.g. an
   NPC-rig clip with 36 tracks against a 36-bone NPC skeleton; a
   player-rig dance clip with 62 tracks against a 62-bone player
   skeleton). This means the binding, once the right rig is identified, is
   **positional** — track *i* drives bone *i* of the `hkaSkeleton.bones`
   array in order.
2. **Annotation bone names**, where present, directly confirm the target
   rig and the per-track ordering.

## Skinning: how mesh vertices bind to bones

Animation is a layer applied on top of the already-resolved mesh set from
[wardrobe.md](wardrobe.md) — it never changes *which* meshes are drawn, only
*how* they move:

```
  WHAT to draw                          HOW to move it
  ------------                          --------------
  item + (species,sex)                  0x04 skeleton   (bone hierarchy + bind pose)
    -> 0x20 record  (wardrobe.md)          ^                    ^
    -> per-slot part meshes (0x06)         | (direct index)     | (per track)
    -> fold over base body's slots         |                    |
         |                                 |                    |
         +---> skinned mesh -------------- +          0x05 clip (per-bone transforms)
                 per-vertex: bone indices + weights
```

Per frame, for each vertex: `pos' = sum_i( w_i * M[boneIndex_i] * pos )`,
where `M` is the standard skinning matrix (animated bone transform composed
with the inverse bind pose). This is ordinary GPU-style linear-blend
skinning — nothing exotic about the math once the bone indices and weights
are correctly extracted.

**Bone indices index the skeleton array directly — there is no separate
bone-palette indirection layer to resolve.** This was confirmed by checking
that decoded bone indices for a given body's meshes never exceed that body's
own skeleton's bone count, across multiple body types. (A per-submesh
"bone palette" region does exist in the mesh format after the vertex block
— see [mesh-format.md](mesh-format.md) — but parsing it turned out not to be
necessary for correct skinning.)

### Skin-weight vertex-stride table

The skinned-vertex tail (everything after the UV at byte offset 24 — see
[mesh-format.md](mesh-format.md)) packs a tangent frame followed by bone
indices and weights. The exact layout differs by total stride, and — this
is the important part — **the position of the index/weight data within the
tail is not a simple linear function of stride**; each stride has its own
fixed layout, derived and separately validated:

| stride (bytes) | influences | index offset | weight offset | typical use |
|---|---|---|---|---|
| 61 | 1 | 56 | 57 | rigid attachment (caps, packs) expressed as 1-bone skin |
| 65 | 1 | 60 | 61 | rigid hair attachment |
| 66 | 2 | 56 | 58 | genuinely 2-bone skinned (boots, greaves) |
| 70 | 2 | 60 | 62 | 2-bone hair (head/neck/ear) |
| 71 | 3 | 56 | 59 | garments, helms |
| 73 | 1 | 68 | 69 | rigid helm attachment |
| 74 | 2 | 64 | 66 | armour garments, boots |
| 75 | 3 | 60 | 63 | cloaks (skinned to the cape bone chain — cloaks genuinely swing with clip cape tracks, not rigid) |
| 76 | 4 | 56 | 60 | garments (the most common skinned-garment stride) |
| 79 | 3 | 64 | 67 | armour garments, boots |
| 80 | 4 | 60 | 64 | hooded cloaks: cape chain plus head/neck |
| 84 | 4 | 64 | 68 | armour garments |

Bone indices are packed as `u8` per influence; weights as `f32` per
influence, summing to 1.0 across the influences for a given vertex.

This table was built by exhaustively probing candidate `(influence_count,
index_offset, weight_offset)` triples per stride and accepting the one
where weights sum to 1.0 on effectively 100% of vertices across a large
mesh sample (hundreds of items, over a thousand meshes), with **zero
unknown strides** left in that survey once all rows above were added.

⚠️ **A false-positive trap in stride derivation**: a candidate offset that
happens to land on a zero byte followed by a `1.0f` float inside the
tangent-frame region will pass the "weights sum to 1.0" test while actually
reading garbage — every vertex will appear to be rigidly bound to bone 0.
Reject any candidate whose resulting bone-index histogram is *entirely* bone
0 (the root/parent bone); real skin data for a legitimately rigid part still
shows a sensible, consistent (non-root) bone across its vertices. Any
"vertices with bad/absent skin data, falling back to bone 0" warning from a
compositor should be treated as **a missing stride-table entry**, not as a
genuinely unskinned mesh — no genuinely unskinned worn-garment stride has
been observed in this project's data.

## First posed, animated render

The full pipeline — decoded skeleton, decoded clip, per-vertex skin
weights — was combined into a first working posed/animated render: a
garment mesh driven by a decoded walk clip on its body's skeleton,
rendered as a GPU-skinned mesh and confirmed by screenshot at multiple
points in the gait cycle (full stride, passing pose, mirrored stride,
mirrored passing pose — torso upright, arms swinging naturally throughout).

Three follow-up bugs, all with the same root cause, were found and fixed
after this first render: entire equipment slots (feet, head, back) appeared
frozen in bind pose during otherwise-correct walking animation. In every
case the mesh **was** correctly skinned — the actual bug was a missing
stride-table entry (the three-additional-stride and later six-additional-
stride rows in the table above), which silently fell back to rigid
attachment at the root bone (which barely moves during a walk cycle,
producing the "frozen" symptom). This is recorded here because it is a
specific, reproducible instance of a broader pattern: an apparently
mesh-level rendering bug turning out to be an incomplete lookup table
entry, not a decode failure.

## Identifying the real walk cycle

Locomotion clips are **not named** in the clip data itself (see "Emotes and
names" below) — hundreds of similar-duration clips exist per rig with no
label distinguishing "walk" from "run" from an unrelated locomotion-length
emote. A naive discriminator (e.g. hip/thigh rotation amplitude alone)
fails badly, because crouches, tumbles, and bows also produce large
amplitude and dominate a simple ranking.

**What works**: forward-kinematic each candidate clip through its
skeleton per frame, and score by **anti-phase ankle oscillation along the
character's horizontal movement axis, combined with the head staying
roughly level** (a clip where the head drops sharply is disqualified).
Concretely, the identifying metric that separates a genuine walk cycle
from everything else is **ground-contact duty factor**: track each foot's
height per frame (using the lower of ankle and toe height, since toe
height matters — the ankle stays elevated during toe-off, and a pure-ankle
test mislabels ground contact), and classify a foot as grounded when its
height sits within roughly a quarter of that clip's own vertical range
above its per-clip minimum.

The single strongest discriminator turned out to be **double-float
percentage** — the fraction of the cycle where *both* feet are off the
ground simultaneously. A genuine walk essentially never has a double-float
phase; a run always does. In one worked comparison: the correctly-identified
walk clip showed roughly balanced left/right ground-contact duty near 0.49
each with only a 6% double-float fraction (itself mostly a threshold
artifact at contact transitions), and modest stride amplitude; several
candidate "run" clips at a similar overall duration showed markedly lower
per-foot duty (0.23–0.40), a much larger double-float fraction (30–53%),
and roughly double the stride amplitude.

⚠️ **Filtering on duty factor alone is a trap.** Several seated/idle emote
clips (torso and arms moving while both feet stay planted) score a very
high duty factor (0.8–0.9) despite being nothing like locomotion. A robust
classifier requires alternating ground-contact episodes (at least two per
foot, with left/right counts within one of each other), roughly balanced
left/right duty, and non-trivial stride amplitude — not duty factor in
isolation.

This gait classification method is **heuristic and confidence-scored**, not
verified ground truth — see [limitations.md](limitations.md). Only one
rig's walk/run family has been directly confirmed by human eye; other
rigs' canonical walk/run clips are extended from the same method without
independent visual confirmation yet.

## Emotes and animation-state names

Typed emote commands (e.g. a `/handstand`-style command) are defined as
string records in the per-language string-table archive, in **UTF-16LE**
encoding (a plain-ASCII search over this archive finds nothing, which cost
real time before the encoding was identified). Each emote record follows a
fixed schema of self-message / tooltip / command-word / third-person /
targeted-variant strings. Scanning for a fixed tooltip marker phrase across
that archive's string records recovers roughly 225 distinct emote records
(224 distinct command names), including several race-specific dance
variants.

**Linking an emote to a clip**: reusing an emote string record's serial
number as a different record type's DID (a "serial-pairing" heuristic, not
a documented mechanism) resolves a `0x05` clip reference for a minority of
emotes. This heuristic resolves roughly 92 of 224 emotes, and of those, most
resolved clips target **monster** rigs rather than player rigs — either the
pairing itself is wrong for the majority of emotes, or those particular
records are specifically the monster/NPC-performed variant of an emote that
also has a separate, not-yet-found player-targeted record. Treat this
heuristic as unreliable beyond the specific entries it was spot-checked
against; a proper typed parse of the referencing record type (rather than
the serial-number coincidence) has not been done.

Separately, a large animation-state name table (roughly 980 plain-ASCII
names) exists, covering locomotion states (`walk_fwd`, `walk_back`,
strafing/turning variants), combat stances per weapon type, and a long tail
of situational idles (reading, sleeping, chopping a tree, etc.) — confirming
that named concepts like "standing" and "walking" do exist in the client
data as a vocabulary. However, that table carries only a small number of
direct clip references against its ~980 names, so it functions as a
**name/state vocabulary**, not a name-to-clip lookup table. The mapping from
a specific named state like `walk_fwd` to the specific clip DID that plays
for it has not been found through this table — the walk *clip itself* was
instead identified independently by the gait-analysis method above, without
relying on this name table at all.

## Open

- `hkaInterleavedUncompressedAnimation` clips (a substantial minority of all
  clips) are not decoded — the tagfile structure parses, but per-frame
  track extraction is unimplemented.
- Quaternion quantization paths other than the 40-bit rotation / 16-bit
  position combination used by every clip examined so far (32-bit and
  48-bit rotation, 8-bit vector quantization) are implemented but never
  exercised against a real clip.
- Float tracks (a separate track type from transform tracks) are skipped
  entirely.
- The per-submesh bone-palette region in the mesh format (distinct from the
  per-vertex bone indices) still does not parse reliably on all blocks —
  not currently load-bearing, since bone indices index the skeleton
  directly, but a genuine unresolved format detail.
- Clip-to-rig binding for non-player/non-player-adjacent rigs (distinct
  monster skeletons) is unconfirmed in general, beyond the specific cases
  spot-checked above.
- Whether cape/skirt "physics" bones receive authored secondary motion
  directly from clip data, or require a runtime physics simulation layered
  on top of clip playback, is unconfirmed — a bind-pose-stiff cloak or
  skirt remains possible even with entirely correct clip playback, pending
  this answer.
- Gait/pose labeling beyond the one directly-verified rig's walk/run family
  is a confidence-scored heuristic, not independently confirmed ground
  truth.

## See also
- [mesh-format.md](mesh-format.md) — skin weights and the bone-palette region in the vertex format
- [wardrobe.md](wardrobe.md) — mesh *selection*, a separate layer from animation
- [hair-face.md](hair-face.md) — skinning a composed head/hair/beard set
- [dat-format.md](dat-format.md) — the `0x04`/`0x05` DID type map
- [limitations.md](limitations.md) — the overclaiming failure mode, and rigging's specific open gaps
