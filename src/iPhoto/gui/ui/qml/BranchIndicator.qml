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

    onIndicatorColorChanged: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true

        // Rotate the canvas content based on angle
        rotation: root.angle

        Behavior on rotation {
            NumberAnimation {
                duration: 180
                easing.type: Easing.InOutQuad
            }
        }

        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();

            // Set line properties
            ctx.lineWidth = 2;
            ctx.strokeStyle = root.indicatorColor;
            ctx.lineCap = "round";
            ctx.lineJoin = "round";

            // Draw chevron pointing right (0 degrees)
            // Center is roughly 8, 8
            // Shape: >
            ctx.beginPath();
            ctx.moveTo(6, 4);
            ctx.lineTo(10, 8);
            ctx.lineTo(6, 12);
            ctx.stroke();
        }
    }
}
