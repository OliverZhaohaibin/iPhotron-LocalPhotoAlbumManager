import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    color: "#2b2b2b"

    // Configuration
    property int minCellWidth: 192
    property int cellGap: 2
    property int safetyMargin: 10
    property bool selectionModeActive: false

    Component.onCompleted: {
        console.log("GalleryView Loaded. AssetModel:", assetModel)
        console.log("SelectionModel:", selectionModel)
        console.log("IconPath:", iconPath)
        console.log("GridView Count:", gridView.count)
    }

    signal itemClicked(var index, var modifiers)
    signal itemDoubleClicked(var index)
    signal requestPreview(var index)
    signal previewReleased()
    signal previewCancelled()
    signal visibleRowsChanged(int first, int last)

    function setSelectionMode(enabled) {
        root.selectionModeActive = enabled
        if (!enabled) {
            gridView.forceActiveFocus()
        }
    }

    GridView {
        id: gridView
        anchors.fill: parent
        anchors.margins: 0
        clip: true

        model: assetModel
        cellWidth: 200
        cellHeight: 200

        highlightFollowsCurrentItem: false
        focus: true

        property int firstVisibleRow: -1
        property int lastVisibleRow: -1

        onContentYChanged: {
            updateVisibleRange()
        }
        onHeightChanged: {
            updateVisibleRange()
            recalcCellSize()
        }
        onWidthChanged: {
            recalcCellSize()
        }

        function updateVisibleRange() {
            var start = indexAt(contentX + 10, contentY + 10)
            var end = indexAt(contentX + width - 10, contentY + height - 10)

            if (start === -1) start = 0
            if (end === -1) {
                 var visibleRows = Math.ceil(height / cellHeight)
                 var cols = Math.floor(width / cellWidth)
                 end = Math.min(count - 1, start + (visibleRows * cols))
            }

            if (start !== firstVisibleRow || end !== lastVisibleRow) {
                firstVisibleRow = start
                lastVisibleRow = end
                var buffer = 20
                var rStart = Math.max(0, start - buffer)
                var rEnd = Math.min(count - 1, end + buffer)
                root.visibleRowsChanged(rStart, rEnd)
            }
        }

        function recalcCellSize() {
            var viewportWidth = width
            if (viewportWidth <= 0) return

            var availableWidth = viewportWidth - root.safetyMargin
            var cols = Math.max(1, Math.floor(availableWidth / (root.minCellWidth + root.cellGap)))
            var size = Math.floor((viewportWidth - root.safetyMargin) / cols)

            cellWidth = size
            cellHeight = size
        }

        // Drag Select Logic
        property int dragStartIndex: -1
        property int lastDragIndex: -1
        property bool isDragging: false

        delegate: Item {
            id: delegateItem
            width: gridView.cellWidth
            height: gridView.cellHeight

            // Force reload when thumbVersion changes
            property string thumbSource: model.thumbUrl ? (model.thumbUrl + "?v=" + (model.thumbVersion || 0)) : ""

            property bool isSelected: delegateItem.GridView.view.selectionModel && delegateItem.GridView.view.selectionModel.isSelected(model.index)

            Rectangle {
                anchors.fill: parent
                anchors.margins: root.cellGap / 2
                color: "#333333" // Lighter placeholder for visibility

                // Debug Text
                Text {
                    anchors.centerIn: parent
                    text: index
                    color: "white"
                    font.pixelSize: 20
                    visible: thumbImage.status !== Image.Ready
                }

                Image {
                    id: thumbImage
                    anchors.fill: parent
                    source: thumbSource
                    asynchronous: true
                    cache: true
                    fillMode: Image.PreserveAspectCrop
                    smooth: true
                    mipmap: true

                    // Removed opacity animation for debugging
                }

                // Selection Overlay (Active Focus Single Selection)
                Rectangle {
                    anchors.fill: parent
                    color: Qt.rgba(1, 1, 1, 0.2)
                    visible: delegateItem.GridView.view.activeFocus && delegateItem.GridView.view.currentIndex === index && !root.selectionModeActive && !delegateItem.isSelected
                }

                // Multi-Selection Overlay (Persistent)
                Rectangle {
                    anchors.fill: parent
                    color: Qt.rgba(1, 1, 1, 0.4)
                    visible: delegateItem.isSelected

                    // Checkmark
                    Image {
                         source: "file:///" + root.iconPath + "/checkmark.circle.svg"
                         width: 30
                         height: 30
                         anchors.right: parent.right
                         anchors.bottom: parent.bottom
                         anchors.margins: 10
                    }
                }

                // Badges
                Item {
                    anchors.fill: parent

                    Image {
                        source: "file:///" + root.iconPath + "/livephoto.svg"
                        width: 18
                        height: 18
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.margins: 8
                        visible: model.isLive
                    }

                    Image {
                        source: "file:///" + root.iconPath + "/suit.heart.fill.svg"
                        width: 16
                        height: 16
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        anchors.margins: 8
                        visible: model.featured
                    }

                    Rectangle {
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.margins: 8
                        height: 20
                        width: durationText.contentWidth + 12
                        radius: 6
                        color: Qt.rgba(0, 0, 0, 0.6)
                        visible: model.isVideo && model.size && model.size.duration > 0 && !delegateItem.isSelected

                        Text {
                            id: durationText
                            anchors.centerIn: parent
                            text: formatDuration(model.size ? model.size.duration : 0)
                            color: "white"
                            font.pixelSize: 11
                            font.bold: true
                        }
                    }
                }

                MouseArea {
                    id: mouseArea
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    hoverEnabled: true

                    onClicked: (mouse) => {
                        gridView.currentIndex = index
                        root.itemClicked(index, mouse.modifiers)
                    }

                    onPressAndHold: {
                        if (root.selectionModeActive) return
                        root.requestPreview(index)
                    }
                    onReleased: {
                        root.previewReleased()
                        if (gridView.isDragging) {
                            gridView.isDragging = false
                            gridView.dragStartIndex = -1
                            gridView.lastDragIndex = -1
                        }
                    }
                    onCanceled: {
                        root.previewCancelled()
                         if (gridView.isDragging) {
                            gridView.isDragging = false
                            gridView.dragStartIndex = -1
                            gridView.lastDragIndex = -1
                        }
                    }

                    // Drag Selection Logic
                    onPressed: (mouse) => {
                         if (root.selectionModeActive) return // If explicit mode, normal clicks handle it?
                         // User requirement: "must support drag-to-select (slide selection)"
                         // Usually this implies starting a drag selects items.

                         gridView.isDragging = true
                         gridView.dragStartIndex = index
                         gridView.lastDragIndex = index
                    }

                    onPositionChanged: (mouse) => {
                        if (gridView.isDragging) {
                            var globalPos = mapToItem(gridView, mouse.x, mouse.y)
                            var currentIndex = gridView.indexAt(globalPos.x, globalPos.y)

                            if (currentIndex !== -1 && currentIndex !== gridView.lastDragIndex) {
                                gridView.lastDragIndex = currentIndex
                                root.itemClicked(currentIndex, Qt.ControlModifier)
                            }
                        }
                    }
                }
            }
        }

        // Animations
        add: Transition {
            NumberAnimation { property: "scale"; from: 0; to: 1; duration: 250; easing.type: Easing.OutQuad }
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 250 }
        }
        remove: Transition {
            NumberAnimation { property: "scale"; to: 0; duration: 250; easing.type: Easing.InQuad }
            NumberAnimation { property: "opacity"; to: 0; duration: 250 }
        }
        displaced: Transition {
            NumberAnimation { properties: "x,y"; duration: 300; easing.type: Easing.OutCubic }
        }
    }

    function formatDuration(seconds) {
        if (!seconds) return "0:00"
        var s = Math.round(seconds)
        var m = Math.floor(s / 60)
        var h = Math.floor(m / 60)
        s = s % 60
        m = m % 60
        var sStr = s < 10 ? "0" + s : s
        if (h > 0) {
            var mStr = m < 10 ? "0" + m : m
            return h + ":" + mStr + ":" + sStr
        }
        return m + ":" + sStr
    }
}
