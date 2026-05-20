# Releasing

Versions are **VCS-derived** (`uv-dynamic-versioning`): a build on a
`vX.Y.Z`-tagged commit is exactly `X.Y.Z`; between tags it is a PEP 440
dev/post version with the git sha embedded. There is **no version field**
to bump.

## Adding a news fragment

Add a Markdown fragment under the package you changed:
`changelog.d/<package>/<issue>.<type>.md` where `<type>` is one of
`added | changed | deprecated | removed | fixed | security`.

## Cutting a release

Releases are cut **on `main`, after** the change has merged through the
`phase1-main-gate` ruleset (the tag must sit on the merged commit so the
version math is correct):

```
just release 0.1.0
git push origin v0.1.0      # explicit — confirm before pushing
```

`just release X.Y.Z` runs `towncrier build` (folds the fragments into the
single root `CHANGELOG.md`, deletes them), commits the changelog, and
creates a **local** annotated `vX.Y.Z` tag. It does **not** push.

Verify after deploy: `agent-core daemon status` shows
`installed version: X.Y.Z`.
