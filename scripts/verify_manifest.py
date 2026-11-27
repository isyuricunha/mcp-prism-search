"""Require both supported runtime platforms; ignore attestation manifests."""
import json
import sys


def verify(data: dict) -> None:
    platforms = {
        (item.get("platform", {}).get("os"), item.get("platform", {}).get("architecture"))
        for item in data.get("manifests", [])
    }
    if not {("linux", "amd64"), ("linux", "arm64")} <= platforms:
        raise ValueError("Manifest lacks one or both required platforms")


if __name__ == "__main__":
    verify(json.load(sys.stdin))
    print("Verified amd64 and arm64 manifest")
