import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import "."  // Import local QML files

Rectangle {
    id: sidebar
    color: "#f5f5f5"
    
    // Color constants matching the widget implementation
    readonly property color backgroundColor: "#f5f5f5"
    readonly property color selectedBackground: Qt.rgba(0, 0.478, 1.0, 0.2)
    readonly property color hoverBackground: Qt.rgba(0, 0.478, 1.0, 0.1)
    readonly property color textColor: "#2b2b2b"
    readonly property color iconColor: "#007AFF"
    readonly property color separatorColor: "#d0d0d0"
    readonly property color headerTextColor: "#1a1a1a"
    
    // Layout constants
    readonly property int rowHeight: 28
    readonly property int leftPadding: 12
    readonly property int indentPerLevel: 16
    readonly property int iconSize: 16
    readonly property int iconTextGap: 8
    readonly property int branchIndicatorSize: 16
    
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 4
        
        // Title
        Text {
            id: titleLabel
            Layout.fillWidth: true
            Layout.leftMargin: leftPadding
            text: "Basic Library"
            font.pixelSize: 14
            font.bold: true
            color: headerTextColor
        }
        
        // Tree list view
        ListView {
            id: treeView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            
            model: sidebarBridge.model
            
            delegate: SidebarItem {
                width: treeView.width
                height: nodeType === 7 ? sidebar.rowHeight / 2 : sidebar.rowHeight  // Separator is smaller
                
                itemTitle: title
                itemNodeType: nodeType
                itemDepth: depth
                itemIsExpanded: isExpanded
                itemHasChildren: hasChildren
                itemIsSelectable: isSelectable
                itemIconName: iconName
                itemIconPath: iconPath
                
                onClicked: {
                    if (isSelectable) {
                        treeView.currentIndex = index
                        sidebarBridge.selectItem(index)
                    }
                }
                
                onToggleExpansion: {
                    sidebarBridge.toggleExpansion(index)
                }
            }
            
            // Highlight current selection
            highlight: Rectangle {
                color: sidebar.selectedBackground
                radius: 6
            }
            highlightFollowsCurrentItem: true
            
            ScrollBar {
                id: sidebarScrollBar
                policy: ScrollBar.AsNeeded
                orientation: Qt.Vertical
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                size: treeView.visibleArea.heightRatio
                position: treeView.visibleArea.yPosition
                onPositionChanged: {
                    const maxContentY = treeView.contentHeight - treeView.height
                    if (maxContentY > 0) {
                        treeView.contentY = position * maxContentY
                    }
                }
            }
        }
    }
}
