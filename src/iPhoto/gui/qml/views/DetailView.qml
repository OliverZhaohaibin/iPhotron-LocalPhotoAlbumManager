import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import "../styles"
import "../components"

/**
 * Detail view for displaying and editing a single asset.
 * 
 * Features:
 * - Header with navigation, metadata, and action buttons
 * - Image/video viewer with zoom controls
 * - Filmstrip navigation
 * - Edit mode integration
 * - Live Photo badge display
 */
Rectangle {
    id: root
    
    // Properties
    property string locationText: ""
    property string timestampText: ""
    property bool isFavorite: false
    property bool editMode: false
    property bool zoomControlsVisible: false
    property int zoomLevel: 100
    property bool hasContent: false
    
    // Signals
    signal backClicked()
    signal infoClicked()
    signal shareClicked()
    signal favoriteClicked()
    signal rotateLeftClicked()
    signal editClicked()
    signal zoomInClicked()
    signal zoomOutClicked()
    signal zoomSliderChanged(int value)
    
    color: Theme.viewerBackground
    
    // Header
    Rectangle {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 56
        color: Theme.viewerSurface
        visible: !root.editMode
        
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.spacingLarge
            anchors.rightMargin: Theme.spacingLarge
            spacing: Theme.spacingMedium
            
            // Back button
            IconButton {
                iconSource: "qrc:/icons/chevron.left.svg"
                tooltipText: qsTr("Return to grid view")
                onClicked: root.backClicked()
            }
            
            // Zoom controls
            Row {
                id: zoomControls
                visible: root.zoomControlsVisible
                spacing: Theme.spacingSmall
                Layout.leftMargin: Theme.spacingMedium
                
                IconButton {
                    iconSource: "qrc:/icons/minus.svg"
                    tooltipText: qsTr("Zoom Out")
                    iconSize: Theme.iconSizeSmall
                    implicitWidth: 20
                    implicitHeight: 20
                    onClicked: root.zoomOutClicked()
                }
                
                Slider {
                    id: zoomSlider
                    width: 90
                    from: 10
                    to: 400
                    value: root.zoomLevel
                    stepSize: 5
                    
                    onValueChanged: {
                        if (value !== root.zoomLevel) {
                            root.zoomSliderChanged(value)
                        }
                    }
                }
                
                IconButton {
                    iconSource: "qrc:/icons/plus.svg"
                    tooltipText: qsTr("Zoom In")
                    iconSize: Theme.iconSizeSmall
                    implicitWidth: 20
                    implicitHeight: 20
                    onClicked: root.zoomInClicked()
                }
            }
            
            // Center info
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                
                Column {
                    anchors.centerIn: parent
                    spacing: 2
                    
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: root.locationText
                        font: Theme.titleFont
                        color: Theme.text
                        visible: text !== ""
                    }
                    
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: root.timestampText
                        font: Theme.bodyFont
                        color: Theme.textSecondary
                        visible: text !== ""
                    }
                }
            }
            
            // Action buttons
            Row {
                spacing: Theme.spacingMedium
                
                IconButton {
                    iconSource: "qrc:/icons/info.circle.svg"
                    tooltipText: qsTr("Info")
                    onClicked: root.infoClicked()
                }
                
                IconButton {
                    iconSource: "qrc:/icons/square.and.arrow.up.svg"
                    tooltipText: qsTr("Share")
                    onClicked: root.shareClicked()
                }
                
                IconButton {
                    iconSource: root.isFavorite ? 
                               "qrc:/icons/suit.heart.fill.svg" : 
                               "qrc:/icons/suit.heart.svg"
                    tooltipText: qsTr("Add to Favorites")
                    onClicked: root.favoriteClicked()
                }
                
                IconButton {
                    iconSource: "qrc:/icons/rotate.left.svg"
                    tooltipText: qsTr("Rotate Left")
                    onClicked: root.rotateLeftClicked()
                }
                
                Button {
                    text: qsTr("Edit")
                    enabled: root.hasContent
                    onClicked: root.editClicked()
                }
            }
        }
        
        // Header separator
        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 2
            color: Theme.headerSeparator
        }
    }
    
    // Content area (placeholder for ImageViewer/VideoArea)
    Item {
        id: contentArea
        anchors.top: header.visible ? header.bottom : parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: filmstrip.top
        
        // Placeholder
        Item {
            anchors.fill: parent
            visible: !root.hasContent
            
            Text {
                anchors.centerIn: parent
                text: qsTr("Select a photo or video to preview")
                font: Theme.bodyFont
                color: Theme.textSecondary
            }
        }
        
        // This is where the actual image viewer would be loaded
        // In the full implementation, this would be a Loader for ImageViewer.qml
        Loader {
            id: viewerLoader
            anchors.fill: parent
            active: root.hasContent
            // source will be set based on content type
        }
        
        // Live Photo badge
        Rectangle {
            id: liveBadge
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.margins: Theme.spacingLarge
            width: liveText.width + Theme.spacingMedium * 2
            height: 28
            radius: 14
            color: Theme.videoBadgeBackground
            visible: false  // Set by controller
            
            Row {
                anchors.centerIn: parent
                spacing: Theme.spacingSmall
                
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 10
                    height: 10
                    radius: 5
                    color: Theme.liveBadgeBackground
                }
                
                Text {
                    id: liveText
                    text: "LIVE"
                    font: Theme.smallFont
                    font.bold: true
                    color: "white"
                }
            }
        }
    }
    
    // Filmstrip
    Filmstrip {
        id: filmstrip
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 80
        visible: !root.editMode
    }
    
    // Public methods for controller binding
    function setContent(type, source) {
        root.hasContent = source !== ""
        if (type === "image") {
            viewerLoader.source = "ImageViewer.qml"
        } else if (type === "video") {
            // viewerLoader.source = "VideoViewer.qml"
        }
    }
    
    function showLiveBadge(show) {
        liveBadge.visible = show
    }
}
