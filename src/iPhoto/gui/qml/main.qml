import QtQuick 2.15
import QtQuick.Controls
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import styles 1.0 as Styles
import components 1.0 as Components
import views 1.0 as Views

/**
 * Main application window for iPhoto.
 * 
 * This is the root QML component that orchestrates the entire UI.
 * It provides:
 * - Frameless window with custom title bar
 * - Navigation sidebar with album tree
 * - Main content area with view switching
 * - Status bar with progress indicators
 * 
 * The window connects to Python controllers via context properties:
 * - albumController: Album navigation and selection
 * - assetController: Asset list and thumbnail loading
 * - editController: Edit session management
 * - themeController: Theme switching
 */
ApplicationWindow {
    id: mainWindow
    
    visible: true
    width: 1200
    height: 720
    minimumWidth: 800
    minimumHeight: 600
    title: "iPhoto"
    
    // Frameless window flags (optional, can be enabled for custom chrome)
    // flags: Qt.FramelessWindowHint | Qt.Window
    
    // Apply theme colors
    color: Styles.Theme.background
    Component.onCompleted: {
        if (themeController) {
            Styles.Theme.mode = themeController.mode
        }
        // Restore window geometry if settings available
        if (typeof settings !== "undefined") {
            var geometry = settings.get("ui.windowGeometry")
            if (geometry) {
                x = geometry.x
                y = geometry.y
                width = geometry.width
                height = geometry.height
            }
        }
    }
    Connections {
        target: themeController
        function onModeChanged(mode) {
            Styles.Theme.mode = mode
        }
    }
    
    // ========================================================================
    // Window Chrome
    // ========================================================================
    
    header: Column {
        width: parent.width
        
        // Title bar
        Views.TitleBar {
            id: titleBar
            width: parent.width
            windowTitle: mainWindow.title
            isMaximized: mainWindow.visibility === Window.Maximized
            
            onMinimizeClicked: mainWindow.showMinimized()
            onFullscreenClicked: {
                if (mainWindow.visibility === Window.Maximized) {
                    mainWindow.showNormal()
                } else {
                    mainWindow.showMaximized()
                }
            }
            onCloseClicked: mainWindow.close()
            
            // Window dragging
            onDragStarted: function(pos) {
                mainWindow.startSystemMove()
            }
        }
        
        // Main header with menu
        Views.MainHeader {
            id: mainHeader
            width: parent.width
            
            onOpenAlbumRequested: {
                if (typeof dialogController !== "undefined") {
                    dialogController.openAlbumDialog()
                }
            }

            onBindLibraryRequested: {
                if (typeof dialogController !== "undefined") {
                    dialogController.bindLibraryDialog()
                }
            }
            
            onRescanRequested: {
                if (typeof navigationController !== "undefined") {
                    navigationController.rescanCurrent()
                } else if (typeof facade !== "undefined") {
                    facade.rescan_current_async()
                }
            }
            
            onSelectionModeToggled: function(enabled) {
                galleryView.selectionMode = enabled
            }
            
            onThemeChanged: function(theme) {
                if (themeController) {
                    themeController.setMode(theme)
                } else {
                    Styles.Theme.mode = theme
                }
            }
        }
    }
    
    // ========================================================================
    // Main Content
    // ========================================================================
    
    SplitView {
        id: mainSplit
        anchors.fill: parent
        orientation: Qt.Horizontal
        
        // Album Sidebar
        Views.AlbumSidebar {
            id: albumSidebar
            SplitView.minimumWidth: Styles.Theme.sidebarMinWidth
            SplitView.preferredWidth: Styles.Theme.sidebarWidth
            SplitView.maximumWidth: Styles.Theme.sidebarMaxWidth
            
            // Connect to controller if available
            model: (typeof albumController !== "undefined" && albumController) ? albumController.model : null
            
            onAlbumSelected: function(path) {
                if (typeof navigationController !== "undefined" && navigationController) {
                    navigationController.openAlbum(path)
                }
                viewStack.currentIndex = 0  // Show gallery
            }
            
            onAllPhotosSelected: {
                if (typeof navigationController !== "undefined" && navigationController) {
                    navigationController.openAllPhotos()
                }
                viewStack.currentIndex = 0
            }
            
            onStaticNodeSelected: function(title) {
                if (title === "Location") {
                    viewStack.currentIndex = 1  // Show map
                } else if (title === "Albums") {
                    viewStack.currentIndex = 3  // Show albums dashboard
                } else {
                    if (typeof navigationController !== "undefined" && navigationController) {
                        navigationController.openStaticNode(title)
                    }
                    viewStack.currentIndex = 0
                }
            }
            
            onBindLibraryRequested: {
                if (typeof dialogController !== "undefined") {
                    dialogController.bindLibraryDialog()
                }
            }
            
            onFilesDropped: function(targetPath, urls) {
                if (typeof importService !== "undefined") {
                    importService.importFiles(targetPath, urls)
                }
            }
        }
        
        // Content Area
        Item {
            SplitView.fillWidth: true
            
            Rectangle {
                anchors.fill: parent
                anchors.margins: 8
                color: Styles.Theme.surface
                radius: 4
                
                StackLayout {
                    id: viewStack
                    anchors.fill: parent
                    currentIndex: 0
                    
                    // Gallery View (index 0)
                    Views.GalleryView {
                        id: galleryView
                        
                        // Connect to controller if available
                        model: (typeof assetController !== "undefined" && assetController) ? assetController.model : null
                        
                        onItemClicked: function(index, modifiers) {
                            if (typeof selectionController !== "undefined" && selectionController) {
                                selectionController.handleItemClick(index, modifiers)
                            }
                        }
                        
                        onItemDoubleClicked: function(index) {
                            if (typeof playbackController !== "undefined") {
                                playbackController.activateIndex(index)
                            }
                            viewStack.currentIndex = 2  // Show detail view
                        }
                        
                        onShowContextMenu: function(index, globalX, globalY) {
                            if (typeof contextMenuController !== "undefined") {
                                contextMenuController.showMenu(index, globalX, globalY)
                            }
                        }
                        
                        onFilesDropped: function(urls) {
                            if (typeof importService !== "undefined") {
                                importService.importFiles(albumSidebar.currentSelection, urls)
                            }
                        }
                    }
                    
                    // Map View (index 1)
                    Rectangle {
                        id: mapViewPlaceholder
                        color: Styles.Theme.viewerBackground
                        
                        Text {
                            anchors.centerIn: parent
                            text: qsTr("Map View")
                            font: Styles.Theme.titleFont
                            color: Styles.Theme.textSecondary
                        }
                        
                        // In full implementation, this would load the QQuickFramebufferObject
                        // based MapView component
                    }
                    
                    // Detail View (index 2)
                    Views.DetailView {
                        id: detailView
                        
                        onBackClicked: {
                            viewStack.currentIndex = 0  // Return to gallery
                        }
                        
                        onEditClicked: {
                            detailView.editMode = true
                            // Show edit sidebar
                            editSidebar.visible = true
                        }
                        
                        onZoomSliderChanged: function(value) {
                            // Forward to image viewer
                        }
                    }
                    
                    // Albums Dashboard (index 3)
                    Rectangle {
                        id: albumsDashboard
                        color: Styles.Theme.viewerBackground
                        
                        Text {
                            anchors.centerIn: parent
                            text: qsTr("Albums Dashboard")
                            font: Styles.Theme.titleFont
                            color: Styles.Theme.textSecondary
                        }
                    }
                }
            }
        }
        
        // Edit Sidebar (shown only in edit mode)
        Views.EditSidebar {
            id: editSidebar
            visible: false
            SplitView.minimumWidth: 240
            SplitView.preferredWidth: 280
            SplitView.maximumWidth: 350
            
            // Connect to edit session if available
            // Note: We use explicit Qt.binding() here because editSession is a
            // context property that may not be defined at parse time. This pattern
            // ensures bindings are established dynamically after the controller
            // is registered. A more declarative approach would require the controller
            // to always exist, which isn't guaranteed during development/testing.
            Component.onCompleted: {
                if (typeof editSession !== "undefined" && editSession) {
                    brilliance = Qt.binding(function() { return editSession ? editSession.brilliance : 0 })
                    exposure = Qt.binding(function() { return editSession ? editSession.exposure : 0 })
                    highlights = Qt.binding(function() { return editSession ? editSession.highlights : 0 })
                    shadows = Qt.binding(function() { return editSession ? editSession.shadows : 0 })
                    contrast = Qt.binding(function() { return editSession ? editSession.contrast : 0 })
                    brightness = Qt.binding(function() { return editSession ? editSession.brightness : 0 })
                    blackPoint = Qt.binding(function() { return editSession ? editSession.blackPoint : 0 })
                    
                    saturation = Qt.binding(function() { return editSession ? editSession.saturation : 0 })
                    vibrance = Qt.binding(function() { return editSession ? editSession.vibrance : 0 })
                    warmth = Qt.binding(function() { return editSession ? editSession.warmth : 0 })
                    tint = Qt.binding(function() { return editSession ? editSession.tint : 0 })
                }
            }
            
            onBrillianceModified: function(v) {
                if (typeof editSession !== "undefined") editSession.brilliance = v
            }
            onExposureModified: function(v) {
                if (typeof editSession !== "undefined") editSession.exposure = v
            }
            onHighlightsModified: function(v) {
                if (typeof editSession !== "undefined") editSession.highlights = v
            }
            onShadowsModified: function(v) {
                if (typeof editSession !== "undefined") editSession.shadows = v
            }
            onContrastModified: function(v) {
                if (typeof editSession !== "undefined") editSession.contrast = v
            }
            onBrightnessModified: function(v) {
                if (typeof editSession !== "undefined") editSession.brightness = v
            }
            onBlackPointModified: function(v) {
                if (typeof editSession !== "undefined") editSession.blackPoint = v
            }
            
            onSaturationModified: function(v) {
                if (typeof editSession !== "undefined") editSession.saturation = v
            }
            onVibranceModified: function(v) {
                if (typeof editSession !== "undefined") editSession.vibrance = v
            }
            onWarmthModified: function(v) {
                if (typeof editSession !== "undefined") editSession.warmth = v
            }
            onTintModified: function(v) {
                if (typeof editSession !== "undefined") editSession.tint = v
            }
        }
    }
    
    // ========================================================================
    // Status Bar
    // ========================================================================
    
    footer: Views.StatusBar {
        id: statusBar
        
        // Connect to status controller if available
        itemCount: (typeof assetController !== "undefined" && assetController) ? assetController.totalCount : 0
        
        Component.onCompleted: {
            if (typeof statusController !== "undefined" && statusController) {
                statusController.messageChanged.connect(function(msg, timeout) {
                    statusBar.showMessage(msg, timeout)
                })
                statusController.progressChanged.connect(function(value) {
                    if (value < 0) {
                        statusBar.showIndeterminateProgress()
                    } else if (value >= 100) {
                        statusBar.hideProgress()
                    } else {
                        statusBar.showProgress(0, 100, value)
                    }
                })
            }
        }
    }
    
    // ========================================================================
    // Keyboard Shortcuts
    // ========================================================================
    
    Shortcut {
        sequence: "Escape"
        onActivated: {
            if (editSidebar.visible) {
                editSidebar.visible = false
                detailView.editMode = false
            } else if (viewStack.currentIndex !== 0) {
                viewStack.currentIndex = 0
            }
        }
    }
    
    Shortcut {
        sequence: StandardKey.Quit
        onActivated: mainWindow.close()
    }
    
    Shortcut {
        sequence: "Space"
        onActivated: {
            if (typeof playbackController !== "undefined") {
                playbackController.togglePlayback()
            }
        }
    }
    
    Shortcut {
        sequence: "Left"
        onActivated: {
            if (viewStack.currentIndex === 2) {  // Detail view
                if (typeof playbackController !== "undefined") {
                    playbackController.requestPreviousItem()
                }
            }
        }
    }
    
    Shortcut {
        sequence: "Right"
        onActivated: {
            if (viewStack.currentIndex === 2) {  // Detail view
                if (typeof playbackController !== "undefined") {
                    playbackController.requestNextItem()
                }
            }
        }
    }
    
    // ========================================================================
    // Window State Persistence
    // ========================================================================
    onClosing: function(close) {
        // Save window geometry
        if (typeof settings !== "undefined") {
            // Manually construct a clean object to avoid QJSValue serialization issues in Python
            var geometry = {
                "x": Number(x),
                "y": Number(y),
                "width": Number(width),
                "height": Number(height)
            }
            settings.set("ui.windowGeometry", geometry)
        }
        close.accepted = true
    }
}
