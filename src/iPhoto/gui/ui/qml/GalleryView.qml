import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: galleryView
    color: "#ffffff"
    
    // Properties
    property string currentTitle: "All Photos"
    property int itemCount: sidebarBridge.galleryModel.count
    
    // Layout constants matching widget implementation
    readonly property int gridCellSize: 192
    readonly property int gridSpacing: 2
    readonly property int headerHeight: 50
    
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        
        // Header with title and count
        Rectangle {
            id: header
            Layout.fillWidth: true
            Layout.preferredHeight: headerHeight
            color: "#f8f8f8"
            
            RowLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 16
                
                Text {
                    text: currentTitle
                    font.pixelSize: 24
                    font.bold: true
                    color: "#1a1a1a"
                }
                
                Text {
                    text: itemCount + " items"
                    font.pixelSize: 14
                    color: "#666666"
                    visible: itemCount > 0
                }
                
                Item { Layout.fillWidth: true }  // Spacer
            }
            
            // Bottom border
            Rectangle {
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: "#e0e0e0"
            }
        }
        
        // Gallery grid
        ScrollView {
            id: scrollView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            
            GridView {
                id: gridView
                anchors.fill: parent
                anchors.margins: gridSpacing
                
                model: sidebarBridge.galleryModel
                
                cellWidth: gridCellSize + gridSpacing
                cellHeight: gridCellSize + gridSpacing
                
                // Empty state
                Text {
                    anchors.centerIn: parent
                    text: "No photos to display"
                    font.pixelSize: 16
                    color: "#999999"
                    visible: gridView.count === 0
                }
                
                delegate: Item {
                    width: gridView.cellWidth
                    height: gridView.cellHeight
                    
                    // No radius - square corners matching widget
                    Rectangle {
                        id: cellBackground
                        anchors.fill: parent
                        anchors.margins: gridSpacing / 2
                        color: "#1b1b1b"  // Match widget dark background
                        
                        // Thumbnail image - fills entire cell
                        Image {
                            id: thumbnailImage
                            anchors.fill: parent
                            source: thumbnail
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                            cache: true
                            
                            // Loading placeholder
                            Rectangle {
                                anchors.fill: parent
                                color: "#1b1b1b"
                                visible: thumbnailImage.status !== Image.Ready
                            }
                        }
                        
                        // Live Photo badge - top left with icon (matching widget)
                        Rectangle {
                            anchors.top: parent.top
                            anchors.left: parent.left
                            anchors.margins: 8
                            width: 30
                            height: 30
                            radius: 6
                            color: Qt.rgba(0, 0, 0, 0.55)
                            visible: isLive
                            
                            Image {
                                anchors.centerIn: parent
                                width: 18
                                height: 18
                                source: sidebarBridge.iconDir + "/livephoto.svg"
                                sourceSize.width: 18
                                sourceSize.height: 18
                            }
                        }
                        
                        // Duration badge - bottom right (matching widget)
                        Rectangle {
                            id: durationBadge
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 8
                            height: 22
                            width: durationText.width + 12
                            radius: 6
                            color: Qt.rgba(0, 0, 0, 0.63)
                            visible: isVideo
                            
                            Text {
                                id: durationText
                                anchors.centerIn: parent
                                text: "0:30"  // Placeholder - would come from model
                                font.pixelSize: 11
                                font.bold: true
                                color: "white"
                            }
                        }
                        
                        // Selection highlight - no radius
                        Rectangle {
                            id: selectionOverlay
                            anchors.fill: parent
                            color: "transparent"
                            border.width: mouseArea.containsMouse ? 2 : 0
                            border.color: "#007AFF"
                        }
                        
                        MouseArea {
                            id: mouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: {
                                gridView.currentIndex = index
                                console.log("Selected:", relPath)
                            }
                        }
                    }
                }
                
                // Highlight current selection - square corners
                highlight: Rectangle {
                    color: "transparent"
                    border.width: 3
                    border.color: "#007AFF"
                }
                highlightFollowsCurrentItem: true
                
                ScrollBar.vertical: ScrollBar {
                    policy: ScrollBar.AsNeeded
                }
            }
        }
    }
    
    // Placeholder when no content
    ColumnLayout {
        anchors.centerIn: parent
        spacing: 20
        visible: !sidebarBridge.hasLibrary || itemCount === 0
        
        Text {
            Layout.alignment: Qt.AlignHCenter
            text: "📸"
            font.pixelSize: 64
        }
        
        Text {
            Layout.alignment: Qt.AlignHCenter
            text: sidebarBridge.hasLibrary ? "Select an album to view photos" : "No library bound"
            font.pixelSize: 16
            color: "#666666"
        }
    }
}
