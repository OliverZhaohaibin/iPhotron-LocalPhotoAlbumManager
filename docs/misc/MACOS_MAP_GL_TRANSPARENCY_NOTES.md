# macOS Map GL Transparency Notes

这份文档记录 Location map 在 macOS 下出现“legacy tile 区域整体透明”的排查经验。
问题发生在透明/无边框主窗口中，地图 marker 或其他 QWidget overlay 可能正常显示，
但底层 `QOpenGLWidget` 渲染出的 map tile 区域会透出后方窗口。

结论先行：这类问题通常不是地图数据、tile renderer 或 `QPainter` 本身坏了，
也不应优先通过在 `paintGL()` 里手动 `makeCurrent()` / `doneCurrent()` 解决。
真正的关键是 macOS 透明顶层窗口、Qt backing store、`QOpenGLWidget` 离屏 FBO
之间的合成边界。对本项目而言，legacy map 在 macOS 上更可靠的路径是使用
`QOpenGLWindow + QWidget.createWindowContainer()`，而不是继续把 map 放在
`QOpenGLWidget` 的 offscreen FBO 中合成。

---

## 现象

- Location section 中 marker/cluster overlay 可见，但地图底图区域透明。
- 透明区域不是显示空白色，而是直接透出 iPhoto 窗口后面的其他应用窗口。
- legacy tile 数据和 marker 数据都正常，marker 还能证明地理聚类链路没有断。
- 切换 Gallery / Detail / Location 后问题更容易复现，说明 show/hide 生命周期参与了问题。
- 单纯强制背景色、palette、stylesheet 或 `QPainter.fillRect()` 只能改善普通 QWidget，
  不能保证 `QOpenGLWidget` 的 FBO 在透明 NSWindow 中被正确合成。

## 根因判断

本项目主窗口在 macOS 上使用无边框/透明窗口来实现圆角和自定义 chrome。
在这种顶层窗口里，Qt 文档提到 `QOpenGLWidget` 需要正确的 alpha channel 才能参与合成。
但实际排查中，即使请求 `alphaBufferSize=8` 并且每帧 clear 到 alpha=1，
`QOpenGLWidget` 的离屏 FBO 仍可能被 macOS/Qt backing store 当成透明区域处理。

换句话说，问题不在“有没有画出 tile”，而在“画出的 GL FBO 是否被顶层窗口 compositor
拿来当不透明内容合成”。

一个很有用的诊断信号是：

- QWidget marker overlay 可见，但 map tile 透明：GL FBO 合成失败，overlay 只是普通 QWidget。
- 把 marker 移进 `paintGL()` 后 marker 也消失：GL 内容本身整层没有被合成出来。
- 切换到 `QOpenGLWindow + createWindowContainer()` 后 legacy tile 正常：说明 native GL window
  绕开了 `QOpenGLWidget` offscreen FBO 合成问题。

## 不太有效的方向

### 只改 alpha=0

最初尝试过让 map GL format 使用 `alphaBufferSize=0`，想把 surface 声明成不透明。
这在 Linux/Windows 上是合理的保守策略，但 macOS 透明顶层窗口下反而可能不符合
`QOpenGLWidget` 的合成要求。

macOS 下更合理的是请求 alpha channel：

- renderable type: OpenGL
- depth: 24
- stencil: 8
- alpha: 8
- samples: 0

然后每帧 clear/write alpha=1。

### 只改 palette/stylesheet

给 map page、host widget、fallback container 设置不透明背景是必要兜底，
可以避免第一帧重建时透出主窗口。但它不能修复 `QOpenGLWidget` FBO 没被合成的问题。

这些设置应该保留，但不要把它们当成根治手段：

- `WA_StyledBackground=True`
- `WA_TranslucentBackground=False`
- `WA_NoSystemBackground=False`
- stable opaque palette / stylesheet background

### 在 `paintGL()` 中手动 makeCurrent

`QOpenGLWidget.initializeGL()`、`resizeGL()`、`paintGL()` 进入时，Qt 已经保证对应 context current。
在这些生命周期函数里再手动 `makeCurrent()` 通常不会改变合成结果。

显式 context 管理只适合这些场景：

- 在 GL 生命周期外删除 texture/buffer。
- 在 worker/offscreen surface 中做离屏 GL 操作。
- 对 `QOpenGLWindow` 或自建 context 做更底层的资源管理。

对这个透明问题来说，手动 context 管理不是主解法。

### 使用 WA_AlwaysStackOnTop

`WA_AlwaysStackOnTop` 有时能改变 `QOpenGLWidget` 和普通 QWidget 的 stacking/compositing 顺序，
但它有明显副作用：GL widget 可能压住 marker overlay、pin overlay、tooltip 等普通 QWidget。

在本次排查中它没有解决底图透明，还导致 overlay 行为变差，因此不适合作为 Location map 的默认修复。

## 有效修复方向

### macOS legacy map 使用 native GL window

在 macOS 上，把 legacy GL map 从 `QOpenGLWidget` 迁到：

```text
QOpenGLWindow
  -> QWidget.createWindowContainer()
  -> 放入 PhotoMapView / mini-map host layout
```

这仍然是 OpenGL map 渲染，不是 QRhi/Metal map renderer，也不是 MapKit。
区别在于它使用真实 native GL window surface，而不是 `QOpenGLWidget` 的离屏 FBO。

对透明主窗口来说，这个差异非常关键：

- `QOpenGLWidget`: render to offscreen FBO, then Qt backing store composites it into top-level widget.
- `QOpenGLWindow`: native child window/surface participates in platform window composition.

当问题集中在 FBO/backing-store alpha 合成时，`QOpenGLWindow` 更接近我们想要的“真实 GL 窗口”。

### 保留严格 clear 和 surface format

即使换成 `QOpenGLWindow`，仍应保留严格的 GL 初始化和绘制约束：

- macOS 请求 alpha=8，depth=24，stencil=8，samples=0。
- 每次 `initializeGL()` 和 `paintGL()` 开始时 full-viewport clear。
- clear 前禁用 scissor 或至少保证 scissor 不会裁掉背景。
- 开启 RGBA color mask。
- clear color 使用稳定地图背景色，alpha 必须是 1。
- `QPainter` 阶段也用 `CompositionMode_Source` 写入 alpha=255 的背景。

这能保证“只要 surface 被合成出来，它就是不透明的”。

### marker overlay 策略

marker overlay 在排查中是一个很好的探针：

- 如果 marker 作为独立 QWidget overlay 可见，而 map 不见，说明数据和 overlay 没坏。
- 如果 marker 移入 GL painter 后也消失，说明 GL layer 整体没有被合成。

最终策略应根据 active map backend 选择：

- `QOpenGLWidget` 路径：谨慎使用独立 QWidget overlay，避免 stack-on-top 冲突。
- `QOpenGLWindow` 路径：可以把 marker 画进同一 GL/QPainter pass，减少 native child window 覆盖 QWidget overlay 的问题。

也就是说，marker 是否画进 GL 不是根治透明的手段，而是要配合最终 surface 类型。

## 维护 OsmAnd GL 后端的建议

native OsmAnd GL 后端在 macOS 上也应优先验证 native window/container 模型，
不要默认照搬 `QOpenGLWidget`。

建议顺序：

1. 保持 legacy tile 的 `QOpenGLWindow` 路径作为已知可用基线。
2. native OsmAnd 先复用相同的 surface format 和 opaque clear 纪律。
3. 避免在 native GL child window 上方依赖普通 QWidget overlay；需要 overlay 时优先同 pass 绘制，
   或使用独立 native overlay 方案。
4. show/hide、stacked page 切换后主动 request full repaint，并在 macOS 上补一个 queued repaint。
5. 用真实 macOS GUI 手动验证，不只依赖 offscreen pytest。

## 推荐诊断日志

保留一个 opt-in 环境变量，例如：

```bash
IPHOTO_MAP_GL_DEBUG=1
```

首个 `initializeGL()` / `paintGL()` 打印这些信息：

- requested surface format: alpha/depth/stencil/samples
- actual context format: alpha/depth/stencil/samples
- update behavior
- painter begin 是否成功
- 当前 backend 是 `QOpenGLWidget` 还是 `QOpenGLWindow`

默认不要输出，避免污染正常启动日志。

## 回归验证清单

macOS 手动验证最重要：

- 重启 app，直接打开 Location，legacy tile 不透明显示。
- 切换 Gallery / Detail / Location 多次，地图仍显示。
- 拖拽、缩放、marker 点击正常。
- cluster/marker overlay 不被 native GL surface 压住或吞掉。
- 后方窗口不再透过 Location map 区域。

自动化测试至少覆盖：

- macOS GL format: alpha=8, depth=24, stencil=8, samples=0。
- 非 macOS GL format 维持 alpha=0 或既有平台策略。
- macOS backend 选择 legacy GL 时返回 native GL window/container 路径，而不是 CPU fallback。
- Linux/Windows 仍使用原有 `QOpenGLWidget` / CPU fallback 逻辑。
- renderer 空 tile 情况下输出图像中心像素 alpha=255。

## 一句话经验

在 macOS 透明/无边框 Qt 主窗口里，如果 `QOpenGLWidget` 地图区域整块透明，
不要只围着 `paintGL()` 的 context current 状态打转。先判断是不是 FBO/backing-store 合成边界问题。
本项目的有效解法是：legacy map 保持 OpenGL，但 macOS 使用 `QOpenGLWindow + createWindowContainer`
作为真正的 GL surface。
