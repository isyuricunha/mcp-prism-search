"""Check the remote before creating a tag or promoting latest.

The publisher owns a serialized workflow; never force a tag or main.
"""
from __future__ import annotations

import argparse

from release_plan import git, latest_tag, tag_key, target


def guard(tag: str, sha: str, *, promote: bool = False) -> bool:
    tag_key(tag)
    if git("rev-parse", "HEAD") != sha:
        raise ValueError("Checkout does not match the validated commit")
    git("fetch", "origin", "main", "--tags")
    latest = latest_tag()
    if latest and tag_key(tag) < tag_key(latest):
        raise ValueError("Refusing to publish an older release as latest")
    if tag == latest:
        if target(tag) != sha:
            raise ValueError("Existing release tag points to a different commit")
        return True
    if promote:
        raise ValueError("Release tag must exist before promotion")
    if git("rev-parse", "origin/main") != sha:
        # A newer push will calculate the correct release; never release stale work.
        return False
    git("tag", "-a", tag, sha, "-m", f"Release {tag}")
    # Only the tag is pushed, never the branch or an arbitrary ref.
    git("push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("sha")
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    if not guard(args.tag, args.sha, promote=args.promote):
        raise SystemExit("A newer main commit exists. Release skipped; rerun from current main.")


if __name__ == "__main__":
    main()
