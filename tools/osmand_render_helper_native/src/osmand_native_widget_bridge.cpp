#include "osmand_native_map_widget.h"

#include <algorithm>
#include <cstring>
#include <cwchar>

#include <QString>
#include <QWidget>

#if defined(_WIN32)
#define OSMAND_EXPORT __declspec(dllexport)
#else
#define OSMAND_EXPORT __attribute__((visibility("default")))
#endif

namespace
{
void writeErrorMessage(const QString& message, wchar_t* buffer, int bufferCapacity)
{
    if (!buffer || bufferCapacity <= 0)
        return;

    const auto utf16 = message.toStdWString();
    const auto copyLength = std::min(static_cast<int>(utf16.size()), bufferCapacity - 1);
    if (copyLength > 0)
        std::wmemcpy(buffer, utf16.c_str(), copyLength);
    buffer[copyLength] = L'\0';
}

inline OsmAndNativeMapWidget* widgetFromPointer(void* widgetPointer)
{
#ifdef Q_OS_MACOS
    auto* host = static_cast<OsmAndNativeMapHost<OsmAndNativeMapWidget>*>(widgetPointer);
    return host ? host->surface : nullptr;
#else
    return static_cast<OsmAndNativeMapWidget*>(widgetPointer);
#endif
}
}

extern "C"
{
// No instance is needed: callers must check before attaching a GL widget.
// 0 = unknown, 1 = QOpenGLWidget, 2 = independent QOpenGLWindow.
OSMAND_EXPORT int osmand_widget_surface_kind()
{
#ifdef Q_OS_MACOS
    return 2;
#else
    return 1;
#endif
}

OSMAND_EXPORT void* osmand_create_map_widget(
    void* parentWidgetPointer,
    const wchar_t* obfPath,
    const wchar_t* resourcesRoot,
    const wchar_t* stylePath,
    int nightMode,
    wchar_t* errorBuffer,
    int errorBufferCapacity)
{
    const auto configuration = OsmAndNativeMapWidget::Configuration{
        QString::fromWCharArray(obfPath ? obfPath : L""),
        QString::fromWCharArray(resourcesRoot ? resourcesRoot : L""),
        QString::fromWCharArray(stylePath ? stylePath : L""),
        nightMode != 0,
    };

    QString errorMessage;
    auto* widget = OsmAndNativeMapWidget::create(
        configuration,
        reinterpret_cast<QWidget*>(parentWidgetPointer),
        errorMessage);
    if (!widget)
    {
        writeErrorMessage(errorMessage, errorBuffer, errorBufferCapacity);
        return nullptr;
    }

#ifdef Q_OS_MACOS
    return new OsmAndNativeMapHost<OsmAndNativeMapWidget>(
        widget, reinterpret_cast<QWidget*>(parentWidgetPointer));
#else
    return widget;
#endif
}

OSMAND_EXPORT void* osmand_create_map_widget_deferred(
    void* parentWidgetPointer,
    const wchar_t* obfPath,
    const wchar_t* resourcesRoot,
    const wchar_t* stylePath,
    int nightMode)
{
    const auto configuration = OsmAndNativeMapWidget::Configuration{
        QString::fromWCharArray(obfPath ? obfPath : L""),
        QString::fromWCharArray(resourcesRoot ? resourcesRoot : L""),
        QString::fromWCharArray(stylePath ? stylePath : L""),
        nightMode != 0,
    };
    auto* widget = OsmAndNativeMapWidget::createDeferred(
        configuration, reinterpret_cast<QWidget*>(parentWidgetPointer));
#ifdef Q_OS_MACOS
    return new OsmAndNativeMapHost<OsmAndNativeMapWidget>(
        widget, reinterpret_cast<QWidget*>(parentWidgetPointer));
#else
    return widget;
#endif
}

OSMAND_EXPORT int osmand_widget_initialize_resources(
    void* widgetPointer,
    wchar_t* errorBuffer,
    int errorBufferCapacity)
{
    auto* widget = widgetFromPointer(widgetPointer);
    if (!widget)
    {
        writeErrorMessage(
            QStringLiteral("Native OsmAnd widget is unavailable"),
            errorBuffer,
            errorBufferCapacity);
        return 0;
    }
    QString errorMessage;
    if (!widget->initializeResources(errorMessage))
    {
        writeErrorMessage(errorMessage, errorBuffer, errorBufferCapacity);
        return 0;
    }
    return 1;
}

OSMAND_EXPORT double osmand_widget_get_zoom(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->zoomLevel();
    return 0.0;
}

OSMAND_EXPORT double osmand_widget_get_min_zoom(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->minZoomLevel();
    return 0.0;
}

OSMAND_EXPORT double osmand_widget_get_max_zoom(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->maxZoomLevel();
    return 0.0;
}

OSMAND_EXPORT int osmand_widget_has_presented_frame(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->hasPresentedFrame() ? 1 : 0;
    return 0;
}

OSMAND_EXPORT void osmand_widget_set_zoom(void* widgetPointer, double zoomLevel)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->setZoomLevel(zoomLevel);
}

OSMAND_EXPORT void osmand_widget_reset_view(void* widgetPointer)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->resetView();
}

OSMAND_EXPORT void osmand_widget_cleanup(void* widgetPointer)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->shutdown();
}

OSMAND_EXPORT void* osmand_widget_get_event_target(void* widgetPointer)
{
    // Return the renderer, never the host's incidental native window handle.
    return widgetFromPointer(widgetPointer);
}

OSMAND_EXPORT void osmand_widget_pan_by_pixels(void* widgetPointer, double deltaX, double deltaY)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->panByPixels(deltaX, deltaY);
}

OSMAND_EXPORT void osmand_widget_set_center_lonlat(void* widgetPointer, double longitude, double latitude)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->setCenterLonLat(longitude, latitude);
}

OSMAND_EXPORT void osmand_widget_get_center_lonlat(void* widgetPointer, double* longitude, double* latitude)
{
    if (!longitude || !latitude)
        return;

    if (const auto* widget = widgetFromPointer(widgetPointer))
    {
        const auto center = widget->centerLonLat();
        *longitude = center.x();
        *latitude = center.y();
        return;
    }

    *longitude = 0.0;
    *latitude = 0.0;
}

OSMAND_EXPORT int osmand_widget_project_lonlat(
    void* widgetPointer,
    double longitude,
    double latitude,
    double* screenX,
    double* screenY)
{
    if (!screenX || !screenY)
        return 0;

    if (const auto* widget = widgetFromPointer(widgetPointer))
    {
        QPointF screenPoint;
        if (widget->projectLonLat(longitude, latitude, screenPoint))
        {
            *screenX = screenPoint.x();
            *screenY = screenPoint.y();
            return 1;
        }
    }

    *screenX = 0.0;
    *screenY = 0.0;
    return 0;
}
}
