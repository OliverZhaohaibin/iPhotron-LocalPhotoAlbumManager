import QtQuick 2.15
import QtQuick.Controls 2.15
import "../styles"

/**
 * Custom styled slider component following the iPhoto design language.
 * 
 * Features:
 * - Theme-aware track and handle colors
 * - Smooth animations
 * - Optional value label
 * - Configurable range with step support
 */
Slider {
    id: control
    
    property bool showValue: false
    property int decimals: 1
    property string valueFormat: value.toFixed(decimals)
    
    implicitWidth: 200
    implicitHeight: Theme.controlHeight
    
    from: 0
    to: 100
    value: 50
    stepSize: 1
    
    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth
        height: 4
        radius: 2
        color: Theme.sliderTrack
        
        // Filled portion
        Rectangle {
            width: control.visualPosition * parent.width
            height: parent.height
            radius: parent.radius
            color: Theme.sliderFill
            
            Behavior on width {
                NumberAnimation { duration: 50 }
            }
        }
    }
    
    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: 16
        height: 16
        radius: 8
        color: control.pressed ? Theme.accentPressed : Theme.sliderHandle
        border.color: Qt.darker(Theme.sliderHandle, 1.1)
        border.width: 1
        
        // Value tooltip on drag
        Rectangle {
            visible: control.pressed && control.showValue
            anchors.bottom: parent.top
            anchors.bottomMargin: 4
            anchors.horizontalCenter: parent.horizontalCenter
            width: valueLabel.width + 8
            height: valueLabel.height + 4
            radius: Theme.borderRadius
            color: Theme.dialogBackground
            border.color: Theme.dialogBorder
            border.width: 1
            
            Text {
                id: valueLabel
                anchors.centerIn: parent
                text: control.valueFormat
                font: Theme.captionFont
                color: Theme.text
            }
        }
        
        Behavior on color {
            ColorAnimation { duration: Theme.animationFast }
        }
        
        // Scale effect on press
        transform: Scale {
            origin.x: 8
            origin.y: 8
            xScale: control.pressed ? 1.1 : 1.0
            yScale: control.pressed ? 1.1 : 1.0
            
            Behavior on xScale { NumberAnimation { duration: Theme.animationFast } }
            Behavior on yScale { NumberAnimation { duration: Theme.animationFast } }
        }
    }
    
    // Mouse cursor
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        acceptedButtons: Qt.NoButton
    }
}
