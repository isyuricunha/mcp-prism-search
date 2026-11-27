"""Release planning and tag-only publication, with no external writes."""
import importlib
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def repository(tmp_path, monkeypatch):
    def run(*args, cwd=tmp_path):
        return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()

    remote = tmp_path / "origin.git"
    local = tmp_path / "checkout"
    run("init", "--bare", "--initial-branch=main", str(remote))
    run("clone", str(remote), str(local))
    run("config", "user.name", "Release test", cwd=local)
    run("config", "user.email", "release-test@example.invalid", cwd=local)
    (local / "source.txt").write_text("first")
    run("add", "source.txt", cwd=local)
    run("commit", "-m", "feat: initial", cwd=local)
    run("tag", "v0.9.0", cwd=local)
    run("push", "origin", "main", "--tags", cwd=local)
    monkeypatch.chdir(local)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    plan = importlib.import_module("release_plan")
    guard = importlib.import_module("release_guard")
    return run, remote, local, plan, guard


def test_recovery_uses_existing_tag_and_does_not_create_commit(repository):
    run, remote, local, plan, guard = repository
    before = run("rev-parse", "HEAD", cwd=local)
    result = plan.calculate("v0.9.0")
    assert result == {"publish": "true", "tag": "v0.9.0", "version": "0.9.0", "sha": before}
    assert guard.guard(result["tag"], before)
    assert guard.guard(result["tag"], before, promote=True)
    assert run("rev-parse", "main", cwd=remote) == before
    assert run("rev-list", "--count", "main", cwd=remote) == "1"


def test_publish_new_tag_without_moving_main(repository):
    run, remote, local, plan, guard = repository
    (local / "source.txt").write_text("changed")
    run("commit", "-am", "fix: change", cwd=local)
    run("push", "origin", "main", cwd=local)
    before = run("rev-parse", "HEAD", cwd=local)
    count = run("rev-list", "--count", "main", cwd=remote)
    assert guard.guard("v0.10.0", before)
    assert run("rev-parse", "v0.10.0^{commit}", cwd=remote) == before
    assert run("rev-list", "--count", "main", cwd=remote) == count
    assert plan.calculate()["tag"] == "v0.10.0"
    assert guard.guard("v0.10.0", before, promote=True)
    with pytest.raises(ValueError, match="older"):
        guard.guard("v0.9.0", before)
    with pytest.raises(ValueError, match="latest stable"):
        plan.calculate("v0.9.0")


def test_stale_checkout_cannot_create_new_release(repository):
    run, remote, local, plan, guard = repository
    before = run("rev-parse", "HEAD", cwd=local)
    other = local.parent / "other"
    run("clone", str(remote), str(other))
    run("config", "user.name", "Release test", cwd=other)
    run("config", "user.email", "release-test@example.invalid", cwd=other)
    (other / "source.txt").write_text("advanced")
    run("commit", "-am", "fix: new main", cwd=other)
    run("push", "origin", "main", cwd=other)
    assert guard.guard("v0.10.0", before) is False
    assert run("tag", "--list", "v0.10.0", cwd=remote) == ""


def test_semver_numeric_order_and_no_release(repository, monkeypatch):
    run, remote, local, plan, _ = repository
    run("tag", "v0.10.0", cwd=local)
    run("tag", "v99.0.0-rc.1", cwd=local)
    assert plan.latest_tag() == "v0.10.0"
    (local / "source.txt").write_text("docs")
    run("commit", "-am", "docs: edit", cwd=local)
    original = subprocess.check_output

    def check(args, **kwargs):
        if args[0] == "fixture-semantic":
            return "0.10.0\n"
        return original(args, **kwargs)

    monkeypatch.setattr(subprocess, "check_output", check)
    result = plan.calculate(semantic="fixture-semantic")
    assert result["publish"] == "false" and not result["tag"]
    with pytest.raises(ValueError):
        plan.tag_key("v1.02.0")


def test_manifest_requires_both_platforms(repository):
    module = importlib.import_module("verify_manifest")
    data = {"manifests": [{"platform": {"os": "linux", "architecture": "amd64"}}]}
    with pytest.raises(ValueError, match="platforms"):
        module.verify(data)
    data["manifests"].extend([
        {"platform": {"os": "linux", "architecture": "arm64"}},
        {"platform": {"os": "unknown", "architecture": "unknown"}},
    ])
    module.verify(data)


def test_release_recovery_does_not_overwrite_existing_artifact(repository, monkeypatch, tmp_path):
    module = importlib.import_module("publish_release")
    (tmp_path / "prism.whl").write_bytes(b"wheel")
    (tmp_path / "prism.tar.gz").write_bytes(b"sdist")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[2] == "view":
            return subprocess.CompletedProcess(args, 0, '{"assets":[{"name":"prism.whl"}]}')
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    module.publish("v0.9.0", tmp_path)
    assert len(calls) == 2
    assert calls[1] == ["gh", "release", "upload", "v0.9.0", str(tmp_path / "prism.tar.gz")]


def test_failed_draft_release_is_completed(repository, monkeypatch, tmp_path):
    module = importlib.import_module("publish_release")
    (tmp_path / "prism.whl").write_bytes(b"wheel")
    (tmp_path / "prism.tar.gz").write_bytes(b"sdist")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[2] == "view":
            return subprocess.CompletedProcess(
                args, 0, '{"isDraft":true,"assets":[{"name":"prism.whl"}]}'
            )
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    module.publish("v0.9.0", tmp_path)
    assert calls[-1] == ["gh", "release", "edit", "v0.9.0", "--draft=false"]
    assert calls[-2][2] == "upload"


def test_release_creation_includes_both_verified_artifacts(repository, monkeypatch, tmp_path):
    module = importlib.import_module("publish_release")
    (tmp_path / "prism.whl").write_bytes(b"wheel")
    (tmp_path / "prism.tar.gz").write_bytes(b"sdist")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1 if args[2] == "view" else 0)

    monkeypatch.setattr(subprocess, "run", run)
    module.publish("v0.9.0", tmp_path)
    assert calls[1][2] == "create"
    assert "--verify-tag" in calls[1]
    assert calls[1][-2:] == [str(tmp_path / "prism.whl"), str(tmp_path / "prism.tar.gz")]
