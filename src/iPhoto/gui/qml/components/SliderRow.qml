import QtQuick 2.15
import QtQuick.Controls 2.15
import "../styles"

/**
 * Labeled slider row for edit controls.
 * 
 * Features:
 * - Label with current value display
 * - Centered slider with configurable range
 * - Reset functionality (double-click label)
 * - Bi-directional value binding
 */
Item {
    id: root
    
    property string label: "Parameter"
    property real value: 0
    property real from: -100
    property real to: 100
    property real defaultValue: 0
    property real stepSize: 1
    property int decimals: 0
    property bool showValue: true
    
    signal valueChanged(real newValue)
    
    implicitWidth: parent ? parent.width : 260
    implicitHeight: 32
    
    Row {
        anchors.fill: parent
        spacing: Theme.spacingMedium
        
        // Label with value
        Item {
            width: labelText.width + (root.showValue ? valueText.width + Theme.spacingSmall : 0)
            height: parent.height
            
            Text {
                id: labelText
                anchors.verticalCenter: parent.verticalCenter
                text: root.label
                font: Theme.smallFont
                color: Theme.text
            }
            
            Text {
                id: valueText
                visible: root.showValue
                anchors.left: labelText.right
                anchors.leftMargin: Theme.spacingSmall
                anchors.verticalCenter: parent.verticalCenter
                text: root.value.toFixed(root.decimals)
                font: Theme.smallFont
                color: Theme.textSecondary
                
                // Show +/- sign for non-zero values when range includes negatives
                Component.onCompleted: {
                    if (root.from < 0 && root.value > 0) {
                        text = "+" + root.value.toFixed(root.decimals)
                    }
                }
            }
            
            // Double-click to reset
            MouseArea {
                anchors.fill: parent
                onDoubleClicked: {
                    root.value = root.defaultValue
                    root.valueChanged(root.defaultValue)
                }
                
                ToolTip {
                    visible: parent.containsMouse
                    text: qsTr("Double-click to reset")
                    delay: 1000
                }
            }
        }
        
        // Slider
        Slider {
            id: slider
            width: parent.width - labelText.width - (root.showValue ? valueText.width + Theme.spacingSmall : 0) - Theme.spacingMedium
            height: parent.height
            
            from: root.from
            to: root.to
            value: root.value
            stepSize: root.stepSize
            
            onValueChanged: {
                if (root.value !== value) {
                    root.value = value
                    root.valueChanged(value)
                }
            }
            
            // Custom styling for edit slider
            background: Rectangle {
                x: slider.leftPadding
                y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: slider.availableWidth
                height: 3
                radius: 1.5
                color: Theme.sliderTrack
                
                // Center mark for bipolar sliders
                Rectangle {
                    visible: root.from < 0 && root.to > 0
                    anchors.centerIn: parent
                    width: 2
                    height: parent.height + 4
                    color: Theme.textSecondary
                    opacity: 0.5
                }
                
                // Filled portion from center (for bipolar) or from left (for unipolar)
                Rectangle {
                    property real centerPos: root.from < 0 ? (0 - root.from) / (root.to - root.from) : 0
                    property real fillStart: root.from < 0 ? Math.min(slider.visualPosition, centerPos) : 0
                    property real fillEnd: root.from < 0 ? Math.max(slider.visualPosition, centerPos) : slider.visualPosition
                    
                    x: fillStart * parent.width
                    width: (fillEnd - fillStart) * parent.width
                    height: parent.height
                    radius: parent.radius
                    color: Theme.sliderFill
                }
            }
            
            handle: Rectangle {
                x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
                y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: 12
                height: 12
                radius: 6
                color: slider.pressed ? Theme.accentPressed : Theme.sliderHandle
                
                Behavior on color {
                    ColorAnimation { duration: Theme.animationFast }
                }
            }
        }
    }
}
