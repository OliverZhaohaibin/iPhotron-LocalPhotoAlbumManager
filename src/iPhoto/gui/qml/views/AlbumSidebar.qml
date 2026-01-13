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

        // Auto-expand headers on startup
        Timer {
            id: startupExpand
            interval: 50
            running: true
            repeat: false
            onTriggered: {
                // Expand "Basic Library" (usually row 0) and "Albums" (usually row 2)
                // Expand indices 0 to 4 (five rows) to safely cover headers in standard layout
                for (var i = 0; i < 5; i++) {
                    treeView.expand(i)
                }
            }
        }

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
            required property var model

            implicitHeight: 32
            implicitWidth: treeView.width
            width: treeView.width

            property string displayText: (model && model.display !== undefined) ? model.display : ""
            property var nodeTypeValue: (model && model.nodeType !== undefined) ? model.nodeType : null
            property var pathValue: (model && model.path !== undefined) ? model.path : null

            property string nodeKey: (nodeTypeValue !== undefined && nodeTypeValue !== null) ? nodeTypeValue.toString().toLowerCase() : ""

            // Classification helpers
            property bool isStatic: nodeKey === "static"
            property bool isAction: nodeKey.indexOf("action") !== -1
            property bool isHeader: nodeKey.indexOf("header") !== -1
            property bool isSeparator: nodeKey.indexOf("separator") !== -1
            property bool isAlbum: nodeKey.indexOf("album") !== -1 && !isStatic // ALBUM or SUBALBUM match 'album'

            property bool isSelected: {
                if (isStatic) return displayText && root.currentStaticSelection === displayText
                if (isHeader) return displayText === "Albums" && root.currentStaticSelection === "Albums"
                if (isAlbum) return pathValue && root.currentSelection === pathValue.toString()
                return false
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
                        if (displayText === "Albums") {
                            root.currentStaticSelection = "Albums"
                            root.currentSelection = null
                            root.staticNodeSelected("Albums")
                        } else if (displayText !== "Basic Library") {
                            // Toggle expansion for headers other than Basic Library
                            treeView.toggleExpanded(index)
                        }
                        return
                    }

                    if (isStatic) {
                        root.currentStaticSelection = displayText
                        root.currentSelection = null
                        if (displayText === "All Photos") {
                            root.allPhotosSelected()
                        } else {
                            root.staticNodeSelected(displayText)
                        }
                        return
                    }

                    if (isAlbum) {
                        if (pathValue !== undefined && pathValue !== null) {
                            var pathStr = pathValue.toString()
                            root.currentSelection = pathStr
                            root.currentStaticSelection = ""
                            root.albumSelected(pathStr)
                        } else {
                            console.warn("AlbumSidebar: path is undefined for album node " + displayText)
                        }
                    }
                }
            }

            Rectangle {
                anchors.fill: parent
                anchors.leftMargin: 4
                anchors.rightMargin: 4
                radius: Styles.Theme.borderRadius
                color: isSelected ? Styles.Theme.sidebarSelected :
                       (mouseArea.containsMouse && !isSeparator) ? Styles.Theme.sidebarHover : "transparent"

                Behavior on color {
                    ColorAnimation { duration: Styles.Theme.animationFast }
                }
            }

            // Separator Line
            Rectangle {
                visible: isSeparator
                anchors.centerIn: parent
                width: parent.width - (Styles.Theme.spacingLarge * 2)
                height: 1
                color: Styles.Theme.textSecondary
                opacity: 0.2
            }

            Row {
                visible: !isSeparator
                anchors.fill: parent
                anchors.leftMargin: Styles.Theme.spacingLarge + (TreeView.depth * 16)
                spacing: Styles.Theme.spacingMedium

                Item {
                    width: 16
                    height: parent.height
                    // Hide chevron for Basic Library (it should always be expanded/resident)
                    visible: (TreeView.hasChildren ?? false) && displayText !== "Basic Library"

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
                        onClicked: treeView.toggleExpanded(index)
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
                            if (displayText === "All Photos") return iconPrefix + "/photo.on.rectangle.svg"
                            if (displayText === "Location") return iconPrefix + "/mappin.and.ellipse.svg"
                            if (displayText === "Recently Deleted") return iconPrefix + "/trash.svg"
                            if (displayText === "Videos") return iconPrefix + "/video.svg"
                            if (displayText === "Live Photos") return iconPrefix + "/livephoto.svg"
                            if (displayText === "Favorites") return iconPrefix + "/suit.heart.svg"
                            return iconPrefix + "/folder.svg"
                        }
                        if (nodeKey.indexOf("header") !== -1) return iconPrefix + "/folder.svg"
                        if (nodeKey.indexOf("action") !== -1) return iconPrefix + "/plus.circle.svg"
                        return iconPrefix + "/folder.svg"
                    }
                    opacity: isAction ? 0.6 : 1.0
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: displayText || ""
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

            DropArea {
                anchors.fill: parent
                enabled: !isStatic && !isAction

                onEntered: function(drag) {
                    if (drag.hasUrls) drag.accept(Qt.CopyAction)
                }

                onDropped: function(drag) {
                    if (drag.hasUrls) {
                        root.filesDropped(pathValue, drag.urls)
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
