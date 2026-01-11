pragma Singleton
import QtQuick 2.15

/**
 * Theme singleton providing consistent styling across the QML interface.
 * 
 * This module centralizes all themeable properties including colors, typography,
 * and spacing metrics. Components should reference Theme properties rather than
 * hardcoding values to enable consistent theming and potential dark/light mode support.
 */
QtObject {
    id: theme
    
    // ========================================================================
    // Theme Mode
    // ========================================================================
    property string mode: "dark"  // "light", "dark", "system"
    property bool isDark: mode === "dark"
    
    // ========================================================================
    // Primary Colors
    // ========================================================================
    property color background: isDark ? "#1E1E1E" : "#FFFFFF"
    property color surface: isDark ? "#2D2D30" : "#F5F5F5"
    property color sidebar: isDark ? "#252526" : "#E8E8E8"
    property color accent: "#007AFF"
    property color accentHover: Qt.darker(accent, 1.1)
    property color accentPressed: Qt.darker(accent, 1.2)
    
    // ========================================================================
    // Text Colors
    // ========================================================================
    property color text: isDark ? "#CCCCCC" : "#1E1E1E"
    property color textSecondary: isDark ? "#8A8A8A" : "#666666"
    property color textDisabled: isDark ? "#5A5A5A" : "#AAAAAA"
    property color textInverse: isDark ? "#1E1E1E" : "#FFFFFF"
    
    // ========================================================================
    // Status Bar
    // ========================================================================
    property color statusBarBackground: isDark ? "#2D2D30" : "#F0F0F0"
    property color statusBarText: textSecondary
    property int statusBarHeight: 24
    
    // ========================================================================
    // Title Bar
    // ========================================================================
    property color titleBarBackground: isDark ? "#323232" : "#FFFFFF"
    property int titleBarHeight: 36
    
    // ========================================================================
    // Sidebar
    // ========================================================================
    property color sidebarBackground: sidebar
    property color sidebarSelected: isDark ? "#094771" : "#CCE4FF"
    property color sidebarHover: isDark ? "#3E3E42" : "#E5E5E5"
    property color sidebarText: text
    property color sidebarTextSelected: isDark ? "#FFFFFF" : "#000000"
    property int sidebarWidth: 240
    property int sidebarMinWidth: 180
    property int sidebarMaxWidth: 400
    
    // ========================================================================
    // Gallery Grid
    // ========================================================================
    property color gridBackground: isDark ? "#2B2B2B" : "#F0F0F0"
    property color gridItemBackground: isDark ? "#1E1E1E" : "#FFFFFF"
    property color gridSelectionBorder: accent
    property color gridCurrentBorder: "#FFFFFF"
    property int gridMinItemWidth: 192
    property int gridItemGap: 2
    
    // ========================================================================
    // Detail View
    // ========================================================================
    property color viewerBackground: isDark ? "#1E1E1E" : "#F5F5F5"
    property color viewerSurface: isDark ? "#2B2B2B" : "#FFFFFF"
    property color headerSeparator: isDark ? "#3E3E42" : "#E0E0E0"
    
    // ========================================================================
    // Edit Mode
    // ========================================================================
    property color editDoneButtonBackground: "#0A84FF"
    property color editDoneButtonBackgroundHover: "#007AFF"
    property color editDoneButtonBackgroundPressed: "#0063CC"
    property color editDoneButtonBackgroundDisabled: "#4D4D4D"
    property color editDoneButtonText: "#FFFFFF"
    property color editDoneButtonTextDisabled: "#808080"
    
    // ========================================================================
    // Controls
    // ========================================================================
    property color buttonBackground: isDark ? "#3E3E42" : "#E5E5E5"
    property color buttonHover: isDark ? "#4E4E52" : "#D5D5D5"
    property color buttonPressed: isDark ? "#2E2E32" : "#C5C5C5"
    property color buttonText: text
    
    property color sliderTrack: isDark ? "#3E3E42" : "#D0D0D0"
    property color sliderHandle: accent
    property color sliderFill: accent
    
    property color scrollbarTrack: "transparent"
    property color scrollbarHandle: isDark ? Qt.rgba(1, 1, 1, 0.3) : Qt.rgba(0, 0, 0, 0.3)
    property color scrollbarHandleHover: isDark ? Qt.rgba(1, 1, 1, 0.5) : Qt.rgba(0, 0, 0, 0.5)
    
    // ========================================================================
    // Dialog
    // ========================================================================
    property color dialogBackground: isDark ? "#2D2D30" : "#FFFFFF"
    property color dialogBorder: isDark ? "#3E3E42" : "#E0E0E0"
    property color dialogOverlay: Qt.rgba(0, 0, 0, 0.5)
    
    // ========================================================================
    // Badges and Indicators
    // ========================================================================
    property color liveBadgeBackground: "#FFD700"
    property color videoBadgeBackground: Qt.rgba(0, 0, 0, 0.5)
    property color badgeText: "#000000"
    
    // ========================================================================
    // Typography
    // ========================================================================
    property font titleFont: Qt.font({ 
        family: Qt.platform.os === "osx" ? "SF Pro Display" : "Segoe UI", 
        pixelSize: 16, 
        weight: Font.Bold 
    })
    property font bodyFont: Qt.font({ 
        family: Qt.platform.os === "osx" ? "SF Pro Text" : "Segoe UI", 
        pixelSize: 14 
    })
    property font smallFont: Qt.font({ 
        family: Qt.platform.os === "osx" ? "SF Pro Text" : "Segoe UI", 
        pixelSize: 12 
    })
    property font captionFont: Qt.font({ 
        family: Qt.platform.os === "osx" ? "SF Pro Text" : "Segoe UI", 
        pixelSize: 10 
    })
    
    // ========================================================================
    // Spacing & Metrics
    // ========================================================================
    property int spacingTiny: 2
    property int spacingSmall: 4
    property int spacingMedium: 8
    property int spacingLarge: 12
    property int spacingXLarge: 16
    property int spacingXXLarge: 24
    
    property int borderRadius: 4
    property int borderRadiusLarge: 8
    property int borderWidth: 1
    
    property int headerHeight: 48
    property int controlHeight: 32
    property int buttonHeight: 30
    property int iconSize: 20
    property int iconSizeSmall: 16
    property int iconSizeLarge: 24
    
    // ========================================================================
    // Animation Durations
    // ========================================================================
    property int animationFast: 100
    property int animationNormal: 200
    property int animationSlow: 300
    
    // ========================================================================
    // Z-Indices
    // ========================================================================
    property int zBackground: -100
    property int zContent: 0
    property int zOverlay: 100
    property int zDialog: 200
    property int zTooltip: 300
}
