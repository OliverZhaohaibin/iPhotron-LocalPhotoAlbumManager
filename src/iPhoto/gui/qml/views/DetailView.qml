import QtQuick 2.15
import QtQuick.Controls
import QtQuick.Layouts 1.15
import styles 1.0 as Styles
import components 1.0 as Components

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
    
    color: Styles.Theme.viewerBackground
    
    // Header
    Rectangle {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 56
        color: Styles.Theme.viewerSurface
        visible: !root.editMode
        
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Styles.Theme.spacingLarge
            anchors.rightMargin: Styles.Theme.spacingLarge
            spacing: Styles.Theme.spacingMedium
            
            // Back button
            Components.IconButton {
                iconSource: iconPrefix + "/chevron.left.svg"
                tooltipText: qsTr("Return to grid view")
                onClicked: root.backClicked()
            }
            
            // Zoom controls
            Row {
                id: zoomControls
                visible: root.zoomControlsVisible
                spacing: Styles.Theme.spacingSmall
                Layout.leftMargin: Styles.Theme.spacingMedium
                
                Components.IconButton {
                    iconSource: iconPrefix + "/minus.svg"
                    tooltipText: qsTr("Zoom Out")
                    iconSize: Styles.Theme.iconSizeSmall
                    implicitWidth: 20
                    implicitHeight: 20
                    onClicked: root.zoomOutClicked()
                }
                
                Components.Slider {
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
                
                Components.IconButton {
                    iconSource: iconPrefix + "/plus.svg"
                    tooltipText: qsTr("Zoom In")
                    iconSize: Styles.Theme.iconSizeSmall
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
                        font: Styles.Theme.titleFont
                        color: Styles.Theme.text
                        visible: text !== ""
                    }
                    
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: root.timestampText
                        font: Styles.Theme.bodyFont
                        color: Styles.Theme.textSecondary
                        visible: text !== ""
                    }
                }
            }
            
            // Action buttons
            Row {
                spacing: Styles.Theme.spacingMedium
                
                Components.IconButton {
                    iconSource: iconPrefix + "/info.circle.svg"
                    tooltipText: qsTr("Info")
                    onClicked: root.infoClicked()
                }
                
                Components.IconButton {
                    iconSource: iconPrefix + "/square.and.arrow.up.svg"
                    tooltipText: qsTr("Share")
                    onClicked: root.shareClicked()
                }
                
                Components.IconButton {
                    iconSource: root.isFavorite ? 
                               iconPrefix + "/suit.heart.fill.svg" :
                               iconPrefix + "/suit.heart.svg"
                    tooltipText: qsTr("Add to Favorites")
                    onClicked: root.favoriteClicked()
                }
                
                Components.IconButton {
                    iconSource: iconPrefix + "/rotate.left.svg"
                    tooltipText: qsTr("Rotate Left")
                    onClicked: root.rotateLeftClicked()
                }
                
                Components.Button {
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
            color: Styles.Theme.headerSeparator
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
                font: Styles.Theme.bodyFont
                color: Styles.Theme.textSecondary
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
            anchors.margins: Styles.Theme.spacingLarge
            width: liveText.width + Styles.Theme.spacingMedium * 2
            height: 28
            radius: 14
            color: Styles.Theme.videoBadgeBackground
            visible: false  // Set by controller
            
            Row {
                anchors.centerIn: parent
                spacing: Styles.Theme.spacingSmall
                
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 10
                    height: 10
                    radius: 5
                    color: Styles.Theme.liveBadgeBackground
                }
                
                Text {
                    id: liveText
                    text: "LIVE"
                    font.family: Styles.Theme.smallFont.family
                    font.pixelSize: Styles.Theme.smallFont.pixelSize
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
