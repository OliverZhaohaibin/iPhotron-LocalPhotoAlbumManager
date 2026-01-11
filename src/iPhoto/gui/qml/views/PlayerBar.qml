import QtQuick 2.15
import QtQuick.Controls
import QtQuick.Layouts 1.15
import styles 1.0 as Styles
import components 1.0 as Components

/**
 * Media player control bar for video and Live Photo playback.
 * 
 * Features:
 * - Play/pause toggle
 * - Seek slider with position indicator
 * - Volume control with mute
 * - Duration display
 * - Theme-aware styling
 */
Rectangle {
    id: root
    
    // Properties
    property bool playing: false
    property bool muted: false
    property int volume: 100
    property real position: 0  // 0.0 to 1.0
    property int duration: 0   // milliseconds
    property int currentTime: 0  // milliseconds
    property bool showVolume: true
    
    // Signals
    signal playPauseClicked()
    signal seekRequested(real position)
    signal volumeChanged(int volume)
    signal muteToggled()
    
    implicitWidth: parent ? parent.width : 400
    implicitHeight: 48
    
    color: Styles.Theme.viewerSurface
    
    // Format time helper
    function formatTime(ms) {
        if (ms < 0) return "0:00"
        var totalSeconds = Math.floor(ms / 1000)
        var minutes = Math.floor(totalSeconds / 60)
        var seconds = totalSeconds % 60
        return minutes + ":" + (seconds < 10 ? "0" : "") + seconds
    }
    
    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Styles.Theme.spacingLarge
        anchors.rightMargin: Styles.Theme.spacingLarge
        spacing: Styles.Theme.spacingMedium
        
        // Play/Pause button
        Components.IconButton {
            iconSource: root.playing ? 
                       iconPrefix + "/pause.fill.svg" :
                       iconPrefix + "/play.fill.svg"
            tooltipText: root.playing ? qsTr("Pause") : qsTr("Play")
            onClicked: root.playPauseClicked()
        }
        
        // Current time
        Text {
            text: formatTime(root.currentTime)
            font: Styles.Theme.smallFont
            color: Styles.Theme.textSecondary
            Layout.minimumWidth: 40
            horizontalAlignment: Text.AlignRight
        }
        
        // Seek slider
        Components.Slider {
            id: seekSlider
            Layout.fillWidth: true
            from: 0
            to: 1
            value: root.position
            
            onPressedChanged: {
                if (!pressed) {
                    root.seekRequested(value)
                }
            }
            
            background: Rectangle {
                x: seekSlider.leftPadding
                y: seekSlider.topPadding + seekSlider.availableHeight / 2 - height / 2
                width: seekSlider.availableWidth
                height: 4
                radius: 2
                color: Styles.Theme.sliderTrack
                
                // Played portion
                Rectangle {
                    width: seekSlider.visualPosition * parent.width
                    height: parent.height
                    radius: parent.radius
                    color: Styles.Theme.accent
                }
                
                // Buffer indicator (if needed)
                // Rectangle {
                //     width: bufferedPosition * parent.width
                //     height: parent.height
                //     radius: parent.radius
                //     color: Qt.rgba(Styles.Theme.accent.r, Styles.Theme.accent.g, Styles.Theme.accent.b, 0.3)
                // }
            }
            
            handle: Rectangle {
                x: seekSlider.leftPadding + seekSlider.visualPosition * (seekSlider.availableWidth - width)
                y: seekSlider.topPadding + seekSlider.availableHeight / 2 - height / 2
                width: 14
                height: 14
                radius: 7
                color: seekSlider.pressed ? Styles.Theme.accentPressed : Styles.Theme.sliderHandle
                visible: seekSlider.hovered || seekSlider.pressed
                
                Behavior on color {
                    ColorAnimation { duration: Styles.Theme.animationFast }
                }
            }
        }
        
        // Duration
        Text {
            text: formatTime(root.duration)
            font: Styles.Theme.smallFont
            color: Styles.Theme.textSecondary
            Layout.minimumWidth: 40
        }
        
        // Volume controls
        Row {
            visible: root.showVolume
            spacing: Styles.Theme.spacingSmall
            Layout.leftMargin: Styles.Theme.spacingMedium
            
            // Mute button
            Components.IconButton {
                iconSource: {
                    if (root.muted || root.volume === 0) {
                        return iconPrefix + "/speaker.slash.fill.svg"
                    } else if (root.volume < 33) {
                        return iconPrefix + "/speaker.wave.1.fill.svg"
                    } else if (root.volume < 66) {
                        return iconPrefix + "/speaker.wave.2.fill.svg"
                    } else {
                        return iconPrefix + "/speaker.wave.3.fill.svg"
                    }
                }
                tooltipText: root.muted ? qsTr("Unmute") : qsTr("Mute")
                onClicked: root.muteToggled()
            }
            
            // Volume slider
            Components.Slider {
                id: volumeSlider
                width: 80
                from: 0
                to: 100
                value: root.volume
                
                onValueChanged: {
                    if (Math.abs(value - root.volume) > 0.5) {
                        root.volumeChanged(Math.round(value))
                    }
                }
                
                background: Rectangle {
                    x: volumeSlider.leftPadding
                    y: volumeSlider.topPadding + volumeSlider.availableHeight / 2 - height / 2
                    width: volumeSlider.availableWidth
                    height: 3
                    radius: 1.5
                    color: Styles.Theme.sliderTrack
                    
                    Rectangle {
                        width: volumeSlider.visualPosition * parent.width
                        height: parent.height
                        radius: parent.radius
                        color: Styles.Theme.sliderFill
                    }
                }
                
                handle: Rectangle {
                    x: volumeSlider.leftPadding + volumeSlider.visualPosition * (volumeSlider.availableWidth - width)
                    y: volumeSlider.topPadding + volumeSlider.availableHeight / 2 - height / 2
                    width: 10
                    height: 10
                    radius: 5
                    color: volumeSlider.pressed ? Styles.Theme.accentPressed : Styles.Theme.sliderHandle
                }
            }
        }
    }
    
    // Top border
    Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: 1
        color: Styles.Theme.headerSeparator
    }
    
    // Public methods
    function setPosition(pos) {
        if (!seekSlider.pressed) {
            root.position = pos
        }
    }
    
    function setVolume(vol) {
        root.volume = vol
    }
    
    function setMuted(m) {
        root.muted = m
    }
    
    function setPlaying(p) {
        root.playing = p
    }
    
    function setDuration(dur) {
        root.duration = dur
    }
    
    function setCurrentTime(time) {
        root.currentTime = time
        if (!seekSlider.pressed && root.duration > 0) {
            root.position = time / root.duration
        }
    }
}
