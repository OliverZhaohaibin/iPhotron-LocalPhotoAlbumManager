import QtQuick 2.15
import QtQuick.Controls 2.15

Rectangle {
    id: root
    color: backgroundColor

    // Signals to communicate with Python controller
    signal itemClicked(int index, int modifiers)
    signal itemDoubleClicked(int index)
    signal currentIndexChanged(int index)
    signal showContextMenu(int index, int globalX, int globalY)
    signal visibleRowsChanged(int first, int last)
    signal filesDropped(var urls)

    property int minItemWidth: 192
    property int itemGap: 2
    property int safetyMargin: 10
    property bool selectionMode: false
    
    // Themeable colors
    property color backgroundColor: "#2b2b2b"
    property color itemBackgroundColor: "#1e1e1e"
    property color selectionBorderColor: "#007AFF"
    property color currentBorderColor: "#FFFFFF"

    // Debounce timer for visible rows update
    Timer {
        id: visibleRowsDebounceTimer
        interval: 100
        repeat: false
        onTriggered: grid.updateVisibleRows()
    }

    DropArea {
        anchors.fill: parent
        onDropped: (drag) => {
            if (drag.hasUrls) {
                var urlList = []
                for (var i = 0; i < drag.urls.length; i++) {
                    urlList.push(drag.urls[i])
                }
                root.filesDropped(urlList)
                drag.accept()
            }
        }
    }

    GridView {
        id: grid
        anchors.fill: parent
        anchors.margins: 0
        focus: true
        clip: true

        model: assetModel

        cellWidth: {
            var avail = root.width - safetyMargin
            var cols = Math.max(1, Math.floor(avail / (minItemWidth + itemGap)))
            // Distribute remaining space
            return Math.floor(avail / cols)
        }
        cellHeight: cellWidth

        onContentYChanged: visibleRowsDebounceTimer.restart()
        onHeightChanged: visibleRowsDebounceTimer.restart()
        onCurrentIndexChanged: root.currentIndexChanged(currentIndex)

        // Initial check after layout
        Component.onCompleted: updateVisibleRows()

        function formatDuration(seconds) {
            if (seconds === null || seconds === undefined) return ""
            var sec = Math.floor(seconds)
            var min = Math.floor(sec / 60)
            sec = sec % 60
            return min + ":" + (sec < 10 ? "0" + sec : sec)
        }

        function updateVisibleRows() {
            if (grid.count === 0) return

            // Calculate approximate visible range
            // indexAt returns index at content coordinates
            var first = grid.indexAt(grid.cellWidth / 2, grid.contentY + grid.cellHeight / 2)
            var last = grid.indexAt(grid.width - grid.cellWidth / 2, grid.contentY + grid.height - grid.cellHeight / 2)

            if (first === -1 && grid.contentY <= 0) first = 0
            if (last === -1) last = grid.count - 1

            if (first !== -1 && last !== -1) {
                // Debounce or just emit? Python side debounces usually.
                root.visibleRowsChanged(first, last)
            }
        }

        delegate: Item {
            width: grid.cellWidth
            height: grid.cellHeight

            Rectangle {
                anchors.fill: parent
                anchors.margins: 1 // Half of gap
                color: root.itemBackgroundColor

                // Selection Background
                Rectangle {
                    anchors.fill: parent
                    color: "transparent"
                    border.color: model.isCurrent && !model.isSelected ? root.currentBorderColor : root.selectionBorderColor
                    border.width: 3
                    visible: model.isSelected || model.isCurrent
                    z: 2
                }

                // Content
                Image {
                    id: thumb
                    anchors.fill: parent
                    anchors.margins: (model.isSelected || model.isCurrent) ? 3 : 0
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                    cache: false

                    // Bind source to rel AND revision to force reload
                    source: "image://thumbnails/" + model.rel + "?v=" + model.thumbnailRev

                    // Video duration badge
                    Rectangle {
                        anchors.bottom: parent.bottom
                        anchors.right: parent.right
                        anchors.margins: 4
                        width: durationLabel.width + 8
                        height: durationLabel.height + 4
                        radius: 4
                        color: "#80000000"
                        visible: model.isVideo

                        Text {
                            id: durationLabel
                            anchors.centerIn: parent
                            text: formatDuration(model.info.dur)
                            color: "white"
                            font.pixelSize: 10
                        }
                    }

                    // Live Photo badge
                    Rectangle {
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.margins: 4
                        width: 16
                        height: 16
                        radius: 8
                        color: "#80000000"
                        visible: model.isLive

                        Rectangle {
                            anchors.centerIn: parent
                            width: 8
                            height: 8
                            radius: 4
                            color: "#FFCC00"
                        }
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton | Qt.RightButton

                    onClicked: (mouse) => {
                        if (mouse.button === Qt.LeftButton) {
                            if (root.selectionMode) {
                                model.isSelected = !model.isSelected
                            } else {
                                grid.currentIndex = index
                                root.itemClicked(index, mouse.modifiers)
                            }
                        } else if (mouse.button === Qt.RightButton) {
                            // Emit global coordinates for context menu
                            // mapToGlobal returns point relative to screen
                            var globalPt = mapToGlobal(mouse.x, mouse.y)
                            root.showContextMenu(index, globalPt.x, globalPt.y)
                        }
                    }

                    onDoubleClicked: (mouse) => {
                        if (mouse.button === Qt.LeftButton) {
                            root.itemDoubleClicked(index)
                        }
                    }

                    onPressAndHold: (mouse) => {
                        // Long press support (optional)
                    }
                }
            }
        }

        ScrollBar.vertical: ScrollBar {
            active: true
        }
    }
}
