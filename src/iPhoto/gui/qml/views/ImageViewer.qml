import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles

/**
 * Image viewer component with pan, zoom, and gesture support.
 * 
 * This is a QML-native image viewer that can display images with
 * interactive pan and zoom. For full edit functionality including
 * shader-based adjustments, the GLImageViewer widget is used instead.
 * 
 * Features:
 * - Smooth pan and zoom
 * - Pinch-to-zoom gesture support
 * - Fit-to-window and 1:1 modes
 * - Loading/error states
 */
Rectangle {
    id: root
    
    // Properties
    property string source: ""
    property real zoomLevel: 1.0
    property real minZoom: 0.1
    property real maxZoom: 10.0
    property bool fitToWindow: true
    property alias status: image.status
    
    // Signals
    signal zoomChanged(real zoom)
    signal clicked()
    signal doubleClicked()
    
    color: Styles.Theme.viewerBackground
    
    // Content container with pan support
    Flickable {
        id: flickable
        anchors.fill: parent
        contentWidth: imageContainer.width
        contentHeight: imageContainer.height
        clip: true
        
        // Enable smooth scrolling
        flickDeceleration: 1500
        maximumFlickVelocity: 2500
        
        // Center content when smaller than view
        Item {
            id: imageContainer
            width: Math.max(flickable.width, image.width * image.scale)
            height: Math.max(flickable.height, image.height * image.scale)
            
            Image {
                id: image
                anchors.centerIn: parent
                source: root.source
                asynchronous: true
                cache: true
                fillMode: Image.PreserveAspectFit
                
                // Calculate scale based on zoom level and fit mode
                scale: root.fitToWindow ? fitScale : root.zoomLevel
                
                property real fitScale: {
                    if (sourceSize.width === 0 || sourceSize.height === 0) return 1.0
                    var widthRatio = flickable.width / sourceSize.width
                    var heightRatio = flickable.height / sourceSize.height
                    return Math.min(widthRatio, heightRatio, 1.0)
                }
                
                transformOrigin: Item.Center
                
                Behavior on scale {
                    NumberAnimation { duration: Styles.Theme.animationNormal }
                }
            }
        }
        
        ScrollBar.vertical: ScrollBar { }
        ScrollBar.horizontal: ScrollBar { }
    }
    
    // Loading indicator
    BusyIndicator {
        anchors.centerIn: parent
        running: image.status === Image.Loading
        width: 48
        height: 48
    }
    
    // Error state
    Item {
        anchors.fill: parent
        visible: image.status === Image.Error
        
        Column {
            anchors.centerIn: parent
            spacing: Styles.Theme.spacingMedium
            
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "⚠"
                font.pixelSize: 48
                color: Styles.Theme.textSecondary
            }
            
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: qsTr("Unable to load image")
                font: Styles.Theme.bodyFont
                color: Styles.Theme.textSecondary
            }
        }
    }
    
    // Pinch-to-zoom
    PinchArea {
        id: pinchArea
        anchors.fill: parent
        
        property real startZoom: 1.0
        
        onPinchStarted: {
            startZoom = root.zoomLevel
        }
        
        onPinchUpdated: function(pinch) {
            var newZoom = startZoom * pinch.scale
            root.zoomLevel = Math.max(root.minZoom, Math.min(root.maxZoom, newZoom))
            root.fitToWindow = false
            root.zoomChanged(root.zoomLevel)
        }
        
        // Mouse interaction
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            
            onClicked: function(mouse) {
                if (mouse.button === Qt.LeftButton) {
                    root.clicked()
                }
            }
            
            onDoubleClicked: function(mouse) {
                if (mouse.button === Qt.LeftButton) {
                    // Toggle between fit and 1:1
                    if (root.fitToWindow) {
                        root.fitToWindow = false
                        root.zoomLevel = 1.0
                    } else {
                        root.fitToWindow = true
                    }
                    root.zoomChanged(root.zoomLevel)
                    root.doubleClicked()
                }
            }
            
            // Wheel zoom
            onWheel: function(wheel) {
                var delta = wheel.angleDelta.y / 120
                var factor = 1.0 + delta * 0.1
                var newZoom = root.zoomLevel * factor
                root.zoomLevel = Math.max(root.minZoom, Math.min(root.maxZoom, newZoom))
                root.fitToWindow = false
                root.zoomChanged(root.zoomLevel)
            }
        }
    }
    
    // Public methods
    function setSource(src) {
        root.source = src
    }
    
    function setZoom(zoom) {
        root.zoomLevel = Math.max(root.minZoom, Math.min(root.maxZoom, zoom))
        root.fitToWindow = false
    }
    
    function resetZoom() {
        root.fitToWindow = true
        root.zoomLevel = 1.0
    }
    
    function fitToView() {
        root.fitToWindow = true
    }
    
    function actualSize() {
        root.fitToWindow = false
        root.zoomLevel = 1.0
    }
}
