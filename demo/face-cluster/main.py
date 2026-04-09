from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from local_imports import import_sibling


FaceClusterWindow = import_sibling("ui").FaceClusterWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Face Cluster MVP")
    window = FaceClusterWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
