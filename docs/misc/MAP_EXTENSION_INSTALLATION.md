# 地图扩展安装与下载恢复

修复版的设置菜单提供地图扩展入口，无需先打开地图页面。
Windows 和 Linux 支持以下手动安装方式：

- **下载**：使用当前环境变量或系统代理配置。失败后“重试”会重新读取配置。
- **用浏览器下载**：浏览器下载完成后，回到该入口选择“从本地安装”。
- **从本地安装**：选择官方 ZIP（Windows）或 tar.xz（Linux），无需联网。
- **尝试直连**：仅在下载失败后由用户选择，只对这一次下载生效，不修改系统代理。

macOS 的地图扩展随签名应用提供，首次使用地图或位置面板时自动准备，
不提供手动导入 ZIP/tar.xz 的入口。设置菜单会显示这一说明。

正常 Windows 安装目录是：

```text
%LOCALAPPDATA%\iPhoto\extensions\maps\v1\tiles\extension
```

程序位于 `Program Files` 也不需要管理员权限。若设置了
`IPHOTO_OSMAND_EXTENSION_ROOT`，它会覆盖默认写入位置；文件夹不可写时，
请移除不需要的覆盖或将其改到当前用户可写目录。

安装已成功激活时，进度窗口直接关闭，不要求重启。
“已暂存、等待重启”表示安装包已验证，但尚未激活。重启后首次使用地图或
位置面板时，应用在后台恢复安装。文件占用时保留旧地图和待安装文件；不要
手动删除正在使用的 DLL。下载成功而安装失败时，应用保留完整压缩包供重试。

## 如何理解报错

- `WinError 5`：目标目录权限或文件占用问题。检查“详细信息”中的安装位置。
- `WinError 10061`：连接被拒绝，可能发生在代理或下载服务器连接处；不能
  单凭错误码确定原因。浏览器与应用可能使用不同代理配置。
- 校验失败：文件损坏、不完整或不在当前应用支持的官方包清单中，请重新下载。
- 不兼容：平台、原生架构或扩展兼容版本不匹配。不要改名来绕过检查。

## 旧版临时离线安装

以下配置已在 **v6.6.8 标签源码**中确认；未知历史二进制版本不保证支持。

1. 从官方 Release 下载 [Windows extension.zip](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v5.0.0/extension.zip)。
2. 关闭 iPhotron，将 ZIP 解压到上述用户目录的父目录，确保存在
   `extension\World_basemap_2.obf`、`extension\search\geonames.sqlite3` 和
   `extension\bin\osmand_render_helper.exe`，避免多套一层 `extension`。
3. 在 Windows“编辑当前用户的环境变量”中添加
   `IPHOTO_OSMAND_EXTENSION_ROOT`，值为 **extension 文件夹的完整绝对路径**。
   不要使用相册路径，也不要把路径写到系统级环境变量中。
4. 从开始菜单重新启动应用。如启动器仍继承旧环境，退出并重新启动启动器。

官方 ZIP 大小：538,920,928 字节（约 514 MiB）。SHA-256：

```text
bebc4885c8c96c82f5701c5ffc6bb064ae53b6e9c42e523abdfe1c6d945506b8
```

可以在 PowerShell 使用 `Get-FileHash -Algorithm SHA256 -LiteralPath '完整ZIP路径'`
核对。请使用官方二进制扩展包，GitHub 的 Source code ZIP 不是地图扩展。

## English quick guide

On Windows and Linux, open Map Extension in Settings, then choose Download,
Download in Browser, or Install from File. A local official archive can be
installed completely offline. On macOS, the extension is included with the
signed app and prepared automatically on first use; manual archive import is
not offered. An activated installation closes the progress window without a
restart prompt.
Windows uses `%LOCALAPPDATA%\iPhoto\extensions\maps\v1\tiles\extension`.
Retry refreshes system proxy settings; Try Direct Connection affects only the
current download. A staged extension still requires restart before activation.

Report the app version, `build-manifest.json` source revision, error category,
OS error code and installation stage from Details. Diagnostics omit proxy
credentials and signed URL query strings. Review local paths before sharing.
