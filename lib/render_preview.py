#!/usr/bin/env python3
"""Step 3: render a palette + terminal mock preview headlessly, and check the
theme's own preview assets.

Usage: render_preview.py <theme-dir> <out-dir> <name> <version>

Writes <out-dir>/<name>-<version>-preview.svg (real text, monospace) and
<out-dir>/<name>-<version>-preview.png (Python stdlib only: zlib + struct; text
is drawn as word-shaped bars in the token color). If rsvg-convert or
ImageMagick is on PATH, the SVG is also rasterized to
<name>-<version>-preview.svg.png with real glyphs.

Then checks that the theme ships preview.png (WARN if not; Omarchy's theme
picker shows a blank without it) and lists screenshots/.
Exit 0 = rendered, 1 = could not render (colors.toml unusable) or preview.png is
corrupt, 2 = usage (including a name or version that is not a safe file name).
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import os
import sys
import tomllib
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ANSI, NAME_RE, SEMVER, load_colors, png_info, rgb_of  # noqa: E402

W, H = 1200, 720
FONT = 18
CH = FONT * 0.6  # monospace advance


def hexrgb(rgb):
    return "#%02x%02x%02x" % rgb


def layout(d: dict, name: str, version: str):
    """Return (rects, texts). rect = (x, y, w, h, rgb); text = (x, baseline, str, rgb, size)."""
    bg = rgb_of(d["background"])

    def c(*keys):
        for k in keys:
            v = rgb_of(d.get(k), over=bg) if d.get(k) else None
            if v:
                return v
        return rgb_of(d["foreground"])

    rects, texts = [], []
    fg = c("foreground")
    rects.append((0, 0, W, H, bg))
    # header
    rects.append((0, 0, W, 64, c("darker_background", "dark_background", "background")))
    rects.append((0, 64, W, 4, c("accent")))
    texts.append((32, 42, f"{name} {version}", c("bright_foreground", "foreground"), 26))
    texts.append((W - 32 - 15 * 0.6 * 16, 40, f"mode: {d.get('mode', '?')}", c("muted"), 16))

    # terminal window
    tx, ty, tw, th = 32, 96, 760, 440
    rects.append((tx - 2, ty - 2, tw + 4, th + 4, c("accent")))
    rects.append((tx, ty, tw, th, bg))
    rects.append((tx, ty, tw, 32, c("dark_background", "lighter_background")))
    for i, k in enumerate(("red", "yellow", "green")):
        rects.append((tx + 14 + i * 22, ty + 10, 12, 12, c(k)))
    texts.append((tx + 90, ty + 22, f"~/.config/omarchy/themes/{name}", c("dark_foreground", "muted"), 14))

    lines = [
        [("$", "green"), ("omarchy-theme-set", "foreground"), (name, "yellow")],
        [("Theme", "foreground"), ("applied:", "foreground"), (name, "accent")],
        [("$", "green"), ("git", "foreground"), ("status", "blue"), ("--short", "magenta")],
        [("On", "foreground"), ("branch", "foreground"), ("main", "cyan")],
        [("M", "red"), ("colors.toml", "red")],
        [("A", "green"), ("preview.png", "green")],
        [("$", "green"), ("ls", "foreground"), ("-la", "magenta")],
        [("backgrounds/", "blue"), ("install.sh", "green"), ("README.md", "foreground"), ("VERSION", "muted")],
        [("#", "muted"), ("forged", "muted"), ("at", "muted"), (f"v{version}", "bright_yellow")],
        [("warning:", "yellow"), ("error:", "red"), ("ok:", "bright_green"), ("info:", "bright_blue")],
    ]
    y = ty + 70
    for toks in lines:
        x = tx + 20
        for text, key in toks:
            texts.append((x, y, text, c(key), FONT))
            x += (len(text) + 1) * CH
        y += 30
    # selection line
    sel_bg = c("selection_background", "selection")
    sel_fg = c("selection_foreground", "foreground")
    sel = "selected text on selection"
    rects.append((tx + 20 - 4, y - FONT, len(sel) * CH + 8, FONT + 8, sel_bg))
    texts.append((tx + 20, y, sel, sel_fg, FONT))
    y += 30
    texts.append((tx + 20, y, "$", c("green"), FONT))
    rects.append((tx + 20 + 2 * CH, y - FONT + 2, CH, FONT + 4, c("cursor", "foreground")))

    # semantic palette column
    px, py = 830, 96
    keys = ["background", "dark_background", "darker_background", "lighter_background", "foreground",
            "dark_foreground", "light_foreground", "bright_foreground", "accent", "muted", "selection",
            "orange", "brown", "cursor"]
    for i, k in enumerate(keys):
        if not d.get(k):
            continue
        yy = py + i * 31
        rects.append((px - 1, yy - 1, 42, 26, c("lighter_background", "muted")))
        rects.append((px, yy, 40, 24, c(k)))
        texts.append((px + 52, yy + 18, f"{k}", fg, 15))
        texts.append((px + 52 + 20 * 0.6 * 15, yy + 18, str(d[k])[:9], c("dark_foreground", "muted"), 15))

    # ANSI swatches
    sx, sy, sw, sh = 32, 568, 136, 56
    for row in range(2):
        for i, n in enumerate(ANSI):
            key = f"color{i + 8 * row}"
            fallback = n if row == 0 else f"bright_{n}"
            val = d.get(key) or d.get(fallback)
            col = rgb_of(val, over=bg) if val else None
            x = sx + i * (sw + 6)
            yy = sy + row * (sh + 6)
            rects.append((x, yy, sw, sh, col or bg))
            if col:
                label_rgb = bg if sum(col) > 382 else rgb_of("#ffffff")
                texts.append((x + 8, yy + 34, f"{key} {str(val)[:7]}", label_rgb, 14))
    return rects, texts


def safe_write(path: Path, data: bytes) -> None:
    """Write via a fresh temp file renamed into place; refuse a symlink (or non-file) destination."""
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError(f"refusing to write through {path}: it is a symlink or not a regular file")
    if path.parent.is_symlink():
        raise OSError(f"refusing to write into {path.parent}: it is a symlink")
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_svg(path: Path, rects, texts):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    for x, y, w, h, rgb in rects:
        out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" fill="{hexrgb(rgb)}"/>')
    for x, y, s, rgb, size in texts:
        out.append(f'<text x="{x:.0f}" y="{y:.0f}" fill="{hexrgb(rgb)}" font-size="{size}" '
                   f'font-family="JetBrains Mono, DejaVu Sans Mono, monospace" xml:space="preserve">{escape(s)}</text>')
    out.append("</svg>")
    safe_write(path, ("\n".join(out) + "\n").encode("utf-8"))


def write_png(path: Path, rects, texts):
    rows = [bytearray(W * 3) for _ in range(H)]

    def fill(x, y, w, h, rgb):
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1, y1 = min(W, int(round(x + w))), min(H, int(round(y + h)))
        if x1 <= x0 or y1 <= y0:
            return
        span = bytes(rgb) * (x1 - x0)
        for yy in range(y0, y1):
            rows[yy][x0 * 3:x1 * 3] = span

    for r in rects:
        fill(*r)
    # Text as word bars: one bar per word, x-height tall, sitting on the baseline.
    for x, y, s, rgb, size in texts:
        adv = size * 0.6
        cx = x
        for word in s.split(" "):
            if word:
                fill(cx, y - size * 0.62, len(word) * adv - 1, size * 0.62, rgb)
            cx += (len(word) + 1) * adv

    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    safe_write(path, png)


def rasterize_svg(svg: Path) -> Path | None:
    dst = svg.with_suffix(".svg.png")
    if dst.is_symlink() or (dst.exists() and not dst.is_file()):
        return None
    for cmd in (["rsvg-convert", "-o", str(dst), str(svg)], ["magick", str(svg), str(dst)],
                ["convert", str(svg), str(dst)]):
        if shutil.which(cmd[0]):
            try:
                if subprocess.run(["timeout", "120", "nice", "-n", "10", *cmd], capture_output=True).returncode == 0:
                    return dst
            except OSError:
                pass
    return None


def main(argv) -> int:
    if len(argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    theme, out, name, version = Path(argv[0]), Path(argv[1]), argv[2], argv[3]
    # name and version become file names under out-dir: refuse anything that could escape it.
    if not NAME_RE.match(name) or not (version == "unknown" or SEMVER.match(version)):
        print(f"render_preview.py: unsafe name or version: {name!r} {version!r}", file=sys.stderr)
        return 2
    rc = 0
    try:
        d = load_colors(theme)
        if not (rgb_of(d.get("background")) and rgb_of(d.get("foreground"))):
            raise ValueError("background/foreground missing or unparsable")
        out.mkdir(parents=True, exist_ok=True)
        rects, texts = layout(d, name, version)
        svg = out / f"{name}-{version}-preview.svg"
        png = out / f"{name}-{version}-preview.png"
        write_svg(svg, rects, texts)
        write_png(png, rects, texts)
        print(f"  [PASS] rendered {svg}")
        print(f"  [PASS] rendered {png} ({W}x{H}, stdlib)")
        r = rasterize_svg(svg)
        if r:
            print(f"  [PASS] rasterized {r}")
    except (OSError, ValueError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        print(f"  [FAIL] could not render a preview: {e}")
        rc = 1

    pv = theme / "preview.png"
    if pv.is_file() and not pv.is_symlink():
        try:
            w, h = png_info(pv)
            print(f"  [PASS] theme ships preview.png ({w}x{h})")
        except (OSError, ValueError) as e:
            print(f"  [FAIL] theme preview.png is not a valid PNG: {e}")
            rc = 1
    else:
        print("  [WARN] theme has no preview.png (the Omarchy theme picker shows a blank)")
    shots = sorted(p.name for p in (theme / "screenshots").glob("*")
                   if p.is_file()) if (theme / "screenshots").is_dir() else []
    if shots:
        print(f"  [PASS] screenshots/: {', '.join(shots)}")
    else:
        print("  [INFO] no screenshots/ folder")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
