#!/usr/bin/env python3
"""Copy the files of a theme that ship into <stage-dir>/<name>/.

Usage: stage.py <theme-dir> <stage-dir> <name>

Skips repo plumbing at the top level (.git, .github, .gitignore,
.gitattributes, tests, releases), junk files and symlinks/special files, so
the archive never contains them even if validation was overridden. Modes are
normalized: directories and executables 0755, everything else 0644.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import is_junk, is_symlink_or_special, ship_entries  # noqa: E402


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    theme, stage, name = Path(argv[0]), Path(argv[1]), argv[2]
    dest = stage / name
    dest.mkdir(parents=True)
    skipped_dirs: list[str] = []
    count = 0
    for rel, p in ship_entries(theme):
        if any(rel == s or rel.startswith(s + "/") for s in skipped_dirs):
            continue
        if is_symlink_or_special(p) or is_junk(p.name):
            if p.is_dir() and not p.is_symlink():
                skipped_dirs.append(rel)
            print(f"  skipped {rel}", file=sys.stderr)
            continue
        target = dest / rel
        if p.is_dir():
            target.mkdir(exist_ok=True)
            os.chmod(target, 0o755)
        else:
            shutil.copyfile(p, target, follow_symlinks=False)
            os.chmod(target, 0o755 if os.access(p, os.X_OK) else 0o644)
            count += 1
    os.chmod(dest, 0o755)
    print(f"  staged {count} files into {name}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
