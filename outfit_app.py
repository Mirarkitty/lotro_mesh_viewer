#!/usr/bin/env python3
"""outfit_app.py — the outfit composer server (multi-slot rendering).

Thin Flask layer over api_common; serves outfit.html on port 8723. The
single-mesh viewer stays on app.py:8722; both share the same backend modules
and caches on disk.

Run:  python3 outfit_app.py [--host H] [--port P] [--game-dir DIR]
      then open http://127.0.0.1:8723/
Needs Flask and the item catalog (items_catalog.py). To load saved outfits
from LotroCompanion, its data must be at ~/.lotrocompanion (default install)
or pointed to with $LOTRO_COMPANION_DIR (the .../data/characters directory).
"""
import argparse
import os
from flask import Flask, send_from_directory, jsonify, request
import config

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--host", default=os.environ.get("OUTFIT_HOST", "127.0.0.1"))
ap.add_argument("--port", type=int,
                default=int(os.environ.get("OUTFIT_PORT", "8723")))
config.add_game_dir_argument(ap)
_args, _ = ap.parse_known_args()
config.apply_args(_args)   # must run BEFORE api_common binds its cache dirs

import api_common as api

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = _args.host, _args.port

app = Flask(__name__)

@app.route("/")
def index():
    return send_from_directory(HERE, "outfit.html")

@app.route("/search")
def _search():
    return jsonify(api.search(request.args.get("q"),
                              slot=request.args.get("slot") or None))


@app.route("/sets")
def _sets():
    return jsonify(api.search_sets(request.args.get("q"),
                                   weight=request.args.get("weight") or None))

@app.route("/setmates")
def _setmates():
    return jsonify(api.setmates(int(request.args.get("did"), 16)))

@app.route("/toons")
def _toons():
    """LotroCompanion characters + their saved outfits (see api_common)."""
    return jsonify({"base": api.companion_base(), "toons": api.companion_toons()})

@app.route("/outfit")
def _outfit():
    try:
        return jsonify(api.companion_outfit(request.args.get("toon"),
                                            int(request.args.get("index"))))
    except Exception as ex:
        return jsonify({"error": str(ex)[:200]}), 400

@app.route("/item")
def _item():
    """Catalog row by DID — lets the viewer rebuild an outfit from the URL hash."""
    r = api.item_row(int(request.args.get("did"), 16))
    return jsonify(r or {"error": "unknown item"}), (200 if r else 404)

@app.route("/bodies")
def _bodies():
    return jsonify(api.bodies_for(int(request.args.get("item"), 16)))

@app.route("/compose")
def _compose():
    try:
        item = int(request.args.get("item"), 16)
        app_ = int(request.args.get("app"), 16)
        f = api.compose_cached(item, app_)
        return jsonify({"file": f, "overrides": api.slot_overrides(item, app_)})
    except Exception as ex:
        return jsonify({"error": str(ex)[:200]}), 500

@app.route("/compose_skinned")
def _compose_skinned():
    try:
        item = int(request.args.get("item"), 16)
        app_ = int(request.args.get("app"), 16)
        f, clip = api.compose_skinned(item, app_)
        return jsonify({"file": f, "clip_did": clip,
                        "overrides": api.slot_overrides(item, app_)})
    except Exception as ex:
        return jsonify({"error": str(ex)[:200]}), 500

@app.route("/compose_face")
def _compose_face():
    """Default head (face) + hair for a body, skinned. Optional head_item/
    head_app (hex) = the equipped Head item, which decides whether/which hair
    shows (docs/hair-face.md stub mechanism). hands=1 also emits the
    body's BARE HANDS, feet=1 the bare FEET — the caller asks for each only
    when no worn garment supplies or blanks that part and no item occupies the
    slot. Dwarf/Hobbit have no bare-feet mesh in the data (see FEET_MESH)."""
    try:
        hi = request.args.get("head_item")
        ha = request.args.get("head_app")
        f, clip = api.compose_face(request.args.get("body"),
                                   int(hi, 16) if hi else None,
                                   int(ha, 16) if ha else None,
                                   hands=request.args.get("hands") == "1",
                                   feet=request.args.get("feet") == "1",
                                   head_style=int(request.args.get("head_style") or 0),
                                   hair_style=int(request.args.get("hair_style") or 0))
        return jsonify({"file": f, "clip_did": clip})
    except Exception as ex:
        return jsonify({"error": str(ex)[:200]}), 500

@app.route("/clips")
def _clips():
    """riding=hide|only filters mounted clips before the cap; dedupe=0 turns
    off motion-duplicate collapsing; motion= filters by the foot classification
    (walk/run/fwd/back/strafe/standing/kneel/loco/unclassified).
    See api_common.clips_for_body / MOTION_FILTERS."""
    try:
        return jsonify(api.clips_for_body(
            request.args.get("body"),
            riding=request.args.get("riding") or None,
            dedupe=request.args.get("dedupe", "1") != "0",
            motion=request.args.get("motion") or None))
    except Exception as ex:
        return jsonify({"error": str(ex)[:200]}), 500

@app.route("/clip")
def _clip():
    try:
        return jsonify(api.clip_cached(int(request.args.get("did"), 0),
                                       request.args.get("body")))
    except Exception as ex:
        return jsonify({"error": str(ex)[:200]}), 500

@app.route("/clipname", methods=["GET", "POST"])
def _clipname():
    try:
        did = int(request.values.get("did"), 0)
        name = (request.values.get("name") or "").strip()
        e = api.set_clip_name(did, name, request.values.get("body"))
        return jsonify({"did": did, "entry": e})
    except Exception as ex:
        return jsonify({"error": str(ex)[:200]}), 500

@app.route("/dyes")
def _dyes():
    return jsonify(api.dyes())

@app.route("/skintex")
def _skintex():
    """Head/body texture tinted by a character's skin colour (alpha = tint mask)."""
    try:
        c = [int(x) for x in request.args.get("rgb", "").split(",")[:3]]
        f = api.skin_tinted_texture(int(request.args.get("tex"), 16), c)
        return jsonify({"file": f})
    except Exception as ex:
        return jsonify({"error": str(ex)[:200]}), 500

@app.route("/dyedtex")
def _dyedtex():
    try:
        f = api.dyed_texture(int(request.args.get("tex"), 16),
                             request.args.get("dye"))
        return jsonify({"file": f})
    except Exception as ex:
        return jsonify({"error": str(ex)[:200]}), 500

@app.route("/decoded/<path:f>")
def _dec(f):
    return send_from_directory(api.DECODED, f)

@app.route("/textures/<path:f>")
def _tex(f):
    return send_from_directory(api.TEXTURES, f)

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
