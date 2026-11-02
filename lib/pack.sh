#!/usr/bin/env bash
# Build a byte-reproducible <out>.tar.gz plus <out>.tar.gz.sha256 from a
# staged folder, with the same tar/gzip settings as Glyphsmith's
# tools/pack/repro-tar.sh and templates/build-release.sh (the Steel & Ember
# release): one top-level folder, entries sorted by name, every mtime =
# SOURCE_DATE_EPOCH, owner/group 0/0 numeric, normalized modes, GNU format,
# gzip -n --best (no name, no timestamp).
#
# Usage: pack.sh <stage-dir> <name> <out.tar.gz> <epoch>
#   <stage-dir>/<name>/ is archived as <name>/.
set -euo pipefail
export LC_ALL=C TZ=UTC
# Inherited options would change the archive bytes.
unset TAR_OPTIONS GZIP GZIP_OPT

die() { printf 'pack.sh: %s\n' "$*" >&2; exit 1; }
[[ $# -eq 4 ]] || die "usage: pack.sh <stage-dir> <name> <out.tar.gz> <epoch>"
stage=$1 name=$2 out=$3 epoch=$4
[[ -d $stage/$name ]] || die "not a directory: $stage/$name"
[[ $out == *.tar.gz ]] || die "output must end in .tar.gz"
[[ $epoch =~ ^[0-9]{1,11}$ ]] && (( 10#$epoch <= 4102444800 )) || die "bad SOURCE_DATE_EPOCH: $epoch"
[[ $(tar --version 2>/dev/null) == *'GNU tar'* ]] || die "GNU tar is required"

out_dir=$(cd -- "$(dirname -- "$out")" && pwd -P)
base=$(basename -- "$out")
tmp=$(mktemp -- "$out_dir/.$base.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT

tar --create --file=- \
    --directory="$stage" \
    --sort=name \
    --format=gnu \
    --mtime="@$((10#$epoch))" \
    --owner=0 --group=0 --numeric-owner \
    --mode='a-st,u=rwX,go=rX' \
    --no-acls --no-xattrs --no-selinux \
    -- "$name" \
    | gzip --stdout --no-name --best >"$tmp"
chmod 644 -- "$tmp"
mv -fT -- "$tmp" "$out_dir/$base"
(cd -- "$out_dir" && sha256sum -- "$base") >"$out_dir/$base.sha256"
chmod 644 -- "$out_dir/$base.sha256"
