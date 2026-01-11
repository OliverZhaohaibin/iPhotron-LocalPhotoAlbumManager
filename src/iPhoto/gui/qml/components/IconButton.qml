import QtQuick 2.15
import QtQuick.Controls 2.15
import "../styles"

/**
 * Icon-only button for toolbar actions.
 * 
 * Features:
 * - Consistent icon sizing
 * - Subtle hover/press feedback
 * - Tooltip support
 * - Optional checkable state
 */
ToolButton {
    id: control
    
    property string iconSource: ""
    property int iconSize: Theme.iconSize
    property color iconColor: Theme.text
    property string tooltipText: ""
    
    implicitWidth: Theme.controlHeight
    implicitHeight: Theme.controlHeight
    
    checkable: false
    
    background: Rectangle {
        implicitWidth: Theme.controlHeight
        implicitHeight: Theme.controlHeight
        
        color: {
            if (control.pressed) return Theme.buttonPressed
            if (control.hovered || control.checked) return Theme.buttonHover
            return "transparent"
        }
        
        radius: Theme.borderRadius
        
        Behavior on color { 
            ColorAnimation { duration: Theme.animationFast } 
        }
    }
    
    contentItem: Image {
        source: control.iconSource
        width: control.iconSize
        height: control.iconSize
        fillMode: Image.PreserveAspectFit
        anchors.centerIn: parent
        opacity: control.enabled ? 1.0 : 0.4
        
        // For SVG icons, we can apply color overlay if needed
        // Note: Qt Quick doesn't directly support SVG recoloring,
        // so icons should be pre-colored or use Image's ColorOverlay
    }
    
    ToolTip {
        visible: control.hovered && control.tooltipText !== ""
        text: control.tooltipText
        delay: 500
        
        background: Rectangle {
            color: Theme.dialogBackground
            border.color: Theme.dialogBorder
            border.width: 1
            radius: Theme.borderRadius
        }
        
        contentItem: Text {
            text: control.ToolTip.text
            font: Theme.smallFont
            color: Theme.text
        }
    }
    
    // Cursor change
    MouseArea {
        anchors.fill: parent
        cursorShape: control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        acceptedButtons: Qt.NoButton
    }
}
