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
    changelog = directory / "CHANGELOG.md"
    notes = directory / "release-notes.md"
    if not changelog.is_file() or not notes.is_file():
        raise ValueError("Changelog and release notes are required")
    text = notes.read_text(encoding="utf-8")
    if not text.startswith(f"## {tag}\n") or "\n- " not in text:
        raise ValueError("Release notes must contain the current tag and real changes")
    if not changelog.read_text(encoding="utf-8").startswith("# Changelog\n\n" + text.strip()):
        raise ValueError("Cumulative changelog must start with the current release notes")
    artifacts.append(changelog)
    result = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets,isDraft,body"],
        capture_output=True, text=True,
    )
    if result.returncode:
        # gh create verifies that the existing remote tag is valid.
        subprocess.run(
            ["gh", "release", "create", tag, "--verify-tag", "--notes-file", str(notes),
             *(str(path) for path in artifacts)],
            check=True,
        )
        return
    release = json.loads(result.stdout)
    names = {asset["name"] for asset in release["assets"]}
    for artifact in artifacts:
        if artifact.name not in names:
            subprocess.run(["gh", "release", "upload", tag, str(artifact)], check=True)
    body = (release.get("body") or "").strip()
    # Preserve substantive manually curated notes; repair the former link-only release.
    if not body or body.lower().startswith("**full changelog**:") and "\n" not in body:
        subprocess.run(["gh", "release", "edit", tag, "--notes-file", str(notes)], check=True)
    if release.get("isDraft"):
        subprocess.run(["gh", "release", "edit", tag, "--draft=false"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    publish(args.tag, args.directory)
