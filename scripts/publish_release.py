"""Recover a release without changing its tag or overwriting existing assets."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from release_plan import tag_key


def publish(tag: str, directory: Path) -> None:
    tag_key(tag)
    artifacts = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.gz"))
    if len(artifacts) != 2:
        raise ValueError("Expected verified wheel and sdist")
    result = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets,isDraft"],
        capture_output=True, text=True,
    )
    if result.returncode:
        # gh create verifies that the existing remote tag is valid.
        subprocess.run(
            ["gh", "release", "create", tag, "--verify-tag", "--generate-notes",
             *(str(path) for path in artifacts)],
            check=True,
        )
        return
    release = json.loads(result.stdout)
    names = {asset["name"] for asset in release["assets"]}
    for artifact in artifacts:
        if artifact.name not in names:
            subprocess.run(["gh", "release", "upload", tag, str(artifact)], check=True)
    if release.get("isDraft"):
        subprocess.run(["gh", "release", "edit", tag, "--draft=false"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    publish(args.tag, args.directory)
