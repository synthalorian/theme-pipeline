"""Shared helpers for the theme release pipeline (stdlib only).

Color math follows the WCAG 2.x definition (WCAG 2.x relative
luminance and contrast ratio, same color syntax), so ratios printed here match
the QA tool for the same colors.
"""
from __future__ import annotations

import os
import re
import stat
import struct
import tomllib
import zlib
from pathlib import Path

# The 25 Omarchy 4 palette keys a complete theme defines (same list as
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

# SemVer 2.0.0: numeric identifiers (including numeric prerelease identifiers)
# have no leading zeros; build metadata identifiers may.
_PRE_ID = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    rf"(-{_PRE_ID}(\.{_PRE_ID})*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?\Z"
)
# Also checked by bin/theme-release before anything is written: no "/", no leading ".".
NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9._+-]*\Z")

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


# ---------------------------------------------------------------- PNG
PNG_SIG = b"\x89PNG\r\n\x1a\n"
# color type -> (allowed bit depths, channels)
_PNG_TYPES = {0: ((1, 2, 4, 8, 16), 1), 2: ((8, 16), 3), 3: ((1, 2, 4, 8), 1), 4: ((8, 16), 2), 6: ((8, 16), 4)}
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def png_info(path) -> tuple[int, int]:
    """Fully check a PNG file and return (width, height); raise ValueError if it is corrupt.

    Checks the signature, the chunk structure (lengths, every CRC, IHDR first,
    PLTE before IDAT when required, consecutive IDATs, IEND last with nothing
    after it), the IHDR fields, and that the zlib image data decompresses to
    exactly the expected scanlines with valid filter bytes."""
    data = Path(path).read_bytes()
    if data[:8] != PNG_SIG:
        raise ValueError("bad PNG signature")
    i, n, ihdr, idat, plte, seen_idat, idat_done, ended = 8, len(data), None, [], False, False, False, False
    while i < n:
        if ended:
            raise ValueError("data after IEND")
        if i + 12 > n:
            raise ValueError("truncated chunk header")
        length, tag = struct.unpack(">I4s", data[i:i + 8])
        if length > 0x7FFFFFFF or i + 12 + length > n:
            raise ValueError(f"truncated {tag!r} chunk")
        body = data[i + 8:i + 8 + length]
        (crc,) = struct.unpack(">I", data[i + 8 + length:i + 12 + length])
        if zlib.crc32(tag + body) & 0xFFFFFFFF != crc:
            raise ValueError(f"CRC mismatch in {tag.decode('latin-1')!r} chunk")
        if ihdr is None and tag != b"IHDR":
            raise ValueError("first chunk is not IHDR")
        if tag == b"IHDR":
            if ihdr is not None or length != 13:
                raise ValueError("bad IHDR")
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            if plte or seen_idat or length == 0 or length % 3 or length > 768:
                raise ValueError("bad PLTE chunk (duplicate, after IDAT, or bad length)")
            if ihdr[3] in (0, 4):
                raise ValueError("PLTE not allowed for greyscale images")
            plte = True
        elif tag == b"IDAT":
            if idat_done:
                raise ValueError("IDAT chunks are not consecutive")
            seen_idat = True
            idat.append(body)
        elif tag == b"IEND":
            ended = True
        if seen_idat and tag != b"IDAT":
            idat_done = True
        i += 12 + length
    if not ended:
        raise ValueError("missing IEND (truncated file)")
    if not idat:
        raise ValueError("no IDAT chunk")
    w, h, depth, ctype, comp, filt, interlace = ihdr
    if not (0 < w <= 0x7FFFFFFF and 0 < h <= 0x7FFFFFFF):
        raise ValueError(f"bad dimensions {w}x{h}")
    if ctype not in _PNG_TYPES or depth not in _PNG_TYPES[ctype][0] or comp or filt or interlace not in (0, 1):
        raise ValueError("bad IHDR fields")
    if ctype == 3 and not plte:
        raise ValueError("palette image without PLTE")
    bits = depth * _PNG_TYPES[ctype][1]
    passes = [(w, h)] if interlace == 0 else [
        ((w - x0 + dx - 1) // dx, (h - y0 + dy - 1) // dy) for x0, y0, dx, dy in _ADAM7]
    rows = [(1 + (pw * bits + 7) // 8, ph) for pw, ph in passes if pw and ph]
    # Stream the decompression so a large image (or a zlib bomb) is never held in memory.
    d, buf, z = zlib.decompressobj(), b"", b"".join(idat)
    try:
        for stride, count in rows:
            for _ in range(count):
                while len(buf) < stride:
                    inp, z = d.unconsumed_tail or z, b""
                    out = b"" if d.eof else d.decompress(inp, stride * 64)
                    if not out:
                        raise ValueError("image data is shorter than the image")
                    buf += out
                if buf[0] > 4:
                    raise ValueError(f"bad scanline filter type {buf[0]}")
                buf = buf[stride:]
        if buf or d.decompress(d.unconsumed_tail or z, 1) or not d.eof:
            raise ValueError("image data is longer than the image or the zlib stream is incomplete")
    except zlib.error as e:
        raise ValueError(f"image data does not decompress: {e}") from None
    return w, h


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
