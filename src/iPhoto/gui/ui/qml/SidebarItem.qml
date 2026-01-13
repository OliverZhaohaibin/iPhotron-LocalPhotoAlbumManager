import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import "."  // Import local QML files

Item {
    id: root
    
    // Properties from model
    property string itemTitle: ""
    property int itemNodeType: 0
    property int itemDepth: 0
    property bool itemIsExpanded: false
    property bool itemHasChildren: false
    property bool itemIsSelectable: true
    property string itemIconName: ""
    property string itemIconPath: ""  // Full path to SVG icon
    
    // Signals
    signal clicked()
    signal toggleExpansion()
    
    // Node type constants (must match Python NodeType enum)
    readonly property int nodeTypeRoot: 0
    readonly property int nodeTypeHeader: 1
    readonly property int nodeTypeSection: 2
    readonly property int nodeTypeStatic: 3
    readonly property int nodeTypeAction: 4
    readonly property int nodeTypeAlbum: 5
    readonly property int nodeTypeSubalbum: 6
    readonly property int nodeTypeSeparator: 7
    
    // Colors
    readonly property color textColor: "#2b2b2b"
    readonly property color headerColor: "#1a1a1a"
    readonly property color actionColor: "#007AFF"
    readonly property color sectionColor: "#888888"
    readonly property color iconColor: "#007AFF"
    readonly property color separatorColor: "#d0d0d0"
    readonly property color hoverColor: Qt.rgba(0, 0.478, 1.0, 0.1)
    
    // Layout
    readonly property int leftPadding: 12
    readonly property int indentPerLevel: 16
    readonly property int iconSize: 16
    readonly property int iconTextGap: 8
    readonly property int branchIndicatorSize: 12
    readonly property int rightPadding: 20  // Right margin for text eliding
    
    // Separator rendering
    Rectangle {
        id: separator
        visible: itemNodeType === nodeTypeSeparator
        anchors.centerIn: parent
        width: parent.width - 2 * leftPadding
        height: 1
        color: separatorColor
    }
    
    // Content row for non-separator items
    Item {
        id: contentRow
        visible: itemNodeType !== nodeTypeSeparator
        anchors.fill: parent
        
        // Hover background
        Rectangle {
            id: hoverBackground
            anchors.fill: parent
            anchors.margins: 2
            radius: 6
            color: mouseArea.containsMouse && itemIsSelectable ? hoverColor : "transparent"
            
            Behavior on color {
                ColorAnimation { duration: 100 }
            }
        }
        
        // Content layout
        Row {
            id: rowContent
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: leftPadding + (itemDepth * indentPerLevel)
            spacing: 4
            
            // Branch indicator (disclosure triangle)
            Item {
                id: branchIndicator
                width: itemHasChildren ? branchIndicatorSize : 0
                height: branchIndicatorSize
                anchors.verticalCenter: parent.verticalCenter
                visible: itemHasChildren
                
                BranchIndicator {
                    anchors.fill: parent
                    angle: itemIsExpanded ? 90 : 0
                    indicatorColor: textColor
                    
                    Behavior on angle {
                        NumberAnimation { 
                            duration: 180 
                            easing.type: Easing.InOutQuad
                        }
                    }
                }
                
                MouseArea {
                    anchors.fill: parent
                    anchors.margins: -4
                    onClicked: root.toggleExpansion()
                }
            }
            
            // Icon using SVG image
            Image {
                id: iconImage
                width: iconSize
                height: iconSize
                anchors.verticalCenter: parent.verticalCenter
                source: itemIconPath
                sourceSize.width: iconSize
                sourceSize.height: iconSize
                visible: itemIconPath !== ""
                fillMode: Image.PreserveAspectFit
            }
            
            // Spacer between icon and text
            Item {
                width: iconImage.visible ? iconTextGap : 0
                height: 1
            }
            
            // Title text
            Text {
                id: titleText
                anchors.verticalCenter: parent.verticalCenter
                text: itemTitle
                font.pixelSize: getTextSize()
                font.bold: itemNodeType === nodeTypeHeader
                font.italic: itemNodeType === nodeTypeAction
                color: getTextColor()
                elide: Text.ElideRight
                width: Math.max(0, root.width - rowContent.x - iconSize - iconTextGap - rightPadding)
                
                function getTextSize() {
                    if (itemNodeType === nodeTypeHeader) return 13
                    if (itemNodeType === nodeTypeSection) return 11
                    return 12
                }
                
                function getTextColor() {
                    if (itemNodeType === nodeTypeHeader) return headerColor
                    if (itemNodeType === nodeTypeAction) return actionColor
                    if (itemNodeType === nodeTypeSection) return sectionColor
                    return textColor
                }
            }
        }
        
        // Click handler
        MouseArea {
            id: mouseArea
            anchors.fill: parent
            hoverEnabled: true
            onClicked: {
                if (itemIsSelectable) {
                    root.clicked()
                }
            }
        }
    }
}
