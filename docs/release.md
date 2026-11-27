# CI/CD and release recovery

The workflow validates pull requests with read-only permissions and serializes
production releases from main. Python Semantic Release 10.7.0 calculates
the next version from Conventional Commits; it does not write version
files, create commits or push the branch. Hatch VCS derives Python versions
from Git tags, and the Docker build receives the validated package version
explicitly (the Docker context deliberately excludes .git). A non-tagged
source checkout uses a development version; an unversioned Docker build
reports 0.0.0+unknown. Runtime package metadata is authoritative.

The dashboard distinguishes tagged release versions, local development
builds (with the Git commit when Hatch VCS embeds it), and unidentified
builds. It always preserves the exact installed version in server details.
A clean source checkout at an exact release tag has the same version as
that release; the version alone does not prove its installation origin.
Rebuild Docker images or reinstall editable source packages after Git
updates to refresh installed metadata. The dashboard never substitutes a
remote latest version for the installed version.

## Publication

1. Plan against the triggering commit, or an explicitly requested latest
   stable tag. No release is generated if Semantic Release calculates no bump.
2. Install locked dependencies, test and build wheel/sdist with the planned
   version. Run Chromium accessibility and interaction tests.
3. Only the separate publish job has contents/packages write access. Refuse
   stale main commits; create an annotated tag pointing to the exact validated
   commit and push only that tag. Never move an existing tag or main.
4. Build amd64/arm64 once in Buildx, with a native architecture dashboard
   build stage and shared registry-independent cache. Push versioned images
   to the existing Docker Hub repository (mcp-ddg-research, preserved for
   existing users) and GHCR. Check manifests and runtime versions on both
   platforms before promoting the digest to latest.
5. Publish the GitHub Release with the mandatory version-specific changelog,
   cumulative CHANGELOG.md and verified Python distributions. The generator uses
   the pinned Semantic Release Conventional Commit parser on exact Git tag
   ranges (including commit details and links). This avoids misattribution
   across old merge commits. Changelogs are generated in CI, not committed
   to main. Missing notes block release publication. Existing
   release assets are not overwritten during recovery. A partially uploaded
   draft is completed and published after missing assets are uploaded. Empty or
   legacy link-only notes are repaired; substantive existing notes are preserved.

Actions are pinned to full commit SHAs. Dependabot proposes updates for review.
The browser suite waits for matching network responses rather than assuming
a fast click completes an asynchronous request. The test runner is pinned
to Ubuntu 24.04; Chromium installs only the headless shell.

## Recovery

If publication fails after the tag is created, run **CI and Release** from
main using workflow_dispatch with the latest tag (for example v0.15.0).
The workflow tests and publishes the exact tagged commit again, without
incrementing its version. Only tags produced after this migration support
this recovery flow. If a newer stable tag exists, an older tag is refused
to avoid overwriting latest. Re-run a failed job if its verified artifact
is still available (seven days); otherwise dispatch the tag again. Each full
rerun uploads a new attempt-specific artifact, while a publish-only rerun
uses the original validation output.

If both registries already hold the same versioned multiarch digest, the
manual workflow also accepts image_digest with the latest tag. It skips
rebuilding and revalidates both registry references, per-platform runtime
versions, architectures and source revisions before promoting that exact
digest. Never supply an unverified or mismatched digest.

A registry push and a GitHub release are external, separate operations:
cross-registry atomicity is not guaranteed. A failure after pushing one
registry or promoting one latest tag can leave a temporary mismatch.
Recovery finishes the same release without moving its Git tag.
A completed release does not imply a local deployment was upgraded; the
running server always reports its actually installed package version.

## Local validation

Run pytest, Ruff and the browser tests as documented in development.md.
The release pipeline tests use disposable local Git repositories and a
bare local remote; they do not create tags on GitHub or publish images.
```sh
python -m pytest tests/test_release_pipeline.py
python -m ruff check .
```

Browser dependency installation retries transient APT or CDN failures up to
three times. A permanent installation failure still blocks publication.


## Dependency updates

Dependabot groups GitHub Actions version updates into a single weekly
pull request (all-actions). Security updates use their own urgent group
(security-actions) because GitHub treats security updates separately.
The existing scope is GitHub Actions only; adding Python or npm updates
requires a separate lockfile compatibility policy.

The repository requires the full CI validation job and GitGuardian's
security check on main, with strict up-to-date branch checks. Repository
administrators retain their existing direct-push workflow. The trusted
workflow_run auto-merge job never executes PR source or artifacts; it
queues only same-repository grouped Dependabot PRs with a successful
CI check on the exact head. GitHub enforces required checks before merge.
An ungrouped PR, human PR, failed check, or update outside the permitted
workflow directory is not automatically merged. Major updates are included
in the group, so one incompatible update blocks the whole group.

The native auto-merge uses squash merging. GitHub may suppress a subsequent
push-triggered workflow for merges performed with GITHUB_TOKEN. Dependency
chore updates do not intentionally create a new release; use the manual CI
and Release workflow if post-merge validation or publication is needed.
