"""Resolve a release without writing commits, tags, or releases.

Run from a full Git checkout of main. Semantic Release calculates SemVer;
the workflow alone publishes the verified commit and artifacts.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

TAG = re.compile(r"v(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def tag_key(tag: str) -> tuple[int, ...]:
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError("Expected a stable vMAJOR.MINOR.PATCH tag")
    return tuple(map(int, match.groups()))


def latest_tag() -> str:
    tags = [t for t in git("tag", "--list").splitlines() if TAG.fullmatch(t)]
    return max(tags, key=tag_key, default="")


def target(tag: str) -> str:
    tag_key(tag)
    return git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")


def calculate(requested: str = "", *, semantic: str = "semantic-release") -> dict[str, str]:
    head = git("rev-parse", "HEAD")
    latest = latest_tag()
    if requested:
        if requested != latest:
            raise ValueError("Only the latest stable tag can be republished")
        sha = target(requested)
        if subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"]).returncode:
            raise ValueError("Release tag is not on main")
        return {"publish": "true", "tag": requested, "version": requested[1:], "sha": sha}
    if latest and target(latest) == head:
        return {"publish": "true", "tag": latest, "version": latest[1:], "sha": head}
    version = subprocess.check_output([semantic, "version", "--print"], text=True).strip()
    tag = "v" + version
    tag_key(tag)
    if latest and tag_key(tag) <= tag_key(latest):
        return {"publish": "false", "tag": "", "version": "", "sha": head}
    return {"publish": "true", "tag": tag, "version": version, "sha": head}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="")
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()
    result = calculate(args.tag)
    lines = "".join(f"{key}={value}\n" for key, value in result.items())
    if args.output:
        with Path(args.output).open("a") as file:
            file.write(lines)
    else:
        print(lines, end="")


if __name__ == "__main__":
    main()
