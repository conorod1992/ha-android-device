# Releasing

Releases are created from `main` with the **Release** GitHub Actions workflow.

1. Make sure the changes you want to release are merged to `main` and CI is green.
2. Open **Actions → Release → Run workflow**.
3. Enter the next version once in `X.Y.Z` form, for example `1.8.1`.
4. Run the workflow.

The workflow validates that the requested version is newer than the currently published release, updates every tracked version field through `scripts/set_release_version.py`, commits the version bump to `main`, and creates the matching `vX.Y.Z` GitHub release/tag with generated notes.

Do not manually pre-bump `manifest.json` or create the release tag first. The release workflow owns both so the release number only needs to be entered once.
