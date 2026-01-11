import QtQuick 2.15
import QtQuick.Controls.Basic
import styles 1.0 as Styles

/**
 * Filmstrip navigation component for browsing assets horizontally.
 * 
 * Features:
 * - Horizontal scrolling thumbnail strip
 * - Current selection highlighting
 * - Smooth animations
 * - Video/Live Photo badges
 */
Rectangle {
    id: root
    
    // Properties
    property alias model: listView.model
    property alias currentIndex: listView.currentIndex
    property int itemWidth: 70
    property int itemHeight: 70
    
    // Signals
    signal itemClicked(int index)
    
    implicitWidth: parent ? parent.width : 400
    implicitHeight: itemHeight + 10
    
    color: Styles.Theme.viewerSurface
    
    // Top border
    Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: 1
        color: Styles.Theme.headerSeparator
    }
    
    ListView {
        id: listView
        anchors.fill: parent
        anchors.topMargin: 5
        anchors.bottomMargin: 5
        orientation: ListView.Horizontal
        spacing: 2
        clip: true
        
        // Leading/trailing spacers for center positioning
        header: Item { width: Math.max(0, (root.width - root.itemWidth) / 2) }
        footer: Item { width: Math.max(0, (root.width - root.itemWidth) / 2) }
        
        // Smooth scrolling
        highlightMoveDuration: Styles.Theme.animationNormal
        highlightMoveVelocity: -1
        
        delegate: Item {
            id: delegateRoot
            width: root.itemWidth
            height: root.itemHeight
            
            property bool isCurrent: index === listView.currentIndex
            
            Rectangle {
                anchors.fill: parent
                anchors.margins: 1
                color: Styles.Theme.gridItemBackground
                
                // Selection border
                Rectangle {
                    anchors.fill: parent
                    color: "transparent"
                    border.color: Styles.Theme.gridSelectionBorder
                    border.width: 2
                    visible: delegateRoot.isCurrent
                    z: 2
                }
                
                // Thumbnail
                Image {
                    id: thumbnail
                    anchors.fill: parent
                    anchors.margins: delegateRoot.isCurrent ? 2 : 0
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
                        visible: thumbnail.status === Image.Loading
                    }
                    
                    // Video badge
                    Rectangle {
                        anchors.bottom: parent.bottom
                        anchors.right: parent.right
                        anchors.margins: 2
                        width: durationText.width + 4
                        height: durationText.height + 2
                        radius: 2
                        color: Styles.Theme.videoBadgeBackground
                        visible: model.isVideo
                        
                        Text {
                            id: durationText
                            anchors.centerIn: parent
                            text: {
                                if (!model.info || !model.info.dur) return ""
                                var sec = Math.floor(model.info.dur)
                                var min = Math.floor(sec / 60)
                                sec = sec % 60
                                return min + ":" + (sec < 10 ? "0" : "") + sec
                            }
                            font: Styles.Theme.captionFont
                            color: "white"
                        }
                    }
                    
                    // Live Photo indicator
                    Rectangle {
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.margins: 2
                        width: 10
                        height: 10
                        radius: 5
                        color: Styles.Theme.videoBadgeBackground
                        visible: model.isLive
                        
                        Rectangle {
                            anchors.centerIn: parent
                            width: 6
                            height: 6
                            radius: 3
                            color: Styles.Theme.liveBadgeBackground
                        }
                    }
                }
                
                // Hover effect
                Rectangle {
                    anchors.fill: parent
                    color: "white"
                    opacity: delegateMouse.containsMouse && !delegateRoot.isCurrent ? 0.1 : 0
                    
                    Behavior on opacity {
                        NumberAnimation { duration: Styles.Theme.animationFast }
                    }
                }
            }
            
            MouseArea {
                id: delegateMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                
                onClicked: {
                    listView.currentIndex = index
                    root.itemClicked(index)
                }
            }
            
            // Scale animation for current item
            transform: Scale {
                origin.x: delegateRoot.width / 2
                origin.y: delegateRoot.height / 2
                xScale: delegateRoot.isCurrent ? 1.0 : 0.95
                yScale: delegateRoot.isCurrent ? 1.0 : 0.95
                
                Behavior on xScale {
                    NumberAnimation { duration: Styles.Theme.animationFast }
                }
                Behavior on yScale {
                    NumberAnimation { duration: Styles.Theme.animationFast }
                }
            }
        }
        
        ScrollBar.horizontal: ScrollBar {
            height: 6
            
            contentItem: Rectangle {
                radius: 3
                color: Styles.Theme.scrollbarHandle
                opacity: parent.active ? 1.0 : 0.5
            }
        }
    }
    
    // Public methods
    function setCurrentIndex(idx) {
        listView.currentIndex = idx
        listView.positionViewAtIndex(idx, ListView.Center)
    }
    
    function scrollToIndex(idx) {
        listView.positionViewAtIndex(idx, ListView.Center)
    }
    
    function refreshSpacers() {
        // Force header/footer recalculation
        listView.headerItem.width = Math.max(0, (root.width - root.itemWidth) / 2)
        listView.footerItem.width = Math.max(0, (root.width - root.itemWidth) / 2)
    }
}
