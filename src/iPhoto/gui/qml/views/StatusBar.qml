import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles

/**
 * Application status bar displaying messages and progress indicators.
 * 
 * Features:
 * - Message display with auto-clear timeout
 * - Progress bar for long-running operations
 * - Theme-aware styling
 */
Rectangle {
    id: root
    
    property alias message: messageLabel.text
    property alias progressVisible: progressBar.visible
    property alias progressValue: progressBar.value
    property alias progressIndeterminate: progressBar.indeterminate
    property int itemCount: 0
    
    signal messageTimeout()
    
    implicitWidth: parent ? parent.width : 400
    implicitHeight: Styles.Theme.statusBarHeight
    
    color: Styles.Theme.statusBarBackground
    
    // Auto-clear timer
    Timer {
        id: clearTimer
        interval: 3000
        repeat: false
        onTriggered: {
            messageLabel.text = ""
            root.messageTimeout()
        }
    }
    
    Row {
        anchors.fill: parent
        anchors.leftMargin: Styles.Theme.spacingLarge
        anchors.rightMargin: Styles.Theme.spacingLarge + 25  // Reserve space for resize grip
        spacing: Styles.Theme.spacingLarge
        
        // Message label
        Text {
            id: messageLabel
            anchors.verticalCenter: parent.verticalCenter
            font: Styles.Theme.smallFont
            color: Styles.Theme.statusBarText
            elide: Text.ElideRight
            width: parent.width - itemCountLabel.width - progressBar.width - parent.spacing * 2
        }
        
        // Item count
        Text {
            id: itemCountLabel
            anchors.verticalCenter: parent.verticalCenter
            font: Styles.Theme.smallFont
            color: Styles.Theme.statusBarText
            visible: root.itemCount > 0 && !progressBar.visible
            text: root.itemCount === 1 ? qsTr("1 item") : qsTr("%1 items").arg(root.itemCount)
        }
        
        // Progress bar
        ProgressBar {
            id: progressBar
            anchors.verticalCenter: parent.verticalCenter
            width: 160
            visible: false
            
            from: 0
            to: 100
            value: 0
            
            background: Rectangle {
                implicitWidth: 160
                implicitHeight: 6
                color: Styles.Theme.sliderTrack
                radius: 3
            }
            
            contentItem: Item {
                implicitWidth: 160
                implicitHeight: 6
                
                Rectangle {
                    width: progressBar.indeterminate ? 
                           parent.width * 0.3 : 
                           progressBar.visualPosition * parent.width
                    height: parent.height
                    radius: 3
                    color: Styles.Theme.accent
                    
                    // Animation for indeterminate mode
                    SequentialAnimation on x {
                        running: progressBar.indeterminate && progressBar.visible
                        loops: Animation.Infinite
                        
                        NumberAnimation {
                            from: 0
                            to: progressBar.width * 0.7
                            duration: 1000
                            easing.type: Easing.InOutQuad
                        }
                        NumberAnimation {
                            from: progressBar.width * 0.7
                            to: 0
                            duration: 1000
                            easing.type: Easing.InOutQuad
                        }
                    }
                }
            }
        }
    }
    
    // Public functions to match QWidget API
    function showMessage(msg, timeout) {
        messageLabel.text = msg
        clearTimer.stop()
        if (timeout > 0) {
            clearTimer.interval = timeout
            clearTimer.start()
        }
    }
    
    function clearMessage() {
        messageLabel.text = ""
        clearTimer.stop()
    }
    
    /**
     * Returns the currently displayed message text.
     * Provided for API compatibility with the QWidget-based ChromeStatusBar.
     */
    function currentMessage() {
        return messageLabel.text
    }
    
    function showProgress(min, max, value) {
        progressBar.from = min
        progressBar.to = max
        progressBar.value = value
        progressBar.indeterminate = false
        progressBar.visible = true
    }
    
    function showIndeterminateProgress() {
        progressBar.indeterminate = true
        progressBar.visible = true
    }
    
    function hideProgress() {
        progressBar.visible = false
    }
}
