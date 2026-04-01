#pragma once

#include <OsmAndCore/QtExtensions.h>

#include <memory>

#include <QOpenGLWidget>
#include <QPointF>
#include <QString>
#include <QTimer>

namespace OsmAnd
{
    class IMapRenderer;
    class ObfsCollection;
    class MapStylesCollection;
    class MapPresentationEnvironment;
    class MapPrimitiviser;
    class ObfMapObjectsProvider;
    class MapPrimitivesProvider;
    class MapObjectsSymbolsProvider;
    class MapRasterLayerProvider_Software;
}

class OsmAndNativeMapWidget final : public QOpenGLWidget
{
public:
    struct Configuration
    {
        QString obfPath;
        QString resourcesRoot;
        QString stylePath;
        bool nightMode = false;
    };

    static OsmAndNativeMapWidget* create(const Configuration& configuration, QWidget* parent, QString& errorMessage);

    ~OsmAndNativeMapWidget() override;

    double zoomLevel() const;
    double minZoomLevel() const;
    double maxZoomLevel() const;
    void setZoomLevel(double zoomLevel);
    void resetView();
    void panByPixels(double deltaX, double deltaY);
    void setCenterLonLat(double longitude, double latitude);
    QPointF centerLonLat() const;
    bool projectLonLat(double longitude, double latitude, QPointF& outScreenPoint) const;

protected:
    void initializeGL() override;
    void resizeGL(int width, int height) override;
    void paintGL() override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void wheelEvent(QWheelEvent* event) override;

private:
    explicit OsmAndNativeMapWidget(const Configuration& configuration, QWidget* parent = nullptr);

    bool initializeResources(QString& errorMessage);
    bool ensureRenderer();
    void maybeDumpCaptionDiagnostics();
    void syncRendererViewport(bool forcedUpdate = false);
    void syncRendererCamera(bool forcedUpdate = false);
    void beginInteractiveRendering();
    void scheduleInteractiveRenderingFinish();
    void finishInteractiveRendering();
    void cleanupRenderer();
    void wrapCenter();
    double worldSize() const;

    static QPointF lonLatToNormalized(double longitude, double latitude);
    static QPointF normalizedToLonLat(double normalizedX, double normalizedY);

    Configuration _configuration;
    QString _styleName;
    QString _locale;
    QString _initError;
    bool _resourcesReady = false;
    bool _dragging = false;
    QPointF _lastMousePosition;
    double _centerX = 0.5;
    double _centerY = 0.5;
    double _zoomLevel = 2.0;
    double _minZoomLevel = 2.0;
    double _maxZoomLevel = 19.0;
    double _defaultZoomLevel = 2.0;
    QTimer _interactionTimer;
    bool _interactiveRendering = false;
    bool _symbolsSuspendedByInteraction = false;
    bool _captionDiagnosticsDumped = false;

    std::shared_ptr<OsmAnd::MapStylesCollection> _stylesCollection;
    std::shared_ptr<OsmAnd::ObfsCollection> _obfsCollection;
    std::shared_ptr<OsmAnd::MapPresentationEnvironment> _mapPresentationEnvironment;
    std::shared_ptr<OsmAnd::MapPrimitiviser> _primitiviser;
    std::shared_ptr<OsmAnd::ObfMapObjectsProvider> _mapObjectsProvider;
    std::shared_ptr<OsmAnd::MapPrimitivesProvider> _mapPrimitivesProvider;
    std::shared_ptr<OsmAnd::MapObjectsSymbolsProvider> _mapSymbolsProvider;
    std::shared_ptr<OsmAnd::MapRasterLayerProvider_Software> _mapRasterLayerProvider;
    std::shared_ptr<OsmAnd::IMapRenderer> _mapRenderer;
};
