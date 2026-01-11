import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles

/**
 * Collapsible section container for organizing edit controls.
 * 
 * Features:
 * - Animated expand/collapse
 * - Header with title and disclosure indicator
 * - Automatic content height management
 */
Item {
    id: root
    
    property string title: "Section"
    property bool expanded: true
    property alias content: contentLoader.sourceComponent
    default property alias contentData: contentColumn.data
    
    implicitWidth: parent ? parent.width : 280
    implicitHeight: header.height + (expanded ? contentContainer.height : 0)
    
    clip: true
    
    Behavior on implicitHeight {
        NumberAnimation { 
            duration: Styles.Theme.animationNormal 
            easing.type: Easing.OutQuad
        }
    }
    
    // Header row
    Rectangle {
        id: header
        width: parent.width
        height: 36
        color: headerMouse.containsMouse ? Styles.Theme.sidebarHover : "transparent"
        
        Row {
            anchors.fill: parent
            anchors.leftMargin: Styles.Theme.spacingLarge
            anchors.rightMargin: Styles.Theme.spacingLarge
            spacing: Styles.Theme.spacingSmall
            
            // Disclosure triangle
            Image {
                id: disclosureIcon
                anchors.verticalCenter: parent.verticalCenter
                width: Styles.Theme.iconSizeSmall
                height: Styles.Theme.iconSizeSmall
                source: iconPrefix + "/chevron.right.svg"
                rotation: root.expanded ? 90 : 0
                opacity: 0.7
                
                Behavior on rotation {
                    NumberAnimation { 
                        duration: Styles.Theme.animationFast 
                        easing.type: Easing.OutQuad
                    }
                }
            }
            
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.title
                font.family: Styles.Theme.bodyFont.family
                font.pixelSize: Styles.Theme.bodyFont.pixelSize
                font.weight: Font.DemiBold
                color: Styles.Theme.text
            }
        }
        
        MouseArea {
            id: headerMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.expanded = !root.expanded
        }
    }
    
    // Content container
    Item {
        id: contentContainer
        anchors.top: header.bottom
        width: parent.width
        height: contentColumn.height
        opacity: root.expanded ? 1.0 : 0.0
        visible: opacity > 0
        
        Behavior on opacity {
            NumberAnimation { duration: Styles.Theme.animationFast }
        }
        
        Column {
            id: contentColumn
            width: parent.width
            spacing: Styles.Theme.spacingSmall
            padding: Styles.Theme.spacingMedium
            leftPadding: Styles.Theme.spacingLarge
            rightPadding: Styles.Theme.spacingLarge
            
            Loader {
                id: contentLoader
                width: parent.width - parent.leftPadding - parent.rightPadding
                active: root.expanded
            }
        }
    }
    
    // Bottom separator
    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: Styles.Theme.headerSeparator
        opacity: 0.5
    }
}
