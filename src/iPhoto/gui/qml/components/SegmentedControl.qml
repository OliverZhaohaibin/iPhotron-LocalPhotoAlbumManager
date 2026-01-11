import QtQuick 2.15
import QtQuick.Controls 2.15
import "../styles"

/**
 * Segmented control for switching between modes.
 * 
 * Features:
 * - Animated selection indicator
 * - Theme-aware styling
 * - Flexible item count
 */
Item {
    id: root
    
    property var items: []
    property int currentIndex: 0
    
    signal indexChanged(int index)
    
    implicitWidth: Math.max(200, itemRow.width + 8)
    implicitHeight: 32
    
    Rectangle {
        anchors.fill: parent
        color: Theme.buttonBackground
        radius: Theme.borderRadiusLarge
        border.color: Theme.dialogBorder
        border.width: 1
    }
    
    // Selection indicator
    Rectangle {
        id: indicator
        width: itemRow.children.length > 0 ? 
               (itemRow.width - (root.items.length - 1) * itemRow.spacing) / root.items.length : 0
        height: parent.height - 4
        x: root.currentIndex * (indicator.width + itemRow.spacing) + 2
        y: 2
        radius: Theme.borderRadius + 2
        color: Theme.accent
        
        Behavior on x {
            NumberAnimation { 
                duration: Theme.animationNormal 
                easing.type: Easing.OutQuad
            }
        }
    }
    
    Row {
        id: itemRow
        anchors.centerIn: parent
        spacing: 2
        
        Repeater {
            model: root.items
            
            delegate: Item {
                width: itemText.width + Theme.spacingXLarge * 2
                height: root.height - 4
                
                Text {
                    id: itemText
                    anchors.centerIn: parent
                    text: modelData
                    font: Theme.bodyFont
                    font.weight: index === root.currentIndex ? Font.DemiBold : Font.Normal
                    color: index === root.currentIndex ? Theme.textInverse : Theme.text
                    
                    Behavior on color {
                        ColorAnimation { duration: Theme.animationFast }
                    }
                }
                
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (root.currentIndex !== index) {
                            root.currentIndex = index
                            root.indexChanged(index)
                        }
                    }
                }
            }
        }
    }
}
