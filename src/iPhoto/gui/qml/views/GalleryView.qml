import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles

/**
 * Gallery view displaying assets in a responsive grid layout.
 * 
 * This is a QML-native implementation that can be used alongside or
 * as a replacement for the existing GalleryGrid.qml widget.
 * 
 * Features:
 * - Responsive grid that adjusts to window width
 * - Thumbnail lazy loading with placeholder
 * - Selection support (single and multi-select)
 * - Video duration badges
 * - Live Photo indicators
 * - Drag and drop import
 * - Context menu support
 */
Rectangle {
    id: root
    
    // Properties
    property alias model: grid.model
    property int minItemWidth: Styles.Theme.gridMinItemWidth
    property int itemGap: Styles.Theme.gridItemGap
    property bool selectionMode: false
    property color backgroundColor: Styles.Theme.gridBackground
    property color itemBackgroundColor: Styles.Theme.gridItemBackground
    property color selectionBorderColor: Styles.Theme.gridSelectionBorder
    property color currentBorderColor: Styles.Theme.gridCurrentBorder
    
    // Signals
    signal itemClicked(int index, int modifiers)
    signal itemDoubleClicked(int index)
    signal currentIndexChanged(int index)
    signal showContextMenu(int index, int globalX, int globalY)
    signal visibleRowsChanged(int first, int last)
    signal filesDropped(var urls)
    
    color: backgroundColor
    
    // Ensure background is always visible
    Rectangle {
        anchors.fill: parent
        color: backgroundColor
        z: Styles.Theme.zBackground
    }
    
    // Debounce timer for visible rows updates
    Timer {
        id: visibleRowsDebounceTimer
        interval: 100
        repeat: false
        onTriggered: grid.updateVisibleRows()
    }
    
    // Drop area for file import
    DropArea {
        anchors.fill: parent
        
        onDropped: function(drag) {
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
        
        cellWidth: {
            var avail = root.width - 10  // Safety margin
            var cols = Math.max(1, Math.floor(avail / (root.minItemWidth + root.itemGap)))
            return Math.floor(avail / cols)
        }
        cellHeight: cellWidth
        
        // Optimize for large datasets
        cacheBuffer: cellHeight * 4
        
        onContentYChanged: visibleRowsDebounceTimer.restart()
        onHeightChanged: visibleRowsDebounceTimer.restart()
        onCurrentIndexChanged: root.currentIndexChanged(currentIndex)
        
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
            
            var first = grid.indexAt(grid.cellWidth / 2, grid.contentY + grid.cellHeight / 2)
            var last = grid.indexAt(grid.width - grid.cellWidth / 2, grid.contentY + grid.height - grid.cellHeight / 2)
            
            if (first === -1 && grid.contentY <= 0) first = 0
            if (last === -1) last = grid.count - 1
            
            if (first !== -1 && last !== -1) {
                root.visibleRowsChanged(first, last)
                if (typeof assetController !== "undefined") {
                    assetController.prioritizeRows(first, last)
                }
            }
        }
        
        delegate: Item {
            id: delegateRoot
            width: grid.cellWidth
            height: grid.cellHeight
            
            Rectangle {
                anchors.fill: parent
                anchors.margins: 1
                color: root.itemBackgroundColor
                
                // Selection border
                Rectangle {
                    anchors.fill: parent
                    color: "transparent"
                    border.color: model.isCurrent && !model.isSelected ? 
                                  root.currentBorderColor : root.selectionBorderColor
                    border.width: 3
                    visible: model.isSelected || model.isCurrent
                    z: 2
                }
                
                // Thumbnail image
                Image {
                    id: thumb
                    anchors.fill: parent
                    anchors.margins: (model.isSelected || model.isCurrent) ? 3 : 0
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                    cache: false
                    
                    source: {
                        var relPath = model.rel || ""
                        var rev = model.thumbnailRev || 0
                        return "image://thumbnails/" + relPath + "?v=" + rev
                    }
                    
                    // Loading placeholder
                    Rectangle {
                        anchors.fill: parent
                        color: Styles.Theme.surface
                        visible: thumb.status === Image.Loading
                        
                        BusyIndicator {
                            anchors.centerIn: parent
                            running: parent.visible
                            width: 24
                            height: 24
                        }
                    }
                    
                    // Error placeholder
                    Rectangle {
                        anchors.fill: parent
                        color: Styles.Theme.surface
                        visible: thumb.status === Image.Error
                        
                        Text {
                            anchors.centerIn: parent
                            text: "⚠"
                            font.pixelSize: 24
                            color: Styles.Theme.textSecondary
                        }
                    }
                    
                    // Video duration badge
                    Rectangle {
                        anchors.bottom: parent.bottom
                        anchors.right: parent.right
                        anchors.margins: 4
                        width: durationLabel.width + 8
                        height: durationLabel.height + 4
                        radius: Styles.Theme.borderRadius
                        color: Styles.Theme.videoBadgeBackground
                        visible: model.isVideo
                        
                        Text {
                            id: durationLabel
                            anchors.centerIn: parent
                            text: grid.formatDuration(model.info ? model.info.dur : null)
                            color: "white"
                            font: Styles.Theme.captionFont
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
                        color: Styles.Theme.videoBadgeBackground
                        visible: model.isLive
                        
                        Rectangle {
                            anchors.centerIn: parent
                            width: 8
                            height: 8
                            radius: 4
                            color: Styles.Theme.liveBadgeBackground
                        }
                    }
                    
                    // Selection checkmark (in selection mode)
                    Rectangle {
                        anchors.top: parent.top
                        anchors.right: parent.right
                        anchors.margins: 4
                        width: 20
                        height: 20
                        radius: 10
                        color: model.isSelected ? Styles.Theme.accent : Styles.Theme.videoBadgeBackground
                        border.color: "white"
                        border.width: 1
                        visible: root.selectionMode
                        
                        Text {
                            anchors.centerIn: parent
                            text: "✓"
                            font.pixelSize: 12
                            font.bold: true
                            color: "white"
                            visible: model.isSelected
                        }
                    }
                }
                
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    
                    onClicked: function(mouse) {
                        if (mouse.button === Qt.LeftButton) {
                            if (root.selectionMode) {
                                model.isSelected = !model.isSelected
                            } else {
                                grid.currentIndex = index
                                root.itemClicked(index, mouse.modifiers)
                            }
                        } else if (mouse.button === Qt.RightButton) {
                            var globalPt = mapToGlobal(mouse.x, mouse.y)
                            root.showContextMenu(index, globalPt.x, globalPt.y)
                        }
                    }
                    
                    onDoubleClicked: function(mouse) {
                        if (mouse.button === Qt.LeftButton) {
                            root.itemDoubleClicked(index)
                        }
                    }
                }
            }
        }
        
        ScrollBar.vertical: ScrollBar {
            active: true
            
            contentItem: Rectangle {
                implicitWidth: 8
                radius: 4
                color: Styles.Theme.scrollbarHandle
                opacity: parent.active || parent.hovered ? 1.0 : 0.5
                
                Behavior on opacity {
                    NumberAnimation { duration: Styles.Theme.animationNormal }
                }
            }
        }
    }
    
    // Empty state
    Item {
        anchors.fill: parent
        visible: grid.count === 0
        
        Column {
            anchors.centerIn: parent
            spacing: Styles.Theme.spacingMedium
            
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "📷"
                font.pixelSize: 48
            }
            
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: qsTr("No photos to display")
                font: Styles.Theme.bodyFont
                color: Styles.Theme.textSecondary
            }
            
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: qsTr("Open an album or drag files here to import")
                font: Styles.Theme.smallFont
                color: Styles.Theme.textDisabled
            }
        }
    }
}
