#!/usr/bin/env bash
# Test suite for theme-pipeline. Builds throwaway copies of the fixture themes
# under a temp dir, mutates them into bad themes, and checks every step.
# Usage: tests/run-tests.sh   (exit 0 = all assertions passed)
# Set SKIP_REAL_GATE=1 to skip the tests that call the real gate ($REAL_GATE, default
# /workspace/pre-publish-gate/gate.sh); they also skip when it is missing.
set -uo pipefail
export LC_ALL=C TZ=UTC
REAL_GATE=${REAL_GATE:-/workspace/pre-publish-gate/gate.sh}
unset SOURCE_DATE_EPOCH GATE_NAME_PATTERN_FILE GATE RELEASE_GIT_NAME RELEASE_GIT_EMAIL

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
FIX=$ROOT/tests/fixtures
LIB=$ROOT/lib
REL=$ROOT/bin/theme-release
# Every fixture copy and stub lives under $T: never continue without it (an empty T
# would turn "$T/themes" and "$T/bin" into /themes and /bin).
T=$(mktemp -d "${TMPDIR:-/tmp}/theme-pipeline-test.XXXXXX") && [[ -n $T && -d $T ]] \
  || { echo "run-tests.sh: cannot create a temp dir under ${TMPDIR:-/tmp}; aborting" >&2; exit 2; }
trap 'rm -rf -- "$T"' EXIT

pass=0 fail=0
ok() { pass=$((pass + 1)); printf '  ok    %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '  FAIL  %s\n' "$1"; [[ -n ${2:-} ]] && printf '%s\n' "$2" | tail -25 | sed 's/^/        | /'; }
# expect <desc> <expected-rc> <cmd...>; output in $OUTPUT
expect() {
  local desc=$1 want=$2; shift 2
  OUTPUT=$("$@" 2>&1); local rc=$?
  if [[ $rc == "$want" ]]; then ok "$desc"; else bad "$desc (rc $rc, want $want)" "$OUTPUT"; fi
}
has() { if grep -qE -- "$2" <<<"$OUTPUT"; then ok "$1"; else bad "$1 (no /$2/)" "$OUTPUT"; fi; }
hasnt() { if grep -qE -- "$2" <<<"$OUTPUT"; then bad "$1 (found /$2/)" "$OUTPUT"; else ok "$1"; fi; }
copy() { rm -rf -- "$T/themes/$2"; mkdir -p "$T/themes"; cp -a -- "$FIX/$1" "$T/themes/$2"; printf '%s' "$T/themes/$2"; }

# Stub gates: record the repo they were given, check its commit identity.
mkdir -p "$T/bin"
cat >"$T/bin/gate-pass.sh" <<'EOF'
#!/usr/bin/env bash
ae=$(git -C "$1" log --format='%ae %ce' | sort -u)
echo "stub gate on $(basename "$1"): identity [$ae] files $(git -C "$1" ls-files | wc -l)"
[[ $ae == "212206047+synthalorian@users.noreply.github.com 212206047+synthalorian@users.noreply.github.com" ]] || exit 1
echo "GATE: PASS"
EOF
printf '#!/usr/bin/env bash\necho "[FAIL] stub check"\necho "GATE: FAIL"\nexit 1\n' >"$T/bin/gate-fail.sh"
chmod +x "$T/bin/"*.sh

echo "== unit: contrast math and color parsing"
expect "white on black is 21:1" 0 python3 -I -c "
import sys; sys.path.insert(0, '$LIB')
from common import contrast_ratio, parse_color, rgb_of
assert round(contrast_ratio((255,255,255),(0,0,0)), 2) == 21.0
assert round(contrast_ratio((119,119,119),(255,255,255)), 2) == 4.48
assert parse_color('#abc')[0] == (170,187,204)
assert parse_color('rgba(f28c38ee)')[1] == 0xee/255
assert rgb_of('#ffffff80', over=(0,0,0)) == (128,128,128)
assert parse_color('nope') is None"

echo "== unit: strict semver"
expect "semver accepts valid and rejects leading-zero prerelease ids" 0 python3 -I -c "
import sys; sys.path.insert(0, '$LIB')
from common import SEMVER
for v in ('1.2.3', '0.1.1', '1.2.3-0', '1.2.3-0a', '1.2.3-01a', '2.0.0-rc.1', '1.0.0-alpha.0.x+001', '1.2.3+0001'):
    assert SEMVER.match(v), v
for v in ('1.2.3-01', '1.2.3-rc.01', '1.2.3-00', '01.2.3', '1.2', 'v1.2.3', '1.2.3-', '1.2.3-a..b', '1.2.3\\n'):
    assert not SEMVER.match(v), v"

echo "== unit: PNG structure checks"
# pngcase <desc> <python expr producing the bytes, using good/chunk/sig> <expected error regex>
PNGLIB="import sys, struct, zlib; sys.path.insert(0, '$LIB')
from common import png_info
def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
sig = bytes.fromhex('89504e470d0a1a0a')
ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 2, 8, 2, 0, 0, 0))
good = sig + ihdr + chunk(b'IDAT', zlib.compress(b''.join(b'\\x00' + bytes(12) for _ in range(2)))) + chunk(b'IEND', b'')"
expect "a well-formed 4x2 PNG is accepted" 0 python3 -I -c "$PNGLIB
open('$T/ok.png', 'wb').write(good); assert png_info('$T/ok.png') == (4, 2)"
pngcase() {
  expect "rejects $1" 0 python3 -I -c "$PNGLIB
open('$T/bad.png', 'wb').write($2)
try: png_info('$T/bad.png')
except ValueError as e: print(e)
else: raise SystemExit('accepted')"
  has "$1: reason given" "$3"
}
pngcase "24-byte signature+IHDR stub" "sig + ihdr[:16]" 'truncated'
pngcase "IHDR without IDAT/IEND" "sig + ihdr" 'missing IEND'
pngcase "bad chunk CRC" "good[:-20] + bytes([good[-20] ^ 1]) + good[-19:]" 'CRC mismatch'
pngcase "missing IEND" "good[:-12]" 'missing IEND'
pngcase "data after IEND" "good + b'junk'" 'after IEND'
pngcase "no IDAT" "sig + ihdr + chunk(b'IEND', b'')" 'no IDAT'
pngcase "short image data" "sig + ihdr + chunk(b'IDAT', zlib.compress(b'\\x00' + bytes(12))) + chunk(b'IEND', b'')" 'shorter'
pngcase "long image data" "sig + ihdr + chunk(b'IDAT', zlib.compress(bytes(13 * 3))) + chunk(b'IEND', b'')" 'longer'
pngcase "non-zlib image data" "sig + ihdr + chunk(b'IDAT', b'not zlib at all') + chunk(b'IEND', b'')" 'does not decompress'
pngcase "bad filter byte" "sig + ihdr + chunk(b'IDAT', zlib.compress(b'\\x07' + bytes(12) + b'\\x00' + bytes(12))) + chunk(b'IEND', b'')" 'filter type'
pngcase "zero width" "sig + chunk(b'IHDR', struct.pack('>IIBBBBB', 0, 2, 8, 2, 0, 0, 0)) + good[33:]" 'bad dimensions'
pngcase "bad bit depth" "sig + chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 2, 3, 2, 0, 0, 0)) + good[33:]" 'bad IHDR fields'
pngcase "palette image without PLTE" "sig + chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 2, 8, 3, 0, 0, 0)) + good[33:]" 'without PLTE'
expect "real fixture PNGs are accepted" 0 python3 -I -c "
import sys, glob; sys.path.insert(0, '$LIB')
from common import png_info
fs = glob.glob('$FIX/*/preview.png') + glob.glob('$FIX/*/backgrounds/*.png')
assert fs and all(png_info(f) for f in fs)"

echo "== good fixtures: validate + contrast"
for t in testforge daylight; do
  expect "$t validates" 0 python3 -I "$LIB/validate.py" "$FIX/$t" "$t"
  hasnt "$t has no FAIL lines" '\[FAIL\]'
  expect "$t passes contrast" 0 python3 -I "$LIB/contrast.py" "$FIX/$t"
done
has "daylight color0 is a background-shade slot" 'INFO.*color0'

echo "== bad fixture: low contrast"
expect "lowcontrast fails contrast" 1 python3 -I "$LIB/contrast.py" "$FIX/lowcontrast"
has "fg/bg below 4.5 reported" 'FAIL.*foreground on background'
has "cursor below 3.0 reported" 'FAIL.*cursor on background'
has "red below 4.5 reported" 'FAIL.*color1 red'
has "selection pair reported" 'FAIL.*selection_foreground on selection_background'

echo "== bad fixtures: validation mutations"
# mutate <case> <expected message regex> <shell snippet run inside the theme copy>
mutate() {
  local name=$1 msg=$2 snippet=$3 dir
  dir=$(copy testforge "$name")
  (cd "$dir" && eval "$snippet")
  expect "$name: validation fails" 1 python3 -I "$LIB/validate.py" "$dir" testforge
  has "$name: reports /$msg/" "$msg"
}
mutate missing-key 'missing palette keys: brown' "sed -i '/^brown/d' colors.toml"
mutate no-mode 'mode must be' "sed -i '/^mode/d' colors.toml"
mutate bad-mode 'mode must be' "sed -i 's/^mode = .*/mode = \"dim\"/' colors.toml"
mutate toml-syntax 'does not parse' "echo 'broken = [' >> colors.toml"
mutate bad-color 'unparsable color values: red' "sed -i 's/^red = .*/red = \"crimson\"/' colors.toml"
mutate no-colors 'colors.toml missing' "rm colors.toml"
mutate bad-version 'not semver' "echo 1.2 > VERSION"
mutate v-prefix 'not semver' "echo v1.2.0 > VERSION"
mutate no-version 'VERSION file missing' "rm VERSION"
mutate no-entry 'no heading for version 1.2.1' "echo 1.2.1 > VERSION"
mutate empty-entry 'has no content' "python3 -c \"import re;p='CHANGELOG.md';s=open(p).read();open(p,'w').write(s.replace('### Added\n- Fixture release for the pipeline tests.\n',''))\""
mutate no-changelog 'CHANGELOG.md missing' "rm CHANGELOG.md"
mutate no-readme 'README missing' "rm README.md"
mutate no-license 'LICENSE missing' "rm LICENSE"
mutate empty-license 'LICENSE missing or empty' ": > LICENSE"
mutate ds-store 'junk files: .DS_Store' "touch .DS_Store"
mutate backup 'junk files: colors.toml~' "cp colors.toml colors.toml~"
mutate bak 'junk files: backgrounds/old.png.bak' "cp backgrounds/1-plain.png backgrounds/old.png.bak"
mutate swap 'junk files: .README.md.swp' "touch .README.md.swp"
mutate nested-git 'junk files: backgrounds/.git' "mkdir -p backgrounds/.git"
mutate gitleaks-cfg 'junk files: .gitleaks.toml' "touch .gitleaks.toml"
mutate symlink 'symlinks/special files: preview2.png \(symlink\)' "ln -s preview.png preview2.png"
mutate dir-symlink 'symlinks/special files: extra \(symlink\)' "ln -s backgrounds extra"
mutate placeholder 'unreplaced template placeholders: README.md' "echo 'Version {{VERSION}}' >> README.md"
mutate broken-link 'README links to files that do not ship: screenshots/missing.png' "echo '![x](screenshots/missing.png)' >> README.md"
mutate escaping-link 'do not ship: ../outside.png' "echo '![x](../outside.png)' >> README.md"
mutate no-backgrounds 'backgrounds/ missing' "rm -r backgrounds"
mutate bad-png 'preview.png is not a valid PNG' "echo notpng > preview.png"
mutate fake-png 'preview.png is not a valid PNG: truncated' "head -c 24 preview.png > p && mv p preview.png"
mutate bad-crc-png 'preview.png is not a valid PNG: CRC mismatch' \
  "python3 -c \"b=bytearray(open('preview.png','rb').read());b[30]^=1;open('preview.png','wb').write(b)\""
mutate bad-screenshot 'screenshots/shot.png is not a valid PNG' "mkdir -p screenshots && head -c 24 preview.png > screenshots/shot.png"
mutate bad-prerelease 'not semver' "echo 1.2.3-01 > VERSION"

dir=$(copy testforge "Bad Name")
expect "invalid theme name fails" 1 python3 -I "$LIB/validate.py" "$dir" "Bad Name"
has "invalid name reported" "does not match"

dir=$(copy testforge warn-only)
(cd "$dir" && chmod -x install.sh && rm uninstall.sh && echo 'x = 1' > neovim.lua && touch .hidden-note)
expect "warnings alone do not fail validation" 0 python3 -I "$LIB/validate.py" "$dir" testforge
has "non-executable install.sh warned" 'WARN.*install.sh is not executable'
has "missing uninstall.sh warned" 'WARN.*uninstall.sh missing'
has "git-dropped lua warned" 'WARN.*neovim.lua'
has "hidden file warned" 'WARN.*hidden files will ship: .hidden-note'

echo "== preview render"
expect "preview renders" 0 python3 -I "$LIB/render_preview.py" "$FIX/testforge" "$T/prev" testforge 1.2.0
has "svg written" 'rendered .*testforge-1.2.0-preview.svg'
expect "rendered PNG decodes to 1200x720 RGB" 0 python3 -I -c "
import struct, zlib
b = open('$T/prev/testforge-1.2.0-preview.png', 'rb').read()
assert b[:8] == bytes.fromhex('89504e470d0a1a0a')
i, idat = 8, b''
while i < len(b):
    n, tag = struct.unpack('>I4s', b[i:i+8]); data = b[i+8:i+8+n]
    assert zlib.crc32(tag + data) & 0xffffffff == struct.unpack('>I', b[i+8+n:i+12+n])[0]
    if tag == b'IHDR': w, h = struct.unpack('>II', data[:8])
    if tag == b'IDAT': idat += data
    i += 12 + n
assert (w, h) == (1200, 720) and len(zlib.decompress(idat)) == h * (1 + 3 * w)"
expect "rendered SVG is well-formed XML" 0 python3 -I -c "import xml.etree.ElementTree as E; E.parse('$T/prev/testforge-1.2.0-preview.svg')"
dir=$(copy testforge fake-preview); head -c 24 "$dir/preview.png" >"$dir/p" && mv "$dir/p" "$dir/preview.png"
expect "24-byte fake preview.png fails the preview step" 1 python3 -I "$LIB/render_preview.py" "$dir" "$T/prev3" testforge 1.2.0
has "fake preview.png reported" 'FAIL.*preview.png is not a valid PNG'
# artifact install never follows a planted temp-name symlink or a symlinked destination
expect "install_artifact refuses a symlinked destination" 1 bash -c 'source <(sed -n "/^install_artifact() {/,/^}/p" "$1"); echo v >"$2/victim2"; echo new >"$2/src"; ln -sf "$2/victim2" "$2/dst"; install_artifact "$2/src" "$2/dst"; rc=$?; [[ $(cat "$2/victim2") == v ]] || exit 9; exit $rc' _ "$ROOT/bin/theme-release" "$T"
expect "install_artifact ignores a planted predictable temp symlink" 0 bash -c 'source <(sed -n "/^install_artifact() {/,/^}/p" "$1"); echo v >"$2/victim3"; echo new >"$2/src"; ln -sf "$2/victim3" "$2/.dst3.tmp.$$"; install_artifact "$2/src" "$2/dst3" && [[ $(cat "$2/victim3") == v && $(cat "$2/dst3") == new ]]' _ "$ROOT/bin/theme-release" "$T"
# preview writes never follow a symlink at the destination
mkdir -p "$T/prev5"; echo victim >"$T/victim.txt"; ln -s "$T/victim.txt" "$T/prev5/testforge-1.2.0-preview.svg"
expect "preview refuses a symlinked destination" 1 python3 -I "$LIB/render_preview.py" "$FIX/testforge" "$T/prev5" testforge 1.2.0
[[ $(cat "$T/victim.txt") == victim ]] && ok "symlink target untouched by preview" || bad "symlink target untouched by preview"
# PLTE structure: empty PLTE after IDAT is rejected
expect "empty PLTE after IDAT rejected" 0 python3 -I -c "
import struct, zlib, sys; sys.path.insert(0, '$LIB')
from common import png_info
def ch(t, b): return struct.pack('>I', len(b)) + t + b + struct.pack('>I', zlib.crc32(t + b) & 0xFFFFFFFF)
ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 3, 0, 0, 0)
def png(*c): open('$T/p.png', 'wb').write(b'\x89PNG\r\n\x1a\n' + b''.join(c))
idat = ch(b'IDAT', zlib.compress(b'\x00\x00'))
png(ch(b'IHDR', ihdr), idat, ch(b'PLTE', b''), ch(b'IEND', b''))
try: png_info('$T/p.png'); sys.exit(1)
except ValueError: pass
png(ch(b'IHDR', ihdr), ch(b'PLTE', b'\x00\x00\x00'), idat, ch(b'IEND', b''))
png_info('$T/p.png')"
mkdir -p "$T/prev4/sub"
expect "render_preview refuses a traversing name" 2 python3 -I "$LIB/render_preview.py" "$FIX/testforge" "$T/prev4/sub" ../../escaped 1.2.0
expect "render_preview refuses a traversing version" 2 python3 -I "$LIB/render_preview.py" "$FIX/testforge" "$T/prev4/sub" testforge ../../x
OUTPUT=$(ls -A "$T/prev4/sub"; ls -d "$T"/*escaped* 2>/dev/null)
[[ -z $OUTPUT ]] && ok "nothing written for unsafe preview names" || bad "nothing written for unsafe preview names" "$OUTPUT"
dir=$(copy testforge no-preview); rm "$dir/preview.png"
expect "missing preview.png is a warning" 0 python3 -I "$LIB/render_preview.py" "$dir" "$T/prev2" testforge 1.2.0
has "missing preview.png warned" 'WARN.*no preview.png'

echo "== pipeline: good theme end to end (stub gate)"
dir=$(copy testforge omarchy-testforge-theme)
mkdir -p "$dir/.github/workflows" "$dir/tests"
echo "name: ci" >"$dir/.github/workflows/ci.yml"; echo "true" >"$dir/tests/t.sh"; echo "out/" >"$dir/.gitignore"
git -C "$dir" init -q && git -C "$dir" -c user.name=t -c user.email=t@example.invalid add -A \
  && GIT_COMMITTER_DATE="@1790000000 +0000" git -C "$dir" -c user.name=t -c user.email=t@example.invalid \
     commit -q -m fixture --date="@1790000000 +0000"
expect "release PASS" 0 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" "$REL" "$dir" --out "$T/out1"
OUT1=$OUTPUT
has "name derived from folder (omarchy-…-theme stripped)" 'name +: testforge \(from folder name\)'
has "result PASS" '^RESULT: PASS'
has "epoch from last git commit" 'mtime +: @1790000000 .*last git commit'
has "gitleaks skipped when GITLEAKS=none" 'SKIP \(not installed\)'
has "stub gate saw noreply identity" 'stub gate on testforge: identity'
A=$T/out1/testforge-1.2.0.tar.gz
[[ -f $A && -f $A.sha256 ]] && ok "archive and .sha256 written" || bad "archive and .sha256 written" "$(ls -la "$T/out1")"
[[ -f $T/out1/testforge-1.2.0.release-report.txt ]] && ok "report written" || bad "report written"
if grep -rqi glyphsmith -- "$T/out1" || grep -qi glyphsmith <<<"$OUTPUT"; then bad "output is unbranded (no Glyphsmith)"; else ok "output is unbranded (no Glyphsmith)"; fi
[[ -f $T/out1/previews/testforge-1.2.0-preview.png ]] && ok "preview written to OUT/previews" || bad "preview written"
expect "sha256 file verifies" 0 bash -c "cd '$T/out1' && sha256sum -c testforge-1.2.0.tar.gz.sha256"
expect "archive verifier passes" 0 python3 -I "$LIB/verify_archive.py" "$A" testforge 1790000000
OUTPUT=$(tar -tzvf "$A")
hasnt "no .git/.github/tests/.gitignore in archive" 'testforge/(\.git|\.github|tests|\.gitignore)'
has "single top-level folder" '0/0 +0 [0-9-]+ [0-9:]+ testforge/$'
has "install.sh is 0755" '^-rwxr-xr-x 0/0 .* testforge/install.sh$'
has "colors.toml is 0644" '^-rw-r--r-- 0/0 .* testforge/colors.toml$'
OUTPUT=$(tar -tzf "$A" | awk -F/ '{print $1}' | sort -u)
[[ $OUTPUT == testforge ]] && ok "every entry is under testforge/" || bad "every entry is under testforge/" "$OUTPUT"

expect "second run (other OUT) PASS" 0 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" "$REL" "$dir" --out "$T/out2"
cmp -s "$A" "$T/out2/testforge-1.2.0.tar.gz" && ok "independent runs are byte-identical" || bad "independent runs are byte-identical"
expect "SOURCE_DATE_EPOCH wins" 0 env GITLEAKS=none SOURCE_DATE_EPOCH=0 GATE="$T/bin/gate-pass.sh" "$REL" "$dir" --out "$T/out3" --name forge-x
has "--name override used" 'name +: forge-x \(from --name\)'
has "epoch 0 used" 'mtime +: @0 '
expect "epoch-0 archive verifies" 0 python3 -I "$LIB/verify_archive.py" "$T/out3/forge-x-1.2.0.tar.gz" forge-x 0
expect "light theme release PASS" 0 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" "$REL" "$FIX/daylight" --out "$T/out4"
[[ -f $T/out4/daylight-2.0.0-rc.1.tar.gz ]] && ok "prerelease version in archive name" || bad "prerelease archive name"

echo "== pipeline: failures"
expect "contrast failure -> FAIL" 1 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" "$REL" "$FIX/lowcontrast" --out "$T/out5"
has "summary marks contrast FAIL" 'FAIL +2\. WCAG contrast'
has "result FAIL" '^RESULT: FAIL'
[[ ! -e $T/out5/lowcontrast-0.1.0.tar.gz ]] && ok "no archive written on FAIL" || bad "no archive written on FAIL"
[[ -f $T/out5/lowcontrast-0.1.0.release-report.txt ]] && ok "report still written on FAIL" || bad "report on FAIL"

dir=$(copy testforge junky); touch "$dir/.DS_Store"
expect "validation failure -> FAIL" 1 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" "$REL" "$dir" --out "$T/out6"
has "packaging skipped after validation failure" 'SKIP \(validation failed\) +4\. package'
has "gate skipped without archive" 'SKIP \(no archive\) +5\. pre-publish gate'

expect "failing gate -> FAIL" 1 env GITLEAKS=none GATE="$T/bin/gate-fail.sh" "$REL" "$FIX/testforge" --out "$T/out7"
has "summary marks gate FAIL" 'FAIL +5\. pre-publish gate'
[[ ! -e $T/out7/testforge-1.2.0.tar.gz ]] && ok "no archive when gate fails" || bad "no archive when gate fails"
expect "missing gate -> FAIL" 1 env GITLEAKS=none GATE="$T/nope/gate.sh" "$REL" "$FIX/testforge" --out "$T/out8"
has "missing gate reported" 'gate script not found'
# No GATE and no default gate on this machine: step 5 is skipped, not failed.
expect "no gate configured -> PASS with gate SKIP" 0 env GITLEAKS=none DEFAULT_GATE_TEST=1 \
  bash -c 'sed "s#^DEFAULT_GATE=.*#DEFAULT_GATE=$1#" "$2" >"$3" && chmod +x "$3" && "$3" "$4" --out "$5"' _ \
  "$T/nope/default-gate.sh" "$REL" "$ROOT/bin/.theme-release-nogate" "$FIX/testforge" "$T/out8b"
has "summary marks gate SKIP (no gate configured)" 'SKIP \(no gate configured\) +5\. pre-publish gate'
has "skip message says how to enable the gate" 'set GATE=<path to gate script>'
rm -f -- "$ROOT/bin/.theme-release-nogate"

if command -v gitleaks >/dev/null; then
  dir=$(copy testforge leaky)
  printf 'aws_access_key_id = %s%s\n' "AKIA" "QYLPMN5HHHFPZAM2" >"$dir/notes.txt"
  expect "gitleaks finding -> FAIL" 1 env GATE="$T/bin/gate-pass.sh" "$REL" "$dir" --out "$T/out9"
  has "summary marks gitleaks FAIL" 'FAIL +6\. gitleaks'
  hasnt "secret value is redacted" 'QYLPMN5HHHFPZAM2'
  expect "clean theme passes gitleaks" 0 env GATE="$T/bin/gate-pass.sh" "$REL" "$FIX/testforge" --out "$T/out10"
  has "gitleaks PASS" 'PASS +6\. gitleaks'
else
  echo "  skip  gitleaks not on PATH"
fi

echo "== pipeline: unsafe names are refused before any output"
mkdir -p "$T/esc/a/b"
expect "--name ../../escaped -> usage error" 2 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" \
  "$REL" "$FIX/testforge" --out "$T/esc/a/b/out" --name ../../escaped
has "unsafe name reported" "theme name '../../escaped' \\(from --name\\) does not match"
OUTPUT=$(find "$T/esc" -mindepth 1 ! -path "$T/esc/a" ! -path "$T/esc/a/b")
[[ -z $OUTPUT ]] && ok "nothing written inside or outside OUT for an unsafe name" \
  || bad "nothing written inside or outside OUT for an unsafe name" "$OUTPUT"
dir=$(copy testforge remote-escape)
git -C "$dir" init -q && git -C "$dir" remote add origin 'https://example.invalid/x/..'
expect "unsafe name from the git remote -> usage error" 2 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" \
  "$REL" "$dir" --out "$T/esc/a/b/out2"
has "remote-derived name refused" 'from git remote origin\) does not match'
[[ ! -e $T/esc/a/b/out2 ]] && ok "OUT not even created for an unsafe name" || bad "OUT not even created for an unsafe name"

echo "== pipeline: failed artifact/report writes are a FAIL"
mkdir -p "$T/wr1/testforge-1.2.0.tar.gz/blocker"
expect "archive copy failure -> FAIL" 1 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" "$REL" "$FIX/testforge" --out "$T/wr1"
has "archive write failure reported" 'could not write the archive'
hasnt "no PASS when the archive cannot be written" '^RESULT: PASS'
[[ ! -e $T/wr1/testforge-1.2.0.tar.gz.sha256 ]] && ok "no orphan .sha256 left behind" || bad "no orphan .sha256 left behind"
OUTPUT=$(ls -A "$T/wr1" | grep '\.tmp\.' || true)
[[ -z $OUTPUT ]] && ok "no temp files left in OUT" || bad "no temp files left in OUT" "$OUTPUT"
mkdir -p "$T/wr2/testforge-1.2.0.tar.gz.sha256/blocker"
expect "checksum copy failure -> FAIL" 1 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" "$REL" "$FIX/testforge" --out "$T/wr2"
[[ ! -e $T/wr2/testforge-1.2.0.tar.gz ]] && ok "archive removed when its .sha256 cannot be written" \
  || bad "archive removed when its .sha256 cannot be written"
mkdir -p "$T/wr3/testforge-1.2.0.release-report.txt/blocker"
expect "report copy failure -> FAIL" 1 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" "$REL" "$FIX/testforge" --out "$T/wr3"
has "report write failure reported" '^RESULT: FAIL \(the release report could not be written\)'

echo "== make: values are passed as data, not shell source"
q1="x\"; touch $T/pwned1; echo \""
q2="x\$(touch $T/pwned2)\`touch $T/pwned3\`"
expect "make release with quotes in THEME -> not a directory" 2 make -s -C "$ROOT" release "THEME=$q1"
expect "make release with backticks/\$() in THEME -> not a directory" 2 make -s -C "$ROOT" release "THEME=$q2"
expect "make release with backticks in NAME -> unsafe name, nothing run" 2 make -s -C "$ROOT" release \
  "THEME=$FIX/testforge" "OUT=$T/mk-name" "NAME=a\`touch $T/pwned4\`"
expect "make release with quotes in OUT -> PASS, nothing run" 0 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" \
  make -s -C "$ROOT" release "THEME=$FIX/testforge" "OUT=$T/mk-out\"; touch $T/pwned5; \""
OUTPUT=$(find "$T" -maxdepth 1 -name 'pwned*')
[[ -z $OUTPUT ]] && ok "no injected command ran" || bad "no injected command ran" "$OUTPUT"
mk_out="$T/mk out 'q' \"dq\""
expect "make release with spaces/quotes in OUT and NAME -> PASS" 0 env GITLEAKS=none GATE="$T/bin/gate-pass.sh" \
  make -s -C "$ROOT" release "THEME=$FIX/testforge" "OUT=$mk_out" "NAME=Forge Y"
[[ -f "$mk_out/forge-y-1.2.0.tar.gz" ]] && ok "archive written to the quoted OUT" || bad "archive written to the quoted OUT" "$(ls -la "$T")"

echo "== test harness"
expect "suite aborts when the temp dir cannot be created" 2 env TMPDIR="$T/no/such/dir" bash "$ROOT/tests/run-tests.sh"
has "abort reason printed" 'cannot create a temp dir'
hasnt "no test ran without a temp dir" '^  (ok|FAIL) '

echo "== usage errors"
expect "missing theme dir -> 2" 2 "$REL" "$T/does-not-exist"
expect "no args -> 2" 2 "$REL"
expect "OUT inside theme -> 2" 2 "$REL" "$FIX/testforge" --out "$FIX/testforge/out"
[[ ! -e $FIX/testforge/out ]] && ok "theme dir left untouched" || { rm -rf -- "$FIX/testforge/out"; bad "theme dir left untouched"; }
expect "make release without THEME -> error" 2 make -s -C "$ROOT" release

echo "== real pre-publish gate"
if [[ ${SKIP_REAL_GATE:-0} == 1 || ! -f $REAL_GATE ]]; then
  echo "  skip  real gate not available (or SKIP_REAL_GATE=1)"
else
  real_gate() { # an assertion that is withdrawn if the gate refused to run (load average > 8)
    local p0=$pass f0=$fail
    expect "$@"
    if grep -q 'refusing to run' <<<"$OUTPUT"; then
      pass=$p0 fail=$f0
      echo "  skip  real gate refused to run (load average > 8); remaining real-gate cases skipped"
      return 1
    fi
  }
  real_gate_cases() {
    real_gate "real gate PASS on good fixture" 0 env GITLEAKS=none GATE="$REAL_GATE" \
      "$REL" "$FIX/testforge" --out "$T/out11" || return 0
    has "gate check 1 passes" '\[PASS\] 1\. commit author/committer email'
    has "gate check 6 passes" '\[PASS\] 6\. README and LICENSE'
    printf 'zz-sentinel-unmatched-zz\n' >"$T/patterns-clean.txt"
    real_gate "real gate PASS with a non-matching pattern file" 0 env GITLEAKS=none GATE="$REAL_GATE" \
      GATE_NAME_PATTERN_FILE="$T/patterns-clean.txt" "$REL" "$FIX/testforge" --out "$T/out12" || return 0
    has "pattern file passed through" 'GATE_NAME_PATTERN_FILE: set'
    has "gate check 5 ran" '\[PASS\] 5\. no name leak'
    hasnt "pattern file contents never printed" 'zz-sentinel-unmatched-zz'
    hasnt "pattern file path never printed" 'patterns-clean'
    printf '# fixture pattern\nzz-sentinel-[a-z]+-zz|test[f]orge\n' >"$T/patterns-hit.txt"
    real_gate "real gate FAIL when the pattern matches" 1 env GITLEAKS=none GATE="$REAL_GATE" \
      GATE_NAME_PATTERN_FILE="$T/patterns-hit.txt" "$REL" "$FIX/testforge" --out "$T/out13" || return 0
    has "gate check 5 fails" '\[FAIL\] 5\. no name leak'
    hasnt "matching pattern never printed" 'zz-sentinel|test\[f\]orge'
  }
  real_gate_cases
fi

echo
echo "tests: $pass passed, $fail failed"
((fail == 0))
