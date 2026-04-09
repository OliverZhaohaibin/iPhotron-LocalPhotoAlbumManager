from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from db import FaceClusterRepository, PersonSummary, RuntimeWorkspace, prepare_runtime_workspace
from pipeline import FaceClusterPipeline


@dataclass(frozen=True)
class WorkerResult:
    workspace: RuntimeWorkspace
    person_summaries: list[PersonSummary]
    image_count: int
    face_count: int
    cluster_count: int
    warnings: list[str]


class FaceClusterWorker(QThread):
    progress_changed = Signal(int, int)
    status_changed = Signal(str)
    finished_with_result = Signal(object)
    failed = Signal(str)

    def __init__(self, folder: Path, parent=None) -> None:
        super().__init__(parent)
        self._folder = Path(folder)
        self._pipeline = FaceClusterPipeline()

    def run(self) -> None:  # type: ignore[override]
        try:
            workspace = prepare_runtime_workspace(self._folder)
            pipeline_result = self._pipeline.scan_folder(
                self._folder,
                workspace.root_path,
                workspace.thumbnail_dir,
                progress_callback=self._emit_progress,
                status_callback=self._emit_status,
            )
            repository = FaceClusterRepository(workspace.db_path)
            repository.replace_all(pipeline_result.faces, pipeline_result.persons)
            summaries = repository.get_person_summaries()
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        self.finished_with_result.emit(
            WorkerResult(
                workspace=workspace,
                person_summaries=summaries,
                image_count=pipeline_result.image_count,
                face_count=pipeline_result.face_count,
                cluster_count=pipeline_result.cluster_count,
                warnings=list(pipeline_result.warning_messages),
            )
        )

    def _emit_progress(self, current: int, total: int) -> None:
        self.progress_changed.emit(int(current), int(total))

    def _emit_status(self, message: str) -> None:
        self.status_changed.emit(message)
