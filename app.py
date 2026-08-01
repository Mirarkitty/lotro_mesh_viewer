#!/usr/bin/env python3
"""Local three.js viewer for decoded LOTRO meshes.

Serves index.html (mesh browser + item search/compose) and anim.html (skinned
animation playback) plus the decoded/ and textures/ caches. Uses Flask when
installed (full feature set: /search, /bodies, /compose, /dyes, /dyedtex);
falls back to a stdlib http.server with the basic mesh-browsing routes.

Run:  python3 app.py [--host H] [--port P] [--game-dir DIR] [--out-dir DIR]
      then open http://127.0.0.1:8722/
The item search needs items_catalog.jsonl (build once with items_catalog.py).
"""
import os, json, glob, argparse
import config

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--host", default=os.environ.get("VIEWER_HOST", "127.0.0.1"))
ap.add_argument("--port", type=int,
                default=int(os.environ.get("VIEWER_PORT", "8722")))
config.add_game_dir_argument(ap)
_args, _ = ap.parse_known_args()
config.apply_args(_args)
HOST, PORT = _args.host, _args.port

HERE = os.path.dirname(os.path.abspath(__file__))   # index.html/anim.html live here
DECODED = config.decoded_dir()
TEXTURES = config.textures_dir()

def list_meshes():
    out = []
    for p in sorted(glob.glob(os.path.join(DECODED, "*.json"))):
        try:
            d = json.load(open(p))
            out.append({"id": d.get("id", os.path.basename(p)[:-5]),
                        "file": os.path.basename(p),
                        "verts": len(d.get("vertices", [])),
                        "tris": len(d.get("triangles", []))})
        except Exception as e:
            out.append({"id": os.path.basename(p), "file": os.path.basename(p), "error": str(e)})
    return out

try:
    from flask import Flask, send_from_directory, jsonify
    app = Flask(__name__)
    @app.route("/")
    def index(): return send_from_directory(HERE, "index.html")
    @app.route("/anim")
    def _anim(): return send_from_directory(HERE, "anim.html")
    @app.route("/list")
    def _list(): return jsonify(list_meshes())
    @app.route("/decoded/<path:f>")
    def _dec(f): return send_from_directory(DECODED, f)
    @app.route("/textures/<path:f>")
    def _tex(f): return send_from_directory(TEXTURES, f)

    # ---- item search / on-demand composition (catalog: items_catalog.jsonl) ----
    import re as _re
    _catalog = None
    def catalog():
        global _catalog
        if _catalog is None:
            _catalog = []
            with open(os.path.join(config.out_dir(), "items_catalog.jsonl")) as f:
                for line in f:
                    _catalog.append(json.loads(line))
        return _catalog

    SPECIES = {23: "Man", 65: "Elf", 73: "Dwarf", 81: "Hobbit", 114: "Beorning",
               117: "HighElf", 120: "Stoutaxe", 125: "sp125"}
    SEX = {0x1000: "M", 0x2000: "F"}
    SLOT_BITS = {0x02: "Head", 0x04: "Chest", 0x08: "Legs", 0x10: "Hands",
                 0x20: "Feet", 0x40: "Shoulders", 0x80: "Back"}
    def slot_name(v):
        if not v: return None
        names = [n for bit, n in SLOT_BITS.items() if v & bit]
        return "+".join(names) if names else "0x%08X" % v
    GARMENT_TAG = 0x1000000C
    def entry_renderable(e, mesh_db):
        """True only if the entry's MAIN garment part ships (not just hands/stubs)."""
        parts = e["blocks"][0]["parts"]
        gar = [p for p in parts if p["tag"] == GARMENT_TAG]
        for p in (gar if gar else parts):
            f = mesh_db.find_file(p["mesh"])
            if f and f[2] > 2000: return True
        return False

    @app.route("/search")
    def _search():
        from flask import request
        q = (request.args.get("q") or "").strip().lower()
        if len(q) < 2: return jsonify([])
        terms = q.split()
        scored = []
        for r in catalog():
            n = (r.get("name") or "").lower()
            if not all(t in n for t in terms): continue
            if not any(b.get("present") for b in r["bodies"]): continue
            disp = _re.sub(r"\[[a-z]+\]$", "", r["name"])
            d = disp.lower()
            rank = 0 if d == q else (1 if d.startswith(q) else 2)
            scored.append((rank, len(disp), disp, r))
        scored.sort(key=lambda x: x[:3])
        out, seen = [], set()
        for rank, _l, disp, r in scored:
            sig = (disp, r["bodies"][0]["key"] if r["bodies"] else 0)
            if sig in seen: continue
            seen.add(sig)
            bodies = [dict(app=b["app"], species=b["species"], sex=b["sex"],
                           label="%s-%s" % (SPECIES.get(b["species"], b["species"]),
                                            SEX.get(b["sex"], "?")))
                      for b in r["bodies"]]
            out.append(dict(did=r["did"], name=disp, quality=r.get("quality"),
                            item_class=r.get("item_class"), level=r.get("level"),
                            slot=r.get("slot"), slot_name=slot_name(r.get("slot")),
                            clothing_color=r.get("clothing_color"), bodies=bodies))
            if len(out) >= 100: break
        return jsonify(out)


    _wrec_cache = {}
    @app.route("/bodies")
    def _bodies():
        from flask import request
        import wearable2
        item = int(request.args.get("item"), 16)
        row = next((r for r in catalog() if r["did"] == item), None)
        if not row: return jsonify([])
        out = []
        seen = set()
        for b in row["bodies"]:
            if b["app"] in _wrec_cache:
                rec = _wrec_cache[b["app"]]
            else:
                try:
                    rec = wearable2.parse_record(config.general().read_content(b["app"]))
                except Exception:
                    rec = None
                _wrec_cache[b["app"]] = rec
            present = False
            if rec:
                e = next((x for x in wearable2.entries(rec) if x["key"] == b["key"]), None)
                if e:
                    present = entry_renderable(e, config.mesh_chain())
            key = (b["app"], b["key"])
            if key in seen: continue
            seen.add(key)
            out.append(dict(app=b["app"], key=b["key"], present=present,
                            label="%s-%s" % (SPECIES.get(b["species"], b["species"]),
                                             SEX.get(b["sex"], "?"))))
        return jsonify(out)


    @app.route("/dyes")
    def _dyes():
        with open(os.path.join(HERE, "dye_colors.json")) as f:
            return jsonify(json.load(f))


    @app.route("/dyedtex")
    def _dyedtex():
        from flask import request
        did = int(request.args.get("tex"), 16)
        dye = request.args.get("dye")
        name = "dyed_%08X_%s.png" % (did, "".join(c for c in dye if c.isalnum()))
        fp = os.path.join(TEXTURES, name)
        if not os.path.exists(fp):
            from PIL import Image
            import tex_extract as tx
            with open(os.path.join(HERE, "dye_colors.json")) as f:
                rgb = json.load(f)[dye]["rgb"]
            im = Image.open(tx.extract_texture(did)).convert("RGBA")
            px = im.load()
            w, h = im.size
            fr, fg, fb = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
            for y in range(h):
                for x in range(w):
                    r, g, b, a = px[x, y]
                    if a < 128:   # low alpha = dyeable cloth
                        px[x, y] = (int(r * fr), int(g * fg), int(b * fb), a)
            im.convert("RGB").save(fp)
        return jsonify({"file": name})

    @app.route("/compose")
    def _compose():
        from flask import request
        item = int(request.args.get("item"), 16)
        app_did = int(request.args.get("app"), 16)
        name = "compose_%08X_%08X" % (item, app_did)
        fp = os.path.join(DECODED, name + ".json")
        if not os.path.exists(fp):
            import compose as _c
            try:
                _c.compose(item, app_did, name)
            except Exception as ex:
                return jsonify({"error": str(ex)[:200]}), 500
        return jsonify({"file": name + ".json"})

    if __name__ == "__main__":
        app.run(host=HOST, port=PORT, debug=False, threaded=True)
except ImportError:
    # stdlib fallback
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                b = open(os.path.join(HERE, "index.html"), "rb").read()
                return self._send(200, b, "text/html")
            if self.path == "/list":
                return self._send(200, json.dumps(list_meshes()).encode())
            if self.path.startswith("/decoded/"):
                fp = os.path.join(DECODED, os.path.basename(self.path))
                if os.path.exists(fp):
                    return self._send(200, open(fp, "rb").read())
                return self._send(404, b'{"error":"not found"}')
            if self.path.startswith("/textures/"):
                fp = os.path.join(TEXTURES, os.path.basename(self.path))
                if os.path.exists(fp):
                    return self._send(200, open(fp, "rb").read(), "image/png")
                return self._send(404, b'{"error":"not found"}')
            self._send(404, b'{"error":"not found"}')
        def log_message(self, *a): pass
    if __name__ == "__main__":
        print("Flask not installed; using stdlib server.")
        HTTPServer((HOST, PORT), H).serve_forever()
