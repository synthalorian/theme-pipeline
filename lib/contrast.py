#!/usr/bin/env python3
"""Step 2: WCAG 2.x contrast of the key colors.toml pairs.

Usage: contrast.py <theme-dir>

Enforced (FAIL below the minimum):
  text 4.5:1  foreground on background, selection text on selection
              background, normal ANSI colors color1-color7 on background.
              color0 (black) is a background shade by convention in both
              modes (black/white are swapped on light themes), and color7 is
              treated as one too when it is within 1.5:1 of the background;
              those are listed as INFO, not judged
  ui   3.0:1  cursor on background, accent on background (WCAG 1.4.11)
Advisory (WARN only):
  text 4.5:1  bright ANSI colors, dark/light/bright_foreground on background
  ui   3.0:1  muted (comments, inactive text) on background

The math and the background-shade rule follow WCAG 2.x (see common.py).
Exit 0 = no FAIL, 1 = at least one FAIL, 2 = colors.toml missing/unparsable.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ANSI, SLOT_KEYS, contrast_ratio, load_colors, parse_color, rgb_of  # noqa: E402

TEXT_MIN = 4.5
UI_MIN = 3.0
SHADE_MAX = 1.5


def ratio(fg: str, bg: str) -> float | None:
    b = rgb_of(bg)
    f = parse_color(fg)
    if not b or not f:
        return None
    frgb = rgb_of(fg, over=b)  # translucent fg is blended over its background
    return contrast_ratio(frgb, b)


def pairs(d: dict):
    """Yield (label, fg, bg, minimum, enforced)."""
    bg, fg = d.get("background"), d.get("foreground")
    yield "foreground on background", fg, bg, TEXT_MIN, True
    if d.get("selection_foreground") and d.get("selection_background"):
        yield "selection_foreground on selection_background", d["selection_foreground"], d["selection_background"], \
            TEXT_MIN, True
    elif d.get("selection"):
        yield "foreground on selection", fg, d["selection"], TEXT_MIN, True
    if d.get("cursor"):
        yield "cursor on background", d["cursor"], bg, UI_MIN, True
    if d.get("accent"):
        yield "accent on background", d["accent"], bg, UI_MIN, True

    seen = set()
    for i, n in enumerate(ANSI):
        for key in (f"color{i}", n if n not in ("black", "white") else SLOT_KEYS[i]):
            val = d.get(key)
            if not val or (i, str(val).lower()) in seen:
                continue
            seen.add((i, str(val).lower()))
            r = ratio(val, bg)
            label = f"color{i} {n} ({key}) on background" if key != f"color{i}" else f"color{i} {n} on background"
            if i == 0 or (i == 7 and r is not None and r < SHADE_MAX):
                yield label, val, bg, None, False
            else:
                yield label, val, bg, TEXT_MIN, True
    for i, n in enumerate(ANSI):
        val = d.get(f"color{i + 8}") or d.get(f"bright_{n}")
        if val and i not in (0, 7):
            yield f"color{i + 8} bright_{n} on background", val, bg, TEXT_MIN, False
    for key in ("dark_foreground", "light_foreground", "bright_foreground"):
        if d.get(key):
            yield f"{key} on background", d[key], bg, TEXT_MIN, False
    if d.get("muted"):
        yield "muted on background", d["muted"], bg, UI_MIN, False


def main(argv) -> int:
    if len(argv) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        d = load_colors(Path(argv[0]))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        print(f"  [FAIL] cannot read colors.toml: {e}")
        return 2
    if not d.get("background") or not d.get("foreground"):
        print("  [FAIL] colors.toml needs background and foreground")
        return 1
    fails = warns = 0
    for label, fg, bg, need, enforced in pairs(d):
        r = ratio(fg, bg)
        if r is None:
            print(f"  [FAIL] {label}: unparsable color ({fg!r} on {bg!r})")
            fails += 1
            continue
        detail = f"{r:5.2f}:1  {label}  {fg} on {bg}"
        if need is None:
            print(f"  [INFO] {detail}  (background-shade slot, not judged)")
        elif r >= need:
            print(f"  [PASS] {detail}  (>= {need}:1)")
        elif enforced:
            print(f"  [FAIL] {detail}  (< {need}:1)")
            fails += 1
        else:
            print(f"  [WARN] {detail}  (< {need}:1, advisory)")
            warns += 1
    print(f"  contrast: {fails} fail, {warns} warn")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
