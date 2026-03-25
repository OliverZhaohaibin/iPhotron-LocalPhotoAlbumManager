#include "file_system_core_resources_provider.h"

#include <cmath>
#include <iostream>
#include <memory>
#include <string>

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocale>

#include <SkBitmap.h>
#include <SkCanvas.h>
#include <SkData.h>
#include <SkEncodedImageFormat.h>

#include <OsmAndCore.h>
#include <OsmAndCore/Logging.h>
#include <OsmAndCore/SimpleQueryController.h>
#include <OsmAndCore/Map/MapRasterizer.h>
#include <OsmAndCore/Map/MapPrimitivesProvider.h>
#include <OsmAndCore/Map/MapPresentationEnvironment.h>
#include <OsmAndCore/Map/MapPrimitiviser.h>
#include <OsmAndCore/Map/ObfMapObjectsProvider.h>
#include <OsmAndCore/Map/MapStylesCollection.h>
#include <OsmAndCore/ObfsCollection.h>
#include <OsmAndCore/Utilities.h>
#include <OsmAndCoreTools/EyePiece.h>

namespace
{
constexpr int kReferenceTileSize = 256;
constexpr double kDefaultMinZoom = 2.0;
constexpr double kDefaultMaxZoom = 19.0;
constexpr int kConcurrentObfReadLimit = 0;

QJsonObject makeErrorResponse(const QString& message)
{
    return QJsonObject{
        {QStringLiteral("status"), QStringLiteral("error")},
        {QStringLiteral("message"), message},
    };
}

QJsonObject makeOkResponse()
{
    return QJsonObject{
        {QStringLiteral("status"), QStringLiteral("ok")},
    };
}

class OsmAndRenderHelperSession
{
public:
    bool initialize(
        const QString& obfPath,
        const QString& resourcesRoot,
        const QString& stylePath,
        bool nightMode,
        QString& errorMessage)
    {
        shutdown();

        if (!QFileInfo::exists(obfPath))
        {
            errorMessage = QStringLiteral("OBF file does not exist: %1").arg(obfPath);
            return false;
        }
        if (!QFileInfo::exists(stylePath))
        {
            errorMessage = QStringLiteral("Rendering style does not exist: %1").arg(stylePath);
            return false;
        }
        if (!QFileInfo(resourcesRoot).isDir())
        {
            errorMessage = QStringLiteral("OsmAnd resources directory does not exist: %1").arg(resourcesRoot);
            return false;
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

        const auto stylesCollection = std::make_shared<OsmAnd::MapStylesCollection>();
        if (!stylesCollection->addStyleFromFile(stylePath))
        {
            OsmAnd::ReleaseCore();
            errorMessage = QStringLiteral("Unable to load rendering style: %1").arg(stylePath);
            return false;
        }

        const auto obfsCollection = std::make_shared<OsmAnd::ObfsCollection>();
        obfsCollection->addFile(obfPath);

        _provider = provider;
        _stylesCollection = stylesCollection;
        _obfsCollection = obfsCollection;
        _resourcesRoot = resourcesRoot;
        _stylePath = stylePath;
        _styleName = QFileInfo(stylePath).baseName();
        _nightMode = nightMode;
        _locale = QLocale::system().name().section(QLatin1Char('_'), 0, 0).toLower();
        if (_locale.isEmpty())
            _locale = QStringLiteral("en");
        _deviceScale = 0.0;
        _rasterSize = kReferenceTileSize;
        _initialized = true;
        return true;
    }

    bool renderTile(
        int z,
        int x,
        int y,
        double deviceScale,
        const QString& outputPath,
        QString& errorMessage)
    {
        if (!_initialized)
        {
            errorMessage = QStringLiteral("helper was not initialized");
            return false;
        }
        if (z < 0 || z > static_cast<int>(OsmAnd::ZoomLevel::MaxZoomLevel))
        {
            errorMessage = QStringLiteral("zoom is out of range: %1").arg(z);
            return false;
        }
        if (outputPath.isEmpty())
        {
            errorMessage = QStringLiteral("output_path can not be empty");
            return false;
        }

        const auto tileId = OsmAnd::TileId::fromXY(x, y);
        const auto zoomLevel = static_cast<OsmAnd::ZoomLevel>(z);
        const auto bbox31 = OsmAnd::Utilities::tileBoundingBox31(tileId, zoomLevel);
        const auto effectiveScale = std::max(1.0, deviceScale);
        if (!ensureRenderPipeline(effectiveScale, errorMessage))
            return false;

        const auto rasterSize = _rasterSize;
        QDir().mkpath(QFileInfo(outputPath).absolutePath());

        OsmAnd::MapPrimitivesProvider::Request request;
        request.tileId = tileId;
        request.zoom = zoomLevel;
        request.detailedZoom = zoomLevel;
        request.visibleArea31 = bbox31;
        request.areaTime = QDateTime::currentMSecsSinceEpoch();
        request.queryController = std::make_shared<OsmAnd::SimpleQueryController>();

        std::shared_ptr<OsmAnd::MapPrimitivesProvider::Data> primitivesData;
        if (!_primitivesProvider->obtainTiledPrimitives(request, primitivesData))
        {
            errorMessage = QStringLiteral("Failed to obtain map primitives for tile rendering");
            return false;
        }

        SkBitmap bitmap;
        if (!bitmap.tryAllocPixels(SkImageInfo::MakeN32Premul(rasterSize, rasterSize)))
        {
            errorMessage = QStringLiteral("Failed to allocate bitmap for tile rendering");
            return false;
        }

        SkCanvas canvas(bitmap);
        canvas.clear(_mapPresentationEnvironment->getDefaultBackgroundColor(zoomLevel).toSkColor());

        if (primitivesData && primitivesData->primitivisedObjects)
        {
            _rasterizer->rasterize(
                bbox31,
                primitivesData->primitivisedObjects,
                canvas,
                true,
                nullptr,
                nullptr,
                request.queryController);
        }

        const auto image = bitmap.asImage();
        if (!image)
        {
            errorMessage = QStringLiteral("Software rasterization produced no image");
            return false;
        }

        const auto encodedImage = image->encodeToData(SkEncodedImageFormat::kPNG, 100);
        if (!encodedImage)
        {
            errorMessage = QStringLiteral("Failed to encode raster tile as PNG");
            return false;
        }

        QFile outputFile(outputPath);
        if (!outputFile.open(QIODevice::WriteOnly | QIODevice::Truncate))
        {
            errorMessage = QStringLiteral("Failed to open destination file: %1").arg(outputPath);
            return false;
        }

        if (outputFile.write(reinterpret_cast<const char*>(encodedImage->data()), encodedImage->size()) != encodedImage->size())
        {
            errorMessage = QStringLiteral("Failed to write rendered tile to %1").arg(outputPath);
            outputFile.close();
            return false;
        }
        outputFile.close();

        return true;
    }

    void shutdown()
    {
        if (_initialized)
        {
            OsmAnd::ReleaseCore();
        }

        _provider.reset();
        _stylesCollection.reset();
        _obfsCollection.reset();
        _mapPresentationEnvironment.reset();
        _primitiviser.reset();
        _mapObjectsProvider.reset();
        _primitivesProvider.reset();
        _rasterizer.reset();
        _resourcesRoot.clear();
        _stylePath.clear();
        _styleName.clear();
        _locale.clear();
        _nightMode = false;
        _deviceScale = 0.0;
        _rasterSize = kReferenceTileSize;
        _initialized = false;
    }

    bool initialized() const
    {
        return _initialized;
    }

private:
    bool ensureRenderPipeline(double deviceScale, QString& errorMessage)
    {
        const auto effectiveScale = std::max(1.0, deviceScale);
        const auto rasterSize = static_cast<unsigned int>(std::lround(kReferenceTileSize * effectiveScale));
        const auto scaleChanged = std::abs(_deviceScale - effectiveScale) > 1e-6;
        if (!scaleChanged && _mapPresentationEnvironment && _primitiviser && _mapObjectsProvider
            && _primitivesProvider && _rasterizer && _rasterSize == rasterSize)
        {
            return true;
        }

        const auto mapStyle = _stylesCollection->getResolvedStyleByName(_styleName);
        if (!mapStyle)
        {
            errorMessage = QStringLiteral("Unable to resolve rendering style: %1").arg(_styleName);
            return false;
        }

        auto mapPresentationEnvironment = std::make_shared<OsmAnd::MapPresentationEnvironment>(
            mapStyle,
            static_cast<float>(effectiveScale),
            1.0f,
            1.0f);
        mapPresentationEnvironment->setLocaleLanguageId(_locale);
        mapPresentationEnvironment->setSettings(QHash<QString, QString>{
            {QStringLiteral("nightMode"), _nightMode ? QStringLiteral("true") : QStringLiteral("false")},
        });

        auto primitiviser = std::make_shared<OsmAnd::MapPrimitiviser>(mapPresentationEnvironment);
        auto mapObjectsProvider = std::make_shared<OsmAnd::ObfMapObjectsProvider>(
            _obfsCollection,
            OsmAnd::ObfMapObjectsProvider::Mode::OnlyBinaryMapObjects,
            kConcurrentObfReadLimit);
        auto primitivesProvider = std::make_shared<OsmAnd::MapPrimitivesProvider>(
            mapObjectsProvider,
            primitiviser,
            rasterSize);

        _mapPresentationEnvironment = std::move(mapPresentationEnvironment);
        _primitiviser = std::move(primitiviser);
        _mapObjectsProvider = std::move(mapObjectsProvider);
        _primitivesProvider = std::move(primitivesProvider);
        _rasterizer = std::make_unique<OsmAnd::MapRasterizer>(_mapPresentationEnvironment);
        _deviceScale = effectiveScale;
        _rasterSize = rasterSize;
        return true;
    }

    std::shared_ptr<const FileSystemCoreResourcesProvider> _provider;
    std::shared_ptr<OsmAnd::MapStylesCollection> _stylesCollection;
    std::shared_ptr<OsmAnd::ObfsCollection> _obfsCollection;
    std::shared_ptr<OsmAnd::MapPresentationEnvironment> _mapPresentationEnvironment;
    std::shared_ptr<OsmAnd::MapPrimitiviser> _primitiviser;
    std::shared_ptr<OsmAnd::ObfMapObjectsProvider> _mapObjectsProvider;
    std::shared_ptr<OsmAnd::MapPrimitivesProvider> _primitivesProvider;
    std::unique_ptr<OsmAnd::MapRasterizer> _rasterizer;
    QString _resourcesRoot;
    QString _stylePath;
    QString _styleName;
    QString _locale;
    bool _nightMode = false;
    double _deviceScale = 0.0;
    unsigned int _rasterSize = kReferenceTileSize;
    bool _initialized = false;
};

QJsonObject handleInitCommand(const QJsonObject& command, OsmAndRenderHelperSession& session)
{
    QString errorMessage;
    const auto obfPath = command.value(QStringLiteral("obf_path")).toString();
    const auto resourcesRoot = command.value(QStringLiteral("resources_root")).toString();
    const auto stylePath = command.value(QStringLiteral("style_path")).toString();
    const auto nightMode = command.value(QStringLiteral("night_mode")).toBool(false);

    if (!session.initialize(obfPath, resourcesRoot, stylePath, nightMode, errorMessage))
        return makeErrorResponse(errorMessage);

    auto response = makeOkResponse();
    response.insert(QStringLiteral("min_zoom"), kDefaultMinZoom);
    response.insert(QStringLiteral("max_zoom"), kDefaultMaxZoom);
    response.insert(QStringLiteral("provides_place_labels"), false);
    response.insert(QStringLiteral("tile_kind"), QStringLiteral("raster"));
    return response;
}

QJsonObject handleRenderCommand(const QJsonObject& command, OsmAndRenderHelperSession& session)
{
    QString errorMessage;
    const auto z = command.value(QStringLiteral("z")).toInt(-1);
    const auto x = command.value(QStringLiteral("x")).toInt();
    const auto y = command.value(QStringLiteral("y")).toInt();
    const auto deviceScale = command.value(QStringLiteral("device_scale")).toDouble(1.0);
    const auto outputPath = command.value(QStringLiteral("output_path")).toString();

    if (!session.renderTile(z, x, y, deviceScale, outputPath, errorMessage))
        return makeErrorResponse(errorMessage);

    return makeOkResponse();
}

QJsonObject dispatchCommand(const QJsonObject& command, OsmAndRenderHelperSession& session, bool& shouldExit)
{
    shouldExit = false;

    const auto commandName = command.value(QStringLiteral("command")).toString();
    if (commandName == QLatin1String("init"))
        return handleInitCommand(command, session);
    if (commandName == QLatin1String("render"))
        return handleRenderCommand(command, session);
    if (commandName == QLatin1String("shutdown"))
    {
        session.shutdown();
        shouldExit = true;
        return makeOkResponse();
    }

    return makeErrorResponse(QStringLiteral("unknown command: %1").arg(commandName));
}

int runOneShotRender(int argc, char** argv)
{
    if (argc != 9 && argc != 10)
    {
        std::cerr << "usage: osmand_render_helper --render-tile <obf> <resources> <style> <z> <x> <y> <output> [deviceScale]" << std::endl;
        return 2;
    }

    bool ok = false;
    const auto z = QString::fromLocal8Bit(argv[5]).toInt(&ok);
    if (!ok)
    {
        std::cerr << "invalid z value" << std::endl;
        return 2;
    }
    const auto x = QString::fromLocal8Bit(argv[6]).toInt(&ok);
    if (!ok)
    {
        std::cerr << "invalid x value" << std::endl;
        return 2;
    }
    const auto y = QString::fromLocal8Bit(argv[7]).toInt(&ok);
    if (!ok)
    {
        std::cerr << "invalid y value" << std::endl;
        return 2;
    }

    double deviceScale = 1.0;
    if (argc == 10)
    {
        deviceScale = QString::fromLocal8Bit(argv[9]).toDouble(&ok);
        if (!ok)
        {
            std::cerr << "invalid deviceScale value" << std::endl;
            return 2;
        }
    }

    OsmAndRenderHelperSession session;
    QString errorMessage;
    if (!session.initialize(
        QString::fromLocal8Bit(argv[2]),
        QString::fromLocal8Bit(argv[3]),
        QString::fromLocal8Bit(argv[4]),
        false,
        errorMessage))
    {
        std::cerr << errorMessage.toStdString() << std::endl;
        return 1;
    }

    if (!session.renderTile(
        z,
        x,
        y,
        deviceScale,
        QString::fromLocal8Bit(argv[8]),
        errorMessage))
    {
        std::cerr << errorMessage.toStdString() << std::endl;
        session.shutdown();
        return 1;
    }

    session.shutdown();
    return 0;
}
}

#include <QCoreApplication>

int main(int argc, char** argv)
{
    QCoreApplication app(argc, argv);
    OsmAnd::Logger::get()->setSeverityLevelThreshold(static_cast<OsmAnd::LogSeverityLevel>(999));

    if (argc > 1)
    {
        const auto command = QString::fromLocal8Bit(argv[1]);
        if (command == QLatin1String("--render-tile"))
            return runOneShotRender(argc, argv);

        std::cerr << "unknown command line mode: " << command.toStdString() << std::endl;
        return 2;
    }

    OsmAndRenderHelperSession session;
    std::string rawLine;

    while (std::getline(std::cin, rawLine))
    {
        const auto line = QByteArray::fromStdString(rawLine);
        if (line.trimmed().isEmpty())
            continue;

        QJsonParseError parseError;
        const auto document = QJsonDocument::fromJson(line, &parseError);
        QJsonObject response;
        bool shouldExit = false;

        if (parseError.error != QJsonParseError::NoError || !document.isObject())
        {
            response = makeErrorResponse(QStringLiteral("invalid JSON request"));
        }
        else
        {
            response = dispatchCommand(document.object(), session, shouldExit);
        }

        std::cout << QJsonDocument(response).toJson(QJsonDocument::Compact).constData() << std::endl;
        if (shouldExit)
            break;
    }

    session.shutdown();
    return 0;
}

