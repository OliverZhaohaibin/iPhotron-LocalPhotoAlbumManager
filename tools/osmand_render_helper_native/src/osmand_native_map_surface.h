#pragma once

#include <QWidget>

#ifdef Q_OS_MACOS
#include <QOpenGLWindow>
#include <QVBoxLayout>

// A native child window has its own swapchain. A QOpenGLWidget would instead
// require OpenGL composition for the entire window, including Metal QRhiWidget
// media viewers. Keep this contract in source, not just in a prebuilt dylib.
class OsmAndNativeMapSurface : public QOpenGLWindow
{
public:
    explicit OsmAndNativeMapSurface(QWidget*)
        : QOpenGLWindow(QOpenGLWindow::NoPartialUpdate)
    {
    }

    qreal devicePixelRatioF() const { return devicePixelRatio(); }
};

template<class Surface>
class OsmAndNativeMapHost final : public QWidget
{
public:
    OsmAndNativeMapHost(Surface* surface, QWidget* parent)
        : QWidget(parent), surface(surface)
    {
        auto* container = QWidget::createWindowContainer(surface, this);
        container->setFocusPolicy(Qt::StrongFocus);
        auto* layout = new QVBoxLayout(this);
        layout->setContentsMargins(0, 0, 0, 0);
        layout->setSpacing(0);
        layout->addWidget(container);
    }

    ~OsmAndNativeMapHost() override
    {
        // The container owns the window. Release GL while its context is still
        // alive, and let Qt destroy the window exactly once with the container.
        surface->shutdown();
    }

    Surface* const surface;

protected:
    void paintEvent(QPaintEvent*) override { surface->update(); }
};
#else
#include <QOpenGLWidget>
using OsmAndNativeMapSurface = QOpenGLWidget;
#endif
