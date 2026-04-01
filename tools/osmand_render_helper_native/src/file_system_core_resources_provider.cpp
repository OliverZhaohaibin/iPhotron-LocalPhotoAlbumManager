#include "file_system_core_resources_provider.h"

#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QReadLocker>
#include <QRegularExpression>
#include <QWriteLocker>

namespace
{
QString normalizeRelativePath(const QString& path)
{
    return QDir::fromNativeSeparators(path);
}
}

FileSystemCoreResourcesProvider::FileSystemCoreResourcesProvider(const QString& resourcesRoot)
    : _resourcesRoot(QDir::cleanPath(QDir::fromNativeSeparators(resourcesRoot)))
    , _lock(QReadWriteLock::Recursive)
{
    scanResources();
}

QByteArray FileSystemCoreResourcesProvider::getResource(
    const QString& name,
    const float displayDensityFactor,
    bool* ok) const
{
    const auto entry = findEntry(name);
    if (!entry)
    {
        if (ok)
            *ok = false;
        return {};
    }

    const auto resourcePath = resolvePathForDensity(*entry, displayDensityFactor);
    if (resourcePath.isEmpty())
    {
        if (ok)
            *ok = false;
        return {};
    }

    return readResourceBytes(resourcePath, ok);
}

QByteArray FileSystemCoreResourcesProvider::getResource(const QString& name, bool* ok) const
{
    const auto entry = findEntry(name);
    if (!entry || entry->defaultPath.isEmpty())
    {
        if (ok)
            *ok = false;
        return {};
    }

    return readResourceBytes(entry->defaultPath, ok);
}

bool FileSystemCoreResourcesProvider::containsResource(
    const QString& name,
    const float displayDensityFactor) const
{
    const auto entry = findEntry(name);
    if (!entry)
        return false;

    if (!resolvePathForDensity(*entry, displayDensityFactor).isEmpty())
        return true;
    return !entry->defaultPath.isEmpty();
}

bool FileSystemCoreResourcesProvider::containsResource(const QString& name) const
{
    const auto entry = findEntry(name);
    return entry && !entry->defaultPath.isEmpty();
}

QString FileSystemCoreResourcesProvider::resourcesRoot() const
{
    return _resourcesRoot;
}

void FileSystemCoreResourcesProvider::scanResources()
{
    const QRegularExpression mapIconPattern(R"(^(c_)?mx_(.+)\.svg$)");
    const QRegularExpression shaderPattern(R"(^(c_)?h_(.+)\.svg$)");
    const QRegularExpression densityStubPattern(R"(^rendering_styles/stubs/\[ddf=([0-9.]+)\]/([^/]+\.png)$)");
    const QRegularExpression defaultStubPattern(R"(^rendering_styles/stubs/([^/\[]+\.png)$)");
    const QRegularExpression fontPattern(R"(^rendering_styles/fonts/([^/]+\.ttf)$)");
    const QRegularExpression icuPattern(R"(^misc/icu4c/icudt\d+([lb])\.dat$)");
    const QRegularExpression stylePattern(R"(^rendering_styles/([^/]+\.render\.xml)$)");

    QDirIterator it(_resourcesRoot, QDir::Files, QDirIterator::Subdirectories);
    while (it.hasNext())
    {
        const auto filePath = QDir::cleanPath(it.next());
        const auto relativePath = normalizeRelativePath(QDir(_resourcesRoot).relativeFilePath(filePath));
        const auto fileInfo = QFileInfo(filePath);
        QRegularExpressionMatch match;

        match = stylePattern.match(relativePath);
        if (match.hasMatch())
        {
            registerDefaultResource(QStringLiteral("map/styles/%1").arg(match.captured(1)), filePath);
            continue;
        }

        if (relativePath == QLatin1String("poi/poi_types.xml"))
        {
            registerDefaultResource(QStringLiteral("poi/poi_types.xml"), filePath);
            continue;
        }

        if (relativePath == QLatin1String("routing/routing.xml"))
        {
            registerDefaultResource(QStringLiteral("routing/routing.xml"), filePath);
            continue;
        }

        if (relativePath.startsWith(QLatin1String("rendering_styles/style-icons/map-icons-svg/")))
        {
            match = mapIconPattern.match(fileInfo.fileName());
            if (match.hasMatch())
            {
                const auto prefix = match.captured(1);
                const auto iconName = match.captured(2);
                registerDefaultResource(
                    QStringLiteral("map/icons/%1%2.svg").arg(prefix, iconName),
                    filePath);
            }
            continue;
        }

        if (relativePath.startsWith(QLatin1String("rendering_styles/style-icons/map-shaders-svg/")))
        {
            match = shaderPattern.match(fileInfo.fileName());
            if (match.hasMatch())
            {
                const auto prefix = match.captured(1);
                const auto shaderName = match.captured(2);
                registerDefaultResource(
                    QStringLiteral("map/shaders_and_shields/%1%2.svg").arg(prefix, shaderName),
                    filePath);
            }
            continue;
        }

        match = densityStubPattern.match(relativePath);
        if (match.hasMatch())
        {
            bool ok = false;
            const auto displayDensityFactor = match.captured(1).toFloat(&ok);
            if (ok)
            {
                registerDensityVariant(
                    QStringLiteral("map/stubs/%1").arg(match.captured(2)),
                    displayDensityFactor,
                    filePath);
            }
            continue;
        }

        match = defaultStubPattern.match(relativePath);
        if (match.hasMatch())
        {
            registerDefaultResource(QStringLiteral("map/stubs/%1").arg(match.captured(1)), filePath);
            continue;
        }

        match = fontPattern.match(relativePath);
        if (match.hasMatch())
        {
            registerDefaultResource(QStringLiteral("map/fonts/%1").arg(match.captured(1)), filePath);
            continue;
        }

        match = icuPattern.match(relativePath);
        if (match.hasMatch())
        {
            registerDefaultResource(QStringLiteral("misc/icu4c/icu-data-%1.dat").arg(match.captured(1)), filePath);
            continue;
        }

        if (relativePath.startsWith(QLatin1String("misc/")) && relativePath.indexOf(QLatin1Char('/'), 5) < 0)
        {
            registerDefaultResource(relativePath, filePath);
        }
    }
}

void FileSystemCoreResourcesProvider::registerDefaultResource(const QString& logicalName, const QString& filePath)
{
    QWriteLocker locker(&_lock);
    _resources[logicalName].defaultPath = filePath;
}

void FileSystemCoreResourcesProvider::registerDensityVariant(
    const QString& logicalName,
    float displayDensityFactor,
    const QString& filePath)
{
    QWriteLocker locker(&_lock);
    _resources[logicalName].variantsByDisplayDensityFactor.insert(displayDensityFactor, filePath);
}

QByteArray FileSystemCoreResourcesProvider::readResourceBytes(const QString& filePath, bool* ok) const
{
    {
        QReadLocker locker(&_lock);
        const auto cached = _bytesCache.constFind(filePath);
        if (cached != _bytesCache.cend())
        {
            if (ok)
                *ok = true;
            return *cached;
        }
    }

    QFile file(filePath);
    if (!file.open(QIODevice::ReadOnly))
    {
        if (ok)
            *ok = false;
        return {};
    }

    const auto bytes = file.readAll();
    {
        QWriteLocker locker(&_lock);
        _bytesCache.insert(filePath, bytes);
    }

    if (ok)
        *ok = true;
    return bytes;
}

const FileSystemCoreResourcesProvider::ResourceEntry* FileSystemCoreResourcesProvider::findEntry(const QString& name) const
{
    QReadLocker locker(&_lock);
    const auto it = _resources.constFind(name);
    if (it == _resources.cend())
        return nullptr;
    return &(*it);
}

QString FileSystemCoreResourcesProvider::resolvePathForDensity(
    const ResourceEntry& entry,
    float displayDensityFactor) const
{
    if (!entry.variantsByDisplayDensityFactor.isEmpty())
    {
        auto it = entry.variantsByDisplayDensityFactor.lowerBound(displayDensityFactor);
        if (it != entry.variantsByDisplayDensityFactor.end())
            return it.value();
        return entry.variantsByDisplayDensityFactor.last();
    }

    return entry.defaultPath;
}
