import QtQuick 2.15
import QtQuick.Controls 2.15
import "../styles"
import "../components"

/**
 * Edit sidebar containing adjustment controls organized in collapsible sections.
 * 
 * Features:
 * - Collapsible Light, Color, and B&W sections
 * - Slider controls for each adjustment parameter
 * - Reset functionality for individual parameters
 * - Live preview support via property bindings
 */
Rectangle {
    id: root
    
    // Edit session bindings
    property real brilliance: 0
    property real exposure: 0
    property real highlights: 0
    property real shadows: 0
    property real contrast: 0
    property real brightness: 0
    property real blackPoint: 0
    
    property real saturation: 0
    property real vibrance: 0
    property real warmth: 0
    property real tint: 0
    
    property real intensity: 0
    property real neutrals: 0
    property real tone: 0
    property real grain: 0
    
    // Signals for value changes
    signal brillianceChanged(real value)
    signal exposureChanged(real value)
    signal highlightsChanged(real value)
    signal shadowsChanged(real value)
    signal contrastChanged(real value)
    signal brightnessChanged(real value)
    signal blackPointChanged(real value)
    
    signal saturationChanged(real value)
    signal vibranceChanged(real value)
    signal warmthChanged(real value)
    signal tintChanged(real value)
    
    signal intensityChanged(real value)
    signal neutralsChanged(real value)
    signal toneChanged(real value)
    signal grainChanged(real value)
    
    implicitWidth: 280
    implicitHeight: parent ? parent.height : 600
    
    color: Theme.sidebarBackground
    
    // Left border
    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 1
        color: Theme.headerSeparator
    }
    
    Flickable {
        anchors.fill: parent
        anchors.leftMargin: 1
        contentHeight: sectionsColumn.height
        clip: true
        
        Column {
            id: sectionsColumn
            width: parent.width
            spacing: 0
            
            // Light Section
            CollapsibleSection {
                title: qsTr("Light")
                width: parent.width
                expanded: true
                
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    
                    SliderRow {
                        label: qsTr("Brilliance")
                        value: root.brilliance
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.brillianceChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Exposure")
                        value: root.exposure
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.exposureChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Highlights")
                        value: root.highlights
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.highlightsChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Shadows")
                        value: root.shadows
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.shadowsChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Contrast")
                        value: root.contrast
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.contrastChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Brightness")
                        value: root.brightness
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.brightnessChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Black Point")
                        value: root.blackPoint
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.blackPointChanged(v) }
                    }
                }
            }
            
            // Color Section
            CollapsibleSection {
                title: qsTr("Color")
                width: parent.width
                expanded: true
                
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    
                    SliderRow {
                        label: qsTr("Saturation")
                        value: root.saturation
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.saturationChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Vibrance")
                        value: root.vibrance
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.vibranceChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Warmth")
                        value: root.warmth
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.warmthChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Tint")
                        value: root.tint
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.tintChanged(v) }
                    }
                }
            }
            
            // Black & White Section
            CollapsibleSection {
                title: qsTr("Black & White")
                width: parent.width
                expanded: false
                
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    
                    SliderRow {
                        label: qsTr("Intensity")
                        value: root.intensity
                        from: 0
                        to: 100
                        defaultValue: 0
                        onValueChanged: function(v) { root.intensityChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Neutrals")
                        value: root.neutrals
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.neutralsChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Tone")
                        value: root.tone
                        from: -100
                        to: 100
                        onValueChanged: function(v) { root.toneChanged(v) }
                    }
                    
                    SliderRow {
                        label: qsTr("Grain")
                        value: root.grain
                        from: 0
                        to: 100
                        defaultValue: 0
                        onValueChanged: function(v) { root.grainChanged(v) }
                    }
                }
            }
        }
        
        ScrollBar.vertical: ScrollBar {
            width: 6
            
            contentItem: Rectangle {
                radius: 3
                color: Theme.scrollbarHandle
                opacity: parent.active ? 1.0 : 0.5
            }
        }
    }
    
    // Public methods for external control
    function setLightValues(brilliance, exposure, highlights, shadows, contrast, brightness, blackPoint) {
        root.brilliance = brilliance
        root.exposure = exposure
        root.highlights = highlights
        root.shadows = shadows
        root.contrast = contrast
        root.brightness = brightness
        root.blackPoint = blackPoint
    }
    
    function setColorValues(saturation, vibrance, warmth, tint) {
        root.saturation = saturation
        root.vibrance = vibrance
        root.warmth = warmth
        root.tint = tint
    }
    
    function setBWValues(intensity, neutrals, tone, grain) {
        root.intensity = intensity
        root.neutrals = neutrals
        root.tone = tone
        root.grain = grain
    }
    
    function resetAll() {
        root.brilliance = 0
        root.exposure = 0
        root.highlights = 0
        root.shadows = 0
        root.contrast = 0
        root.brightness = 0
        root.blackPoint = 0
        
        root.saturation = 0
        root.vibrance = 0
        root.warmth = 0
        root.tint = 0
        
        root.intensity = 0
        root.neutrals = 0
        root.tone = 0
        root.grain = 0
    }
}
