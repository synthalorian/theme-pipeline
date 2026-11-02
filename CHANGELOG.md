# Changelog

All notable changes to theme-pipeline are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [SemVer](https://semver.org/).

## [0.1.1] - 2026-11-02
### Fixed
- When the optional pre-publish gate was skipped (no `GATE` configured), the archive was never extracted, so the gitleaks step had nothing to scan and a release could pass without a secrets scan. The archive is now always extracted and scanned.

### Changed
- No brand wording in output, messages or docs.
- The pre-publish gate is optional. With `GATE` unset and no default gate present, step 5 is skipped with a clear message. A `GATE` that is set but missing still fails.
- `RELEASE_GIT_NAME` / `RELEASE_GIT_EMAIL` set the identity of the temporary commit the gate checks.
- The real-gate tests read `REAL_GATE` and skip when it is missing.
- Adds a GitHub Actions workflow that runs the test suite.

## [0.1.0] - not published
### Added
- `theme-release` / `make release`: validate, WCAG contrast check, preview, reproducible package (built twice, byte-compared), pre-publish gate and gitleaks for an Omarchy 4 theme folder, with a PASS/FAIL summary and release report.

[0.1.1]: https://github.com/synthalorian/theme-pipeline/releases/tag/v0.1.1
