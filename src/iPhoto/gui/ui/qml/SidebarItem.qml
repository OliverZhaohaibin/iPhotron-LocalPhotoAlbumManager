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
    property bool isSelected: false
    
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
    
    // Colors matching palette.py
    readonly property color textColor: "#2b2b2b"
    readonly property color headerColor: "#1b1b1b"
    readonly property color actionColor: "#1e73ff"
    readonly property color sectionColor: Qt.rgba(0, 0, 0, 0.63)
    readonly property color iconColor: "#1e73ff"
    readonly property color separatorColor: Qt.rgba(0, 0, 0, 0.16)
    readonly property color hoverColor: Qt.rgba(0, 0, 0, 0.1)
    readonly property color selectedColor: Qt.rgba(0, 0, 0, 0.22)
    
    // Layout matching palette.py values
    readonly property int leftPadding: 14
    readonly property int indentPerLevel: 22
    readonly property int iconSize: 24
    readonly property int iconTextGap: 10
    readonly property int branchIndicatorSize: 12
    readonly property int branchContentGap: 6
    readonly property int rightPadding: 24
    readonly property int highlightMarginX: 6
    readonly property int highlightMarginY: 4
    readonly property int highlightRadius: 10
    
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
        
        // Selection/Hover background
        Rectangle {
            id: backgroundRect
            anchors.fill: parent
            anchors.leftMargin: highlightMarginX
            anchors.rightMargin: highlightMarginX
            anchors.topMargin: highlightMarginY
            anchors.bottomMargin: highlightMarginY
            radius: highlightRadius
            color: {
                if (isSelected && itemIsSelectable) return selectedColor
                if (mouseArea.containsMouse && itemIsSelectable) return hoverColor
                return "transparent"
            }
            
            Behavior on color {
                ColorAnimation { duration: 100 }
            }
        }
        
        // Click handler - moved BEFORE rowContent so it's behind it in z-order
        // This allows child items in rowContent (like branchIndicator) to receive mouse events
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
                width: itemHasChildren ? branchIndicatorSize + branchContentGap : 0
                height: branchIndicatorSize
                anchors.verticalCenter: parent.verticalCenter
                visible: itemHasChildren
                
                BranchIndicator {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    width: branchIndicatorSize
                    height: branchIndicatorSize
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
            
            // Icon using image provider
            Image {
                id: iconImage
                width: iconSize
                height: iconSize
                anchors.verticalCenter: parent.verticalCenter
                source: getIconSource(itemIconName, itemNodeType, isSelected)
                sourceSize.width: iconSize
                sourceSize.height: iconSize
                fillMode: Image.PreserveAspectFit
                visible: source !== ""
                
                function getIconSource(iconName, nodeType, selected) {
                    // Map icon names to bundled SVG files via image provider
                    var baseName = ""
                    var color = iconColor
                    
                    // Map the icon name to actual SVG file
                    switch (iconName) {
                        case "photo.on.rectangle":
                            baseName = "photo.on.rectangle"
                            break
                        case "video":
                            baseName = selected ? "video.fill" : "video"
                            break
                        case "livephoto":
                            baseName = "livephoto"
                            break
                        case "suit.heart":
                            baseName = selected ? "suit.heart.fill" : "suit.heart"
                            break
                        case "mappin.and.ellipse":
                            baseName = "mappin.and.ellipse"
                            break
                        case "trash":
                            baseName = "trash"
                            break
                        case "folder":
                            baseName = "folder"
                            break
                        case "rectangle.stack":
                            baseName = "rectangle.stack"
                            // Albums use text color instead of icon color
                            color = textColor
                            break
                        case "plus.circle":
                            baseName = "plus.circle"
                            break
                        default:
                            if (iconName && iconName.length > 0) {
                                baseName = iconName
                            }
                            break
                    }
                    
                    if (!baseName) return ""
                    
                    // Return URL for icon image provider with color parameter
                    return "image://icons/" + baseName + ".svg?color=" + color
                }
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
    }
}
