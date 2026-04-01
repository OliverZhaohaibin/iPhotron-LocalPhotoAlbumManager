#include "osmand_native_map_widget.h"

#include "file_system_core_resources_provider.h"

#include <algorithm>
#include <cstdlib>
#include <cmath>
#include <memory>
#include <mutex>

#include <QDir>
#include <QCryptographicHash>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocale>
#include <QMetaObject>
#include <QMouseEvent>
#include <QOpenGLContext>
#include <QOpenGLFunctions>
#include <QPointer>
#include <QStandardPaths>
#include <QStringList>
#include <QThread>
#include <QWheelEvent>

#include <SkBitmap.h>
#include <SkData.h>
#include <SkEncodedImageFormat.h>

#include <OsmAndCore.h>
#include <OsmAndCore/CachingTypefaceFinder.h>
#include <OsmAndCore/EmbeddedTypefaceFinder.h>
#include <OsmAndCore/Logging.h>
#include <OsmAndCore/ObfsCollection.h>
#include <OsmAndCore/SimpleQueryController.h>
#include <OsmAndCore/TextRasterizer.h>
#include <OsmAndCore/Utilities.h>
#include <OsmAndCore/Data/MapObject.h>
#include <OsmAndCore/Map/AtlasMapRendererConfiguration.h>
#include <OsmAndCore/Map/IMapRenderer.h>
#include <OsmAndCore/Map/MapObjectsSymbolsProvider.h>
#include <OsmAndCore/Map/MapPresentationEnvironment.h>
#include <OsmAndCore/Map/MapPrimitivesProvider.h>
#include <OsmAndCore/Map/MapPrimitiviser.h>
#include <OsmAndCore/Map/MapRasterLayerProvider_Software.h>
#include <OsmAndCore/Map/SymbolRasterizer.h>
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
constexpr float kStableDetailedDistance = 0.5f;
constexpr float kInteractiveDetailedDistance = 0.0f;
constexpr float kStableSymbolsOpacity = 1.0f;
constexpr float kInteractiveSymbolsOpacity = 0.0f;
constexpr int kInteractionSettleDelayMs = 140;
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

    QByteArray fingerprintSeed("shader-cache-v2");
    if (auto* currentContext = QOpenGLContext::currentContext())
    {
        if (auto* functions = currentContext->functions())
        {
            const auto appendString = [&fingerprintSeed, functions](const GLenum name)
            {
                const auto* rawValue = reinterpret_cast<const char*>(functions->glGetString(name));
                if (rawValue != nullptr)
                    fingerprintSeed.append(rawValue);
                fingerprintSeed.append('\n');
            };
            appendString(GL_VENDOR);
            appendString(GL_RENDERER);
            appendString(GL_VERSION);
            appendString(GL_SHADING_LANGUAGE_VERSION);
        }
    }

    const auto cacheKey = QString::fromLatin1(
        QCryptographicHash::hash(fingerprintSeed, QCryptographicHash::Sha1).toHex().left(16));
    const auto cachePath = QDir(baseCachePath).filePath(
        QStringLiteral("maps/osmand_gl_shaders/%1").arg(cacheKey));
    QDir().mkpath(cachePath);
    return cachePath;
}

bool writeImageAsPng(const sk_sp<const SkImage>& image, const QString& outputPath)
{
    if (!image)
        return false;

    QDir().mkpath(QFileInfo(outputPath).absolutePath());
    const auto encodedImage = image->encodeToData(SkEncodedImageFormat::kPNG, 100);
    if (!encodedImage)
        return false;

    QFile outputFile(outputPath);
    if (!outputFile.open(QIODevice::WriteOnly | QIODevice::Truncate))
        return false;

    const auto bytesWritten =
        outputFile.write(reinterpret_cast<const char*>(encodedImage->data()), encodedImage->size());
    outputFile.close();
    return bytesWritten == encodedImage->size();
}

std::shared_ptr<const OsmAnd::TextRasterizer> createEmbeddedOnlyTextRasterizer()
{
    return std::make_shared<OsmAnd::TextRasterizer>(
        std::shared_ptr<const OsmAnd::ITypefaceFinder>(
            new OsmAnd::CachingTypefaceFinder(
                std::shared_ptr<const OsmAnd::ITypefaceFinder>(
                    new OsmAnd::EmbeddedTypefaceFinder()))));
}

void dumpTextRasterizerProbe(
    const QString& tag,
    const std::shared_ptr<const OsmAnd::TextRasterizer>& rasterizer,
    const QString& text)
{
    if (!rasterizer || text.isEmpty())
        return;

    OsmAnd::TextRasterizer::Style style;
    style
        .setSize(24.0f)
        .setBold(false)
        .setItalic(false)
        .setColor(OsmAnd::ColorARGB(255, 0, 0, 0))
        .setHaloRadius(4)
        .setHaloColor(OsmAnd::ColorARGB(255, 255, 255, 255));

    const auto image = rasterizer->rasterize(text, style);
    const auto outputPath = QDir::current().filePath(
        QStringLiteral("debug/text_probe_%1.png").arg(tag));
    const auto ok = writeImageAsPng(image, outputPath);
    const auto payload = QJsonObject{
        {QStringLiteral("tag"), tag},
        {QStringLiteral("path"), outputPath},
        {QStringLiteral("ok"), ok},
        {QStringLiteral("width"), image ? image->width() : 0},
        {QStringLiteral("height"), image ? image->height() : 0},
        {QStringLiteral("text"), text},
    };
    std::cout
        << "[osmand_native_widget][text_probe] "
        << QJsonDocument(payload).toJson(QJsonDocument::Compact).constData()
        << std::endl;
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
    , _interactionTimer(this)
{
    _interactionTimer.setSingleShot(true);
    _interactionTimer.setInterval(kInteractionSettleDelayMs);
    connect(&_interactionTimer, &QTimer::timeout, this, &OsmAndNativeMapWidget::finishInteractiveRendering);

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

    beginInteractiveRendering();
    scheduleInteractiveRenderingFinish();
    _zoomLevel = clampedZoom;
    wrapCenter();
    syncRendererCamera(false);
    update();
}

void OsmAndNativeMapWidget::resetView()
{
    finishInteractiveRendering();
    _centerX = 0.5;
    _centerY = 0.5;
    _zoomLevel = _defaultZoomLevel;
    wrapCenter();
    syncRendererCamera(false);
    update();
}

void OsmAndNativeMapWidget::panByPixels(double deltaX, double deltaY)
{
    const auto currentWorldSize = worldSize();
    if (currentWorldSize <= 0.0)
        return;

    beginInteractiveRendering();
    scheduleInteractiveRenderingFinish();
    _centerX -= deltaX / currentWorldSize;
    _centerY -= deltaY / currentWorldSize;
    wrapCenter();
    syncRendererCamera(false);
    update();
}

void OsmAndNativeMapWidget::setCenterLonLat(double longitude, double latitude)
{
    finishInteractiveRendering();
    const auto normalized = lonLatToNormalized(longitude, latitude);
    _centerX = normalized.x();
    _centerY = normalized.y();
    wrapCenter();
    syncRendererCamera(false);
    update();
}

QPointF OsmAndNativeMapWidget::centerLonLat() const
{
    return normalizedToLonLat(_centerX, _centerY);
}

bool OsmAndNativeMapWidget::projectLonLat(double longitude, double latitude, QPointF& outScreenPoint) const
{
    if (!_mapRenderer)
        return false;

    const auto position31 = OsmAnd::Utilities::convertLatLonTo31(OsmAnd::LatLon(latitude, longitude));
    OsmAnd::PointI screenPoint;
    if (!_mapRenderer->obtainScreenPointFromPosition(position31, screenPoint, false))
        return false;

    outScreenPoint = QPointF(screenPoint.x, screenPoint.y);
    return true;
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
        beginInteractiveRendering();
        scheduleInteractiveRenderingFinish();
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
        scheduleInteractiveRenderingFinish();
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

    beginInteractiveRendering();
    scheduleInteractiveRenderingFinish();
    _centerX = newCenterPixelX / newWorldSize;
    _centerY = newCenterPixelY / newWorldSize;
    wrapCenter();
    syncRendererCamera(false);
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
    rendererConfiguration->referenceTileSizeOnScreenInPixels = static_cast<float>(kReferenceTileSize);
    _mapRenderer->setConfiguration(rendererConfiguration);
    _mapRenderer->setFieldOfView(kDefaultFieldOfView);
    _mapRenderer->setElevationAngle(kDefaultElevationAngle);
    _mapRenderer->setMapLayerProvider(0, _mapRasterLayerProvider);
    _mapRenderer->addSymbolsProvider(_mapSymbolsProvider);
    _mapRenderer->setResourceWorkerThreadsLimit(static_cast<unsigned int>(std::clamp(QThread::idealThreadCount(), 4, 8)));
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
    _mapRenderer->setDetailedDistance(kStableDetailedDistance, true);
    _mapRenderer->setSymbolsOpacity(kStableSymbolsOpacity, true);
    maybeDumpCaptionDiagnostics();
    syncRendererCamera(true);
    return true;
}

void OsmAndNativeMapWidget::maybeDumpCaptionDiagnostics()
{
    if (_captionDiagnosticsDumped || !_mapObjectsProvider)
        return;
    if (std::getenv("IPHOTO_OSMAND_DEBUG_CAPTIONS") == nullptr)
        return;

    _captionDiagnosticsDumped = true;

    const auto integralZoom = std::clamp(
        static_cast<int>(std::floor(_zoomLevel)),
        static_cast<int>(kDefaultMinZoom),
        static_cast<int>(kDefaultMaxZoom));
    const auto zoomLevel = static_cast<OsmAnd::ZoomLevel>(integralZoom);
    const auto tilesCount = 1 << integralZoom;
    const auto tileX = std::clamp(static_cast<int>(std::floor(_centerX * tilesCount)), 0, tilesCount - 1);
    const auto tileY = std::clamp(static_cast<int>(std::floor(_centerY * tilesCount)), 0, tilesCount - 1);
    const auto tileId = OsmAnd::TileId::fromXY(tileX, tileY);

    OsmAnd::ObfMapObjectsProvider::Request request;
    request.tileId = tileId;
    request.zoom = zoomLevel;
    request.detailedZoom = zoomLevel;
    request.visibleArea31 = OsmAnd::Utilities::tileBoundingBox31(tileId, zoomLevel);
    request.areaTime = QDateTime::currentMSecsSinceEpoch();
    request.queryController = std::make_shared<OsmAnd::SimpleQueryController>();

    std::shared_ptr<OsmAnd::ObfMapObjectsProvider::Data> mapObjectsData;
    if (!_mapObjectsProvider->obtainTiledObfMapObjects(request, mapObjectsData) || !mapObjectsData)
    {
        std::cerr << "[osmand_native_widget][caption] failed to obtain map objects for diagnostics" << std::endl;
        return;
    }

    std::cout
        << "[osmand_native_widget][caption] "
        << QJsonDocument(QJsonObject{
               {QStringLiteral("tile_z"), integralZoom},
               {QStringLiteral("tile_x"), tileX},
               {QStringLiteral("tile_y"), tileY},
               {QStringLiteral("objects"), static_cast<int>(mapObjectsData->mapObjects.size())},
           }).toJson(QJsonDocument::Compact).constData()
        << std::endl;

    int emitted = 0;
    for (const auto& mapObject : constOf(mapObjectsData->mapObjects))
    {
        if (!mapObject || mapObject->captions.isEmpty())
            continue;

        const auto nativeCaption = mapObject->getCaptionInNativeLanguage();
        const auto englishCaption = mapObject->getCaptionInLanguage(QStringLiteral("en"));
        if (nativeCaption.isEmpty() && englishCaption.isEmpty())
            continue;

        const auto payload = QJsonObject{
            {QStringLiteral("object"), mapObject->toString()},
            {QStringLiteral("native"), nativeCaption},
            {QStringLiteral("en"), englishCaption},
            {QStringLiteral("captions_count"), static_cast<int>(mapObject->captions.size())},
        };
        std::cout
            << "[osmand_native_widget][caption] "
            << QJsonDocument(payload).toJson(QJsonDocument::Compact).constData()
            << std::endl;

        emitted++;
        if (emitted >= 20)
            break;
    }

    if (std::getenv("IPHOTO_OSMAND_DEBUG_TEXT_SYMBOLS") != nullptr && _mapPrimitivesProvider)
    {
        OsmAnd::MapPrimitivesProvider::Request primitivesRequest;
        primitivesRequest.tileId = tileId;
        primitivesRequest.zoom = zoomLevel;
        primitivesRequest.detailedZoom = zoomLevel;
        primitivesRequest.visibleArea31 = request.visibleArea31;
        primitivesRequest.areaTime = request.areaTime;
        primitivesRequest.queryController = request.queryController;

        std::shared_ptr<OsmAnd::MapPrimitivesProvider::Data> primitivesData;
        if (_mapPrimitivesProvider->obtainTiledPrimitives(primitivesRequest, primitivesData)
            && primitivesData
            && primitivesData->primitivisedObjects)
        {
            int textSymbolsEmitted = 0;
            for (const auto& symbolsGroupEntry : rangeOf(constOf(primitivesData->primitivisedObjects->symbolsGroups)))
            {
                const auto& symbolsGroup = symbolsGroupEntry.value();
                for (const auto& symbol : constOf(symbolsGroup->symbols))
                {
                    const auto textSymbol =
                        std::dynamic_pointer_cast<const OsmAnd::MapPrimitiviser::TextSymbol>(symbol);
                    if (!textSymbol)
                        continue;

                    const auto payload = QJsonObject{
                        {QStringLiteral("base"), textSymbol->baseValue},
                        {QStringLiteral("value"), textSymbol->value},
                        {QStringLiteral("on_path"), textSymbol->drawOnPath},
                        {QStringLiteral("shield"), textSymbol->shieldResourceName},
                    };
                    std::cout
                        << "[osmand_native_widget][text_symbol] "
                        << QJsonDocument(payload).toJson(QJsonDocument::Compact).constData()
                        << std::endl;

                    textSymbolsEmitted++;
                    if (textSymbolsEmitted >= 20)
                        break;
                }
                if (textSymbolsEmitted >= 20)
                    break;
            }
        }
    }

    if (std::getenv("IPHOTO_OSMAND_DEBUG_TEXT_RASTERIZER") != nullptr)
    {
        QStringList sampleLines{
            QStringLiteral("Germany"),
            QStringLiteral("Deutschland"),
            QStringLiteral("Lower Saxony"),
            QStringLiteral("Niedersachsen"),
            QStringLiteral("Saxony-Anhalt"),
            QStringLiteral("Thuringia"),
            QStringLiteral("Bad Nenndorf"),
            QStringLiteral("Bueckeburg"),
            QStringLiteral("Thueringen"),
        };
        for (const auto& mapObject : constOf(mapObjectsData->mapObjects))
        {
            if (!mapObject)
                continue;

            const auto englishCaption = mapObject->getCaptionInLanguage(QStringLiteral("en"));
            if (!englishCaption.isEmpty() && !sampleLines.contains(englishCaption))
                sampleLines.push_back(englishCaption);

            if (sampleLines.size() >= 12)
                break;
        }

        const auto sampleText = sampleLines.join(QLatin1Char('\n'));
        dumpTextRasterizerProbe(
            QStringLiteral("default"),
            OsmAnd::TextRasterizer::getDefault(),
            sampleText);
        dumpTextRasterizerProbe(
            QStringLiteral("system"),
            OsmAnd::TextRasterizer::getOnlySystemFonts(),
            sampleText);
        dumpTextRasterizerProbe(
            QStringLiteral("embedded"),
            createEmbeddedOnlyTextRasterizer(),
            sampleText);
    }
}

void OsmAndNativeMapWidget::syncRendererViewport(bool forcedUpdate)
{
    if (!_mapRenderer)
        return;

    const auto scale = std::max(1.0, devicePixelRatioF());
    const auto logicalWidth = std::max(1, width());
    const auto logicalHeight = std::max(1, height());
    // OsmAnd expects window/viewport geometry in widget coordinates and applies
    // the high-DPI scale separately via ``setViewportScale()``. Passing device
    // pixels here as well effectively doubles the DPR and can make the
    // renderer wrap the map vertically at low zoom levels.
    _mapRenderer->setWindowSize(OsmAnd::PointI(logicalWidth, logicalHeight), forcedUpdate);
    _mapRenderer->setViewport(OsmAnd::AreaI(0, 0, logicalHeight, logicalWidth), forcedUpdate);
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

void OsmAndNativeMapWidget::beginInteractiveRendering()
{
    if (_interactionTimer.isActive())
        _interactionTimer.stop();

    if (_interactiveRendering)
        return;

    _interactiveRendering = true;
    if (_mapRenderer)
    {
        // Keep the reference tile size stable while interacting so the camera
        // scale does not visibly jump at the start/end of drag or wheel input.
        _mapRenderer->setDetailedDistance(kInteractiveDetailedDistance);
        _mapRenderer->setSymbolsOpacity(kInteractiveSymbolsOpacity);
        if (!_symbolsSuspendedByInteraction && !_mapRenderer->isSymbolsUpdateSuspended())
            _symbolsSuspendedByInteraction = _mapRenderer->suspendSymbolsUpdate();
        _mapRenderer->forcedFrameInvalidate();
    }
}

void OsmAndNativeMapWidget::scheduleInteractiveRenderingFinish()
{
    _interactionTimer.start();
}

void OsmAndNativeMapWidget::finishInteractiveRendering()
{
    if (_interactionTimer.isActive())
        _interactionTimer.stop();

    if (!_interactiveRendering)
        return;

    _interactiveRendering = false;
    if (_mapRenderer)
    {
        _mapRenderer->setDetailedDistance(kStableDetailedDistance);
        _mapRenderer->setSymbolsOpacity(kStableSymbolsOpacity);
        if (_symbolsSuspendedByInteraction)
        {
            _mapRenderer->resumeSymbolsUpdate();
            _symbolsSuspendedByInteraction = false;
        }
        _mapRenderer->forcedFrameInvalidate();
    }
    else
    {
        _symbolsSuspendedByInteraction = false;
    }

    update();
}

void OsmAndNativeMapWidget::cleanupRenderer()
{
    if (_interactionTimer.isActive())
        _interactionTimer.stop();

    if (!_mapRenderer)
        return;

    if (_symbolsSuspendedByInteraction)
    {
        _mapRenderer->resumeSymbolsUpdate();
        _symbolsSuspendedByInteraction = false;
    }
    _interactiveRendering = false;

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
