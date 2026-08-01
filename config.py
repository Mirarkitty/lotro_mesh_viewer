"""Shared configuration for the LOTRO extraction tools.

Centralizes two things every tool needs:

1. **Locating the game install** (the directory holding ``client_*.dat``).
   Resolution order:
     1. an explicit ``--game-dir`` argument (see :func:`add_game_dir_argument`)
        or a call to :func:`set_game_dir`,
     2. the ``LOTRO_DIR`` environment variable,
     3. a probe of :data:`DEFAULT_LOCATIONS` (common install paths).
   The chosen directory must contain ``client_general.dat``.

2. **Opening archives once.** :func:`open_dat` / :func:`open_chain` cache one
   handle per archive for the whole process (``datfile.DatFile`` is internally
   locked, so a shared handle is safe across threads). The named helpers
   (:func:`general`, :func:`mesh_chain`, ...) encode which physical archives
   each logical archive spans — e.g. meshes live in ``client_mesh.dat`` plus
   the ``client_mesh_aux_1.datx`` overflow file.

Output caches: decoded meshes are written to ``decoded/`` and extracted
textures to ``textures/`` under :func:`out_dir` (the repository directory by
default, overridable with the ``LOTRO_OUT`` environment variable or
``set_out_dir``). The viewer (``app.py``) serves those same directories.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

#: Probed (after ~-expansion) when neither --game-dir nor $LOTRO_DIR is given.
DEFAULT_LOCATIONS = [
    "~/The Lord of the Rings Online",
    "~/.steam/steam/steamapps/common/Lord of the Rings Online",
    "~/.local/share/Steam/steamapps/common/Lord of the Rings Online",
    "C:/Program Files (x86)/Steam/steamapps/common/Lord of the Rings Online",
    "C:/Program Files (x86)/StandingStoneGames/The Lord of the Rings Online",
]

#: The file whose presence identifies a LOTRO install directory.
_SENTINEL = "client_general.dat"

_game_dir = None
_out_dir = None


def set_game_dir(path):
    """Explicitly set the game install directory (wins over env/probing)."""
    global _game_dir
    path = os.path.expanduser(path)
    if not os.path.isfile(os.path.join(path, _SENTINEL)):
        raise FileNotFoundError(
            "%s not found in %r - not a LOTRO install directory" % (_SENTINEL, path))
    _game_dir = path


def game_dir():
    """The resolved game install directory (see module docstring for order)."""
    global _game_dir
    if _game_dir is not None:
        return _game_dir
    env = os.environ.get("LOTRO_DIR")
    if env:
        set_game_dir(env)          # validates the sentinel too
        return _game_dir
    for cand in DEFAULT_LOCATIONS:
        p = os.path.expanduser(cand)
        if os.path.isfile(os.path.join(p, _SENTINEL)):
            _game_dir = p
            return _game_dir
    raise FileNotFoundError(
        "LOTRO install not found. Pass --game-dir, set $LOTRO_DIR, or install "
        "the game in one of: %s" % ", ".join(DEFAULT_LOCATIONS))


def dat_path(name):
    """Absolute path of one archive file inside the game directory."""
    return os.path.join(game_dir(), name)


# ---- cached archive handles -------------------------------------------------

_dats = {}     # filename -> DatFile
_chains = {}   # (filenames...) -> DatChain


def open_dat(name):
    """One cached DatFile per archive filename (e.g. 'client_general.dat')."""
    if name not in _dats:
        from datfile import DatFile
        _dats[name] = DatFile(dat_path(name))
    return _dats[name]


def open_chain(*names):
    """One cached DatChain over the subset of `names` that exists on disk.

    Aux ``.datx`` overflow files are optional per install/patch level, so
    missing members are silently skipped (the base archive must exist)."""
    if names not in _chains:
        from datfile import DatChain
        paths = [dat_path(n) for n in names if os.path.exists(dat_path(n))]
        if not paths:
            raise FileNotFoundError("none of %s exist in %s" % (names, game_dir()))
        _chains[names] = DatChain(*paths)
    return _chains[names]


# Logical archives -> the physical files they span.
def general():
    """client_general.dat: surfaces, materials, wardrobe records, skeletons."""
    return open_dat("client_general.dat")


def gamelogic():
    """client_gamelogic.dat: item property records + the property dictionary."""
    return open_dat("client_gamelogic.dat")


def anim():
    """client_anim.dat: 0x05 Havok animation clips."""
    return open_dat("client_anim.dat")


def local(language="English"):
    """client_local_<language>.dat: 0x25 text records (item names etc.)."""
    return open_dat("client_local_%s.dat" % language)


def mesh_chain():
    """Mesh geometry: client_mesh.dat + its aux overflow archive."""
    return open_chain("client_mesh.dat", "client_mesh_aux_1.datx")


def surface_chain():
    """Standard-resolution textures."""
    return open_chain("client_surface.dat", "client_surface_aux_1.datx",
                      "client_surface_aux_2.datx")


def highres_chain():
    """High-resolution textures."""
    return open_chain("client_highres.dat", "client_highres_aux_1.datx",
                      "client_highres_aux_2.datx", "client_highres_aux_3.datx")


# ---- output caches ----------------------------------------------------------

def set_out_dir(path):
    """Explicitly set the output/cache root (wins over $LOTRO_OUT)."""
    global _out_dir
    _out_dir = os.path.expanduser(path)


def out_dir():
    """Root for the decoded/ and textures/ caches (default: this repo dir)."""
    if _out_dir is not None:
        return _out_dir
    return os.environ.get("LOTRO_OUT") or HERE


def decoded_dir():
    """decoded/ cache (viewer JSON meshes); created on first use."""
    d = os.path.join(out_dir(), "decoded")
    os.makedirs(d, exist_ok=True)
    return d


def textures_dir():
    """textures/ cache (extracted PNGs); created on first use."""
    d = os.path.join(out_dir(), "textures")
    os.makedirs(d, exist_ok=True)
    return d


# ---- CLI plumbing -----------------------------------------------------------

def add_game_dir_argument(parser):
    """Add the standard --game-dir/--out-dir options to an argparse parser."""
    parser.add_argument("--game-dir", metavar="DIR",
                        help="LOTRO install directory holding client_*.dat "
                             "(default: $LOTRO_DIR or a probe of common paths)")
    parser.add_argument("--out-dir", metavar="DIR",
                        help="root for the decoded/ and textures/ output caches "
                             "(default: $LOTRO_OUT or the repository directory)")
    return parser


def apply_args(args):
    """Apply the options added by add_game_dir_argument (call once in main)."""
    if getattr(args, "game_dir", None):
        set_game_dir(args.game_dir)
    if getattr(args, "out_dir", None):
        set_out_dir(args.out_dir)
