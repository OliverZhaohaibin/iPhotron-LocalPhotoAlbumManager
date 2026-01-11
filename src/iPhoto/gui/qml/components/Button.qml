import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles

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
    property int iconSize: Styles.Theme.iconSize
    
    implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
    implicitHeight: Styles.Theme.buttonHeight
    
    leftPadding: 20
    rightPadding: 20
    topPadding: 0
    bottomPadding: 0
    
    font: Styles.Theme.bodyFont
    
    background: Rectangle {
        implicitWidth: 80
        implicitHeight: Styles.Theme.buttonHeight
        
        color: {
            if (!control.enabled) return Styles.Theme.buttonBackground
            if (control.pressed) return control.primary ? Styles.Theme.accentPressed : Styles.Theme.buttonPressed
            if (control.hovered) return control.primary ? Styles.Theme.accentHover : Styles.Theme.buttonHover
            return control.primary ? Styles.Theme.accent : Styles.Theme.buttonBackground
        }
        
        radius: Styles.Theme.borderRadiusLarge
        
        border.color: control.primary ? "transparent" : Styles.Theme.dialogBorder
        border.width: control.primary ? 0 : 1
        
        Behavior on color { 
            ColorAnimation { duration: Styles.Theme.animationFast } 
        }
    }
    
    contentItem: Row {
        spacing: control.iconSource ? Styles.Theme.spacingSmall : 0
        
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
                if (!control.enabled) return Styles.Theme.textDisabled
                return control.primary ? Styles.Theme.textInverse : Styles.Theme.buttonText
            }
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            anchors.verticalCenter: parent.verticalCenter
            
            Behavior on color { 
                ColorAnimation { duration: Styles.Theme.animationFast } 
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
