# Windows 全屏一键验证与回传

此流程用于为 PR #931 收集验收证据。脚本只在本机运行检查和生成产物，**不会自行更新 GitHub、勾选 PR 清单或合并 PR**。回传 ZIP 后，再根据实际结果更新 PR body。

## 运行

更新 Windows 工作区到包含此脚本的最新提交，关闭其他 iPhoto 实例，在项目根目录打开 PowerShell，并使用运行 iPhoto 的同一个 Python 环境：

```powershell
python .\tools\validate_windows_fullscreen.py
```

提示符已有 `(.venv)` 时直接使用 `python`，不用手写 `.venv` 路径。运行期间保持桌面解锁，不要遮挡自动探针窗口，也不要手动操作自动探针。

默认流程：

1. **预检：** 记录解释器、Qt/PySide、源码提交、工作区脏状态、工具 SHA-256 和相关图形环境变量；检查是否为已修正的桌面区域采样工具。
2. **状态与采样合约测试：** 在 Windows Qt 平台运行全屏恢复、Edit 状态同步和截图坐标测试。它们包含测试替身，不能替代真实 GPU 验收。没有 pytest 时会如实跳过，不会自动安装依赖。
3. **两组像素探针：** 使用透明窗口和 OpenGL，分别执行普通 overscan、GL 状态污染测试。每组针对普通图、裁剪图、裁剪＋拉直图各进行 **20 次**全屏往返，并模拟滚轮。脚本会询问每组是否仍有肉眼闪烁、偏移或卡顿；即使像素检查通过，也要如实记录肉眼异常。
4. **真实应用：** 显示验收清单，然后询问是否启动现有诊断采集器。应用中手动验证真实媒体和窗口操作；遇到问题切回采集器控制台按 `R`，正常关闭应用后等待采集结束；无响应时按 `Q` 强制结束。
5. **人工结果：** 逐项输入 `P`（列出的子步骤全部通过）、`F`（出现问题）或 `S`（没测/设备不具备条件）。默认回车是 `S`，不是通过。可填写中文备注。
6. **打包：** 输出一个 ZIP 和同名展开目录，默认保存在 Windows 系统桌面（包含重定向的 OneDrive 桌面）。终端最后打印完整 ZIP 路径。

脚本不更改显卡驱动、Windows 缩放设置或父进程环境，不切换到 DX，也不复制原图、sidecar 或图库数据库。像素图片来自合成图探针的可见桌面区域；应用日志仍可能包含媒体文件名或元数据，回传前可以检查。

## 人工验收项目

运行目录中的 `CHECKLIST.md` 可随时查看。以下项目分别记录，不会由一次像素探针自动推定为通过：

| 项目 | 操作与观察 |
|---|---|
| 普通终端启动 | 另行运行 `python .\src\entrypoint.py`，确认全屏和滚轮稳定。 |
| PyCharm Run | 用同一解释器、同一代码的实际 Run 配置启动，确认全屏策略和可见效果。采集器启动不能代替此项。 |
| 打包程序 | 有对应打包产物时验证；没有则填 `S`，不要根据源码运行推定通过。 |
| 非对称裁剪＋拉直 | 真实媒体完成 20 次进出全屏，检查居中、完整显示和累积偏移。 |
| 窗口操作 | 最小化/恢复、Alt-Tab、任务栏、双击和 Esc 退出，确认原位置和正常/最大化状态恢复。 |
| Edit 系统最大化 | Edit 全屏后执行系统最大化，工具栏应恢复；再次进入/退出全屏，保留正确窗口状态。 |
| 视频与 Live Photo | 播放和切换，记录任何异常；已知 filmstrip 原生崩溃仍为独立问题。 |
| 100% / 150% / 250% DPI | 按机器实际可用设置分别测试；脚本不会自动更改系统缩放。没测的比例填 `S`。 |
| 不同 DPI 多显示器 | 跨屏移动后全屏、缩放、退出；只有一块屏幕时填 `S`。 |

每个 `P/F/S` 结果都会标记为 `user_attestation`。若想在填写前补测普通终端或 PyCharm，可以暂停在提示处，去启动应用测试，关闭后回来继续填写。

## 回传哪些产物

**回传终端最后打印的一个 ZIP 即可**，例如：

```text
iPhoto-windows-validation-20260923-153000-a1b2c3.zip
```

包中包含：

- `validation.json`：版本、自动检查状态、人工观察、备注、采集器校验结果和整体状态。
- `SUMMARY.md`：便于阅读的结果汇总；`CHECKLIST.md`：人工测试清单。
- `automatic/state-contracts/`：pytest 控制台日志与 JUnit XML。
- `automatic/overscan/`、`automatic/overscan_gl_state/`：schema 2 像素结果、逐帧事件和失败截图。
- `application/bundles/`：现有采集器的诊断 ZIP，包含系统/GPU 信息、线程栈、进程指标和应用日志。已封装的内部 ZIP 不会被改写或重复展开打包。
- 各阶段的 `.process.json`：启动参数、退出码、耗时和超时/中断状态。
- `manifest.json`：外层包中实际写入文件的字节数与 SHA-256。

脚本会检查采集器 manifest、GUI PID 一致性，并标记已采到的无响应、原生 fatal exception、匹配 PID 的 Windows Application Error、`R` 和强制停止记录。这是自动筛查，不等于根因诊断。历史上其他进程的系统事件不能据此归因到本次运行。

## 状态的含义

- **passed / 退出码 0：** 本机本次自动检查完整通过、人工项目均明确填写通过、应用采集正常结束，并有可关联的干净源码提交。仍不能代表其他驱动/显示器或整个 CI 已通过。
- **failed / 退出码 1：** 存在自动失败、记录到的运行异常、人工失败或执行错误。
- **incomplete / 退出码 2：** 有跳过/未测项、短于 20 次的 smoke、未知/脏源码版本，或者运行被中断。**这不一定是脚本故障，产物仍有研究价值，请照常回传。**

人工“失败”会覆盖自动像素“通过”；不完整、重复或旧版 HWND 截图结果不会被当成完整像素验收。没有设备条件的项目保持未验证，不会自动勾选。

## 可选用法

只做短自动检查，暂不打开真实应用（产物标为 incomplete）：

```powershell
python .\tools\validate_windows_fullscreen.py --cycles 3 --skip-app --non-interactive
```

指定输出目录、采集时长和需要脱敏的图库根目录：

```powershell
python .\tools\validate_windows_fullscreen.py --output-root D:\iPhoto-validation --max-minutes 45 --library-root-to-redact D:\Photos
```

真实应用阶段改为指定打包程序；自动探针仍运行当前源码，因此两类证据会分别标注：

```powershell
python .\tools\validate_windows_fullscreen.py --app-path "D:\Apps\iPhotron\entrypoint.exe"
```

默认每个自动探针最多运行 30 分钟，每个合约阶段最多 5 分钟。超时只结束脚本创建的对应进程树，不扫描关闭其他 iPhoto 实例。按一次 Ctrl+C 会尝试结束当前子进程并封装已完成/部分产物；如果直接杀掉验证脚本本身或在打包时再次中断，展开目录仍可用于恢复证据，不能保证 ZIP 完成。

## 2026-09-23 回传结果与复验重点

`iPhoto-windows-validation-20260923-102535-95784f.zip` 的外层 20 个文件与内层 8 个文件校验通过。运行版本为 `1346b613`，工作区标记为 dirty；三个工具的哈希与该版本的 CRLF 文件一致，其他本地变更未记录。环境为 Windows 11 build 26200、Qt/PySide 6.10.1，实际 OpenGL renderer 为 Intel Iris Xe（驱动 32.0.101.7088）；自动截图覆盖 3840×2400、250% DPI 的单屏。

本次结果保持 **failed**：

- 合约测试 54 通过、2 失败，均为原窗口最大化时的几何拒绝/原生回退场景，分别只尝试 2 次几何和未发出原生全屏请求。
- 两组像素探针各 839/840 样本通过。失败发生在 `crop/17/full/idle-0` 和 `straighten/9/full/idle-0`。截图中的绿色主图仍在，Windows 任务栏覆盖底部 120 个物理像素；下一次采样恢复，提交计数不变。不能把这两帧改记通过，也不能据此认定发生了黑屏重绘。
- 应用采集 PID 18004，47 条进程指标，约 49 秒正常退出，记录一次 overscan 进入和退出，未记录无响应/原生 fatal/强制停止。它不能证明整个人工矩阵或此前 filmstrip 崩溃已解决。
- 所有人工项原先填写 P，但用户随后指出可能有遗漏；保留原始记录，重新验收时对未实际执行的项目填 S。多屏、其他 DPI、打包版本和 PyCharm 的通过不应由本次单屏源码日志推定。

后续实现对两类问题分别处理：

1. 从最大化进入时，第一轮几何调整排队到 `showNormal()` 之后。进入期间的旧最大化反馈不会提前清除逻辑全屏；恢复与几何调整仍在三次尝试预算内，随后至多一次原生回退。全屏建立后用户主动最大化仍退出沉浸界面。测试保留三次/一次及最终状态断言，以有上限的事件等待代替固定十次 `processEvents()`。
2. 在调整 overscan 几何之前，通过 [ITaskbarList2::MarkFullscreenWindow](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-itaskbarlist2-markfullscreenwindow) 向 Explorer 声明全屏；显示/重新激活时重申，退出/失败/系统退出时撤销。它只声明活动窗口的 Shell 全屏意图，不改变 HWND、渲染后端或设置永久置顶。COM 失败记录 warning 与诊断事件，不能声称 Shell 已接受请求。

这些改动需要新的 Windows 产物确认，旧包不能作为修复后的通过证据。优先用以下命令复验自动失败项（仍是每种图 20 次）：

```powershell
python .\tools\validate_windows_fullscreen.py --skip-app --non-interactive
```

该命令因主动跳过人工/应用阶段而显示 `incomplete` 是预期行为；应检查三项自动检查是否全部通过，并回传完整 ZIP。新增合约 `detail_events.jsonl` 记录进入阶段、状态变化和尝试次数；像素样本记录活动窗口状态、窗口几何和单调时钟，探针/应用日志记录 `fullscreen_shell_mark` 请求及结果。合约测试会故意注入失败，与真实应用日志分开解释。

自动阶段通过后，再运行默认完整流程，特别核验：从最大化窗口进入全屏、全屏首帧任务栏遮挡、Alt-Tab、最小化恢复、Edit 中系统最大化，以及退出后的任务栏和窗口状态。保持原来的像素容差、采样次数与采样等待，不通过放宽阈值掩盖任务栏遮挡。
