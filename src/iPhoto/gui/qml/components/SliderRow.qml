import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles

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
    property alias value: slider.value
    property alias from: slider.from
    property alias to: slider.to
    property alias stepSize: slider.stepSize
    property real defaultValue: 0
    property int decimals: 0
    property bool showValue: true
    
    implicitWidth: parent ? parent.width : 260
    implicitHeight: 32
    
    Row {
        anchors.fill: parent
        spacing: Styles.Theme.spacingMedium
        
        // Label with value
        Item {
            width: labelText.width + (root.showValue ? valueText.width + Styles.Theme.spacingSmall : 0)
            height: parent.height
            
            Text {
                id: labelText
                anchors.verticalCenter: parent.verticalCenter
                text: root.label
                font: Styles.Theme.smallFont
                color: Styles.Theme.text
            }
            
            Text {
                id: valueText
                visible: root.showValue
                anchors.left: labelText.right
                anchors.leftMargin: Styles.Theme.spacingSmall
                anchors.verticalCenter: parent.verticalCenter
                text: formatValue(root.value)
                font: Styles.Theme.smallFont
                color: Styles.Theme.textSecondary
                
                // Format value with +/- sign for bipolar ranges
                function formatValue(val) {
                    var formatted = val.toFixed(root.decimals)
                    if (root.from < 0 && val > 0) {
                        return "+" + formatted
                    }
                    return formatted
                }
            }
            
            // Double-click to reset
            MouseArea {
                anchors.fill: parent
                onDoubleClicked: {
                    root.value = root.defaultValue
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
            width: parent.width - labelText.width - (root.showValue ? valueText.width + Styles.Theme.spacingSmall : 0) - Styles.Theme.spacingMedium
            height: parent.height
            
            from: -100
            to: 100
            value: 0
            stepSize: 1
            
            // Custom styling for edit slider
            background: Rectangle {
                x: slider.leftPadding
                y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: slider.availableWidth
                height: 3
                radius: 1.5
                color: Styles.Theme.sliderTrack
                
                // Center mark for bipolar sliders
                Rectangle {
                    visible: root.from < 0 && root.to > 0
                    anchors.centerIn: parent
                    width: 2
                    height: parent.height + 4
                    color: Styles.Theme.textSecondary
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
                    color: Styles.Theme.sliderFill
                }
            }
            
            handle: Rectangle {
                x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
                y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: 12
                height: 12
                radius: 6
                color: slider.pressed ? Styles.Theme.accentPressed : Styles.Theme.sliderHandle
                
                Behavior on color {
                    ColorAnimation { duration: Styles.Theme.animationFast }
                }
            }
        }
    }
}
