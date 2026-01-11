import QtQuick 2.15
import QtQuick.Controls 2.15
import "../styles"

/**
 * Custom styled button component following the iPhoto design language.
 * 
 * Features:
 * - Consistent theme-aware styling
 * - Smooth hover/press animations
 * - Optional icon support
 * - Primary/secondary variants
 */
Button {
    id: control
    
    property bool primary: false
    property string iconSource: ""
    property int iconSize: Theme.iconSize
    
    implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
    implicitHeight: Theme.buttonHeight
    
    leftPadding: 20
    rightPadding: 20
    topPadding: 0
    bottomPadding: 0
    
    font: Theme.bodyFont
    
    background: Rectangle {
        implicitWidth: 80
        implicitHeight: Theme.buttonHeight
        
        color: {
            if (!control.enabled) return Theme.buttonBackground
            if (control.pressed) return control.primary ? Theme.accentPressed : Theme.buttonPressed
            if (control.hovered) return control.primary ? Theme.accentHover : Theme.buttonHover
            return control.primary ? Theme.accent : Theme.buttonBackground
        }
        
        radius: Theme.borderRadiusLarge
        
        border.color: control.primary ? "transparent" : Theme.dialogBorder
        border.width: control.primary ? 0 : 1
        
        Behavior on color { 
            ColorAnimation { duration: Theme.animationFast } 
        }
    }
    
    contentItem: Row {
        spacing: control.iconSource ? Theme.spacingSmall : 0
        
        Image {
            id: iconImage
            visible: control.iconSource !== ""
            source: control.iconSource
            width: control.iconSize
            height: control.iconSize
            anchors.verticalCenter: parent.verticalCenter
            fillMode: Image.PreserveAspectFit
            opacity: control.enabled ? 1.0 : 0.5
        }
        
        Text {
            text: control.text
            font: control.font
            color: {
                if (!control.enabled) return Theme.textDisabled
                return control.primary ? Theme.textInverse : Theme.buttonText
            }
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            anchors.verticalCenter: parent.verticalCenter
            
            Behavior on color { 
                ColorAnimation { duration: Theme.animationFast } 
            }
        }
    }
    
    // Mouse cursor change
    MouseArea {
        anchors.fill: parent
        cursorShape: control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        acceptedButtons: Qt.NoButton
    }
}
