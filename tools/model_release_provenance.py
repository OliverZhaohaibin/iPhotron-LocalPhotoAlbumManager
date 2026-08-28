#!/usr/bin/env python3
"""Validate that a model artifact, workflow run, and Release tag share one builder."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class ProvenanceError(ValueError):
    pass


def validate_release_provenance(
    *,
    run: dict[str, Any],
    build: dict[str, Any],
    builder_commit: str,
    expected_repository: str,
    expected_workflow_id: int,
    expected_workflow_path: str,
) -> str:
    try:
        run_id = int(run["id"])
        workflow_id = int(run["workflow_id"])
        repository = str(run["repository"]["full_name"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProvenanceError("artifact run metadata is incomplete") from exc
    head_sha = str(run.get("head_sha") or "").lower()
    if re.fullmatch(r"[0-9a-f]{40}", head_sha) is None:
        raise ProvenanceError("artifact run head_sha is invalid")
    if str(run.get("conclusion") or "") != "success":
        raise ProvenanceError("artifact run did not complete successfully")
    if workflow_id != int(expected_workflow_id):
        raise ProvenanceError("artifact run belongs to a different workflow")
    if str(run.get("path") or "") != expected_workflow_path:
        raise ProvenanceError("artifact run workflow path is invalid")
    if repository != expected_repository:
        raise ProvenanceError("artifact run belongs to a different repository")
    if str(builder_commit).lower() != head_sha:
        raise ProvenanceError("builder checkout does not match artifact run head_sha")
    if str(build.get("repository_commit") or "").lower() != head_sha:
        raise ProvenanceError("build manifest repository_commit does not match run head_sha")
    if str(build.get("workflow_run_id") or "") != str(run_id):
        raise ProvenanceError("build manifest workflow_run_id does not match artifact run")
    return head_sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-json", required=True, type=Path)
    parser.add_argument("--build-manifest", required=True, type=Path)
    parser.add_argument("--builder-commit", required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-workflow-id", required=True, type=int)
    parser.add_argument("--expected-workflow-path", required=True)
    args = parser.parse_args(argv)
    try:
        head_sha = validate_release_provenance(
            run=json.loads(args.run_json.read_text(encoding="utf-8")),
            build=json.loads(args.build_manifest.read_text(encoding="utf-8")),
            builder_commit=args.builder_commit,
            expected_repository=args.expected_repository,
            expected_workflow_id=args.expected_workflow_id,
            expected_workflow_path=args.expected_workflow_path,
        )
    except (OSError, json.JSONDecodeError, ProvenanceError) as exc:
        print(f"model release provenance error: {exc}", file=sys.stderr)
        return 2
    print(head_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
