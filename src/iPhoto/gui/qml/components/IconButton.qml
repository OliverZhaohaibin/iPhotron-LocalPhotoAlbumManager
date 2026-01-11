import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles

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
    property int iconSize: Styles.Theme.iconSize
    property color iconColor: Styles.Theme.text
    property string tooltipText: ""
    
    implicitWidth: Styles.Theme.controlHeight
    implicitHeight: Styles.Theme.controlHeight
    
    checkable: false
    
    background: Rectangle {
        implicitWidth: Styles.Theme.controlHeight
        implicitHeight: Styles.Theme.controlHeight
        
        color: {
            if (control.pressed) return Styles.Theme.buttonPressed
            if (control.hovered || control.checked) return Styles.Theme.buttonHover
            return "transparent"
        }
        
        radius: Styles.Theme.borderRadius
        
        Behavior on color { 
            ColorAnimation { duration: Styles.Theme.animationFast } 
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
            color: Styles.Theme.dialogBackground
            border.color: Styles.Theme.dialogBorder
            border.width: 1
            radius: Styles.Theme.borderRadius
        }
        
        contentItem: Text {
            text: control.ToolTip.text
            font: Styles.Theme.smallFont
            color: Styles.Theme.text
        }
    }
    
    // Cursor change
    MouseArea {
        anchors.fill: parent
        cursorShape: control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        acceptedButtons: Qt.NoButton
    }
}
