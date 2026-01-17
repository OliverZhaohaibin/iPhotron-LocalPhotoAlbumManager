import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

/**
 * Gallery grid view displaying thumbnails with overlay badges.
 * 
 * This component mirrors the traditional GalleryGridView widget,
 * providing consistent thumbnail display with live photo badges,
 * video duration, and favorite indicators.
 */
Item {
    id: galleryView
    
    // Properties
    property string albumTitle: ""
    
    // Gallery configuration matching widget implementation
    readonly property int minItemWidth: 192
    readonly property int itemGap: 2
    readonly property int safetyMargin: 10
    
    // Badge styling
    readonly property color badgeBackground: Qt.rgba(0, 0, 0, 0.6)
    readonly property color badgeText: "#ffffff"
    readonly property int badgePadding: 6
    readonly property int badgeRadius: 6
    readonly property int badgeIconSize: 18
    
    // Calculate number of columns based on available width
    readonly property int numColumns: Math.max(1, Math.floor((width - safetyMargin) / (minItemWidth + itemGap)))
    readonly property int cellSize: Math.floor((width - safetyMargin) / numColumns)
    readonly property int itemSize: cellSize - itemGap
    
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        
        // Header with album title and count
        Rectangle {
            id: header
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            color: "transparent"
            visible: albumTitle !== ""
            
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                
                Text {
                    text: albumTitle
                    font.pixelSize: 18
                    font.bold: true
                    color: "#333333"
                }
                
                Item { Layout.fillWidth: true }
                
                Text {
                    text: galleryBridge && galleryBridge.model ? galleryBridge.count + " items" : "0 items"
                    font.pixelSize: 14
                    color: "#666666"
                }
            }
        }
        
        // Loading indicator
        Rectangle {
            id: loadingIndicator
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "transparent"
            visible: galleryBridge && galleryBridge.loading
            
            BusyIndicator {
                anchors.centerIn: parent
                running: galleryBridge && galleryBridge.loading
            }
        }
        
        // Empty state
        Rectangle {
            id: emptyState
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "transparent"
            visible: !loadingIndicator.visible && galleryBridge && galleryBridge.model && galleryBridge.count === 0
            
            ColumnLayout {
                anchors.centerIn: parent
                spacing: 16
                
                Image {
                    Layout.alignment: Qt.AlignHCenter
                    source: "image://icons/photo.on.rectangle.svg?color=#999999"
                    sourceSize.width: 48
                    sourceSize.height: 48
                    fillMode: Image.PreserveAspectFit
                }
                
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: "No photos"
                    font.pixelSize: 16
                    color: "#666666"
                }
            }
        }
        
        // Grid view
        GridView {
            id: gridView
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !loadingIndicator.visible && galleryBridge && galleryBridge.model && galleryBridge.count > 0
            
            model: galleryBridge ? galleryBridge.model : null
            cellWidth: cellSize
            cellHeight: cellSize
            clip: true
            cacheBuffer: cellHeight * 4
            
            // Smooth scrolling
            flickableDirection: Flickable.VerticalFlick
            boundsBehavior: Flickable.StopAtBounds
            
            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded
            }
            
            delegate: Item {
                id: delegateRoot
                property bool showPlaceholder: thumbnail.status !== Image.Ready && (microThumb.source || "") === ""
                width: cellSize
                height: cellSize
                
                // Thumbnail container with gap
                Rectangle {
                        id: thumbnailContainer
                        anchors.fill: parent
                        anchors.margins: itemGap / 2
                        color: "#1b1b1b"
                        clip: true
                        
                        // Micro thumbnail placeholder (from database)
                        Image {
                            id: microThumb
                            anchors.fill: parent
                            source: model.microThumbnail || ""
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                            cache: true
                            visible: source !== "" && thumbnail.status !== Image.Ready
                        }

                        // Thumbnail image
                        Image {
                            id: thumbnail
                            anchors.fill: parent
                            source: model.thumbnailUrl || ""
                            sourceSize.width: galleryView.itemSize
                            sourceSize.height: galleryView.itemSize
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                            cache: true
                            opacity: status === Image.Ready ? 1 : 0
                            
                            Behavior on opacity {
                                NumberAnimation { duration: 120 }
                            }
                        
                            // Loading placeholder
                            Rectangle {
                                anchors.fill: parent
                                color: "#1b1b1b"
                                visible: delegateRoot.showPlaceholder
                            
                            BusyIndicator {
                                anchors.centerIn: parent
                                running: thumbnail.status === Image.Loading
                                scale: 0.5
                            }
                        }
                    }
                    
                    // Selection overlay
                    Rectangle {
                        anchors.fill: parent
                        color: "#007AFF"
                        opacity: 0.3
                        visible: delegateRoot.GridView.isCurrentItem
                    }
                    
                    // Hover overlay
                    Rectangle {
                        anchors.fill: parent
                        color: "#000000"
                        opacity: mouseArea.containsMouse ? 0.1 : 0
                        
                        Behavior on opacity {
                            NumberAnimation { duration: 100 }
                        }
                    }
                    
                    // Live Photo badge (top-left)
                    Rectangle {
                        id: liveBadge
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.margins: 8
                        width: badgeIconSize + badgePadding * 2
                        height: badgeIconSize + badgePadding * 2
                        radius: badgeRadius
                        color: badgeBackground
                        visible: model.isLive || false
                        
                        Image {
                            anchors.centerIn: parent
                            source: "image://icons/livephoto.svg?color=#ffffff"
                            sourceSize.width: badgeIconSize
                            sourceSize.height: badgeIconSize
                            fillMode: Image.PreserveAspectFit
                        }
                    }
                    
                    // Favorite badge (bottom-left)
                    Rectangle {
                        id: favoriteBadge
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        anchors.margins: 8
                        width: 16 + 10
                        height: 16 + 10
                        radius: badgeRadius
                        color: badgeBackground
                        visible: model.isFavorite || false
                        
                        Image {
                            anchors.centerIn: parent
                            source: "image://icons/suit.heart.fill.svg?color=#ff4d67"
                            sourceSize.width: 16
                            sourceSize.height: 16
                            fillMode: Image.PreserveAspectFit
                        }
                    }
                    
                    // Video duration badge (bottom-right)
                    Rectangle {
                        id: durationBadge
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.margins: 8
                        width: durationText.implicitWidth + badgePadding * 2
                        height: durationText.implicitHeight + badgePadding
                        radius: badgeRadius
                        color: badgeBackground
                        visible: model.isVideo || false
                        
                        Text {
                            id: durationText
                            anchors.centerIn: parent
                            text: formatDuration(model.duration || 0)
                            font.pixelSize: 11
                            font.bold: true
                            color: badgeText
                            
                            function formatDuration(seconds) {
                                var s = Math.round(seconds)
                                var mins = Math.floor(s / 60)
                                var secs = s % 60
                                var hours = Math.floor(mins / 60)
                                mins = mins % 60
                                
                                if (hours > 0) {
                                    return hours + ":" + (mins < 10 ? "0" : "") + mins + ":" + (secs < 10 ? "0" : "") + secs
                                }
                                return mins + ":" + (secs < 10 ? "0" : "") + secs
                            }
                        }
                    }
                    
                    // Panorama badge (bottom-right, above duration if both present)
                    Rectangle {
                        id: panoBadge
                        anchors.right: parent.right
                        anchors.bottom: durationBadge.visible ? durationBadge.top : parent.bottom
                        anchors.bottomMargin: durationBadge.visible ? 4 : 8
                        anchors.rightMargin: 8
                        width: badgeIconSize + badgePadding * 2
                        height: badgeIconSize + badgePadding * 2
                        radius: badgeRadius
                        color: badgeBackground
                        visible: model.isPano || false
                        
                        Image {
                            anchors.centerIn: parent
                            source: "image://icons/pano.svg?color=#ffffff"
                            sourceSize.width: badgeIconSize
                            sourceSize.height: badgeIconSize
                            fillMode: Image.PreserveAspectFit
                        }
                    }
                    
                    // Click handler
                    MouseArea {
                        id: mouseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        
                        onClicked: {
                            gridView.currentIndex = model.itemIndex
                            if (galleryBridge) {
                                galleryBridge.selectItem(model.itemIndex)
                            }
                        }
                        
                        onDoubleClicked: {
                            console.log("Double clicked:", model.filePath)
                        }
                    }
                }
            }
            
            // Highlight effect
            highlight: Rectangle {
                color: "transparent"
            }
            highlightFollowsCurrentItem: true
        }
    }
}
