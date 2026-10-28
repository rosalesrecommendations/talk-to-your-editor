"""Editing know-how as files: starter styles and the creator's own.

The words that teach the model how this creator edits used to live
only as strings inside ``meta_provider.py``. They now also live as
plain files a person can read, so a style can be picked, a pack can
be edited, and a run can say which pack it used.

Pack file format: a header of ``key: value`` lines (``name``,
``version``, ``group``, ``style``), then a line containing exactly
``---``, then the body, word for word. The parser here is small and
hand-rolled on purpose: the standard library is all this app uses.

Where packs live, under ``~/.talk-to-your-editor/skills/``, in two
groups kept apart:

- ``starter/<style-id>/direction.md`` and ``retake-judge.md``: the
  styles that ship with the app. Copies of the packs built into the
  package (``skill_packs/starter/``) are placed here the first time
  they are needed; a file that is already here is never overwritten,
  so an edit a person made to a pack stays.
- ``yours/direction.md`` and ``yours/retake-judge.md``: the creator's
  own added know-how. This group starts empty. When one of these
  files is present, its words are added after the starter pack's
  words for the same job, and the run record says so.

When no pack can be found at all, loading comes back empty-handed
and the caller falls back to the built-in words in
``meta_provider.py``, so the app works exactly as it did before
packs existed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# The three starter styles, id to the plain label the page shows.
STYLES = {
    "justins-way": "Justin's way",
    "keep-it-simple": "Keep it simple",
    "hands-off": "Hands off",
}
STYLE_LABELS = STYLES
DEFAULT_STYLE = "justins-way"

SKILLS_DIR_NAME = "skills"
APP_DIR_NAME = ".talk-to-your-editor"

# The line that joins a starter pack's words to the creator's own
# added words inside the text the model reads.
YOURS_SEPARATOR = "\n\nYour own added know-how:\n"

_PACK_FILENAMES = {
    "direction": "direction.md",
    "retake": "retake-judge.md",
}


def default_skills_dir() -> Path:
    """The skills folder, resolved at call time through the home
    folder, so a test can point the home folder somewhere temporary
    and exercise the real load and save paths."""
    return Path.home() / APP_DIR_NAME / SKILLS_DIR_NAME


def seed_packs_dir() -> Path:
    """The starter packs built into the package itself."""
    return Path(__file__).resolve().parent / "skill_packs" / "starter"


def clean_style(style: Optional[str]) -> str:
    """A style id that is known, else the default style."""
    if style in STYLES:
        return str(style)
    return DEFAULT_STYLE


def parse_pack(text: str) -> tuple[dict, str]:
    """Split one pack file's text into its header and its body.

    The header is the ``key: value`` lines before the first line
    that is exactly ``---``; the body is everything after that line,
    word for word, newlines included. A file with no ``---`` line
    has no header: the whole text is the body. A ``version`` value
    that reads as a whole number comes back as an int.
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == "---":
            header: dict = {}
            for header_line in lines[:index]:
                key, sep, value = header_line.partition(":")
                if not sep:
                    continue
                key = key.strip().lower()
                value = value.strip()
                if not key:
                    continue
                if key == "version":
                    try:
                        header[key] = int(value)
                        continue
                    except ValueError:
                        pass
                header[key] = value
            return header, "".join(lines[index + 1:])
    return {}, text


def read_pack(path: Path) -> Optional[tuple[dict, str]]:
    """One pack file as ``(header, body)``, or None when it is not
    there or cannot be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_pack(text)


def _read_yours_body(path: Path) -> Optional[str]:
    """The creator's own added words from one of the yours files.

    A yours file written as a pack (header, ``---``, body) gives up
    its body; a plain file gives up its whole text. None when the
    file is not there.
    """
    pack = read_pack(path)
    if pack is None:
        return None
    _header, body = pack
    return body


def ensure_seeded(skills_dir: Optional[Path] = None) -> Path:
    """Place any missing starter pack into the skills folder.

    Copies come from the packs built into the package. A file that
    already exists is never overwritten, so an edit made to a pack
    on this computer stays. The creator's own group folder is made
    too; it starts empty. A filesystem problem never raises: the
    caller can always fall back to the built-in words.
    """
    root = Path(skills_dir) if skills_dir is not None else default_skills_dir()
    try:
        (root / "yours").mkdir(parents=True, exist_ok=True)
        source_root = seed_packs_dir()
        if source_root.is_dir():
            for style_id in STYLES:
                source_dir = source_root / style_id
                if not source_dir.is_dir():
                    continue
                target_dir = root / "starter" / style_id
                for filename in _PACK_FILENAMES.values():
                    source = source_dir / filename
                    target = target_dir / filename
                    if source.is_file() and not target.exists():
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(source.read_bytes())
    except OSError:
        pass
    return root


class LoadedSkills:
    """What loading one style came back with.

    Each of the two packs (direction, retake) carries its body (or
    None when no pack was found for it), the starter pack's version
    (or None), and where the words came from: ``"pack"`` for the
    starter pack alone, ``"pack+yours"`` when the creator's own
    words were added after it, or ``"built-in"`` when no pack was
    found and the caller should use the built-in words.
    """

    def __init__(self, style: str):
        self.style = style
        self.direction_body: Optional[str] = None
        self.direction_version: Optional[int] = None
        self.direction_source = "built-in"
        self.retake_body: Optional[str] = None
        self.retake_version: Optional[int] = None
        self.retake_source = "built-in"
        # Whether a starter pack file itself was found, apart from
        # any of the creator's own words: a caller that falls back
        # to the built-in words needs to know if the body it got is
        # a starter body, or only the creator's own added words.
        self.direction_has_pack = False
        self.retake_has_pack = False

    @property
    def found(self) -> bool:
        """True when at least one starter pack was found."""
        return (
            self.direction_source != "built-in"
            or self.retake_source != "built-in"
            or self.direction_body is not None
            or self.retake_body is not None
        )

    def versions(self) -> dict:
        """The run record of what was used, in the shared shape."""
        return {
            "style": self.style,
            "direction": {
                "version": self.direction_version,
                "source": self.direction_source,
            },
            "retake": {
                "version": self.retake_version,
                "source": self.retake_source,
            },
        }


def builtin_versions(style: Optional[str] = None) -> dict:
    """The versions record for a run that used the built-in words."""
    return LoadedSkills(clean_style(style)).versions()


def load(
    skills_dir: Optional[Path] = None,
    style: Optional[str] = None,
    seed: bool = True,
) -> LoadedSkills:
    """Load one style's packs from the skills folder.

    With ``seed`` on (the standing behaviour), any missing starter
    pack is first placed into the folder from the packs built into
    the package. With ``seed`` off, the folder is read exactly as
    it stands: an empty or missing folder is a clean no-pack
    result, never an error, and the caller falls back to the
    built-in words for any pack that was not found.

    The creator's own files, when present, are added after the
    starter pack's words for the same job, behind a plain separator
    line, and the pack's source is noted as ``"pack+yours"``. When
    there is no starter pack but the creator's own file is present,
    the creator's words stand on their own and the caller still
    falls back to the built-in words only if it chooses; the load
    reports the body it found.
    """
    style_id = clean_style(style)
    loaded = LoadedSkills(style_id)
    root = Path(skills_dir) if skills_dir is not None else default_skills_dir()
    if seed:
        ensure_seeded(root)
    for kind, filename in _PACK_FILENAMES.items():
        pack = read_pack(root / "starter" / style_id / filename)
        yours_body = _read_yours_body(root / "yours" / filename)
        body: Optional[str] = None
        version: Optional[int] = None
        source = "built-in"
        if pack is not None:
            header, body = pack
            raw_version = header.get("version")
            version = raw_version if isinstance(raw_version, int) else None
            source = "pack"
        if yours_body is not None:
            if body is not None:
                body = body + YOURS_SEPARATOR + yours_body
            else:
                body = yours_body
            source = "pack+yours"
        if kind == "direction":
            loaded.direction_body = body
            loaded.direction_version = version
            loaded.direction_source = source
            loaded.direction_has_pack = pack is not None
        else:
            loaded.retake_body = body
            loaded.retake_version = version
            loaded.retake_source = source
            loaded.retake_has_pack = pack is not None
    return loaded


# A longer name for the same load, for callers that read better
# with the word "packs" in the call.
load_packs = load
