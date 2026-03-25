#include "osmand_native_map_widget.h"

#include "file_system_core_resources_provider.h"

#include <algorithm>
#include <cmath>
#include <memory>
#include <mutex>

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QLocale>
#include <QMetaObject>
#include <QMouseEvent>
#include <QOpenGLContext>
#include <QOpenGLFunctions>
#include <QPointer>
#include <QStandardPaths>
#include <QWheelEvent>

#include <OsmAndCore.h>
#include <OsmAndCore/Logging.h>
#include <OsmAndCore/ObfsCollection.h>
#include <OsmAndCore/Utilities.h>
#include <OsmAndCore/Map/AtlasMapRendererConfiguration.h>
#include <OsmAndCore/Map/IMapRenderer.h>
#include <OsmAndCore/Map/MapObjectsSymbolsProvider.h>
#include <OsmAndCore/Map/MapPresentationEnvironment.h>
#include <OsmAndCore/Map/MapPrimitivesProvider.h>
#include <OsmAndCore/Map/MapPrimitiviser.h>
#include <OsmAndCore/Map/MapRasterLayerProvider_Software.h>
#include <OsmAndCore/Map/MapStylesCollection.h>
#include <OsmAndCore/Map/ObfMapObjectsProvider.h>

namespace
{
constexpr int kReferenceTileSize = 256;
constexpr double kMercatorLatBound = 85.05112878;
constexpr double kDefaultMinZoom = 2.0;
constexpr double kDefaultMaxZoom = 19.0;
constexpr double kDefaultZoom = 2.0;
constexpr float kDefaultFieldOfView = 16.5f;
constexpr float kDefaultElevationAngle = 90.0f;
constexpr double kPi = 3.14159265358979323846;
constexpr int kConcurrentObfReadLimit = 0;

class CoreRuntime
{
public:
    static CoreRuntime& instance()
    {
        static CoreRuntime runtime;
        return runtime;
    }

    bool acquire(const QString& resourcesRoot, QString& errorMessage)
    {
        std::lock_guard<std::mutex> lock(_mutex);

        if (_refCount > 0)
        {
            if (_resourcesRoot != resourcesRoot)
            {
                errorMessage = QStringLiteral("OsmAnd core is already initialized with a different resources root");
                return false;
            }
            ++_refCount;
            return true;
        }

        const auto provider = std::make_shared<FileSystemCoreResourcesProvider>(resourcesRoot);
        if (!provider->containsResource(QStringLiteral("map/styles/default.render.xml")))
        {
            errorMessage = QStringLiteral("default.render.xml was not found in the mapped OsmAnd resources");
            return false;
        }

        const auto fontsRoot = QDir(resourcesRoot).filePath(QStringLiteral("rendering_styles/fonts"));
        const auto fontsRootUtf8 = QFile::encodeName(QDir::toNativeSeparators(fontsRoot));
        const auto bitness = OsmAnd::InitializeCore(provider, fontsRootUtf8.constData());
        if (bitness == 0)
        {
            errorMessage = QStringLiteral("OsmAnd::InitializeCore failed");
            return false;
        }

        _provider = provider;
        _resourcesRoot = resourcesRoot;
        _refCount = 1;
        return true;
    }

    void release()
    {
        std::lock_guard<std::mutex> lock(_mutex);
        if (_refCount <= 0)
            return;

        --_refCount;
        if (_refCount == 0)
        {
            _provider.reset();
            _resourcesRoot.clear();
            OsmAnd::ReleaseCore();
        }
    }

private:
    std::mutex _mutex;
    int _refCount = 0;
    QString _resourcesRoot;
    std::shared_ptr<const FileSystemCoreResourcesProvider> _provider;
};

inline double clampLatitude(double latitude)
{
    return std::clamp(latitude, -kMercatorLatBound, kMercatorLatBound);
}

QString openGlShadersCachePath()
{
    const auto baseCachePath = QStandardPaths::writableLocation(QStandardPaths::CacheLocation);
    if (baseCachePath.isEmpty())
        return QString();

    const auto cachePath = QDir(baseCachePath).filePath(QStringLiteral("maps/osmand_gl_shaders"));
    QDir().mkpath(cachePath);
    return cachePath;
}
}

OsmAndNativeMapWidget* OsmAndNativeMapWidget::create(
    const Configuration& configuration,
    QWidget* parent,
    QString& errorMessage)
{
    auto* widget = new OsmAndNativeMapWidget(configuration, parent);
    if (!widget->initializeResources(errorMessage))
    {
        delete widget;
        return nullptr;
    }
    return widget;
}

OsmAndNativeMapWidget::OsmAndNativeMapWidget(const Configuration& configuration, QWidget* parent)
    : QOpenGLWidget(parent)
    , _configuration(configuration)
{
    setUpdateBehavior(QOpenGLWidget::NoPartialUpdate);
    setMouseTracking(true);
    setFocusPolicy(Qt::StrongFocus);
    setMinimumSize(640, 480);
}

OsmAndNativeMapWidget::~OsmAndNativeMapWidget()
{
    cleanupRenderer();
    if (_resourcesReady)
        CoreRuntime::instance().release();
}

double OsmAndNativeMapWidget::zoomLevel() const
{
    return _zoomLevel;
}

double OsmAndNativeMapWidget::minZoomLevel() const
{
    return _minZoomLevel;
}

double OsmAndNativeMapWidget::maxZoomLevel() const
{
    return _maxZoomLevel;
}

void OsmAndNativeMapWidget::setZoomLevel(double zoomLevel)
{
    const auto clampedZoom = std::clamp(zoomLevel, _minZoomLevel, _maxZoomLevel);
    if (std::abs(clampedZoom - _zoomLevel) <= 1e-6)
        return;

    _zoomLevel = clampedZoom;
    wrapCenter();
    syncRendererCamera(true);
    update();
}

void OsmAndNativeMapWidget::resetView()
{
    _centerX = 0.5;
    _centerY = 0.5;
    _zoomLevel = _defaultZoomLevel;
    wrapCenter();
    syncRendererCamera(true);
    update();
}

void OsmAndNativeMapWidget::panByPixels(double deltaX, double deltaY)
{
    const auto currentWorldSize = worldSize();
    if (currentWorldSize <= 0.0)
        return;

    _centerX -= deltaX / currentWorldSize;
    _centerY -= deltaY / currentWorldSize;
    wrapCenter();
    syncRendererCamera(true);
    update();
}

void OsmAndNativeMapWidget::setCenterLonLat(double longitude, double latitude)
{
    const auto normalized = lonLatToNormalized(longitude, latitude);
    _centerX = normalized.x();
    _centerY = normalized.y();
    wrapCenter();
    syncRendererCamera(true);
    update();
}

QPointF OsmAndNativeMapWidget::centerLonLat() const
{
    return normalizedToLonLat(_centerX, _centerY);
}

void OsmAndNativeMapWidget::initializeGL()
{
    connect(context(), &QOpenGLContext::aboutToBeDestroyed, this, &OsmAndNativeMapWidget::cleanupRenderer, Qt::DirectConnection);
    if (!ensureRenderer() && _initError.isEmpty())
        _initError = QStringLiteral("Failed to initialize the native OsmAnd renderer");
}

void OsmAndNativeMapWidget::resizeGL(int width, int height)
{
    Q_UNUSED(width)
    Q_UNUSED(height)
    syncRendererViewport(true);
    update();
}

void OsmAndNativeMapWidget::paintGL()
{
    if (!ensureRenderer())
    {
        if (auto* functions = context() ? context()->functions() : nullptr)
        {
            functions->glClearColor(0.53f, 0.65f, 0.76f, 1.0f);
            functions->glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        }
        return;
    }

    syncRendererViewport(false);
    syncRendererCamera(false);

    _mapRenderer->update();
    if (_mapRenderer->prepareFrame())
        _mapRenderer->renderFrame();

    if (!_mapRenderer->isIdle() || _mapRenderer->isFrameInvalidated())
        update();
}

void OsmAndNativeMapWidget::mousePressEvent(QMouseEvent* event)
{
    if (event->button() == Qt::LeftButton)
    {
        _dragging = true;
        _lastMousePosition = event->position();
        setCursor(Qt::ClosedHandCursor);
        setFocus(Qt::MouseFocusReason);
        event->accept();
        return;
    }

    QOpenGLWidget::mousePressEvent(event);
}

void OsmAndNativeMapWidget::mouseMoveEvent(QMouseEvent* event)
{
    if (_dragging && (event->buttons() & Qt::LeftButton))
    {
        const auto currentPosition = event->position();
        const auto delta = currentPosition - _lastMousePosition;
        _lastMousePosition = currentPosition;
        if (!delta.isNull())
            panByPixels(delta.x(), delta.y());
        event->accept();
        return;
    }

    QOpenGLWidget::mouseMoveEvent(event);
}

void OsmAndNativeMapWidget::mouseReleaseEvent(QMouseEvent* event)
{
    if (_dragging && event->button() == Qt::LeftButton)
    {
        _dragging = false;
        unsetCursor();
        event->accept();
        return;
    }

    QOpenGLWidget::mouseReleaseEvent(event);
}

void OsmAndNativeMapWidget::wheelEvent(QWheelEvent* event)
{
    const auto delta = event->angleDelta().y();
    if (delta == 0)
    {
        QOpenGLWidget::wheelEvent(event);
        return;
    }

    const auto zoomFactor = 1.0 + static_cast<double>(delta) / 1200.0;
    const auto nextZoom = std::clamp(_zoomLevel * zoomFactor, _minZoomLevel, _maxZoomLevel);
    if (std::abs(nextZoom - _zoomLevel) <= 1e-6)
    {
        event->accept();
        return;
    }

    const auto oldWorldSize = worldSize();
    const auto centerPixelX = _centerX * oldWorldSize;
    const auto centerPixelY = _centerY * oldWorldSize;
    const auto topLeftX = centerPixelX - width() / 2.0;
    const auto topLeftY = centerPixelY - height() / 2.0;

    const auto mouseWorldX = (topLeftX + event->position().x()) / oldWorldSize;
    const auto mouseWorldY = (topLeftY + event->position().y()) / oldWorldSize;

    _zoomLevel = nextZoom;
    const auto newWorldSize = worldSize();
    const auto newCenterPixelX = mouseWorldX * newWorldSize - event->position().x() + width() / 2.0;
    const auto newCenterPixelY = mouseWorldY * newWorldSize - event->position().y() + height() / 2.0;

    _centerX = newCenterPixelX / newWorldSize;
    _centerY = newCenterPixelY / newWorldSize;
    wrapCenter();
    syncRendererCamera(true);
    update();
    event->accept();
}

bool OsmAndNativeMapWidget::initializeResources(QString& errorMessage)
{
    if (!QFileInfo::exists(_configuration.obfPath))
    {
        errorMessage = QStringLiteral("OBF file does not exist: %1").arg(_configuration.obfPath);
        return false;
    }
    if (!QFileInfo::exists(_configuration.stylePath))
    {
        errorMessage = QStringLiteral("Rendering style does not exist: %1").arg(_configuration.stylePath);
        return false;
    }
    if (!QFileInfo(_configuration.resourcesRoot).isDir())
    {
        errorMessage = QStringLiteral("OsmAnd resources directory does not exist: %1").arg(_configuration.resourcesRoot);
        return false;
    }
    if (!CoreRuntime::instance().acquire(_configuration.resourcesRoot, errorMessage))
        return false;

    _stylesCollection = std::make_shared<OsmAnd::MapStylesCollection>();
    if (!_stylesCollection->addStyleFromFile(_configuration.stylePath))
    {
        CoreRuntime::instance().release();
        errorMessage = QStringLiteral("Unable to load rendering style: %1").arg(_configuration.stylePath);
        return false;
    }

    _obfsCollection = std::make_shared<OsmAnd::ObfsCollection>();
    _obfsCollection->addFile(_configuration.obfPath);
    _styleName = QFileInfo(_configuration.stylePath).baseName();
    _locale = QLocale::system().name().section(QLatin1Char('_'), 0, 0).toLower();
    if (_locale.isEmpty())
        _locale = QStringLiteral("en");

    _resourcesReady = true;
    return true;
}

bool OsmAndNativeMapWidget::ensureRenderer()
{
    if (_mapRenderer)
        return true;
    if (!_resourcesReady)
        return false;

    const auto mapStyle = _stylesCollection->getResolvedStyleByName(_styleName);
    if (!mapStyle)
    {
        _initError = QStringLiteral("Unable to resolve rendering style: %1").arg(_styleName);
        return false;
    }

    _mapPresentationEnvironment = std::make_shared<OsmAnd::MapPresentationEnvironment>(
        mapStyle,
        static_cast<float>(std::max(1.0, devicePixelRatioF())),
        1.0f,
        1.0f);
    _mapPresentationEnvironment->setLocaleLanguageId(_locale);
    _mapPresentationEnvironment->setSettings(QHash<QString, QString>{
        {QStringLiteral("nightMode"), _configuration.nightMode ? QStringLiteral("true") : QStringLiteral("false")},
    });

    _primitiviser = std::make_shared<OsmAnd::MapPrimitiviser>(_mapPresentationEnvironment);
    _mapObjectsProvider = std::make_shared<OsmAnd::ObfMapObjectsProvider>(
        _obfsCollection,
        OsmAnd::ObfMapObjectsProvider::Mode::OnlyBinaryMapObjects,
        kConcurrentObfReadLimit);
    _mapPrimitivesProvider = std::make_shared<OsmAnd::MapPrimitivesProvider>(
        _mapObjectsProvider,
        _primitiviser,
        kReferenceTileSize);
    _mapSymbolsProvider = std::make_shared<OsmAnd::MapObjectsSymbolsProvider>(
        _mapPrimitivesProvider,
        kReferenceTileSize,
        std::shared_ptr<const OsmAnd::SymbolRasterizer>(),
        true);
    _mapRasterLayerProvider = std::make_shared<OsmAnd::MapRasterLayerProvider_Software>(
        _mapPrimitivesProvider,
        true,
        true);
    _mapRenderer = OsmAnd::createMapRenderer(OsmAnd::MapRendererClass::AtlasMapRenderer_OpenGL2plus);
    if (!_mapRenderer)
    {
        _initError = QStringLiteral("No supported OsmAnd renderer found");
        return false;
    }

    OsmAnd::MapRendererSetupOptions setupOptions;
    setupOptions.gpuWorkerThreadEnabled = false;
    setupOptions.displayDensityFactor = static_cast<float>(std::max(1.0, devicePixelRatioF()));
    setupOptions.pathToOpenGLShadersCache = openGlShadersCachePath();
    setupOptions.frameUpdateRequestCallback =
        [widget = QPointer<OsmAndNativeMapWidget>(this)]
        (const OsmAnd::IMapRenderer*)
        {
            if (!widget)
                return;

            QMetaObject::invokeMethod(widget.data(), "update", Qt::QueuedConnection);
        };
    if (!_mapRenderer->setup(setupOptions))
    {
        _initError = QStringLiteral("Failed to setup the OsmAnd renderer");
        _mapRenderer.reset();
        return false;
    }

    const auto rendererConfiguration = std::static_pointer_cast<OsmAnd::AtlasMapRendererConfiguration>(_mapRenderer->getConfiguration());
    rendererConfiguration->referenceTileSizeOnScreenInPixels = kReferenceTileSize;
    _mapRenderer->setConfiguration(rendererConfiguration);
    _mapRenderer->setFieldOfView(kDefaultFieldOfView);
    _mapRenderer->setElevationAngle(kDefaultElevationAngle);
    _mapRenderer->setMapLayerProvider(0, _mapRasterLayerProvider);
    _mapRenderer->addSymbolsProvider(_mapSymbolsProvider);
    syncRendererViewport(true);
    syncRendererCamera(true);

    if (!_mapRenderer->initializeRendering(true))
    {
        _initError = QStringLiteral("Failed to initialize native OsmAnd rendering");
        _mapRenderer.reset();
        return false;
    }

    _minZoomLevel = std::max(kDefaultMinZoom, static_cast<double>(_mapRenderer->getMinZoomLevel()));
    _maxZoomLevel = std::max(_minZoomLevel, static_cast<double>(_mapRenderer->getMaxZoomLevel()));
    _defaultZoomLevel = std::clamp(kDefaultZoom, _minZoomLevel, _maxZoomLevel);
    _zoomLevel = std::clamp(_zoomLevel, _minZoomLevel, _maxZoomLevel);
    syncRendererCamera(true);
    return true;
}

void OsmAndNativeMapWidget::syncRendererViewport(bool forcedUpdate)
{
    if (!_mapRenderer)
        return;

    const auto scale = std::max(1.0, devicePixelRatioF());
    const auto pixelWidth = std::max(1, static_cast<int>(std::lround(width() * scale)));
    const auto pixelHeight = std::max(1, static_cast<int>(std::lround(height() * scale)));
    _mapRenderer->setWindowSize(OsmAnd::PointI(pixelWidth, pixelHeight), forcedUpdate);
    _mapRenderer->setViewport(OsmAnd::AreaI(0, 0, pixelHeight, pixelWidth), forcedUpdate);
    _mapRenderer->setViewportScale(scale, forcedUpdate);
}

void OsmAndNativeMapWidget::syncRendererCamera(bool forcedUpdate)
{
    if (!_mapRenderer)
        return;

    const auto center = normalizedToLonLat(_centerX, _centerY);
    const auto target31 = OsmAnd::Utilities::convertLatLonTo31(OsmAnd::LatLon(center.y(), center.x()));
    _mapRenderer->setTarget(target31, forcedUpdate);
    _mapRenderer->setZoom(static_cast<float>(_zoomLevel), forcedUpdate);
    _mapRenderer->setAzimuth(0.0f, forcedUpdate);
    _mapRenderer->setElevationAngle(kDefaultElevationAngle, forcedUpdate);
    _mapRenderer->forcedFrameInvalidate();
}

void OsmAndNativeMapWidget::cleanupRenderer()
{
    if (!_mapRenderer)
        return;

    const auto hadContext = context() != nullptr;
    if (hadContext)
        makeCurrent();

    if (_mapRenderer->isRenderingInitialized())
        _mapRenderer->releaseRendering(false);

    _mapRenderer.reset();
    _mapRasterLayerProvider.reset();
    _mapSymbolsProvider.reset();
    _mapPrimitivesProvider.reset();
    _mapObjectsProvider.reset();
    _primitiviser.reset();
    _mapPresentationEnvironment.reset();

    if (hadContext)
        doneCurrent();
}

void OsmAndNativeMapWidget::wrapCenter()
{
    _centerX = std::fmod(_centerX, 1.0);
    if (_centerX < 0.0)
        _centerX += 1.0;

    const auto currentWorldSize = worldSize();
    const auto viewportHeight = std::max(1, height());
    const auto halfViewRatio = static_cast<double>(viewportHeight) / (2.0 * currentWorldSize);
    if (halfViewRatio >= 0.5)
    {
        _centerY = 0.5;
        return;
    }

    const auto minCenter = halfViewRatio;
    const auto maxCenter = 1.0 - halfViewRatio;
    _centerY = std::clamp(_centerY, minCenter, maxCenter);
}

double OsmAndNativeMapWidget::worldSize() const
{
    return static_cast<double>(kReferenceTileSize) * std::pow(2.0, _zoomLevel);
}

QPointF OsmAndNativeMapWidget::lonLatToNormalized(double longitude, double latitude)
{
    const auto clampedLatitude = clampLatitude(latitude);
    const auto x = (longitude + 180.0) / 360.0;
    const auto sinLatitude = std::sin(clampedLatitude * kPi / 180.0);
    const auto y = 0.5 - std::log((1.0 + sinLatitude) / (1.0 - sinLatitude)) / (4.0 * kPi);
    return {x, y};
}

QPointF OsmAndNativeMapWidget::normalizedToLonLat(double normalizedX, double normalizedY)
{
    const auto wrappedX = normalizedX - std::floor(normalizedX);
    const auto clampedY = std::clamp(normalizedY, 0.0, 1.0);
    const auto longitude = wrappedX * 360.0 - 180.0;
    const auto latitude = std::atan(std::sinh(kPi * (1.0 - 2.0 * clampedY))) * 180.0 / kPi;
    return {longitude, latitude};
}

