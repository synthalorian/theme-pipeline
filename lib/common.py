"""Shared helpers for the theme release pipeline (stdlib only).

Color math mirrors /workspace/qa/tools/contrast-check.py (WCAG 2.x relative
luminance and contrast ratio, same color syntax), so ratios printed here match
the QA tool for the same colors.
"""
from __future__ import annotations

import os
import re
import stat
import tomllib
from pathlib import Path

# The 25 Omarchy 4 palette keys every Glyphsmith theme defines (same list as
# tests/validators/validate-theme.sh). With `mode` that makes 26 required keys.
PALETTE_KEYS = (
    "accent background foreground selection muted dark_background darker_background "
    "lighter_background dark_foreground light_foreground bright_foreground red yellow orange "
    "green cyan blue magenta brown bright_red bright_yellow bright_green bright_cyan bright_blue "
    "bright_magenta"
).split()

ANSI = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]

# Semantic key behind each colorN slot (tests/validators/validate-theme.sh).
SLOT_KEYS = (
    "background red green yellow blue magenta cyan foreground muted bright_red bright_green "
    "bright_yellow bright_blue bright_magenta bright_cyan bright_foreground"
).split()

SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$"
)
NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9._+-]*$")

# Top-level entries of a theme checkout that never ship (repo plumbing).
TOP_EXCLUDES = {".git", ".github", ".gitignore", ".gitattributes", "tests", "releases"}

JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__", ".gitleaks.toml", ".gitleaksignore",
              ".directory", ".idea", ".vscode", "node_modules"}
JUNK_PATTERNS = [re.compile(p) for p in (
    r".*~$", r".*\.(bak|orig|rej|swp|swo|swn|tmp|old|pyc|log)$", r"^\.#.*", r"^#.*#$", r"^\._.*",
    r".*\.(tar|tar\.gz|tgz|zip|7z)$", r".*\.sha256$",
)]
# Shipped by some themes, but ignored by Omarchy 4 for git-installed themes
# (OMARCHY-THEME-SPEC.md section 3); everything should live in colors.toml.
GIT_DROPPED = re.compile(r"^(.*\.lua|alacritty\.toml|kitty\.conf|ghostty\.conf|foot\.ini|vscode\.json)$")


def is_junk(base: str) -> bool:
    """True for names that must never ship (also skipped when staging)."""
    return base == ".git" or base in JUNK_NAMES or any(r.match(base) for r in JUNK_PATTERNS)


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"}


# ---------------------------------------------------------------- files
def ship_entries(theme: Path):
    """Yield (relative posix path, Path) for every entry that would ship,
    walking without following symlinks. Directories are yielded too."""
    theme = Path(theme)
    for root, dirs, files in os.walk(theme, followlinks=False):
        rel_root = Path(root).relative_to(theme)
        if rel_root == Path("."):
            dirs[:] = [d for d in dirs if d not in TOP_EXCLUDES]
            files = [f for f in files if f not in TOP_EXCLUDES]
        dirs.sort()
        for name in sorted(dirs) + sorted(files):
            p = Path(root) / name
            yield (rel_root / name).as_posix(), p


def is_symlink_or_special(p: Path) -> str | None:
    st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        return "special file"
    return None


# ---------------------------------------------------------------- colors.toml
def load_colors(theme: Path) -> dict:
    with (Path(theme) / "colors.toml").open("rb") as fh:
        return tomllib.load(fh)


def parse_color(value):
    """Return ((r,g,b) 0-255, alpha 0-1) or None. Same syntax as contrast-check.py."""
    if not isinstance(value, str):
        return None
    v = value.strip().strip("\"'").strip().rstrip(";").strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), a
    m = re.fullmatch(r"0x([0-9a-fA-F]{8})", v)
    if m:
        h = m.group(1)
        return (int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)), int(h[0:2], 16) / 255
    m = re.fullmatch(r"rgba?\(\s*([0-9a-fA-F]{6}|[0-9a-fA-F]{8})\s*\)", v)
    if m:
        return parse_color("#" + m.group(1))
    m = re.fullmatch(r"rgba?\(\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*(?:,\s*([\d.]+%?)\s*)?\)", v)
    if m:
        def ch(s):
            return float(s[:-1]) * 2.55 if s.endswith("%") else float(s)
        rgb = tuple(max(0.0, min(255.0, ch(m.group(i)))) for i in (1, 2, 3))
        a = m.group(4)
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        return rgb, max(0.0, min(1.0, alpha))
    if re.fullmatch(r"[0-9a-fA-F]{6}|[0-9a-fA-F]{8}", v):
        return parse_color("#" + v)
    return None


def relative_luminance(rgb) -> float:
    def lin(c):
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg) -> float:
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg, alpha, bg):
    return tuple(alpha * f + (1 - alpha) * b for f, b in zip(fg, bg))


def rgb_of(value, over=(0, 0, 0)):
    """Opaque (r,g,b) ints for a color string, alpha blended over `over`."""
    c = parse_color(value)
    if not c:
        return None
    rgb, a = c
    if a < 1.0:
        rgb = blend(rgb, a, over)
    return tuple(int(round(x)) for x in rgb)
