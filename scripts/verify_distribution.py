"""Verify wheel and sdist metadata agree with the installed package."""
from __future__ import annotations

import argparse
import tarfile
import zipfile
from email.parser import BytesParser
from importlib.metadata import version
from pathlib import Path


def verify(directory: Path, expected: str) -> None:
    wheels = list(directory.glob("*.whl"))
    archives = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(archives) != 1:
        raise ValueError("Expected exactly one wheel and one source distribution")
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError("Invalid wheel metadata")
        wheel_version = BytesParser().parsebytes(wheel.read(names[0]))["Version"]
    with tarfile.open(archives[0], "r:gz") as archive:
        names = [n for n in archive.getnames() if n.count("/") == 1 and n.endswith("/PKG-INFO")]
        if len(names) != 1:
            raise ValueError("Invalid sdist metadata")
        info = archive.extractfile(names[0])
        if info is None:
            raise ValueError("Missing sdist metadata")
        archive_version = BytesParser().parsebytes(info.read())["Version"]
    if wheel_version != archive_version or wheel_version != expected:
        raise ValueError(
            f"Distribution version mismatch: {wheel_version}, {archive_version}, {expected}"
        )
    print(f"Verified wheel and sdist version {expected}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected")
    args = parser.parse_args()
    verify(args.directory, args.expected or version("mcp-prism-search"))
