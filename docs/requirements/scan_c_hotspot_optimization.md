# 扫描阶段 C 语言热点代码优化分析

> **目的**：识别扫描（Scan）阶段中可以用 C 语言重写以大幅提升性能的热点代码，并给出优先级排序、预期收益与实施建议。

---

## 1. 扫描流程概览

iPhotos 的扫描流程分为以下几个阶段：

```
文件发现
  └─ FileDiscoveryThread.run()  [scan_album.py]
  └─ ParallelScanner._discover_files()  [parallel_scanner.py]
          ↓
批量元数据提取
  └─ get_metadata_batch()  [utils/exiftool.py]
  └─ normalize_metadata()  [infrastructure/services/metadata_provider.py]
          ↓
文件内容哈希（去重）
  └─ compute_file_id()  [utils/hashutils.py]
          ↓
缩略图生成（微缩图）
  └─ generate_micro_thumbnail()  [utils/image_loader.py]
          ↓
Live Photo 配对
  └─ pair_live() / _match_by_time()  [core/pairing.py]
          ↓
数据库持久化
  └─ append_rows() / _insert_rows()  [cache/index_store/repository.py]
```

扫描 5,000 个文件的各阶段耗时估算：

| 阶段 | 估算耗时 | 占比 | 主要瓶颈 |
|------|---------|------|---------|
| 文件发现 | 2–5 s | ~2% | `os.walk` I/O |
| 元数据提取 | 10–50 s | ~15% | ExifTool 子进程启动 |
| 文件哈希 | 5–30 s | ~10% | 磁盘 I/O + Python 循环 |
| 微缩图生成 | 5–30 s | ~10% | 图像解码 |
| **Live Photo 配对** | **50–300 s** | **~50%** | **datetime 解析在内层循环** |
| 数据库写入 | 5–25 s | ~5% | 事务批量过小（chunk=10） |

---

## 2. 热点代码详细分析

### 🔴 热点 1：ISO 8601 日期时间解析（**最高优先级**）

**文件：** `src/iPhoto/core/pairing.py`

**函数：**

```python
def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parser.isoparse(value)   # ← 热点：每次调用约 1–5 ms
    except (ValueError, TypeError):
        return None

def _match_by_time(photo, candidates, used_videos):
    photo_dt = _parse_dt(photo.get("dt"))       # ← 调用 1 次
    for candidate in candidates:
        video_dt = _parse_dt(candidate.get("dt"))  # ← 每个候选视频调用 1 次
        if not photo_dt or not video_dt:
            continue
        delta = abs((photo_dt - video_dt).total_seconds())
        ...
```

**为何是热点：**

- `dateutil.parser.isoparse()` 是纯 Python 实现，每次调用约 1–5 ms。
- `_match_by_time` 被 `pair_live()` 中的两个循环（茎名匹配、目录匹配）反复调用，内层再对每个候选视频调用 `_parse_dt`。
- 对于 100,000 张照片的库（1,000 个视频，每张照片平均 5 个候选）：
  - 解析次数：~5,000 次
  - 总耗时：~5,000 × 3 ms = **15 秒**仅在匹配循环内，加上外层调用可达 50–300 s。

**C 实现方案：**

```c
/* scan_utils.c */

#define _GNU_SOURCE   /* timegm() — Linux/glibc; BSDs 无需此宏 */
#include <time.h>
#include <string.h>
#include <stdint.h>
#include <limits.h>

/* 辅助：从字符串 s 读取恰好 n 位十进制整数（不带符号） */
static int read_n_digits(const char *s, int n) {
    int v = 0;
    for (int i = 0; i < n; i++) {
        if (s[i] < '0' || s[i] > '9') return -1;   /* 错误：非数字 */
        v = v * 10 + (s[i] - '0');
    }
    return v;
}

/* 辅助：解析亚秒小数部分，归一化为微秒；*endp 指向解析结束后的位置 */
static int parse_subsec_us(const char *s, const char **endp) {
    int digits = 0, us = 0;
    while (digits < 6 && s[digits] >= '0' && s[digits] <= '9') {
        us = us * 10 + (s[digits] - '0');
        digits++;
    }
    /* 补零至 6 位精度 */
    for (int i = digits; i < 6; i++) us *= 10;
    *endp = s + digits;
    /* 跳过超出 6 位的多余数字 */
    while (**endp >= '0' && **endp <= '9') (*endp)++;
    return us;
}

/**
 * parse_iso8601_to_unix_us
 *
 * 将 ISO 8601 字符串（如 "2024-03-15T10:30:00Z" 或 "2024-03-15T10:30:00+08:00"）
 * 解析为 Unix 微秒时间戳，失败返回 INT64_MIN。
 *
 * 格式支持：
 *   YYYY-MM-DDTHH:MM:SS[.ffffff][Z|±HH:MM]
 *
 * 移植说明：timegm() 是 GNU/BSD 扩展，非 POSIX 标准。
 * 若需要 POSIX 可移植性，可改为：将 struct tm 的 tm_isdst=-1，
 * 用 mktime() 获得本地时间后加上 timezone 偏移量。
 */
int64_t parse_iso8601_to_unix_us(const char *s) {
    if (!s || strlen(s) < 19) return INT64_MIN;

    struct tm t = {0};
    t.tm_year = read_n_digits(s,    4) - 1900;  /* YYYY */
    t.tm_mon  = read_n_digits(s+5,  2) - 1;     /* MM   */
    t.tm_mday = read_n_digits(s+8,  2);         /* DD   */
    t.tm_hour = read_n_digits(s+11, 2);         /* HH   */
    t.tm_min  = read_n_digits(s+14, 2);         /* MM   */
    t.tm_sec  = read_n_digits(s+17, 2);         /* SS   */

    /* 基本范围验证 */
    if (t.tm_year < 0 || t.tm_mon < 0 || t.tm_mday < 1 ||
        t.tm_hour < 0 || t.tm_min < 0 || t.tm_sec < 0)
        return INT64_MIN;

    const char *p = s + 19;
    int us = 0;
    int tz_offset_sec = 0;

    /* 可选亚秒部分 */
    if (*p == '.') { p++; us = parse_subsec_us(p, &p); }

    /* 时区 */
    if (*p == 'Z' || *p == 'z') {
        tz_offset_sec = 0;
    } else if (*p == '+' || *p == '-') {
        int sign = (*p++ == '+') ? 1 : -1;
        int hh = read_n_digits(p, 2);
        int mm = (strlen(p) >= 5) ? read_n_digits(p + 3, 2) : 0;
        if (hh < 0 || mm < 0) return INT64_MIN;
        tz_offset_sec = sign * (hh * 3600 + mm * 60);
    }

    /* timegm() 将 struct tm（UTC）转为 time_t，不受本地时区影响 */
    time_t epoch = timegm(&t);
    if (epoch == (time_t)-1) return INT64_MIN;

    return (int64_t)(epoch - tz_offset_sec) * 1000000LL + us;
}
```

**Python ctypes 调用示例：**

```python
# src/iPhoto/_native/scan_utils.py
import ctypes, os, pathlib

_lib = ctypes.CDLL(str(pathlib.Path(__file__).parent / "_scan_utils.so"))
_lib.parse_iso8601_to_unix_us.restype = ctypes.c_int64
_lib.parse_iso8601_to_unix_us.argtypes = [ctypes.c_char_p]
_INT64_MIN = -2**63

def parse_dt_fast(value: str | None) -> int | None:
    """返回 Unix 微秒时间戳，解析失败返回 None。"""
    if not value:
        return None
    result = _lib.parse_iso8601_to_unix_us(value.encode())
    return None if result == _INT64_MIN else result
```

**预期加速：10–50×**（每次调用 0.05–0.1 ms vs. 原来 1–5 ms）
**对总扫描时间的影响：Live Photo 配对阶段可减少 80–95%**

---

### 🔴 热点 2：文件内容哈希（去重标识）

**文件：** `src/iPhoto/utils/hashutils.py`

**函数：**

```python
def compute_file_id(path: Path) -> str:
    threshold = 2 * 1024 * 1024   # 2 MB

    with path.open("rb") as f:
        file_size = os.fstat(f.fileno()).st_size

        if file_size <= threshold:
            # 小文件：整体哈希，Python 循环读取 1MB 块
            hasher = xxhash.xxh3_128()
            chunk_size = 1024 * 1024
            while True:
                chunk = f.read(chunk_size)   # ← Python bytes 对象分配
                if not chunk:
                    break
                hasher.update(chunk)
            return hasher.hexdigest()

        # 大文件：采样哈希（头 + 中 + 尾 各 256KB）
        hasher = xxhash.xxh3_128()
        hasher.update(file_size.to_bytes(8, "little"))
        hasher.update(f.read(256 * 1024))       # Head
        f.seek(file_size // 2 - 131072)
        hasher.update(f.read(256 * 1024))       # Middle
        f.seek(max(0, file_size - 256 * 1024))
        hasher.update(f.read(256 * 1024))       # Tail
        return hasher.hexdigest()
```

**为何是热点：**

- 扫描中**每个文件**都要调用（100% 覆盖）。
- 小文件 Python 循环：每次 `f.read()` 创建 Python `bytes` 对象，有 GIL 保持 + 引用计数开销。
- 大文件每次 `f.seek()` + `f.read()` 均经过 Python 文件对象层。
- `xxhash.xxh3_128()` 本身已是 C 扩展，但 Python 调用链开销仍不可忽略。

**C 实现方案（`mmap` + 直接哈希）：**

```c
/* scan_utils.c */

#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdint.h>
#include <string.h>
#include "xxhash.h"          /* xxHash 头文件（vendored 或系统库） */

#define THRESHOLD      (2 * 1024 * 1024)
#define CHUNK_256K     (256 * 1024)

/* 辅助：循环读取直到满足 count 字节或 EOF；返回实际读取字节数 */
static ssize_t pread_full(int fd, void *buf, size_t count, off_t offset) {
    ssize_t total = 0;
    while ((size_t)total < count) {
        ssize_t n = pread(fd, (char *)buf + total, count - (size_t)total,
                          offset + total);
        if (n <= 0) break;   /* EOF 或错误 */
        total += n;
    }
    return total;
}

/**
 * compute_file_id_c
 *
 * 计算文件的 128-bit XXH3 哈希（小文件完整、大文件采样）。
 * 输出写入 out_hex（至少 33 字节），成功返回 0，失败返回 -1。
 *
 * 注意：out_hex 格式为 "low64_hexhigh64_hex"（小端 64 位拼接），
 * 若需与 Python xxhash.xxh3_128().hexdigest() 对齐，请验证字节序后调整。
 */
int compute_file_id_c(const char *path, char *out_hex) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;

    struct stat st;
    if (fstat(fd, &st) < 0) { close(fd); return -1; }
    off_t size = st.st_size;

    XXH3_state_t *state = XXH3_createState();
    if (!state) { close(fd); return -1; }
    XXH3_128bits_reset(state);

    int ok = 0;

    if (size > 0 && size <= THRESHOLD) {
        /* 小文件：mmap 后一次性哈希，无 Python 循环开销 */
        void *data = mmap(NULL, (size_t)size, PROT_READ, MAP_PRIVATE, fd, 0);
        if (data == MAP_FAILED) { ok = -1; goto cleanup; }
        madvise(data, (size_t)size, MADV_SEQUENTIAL);
        XXH3_128bits_update(state, data, (size_t)size);
        munmap(data, (size_t)size);
    } else if (size > THRESHOLD) {
        /* 大文件：采样头 + 中 + 尾，混入文件大小 */
        uint8_t buf[CHUNK_256K];
        uint64_t size_le = (uint64_t)size;
        XXH3_128bits_update(state, &size_le, 8);

        ssize_t n;

        /* Head */
        n = pread_full(fd, buf, CHUNK_256K, 0);
        if (n < 0) { ok = -1; goto cleanup; }
        XXH3_128bits_update(state, buf, (size_t)n);

        /* Middle */
        off_t mid = (size / 2) - (CHUNK_256K / 2);
        if (mid < 0) mid = 0;
        n = pread_full(fd, buf, CHUNK_256K, mid);
        if (n < 0) { ok = -1; goto cleanup; }
        XXH3_128bits_update(state, buf, (size_t)n);

        /* Tail */
        off_t tail_off = (size > (off_t)CHUNK_256K) ? (size - CHUNK_256K) : 0;
        n = pread_full(fd, buf, CHUNK_256K, tail_off);
        if (n < 0) { ok = -1; goto cleanup; }
        XXH3_128bits_update(state, buf, (size_t)n);
    }
    /* size == 0：哈希空内容，结果为全零摘要 */

    {
        XXH128_hash_t result = XXH3_128bits_digest(state);
        /*
         * xxhash Python 库的 hexdigest() 输出为大端序（高 64 位在前）：
         *   high64_hex || low64_hex
         * 此处与 Python 实现保持一致。
         */
        snprintf(out_hex, 33, "%016llx%016llx",
                 (unsigned long long)result.high64,
                 (unsigned long long)result.low64);
    }

cleanup:
    XXH3_freeState(state);
    close(fd);
    return ok;
}
```

**关键优化点：**

| 优化 | 说明 |
|------|------|
| `mmap` 替代 `f.read()` 循环 | 避免 Python `bytes` 对象分配；操作系统页面缓存直接传给哈希函数 |
| `madvise(MADV_SEQUENTIAL)` | 小文件触发预读，减少缺页中断 |
| `pread()` 替代 `lseek+read` | 原子操作，无需额外 seek 调用 |
| 无 GIL 持有 | 整个 C 函数可在 `Py_BEGIN_ALLOW_THREADS` 块中运行 |

**预期加速：3–8×**（消除 Python 循环和 bytes 对象分配）
**对总扫描时间的影响：哈希阶段减少 60–75%**

---

### 🟡 热点 3：Glob 路径过滤（文件发现）

**文件：** `src/iPhoto/utils/pathutils.py`

**函数：**

```python
def should_include(path: Path, include_globs, exclude_globs, *, root: Path) -> bool:
    if is_excluded(path, exclude_globs, root=root):
        return False
    rel = path.relative_to(root).as_posix()
    for pattern in include_globs:
        for expanded in _expand_cached(pattern):
            if fnmatch.fnmatch(rel, expanded):     # ← 热点
                return True
            if expanded.startswith("**/") and fnmatch.fnmatch(rel, expanded[3:]):
                return True
    return False
```

**为何是热点：**

- 在 `FileDiscoveryThread.run()` 中，**目录树中每个文件**都调用一次（通过 `os.walk`）。
- `fnmatch.fnmatch()` 在 CPython 中有 C 加速，但仍需要进入 Python 帧，加上 `_expand_cached` 的 tuple 迭代。
- `path.relative_to(root).as_posix()` 每次调用构造一个 `Path` 对象和一个字符串，有内存分配开销。
- 对于 100,000 文件的库，此函数调用次数高达 100,000+。

**C 实现方案：**

```c
/* scan_utils.c */

#include <fnmatch.h>
#include <string.h>
#include <stdlib.h>

/**
 * should_include_c
 *
 * 检查相对路径 rel_path 是否匹配 include_globs 中的任意模式，
 * 且不匹配 exclude_globs 中的任意模式。
 *
 * globs 为 NULL 结尾的字符串数组。
 * 返回 1 表示应包含，0 表示排除。
 */
int should_include_c(
    const char *rel_path,
    const char **include_globs,
    const char **exclude_globs
) {
    /* 排除检查 */
    for (int i = 0; exclude_globs[i] != NULL; i++) {
        const char *pat = exclude_globs[i];
        if (fnmatch(pat, rel_path, FNM_PATHNAME) == 0)
            return 0;
        /* 支持 **/ 前缀 */
        if (strncmp(pat, "**/", 3) == 0) {
            if (fnmatch(pat + 3, rel_path, FNM_PATHNAME) == 0)
                return 0;
        }
    }
    /* 包含检查 */
    for (int i = 0; include_globs[i] != NULL; i++) {
        const char *pat = include_globs[i];
        if (fnmatch(pat, rel_path, FNM_PATHNAME) == 0)
            return 1;
        if (strncmp(pat, "**/", 3) == 0) {
            if (fnmatch(pat + 3, rel_path, FNM_PATHNAME) == 0)
                return 1;
        }
    }
    return 0;
}
```

**注意：** `{a,b}` 花括号扩展逻辑（`_expand()`）也需要在 C 中实现，或在 Python 端预展开后传入展平的 glob 列表。

**预期加速：2–5×**
**对总扫描时间的影响：文件发现阶段减少 40–60%**

---

### 🟡 热点 4：目录遍历扩展支持（文件类型过滤）

**文件：** `src/iPhoto/application/services/parallel_scanner.py`

**函数：**

```python
def _discover_files(self, path: Path) -> Generator[Path, None, None]:
    for entry in os.scandir(path):
        if self._cancelled.is_set():
            return
        if entry.is_file(follow_symlinks=False) and self._is_supported(entry.name):
            yield Path(entry.path)
        elif entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
            yield from self._discover_files(Path(entry.path))

@staticmethod
def _is_supported(filename: str) -> bool:
    _, _, ext = filename.rpartition(".")
    return f".{ext.lower()}" in _SUPPORTED_EXTENSIONS if ext else False
```

**为何是热点：**

- Python 生成器递归调用（`yield from self._discover_files(...)`）有函数栈帧开销。
- `entry.name.rpartition(".")` + 字符串小写化 + frozenset 查找：每个文件约 1–2 μs。
- 100,000 文件 × 2 μs = 0.2 s（独立看不多，但与其他阶段叠加会影响总时延）。

**C 实现方案（POSIX `nftw`）：**

```c
/* scan_utils.c */

#define _GNU_SOURCE
#include <ftw.h>
#include <string.h>
#include <strings.h>   /* strcasecmp */

static const char *SUPPORTED_EXT[] = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif",
    ".mov", ".mp4", ".m4v", ".qt", ".avi", ".mkv",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf",
    NULL
};

/* 回调函数指针（供 Python 层注册） */
typedef void (*FileFoundCallback)(const char *path, void *userdata);
static FileFoundCallback g_callback = NULL;
static void *g_userdata = NULL;

static int _nftw_cb(const char *fpath, const struct stat *sb,
                    int typeflag, struct FTW *ftwbuf) {
    const char *base = fpath + ftwbuf->base;

    /* 跳过以 '.' 开头的隐藏目录（含其所有子树） */
    if ((typeflag == FTW_D || typeflag == FTW_DNR) && base[0] == '.')
        return FTW_SKIP_SUBTREE;   /* 需要 FTW_ACTIONRETVAL 标志 */

    /* 只处理普通文件 */
    if (typeflag != FTW_F) return FTW_CONTINUE;

    /* 提取扩展名并与支持列表比对 */
    const char *dot = strrchr(base, '.');
    if (!dot) return FTW_CONTINUE;

    for (int i = 0; SUPPORTED_EXT[i]; i++) {
        if (strcasecmp(dot, SUPPORTED_EXT[i]) == 0) {
            if (g_callback) g_callback(fpath, g_userdata);
            return FTW_CONTINUE;
        }
    }
    return FTW_CONTINUE;
}

/**
 * discover_files_c
 *
 * 递归扫描 root_dir，对每个支持的媒体文件调用 callback(path, userdata)。
 * 使用 nftw + FTW_ACTIONRETVAL 以支持 FTW_SKIP_SUBTREE 跳过隐藏目录。
 */
void discover_files_c(const char *root_dir,
                      FileFoundCallback callback,
                      void *userdata) {
    g_callback = callback;
    g_userdata = userdata;
    nftw(root_dir, _nftw_cb, 64,
         FTW_PHYS           /* 不跟随符号链接 */
         | FTW_ACTIONRETVAL /* 启用 FTW_SKIP_SUBTREE / FTW_CONTINUE 返回值 */
    );
}
```

**预期加速：2–4×（文件发现阶段）**

---

### 🟡 热点 5：元数据归一化中的时间戳解析

**文件：** `src/iPhoto/infrastructure/services/metadata_provider.py`

**函数片段：**

```python
def normalize_metadata(self, root, file_path, raw_metadata):
    stat = file_path.stat()
    row = {
        ...
        "dt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                      .isoformat().replace("+00:00", "Z"),
        "ts": int(stat.st_mtime * 1_000_000),
        ...
    }
    # 修正 dt/ts
    if "dt" in processed_meta:
        dt_str = processed_meta["dt"].replace("Z", "+00:00")
        dt_obj = datetime.fromisoformat(dt_str)    # ← 字符串解析
        row["ts"] = int(dt_obj.timestamp() * 1_000_000)

    # 计算 year/month
    if "dt" in row:
        dt_str = row["dt"].replace("Z", "+00:00")
        dt_obj = datetime.fromisoformat(dt_str)    # ← 再次解析同一字符串
        row["year"] = dt_obj.year
        row["month"] = dt_obj.month
```

**为何是热点：**

- `datetime.fromisoformat()` **对同一字符串解析两次**（ts 修正 + year/month 计算）。
- 每个文件调用一次 `normalize_metadata`，100,000 文件 × 2 次解析 = 200,000 次解析。
- 可直接复用热点 1 的 `parse_iso8601_to_unix_us` C 函数，同时返回年、月、微秒时间戳。

**C 实现扩展：**

```c
/* 扩展版本，同时输出 year / month */
typedef struct {
    int64_t unix_us;   /* Unix 微秒时间戳 */
    int     year;
    int     month;
} DateTimeResult;

DateTimeResult parse_iso8601_full(const char *s);
```

**预期加速：** 将 normalize_metadata 中的日期处理开销减少 70–80%，
**对总扫描时间的影响：** 元数据归一化阶段减少 30–50%

---

### 🟢 热点 6：Live Photo 内容 ID 归一化

**文件：** `src/iPhoto/core/pairing.py`

**函数：**

```python
def _normalise_content_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed.casefold()
```

**为何是热点：**

- 在 `pair_live()` 中对所有照片和视频各调用一次，然后在 `defaultdict` 构建循环中再次调用。
- Python 的 `.strip()` 和 `.casefold()` 都创建新的字符串对象（堆分配）。
- 对于 100,000 资产库：200,000 次字符串操作。

**C 实现方案：**

```c
/**
 * normalise_content_id_inplace
 *
 * 原地修改字符串：去除首尾空白，转为小写。
 * 若结果为空字符串，将第一个字节设为 '\0'。
 * 返回修改后的长度（0 表示空）。
 */
int normalise_content_id_inplace(char *s) {
    if (!s) return 0;
    /* 去尾空白 */
    int len = strlen(s);
    while (len > 0 && (s[len-1] == ' ' || s[len-1] == '\t' ||
                       s[len-1] == '\n' || s[len-1] == '\r'))
        s[--len] = '\0';
    /* 去首空白 */
    int start = 0;
    while (start < len && (s[start] == ' ' || s[start] == '\t'))
        start++;
    if (start > 0) {
        memmove(s, s + start, len - start + 1);
        len -= start;
    }
    /* 小写化（仅 ASCII；UUID 内容 ID 均为 ASCII） */
    for (int i = 0; i < len; i++)
        if (s[i] >= 'A' && s[i] <= 'Z') s[i] += 32;
    return len;
}
```

**预期加速：3–5×（该函数单体）**
**对总扫描时间的影响：Live Photo 配对阶段额外减少 5–10%**

---

## 3. 优先级汇总与实施建议

| 优先级 | 热点 | 涉及文件 | 实施难度 | 预期加速 | 总时间收益 |
|-------|------|---------|---------|---------|-----------|
| 🔴 P1 | ISO 8601 datetime 解析 | `core/pairing.py` | 中 | 10–50× | **最大**（-80%配对耗时） |
| 🔴 P2 | 文件内容哈希 | `utils/hashutils.py` | 中 | 3–8× | 大（-60%哈希耗时） |
| 🟡 P3 | Glob 路径过滤 | `utils/pathutils.py` | 中 | 2–5× | 中（-40%发现耗时） |
| 🟡 P4 | 目录遍历 + 扩展名过滤 | `application/services/parallel_scanner.py` | 中高 | 2–4× | 中 |
| 🟡 P5 | 元数据时间戳解析（重复） | `infrastructure/services/metadata_provider.py` | 低 | 2–3× | 中（-30%归一化耗时） |
| 🟢 P6 | Content ID 归一化 | `core/pairing.py` | 低 | 3–5× | 小 |

### 实施路径建议

**第一阶段（2–3 天，收益最大）**

1. 创建 `src/iPhoto/_native/` 目录，参考 `demo/video/_native/` 的 JIT 编译模式（gcc ctypes）。
2. 实现 `parse_iso8601_to_unix_us()` C 函数，替换 `pairing.py` 中的 `_parse_dt()`。
3. 实现 `compute_file_id_c()` C 函数，替换 `hashutils.py` 中的 `compute_file_id()`（使用 `mmap`）。
4. 在两处都保留 Python 纯实现作为 fallback（`gcc` 不可用或编译失败时）。

**第二阶段（3–5 天）**

5. 实现 `should_include_c()` C 函数，替换 `pathutils.py` 中的 `should_include()`。
6. 实现 `parse_iso8601_full()` 扩展版本，消除 `metadata_provider.py` 中的重复解析。

**第三阶段（5–7 天，可选）**

7. 实现 `discover_files_c()` 使用 POSIX `nftw`，替换 `parallel_scanner.py` 中的 Python 递归生成器。
8. 实现 `normalise_content_id_inplace()` 降低字符串分配压力。

---

## 4. C 扩展集成模式

参考项目中已有的 `demo/video/_native/` 实现方式（JIT 编译 + ctypes + Python fallback）：

```
src/iPhoto/_native/
├── __init__.py          # JIT 编译 + ctypes 绑定 + fallback 切换
└── scan_utils.c         # 所有扫描热点 C 实现
```

**`__init__.py` 关键逻辑：**

```python
"""iPhoto 扫描阶段 C 原生加速模块。

在首次导入时 JIT 编译 scan_utils.c（需要 gcc）。
若编译失败，所有函数自动回退到纯 Python 实现。
"""

import ctypes
import os
import shutil
import subprocess
import pathlib
import threading

_lock = threading.Lock()
_lib = None
_C_AVAILABLE = False

_SRC = pathlib.Path(__file__).parent / "scan_utils.c"
_OUT = pathlib.Path(__file__).parent / "_scan_utils.so"

def _compile():
    global _lib, _C_AVAILABLE
    gcc = shutil.which("gcc") or shutil.which("cc")
    if not gcc:
        return
    try:
        flags = ["-O3", "-march=native", "-shared", "-fPIC",
                 "-o", str(_OUT), str(_SRC)]
        subprocess.run([gcc] + flags, check=True,
                       capture_output=True, timeout=30)
    except Exception:
        try:  # 降级：不使用 -march=native
            flags = ["-O2", "-shared", "-fPIC",
                     "-o", str(_OUT), str(_SRC)]
            subprocess.run([gcc] + flags, check=True,
                           capture_output=True, timeout=30)
        except Exception:
            return
    try:
        _lib = ctypes.CDLL(str(_OUT))
        _lib.parse_iso8601_to_unix_us.restype = ctypes.c_int64
        _lib.parse_iso8601_to_unix_us.argtypes = [ctypes.c_char_p]
        # ... 其他函数签名 ...
        _C_AVAILABLE = True
    except Exception:
        pass

with _lock:
    if not _C_AVAILABLE and _SRC.exists():
        _compile()
```

---

## 5. 参考资料

- [xxHash 官方文档](https://xxhash.com/)
- [POSIX `nftw(3)`](https://man7.org/linux/man-pages/man3/nftw.3.html)
- [POSIX `mmap(2)` + `madvise(2)`](https://man7.org/linux/man-pages/man2/mmap.2.html)
- [ISO 8601 规范](https://www.iso.org/iso-8601-date-and-time-format.html)
- `demo/video/_native/fast_thumb.c` — 项目内已有的 C JIT 扩展示例
- `demo/video/_native/__init__.py` — JIT 编译 + ctypes 绑定示例
