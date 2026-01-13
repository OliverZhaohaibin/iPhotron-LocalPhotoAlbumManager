import QtQuick 2.15

Item {
    id: root
    width: 16
    height: 16

    /*!
        Angle of the indicator in degrees.
        0 degrees renders a ">" chevron, 90 degrees renders a "v" chevron.
    */
    property real angle: 0

    /*! Color of the indicator stroke. */
    property color indicatorColor: "#2b2b2b"

    antialiasing: true

    Text {
        anchors.centerIn: parent
        text: ">"
        font.pixelSize: 14
        font.bold: true
        color: root.indicatorColor

        transform: Rotation {
            origin.x: width / 2
            origin.y: height / 2
            angle: root.angle
        }
    }
}
