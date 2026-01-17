import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import QtQuick.Dialogs
import "."  // Import local QML files

ApplicationWindow {
    id: mainWindow
    visible: true
    width: 1200
    height: 720
    title: "iPhoto"
    
    // Color palette matching the widget implementation
    readonly property color sidebarBackground: "#eef3f6"
    readonly property color sidebarSelectedBackground: "#e0e0e0"
    readonly property color sidebarTextColor: "#2b2b2b"
    readonly property color sidebarIconColor: "#1e73ff"
    readonly property color separatorColor: "#d0d0d0"
    readonly property color contentBackground: "#ffffff"
    readonly property color contentBackgroundDark: "#1b1b1b"
    readonly property color hoverBackground: Qt.rgba(0, 0, 0, 0.1)
    
    // Menu button styling
    readonly property int menuFontSize: 13
    readonly property int menuButtonHeight: 26

    // Native menubar replacement for consistent visibility
    MenuBar {
        id: inlineMenuBar
        nativeMenuBar: false
        background: Rectangle { color: sidebarBackground }
        Menu {
            title: qsTr("File")
            MenuItem { text: qsTr("Open Album Folder…"); onTriggered: albumFolderDialog.open() }
            MenuSeparator {}
            MenuItem { text: qsTr("Set Basic Library…"); onTriggered: libraryFolderDialog.open() }
            MenuSeparator {}
            MenuItem { text: qsTr("Export All Edited"); enabled: false }
            MenuItem { text: qsTr("Export Selected"); enabled: false }
            MenuSeparator {}
            MenuItem {
                text: qsTr("Rebuild Live Links")
                enabled: isSidebarReady() && sidebarBridge.hasLibrary
                onTriggered: {
                    if (appBridge) { appBridge.rebuildLiveLinks() }
                }
            }
        }
        Menu {
            title: qsTr("Settings")
            MenuItem { text: qsTr("Show Filmstrip"); checkable: true; checked: true }
            MenuSeparator {}
            Menu {
                title: qsTr("Appearance")
                MenuItem { text: qsTr("System Default"); checkable: true; checked: true }
                MenuItem { text: qsTr("Light Mode"); checkable: true }
                MenuItem { text: qsTr("Dark Mode"); checkable: true }
            }
            Menu {
                title: qsTr("Wheel Action")
                MenuItem { text: qsTr("Navigate"); checkable: true; checked: true }
                MenuItem { text: qsTr("Zoom"); checkable: true }
            }
            Menu {
                title: qsTr("Share Action")
                MenuItem { text: qsTr("Copy File"); checkable: true }
                MenuItem { text: qsTr("Copy Path"); checkable: true }
                MenuItem { text: qsTr("Reveal in File Manager"); checkable: true; checked: true }
            }
        }
    }

    menuBar: inlineMenuBar
    
    // State tracking
    property string currentView: "empty"  // "empty", "gallery", "album"
    property string currentAlbumPath: ""
    property string currentAlbumTitle: ""
    property int sidebarWidth: 220
    
    // Helper function to check if bridge is ready
    function isSidebarReady() {
        return typeof sidebarBridge !== 'undefined' && sidebarBridge !== null
    }
    
    function isGalleryReady() {
        return typeof galleryBridge !== 'undefined' && galleryBridge !== null
    }
    
    // Folder dialog for opening an arbitrary album
    FolderDialog {
        id: albumFolderDialog
        title: "Select Album Folder"
        onAccepted: {
            var path = selectedFolder.toString()
            if (path.startsWith("file://")) {
                path = path.substring(7)
            }
            currentAlbumPath = path
            currentAlbumTitle = path.split("/").pop()
            currentView = "gallery"
            if (appBridge) {
                appBridge.openAlbum(path)
            } else if (isGalleryReady()) {
                galleryBridge.loadAlbum(path)
            }
        }
    }

    // Folder dialog for binding the basic library
    FolderDialog {
        id: libraryFolderDialog
        title: "Select Basic Library"
        onAccepted: {
            var path = selectedFolder.toString()
            if (path.startsWith("file://")) {
                path = path.substring(7)
            }
            if (appBridge) {
                appBridge.bindLibrary(path)
            } else if (isSidebarReady()) {
                sidebarBridge.bindLibrary(path)
            }
        }
    }

    // Main layout with header bar and content
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        
        // Main content area
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            
            // Sidebar with drag handle
            Item {
                id: sidebarContainer
                Layout.preferredWidth: sidebarWidth
                Layout.minimumWidth: 180
                Layout.maximumWidth: 350
                Layout.fillHeight: true
                
                Sidebar {
                    id: sidebar
                    anchors.fill: parent
                }
                
                // Resize handle
                Rectangle {
                    id: resizeHandle
                    width: 4
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    color: mouseAreaResize.containsMouse || mouseAreaResize.drag.active ? "#007AFF" : "transparent"
                    
                    Behavior on color { ColorAnimation { duration: 150 } }
                    
                    MouseArea {
                        id: mouseAreaResize
                        anchors.fill: parent
                        anchors.margins: -2
                        hoverEnabled: true
                        cursorShape: Qt.SizeHorCursor
                        
                        property real startX: 0
                        property real startWidth: 0
                        
                        onPressed: {
                            startX = mouseX
                            startWidth = sidebarWidth
                        }
                        
                        onPositionChanged: {
                            if (pressed) {
                                var delta = mouseX - startX
                                var newWidth = startWidth + delta
                                sidebarWidth = Math.max(180, Math.min(350, newWidth))
                            }
                        }
                    }
                }
            }
            
            // Separator line
            Rectangle {
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                color: separatorColor
            }
            
            // Main content area
            Rectangle {
                id: contentArea
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: contentBackground
            
            // Empty state / Welcome screen (always shown in current state)
            Item {
                id: emptyView
                anchors.fill: parent
                visible: currentView === "empty"
                
                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 20
                    
                    // Icon
                    Image {
                        Layout.alignment: Qt.AlignHCenter
                        source: "image://icons/photo.on.rectangle.svg?color=#666666"
                        sourceSize.width: 64
                        sourceSize.height: 64
                        fillMode: Image.PreserveAspectFit
                    }
                    
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: "iPhoto"
                        font.pixelSize: 32
                        font.bold: true
                        color: "#333333"
                    }
                    
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: isSidebarReady() && sidebarBridge.hasLibrary 
                            ? "Select an album from the sidebar to view photos"
                            : "Set a library folder to get started"
                        font.pixelSize: 16
                        color: "#666666"
                    }
                    
                    Text {
                        id: statusText
                        Layout.alignment: Qt.AlignHCenter
                        text: isSidebarReady() && sidebarBridge.hasLibrary ? "Library bound" : "No library bound"
                        font.pixelSize: 14
                        color: isSidebarReady() && sidebarBridge.hasLibrary ? "#28a745" : "#dc3545"
                    }
                }
            }
            
            // Gallery view
            GalleryView {
                id: galleryView
                anchors.fill: parent
                visible: currentView === "gallery"
                albumTitle: currentAlbumTitle
            }
        }
        }  // End RowLayout (main content)
    }  // End ColumnLayout
    
    // Handle signals from bridges - use Loader to defer connection
    Loader {
        id: connectionsLoader
        active: isSidebarReady()
        sourceComponent: Component {
            Item {
                Connections {
                    target: sidebarBridge
                    
                    function onAlbumSelected(path) {
                        console.log("Album selected:", path)
                        currentAlbumPath = path
                        currentAlbumTitle = path.split("/").pop()
                        currentView = "gallery"
                        
                        if (isGalleryReady() && galleryBridge.model) {
                            galleryBridge.loadAlbum(path)
                        }
                    }
                    
                    function onAllPhotosSelected() {
                        console.log("All photos selected")
                        currentAlbumTitle = "All Photos"
                        currentView = "gallery"
                        
                        if (isGalleryReady() && galleryBridge.model) {
                            galleryBridge.loadAllPhotos()
                        }
                    }
                    
                    function onStaticNodeSelected(title) {
                        console.log("Static node selected:", title)
                        currentAlbumTitle = title
                        currentView = "gallery"
                        
                        // For now, show empty gallery for static nodes other than All Photos
                        if (isGalleryReady() && galleryBridge.model) {
                            galleryBridge.clear()
                        }
                        statusText.text = "Viewing: " + title
                    }
                    
                    function onBindLibraryRequested() {
                        console.log("Library binding requested")
                        libraryFolderDialog.open()
                    }
                    
                    function onHasLibraryChanged() {
                        console.log("Library changed:", sidebarBridge.hasLibrary)
                    }
                }
            }
        }
    }
    
    // Handle gallery signals
    Loader {
        id: galleryConnectionsLoader
        active: isGalleryReady()
        sourceComponent: Component {
            Item {
                Connections {
                    target: galleryBridge
                    
                    function onItemSelected(path) {
                        console.log("Gallery item selected:", path)
                    }
                }
            }
        }
    }
    
    Component.onCompleted: {
        console.log("Main window loaded")
        console.log("sidebarBridge ready:", isSidebarReady())
        console.log("galleryBridge ready:", isGalleryReady())
    }
}
