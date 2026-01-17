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
    
    // Folder dialog for binding library
    FolderDialog {
        id: folderDialog
        title: "Select Library Folder"
        onAccepted: {
            if (isSidebarReady()) {
                // Convert file:// URL to path string properly
                // On Unix: file:///path/to/folder -> /path/to/folder
                // On Windows: file:///C:/path -> C:/path
                var path = selectedFolder.toString()
                if (path.startsWith("file:///")) {
                    // Handle both Unix and Windows paths
                    path = path.substring(7)  // Keep one leading slash for Unix
                } else if (path.startsWith("file://")) {
                    path = path.substring(7)
                }
                console.log("Binding library to:", path)
                sidebarBridge.bindLibrary(path)
            }
        }
    }
    
    // Main layout with header bar and content
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        
        // Header bar with menu buttons
        Rectangle {
            id: headerBar
            Layout.fillWidth: true
            Layout.preferredHeight: 32
            color: sidebarBackground
            
            Row {
                id: menuRow
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                
                // File menu button
                Button {
                    id: fileMenuButton
                    text: qsTr("File")
                    flat: true
                    font.pixelSize: menuFontSize
                    implicitWidth: 50
                    implicitHeight: menuButtonHeight
                    
                    background: Rectangle {
                        color: fileMenuButton.hovered || fileMenu.visible ? hoverBackground : "transparent"
                        radius: 4
                    }
                    
                    contentItem: Text {
                        text: fileMenuButton.text
                        font: fileMenuButton.font
                        color: sidebarTextColor
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    
                    onClicked: {
                        console.log("File menu button clicked")
                        fileMenu.popup(0, fileMenuButton.height)
                    }
                }
                
                // Settings menu button
                Button {
                    id: settingsMenuButton
                    text: qsTr("Settings")
                    flat: true
                    font.pixelSize: menuFontSize
                    implicitWidth: 70
                    implicitHeight: menuButtonHeight
                    
                    background: Rectangle {
                        color: settingsMenuButton.hovered || settingsMenu.visible ? hoverBackground : "transparent"
                        radius: 4
                    }
                    
                    contentItem: Text {
                        text: settingsMenuButton.text
                        font: settingsMenuButton.font
                        color: sidebarTextColor
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    
                    onClicked: {
                        console.log("Settings menu button clicked")
                        settingsMenu.popup(0, settingsMenuButton.height)
                    }
                }
            }
            
            // File menu (defined outside button for proper popup behavior)
            Menu {
                id: fileMenu
                
                MenuItem {
                    text: qsTr("Open Album Folder…")
                    onTriggered: {
                        console.log("Open Album Folder triggered")
                        folderDialog.open()
                    }
                }
                
                MenuSeparator {}
                
                MenuItem {
                    text: qsTr("Set Basic Library…")
                    onTriggered: {
                        console.log("Set Basic Library triggered")
                        folderDialog.open()
                    }
                }
                
                MenuSeparator {}
                
                MenuItem {
                    text: qsTr("Export All Edited")
                    enabled: false
                }
                
                MenuItem {
                    text: qsTr("Export Selected")
                    enabled: false
                }
                
                MenuSeparator {}
                
                MenuItem {
                    text: qsTr("Rebuild Live Links")
                    enabled: isSidebarReady() && sidebarBridge.hasLibrary
                    onTriggered: {
                        console.log("Rebuild Live Links triggered")
                    }
                }
            }
            
            // Settings menu (defined outside button for proper popup behavior)
            Menu {
                id: settingsMenu
                
                MenuItem {
                    text: qsTr("Set Basic Library…")
                    onTriggered: {
                        console.log("Set Basic Library triggered from Settings")
                        folderDialog.open()
                    }
                }
                
                MenuSeparator {}
                
                MenuItem {
                    id: showFilmstripMenuItem
                    text: qsTr("Show Filmstrip")
                    checkable: true
                    checked: true
                    onTriggered: {
                        console.log("Show Filmstrip toggled:", checked)
                    }
                }
                
                MenuSeparator {}
                
                Menu {
                    title: qsTr("Appearance")
                    
                    MenuItem {
                        text: qsTr("System Default")
                        checkable: true
                        checked: true
                        autoExclusive: true
                    }
                    
                    MenuItem {
                        text: qsTr("Light Mode")
                        checkable: true
                        autoExclusive: true
                    }
                    
                    MenuItem {
                        text: qsTr("Dark Mode")
                        checkable: true
                        autoExclusive: true
                    }
                }
                
                Menu {
                    title: qsTr("Wheel Action")
                    
                    MenuItem {
                        text: qsTr("Navigate")
                        checkable: true
                        checked: true
                        autoExclusive: true
                    }
                    
                    MenuItem {
                        text: qsTr("Zoom")
                        checkable: true
                        autoExclusive: true
                    }
                }
                
                Menu {
                    title: qsTr("Share Action")
                    
                    MenuItem {
                        text: qsTr("Copy File")
                        checkable: true
                        autoExclusive: true
                    }
                    
                    MenuItem {
                        text: qsTr("Copy Path")
                        checkable: true
                        autoExclusive: true
                    }
                    
                    MenuItem {
                        text: qsTr("Reveal in File Manager")
                        checkable: true
                        checked: true
                        autoExclusive: true
                    }
                }
            }
            
            // Bottom separator
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: separatorColor
            }
        }
        
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
                        folderDialog.open()
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
