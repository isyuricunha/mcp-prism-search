"""Classify installed package metadata without querying Git or a registry."""

import re
from typing import Literal, TypedDict

from packaging.version import InvalidVersion, Version


class BuildInfo(TypedDict):
    build_kind: Literal["release", "development", "unknown"]
    commit: str | None


def build_info(installed: str) -> BuildInfo:
    """Describe version identity, not the installation method or latest remote tag."""
    try:
        parsed = Version(installed)
    except InvalidVersion:
        return {"build_kind": "unknown", "commit": None}
    if parsed.local == "unknown":
        return {"build_kind": "unknown", "commit": None}
    if parsed.is_prerelease or parsed.local:
        commit = next(
            (part[1:] for part in (parsed.local or "").split(".")
             if re.fullmatch(r"g[0-9a-f]{7,40}", part)),
            None,
        )
        return {"build_kind": "development", "commit": commit}
    if re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", installed):
        return {"build_kind": "release", "commit": None}
    return {"build_kind": "unknown", "commit": None}
