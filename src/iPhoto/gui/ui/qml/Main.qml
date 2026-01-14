import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import Qt.labs.platform 1.1 as Platform
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
    
    // State tracking
    property string currentView: "empty"  // "empty", "gallery", "album"
    property string currentAlbumPath: ""
    property string currentAlbumTitle: ""
    property int sidebarWidth: 220

    // Helper function to convert file:// URL to path
    function urlToPath(urlString) {
        return urlString.toString().replace("file://", "")
    }

    // Menu bar
    menuBar: MenuBar {
        Menu {
            title: qsTr("&File")
            Action {
                text: qsTr("&Open Album...")
                shortcut: StandardKey.Open
                onTriggered: folderDialog.open()
            }
            Action {
                text: qsTr("&Bind Library...")
                onTriggered: libraryDialog.open()
            }
            MenuSeparator {}
            Action {
                text: qsTr("&Quit")
                shortcut: StandardKey.Quit
                onTriggered: Qt.quit()
            }
        }
        Menu {
            title: qsTr("&View")
            Action {
                text: qsTr("&All Photos")
                enabled: isSidebarReady() && sidebarBridge.hasLibrary
                onTriggered: {
                    if (isSidebarReady()) {
                        currentAlbumTitle = "All Photos"
                        currentView = "gallery"
                        if (isGalleryReady() && galleryBridge.model) {
                            galleryBridge.loadAllPhotos()
                        }
                    }
                }
            }
            Action {
                text: qsTr("&Videos")
                enabled: isSidebarReady() && sidebarBridge.hasLibrary
                onTriggered: {
                    currentAlbumTitle = "Videos"
                    currentView = "gallery"
                    if (isGalleryReady() && galleryBridge.model) {
                        galleryBridge.loadVideos()
                    }
                }
            }
            Action {
                text: qsTr("&Live Photos")
                enabled: isSidebarReady() && sidebarBridge.hasLibrary
                onTriggered: {
                    currentAlbumTitle = "Live Photos"
                    currentView = "gallery"
                    if (isGalleryReady() && galleryBridge.model) {
                        galleryBridge.loadLivePhotos()
                    }
                }
            }
            Action {
                text: qsTr("&Favorites")
                enabled: isSidebarReady() && sidebarBridge.hasLibrary
                onTriggered: {
                    currentAlbumTitle = "Favorites"
                    currentView = "gallery"
                    if (isGalleryReady() && galleryBridge.model) {
                        galleryBridge.loadFavorites()
                    }
                }
            }
        }
        Menu {
            title: qsTr("&Help")
            Action {
                text: qsTr("&About iPhoto")
                onTriggered: aboutDialog.open()
            }
        }
    }

    // Folder dialog for opening albums
    Platform.FolderDialog {
        id: folderDialog
        title: "Select Album Folder"
        onAccepted: {
            var path = urlToPath(folder)
            if (isSidebarReady()) {
                currentAlbumPath = path
                currentAlbumTitle = path.split("/").pop()
                currentView = "gallery"
                if (isGalleryReady() && galleryBridge.model) {
                    galleryBridge.loadAlbum(path)
                }
            }
        }
    }

    // Folder dialog for binding library
    Platform.FolderDialog {
        id: libraryDialog
        title: "Select Library Folder"
        onAccepted: {
            var path = urlToPath(folder)
            if (isSidebarReady()) {
                sidebarBridge.bindLibrary(path)
            }
        }
    }

    // About dialog
    Dialog {
        id: aboutDialog
        title: "About iPhoto"
        standardButtons: Dialog.Ok
        anchors.centerIn: parent
        modal: true
        
        ColumnLayout {
            spacing: 16
            
            Text {
                text: "iPhoto"
                font.pixelSize: 24
                font.bold: true
            }
            
            Text {
                text: "Local Photo Album Manager"
                font.pixelSize: 14
                color: "#666666"
            }
            
            Text {
                text: "QML Migration Preview"
                font.pixelSize: 12
                color: "#999999"
            }
        }
    }
    
    // Helper function to check if bridge is ready
    function isSidebarReady() {
        return typeof sidebarBridge !== 'undefined' && sidebarBridge !== null
    }
    
    function isGalleryReady() {
        return typeof galleryBridge !== 'undefined' && galleryBridge !== null
    }
    
    // Use RowLayout instead of SplitView for better compatibility
    RowLayout {
        anchors.fill: parent
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
    }
    
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
                        
                        if (isGalleryReady() && galleryBridge.model) {
                            var lowerTitle = title.toLowerCase()
                            if (lowerTitle === "videos") {
                                galleryBridge.loadVideos()
                            } else if (lowerTitle === "live photos") {
                                galleryBridge.loadLivePhotos()
                            } else if (lowerTitle === "favorites") {
                                galleryBridge.loadFavorites()
                            } else {
                                // For other static nodes (Location, Recently Deleted, Albums),
                                // clear the gallery for now
                                galleryBridge.clear()
                            }
                        }
                    }
                    
                    function onBindLibraryRequested() {
                        console.log("Library binding requested")
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
