"""Render exact Git tag ranges using the pinned Semantic Release commit parser.

No tags, commits, working-tree files, or remote releases are modified.
Run with the same isolated Semantic Release environment used for planning.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from release_plan import TAG, tag_key

REPOSITORY = "https://github.com/isyuricunha/mcp-prism-search"
GROUPS = (
    "Breaking changes", "Features", "Bug fixes", "Security", "Performance",
    "Documentation", "Refactoring", "Build", "Continuous integration",
    "Tests", "Maintenance", "Other changes",
)
TYPES = {
    "features": "Features", "bug fixes": "Bug fixes",
    "performance improvements": "Performance", "documentation": "Documentation",
    "refactoring": "Refactoring", "build system": "Build",
    "continuous integration": "Continuous integration", "testing": "Tests",
    "tests": "Tests", "chores": "Maintenance", "code style": "Maintenance",
}


def commit_entries(commit, parser):
    """Use PSR semantics, but keep all real changes inside the exact range."""
    if len(commit.parents) > 1:
        return
    parsed = parser.parse(commit)
    if not isinstance(parsed, list):
        parsed = [parsed]
    for item in parsed:
        if hasattr(item, "error"):
            subject = str(commit.summary).strip()
            if subject:
                yield "Other changes", subject, []
            continue
        if not item.include_in_changelog:
            continue
        if item.scope == "release" and item.type == "chores":
            continue
        if not item.descriptions:
            continue
        group = TYPES.get(item.type, "Other changes")
        if item.breaking_descriptions or item.bump.name == "MAJOR":
            group = "Breaking changes"
        elif item.scope in {"security", "ssrf", "auth"}:
            group = "Security"
        subject = item.descriptions[0]
        if item.scope:
            subject = f"**{item.scope}:** {subject}"
        details = list(dict.fromkeys([*item.descriptions[1:], *item.breaking_descriptions]))
        yield group, subject, details


def one_line(value: str) -> str:
    return " ".join(str(value).split())


def section(repo, parser, tag: str, sha: str, previous: str | None) -> str:
    revision = f"{previous}..{sha}" if previous else sha
    grouped = defaultdict(list)
    for commit in repo.iter_commits(revision):
        for group, subject, details in commit_entries(commit, parser):
            link = f"[`{commit.hexsha[:7]}`]({REPOSITORY}/commit/{commit.hexsha})"
            lines = [f"- {one_line(subject)} ({link})"]
            lines.extend(f"  - {one_line(detail)}" for detail in details if one_line(detail))
            grouped[group].append("\n".join(lines))
    if not grouped:
        raise ValueError(f"No changelog entries for {tag}; refusing an empty release note")
    blocks = [f"## {tag}"]
    for group in GROUPS:
        if grouped[group]:
            blocks.extend([f"### {group}", "\n".join(grouped[group])])
    if previous:
        blocks.append(f"**Full changelog:** {REPOSITORY}/compare/{previous}...{tag}")
    return "\n\n".join(blocks)


def generate(tag: str, sha: str, directory: Path) -> None:
    from git import Repo
    from semantic_release.commit_parser.conventional import ConventionalCommitParser

    tag_key(tag)
    parser = ConventionalCommitParser()
    with Repo(".") as repo:
        commit = repo.commit(sha)
        if commit.hexsha != sha:
            raise ValueError("Expected an exact, full source commit")
        tags = {t.name: t.commit.hexsha for t in repo.tags if TAG.fullmatch(t.name)}
        if tag in tags and tags[tag] != sha:
            raise ValueError("Existing tag does not match the planned source")
        # Never attribute changes to a later version or an unrelated branch.
        history = [
            (name, target) for name, target in tags.items()
            if tag_key(name) < tag_key(tag) and repo.is_ancestor(target, sha)
        ]
        history.sort(key=lambda pair: tag_key(pair[0]))
        history.append((tag, sha))
        sections = [
            section(repo, parser, name, target, history[i - 1][0] if i else None)
            for i, (name, target) in enumerate(history)
        ]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "CHANGELOG.md").write_text(
        "# Changelog\n\n" + "\n\n---\n\n".join(reversed(sections)) + "\n",
        encoding="utf-8",
    )
    (directory / "release-notes.md").write_text(sections[-1] + "\n", encoding="utf-8")


if __name__ == "__main__":
    arg = argparse.ArgumentParser(description=__doc__)
    arg.add_argument("tag")
    arg.add_argument("sha")
    arg.add_argument("directory", type=Path)
    args = arg.parse_args()
    generate(args.tag, args.sha, args.directory)
