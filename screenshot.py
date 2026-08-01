#!/usr/bin/env python3
"""Headless screenshot of the mesh viewer, for visual validation.

Drives the running viewer (app.py) with Playwright's headless Chromium
(software GL, so it works on servers with no GPU), selects a mesh from the
viewer's dropdown, waits for the render, and saves a PNG plus the on-page
stats line and any browser console errors.

This encodes the project's most important verification lesson: statistical
checks on decoded geometry can look correct while the mesh is scrambled --
only a rendered image catches that (see docs/mesh-format.md). Always
screenshot both shaded and wireframe, and validate on at least two
independent meshes.

Usage:
    python3 app.py &                      # viewer must be running
    python3 screenshot.py <mesh_json_name> <out_png> [--flip] [--url URL]
"""
import argparse

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser(
    description="Screenshot one mesh in the running viewer (headless "
                "software-GL Chromium).")
ap.add_argument("mesh", help="value of the viewer's #pick dropdown = the "
                             "decoded/*.json file name")
ap.add_argument("out", help="output PNG path ('.png' appended if missing)")
ap.add_argument("--flip", action="store_true",
                help="check the viewer's flip-V checkbox before rendering")
ap.add_argument("--url", default="http://127.0.0.1:8722/",
                help="viewer URL (default: %(default)s)")
ap.add_argument("--wait", type=float, default=2.5,
                help="seconds to wait for page load and again for the render "
                     "(default: %(default)s)")
args = ap.parse_args()
out = args.out if args.out.endswith(".png") else args.out + ".png"

with sync_playwright() as p:
    # Software GL flags: render deterministically on GPU-less machines/CI.
    b = p.chromium.launch(headless=True, args=[
        "--use-gl=angle", "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    pg = b.new_page(viewport={"width": 900, "height": 700})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(args.url, wait_until="networkidle")
    pg.wait_for_timeout(args.wait * 1000)
    pg.select_option("#pick", value=args.mesh)
    pg.uncheck("#spin")            # a spinning mesh screenshots inconsistently
    if args.flip:
        pg.check("#flip")
    pg.wait_for_timeout(args.wait * 1000)
    print("stats:", (pg.text_content("#stats") or "").replace("\n", " "))
    pg.screenshot(path=out)
    b.close()
    print("errors:", errs[:4] if errs else "none")
    print("wrote", out)
