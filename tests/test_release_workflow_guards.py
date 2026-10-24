"""Contract tests for release-tag gating in publish workflows.

The PyPI workflow keeps upstream's ``validate-release-tag`` guard. The Docker
workflow was rewritten for this fork's private GHCR package: publication runs
on release events or an operator-triggered ``workflow_dispatch``, and image
tags come from ``docker/metadata-action`` semver parsing instead of the
validator job. Its tests therefore assert the fork's actual contract:
non-semver release tags can only ever produce the ``latest`` tag, and manual
dispatch can only add an explicit operator-supplied version tag.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOWS = {
    "pypi": (
        REPOSITORY_ROOT / ".github" / "workflows" / "pypi-release.yml",
        "build-and-publish",
    ),
}
DOCKER_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "docker-release.yml"


def _workflow(publication: str) -> tuple[dict, str]:
    workflow_path, publish_job_name = RELEASE_WORKFLOWS[publication]
    with workflow_path.open(encoding="utf-8") as file:
        return yaml.safe_load(file), publish_job_name


def _validator_script(publication: str) -> str:
    document, _ = _workflow(publication)
    validator = document["jobs"]["validate-release-tag"]
    return validator["steps"][0]["run"]


def _run_validator(publication: str, tag: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    output = tmp_path / f"{publication}-output.txt"
    return subprocess.run(
        [sys.executable, "-c", _validator_script(publication)],
        env={
            "RELEASE_TAG": tag,
            "GITHUB_OUTPUT": str(output),
            "PATH": os.environ["PATH"],
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("publication", RELEASE_WORKFLOWS)
def test_non_version_release_tags_skip_publication(publication: str) -> None:
    document, publish_job_name = _workflow(publication)
    validator = document["jobs"]["validate-release-tag"]

    assert validator["if"] == "startsWith(github.event.release.tag_name, 'v')"
    assert document["jobs"][publish_job_name]["needs"] == "validate-release-tag"


@pytest.mark.parametrize(
    ("publication", "tag"),
    [
        ("pypi", "v1.2.3"),
        ("pypi", "v1.2.3rc1"),
        ("pypi", "v1.2.3+build.1"),
    ],
)
def test_version_release_tags_pass_the_guard(publication: str, tag: str, tmp_path: Path) -> None:
    result = _run_validator(publication, tag, tmp_path)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("publication", "tag"),
    [
        ("pypi", "vmain"),
        ("pypi", "v1.2"),
        ("pypi", "v1.2.x"),
        ("pypi", "v01.2.3"),
        ("pypi", "v1.2.3+"),
        ("pypi", "v1.2.3...."),
    ],
)
def test_malformed_version_tags_fail_the_guard(publication: str, tag: str, tmp_path: Path) -> None:
    result = _run_validator(publication, tag, tmp_path)

    assert result.returncode != 0
    assert repr(tag) in result.stderr


def _docker_workflow() -> dict:
    with DOCKER_WORKFLOW.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def _docker_meta_step(document: dict) -> dict:
    return next(
        step for step in document["jobs"]["build-and-push"]["steps"] if step.get("id") == "meta"
    )


def test_docker_version_tags_come_only_from_semver_parsing() -> None:
    document = _docker_workflow()
    tags = _docker_meta_step(document)["with"]["tags"]

    # metadata-action ignores non-semver release tags for this entry, so a
    # non-version tag can never be published as a version image tag.
    assert "type=semver,pattern={{version}}" in tags
    # The unconditioned latest entry must be release-event-only; every other
    # raw entry needs an explicit operator-supplied version input.
    assert "type=raw,value=latest" in tags
    assert all(
        "enable=${{ inputs.version != '' }}" in entry
        for entry in tags.splitlines()
        if entry.startswith("type=raw") and "value=latest" not in entry
    )


def test_docker_manual_dispatch_builds_authenticated_push_only() -> None:
    document = _docker_workflow()
    triggers = document[True] if True in document else document["on"]

    assert "workflow_dispatch" in triggers
    assert set(triggers["workflow_dispatch"]["inputs"]) >= {"ref", "version"}
    build = document["jobs"]["build-and-push"]
    login = next(
        step for step in build["steps"] if step.get("name") == "Log in to GitHub Container Registry"
    )
    assert login["with"]["registry"] == "ghcr.io"
    push = next(
        step
        for step in build["steps"]
        if step.get("uses", "").startswith("docker/build-push-action")
    )
    assert push["with"]["push"] is True
