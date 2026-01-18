import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

/**
 * QML-based Gallery Grid View for displaying photo thumbnails.
 * Uses native QML rendering pipeline with explicit opaque background
 * to ensure compatibility with Windows frameless/DWM windows.
 */
Rectangle {
    id: root

    // Background must be explicitly opaque for frameless window compatibility
    color: palette.base

    // Minimum item dimensions
    readonly property int minItemWidth: 192
    readonly property int itemGap: 2
    readonly property int safetyMargin: 10

    // Model from Python
    property var assetModel: null

    // Signals for interaction
    signal itemClicked(int index)
    signal itemDoubleClicked(int index)
    signal requestPreview(int index)
    signal previewReleased()
    signal previewCancelled()
    signal visibleRowsChanged(int first, int last)

    // Selection mode state
    property bool selectionModeEnabled: false

    // Computed column count based on available width
    property int columnCount: {
        var availableWidth = root.width - safetyMargin
        return Math.max(1, Math.floor(availableWidth / (minItemWidth + itemGap)))
    }

    // Computed cell size
    property int cellSize: {
        return Math.floor((root.width - safetyMargin) / columnCount)
    }

    // Computed item size
    property int itemSize: {
        return cellSize - itemGap
    }

    function clearSelection() {
        gridView.currentIndex = -1
    }

    // Access the system palette for theming
    SystemPalette {
        id: palette
    }

    GridView {
        id: gridView
        objectName: "gridView"
        anchors.fill: parent
        anchors.margins: 0

        // Ensure opaque background
        clip: true

        model: root.assetModel

        cellWidth: root.cellSize
        cellHeight: root.cellSize

        // Smooth scrolling
        flickDeceleration: 3000
        maximumFlickVelocity: 5000
        boundsBehavior: Flickable.StopAtBounds

        // ScrollBar styling
        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
            minimumSize: 0.1
        }

        delegate: Item {
            id: delegateRoot
            width: gridView.cellWidth
            height: gridView.cellHeight

            required property int index
            required property var model

            // Visual item with gap
            Rectangle {
                id: thumbnailContainer
                anchors.fill: parent
                anchors.margins: root.itemGap / 2
                color: "#1b1b1b"
                clip: true

                // Thumbnail image
                Image {
                    id: thumbnailImage
                    anchors.fill: parent
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                    cache: true
                    smooth: true
                    mipmap: true

                    // Use image provider for thumbnails
                    source: delegateRoot.model.rel ? "image://thumbnail/" + delegateRoot.model.rel : ""

                    // Show placeholder while loading
                    Rectangle {
                        anchors.fill: parent
                        color: "#1b1b1b"
                        visible: thumbnailImage.status !== Image.Ready
                    }
                }

                // Selection overlay
                Rectangle {
                    anchors.fill: parent
                    color: palette.highlight
                    opacity: 0.4
                    visible: delegateRoot.model.isCurrent || (root.selectionModeEnabled && delegateRoot.ListView.isCurrentItem)
                }

                // Live Photo badge
                Rectangle {
                    visible: delegateRoot.model.isLive === true
                    width: liveLabel.implicitWidth + 12
                    height: 20
                    radius: 4
                    color: "#80000000"
                    anchors.left: parent.left
                    anchors.bottom: parent.bottom
                    anchors.margins: 6

                    Text {
                        id: liveLabel
                        anchors.centerIn: parent
                        text: "LIVE"
                        color: "white"
                        font.pixelSize: 10
                        font.weight: Font.Bold
                    }
                }

                // Video duration badge
                Rectangle {
                    visible: delegateRoot.model.isVideo === true && delegateRoot.model.size && delegateRoot.model.size.duration > 0
                    width: durationLabel.implicitWidth + 12
                    height: 20
                    radius: 4
                    color: "#80000000"
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: 6

                    Text {
                        id: durationLabel
                        anchors.centerIn: parent
                        text: {
                            var duration = delegateRoot.model.size ? delegateRoot.model.size.duration || 0 : 0
                            var minutes = Math.floor(duration / 60)
                            var seconds = Math.floor(duration % 60)
                            return minutes + ":" + (seconds < 10 ? "0" : "") + seconds
                        }
                        color: "white"
                        font.pixelSize: 10
                        font.weight: Font.Medium
                    }
                }

                // Favorite badge
                Rectangle {
                    visible: delegateRoot.model.featured === true
                    width: 24
                    height: 24
                    radius: 12
                    color: "#80000000"
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 6

                    Text {
                        anchors.centerIn: parent
                        text: "♥"
                        color: "#FF6B6B"
                        font.pixelSize: 14
                    }
                }

                // Selection badge (in selection mode)
                Rectangle {
                    visible: root.selectionModeEnabled && delegateRoot.ListView.isCurrentItem
                    width: 24
                    height: 24
                    radius: 12
                    color: palette.highlight
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.margins: 6

                    Text {
                        anchors.centerIn: parent
                        text: "✓"
                        color: "white"
                        font.pixelSize: 14
                        font.weight: Font.Bold
                    }
                }
            }

            // Mouse handling
            MouseArea {
                id: mouseArea
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                hoverEnabled: true

                property bool longPressActive: false

                onClicked: function(mouse) {
                    if (mouse.button === Qt.LeftButton) {
                        root.itemClicked(delegateRoot.index)
                    }
                }

                onDoubleClicked: function(mouse) {
                    if (mouse.button === Qt.LeftButton) {
                        root.itemDoubleClicked(delegateRoot.index)
                    }
                }

                onPressAndHold: {
                    longPressActive = true
                    root.requestPreview(delegateRoot.index)
                }

                onReleased: {
                    if (longPressActive) {
                        longPressActive = false
                        root.previewReleased()
                    }
                }

                onExited: {
                    if (longPressActive) {
                        longPressActive = false
                        root.previewCancelled()
                    }
                }
            }
        }

        // Report visible rows changed
        onContentYChanged: {
            updateVisibleRows()
        }

        onHeightChanged: {
            updateVisibleRows()
        }

        function updateVisibleRows() {
            if (!model) return

            var totalRows = model.rowCount ? model.rowCount() : (model.count || 0)
            if (totalRows === 0) return

            var firstVisibleIndex = indexAt(0, contentY)
            var lastVisibleIndex = indexAt(width - 1, contentY + height)

            if (firstVisibleIndex < 0) firstVisibleIndex = 0
            if (lastVisibleIndex < 0) lastVisibleIndex = totalRows - 1

            // Add buffer
            var buffer = 20
            firstVisibleIndex = Math.max(0, firstVisibleIndex - buffer)
            lastVisibleIndex = Math.min(totalRows - 1, lastVisibleIndex + buffer)

            root.visibleRowsChanged(firstVisibleIndex, lastVisibleIndex)
        }
    }

    // Ensure we update visible rows on model changes
    Connections {
        target: root.assetModel
        function onModelReset() {
            Qt.callLater(gridView.updateVisibleRows)
        }
        function onRowsInserted() {
            Qt.callLater(gridView.updateVisibleRows)
        }
        function onRowsRemoved() {
            Qt.callLater(gridView.updateVisibleRows)
        }
    }
}
