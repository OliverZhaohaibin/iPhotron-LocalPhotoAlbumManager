import QtQuick 2.15
import QtQuick.Controls 2.15
import "../styles"

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
            duration: Theme.animationNormal 
            easing.type: Easing.OutQuad
        }
    }
    
    // Header row
    Rectangle {
        id: header
        width: parent.width
        height: 36
        color: headerMouse.containsMouse ? Theme.sidebarHover : "transparent"
        
        Row {
            anchors.fill: parent
            anchors.leftMargin: Theme.spacingLarge
            anchors.rightMargin: Theme.spacingLarge
            spacing: Theme.spacingSmall
            
            // Disclosure triangle
            Image {
                id: disclosureIcon
                anchors.verticalCenter: parent.verticalCenter
                width: Theme.iconSizeSmall
                height: Theme.iconSizeSmall
                source: "qrc:/icons/chevron.right.svg"
                rotation: root.expanded ? 90 : 0
                opacity: 0.7
                
                Behavior on rotation {
                    NumberAnimation { 
                        duration: Theme.animationFast 
                        easing.type: Easing.OutQuad
                    }
                }
            }
            
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.title
                font: Theme.bodyFont
                font.weight: Font.DemiBold
                color: Theme.text
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
            NumberAnimation { duration: Theme.animationFast }
        }
        
        Column {
            id: contentColumn
            width: parent.width
            spacing: Theme.spacingSmall
            padding: Theme.spacingMedium
            leftPadding: Theme.spacingLarge
            rightPadding: Theme.spacingLarge
            
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
        color: Theme.headerSeparator
        opacity: 0.5
    }
}
