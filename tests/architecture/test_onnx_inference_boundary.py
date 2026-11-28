"""Model-development runtimes must never reenter production inference."""

import ast
from pathlib import Path


def test_production_sources_do_not_import_model_development_runtimes():
    root = Path(__file__).resolve().parents[2] / "src"
    forbidden = {"torch", "torchvision", "torchaudio", "triton"}
    violations = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imports = []
            if isinstance(node, ast.Import):
                imports = [name.name for name in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            if any(name.split(".")[0] in forbidden for name in imports):
                violations.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not violations, violations
