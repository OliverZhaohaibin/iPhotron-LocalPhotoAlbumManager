# People & Pets 扩展下载与安装计划

打包手验见[现行验收表](../../requirements/PEOPLE_PETS_EXTENSION_INSTALL_VALIDATION.md)。

## PR 932 的参考行为

以 [PR 932](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/pull/932) 的合并提交 `ecad02fa` 为基准。地图扩展把设置菜单中的下载入口改为安装方式选择，提供应用内下载、浏览器下载和本地归档安装。浏览器打开后会引导用户选取下载好的文件。地图页和位置面板会等待后台准备结束，再查询运行时能力。

- `RuntimeContext.map_extensions` 延迟创建进程级 `MapExtensionService`。`MapExtensionDownloadWorker` 负责 Qt 线程和信号，`MapExtensionInstaller` 负责从目标盘复制归档、校验官方包、受控解压、加锁、暂存及恢复。Windows 将扩展装到用户目录，遇到正在使用的原生 DLL 时可以延迟激活。参见[地图安装说明](../../misc/MAP_EXTENSION_INSTALLATION.md)和[打包验证表](../../requirements/MAP_EXTENSION_INSTALL_VALIDATION.md)。
- 沿用地图扩展的菜单位置、安装方式和浏览器恢复流程。People & Pets 的提示走 `dialogs` / `InformationPopup`。地图控制器的相关提示目前仍使用 `QMessageBox`，People & Pets 按[项目弹窗规范](../../misc/PROJECT_POPUP_GUARDRAILS.md)实现。

## 已核对的 Release 包

应用内下载和浏览器下载都使用 [iphotron-recognition-models-v1.zip](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/pet-models-v1/iphotron-recognition-models-v1.zip)。GitHub Release 元数据与实际下载结果一致，归档为 **100,497,212 字节**，SHA-256 为 `73819048dcf2d9352758a544a751be67512c18e0be458c295551b7142e91fa28`。

| 包内文件 | 当前运行时用途 |
| --- | --- |
| `buffalo_s/det_500m.onnx`、`buffalo_s/w600k_mbf.onnx` | InsightFace `buffalo_s` 的检测与识别 |
| `pets/detector/yolox_nano_coco.onnx` | Pets YOLOX 检测 |
| `pets/embedding/dinov2_vits14/dinov2_vits14.pt` 与同目录 `.pt.metadata.json` | Pets DINOv2 TorchScript 嵌入 |
| `manifest.json` | 包名 `iphotron-recognition-models`、版本 1、组件路径和文件校验清单 |

逐项读取了 ZIP 成员并计算摘要。包内 DINOv2 为 88,587,286 字节，哈希 `36e01591cfd52d194845d5b23b870dcb698f79c33f6140144180568f2155ab94`，与当前分支的 `src/iPhoto/pets/model_manifest.json` 及缓存元数据契约一致。同一 Release 中独立上传的 `dinov2_vits14.pt` 为 88,566,102 字节，摘要不同。组合 ZIP 安装应使用包内那份模型和配套元数据。

## 1. 复用地图归档安装基础设施

- 从 `infrastructure/services/map_extension_installer.py` 提取通用的归档导入步骤，包括将文件复制到目标盘的临时区、按大小和 SHA-256 验包、ZIP 路径与成员类型检查、进程间安装锁和暂存清理。地图原有的 ZIP / tar.xz 流程继续通过其安装器调用公共步骤，地图目录结构、ABI 校验、原生运行时和重启逻辑由地图安装器负责。
- Recognition 安装器提供独立的包定义和布局校验。固定 ZIP 的 URL、大小与 SHA-256，解压后校验 `manifest.json`、必需成员及其大小和哈希。确认两个 People ONNX 文件、YOLOX 和 DINOv2 元数据均满足当前运行时契约。此阶段只读模型字节和元数据，加载模型由识别 worker 负责。应用内下载和离线导入汇入同一条校验与安装路径。
- 由 `RuntimeContext` 延迟装配进程级 Recognition 扩展服务，安装入口由 `RuntimeEntryContract` 暴露给 GUI。GUI worker 转发进度和结构化结果。每个进程仅运行一项手动安装，跨进程使用目标目录的安装锁。完整下载可在安装失败后复用，下载中断清理 `.part` 文件。
- 建议采用应用级、版本化的用户可写 `extensions/recognition/v1/models`，以 `%LOCALAPPDATA%`、`~/Library/Application Support` 和 `${XDG_DATA_HOME:-~/.local/share}` 下的 `iPhoto` 为平台基目录。`models` 下保存 `buffalo_s/` 和 `pets/`，与 `FaceAnalysis(root=...)` 的 `root/models/<pack>` 约定一致。以整棵已验证的 `models` 目录为提交单元，在目标盘暂存并切换，保留上一份可用目录直至完成。模型读取与切换协调，中断恢复在读取前完成。
- People 和 Pets 的模型解析同时接入新的已验证目录。保留 `IPHOTO_FACE_MODEL_DIR`、`IPHOTO_PET_MODEL_DIR` 的显式覆盖及现有缓存、随包模型的读取回退。People 在 macOS / Linux 需要首次写入且随包路径只读时，使用单独的用户可写自动下载缓存，避免半下载文件进入已验证安装目录。解压、哈希和目录切换都在后台执行。

## 2. 在设置菜单提供显式操作

在 `MainHeader.settings_menu` 中紧邻「下载地图扩展…」加入「下载 People & Pets 扩展…」，经 `Ui_MainWindow` 暴露 `QAction`，由 `DesktopCoordinatorRuntime` 接线。菜单入口从主窗口可用，支持在未打开 People 页或未绑定相册时安装。为动作和全部提示补齐英语源文案、简体中文和德语翻译并重新生成 `.qm`。

| 操作 | 行为 |
| --- | --- |
| 下载 | 后台获取指定 ZIP，显示进度，完成校验与安装后关闭进度窗口 |
| 在浏览器中下载 | 用默认浏览器打开指定附件 URL，随后提示下载完成后从文件安装。浏览器打开失败时提供可复制的完整 URL |
| 从文件安装… | 用 `QFileDialog` 选择 ZIP，调用同一后台安装服务，全程可离线。取消文件选择时直接返回 |

选项、恢复和失败提示统一由 `InformationPopup` 呈现。多按钮场景在 `dialogs` 中增加基于 `InformationPopup` 的共享操作入口，复用当前主题调色板和顶层主窗口居中逻辑。普通信息和警告继续走 `dialogs.show_information()` / `dialogs.show_warning()`。后台失败提示使用非阻塞的展示方式，原有普通提示调用保留其阻塞语义。

## 3. 保留首次静默尝试，失败后提供恢复入口

1. 保留 `RecognitionCoordinator` 的激活条件。People 页已显示、首个视口已就绪并经过现有 350 ms 静默间隔后，由 `LibraryRuntimeController` 启动识别 worker。启动、单纯元数据扫描及菜单安装本身均不触发模型加载。
2. 有效缓存命中时直接使用。People 继续由 InsightFace 在首次初始化时尝试取得 `buffalo_s`，Pets 保留已有 YOLOX 下载逻辑和 `IPHOTO_PET_MODEL_AUTO_DOWNLOAD`。当前分支先初始化 DINOv2，且 `torchscript_url` 为空。若本机缺 DINOv2，初始化会在尝试 YOLOX 下载之前结束，此时直接进入组合 ZIP 恢复流程。组合包的应用内下载由用户从菜单显式发起。
3. 从 worker 经现有扫描协调边界传递类型化的模型缺失、下载失败或缓存不完整结果，附当前库/识别代次。People 在通用 `RuntimeError` 分支之前捕获模型和运行时可用性错误，并像 Pets 一样保留该批及其余照片的 `pending` / `retry`。缺少可选 AI 依赖、用户主动禁用模型下载、运行时初始化或单张推理错误按各自的原因显示状态。主动禁用下载时保留菜单手动安装入口。
4. 对当前识别请求的可恢复模型错误，在本次应用运行中合并为一个提示。示例文案为「未能自动获取完整的 People & Pets 识别模型。请在浏览器中下载安装包，然后从文件安装。」按钮为「在浏览器中下载」「从文件安装…」「稍后」。关闭后仍可随时从设置进入。库切换或退出后丢弃旧 worker 的提示，已在进行的手动安装与静默模型写入须协调，避免重复下载或同时覆盖目标。
5. 安装提交后更新模型可用状态。已通过激活条件的当前库，由 `LibraryRuntimeController` 在失败的 worker 退场后仅重新调度相应识别类型，使用新的模型路径继续处理 `pending` / `retry`。另一个仍工作的 worker 可以继续。安装结果若先于当前库的首次识别激活，留待用户进入 People 页时加载。复用现有 People/Pets 服务、快照提交及持久状态路径。

## 4. 验证与交付

- **归档与恢复**：覆盖正确的组合 ZIP、地图包误选、截断、摘要不符、必需成员或元数据缺失、越界路径、同盘暂存、两进程竞争、激活中断及上一版恢复。对地图安装器和原有地图菜单、后台准备流程运行回归测试，尤其是 Windows 锁定 DLL 和 macOS 随包准备。
- **启动与扫描**：用假下载端点和本地归档验证静默成功时无弹窗，缺模型或下载失败时只弹一次。验证禁网、缺依赖、已有缓存、双 worker 同时失败、后到的 worker 结果、切换库、关闭窗口及导入成功恢复；待扫描照片不因全局模型问题进入 `failed`。
- **界面**：验证设置菜单可在进入 People 页之前打开三种操作，浏览器 URL 与指定附件一致，浏览器失败时链接可复制，文件选择取消可安全返回。覆盖简体中文、英语、德语，应用深浅主题与系统主题相反时仍按应用主题显示，并在主窗口居中。
- **打包手验**：以 Windows `Program Files` 下的标准用户、签名 macOS 应用和 Linux AppImage 分别验证空缓存首次失败、浏览器下载、离线导入和安装后识别。至少使用一张有脸和一张有宠物的测试图确认运行时能实际加载，并检查图库、编辑和地图可正常使用。
- **测试入口**：扩展 `tests/infrastructure/test_map_extension_installer.py` 的共享流程回归，新增 Recognition 安装器与控制器测试。运行 `tests/test_library_manager.py`、`tests/test_people_pipeline.py`、`tests/test_people_service.py`、`tests/test_pet_service.py`、`tests/test_information_popup.py`、`tests/application/test_runtime_context.py`、地图扩展相关测试和 `tests/architecture`，再运行 `python3 tools/check_architecture.py`。实现落地时同步更新 `docs/architecture.md` 及 `docs/misc/PETS_RECOGNITION_RUNTIME.md` 的安装和路径说明。
