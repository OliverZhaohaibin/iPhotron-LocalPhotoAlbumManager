import sys
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout,
                               QHBoxLayout, QFrame, QGridLayout, QGraphicsDropShadowEffect)
from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt


class AlbumCard(QFrame):
    def __init__(self, title, count, parent=None):
        super().__init__(parent)

        # 1. 整体容器尺寸
        self.setFixedSize(260, 80)
        self.setObjectName("AlbumCard")

        # 2. 布局设置
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # 3. 左侧图片
        self.image_label = QLabel("jpg")
        self.image_label.setFixedSize(80, 80)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setObjectName("ImagePart")

        # 4. 右侧文字容器 (背景透明)
        self.text_container = QWidget()
        self.text_container.setObjectName("TextPart")  # 虽然设置了ID，但我们会在QSS里设为透明

        self.text_layout = QVBoxLayout(self.text_container)
        self.text_layout.setContentsMargins(15, 0, 10, 0)
        self.text_layout.setSpacing(4)
        self.text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 标题
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #1d1d1f; font-size: 14px; font-weight: 600; background: transparent;")

        # 数量
        self.count_label = QLabel(str(count))
        self.count_label.setStyleSheet("color: #86868b; font-size: 13px; background: transparent;")

        self.text_layout.addWidget(self.title_label)
        self.text_layout.addWidget(self.count_label)

        # 添加到布局
        self.layout.addWidget(self.image_label)
        self.layout.addWidget(self.text_container)

        # 5. 样式表 (QSS) - 关键修改
        self.setStyleSheet("""
            /* 父容器：负责整体的白色背景和圆角。
               因为阴影是加在父容器上的，所以这里必须是圆角。
            */
            #AlbumCard {
                background-color: #FFFFFF;
                border-radius: 12px;
            }

            /* 左侧图片：左边圆角(匹配父容器)，右边直角。
               背景色设为灰色占位。
            */
            #ImagePart {
                background-color: #B0BEC5; 
                color: white;
                font-weight: bold;
                border-top-left-radius: 12px;
                border-bottom-left-radius: 12px;
                border-top-right-radius: 0px;
                border-bottom-right-radius: 0px;
            }

            /* 右侧文字区域：背景透明！
               这样显示出来的就是父容器(#AlbumCard)的白色圆角背景。
               既然没有独立的白色方块，自然就不会有直角溢出了。
            */
            #TextPart {
                background-color: transparent;
            }
        """)

        # 6. 添加阴影
        self.add_shadow()

    def add_shadow(self):
        # 阴影加在 AlbumCard (父容器) 上
        # 因为父容器现在是一个完美的圆角矩形，阴影也会是圆滑的
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setXOffset(0)
        shadow.setYOffset(4)
        shadow.setColor(QColor(0, 0, 0, 25))
        self.setGraphicsEffect(shadow)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Perfect Rounded Corners")
        self.resize(900, 600)
        self.setStyleSheet("background-color: #F5F5F7;")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)

        header = QLabel("Albums")
        header.setFont(QFont("Arial", 22, QFont.Weight.Bold))
        header.setStyleSheet("color: #1d1d1f; margin-bottom: 10px;")
        main_layout.addWidget(header)

        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(20)
        grid_layout.setVerticalSpacing(20)

        albums = [
            ("Great Shots", 19), ("Family Memories", 21), ("Delicious Bites", 10),
            ("In the Sun", 20), ("Portfolio Highlights", 20), ("Furry Friends", 19),
            ("Travel Adventures", 32), ("Favorite portrait photos", 12)
        ]

        row, col = 0, 0
        for title, count in albums:
            card = AlbumCard(title, count)
            grid_layout.addWidget(card, row, col)
            col += 1
            if col > 2:
                col = 0
                row += 1

        main_layout.addLayout(grid_layout)
        main_layout.addStretch()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())