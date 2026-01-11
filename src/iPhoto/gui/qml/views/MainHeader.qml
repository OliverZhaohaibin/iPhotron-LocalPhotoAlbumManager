import QtQuick 2.15
import QtQuick.Controls
import styles 1.0 as Styles

/**
 * Main header bar with menu and toolbar buttons.
 * 
 * Features:
 * - Menu bar (File, Settings)
 * - Toolbar buttons (Rescan, Select)
 * - Theme-aware styling
 */
Rectangle {
    id: root
    
    property bool selectionModeEnabled: false
    
    signal openAlbumRequested()
    signal bindLibraryRequested()
    signal rescanRequested()
    signal selectionModeToggled(bool enabled)
    signal themeChanged(string theme)
    
    implicitWidth: parent ? parent.width : 400
    implicitHeight: 36
    
    color: Styles.Theme.titleBarBackground
    
    Row {
        anchors.fill: parent
        anchors.leftMargin: Styles.Theme.spacingMedium
        anchors.rightMargin: Styles.Theme.spacingMedium
        spacing: Styles.Theme.spacingMedium
        
        // Menu bar placeholder (will be replaced with actual QML menus)
        MenuBar {
            id: menuBar
            anchors.verticalCenter: parent.verticalCenter
            contentItem: Row {
                spacing: 0
                Repeater {
                    model: menuBar.menus
                    MenuBarItem { menu: modelData }
                }
            }

            Menu {
                title: qsTr("&File")
                MenuItem {
                    text: qsTr("Open Album Folder…")
                    onTriggered: root.openAlbumRequested()
                }
                MenuSeparator {}
                MenuItem {
                    text: qsTr("Set Basic Library…")
                    onTriggered: root.bindLibraryRequested()
                }
                MenuSeparator {}
                MenuItem { text: qsTr("Export All Edited") }
                MenuItem { text: qsTr("Export Selected") }
                MenuSeparator {}
                MenuItem { text: qsTr("Rebuild Live Links") }
            }

            Menu {
                title: qsTr("&Settings")
                MenuItem {
                    text: qsTr("Set Basic Library…")
                    onTriggered: root.bindLibraryRequested()
                }
                MenuSeparator {}
                MenuItem {
                    text: qsTr("Show Filmstrip")
                    checkable: true
                    checked: true
                }
                MenuSeparator {}

                Menu {
                    title: qsTr("Appearance")

                    MenuItem {
                        text: qsTr("System Default")
                        checkable: true
                        checked: true
                        onTriggered: root.themeChanged("system")
                    }
                    MenuItem {
                        text: qsTr("Light Mode")
                        checkable: true
                        onTriggered: root.themeChanged("light")
                    }
                    MenuItem {
                        text: qsTr("Dark Mode")
                        checkable: true
                        onTriggered: root.themeChanged("dark")
                    }
                }
            }
        }
        
        // Spacer
        Item {
            width: parent.width - menuBar.width - toolbarButtons.width - parent.spacing * 2
            height: parent.height
        }
        
        // Toolbar buttons
        Row {
            id: toolbarButtons
            anchors.verticalCenter: parent.verticalCenter
            spacing: Styles.Theme.spacingSmall
            
            // Rescan button
            ToolButton {
                text: qsTr("Rescan")
                onClicked: root.rescanRequested()
                
                background: Rectangle {
                    color: parent.pressed ? Styles.Theme.buttonPressed :
                           parent.hovered ? Styles.Theme.buttonHover : "transparent"
                    radius: Styles.Theme.borderRadius
                }
                
                contentItem: Text {
                    text: parent.text
                    font: Styles.Theme.bodyFont
                    color: Styles.Theme.text
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
            
            // Selection button
            ToolButton {
                text: qsTr("Select")
                checkable: true
                checked: root.selectionModeEnabled
                onCheckedChanged: root.selectionModeToggled(checked)
                
                background: Rectangle {
                    color: parent.checked ? Styles.Theme.sidebarSelected :
                           parent.pressed ? Styles.Theme.buttonPressed :
                           parent.hovered ? Styles.Theme.buttonHover : "transparent"
                    radius: Styles.Theme.borderRadius
                }
                
                contentItem: Text {
                    text: parent.text
                    font: Styles.Theme.bodyFont
                    color: parent.checked ? Styles.Theme.sidebarTextSelected : Styles.Theme.text
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }
}
