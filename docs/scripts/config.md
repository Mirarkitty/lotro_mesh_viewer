# config.py

[`config.py`](../../config.py)

## Purpose

`config.py` is the shared foundation every other tool imports. It has no CLI
of its own; it exists to answer two questions the same way for every script
in the toolkit:

1. **Where is the LOTRO install?** (the directory containing `client_*.dat`)
2. **Where should decoded output go, and how do we avoid re-opening the same
   archive file twice per process?**

It sits underneath the whole pipeline (item → PropertiesSet → worn
appearance → mesh + material → texture → dye → render) as plumbing: every
other page in this directory calls into `config` to reach an archive handle
or an output directory, rather than opening files itself. See
[overview.md](../overview.md) for how the pipeline stages connect.

## Game-directory resolution order

1. An explicit `--game-dir` CLI argument (via `add_game_dir_argument` +
   `apply_args`), or a direct call to `set_game_dir(path)`.
2. The `LOTRO_DIR` environment variable.
3. A probe of `DEFAULT_LOCATIONS` — common Steam/Standing Stone Games
   install paths (Linux, Steam-on-Linux, and Windows layouts).

Whichever directory is chosen must contain `client_general.dat` (the
sentinel file); otherwise `set_game_dir` raises `FileNotFoundError`.

## Output-root resolution order

1. An explicit `--out-dir` CLI argument, or `set_out_dir(path)`.
2. The `LOTRO_OUT` environment variable.
3. The repository directory itself (`HERE`, i.e. where `config.py` lives).

The output root holds two caches, created on first access:
- `decoded/` — viewer-ready mesh/animation JSON (written by
  [mesh_decode.py](mesh_decode.md), [compose.py](compose.md),
  [export_skinned.py](export_skinned.md)).
- `textures/` — extracted PNGs (written by [tex_extract.py](tex_extract.md)).

[viewer.md](viewer.md) (`app.py`) serves both of these directories directly
over HTTP.

## Public API

| Function | Signature | Returns |
|---|---|---|
| `set_game_dir` | `set_game_dir(path)` | `None`; raises `FileNotFoundError` if `client_general.dat` is not in `path` |
| `game_dir` | `game_dir()` | resolved game directory string (see resolution order above) |
| `dat_path` | `dat_path(name)` | absolute path of one archive file inside the game directory |
| `open_dat` | `open_dat(name)` | a cached `datfile.DatFile` for one archive filename |
| `open_chain` | `open_chain(*names)` | a cached `datfile.DatChain` over whichever of `names` exist on disk |
| `general` | `general()` | `DatFile` for `client_general.dat` (surfaces, materials, wardrobe records, skeletons) |
| `gamelogic` | `gamelogic()` | `DatFile` for `client_gamelogic.dat` (item property records + property dictionary) |
| `anim` | `anim()` | `DatFile` for `client_anim.dat` (0x05 Havok animation clips) |
| `local` | `local(language="English")` | `DatFile` for `client_local_<language>.dat` (0x25 text records, e.g. item names) |
| `mesh_chain` | `mesh_chain()` | `DatChain` over `client_mesh.dat` + `client_mesh_aux_1.datx` |
| `surface_chain` | `surface_chain()` | `DatChain` over `client_surface.dat` + its 2 aux overflow files (standard-res textures) |
| `highres_chain` | `highres_chain()` | `DatChain` over `client_highres.dat` + its 3 aux overflow files (high-res textures) |
| `set_out_dir` | `set_out_dir(path)` | `None`; sets the output/cache root |
| `out_dir` | `out_dir()` | resolved output root string |
| `decoded_dir` | `decoded_dir()` | `<out_dir>/decoded`, created if missing |
| `textures_dir` | `textures_dir()` | `<out_dir>/textures`, created if missing |
| `add_game_dir_argument` | `add_game_dir_argument(parser)` | adds `--game-dir` and `--out-dir` to an `argparse.ArgumentParser` |
| `apply_args` | `apply_args(args)` | applies the two options parsed above (call once in each tool's `main`) |

## How it works internally

- **Cached archive handles.** `_dats` and `_chains` are process-global dicts
  keyed by filename / filename-tuple. `open_dat` and `open_chain` each open
  their underlying `datfile.DatFile` exactly once per process and hand back
  the same object on every subsequent call — important because
  `datfile.DatFile` is internally locked (see [datfile.py](datfile.md)) and
  a shared handle is what lets [viewer.md](viewer.md) (`app.py`, run with
  `threaded=True`) serve multiple concurrent requests against one archive
  file safely.
- **Aux `.datx` overflow files are optional.** `open_chain` silently skips
  any of `names` that don't exist on disk — different LOTRO patch levels
  ship different numbers of aux overflow archives — but requires at least
  one physical file to exist, else it raises `FileNotFoundError`.
- **Lazy imports.** `open_dat`/`open_chain` import `datfile` inside the
  function body, and every module-level "archive handle" helper elsewhere in
  the toolkit (e.g. `tex_extract._gen()`) is similarly lazy, so importing
  `config` (or any tool module) never touches the filesystem until an
  archive is actually needed. This is what keeps `import mesh_decode` (etc.)
  safe to run without a configured game directory, e.g. for unit tests.
- **Logical archives vs physical files.** The named helpers (`general`,
  `mesh_chain`, `surface_chain`, ...) encode which physical `.dat`/`.datx`
  files each logical archive spans, so callers never need to know the exact
  overflow-file list for meshes vs. surfaces vs. highres textures.

## Gotchas & lessons

- `game_dir()` and `open_dat`/`open_chain` are the ONLY sanctioned way to
  reach the install directory or an archive handle — scripts that bypass
  `config` and hardcode a path lose the env-var/CLI override and the
  per-process handle cache (and can silently open the same archive file
  twice, defeating the point of the RLock in `DatFile`).
- The sentinel check (`client_general.dat` must exist) is deliberately
  strict: it fails fast on a wrong path rather than letting a later archive
  lookup fail with a confusing `KeyError`.
- `out_dir()` defaults to the repository directory itself, so a bare
  `python3 mesh_decode.py ...` run from inside the repo will write into
  `decoded/`/`textures/` right next to the source — set `LOTRO_OUT` or
  `--out-dir` to keep the repo clean.

## See also

- [datfile.py](datfile.md) — the archive reader `config.open_dat`/`open_chain` wrap.
- [viewer.md](viewer.md) — serves `decoded_dir()`/`textures_dir()` over HTTP.
- [../dat-format.md](../dat-format.md) — the container format these handles read.
- [../overview.md](../overview.md) — end-to-end pipeline this plumbing supports.
- [INDEX.md](INDEX.md) — full script index.
