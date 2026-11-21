from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"


def _job_block(workflow: str, job_name: str) -> str:
    lines = workflow.splitlines()
    header = f"  {job_name}:"
    start = lines.index(header)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end = index
            break
    return "\n".join(lines[start:end])


def test_full_contract_workflow_covers_head_merge_ref_and_merge_push() -> None:
    workflow = TEST_WORKFLOW.read_text(encoding="utf-8")

    assert "branches: [main, edit-base, codex/startup-chain-optimization]" in workflow
    assert "pull_request:\n    branches: [main, edit-base]" in workflow
    production_shape = _job_block(workflow, "pets-production-shape-contract")
    assert "github.event_name" not in production_shape
    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in production_shape
    assert "timeout-minutes: 30" in production_shape
