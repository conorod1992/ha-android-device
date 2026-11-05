# Releasing

The Release workflow first runs minimum/current Home Assistant tests, lint, formatting,
HACS validation, hassfest, and the real Companion Android emulator suite against the workflow's source commit. A failed check
prevents the version commit, tag, and release. If main changes during validation,
the workflow refuses to publish; rerun it from current main.

Releases are created from `main` with the **Release** GitHub Actions workflow.

1. Make sure the changes you want to release are merged to `main` and CI is green.
2. Open **Actions → Release → Run workflow**.
3. Enter the next version once in `X.Y.Z` form, for example `1.8.1`.
4. Run the workflow.

The workflow validates that the requested version is newer than the currently published release, updates every tracked version field through `scripts/set_release_version.py`, commits the version bump to `main`, and creates the matching `vX.Y.Z` GitHub release/tag with generated notes.

Do not manually pre-bump `manifest.json` or create the release tag first. The release workflow owns both so the release number only needs to be entered once.
