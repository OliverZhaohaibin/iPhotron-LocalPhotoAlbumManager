from __future__ import annotations

import pytest

from tools.model_release_provenance import ProvenanceError, validate_release_provenance


HEAD = "a" * 40
WORKFLOW_ID = 340207631
WORKFLOW_PATH = ".github/workflows/pets-dino-source-contract.yml"
REPOSITORY = "OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager"


def _run(**overrides) -> dict:
    value = {
        "id": 123,
        "workflow_id": WORKFLOW_ID,
        "conclusion": "success",
        "head_sha": HEAD,
        "path": WORKFLOW_PATH,
        "repository": {"full_name": REPOSITORY},
    }
    value.update(overrides)
    return value


def _build(**overrides) -> dict:
    value = {"repository_commit": HEAD, "workflow_run_id": "123"}
    value.update(overrides)
    return value


def _validate(*, run=None, build=None, builder_commit=HEAD) -> str:
    return validate_release_provenance(
        run=run or _run(),
        build=build or _build(),
        builder_commit=builder_commit,
        expected_repository=REPOSITORY,
        expected_workflow_id=WORKFLOW_ID,
        expected_workflow_path=WORKFLOW_PATH,
    )


def test_valid_release_provenance_returns_artifact_head() -> None:
    assert _validate() == HEAD


@pytest.mark.parametrize(
    ("run", "message"),
    [
        (_run(head_sha=""), "head_sha"),
        (_run(conclusion="failure"), "successfully"),
        (_run(workflow_id=1), "different workflow"),
        (_run(path=".github/workflows/other.yml"), "workflow path"),
        (_run(repository={"full_name": "other/repo"}), "different repository"),
    ],
)
def test_invalid_artifact_run_is_rejected(run: dict, message: str) -> None:
    with pytest.raises(ProvenanceError, match=message):
        _validate(run=run)


def test_builder_checkout_must_match_run_head() -> None:
    with pytest.raises(ProvenanceError, match="builder checkout"):
        _validate(builder_commit="b" * 40)


def test_build_manifest_commit_must_match_run_head() -> None:
    with pytest.raises(ProvenanceError, match="repository_commit"):
        _validate(build=_build(repository_commit="b" * 40))


def test_build_manifest_run_id_must_match_artifact_run() -> None:
    with pytest.raises(ProvenanceError, match="workflow_run_id"):
        _validate(build=_build(workflow_run_id="999"))
