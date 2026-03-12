"""Entry-point: ``python -m demo.video``."""

import sys

from PySide6.QtWidgets import QApplication

from ui import VideoEditor  # noqa: E402  (relative bare import)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoEditor()
    window.show()
    sys.exit(app.exec())
