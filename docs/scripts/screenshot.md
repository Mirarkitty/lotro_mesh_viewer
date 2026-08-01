# screenshot.py

[`screenshot.py`](../../screenshot.py)

## Purpose

`screenshot.py` drives the running viewer ([viewer.md](viewer.md)'s
`app.py`) with Playwright's headless Chromium and saves a PNG plus the
on-page stats line and any browser console errors. It is a verification
tool, not a pipeline stage — but it encodes the project's single
most-repeated lesson (see [../limitations.md](../limitations.md) and the
"Gotchas" sections throughout this directory): **statistical checks on
decoded geometry can look correct while the mesh is scrambled — only a
rendered image catches that.** [mesh_decode.py](mesh_decode.md)'s `stats()`
can report zero degenerate triangles, zero NaN coordinates, and in-range
indices on a mesh whose vertex/triangle mapping is nonetheless garbled;
sliver-edge detection helps but a screenshot is the ground truth.

Runs headless with **software GL** (`--use-gl=angle
--use-angle=swiftshader --enable-unsafe-swiftshader
--ignore-gpu-blocklist`), specifically so it works on servers with no GPU —
this is what makes it usable in CI or over SSH to a headless machine.

Requires `playwright`, which is **not** installed by the base
`requirements.txt` (see that file's comment) — install it and its browser
separately:

```bash
pip install playwright
playwright install chromium
```

## CLI usage

```
python3 app.py &                      # viewer must already be running
python3 screenshot.py <mesh_json_name> <out_png> [--flip] [--url URL] [--wait SECONDS]
```

| Argument | Meaning |
|---|---|
| `mesh` | value of the viewer's `#pick` dropdown = the `decoded/*.json` file name |
| `out` | output PNG path (`.png` appended automatically if missing) |
| `--flip` | check the viewer's flip-V checkbox before rendering (see [viewer.md](viewer.md)'s flipV gotcha) |
| `--url` | viewer URL (default `http://127.0.0.1:8722/`) |
| `--wait` | seconds to wait for page load, and again for the render, before capturing (default `2.5`) |

Example:

```
python3 app.py &
python3 screenshot.py compose_7000DA5B_20001E58.json exq_dwarf.png
python3 screenshot.py compose_7000DA5B_20001E58.json exq_dwarf_flip.png --flip
```

This tool has no `--game-dir`/`--out-dir` options and does not import
[config.py](config.md) — it only talks HTTP to an already-running viewer, so
it inherits whatever game/output directories that viewer was started with.

## How it works internally

1. Launches headless Chromium via Playwright with the software-GL flags
   above, opens a 900×700 viewport page, and installs a console listener
   that collects `error`-type console messages.
2. Navigates to `--url` and waits for network idle, then waits `--wait`
   seconds for the three.js scene and its assets to settle.
3. Selects the requested mesh from the `#pick` dropdown (`select_option`),
   **unchecks `#spin`** — a spinning mesh screenshots inconsistently
   between runs, so it's disabled specifically for reproducible
   verification — and checks `#flip` if `--flip` was passed.
4. Waits `--wait` seconds again for the reload/render to complete, prints
   the on-page `#stats` text (verts/tris/texture list — see
   [viewer.md](viewer.md)'s `index.html` stats block), takes the
   screenshot, and prints any collected console errors (first 4).

## Gotchas & lessons

- **Always screenshot both shaded and wireframe, and validate on at least
  two independent meshes** — this is stated directly in the module
  docstring as the verification discipline this tool exists to enforce.
  Wireframe reveals scrambled topology that a shaded render can hide behind
  correct-looking silhouettes and lighting.
- **`#spin` is deliberately disabled before capture** — without this, two
  screenshots of the identical mesh state can differ in rotation angle,
  making before/after comparisons across a decoder change unreliable.
- **The viewer must already be running** — this script has no lifecycle
  management for `app.py`; it assumes port 8722 (or `--url`) is already
  serving.
- **Software GL is a correctness choice, not just a convenience** — real
  GPU drivers can vary rendering subtly (antialiasing, texture filtering)
  across machines, whereas SwiftShader gives deterministic, reproducible
  output for comparing screenshots run-to-run and machine-to-machine.

## See also

- [viewer.md](viewer.md) — the app this script drives, including the `#pick`/`#spin`/`#flip`/`#stats` DOM elements it depends on.
- [mesh_decode.py](mesh_decode.md) — the decoder whose `stats()` cannot substitute for this tool's visual check.
- [../mesh-format.md](../mesh-format.md), [../limitations.md](../limitations.md) — the format traps and verification-discipline log this tool exists to guard against.
- [INDEX.md](INDEX.md) — full script index.
