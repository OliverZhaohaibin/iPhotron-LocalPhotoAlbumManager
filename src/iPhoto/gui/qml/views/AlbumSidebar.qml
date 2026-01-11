import QtQuick
import QtQuick.Controls.Basic
import Qt.labs.qmlmodels 1.0
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
    TreeView {
        id: treeView
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 0
        model: root.model

        ScrollBar.vertical: ScrollBar {
            active: treeView.moving

            background: Rectangle { color: "transparent" }

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
            required property string display
            required property var nodeType
            required property var path

            implicitHeight: 32
            implicitWidth: treeView.width
            width: treeView.width

            property string nodeKey: (nodeType !== undefined && nodeType !== null) ? nodeType.toString().toLowerCase() : ""

            // Classification helpers
            property bool isStatic: nodeKey.indexOf("static") !== -1
            property bool isAction: nodeKey.indexOf("action") !== -1
            property bool isHeader: nodeKey.indexOf("header") !== -1
            property bool isSeparator: nodeKey.indexOf("separator") !== -1
            property bool isAlbum: nodeKey.indexOf("album") !== -1 && !isStatic // ALBUM or SUBALBUM match 'album'

            property bool isSelected: {
                if (isStatic) return display && root.currentStaticSelection === display
                if (isHeader) return display === "Albums" && root.currentStaticSelection === "Albums"
                if (isAlbum) return path && root.currentSelection === path.toString()
                return false
            }

            Rectangle {
                anchors.fill: parent
                anchors.leftMargin: 4
                anchors.rightMargin: 4
                radius: Styles.Theme.borderRadius
                color: isSelected ? Styles.Theme.sidebarSelected :
                       mouseArea.containsMouse ? Styles.Theme.sidebarHover : "transparent"

                Behavior on color {
                    ColorAnimation { duration: Styles.Theme.animationFast }
                }
            }

            Row {
                anchors.fill: parent
                anchors.leftMargin: Styles.Theme.spacingLarge + (TreeView.depth * 16)
                spacing: Styles.Theme.spacingMedium

                Item {
                    width: 16
                    height: parent.height
                    visible: TreeView.hasChildren ?? false

                    Image {
                        anchors.centerIn: parent
                        width: 12
                        height: 12
                        source: iconPrefix + "/chevron.right.svg"
                        rotation: TreeView.isExpanded ? 90 : 0
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
                        onClicked: TreeView.view.toggleExpanded(index)
                    }
                }

                Item {
                    width: 16
                    height: parent.height
                    visible: !TreeView.hasChildren
                }

                Image {
                    id: nodeIcon
                    anchors.verticalCenter: parent.verticalCenter
                    width: Styles.Theme.iconSizeSmall
                    height: Styles.Theme.iconSizeSmall
                    source: {
                        if (isStatic) {
                            if (display === "All Photos") return iconPrefix + "/photo.on.rectangle.svg"
                            if (display === "Location") return iconPrefix + "/map.svg"
                            if (display === "Recently Deleted") return iconPrefix + "/trash.svg"
                            if (display === "Videos") return iconPrefix + "/video.svg"
                            if (display === "Live Photos") return iconPrefix + "/livephoto.svg"
                            if (display === "Favorites") return iconPrefix + "/suit.heart.svg"
                            return iconPrefix + "/folder.svg"
                        }
                        if (nodeKey.indexOf("header") !== -1) return iconPrefix + "/folder.badge.svg"
                        if (nodeKey.indexOf("action") !== -1) return iconPrefix + "/plus.circle.svg"
                        return iconPrefix + "/folder.svg"
                    }
                    opacity: isAction ? 0.6 : 1.0
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: display || ""
                    font.family: Styles.Theme.bodyFont.family
                    font.pixelSize: Styles.Theme.bodyFont.pixelSize
                    font.weight: nodeKey.indexOf("header") !== -1 ? Font.DemiBold : Font.Normal
                    font.italic: isAction
                    color: isSelected ? Styles.Theme.sidebarTextSelected : Styles.Theme.sidebarText
                    opacity: isAction ? 0.7 : 1.0
                    elide: Text.ElideRight
                    width: parent.width - nodeIcon.width - 32 - (TreeView.depth * 16)
                }
            }

            MouseArea {
                id: mouseArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor

                onClicked: {
                    if (isSeparator) return

                    if (isAction) {
                        root.bindLibraryRequested()
                        return
                    }

                    if (isHeader) {
                        if (display === "Albums") {
                            root.currentStaticSelection = "Albums"
                            root.currentSelection = null
                            root.staticNodeSelected("Albums")
                        } else {
                            // Toggle expansion for other headers
                            TreeView.view.toggleExpanded(index)
                        }
                        return
                    }

                    if (isStatic) {
                        root.currentStaticSelection = display
                        root.currentSelection = null
                        if (display === "All Photos") {
                            root.allPhotosSelected()
                        } else {
                            root.staticNodeSelected(display)
                        }
                        return
                    }

                    if (isAlbum) {
                        if (path !== undefined && path !== null) {
                            var pathStr = path.toString()
                            root.currentSelection = pathStr
                            root.currentStaticSelection = ""
                            root.albumSelected(pathStr)
                        } else {
                            console.warn("AlbumSidebar: path is undefined for album node " + display)
                        }
                    }
                }
            }

            DropArea {
                anchors.fill: parent
                enabled: !isStatic && !isAction

                onEntered: function(drag) {
                    if (drag.hasUrls) drag.accept(Qt.CopyAction)
                }

                onDropped: function(drag) {
                    if (drag.hasUrls) {
                        root.filesDropped(path, drag.urls)
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
