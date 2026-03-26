#include "osmand_native_map_widget.h"

#include <algorithm>
#include <cstring>
#include <cwchar>

#include <QString>
#include <QWidget>

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
    return reinterpret_cast<OsmAndNativeMapWidget*>(widgetPointer);
}
}

extern "C"
{
__declspec(dllexport) void* osmand_create_map_widget(
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

    return widget;
}

__declspec(dllexport) double osmand_widget_get_zoom(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->zoomLevel();
    return 0.0;
}

__declspec(dllexport) double osmand_widget_get_min_zoom(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->minZoomLevel();
    return 0.0;
}

__declspec(dllexport) double osmand_widget_get_max_zoom(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->maxZoomLevel();
    return 0.0;
}

__declspec(dllexport) void osmand_widget_set_zoom(void* widgetPointer, double zoomLevel)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->setZoomLevel(zoomLevel);
}

__declspec(dllexport) void osmand_widget_reset_view(void* widgetPointer)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->resetView();
}

__declspec(dllexport) void osmand_widget_pan_by_pixels(void* widgetPointer, double deltaX, double deltaY)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->panByPixels(deltaX, deltaY);
}

__declspec(dllexport) void osmand_widget_set_center_lonlat(void* widgetPointer, double longitude, double latitude)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->setCenterLonLat(longitude, latitude);
}

__declspec(dllexport) void osmand_widget_get_center_lonlat(void* widgetPointer, double* longitude, double* latitude)
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

__declspec(dllexport) int osmand_widget_project_lonlat(
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


