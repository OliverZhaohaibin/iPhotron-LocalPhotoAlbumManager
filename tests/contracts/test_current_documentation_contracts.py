from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
PETS_RUNTIME = DOCS / "misc/PETS_RECOGNITION_RUNTIME.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _constant(source: str, name: str) -> str:
    match = re.search(rf"^{name}\s*=\s*([^#\n]+)", source, flags=re.MULTILINE)
    assert match is not None, name
    return match.group(1).strip().strip('"')


def test_current_docs_use_the_production_desktop_runtime_and_entrypoint() -> None:
    architecture = _read(DOCS / "architecture.md")
    development = _read(DOCS / "development.md")
    current_docs = "\n".join(
        (
            architecture,
            development,
            _read(PETS_RUNTIME),
            _read(DOCS / "misc/SCAN_VISIBLE_PUBLISH_GUARDRAILS.md"),
        )
    )
    assert "MainCoordinator" not in current_docs
    assert "main_coordinator.started" not in current_docs
    assert "species-complete-link-v1" not in current_docs
    assert "DesktopCoordinatorRuntime" in architecture
    assert "desktop_coordinator_runtime.started" in development
    assert "People view shown" in architecture
    assert "350 ms quiet interval" in current_docs
    for stale_lifecycle in (
        "Interactive scans feed both AI workers",
        "Startup scans defer both workers",
        "starts both AI workers with closed input",
    ):
        assert stale_lifecycle not in current_docs

    metadata = tomllib.loads(_read(ROOT / "pyproject.toml"))
    entrypoint = metadata["project"]["scripts"]["iphoto-gui"]
    assert entrypoint == "iPhoto.entrypoint:main"
    assert f"`{entrypoint}`" in development


def test_pets_runtime_documentation_tracks_production_constants_and_manifest() -> None:
    pet_doc = _read(PETS_RUNTIME)
    source = _read(ROOT / "src/iPhoto/pets/pipeline.py")
    manifest = json.loads(_read(ROOT / "src/iPhoto/pets/model_manifest.json"))

    assert _constant(source, "PET_CLUSTERING_PIPELINE_VERSION") in pet_doc
    assert _constant(source, "DEFAULT_PET_DISTANCE_THRESHOLD") in pet_doc
    assert _constant(source, "PET_CLUSTER_DIAMETER_MULTIPLIER") in pet_doc
    assert _constant(source, "PET_PEOPLE_IOU_THRESHOLD") in pet_doc
    assert _constant(source, "PET_PEOPLE_SMALLER_BOX_COVERAGE_THRESHOLD") in pet_doc
    assert _constant(source, "PET_PEOPLE_LARGER_PET_RATIO") in pet_doc
    assert _constant(source, "PET_PEOPLE_MURAL_IMAGE_COVERAGE_THRESHOLD") in pet_doc
    assert "cannot-link" in pet_doc
    assert "People view" in pet_doc
    assert "350 ms" in pet_doc
    assert manifest["embedder"]["torchscript_url"] is None
    assert "`torchscript_url` is currently `null`" in pet_doc


def test_current_readmes_share_release_xcb_and_overlap_semantics() -> None:
    required_by_path = {
        ROOT / "README.md": (
            "current development branch",
            "does not force a Wayland session onto XCB",
            "larger pet-body detection",
        ),
        DOCS / "readme/README_zh-CN.md": (
            "当前开发分支",
            "不会强制把 Wayland 会话切换到 XCB",
            "明显更大的宠物身体检测框",
        ),
        DOCS / "readme/README_de.md": (
            "aktuellen Entwicklungszweig",
            "erzwingt keinen Wechsel einer Wayland-Sitzung zu XCB",
            "Tierkörper-Erkennungsrahmen",
        ),
    }
    for path, required_phrases in required_by_path.items():
        contents = _read(path)
        assert "v6.6.8" in contents
        for phrase in required_phrases:
            assert phrase in contents, f"{phrase!r} missing from {path}"


def test_unreleased_changelog_describes_feature_driven_recognition() -> None:
    changelog = _read(DOCS / "CHANGELOG.md")
    unreleased = changelog.split("## 🚀 v6.6.8", 1)[0]
    stale_lifecycle = "Deferred startup Face/Pet AI workers until metadata scanning completes"
    assert stale_lifecycle not in unreleased
    assert "People surface" in unreleased
    assert "first viewport" in unreleased
    assert "350 ms" in unreleased


def test_security_docs_separate_model_storage_and_download_contracts() -> None:
    security = _read(DOCS / "security.md")
    assert "Platform user model caches" in security
    assert "Packaged `extension/models/`" in security
    assert "Explicit `IPHOTO_*_MODEL_DIR` override" in security
    assert "YOLOX detector download" in security
    assert "`torchscript_url` is `null`" in security
