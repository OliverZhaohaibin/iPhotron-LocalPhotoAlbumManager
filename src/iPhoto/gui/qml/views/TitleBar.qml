import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles
import components 1.0 as Components

/**
 * Custom title bar with window controls (traffic lights).
 * 
 * Features:
 * - Window title display
 * - Minimize, maximize/fullscreen, and close buttons
 * - Drag area for window moving
 * - Theme-aware styling
 */
Rectangle {
    id: root
    
    property string windowTitle: "iPhoto"
    property bool isMaximized: false
    
    signal minimizeClicked()
    signal fullscreenClicked()
    signal closeClicked()
    signal dragStarted(point pos)
    signal dragMoved(point pos)
    
    implicitWidth: parent ? parent.width : 400
    implicitHeight: Styles.Theme.titleBarHeight
    
    color: Styles.Theme.titleBarBackground
    
    // Drag area
    MouseArea {
        id: dragArea
        anchors.fill: parent
        anchors.rightMargin: windowControls.width + Styles.Theme.spacingLarge
        
        property point clickPos
        
        onPressed: function(mouse) {
            clickPos = Qt.point(mouse.x, mouse.y)
            root.dragStarted(clickPos)
        }
        
        onPositionChanged: function(mouse) {
            if (pressed) {
                root.dragMoved(Qt.point(mouse.x - clickPos.x, mouse.y - clickPos.y))
            }
        }
        
        // Double-click to toggle fullscreen
        onDoubleClicked: root.fullscreenClicked()
    }
    
    Row {
        anchors.fill: parent
        anchors.leftMargin: Styles.Theme.spacingLarge
        anchors.rightMargin: Styles.Theme.spacingLarge
        anchors.topMargin: 10
        anchors.bottomMargin: 6
        spacing: Styles.Theme.spacingMedium
        
        // Window title
        Text {
            id: titleLabel
            anchors.verticalCenter: parent.verticalCenter
            text: root.windowTitle
            font: Styles.Theme.titleFont
            color: Styles.Theme.text
            elide: Text.ElideRight
            width: parent.width - windowControls.width - parent.spacing
        }
        
        // Window controls (traffic lights)
        Row {
            id: windowControls
            anchors.verticalCenter: parent.verticalCenter
            spacing: 6
            layoutDirection: Qt.RightToLeft
            
            // Close button (red)
            Rectangle {
                id: closeButton
                width: 14
                height: 14
                radius: 7
                color: closeArea.containsMouse ? "#FF605C" : "#FF5F56"
                border.color: "#E0443E"
                border.width: 1
                
                Text {
                    anchors.centerIn: parent
                    text: "×"
                    font.pixelSize: 11
                    font.bold: true
                    color: "#4D0000"
                    visible: closeArea.containsMouse
                }
                
                MouseArea {
                    id: closeArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.closeClicked()
                }
            }
            
            // Fullscreen button (green)
            Rectangle {
                id: fullscreenButton
                width: 14
                height: 14
                radius: 7
                color: fullscreenArea.containsMouse ? "#00CA56" : "#27C93F"
                border.color: "#14AE32"
                border.width: 1
                
                // Maximize icon
                Item {
                    anchors.centerIn: parent
                    visible: fullscreenArea.containsMouse
                    
                    Rectangle {
                        width: 6
                        height: 6
                        x: -3
                        y: -3
                        color: "transparent"
                        border.color: "#006400"
                        border.width: 1
                    }
                }
                
                MouseArea {
                    id: fullscreenArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.fullscreenClicked()
                }
            }
            
            // Minimize button (yellow)
            Rectangle {
                id: minimizeButton
                width: 14
                height: 14
                radius: 7
                color: minimizeArea.containsMouse ? "#FFD93D" : "#FFBD2E"
                border.color: "#DFA123"
                border.width: 1
                
                Rectangle {
                    anchors.centerIn: parent
                    width: 8
                    height: 2
                    color: "#995700"
                    visible: minimizeArea.containsMouse
                }
                
                MouseArea {
                    id: minimizeArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.minimizeClicked()
                }
            }
        }
    }
    
    // Bottom separator
    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: Styles.Theme.headerSeparator
    }
}
