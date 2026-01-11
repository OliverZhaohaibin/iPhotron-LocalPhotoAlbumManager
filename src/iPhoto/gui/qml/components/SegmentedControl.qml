import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles

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
        color: Styles.Theme.buttonBackground
        radius: Styles.Theme.borderRadiusLarge
        border.color: Styles.Theme.dialogBorder
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
        radius: Styles.Theme.borderRadius + 2
        color: Styles.Theme.accent
        
        Behavior on x {
            NumberAnimation { 
                duration: Styles.Theme.animationNormal 
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
                width: itemText.width + Styles.Theme.spacingXLarge * 2
                height: root.height - 4
                
                Text {
                    id: itemText
                    anchors.centerIn: parent
                    text: modelData
                    font.family: Styles.Theme.bodyFont.family
                    font.pixelSize: Styles.Theme.bodyFont.pixelSize
                    font.weight: index === root.currentIndex ? Font.DemiBold : Font.Normal
                    color: index === root.currentIndex ? Styles.Theme.textInverse : Styles.Theme.text
                    
                    Behavior on color {
                        ColorAnimation { duration: Styles.Theme.animationFast }
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
