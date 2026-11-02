#!/usr/bin/env python3
"""Verify a release tarball has the Steel & Ember release shape.

Usage: verify_archive.py <name>-<ver>.tar.gz <name> <epoch> [<stage-dir>]

Checks: .sha256 file matches; gzip header has no name and mtime 0; one
top-level folder named <name>; no absolute paths, '..', links or devices;
uid/gid 0 with no user/group names; every mtime == epoch; modes 0644/0755;
entries in sorted order; colors.toml at <name>/colors.toml; nothing from .git,
.github or tests; with <stage-dir>, the file list and bytes match the stage.
Exit 0 = all good, 1 = problems found, 2 = usage.
"""
from __future__ import annotations

import hashlib
import sys
import tarfile
from pathlib import Path


def main(argv) -> int:
    if len(argv) not in (3, 4):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    tgz, name, epoch = Path(argv[0]), argv[1], int(argv[2])
    stage = Path(argv[3]) / name if len(argv) == 4 else None
    problems: list[str] = []

    data = tgz.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    sumfile = tgz.with_name(tgz.name + ".sha256")
    if not sumfile.is_file() or sumfile.read_text() != f"{digest}  {tgz.name}\n":
        problems.append(f"{sumfile.name} missing or does not match")
    flags, mtime = data[3], int.from_bytes(data[4:8], "little")
    if data[:2] != b"\x1f\x8b" or flags & 0x08 or mtime != 0:
        problems.append("gzip header carries a file name or timestamp (not gzip -n)")

    with tarfile.open(tgz, "r:gz") as tf:
        members = tf.getmembers()
        names = [m.name for m in members]
        for m in members:
            n = m.name
            if n.startswith("/") or ".." in n.split("/"):
                problems.append(f"unsafe path: {n}")
            if n != name and not n.startswith(name + "/"):
                problems.append(f"outside top-level folder {name}/: {n}")
            if not (m.isfile() or m.isdir()):
                problems.append(f"not a regular file or directory: {n}")
            if m.uid or m.gid or m.uname or m.gname:
                problems.append(f"owner not 0/0 numeric: {n} ({m.uid}:{m.gid} {m.uname!r}:{m.gname!r})")
            if m.mtime != epoch:
                problems.append(f"mtime {m.mtime} != {epoch}: {n}")
            if m.mode not in (0o644, 0o755) or (m.isdir() and m.mode != 0o755):
                problems.append(f"mode {oct(m.mode)}: {n}")
            parts = n.split("/")
            if len(parts) > 1 and parts[1] in (".git", ".github", "tests"):
                problems.append(f"excluded path shipped: {n}")
        # tar --sort=name: siblings sorted bytewise within each directory.
        by_dir: dict[str, list[str]] = {}
        for n in names:
            parent, _, leaf = n.rstrip("/").rpartition("/")
            by_dir.setdefault(parent, []).append(leaf)
        for parent, leaves in by_dir.items():
            if leaves != sorted(leaves, key=lambda s: s.encode()):
                problems.append(f"entries not sorted in {parent or '/'}")
        if f"{name}/colors.toml" not in names:
            problems.append(f"{name}/colors.toml missing")
        if stage is not None:
            want = {p.relative_to(stage.parent).as_posix(): p for p in stage.rglob("*")}
            want[name] = stage
            got = set(names)
            if set(want) != got:
                problems.append(f"archive vs stage differ: extra {sorted(got - set(want))[:5]}, "
                                f"missing {sorted(set(want) - got)[:5]}")
            for m in members:
                if m.isfile() and m.name in want:
                    if tf.extractfile(m).read() != want[m.name].read_bytes():
                        problems.append(f"content differs from stage: {m.name}")

    for p in problems[:40]:
        print(f"  [FAIL] {p}")
    if not problems:
        files = sum(1 for m in members if m.isfile())
        print(f"  [PASS] archive shape OK: {len(members)} entries ({files} files) under {name}/, "
              f"sorted, mtime @{epoch}, 0/0, modes 644/755, gzip -n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
