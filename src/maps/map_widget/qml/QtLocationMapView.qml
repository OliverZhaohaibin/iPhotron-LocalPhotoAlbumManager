import QtQuick
import QtLocation
import QtPositioning

Item {
    id: root
    objectName: "root"

    Rectangle {
        anchors.fill: parent
        color: "#dfe7ea"
    }

    Plugin {
        id: osmPlugin
        name: "osm"

        PluginParameter {
            name: "osm.mapping.highdpi_tiles"
            value: true
        }
    }

    Map {
        id: map
        objectName: "map"
        anchors.fill: parent
        plugin: osmPlugin
        center: QtPositioning.coordinate(0, 0)
        zoomLevel: 2
        minimumZoomLevel: 2
        maximumZoomLevel: 19
        copyrightsVisible: true
        enabled: false
    }
}

