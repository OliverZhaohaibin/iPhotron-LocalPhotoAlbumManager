import sys
import os
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QRadioButton, QButtonGroup, QSpacerItem, QSizePolicy
)
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt


class AspectRatioMenu(QWidget):
    def __init__(self):
        super().__init__()
        self.ensure_check_svg_exists()  # 确保对号SVG文件存在
        self.init_ui()

    def ensure_check_svg_exists(self):
        """在当前目录下动态生成一个对号 SVG 文件，确保 QSS 能稳定读取"""
        self.check_icon_path = "check_indicator.svg"
        svg_content = """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#D0D0D0" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>"""
        if not os.path.exists(self.check_icon_path):
            with open(self.check_icon_path, "w", encoding="utf-8") as f:
                f.write(svg_content)

    def init_ui(self):
        self.setWindowTitle("Aspect Ratio Menu")
        self.resize(200, 400)
        self.setStyleSheet(self.get_stylesheet())

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        # 1. 标题区域
        title_layout = QHBoxLayout()
        title_layout.setSpacing(8)

        icon_label = QLabel()
        icon_path = r"D:\python_code\iPhoto\iPhotos\src/iPhoto/gui/ui/icon/aspect.svg"
        icon_label.setPixmap(QIcon(icon_path).pixmap(16, 16))

        title_label = QLabel("Aspect")
        title_label.setObjectName("TitleLabel")

        title_layout.addWidget(icon_label)
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        main_layout.addLayout(title_layout)

        # 2. 比例选项列表
        options_layout = QVBoxLayout()
        options_layout.setSpacing(2)
        options_layout.setContentsMargins(6, 5, 0, 0)

        self.button_group = QButtonGroup(self)

        aspect_options = [
            "Original", "Freeform", "Square", "16:9",
            "4:5", "5:7", "4:3", "3:5", "3:2", "Custom"
        ]

        for opt in aspect_options:
            btn = QRadioButton(opt)
            self.button_group.addButton(btn)
            options_layout.addWidget(btn)

            # 将默认选中项修改为 Freeform
            if opt == "Freeform":
                btn.setChecked(True)

        main_layout.addLayout(options_layout)
        main_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def get_stylesheet(self):
        # 注意这里的 url 引用了我们动态生成的 SVG 文件路径
        return f"""
        QWidget {{
            background-color: #1e1e1e;
            color: #a0a0a0;
            font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            font-size: 13px;
        }}

        #TitleLabel {{
            color: #dcdcdc;
            font-weight: bold;
            font-size: 14px;
        }}

        QRadioButton {{
            padding: 6px 4px;
            spacing: 12px;
            background-color: transparent;
            border: none;
            outline: none;
        }}

        QRadioButton:hover {{
            color: #ffffff;
        }}

        QRadioButton:checked {{
            color: #dcdcdc;
        }}

        QRadioButton::indicator {{
            width: 16px;
            height: 16px;
        }}

        QRadioButton::indicator:unchecked {{
            image: none; 
        }}

        /* 使用正斜杠替换反斜杠，确保 QSS 路径在 Windows 上也能正确解析 */
        QRadioButton::indicator:checked {{
            image: url({self.check_icon_path.replace(os.sep, '/')});
        }}
        """


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AspectRatioMenu()
    window.show()
    sys.exit(app.exec())