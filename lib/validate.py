#!/usr/bin/env python3
"""Step 1: validate an Omarchy 4 theme folder before packaging.

Usage: validate.py <theme-dir> <theme-name> [--emit FILE]

Prints one [PASS]/[WARN]/[FAIL] line per check. With --emit, writes
VERSION=<version> (or VERSION=unknown) to FILE for the orchestrator.
Exit 0 = no FAIL, 1 = at least one FAIL, 2 = usage error.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (GIT_DROPPED, IMAGE_EXT, NAME_RE, PALETTE_KEYS, SEMVER, SLOT_KEYS, VIDEO_EXT,  # noqa: E402
                    is_junk, is_symlink_or_special, parse_color, ship_entries)

PLACEHOLDER = re.compile(r"\{\{\s*(VERSION|THEME_NAME|[A-Z_]+)\s*\}\}")
BIG = 20 * 1024 * 1024


class Checks:
    def __init__(self):
        self.fails = 0
        self.warns = 0

    def ok(self, msg):
        print(f"  [PASS] {msg}")

    def warn(self, msg):
        self.warns += 1
        print(f"  [WARN] {msg}")

    def fail(self, msg):
        self.fails += 1
        print(f"  [FAIL] {msg}")

    def check(self, cond, good, bad):
        (self.ok(good) if cond else self.fail(bad))
        return cond


def first_file(theme: Path, *names):
    for n in names:
        p = theme / n
        if p.is_file() and not p.is_symlink():
            return p
    return None


def check_colors(c: Checks, theme: Path):
    path = theme / "colors.toml"
    if not c.check(path.is_file() and not path.is_symlink(), "colors.toml present", "colors.toml missing"):
        return None
    try:
        with path.open("rb") as fh:
            d = tomllib.load(fh)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        c.fail(f"colors.toml does not parse: {e}")
        return None
    c.ok("colors.toml parses (tomllib)")
    c.check(d.get("mode") in ("dark", "light"), f'mode = "{d.get("mode")}"',
            f'colors.toml mode must be "dark" or "light" (got {d.get("mode")!r})')
    missing = [k for k in PALETTE_KEYS if k not in d]
    c.check(not missing, f"all {len(PALETTE_KEYS)} Omarchy 4 palette keys defined (+ mode)",
            f"missing palette keys: {', '.join(missing)}")
    bad = [k for k in PALETTE_KEYS + ["cursor", "selection_foreground", "selection_background"]
           + [f"color{i}" for i in range(16)] if k in d and not parse_color(d[k])]
    c.check(not bad, "palette values are valid colors", f"unparsable color values: {', '.join(bad)}")
    drift = [f"color{i}" for i, k in enumerate(SLOT_KEYS)
             if f"color{i}" in d and k in d and str(d[f"color{i}"]).lower() != str(d[k]).lower()]
    if drift:
        c.warn(f"colorN differs from its semantic key: {', '.join(drift)}")
    return d


def check_version(c: Checks, theme: Path):
    p = first_file(theme, "VERSION")
    if not p:
        c.fail("VERSION file missing")
        return None
    raw = p.read_text(errors="replace")
    v = raw.strip()
    if not c.check(SEMVER.match(v) and "\n" not in v, f"VERSION {v} is semver",
                   f"VERSION {v!r} is not semver X.Y.Z[-pre][+build]"):
        return None
    return v


def check_changelog(c: Checks, theme: Path, version: str | None):
    p = first_file(theme, "CHANGELOG.md", "CHANGELOG", "CHANGELOG.txt")
    if not c.check(p, f"{p.name if p else 'CHANGELOG'} present", "CHANGELOG.md missing"):
        return
    if not version:
        c.fail("CHANGELOG entry not checked (no valid VERSION)")
        return
    lines = p.read_text(errors="replace").splitlines()
    head = re.compile(r"^(#{1,3})\s*\[?v?" + re.escape(version) + r"\]?(\s|$)")
    for i, line in enumerate(lines):
        m = head.match(line)
        if m:
            body = []
            for nxt in lines[i + 1:]:
                if re.match(r"^#{1,%d}\s" % len(m.group(1)), nxt):
                    break
                if nxt.strip() and not nxt.lstrip().startswith(("#", "<!--")):
                    body.append(nxt)
            c.check(body, f"CHANGELOG has an entry for {version}", f"CHANGELOG heading for {version} has no content")
            return
    c.fail(f"CHANGELOG has no heading for version {version}")


def check_docs(c: Checks, theme: Path, shipped: set[str]):
    readme = first_file(theme, "README.md", "README", "README.txt")
    if c.check(readme and readme.stat().st_size > 0, "README present", "README missing or empty"):
        text = readme.read_text(errors="replace")
        text_nocode = re.sub(r"```.*?```", "", text, flags=re.S)
        targets = re.findall(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)", text_nocode)
        targets += re.findall(r"<img[^>]*\ssrc=[\"']([^\"']+)[\"']", text_nocode)
        broken = []
        for t in targets:
            if re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I) or t.startswith("#"):
                continue
            path = unquote(t.split("#")[0].split("?")[0])
            if not path:
                continue
            rel = os.path.normpath(path)
            if rel.startswith(("/", "..")) or (rel not in shipped and not any(s.startswith(rel + "/") for s in shipped)):
                broken.append(t)
        c.check(not broken, "README local links/images resolve to shipped files",
                f"README links to files that do not ship: {', '.join(sorted(set(broken)))}")
    lic = first_file(theme, "LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md")
    c.check(lic and lic.stat().st_size > 0, f"{lic.name if lic else 'LICENSE'} present", "LICENSE missing or empty")


def check_assets(c: Checks, theme: Path):
    bg = theme / "backgrounds"
    imgs = sorted(p.name for p in bg.iterdir() if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT) \
        if bg.is_dir() and not bg.is_symlink() else []
    c.check(imgs, f"backgrounds/ has {len(imgs)} image(s), default {imgs[0] if imgs else ''}",
            "backgrounds/ missing or has no jpg/png/gif/bmp/webp/video")
    for name in ("install.sh", "uninstall.sh"):
        p = theme / name
        if not p.is_file():
            c.warn(f"{name} missing (Glyphsmith releases ship install.sh/uninstall.sh)")
        elif not os.access(p, os.X_OK):
            c.warn(f"{name} is not executable (it will ship as 0644)")
    for p in [theme / "preview.png"] + sorted((theme / "screenshots").glob("*.png")):
        if p.is_file() and not p.is_symlink():
            with p.open("rb") as fh:
                sig = fh.read(24)
            if sig[:8] != b"\x89PNG\r\n\x1a\n":
                c.fail(f"{p.relative_to(theme)} is not a valid PNG")


def check_tree(c: Checks, theme: Path, name: str):
    shipped: set[str] = set()
    junk, special, dropped, placeholders, big, hidden = [], [], [], [], [], []
    for rel, p in ship_entries(theme):
        kind = is_symlink_or_special(p)
        base = p.name
        if kind:
            special.append(f"{rel} ({kind})")
            continue
        if is_junk(base):
            junk.append(rel)
            continue
        if base.startswith("."):
            hidden.append(rel)
        if p.is_dir():
            continue
        shipped.add(rel)
        if GIT_DROPPED.match(rel):
            dropped.append(rel)
        size = p.stat().st_size
        if size > BIG:
            big.append(f"{rel} ({size // (1024 * 1024)} MiB)")
        if size < 2 * 1024 * 1024 and p.suffix.lower() not in IMAGE_EXT | VIDEO_EXT:
            try:
                m = PLACEHOLDER.search(p.read_text(errors="strict"))
                if m:
                    placeholders.append(f"{rel} ({m.group(0)})")
            except (UnicodeDecodeError, OSError):
                pass
    c.check(NAME_RE.match(name), f"theme name '{name}' is a valid Omarchy name",
            f"theme name '{name}' does not match ^[a-z0-9_][a-z0-9._+-]*$")
    c.check(not special, "no symlinks or special files", f"symlinks/special files: {', '.join(special)}")
    c.check(not junk, "no junk files (.DS_Store, backups, nested .git, archives, caches)",
            f"junk files: {', '.join(junk)}")
    c.check(not placeholders, "no unreplaced {{TEMPLATE}} placeholders",
            f"unreplaced template placeholders: {', '.join(placeholders)}")
    if hidden:
        c.warn(f"hidden files will ship: {', '.join(hidden)}")
    if dropped:
        c.warn(f"ignored by Omarchy 4 for git-installed themes: {', '.join(dropped)}")
    if big:
        c.warn(f"large files: {', '.join(big)}")
    return shipped


def check_git(c: Checks, theme: Path):
    if not (theme / ".git").exists():
        return
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        r = subprocess.run(["git", "-C", str(theme), "status", "--porcelain", "--untracked-files=normal"],
                           capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return
    if r.returncode == 0:
        changed = [ln[3:] for ln in r.stdout.splitlines() if ln.strip()]
        if changed:
            c.warn(f"git checkout has {len(changed)} uncommitted change(s); the working tree is packaged as-is: "
                   + ", ".join(changed[:10]))
        else:
            c.ok("git checkout is clean")


def main(argv):
    emit = None
    if "--emit" in argv:
        i = argv.index("--emit")
        emit = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 2 or not Path(argv[0]).is_dir():
        print(__doc__.strip(), file=sys.stderr)
        return 2
    theme, name = Path(argv[0]), argv[1]
    c = Checks()
    shipped = check_tree(c, theme, name)
    check_colors(c, theme)
    version = check_version(c, theme)
    check_changelog(c, theme, version)
    check_docs(c, theme, shipped)
    check_assets(c, theme)
    check_git(c, theme)
    if emit:
        Path(emit).write_text(f"VERSION={version or 'unknown'}\n")
    print(f"  validation: {c.fails} fail, {c.warns} warn")
    return 1 if c.fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
