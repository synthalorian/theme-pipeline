# theme-pipeline

One command that takes an Omarchy 4 theme folder from "looks done" to "ready for sign-off":
it validates the files, checks WCAG contrast, renders a preview, builds a reproducible
release archive (the same format as the Steel & Ember theme releases, its first user), and runs the
pre-publish gate and gitleaks on what actually ships. It ends with a PASS/FAIL summary.

It only reads the theme folder. It never pushes, publishes or touches the network.

```bash
make release THEME=/path/to/omarchy-mytheme-theme            # writes ./out/
make release THEME=/path/to/theme OUT=/tmp/rel NAME=mytheme  # custom output dir and name
make test
```

## Requirements

- bash, GNU tar, gzip, coreutils, git
- Python 3.11+ (stdlib only: `tomllib`, `zlib`, `tarfile`)
- Optional: a pre-publish gate script, set with `GATE=` (default `/workspace/pre-publish-gate/gate.sh` if it exists). Without one, step 5 is skipped with a clear message
- Optional: `gitleaks` on PATH; `rsvg-convert` or ImageMagick to also rasterize the SVG preview with real glyphs

## What `make release` does

| Step | Checks | Fails when |
|---|---|---|
| 1. validate files | `colors.toml` parses with `tomllib`, `mode` is `dark`/`light`, all 25 Omarchy 4 palette keys are present (26 with `mode`, the same list as Glazier's `validate-theme.sh`), values are valid colors. `VERSION` is strict SemVer 2.0.0 (no leading zeros in numeric identifiers, prerelease ones included, so `1.2.3-01` fails). CHANGELOG has a non-empty heading for that version. README and LICENSE are present and non-empty, and README local links/images point to files that ship. `backgrounds/` has an image. `preview.png` and `screenshots/*.png` are fully checked PNGs (see step 3). No junk (`.DS_Store`, `*~`, `*.bak`/`.orig`/`.swp`, `.#*`, nested `.git`, archives, caches, `.gitleaks.toml`), no symlinks or special files, no unreplaced `{{VERSION}}`-style placeholders. Warns about a missing or non-executable `install.sh`/`uninstall.sh`, hidden files, files Omarchy 4 ignores for git installs (`*.lua`, `kitty.conf`, …), `colorN` that differ from their semantic key, and uncommitted changes in a git checkout | any check fails |
| 2. WCAG contrast | foreground/background, selection text on selection background, color1–color7 on background: **4.5:1**. Cursor and accent on background: **3:1** (non-text). Bright colors, `dark/light/bright_foreground` and `muted` are advisory. color0 (and color7 when within 1.5:1 of the background) is a background-shade slot and is listed but not judged. The math is the WCAG 2.x definition, built in (`lib/common.py`) | an enforced pair is below its minimum |
| 3. preview | Renders `OUT/previews/<name>-<ver>-preview.svg` (real monospace text) and `.png` (a pure-stdlib PNG encoder: palette, terminal mock, 16 ANSI swatches; text is drawn as word bars). Also checks the theme's own `preview.png` is a valid PNG and lists `screenshots/`. The PNG check is structural, not just the signature: every chunk length and CRC, IHDR first with valid fields, PLTE for palette images, consecutive IDATs, IEND last with nothing after it, and the image data must decompress to exactly the expected scanlines with valid filter bytes | colors.toml is unusable or `preview.png` is corrupt (missing `preview.png` is a warning) |
| 4. package | Stages the shipping files (the top-level `.git`, `.github`, `.gitignore`, `.gitattributes`, `tests` and `releases` are left out), then builds `<name>-<ver>.tar.gz` with reproducible settings: one top-level folder named after the theme, `--sort=name`, GNU format, every mtime = `SOURCE_DATE_EPOCH`, owner 0/0 numeric, modes 0644/0755, `gzip -n --best`, plus a `sha256sum`-format `.sha256`. It builds **twice** from fresh stages and requires byte-identical output, then verifies the archive (paths, owners, mtimes, modes, order, gzip header, content = stage) | the builds differ or the archive shape is wrong |
| 5. pre-publish gate | Extracts the archive into a temp dir, `git init`s it with one commit by `$RELEASE_GIT_NAME <$RELEASE_GIT_EMAIL>` (default `synthalorian <212206047+synthalorian@users.noreply.github.com>`; no global config or hooks), and runs `gate.sh` on it. `GATE_NAME_PATTERN_FILE` is passed through when set. The pipeline never prints its path or contents, and the gate prints locations only | gate exit ≠ 0, or `GATE` is set but missing (skipped when `GATE` is unset and the default gate is not there) |
| 6. gitleaks | `gitleaks dir` on the extracted archive (extracted even when the gate is skipped), with the built-in rules pinned (`[extend] useDefault = true`, an empty ignore dir, `GITLEAKS_CONFIG*` unset) and `--redact=100` | any finding (skipped if gitleaks isn't installed) |

Packaging and the gate are skipped once validation fails. The archive and `.sha256` are
copied to `OUT` **only when every step passes**. Each copy goes to a temp file in `OUT`, is compared
with the source, then renamed into place. If either copy fails (disk full, permissions), neither is left
behind and the result is FAIL. The previews and `OUT/<name>-<ver>.release-report.txt` (the full console log)
are written on PASS and FAIL. If the report can't be written, the run ends `RESULT: FAIL` with exit 1.

**Theme name:** `NAME=`/`--name`, else the basename of the checkout's `remote.origin.url`, else the folder
name. It is lowercased, spaces become `-`, and a leading `omarchy-` and trailing `-theme` are stripped, as
`omarchy-theme-install` does. The name must match `^[a-z0-9_][a-z0-9._+-]*$`. It is checked before anything
is written (not even `OUT` is created), so a name like `../../x` is a usage error (exit 2) and can't escape `OUT`.

**mtime:** `SOURCE_DATE_EPOCH` if set, else the last git commit touching the theme folder, else 0 . The published Steel & Ember archives were built with
mtime 0, so set `SOURCE_DATE_EPOCH=0` to reproduce them byte for byte (see below).

### Environment

| Variable | Meaning |
|---|---|
| `SOURCE_DATE_EPOCH` | archive mtime (integer, 0 to 4102444800) |
| `GATE` | gate script. Unset: `/workspace/pre-publish-gate/gate.sh` if present, else step 5 is skipped. Set but missing: FAIL |
| `RELEASE_GIT_NAME`, `RELEASE_GIT_EMAIL` | identity of the temp commit the gate checks (default synthalorian noreply) |
| `GATE_NAME_PATTERN_FILE` | passed to the gate for its name-leak check; never printed |
| `GITLEAKS` | gitleaks binary; `none` skips the step |

Exit codes: `0` PASS, `1` FAIL, `2` usage error (bad theme dir, `OUT` inside the theme dir, invalid theme name, …).
`make release` hands `THEME`, `OUT` and `NAME` to the recipe through the environment, so quotes, `$` or
backticks in them are never run by the shell.
Heavy commands run under `nice` and `timeout`.

## Layout

```
Makefile               release / test / clean
bin/theme-release      orchestrator (bash): runs the steps, summary, report
lib/common.py          palette keys, junk rules, ship-file walk, WCAG color math
lib/validate.py        step 1
lib/contrast.py        step 2
lib/render_preview.py  step 3 (SVG + stdlib PNG)
lib/stage.py           copies the shipping files with normalized modes
lib/pack.sh            reproducible tar.gz + .sha256
lib/verify_archive.py  archive shape checks
tests/run-tests.sh     test suite
tests/fixtures/        testforge (dark, good), daylight (light, good, pre-release version), lowcontrast (bad)
```

## Tests

```bash
make test        # or: tests/run-tests.sh   (SKIP_REAL_GATE=1 to skip the real-gate cases)
```

The suite copies the fixtures into a temp dir and generates the bad themes there by mutation. That covers 33
validation failures: missing key, mode, bad TOML, bad color, semver (including a leading-zero prerelease), missing or
empty CHANGELOG entry, README, LICENSE, `.DS_Store`, backups, swap files, nested `.git`, `.gitleaks.toml`, file and dir
symlinks, placeholders, broken or escaping README links, no backgrounds, corrupt PNGs (not a PNG, a 24-byte fake header,
a bad CRC, a corrupt screenshot), and a bad name. Unit tests cover strict semver and the PNG checker (truncation, CRCs,
missing IDAT/IEND, trailing data, short/long/non-zlib image data, filter bytes, IHDR fields, PLTE). It also covers warnings,
the contrast failures, preview rendering (PNG decoded and CRC-checked, SVG parsed, unsafe names refused), and end-to-end runs. Those check
the archive layout and modes, that `.git`/`.github`/`tests` are excluded, byte-identical repeat builds,
`SOURCE_DATE_EPOCH`, `--name`, that no archive is written on FAIL, failing and missing gates, a planted fake key caught by gitleaks
and redacted, and usage errors. Regression cases cover unsafe names from `--name` and from the git remote (nothing is
written inside or outside `OUT`), archive, `.sha256` and report copy failures (FAIL, exit 1, no partial files), shell
metacharacters in `make release` variables (no injected command runs, and quoted paths still work), and the suite
aborting when it can't create its temp dir. The real gate is run too: plain, with a non-matching pattern file, and with a matching one. Those
cases assert that the pattern text and path never appear in the output. Tests that need the real gate (`REAL_GATE`, default `/workspace/pre-publish-gate/gate.sh`) skip when it is missing. Current result: **216 passed, 0 failed** (about 11 s).

## First real run: Steel & Ember 0.1.1

Run on 2026-11-02 against a clean, read-only checkout of the theme repo (HEAD `b36e216`):

```bash
make release THEME=path/to/omarchy-steel-and-ember-theme OUT=out/steel-and-ember
```

**RESULT: PASS** (all six steps).

| Step | Result |
|---|---|
| 1. validate | PASS. Name `steel-and-ember` (from the git remote `omarchy-steel-and-ember-theme`), `mode = "dark"`, 25/25 palette keys, VERSION 0.1.1, CHANGELOG entry `[0.1.1] - 2026-11-02`, README links resolve, no junk/symlinks, checkout clean |
| 2. contrast | PASS, 0 fail, 0 warn. fg/bg 10.93:1, selection 10.73:1, cursor 14.31:1, accent 6.92:1. The lowest enforced pair is red `#e8604f` at 5.01:1. Muted is 3.73:1 (advisory, ≥ 3:1) |
| 3. preview | PASS. SVG + PNG rendered. The theme ships `preview.png` (2560x1440) and `screenshots/lock.png`, `terminal.png` |
| 4. package | PASS. 13 files, 16 entries under `steel-and-ember/`. `.git` and `.github/` are excluded. Two builds are byte-identical. mtime @1793633446 (last commit). 4,342,129 bytes, sha256 `4fc41964…3caff8` |
| 5. gate | PASS. Checks 1–4 and 6 pass. Check 5 was skipped because `GATE_NAME_PATTERN_FILE` was unset. A second run with it set also passed check 5 (`GATE: PASS (0 skipped)`) |
| 6. gitleaks | PASS (8.30.1, no leaks) |

**Reproducing the reference archive.** With `SOURCE_DATE_EPOCH=0` (the convention of the published
archives), the output is **byte-identical** to the published `steel-and-ember-0.1.1.tar.gz` release asset
(sha256 `36c12b38b40fde300a56aa0d940f5356704e87e22d2c5876aa15e2b6902dfdc8`, 4,342,078 bytes). With the
default git-commit mtime, the extracted files are identical to the reference and only the timestamps differ.

## Limits

- The stdlib PNG preview draws text as colored word bars, not glyphs. The SVG has real text. If `rsvg-convert` or
  ImageMagick is installed, a glyph-accurate `*.svg.png` is written too (neither is on the build box today).
- Contrast covers `colors.toml` only. Per-app files (`btop.theme`, `shell.toml`, …) are not paired. Check those with a separate contrast tool.
- Install, uninstall and reinstall in a clean environment (RELEASE-CHECKLIST item 6) are not run here. That remains the
  bubblewrap release test.
- Nothing is signed. Sign-off and publishing stay manual (RELEASE-CHECKLIST item 8).

## License

MIT, (c) 2026 synthalorian. See [LICENSE](LICENSE).
