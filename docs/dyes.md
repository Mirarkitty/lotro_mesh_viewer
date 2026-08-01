# Dye System

How LOTRO armor dyeing works, reverse-engineered enough to render a
correctly-dyed (or correctly un-dyed) garment. Source data referenced below:
the public `lotro-data` project's `lore/colors.xml`
([LotroCompanion/lotro-data](https://github.com/LotroCompanion/lotro-data)).
Tooling: dye reconstruction and wiki-scraping scripts in this project's
tooling set.

## The dye model is a single scalar "floatCode"

The public dye-color data table has 46 entries: 45 named dyes plus a
`Unique` sentinel at `floatCode 99.0` meaning "not dyeable, fixed
appearance." Each dye is stored as **one scalar** — `floatCode`, ranging
roughly 0.01–0.80 — **not an RGB triple**. This confirms the dye system is
literally single-channel: the float is an index into a color palette or a
shader tint parameter, evaluated at render time. It is the direct LOTRO
analog of Asheron's Call's `CloSubPalEffect` dye mechanism.

The 45 named dyes span crafted/quest dyes (e.g. Red 0.40, Umber 0.50, Navy
0.10, Olive 0.25, Black 0.55, White 0.60, Yellow 0.12) and festival/store
dyes (e.g. Lórien Gold 0.23, Belegaer Blue 0.22, Moria Silver 0.21,
Twilight Purple 0.34).

## Render model: gray-base × dye-color

The dyeable region of a garment diffuse texture is stored **near-grayscale**
— a genuinely desaturated dye-ready base, not pre-colored cloth. The render
model is:

```
final.rgb = alpha < 128 ? tex.rgb * dye_RGB : tex.rgb
```

⚠️ An earlier, cruder version of this model applied the multiply to the
*entire* cloth surface uniformly, based on a whole-image saturation
measurement. That measurement was later flagged as suspect: the texture
atlas examined held small, genuinely saturated (brown) cloth pieces on a
large neutral-gray *empty* background, so a whole-image mean saturation
number was dominated by unused atlas space rather than by the dyeable cloth
itself. The underlying gray-base × dye-color model held up, but that
specific supporting measurement did not — a reminder to measure over the
UV-covered texels only, not the whole image, when characterizing a texture
atlas.

### The dye mask is the diffuse alpha channel

The correct per-pixel dye mask was eventually identified directly: garment
diffuse textures are stored as RGBA, and **the alpha channel is the dye
mask**. Two independently-checked dress atlases were both strongly bimodal
in alpha (roughly 40–55% low, 45–55% high, a small midtone fraction) — alpha
LOW marks dyeable cloth, alpha HIGH marks fixed-color detail (trim,
knotwork, embroidery lines were consistently white/high in the alpha
channel). An earlier texture pipeline that converted straight to RGB
discarded this channel entirely, which is why the uniform-tint model above
was needed as an interim approximation.

The render rule above (`alpha < 128 ? tinted : original`) was visually
verified: dyeing a garment navy tints the bodice while the gold waist band
and knotwork trim correctly keep their baked color.

Non-dyeable "fixed trim" regions (leather panels, metal trim, etc.) keep
their baked color regardless of dye choice — a correct **un-dyed** render is
gray-body-plus-fixed-color-trim, not a flat single-hue look.

Shader material chains for dyeable garments carry a color/dye texture
sampler and a matching shader constant — consistent with a runtime tint
multiply. This was validated experimentally by taking a real diffuse and
multiplying it by white / yellow / black and confirming clean recolors
(yellow → yellow cloth, black → black, white → neutral), matching the
expected model.

## Dye palette RGB values

A subset (28 of 45) of standard/crafted dye colors were extracted by
sampling the center pixel of each dye's rendered swatch icon from the
LOTRO wiki, via a headless-browser screenshot technique (see "Technique"
below). Calibration sanity checks passed on primary/neutral colors (White →
`#FFFFFF`, Black → `#000000`, Yellow → `#FFF200`, Red → `#FF0000`, Grey →
`#808080`).

**16 dyes are still missing** — all festival/event dyes that are not listed
on the wiki's main dye catalogue page and require scraping each dye's own
individual wiki page: Lórien Gold, Belegaer Blue, Moria Silver, Rohan
Green, Ashenslades Green, Dark Clay, Sunset Orange, Twilight Purple,
Lavender, Shire Peach, Shire-plum, Autumn Leaf, Imladris Fallen Leaf, Dark
Purple, Dark Mossy Green, Bullroarer's Green. Cross-checking against the
public data confirms these 16 (plus the 28 scraped, plus one "Dye Wash")
account for all 45 named dyes — nothing unaccounted-for is hiding in the
client's dye table; the remaining gap is purely a data-collection task
against individual wiki pages.

**Ground-truth caveat**: these RGB values come from wiki-rendered swatch
icons, not from the client's own floatCode → color mapping. The client-side
mapping (presumably a small palette texture or a constant table read via the
shader's dye sampler) has not been located — see [limitations.md](limitations.md).
The wiki-sourced values are good enough for a plausible-looking picker but
are **not** verified against the game's own internal math.

### Technique: scraping a Cloudflare-protected wiki

The LOTRO wiki referenced above is behind a Cloudflare JS challenge that
blocks plain server-side fetches (a bare HTTP client or non-browser fetch
tool fails the challenge, as does most archived-page access). A **headless
browser** works, because it executes the challenge JavaScript like a normal
visitor. This is a general technique worth knowing for any automated access
to that wiki, not just for dye colors: table-scraping for text data first,
then per-swatch canvas pixel sampling once table text alone proved
insufficient to recover exact RGB values.

Dye **items** (as opposed to their color/floatCode data) live in the
client's public item data, named `"<Color> Dye"`; the item DID resolves
through the same [PropertiesSet](properties.md) pipeline as any other item.

## Remaining work

- Extract the 16 missing festival dye RGBs from their individual wiki
  pages.
- Locate the client's own floatCode → color ground truth, if it exists as a
  discrete table (versus being computed entirely in-shader from the
  float) — this would let the palette be verified or replaced with exact
  values instead of wiki-sourced approximations.
- A skin-tone picker uses the same shader mechanism against a different
  sampler/region (unconfirmed in detail) — see [limitations.md](limitations.md).
- The v1 render approach described above tints the whole cloth surface via
  the alpha mask; items with per-dye wardrobe `q`-blocks (see
  [wardrobe.md](wardrobe.md)) should eventually switch **material** per dye
  choice instead of tinting at render time, since the client data suggests
  some items ship genuinely distinct per-dye textures rather than relying
  purely on a runtime tint.
- A secondary dye channel (a second dyeable region on two-tone items) is not
  yet distinguished from the single alpha-mask model above.

## See also
- [textures.md](textures.md) — the diffuse texture the dye tint is applied to
- [overview.md](overview.md) — where dyeing fits in the overall pipeline
- [wardrobe.md](wardrobe.md) — the wardrobe-entry `q`-block dye-variant connection
- [limitations.md](limitations.md) — festival-dye gap and remaining picker work
