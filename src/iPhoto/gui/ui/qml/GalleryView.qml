import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: galleryView
    color: "#ffffff"
    
    // Properties
    property string currentTitle: "All Photos"
    property int itemCount: sidebarBridge.galleryModel.count
    
    // Layout constants
    readonly property int gridCellSize: 200
    readonly property int gridSpacing: 4
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
                    
                    Rectangle {
                        id: cellBackground
                        anchors.fill: parent
                        anchors.margins: gridSpacing / 2
                        color: "#f0f0f0"
                        radius: 4
                        
                        // Thumbnail image
                        Image {
                            id: thumbnailImage
                            anchors.fill: parent
                            anchors.margins: 2
                            source: thumbnail
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                            cache: true
                            
                            // Loading placeholder
                            Rectangle {
                                anchors.fill: parent
                                color: "#e0e0e0"
                                visible: thumbnailImage.status !== Image.Ready
                                
                                Text {
                                    anchors.centerIn: parent
                                    text: "📷"
                                    font.pixelSize: 32
                                    opacity: 0.5
                                }
                            }
                            
                            // Video indicator
                            Rectangle {
                                anchors.bottom: parent.bottom
                                anchors.left: parent.left
                                anchors.margins: 8
                                width: 24
                                height: 24
                                radius: 12
                                color: "rgba(0, 0, 0, 0.6)"
                                visible: isVideo
                                
                                Text {
                                    anchors.centerIn: parent
                                    text: "▶"
                                    font.pixelSize: 12
                                    color: "white"
                                }
                            }
                            
                            // Live photo indicator
                            Rectangle {
                                anchors.top: parent.top
                                anchors.left: parent.left
                                anchors.margins: 8
                                width: 20
                                height: 20
                                radius: 10
                                color: "rgba(0, 0, 0, 0.6)"
                                visible: isLive
                                
                                Text {
                                    anchors.centerIn: parent
                                    text: "◉"
                                    font.pixelSize: 10
                                    color: "white"
                                }
                            }
                        }
                        
                        // Selection/hover effect
                        Rectangle {
                            id: selectionOverlay
                            anchors.fill: parent
                            color: "transparent"
                            radius: 4
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
                
                // Highlight current selection
                highlight: Rectangle {
                    color: "transparent"
                    border.width: 3
                    border.color: "#007AFF"
                    radius: 4
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
