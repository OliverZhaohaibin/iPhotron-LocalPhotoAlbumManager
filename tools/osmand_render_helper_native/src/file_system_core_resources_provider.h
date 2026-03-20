#pragma once

#include <memory>

#include <OsmAndCore/ICoreResourcesProvider.h>

#include <QHash>
#include <QMap>
#include <QReadWriteLock>
#include <QString>

class FileSystemCoreResourcesProvider final : public OsmAnd::ICoreResourcesProvider
{
    Q_DISABLE_COPY_MOVE(FileSystemCoreResourcesProvider)

public:
    explicit FileSystemCoreResourcesProvider(const QString& resourcesRoot);
    ~FileSystemCoreResourcesProvider() override = default;

    QByteArray getResource(
        const QString& name,
        const float displayDensityFactor,
        bool* ok = nullptr) const override;
    QByteArray getResource(
        const QString& name,
        bool* ok = nullptr) const override;

    bool containsResource(
        const QString& name,
        const float displayDensityFactor) const override;
    bool containsResource(
        const QString& name) const override;

    QString resourcesRoot() const;

private:
    struct ResourceEntry
    {
        QString defaultPath;
        QMap<float, QString> variantsByDisplayDensityFactor;
    };

    QString _resourcesRoot;
    QHash<QString, ResourceEntry> _resources;
    mutable QHash<QString, QByteArray> _bytesCache;
    mutable QReadWriteLock _lock;

    void scanResources();
    void registerDefaultResource(const QString& logicalName, const QString& filePath);
    void registerDensityVariant(const QString& logicalName, float displayDensityFactor, const QString& filePath);
    QByteArray readResourceBytes(const QString& filePath, bool* ok) const;
    const ResourceEntry* findEntry(const QString& name) const;
    QString resolvePathForDensity(const ResourceEntry& entry, float displayDensityFactor) const;
};
