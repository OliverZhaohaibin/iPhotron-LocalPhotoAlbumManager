import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import "."  // Import local QML files

ApplicationWindow {
    id: mainWindow
    visible: true
    width: 1200
    height: 720
    title: "iPhoto"
    
    // Color palette matching the widget implementation
    readonly property color sidebarBackground: "#f5f5f5"
    readonly property color sidebarSelectedBackground: "#e0e0e0"
    readonly property color sidebarTextColor: "#2b2b2b"
    readonly property color sidebarIconColor: "#007AFF"
    readonly property color separatorColor: "#d0d0d0"
    
    SplitView {
        anchors.fill: parent
        orientation: Qt.Horizontal
        
        // Sidebar
        Sidebar {
            id: sidebar
            SplitView.preferredWidth: 200
            SplitView.minimumWidth: 150
            SplitView.maximumWidth: 350
        }
        
        // Main content area - Gallery View
        GalleryView {
            id: galleryView
            SplitView.fillWidth: true
        }
    }
    
    // Helper function to get album name from path
    function getAlbumNameFromPath(path) {
        if (!path || path.length === 0) return "Unknown Album"
        // Handle both Windows and Unix paths
        var parts = path.replace(/\\/g, "/").split("/")
        // Filter out empty parts and return the last non-empty part
        for (var i = parts.length - 1; i >= 0; i--) {
            if (parts[i] && parts[i].length > 0) {
                return parts[i]
            }
        }
        return "Unknown Album"
    }
    
    // Handle album selection
    Connections {
        target: sidebarBridge
        
        function onAlbumSelected(path) {
            galleryView.currentTitle = getAlbumNameFromPath(path)
        }
        
        function onAllPhotosSelected() {
            galleryView.currentTitle = "All Photos"
        }
        
        function onStaticNodeSelected(title) {
            galleryView.currentTitle = title
        }
        
        function onBindLibraryRequested() {
            // In a full implementation, this would open a folder dialog
            console.log("Library binding requested")
        }
    }
}
