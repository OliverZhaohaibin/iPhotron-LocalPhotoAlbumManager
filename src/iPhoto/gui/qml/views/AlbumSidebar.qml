import QtQuick 2.15
import QtQuick.Controls 2.15
import styles 1.0 as Styles

/**
 * Album navigation sidebar showing the library tree.
 * 
 * Features:
 * - Hierarchical album tree with expand/collapse
 * - Static nodes (All Photos, Albums, Location, Recently Deleted)
 * - Selection highlighting
 * - Drag and drop support for importing files
 * - Theme-aware styling
 */
Rectangle {
    id: root
    
    // Public properties
    property string title: "Basic Library"
    property alias model: treeView.model
    property var currentSelection: null
    property string currentStaticSelection: ""
    
    // Signals
    signal albumSelected(string path)
    signal allPhotosSelected()
    signal staticNodeSelected(string title)
    signal bindLibraryRequested()
    signal filesDropped(string targetPath, var urls)
    
    implicitWidth: Styles.Theme.sidebarWidth
    implicitHeight: parent ? parent.height : 400
    
    color: Styles.Theme.sidebarBackground
    
    // Header
    Item {
        id: header
        width: parent.width
        height: 44
        
        Text {
            id: titleLabel
            anchors.left: parent.left
            anchors.leftMargin: Styles.Theme.spacingLarge
            anchors.verticalCenter: parent.verticalCenter
            text: root.title
            font: Styles.Theme.titleFont
            color: Styles.Theme.sidebarText
            elide: Text.ElideRight
            width: parent.width - Styles.Theme.spacingLarge * 2
        }
    }
    
    // Tree view
    ListView {
        id: treeView
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 0
        
        clip: true
        
        ScrollBar.vertical: ScrollBar {
            active: treeView.moving
            
            background: Rectangle {
                color: "transparent"
            }
            
            contentItem: Rectangle {
                implicitWidth: 6
                radius: 3
                color: Styles.Theme.scrollbarHandle
                opacity: parent.active || parent.hovered ? 1.0 : 0.0
                
                Behavior on opacity {
                    NumberAnimation { duration: Styles.Theme.animationNormal }
                }
            }
        }
        
        delegate: Item {
            id: delegateRoot
            width: ListView.view.width
            height: 32
            
            property bool isExpanded: model.expanded || false
            property bool hasChildren: model.hasChildren || false
            property int depth: model.depth || 0
            property string nodeType: model.nodeType || "album"
            property bool isSelected: {
                if (nodeType === "static") {
                    return model.title === root.currentStaticSelection
                }
                return model.path === root.currentSelection
            }
            
            // Selection/hover background
            Rectangle {
                anchors.fill: parent
                anchors.leftMargin: 4
                anchors.rightMargin: 4
                radius: Styles.Theme.borderRadius
                color: delegateRoot.isSelected ? Styles.Theme.sidebarSelected :
                       delegateMouse.containsMouse ? Styles.Theme.sidebarHover : "transparent"
                
                Behavior on color {
                    ColorAnimation { duration: Styles.Theme.animationFast }
                }
            }
            
            Row {
                anchors.fill: parent
                anchors.leftMargin: Styles.Theme.spacingLarge + (delegateRoot.depth * 16)
                spacing: Styles.Theme.spacingMedium
                
                // Branch indicator (disclosure triangle)
                Item {
                    width: 16
                    height: parent.height
                    visible: delegateRoot.hasChildren
                    
                    Image {
                        anchors.centerIn: parent
                        width: 12
                        height: 12
                        source: "qrc:/icons/chevron.right.svg"
                        rotation: delegateRoot.isExpanded ? 90 : 0
                        opacity: 0.6
                        
                        Behavior on rotation {
                            NumberAnimation { 
                                duration: Styles.Theme.animationFast 
                                easing.type: Easing.OutQuad
                            }
                        }
                    }
                    
                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -4
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            model.expanded = !model.expanded
                        }
                    }
                }
                
                // Spacer when no children
                Item {
                    width: 16
                    height: parent.height
                    visible: !delegateRoot.hasChildren
                }
                
                // Icon
                Image {
                    id: nodeIcon
                    anchors.verticalCenter: parent.verticalCenter
                    width: Styles.Theme.iconSizeSmall
                    height: Styles.Theme.iconSizeSmall
                    source: {
                        switch (delegateRoot.nodeType) {
                            case "static":
                                if (model.title === "All Photos") return "qrc:/icons/photo.on.rectangle.svg"
                                if (model.title === "Location") return "qrc:/icons/map.svg"
                                if (model.title === "Recently Deleted") return "qrc:/icons/trash.svg"
                                return "qrc:/icons/folder.svg"
                            case "header":
                                return "qrc:/icons/folder.badge.svg"
                            case "album":
                            case "subalbum":
                                return "qrc:/icons/folder.svg"
                            case "action":
                                return "qrc:/icons/plus.circle.svg"
                            default:
                                return "qrc:/icons/folder.svg"
                        }
                    }
                    opacity: delegateRoot.nodeType === "action" ? 0.6 : 1.0
                }
                
                // Text
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: model.displayName || model.title || ""
                    font.family: Styles.Theme.bodyFont.family
                    font.pixelSize: Styles.Theme.bodyFont.pixelSize
                    font.weight: delegateRoot.nodeType === "header" ? Font.DemiBold : Font.Normal
                    font.italic: delegateRoot.nodeType === "action"
                    color: delegateRoot.isSelected ? Styles.Theme.sidebarTextSelected : Styles.Theme.sidebarText
                    opacity: delegateRoot.nodeType === "action" ? 0.7 : 1.0
                    elide: Text.ElideRight
                    width: parent.width - nodeIcon.width - 32 - (delegateRoot.depth * 16)
                }
            }
            
            MouseArea {
                id: delegateMouse
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                
                onClicked: function(mouse) {
                    if (mouse.button === Qt.LeftButton) {
                        handleSelection()
                    } else if (mouse.button === Qt.RightButton) {
                        // Context menu would go here
                    }
                }
                
                onDoubleClicked: {
                    if (delegateRoot.nodeType === "action") {
                        root.bindLibraryRequested()
                    } else if (delegateRoot.hasChildren) {
                        model.expanded = !model.expanded
                    }
                }
                
                function handleSelection() {
                    switch (delegateRoot.nodeType) {
                        case "action":
                            root.bindLibraryRequested()
                            break
                        case "header":
                            if (model.title === "Albums") {
                                root.staticNodeSelected("Albums")
                            }
                            break
                        case "static":
                            root.currentStaticSelection = model.title
                            root.currentSelection = null
                            if (model.title === "All Photos") {
                                root.allPhotosSelected()
                            } else {
                                root.staticNodeSelected(model.title)
                            }
                            break
                        case "album":
                        case "subalbum":
                            root.currentSelection = model.path
                            root.currentStaticSelection = ""
                            root.albumSelected(model.path)
                            break
                    }
                }
            }
            
            // Drop area for importing files
            DropArea {
                anchors.fill: parent
                enabled: delegateRoot.nodeType === "album" || delegateRoot.nodeType === "subalbum"
                
                onEntered: function(drag) {
                    if (drag.hasUrls) {
                        drag.accept(Qt.CopyAction)
                    }
                }
                
                onDropped: function(drag) {
                    if (drag.hasUrls) {
                        root.filesDropped(model.path, drag.urls)
                    }
                }
            }
        }
    }
    
    // Public methods
    function selectPath(path) {
        root.currentSelection = path
        root.currentStaticSelection = ""
        // Scroll to and highlight the item
    }
    
    function selectAllPhotos() {
        selectStaticNode("All Photos")
    }
    
    function selectStaticNode(title) {
        root.currentStaticSelection = title
        root.currentSelection = null
    }
}
