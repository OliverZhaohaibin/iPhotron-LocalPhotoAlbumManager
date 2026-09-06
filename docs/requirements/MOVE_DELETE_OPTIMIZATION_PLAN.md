# 删除与移动媒体操作性能优化 — 未完成部分（pybind11 / C++ 加速层）

> **版本:** 1.0 | **日期:** 2026-02-14  
> **状态:** 🔮 未来需求  
> **已完成部分:** 见 `docs/finished/requirements/MOVE_DELETE_OPTIMIZATION_PYTHON.md`

> **实施门禁：性能判断基于 2026-02 的历史 profiling。** 实施前必须重新测量
> 当前 `LibraryAssetOperationService -> MoveWorker -> LibraryAssetLifecycleService`
> 完整路径。只有目标平台数据仍证明 Python 调度、ExifTool 启动、文件操作或缩略图
> 解码是主要瓶颈时，才能采用本文对应的 native rewrite 建议。本文的收益排序、耗时
> 和倍数均不是当前性能合同。

---

## 概述

纯 Python 架构优化（方案一至四）已实施完成。2026-02 的原始方案曾预计它们可
解决 80% 以上的性能问题，但该估算尚未针对当前 lifecycle 和 move service 路径重新
验证。本文档仅保留方案五（pybind11 / C++ 加速层）作为未来极限优化输入。

> **建议：** 优先验证方案一至四的效果，仅在纯 Python 优化无法满足需求时再考虑 C++ 加速层。

---

## 目录

1. [适用场景分析](#1-适用场景分析)
2. [推荐的 C++ 加速模块](#2-推荐的-c-加速模块)
3. [集成方式](#3-集成方式)
4. [构建配置](#4-构建配置)
5. [预期收益](#5-预期收益)
6. [成本与风险](#6-成本与风险)
7. [实施路线图](#7-实施路线图)

---

## 1. 适用场景分析

| 历史候选瓶颈 | C++ 能否加速 | 历史收益假设（需重新 profiling） |
|-------------|-------------|---------|
| ExifTool 子进程启动 | ✅ 使用 libexiv2 内嵌替代 | 🔴 高（消除进程启动开销） |
| shutil.move 文件操作 | ✅ 批量 rename() 无 GIL | 🟡 中等（减少 GIL 竞争） |
| JSON 解析/序列化 | ✅ rapidjson/simdjson | 🟢 低（不是主要瓶颈） |
| 微缩略图生成 | ✅ libjpeg-turbo/libvips | 🟡 中等（Pillow draft 已较优） |
| SQLite 操作 | ❌ Python sqlite3 已是 C 扩展 | 🟢 低 |
| Qt 信号/槽 | ❌ 已在 C++ 层 | 🟢 无 |

---

## 2. 推荐的 C++ 加速模块

### 2.1 模块一：`iphoto_native.file_ops` — 批量文件操作

```cpp
// file_ops.cpp
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <filesystem>
#include <stdexcept>
#include <system_error>

namespace py = pybind11;
namespace fs = std::filesystem;

struct MoveResult {
    std::string source;
    std::string target;
    bool success;
    std::string error;
};

/**
 * 批量移动文件，释放 GIL 以避免阻塞 Python 主线程。
 * 使用 std::filesystem::rename 实现零拷贝移动。
 * 注意：rename 仅在同分区内为零拷贝；跨分区时自动回退到拷贝+删除。
 */
std::vector<MoveResult> batch_move(
    const std::vector<std::string>& sources,
    const std::string& destination_dir,
    bool handle_collisions = true
) {
    std::vector<MoveResult> results;
    results.reserve(sources.size());

    // 释放 GIL
    py::gil_scoped_release release;

    fs::path dest(destination_dir);
    fs::create_directories(dest);

    for (const auto& src_str : sources) {
        MoveResult r;
        r.source = src_str;
        try {
            fs::path src(src_str);
            fs::path target = dest / src.filename();

            if (handle_collisions) {
                int counter = 1;
                auto stem = target.stem().string();
                auto ext = target.extension().string();
                while (fs::exists(target)) {
                    target = dest / (stem + " (" + std::to_string(counter++) + ")" + ext);
                }
            }

            fs::rename(src, target);  // 同分区内为零拷贝；跨分区抛出异常
            r.target = target.string();
            r.success = true;
        } catch (const fs::filesystem_error& e) {
            // 只有 cross-device rename 才能按 move 语义回退。权限、只读文件系统等
            // 其他错误必须原样失败，不能用 copy+delete 掩盖。
            if (e.code() != std::errc::cross_device_link) {
                r.success = false;
                r.error = e.what();
                results.push_back(std::move(r));
                continue;
            }
            try {
                fs::path src(src_str);
                fs::path target = dest / src.filename();

                // 确保不覆盖已有文件，防止数据丢失
                if (handle_collisions) {
                    int counter = 1;
                    auto stem = target.stem().string();
                    auto ext = target.extension().string();
                    while (fs::exists(target)) {
                        target = dest / (stem + " (" + std::to_string(counter++) + ")" + ext);
                    }
                }

                // copy 成功后才能删除源文件。生产实现还必须接入现有 operation
                // journal/lifecycle，处理 copy 成功但 remove 失败后的恢复。
                fs::copy_file(src, target, fs::copy_options::none);
                if (!fs::remove(src)) {
                    throw std::runtime_error("source was copied but could not be removed");
                }
                r.target = target.string();
                r.success = true;
            } catch (const std::exception& e2) {
                r.success = false;
                r.error = e2.what();
            }
        }
        results.push_back(std::move(r));
    }
    return results;
}

PYBIND11_MODULE(file_ops, m) {
    py::class_<MoveResult>(m, "MoveResult")
        .def_readonly("source", &MoveResult::source)
        .def_readonly("target", &MoveResult::target)
        .def_readonly("success", &MoveResult::success)
        .def_readonly("error", &MoveResult::error);

    m.def("batch_move", &batch_move,
          py::arg("sources"),
          py::arg("destination_dir"),
          py::arg("handle_collisions") = true,
          "批量移动文件，同分区内使用零拷贝 rename");
}
```

### 2.2 模块二：`iphoto_native.metadata` — 内嵌元数据提取

```cpp
// metadata.cpp — 使用 libexiv2 替代 ExifTool 子进程
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <exiv2/exiv2.hpp>

namespace py = pybind11;

/**
 * 批量提取元数据，不启动子进程。
 * 在 C++ 侧完成，释放 GIL 以不阻塞 UI。
 */
std::vector<std::map<std::string, std::string>> batch_get_metadata(
    const std::vector<std::string>& paths
) {
    std::vector<std::map<std::string, std::string>> results;
    results.reserve(paths.size());

    py::gil_scoped_release release;

    for (const auto& path : paths) {
        std::map<std::string, std::string> meta;
        try {
            auto image = Exiv2::ImageFactory::open(path);
            image->readMetadata();

            const auto& exifData = image->exifData();
            // 提取关键字段
            auto get = [&](const char* key) -> std::string {
                auto it = exifData.findKey(Exiv2::ExifKey(key));
                return it != exifData.end() ? it->toString() : "";
            };

            meta["width"] = get("Exif.Photo.PixelXDimension");
            meta["height"] = get("Exif.Photo.PixelYDimension");
            meta["make"] = get("Exif.Image.Make");
            meta["model"] = get("Exif.Image.Model");
            meta["datetime"] = get("Exif.Photo.DateTimeOriginal");
            meta["gps_lat"] = get("Exif.GPSInfo.GPSLatitude");
            meta["gps_lon"] = get("Exif.GPSInfo.GPSLongitude");
            meta["iso"] = get("Exif.Photo.ISOSpeedRatings");
            meta["f_number"] = get("Exif.Photo.FNumber");
            meta["exposure_time"] = get("Exif.Photo.ExposureTime");
            meta["focal_length"] = get("Exif.Photo.FocalLength");
            meta["orientation"] = get("Exif.Image.Orientation");
        } catch (...) {
            meta["error"] = "Failed to read metadata";
        }
        results.push_back(std::move(meta));
    }
    return results;
}

PYBIND11_MODULE(metadata, m) {
    m.def("batch_get_metadata", &batch_get_metadata,
          py::arg("paths"),
          "批量提取 EXIF 元数据，无需 ExifTool 子进程");
}
```

---

## 3. 集成方式

```
src/
├── iPhoto/
│   ├── native/                    # 新增 C++ 加速层
│   │   ├── CMakeLists.txt
│   │   ├── file_ops.cpp
│   │   ├── metadata.cpp
│   │   └── __init__.py            # 提供 Python 回退
│   └── ...
```

**Python 回退策略（graceful degradation）：**

```python
# src/iPhoto/native/__init__.py
try:
    from .file_ops import batch_move
    from .metadata import batch_get_metadata
    NATIVE_AVAILABLE = True
except ImportError:
    NATIVE_AVAILABLE = False

    def batch_move(sources, destination_dir, handle_collisions=True):
        """Python 回退实现。"""
        import shutil
        from pathlib import Path
        # ... 现有 shutil.move 逻辑 ...

    def batch_get_metadata(paths):
        """Python 回退实现。"""
        from ..infrastructure.services.metadata_provider import ExifToolMetadataProvider
        provider = ExifToolMetadataProvider()
        return provider.get_metadata_batch([Path(p) for p in paths])
```

---

## 4. 构建配置

```toml
# pyproject.toml 新增
[build-system]
requires = ["setuptools", "pybind11>=2.12"]

[tool.setuptools.ext-modules]
# file_ops 仅需 C++17 标准库，无外部依赖
iphoto_native_file_ops = {sources = ["src/iPhoto/native/file_ops.cpp"]}
# metadata 模块需要链接 libexiv2
iphoto_native_metadata = {
    sources = ["src/iPhoto/native/metadata.cpp"],
    libraries = ["exiv2"],
}
```

---

## 5. 预期收益

下表是 2026-02 的历史估算，只能用于确定重新 profiling 的候选场景，不能作为当前
实现的验收目标或 native rewrite 的立项依据。

| 模块 | Python 耗时 | C++ 耗时 | 加速比 |
|------|------------|---------|--------|
| 批量移动 20 文件 | 100ms | 20ms | 5× |
| 元数据提取 20 文件 | 200-400ms | 30-60ms | 5-7× |
| 微缩略图生成 20 张 | 200-600ms | 50-100ms | 4-6× |

---

## 6. 成本与风险

| 方面 | 评估 |
|------|------|
| 开发成本 | 🟡 中等（需要 C++ 开发经验） |
| 构建复杂度 | 🔴 显著增加（需要 CMake + 编译器工具链） |
| 跨平台兼容 | 🟡 需要 macOS/Windows/Linux 分别编译 |
| 分发体积 | 🟡 增加 2-5MB 二进制 |
| 维护成本 | 🔴 双语言维护，调试复杂度增加 |
| 回退能力 | ✅ Python fallback 保证功能不受影响 |
| C++ 编译环境不一致 | 🔴 部分用户无法使用 — Python fallback 必须完整 |

---

## 7. 实施路线图

### 阶段三：极限优化（可选，3-4 周）

```
优先级  方案                              历史预计耗时  历史收益假设
──────────────────────────────────────────────────────────
P3     模块一: C++ 批量文件操作            2 周     5× 加速
P3     模块二: C++ 元数据提取              2 周     5-7× 加速
P3     C++ 缩略图解码                      1 周     4-6× 加速
```

**目标：** 万级文件操作毫秒级响应

---

> **注意：** 本文档中的 C++ 代码仅为设计参考，尚未实施，也不得绕过当前
> `LibraryAssetOperationService`、`MoveWorker`、`LibraryAssetLifecycleService`
> 及其 durable-state/recovery 边界。实施前必须先完成当前路径的目标平台 profiling。
