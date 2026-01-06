## 2025-02-23 - Pillow Draft Optimization
**Learning:** When generating small thumbnails from large JPEGs using Pillow, `img.draft()` can significantly speed up loading by using the JPEG decoder's internal downscaling (DCT scaling).
**Action:** Use `img.draft(mode, size)` before loading pixel data (e.g., before `transpose` or `resize`) when the target size is much smaller than the original. For 16x16 micro-thumbnails, `draft`ing to 64x64 provided a ~4.4x speedup.
## 2024-05-24 - Glob Expansion Caching
**Learning:** Repetitive expansion of the same glob patterns (e.g., `*.{jpg,png}`) during file scanning is a significant bottleneck. Standard Python functions like `fnmatch` are fast, but custom logic for braces expansion (like `{a,b}`) using Regex can be costly if called for every file.
**Action:** Use `functools.lru_cache` to memoize the expansion of glob patterns. This turns an O(N*M) operation (N files, M patterns) into effectively O(N) by making pattern processing O(1) after the first hit. Also, watch out for redundant checks in loops where a helper function (like `should_include`) might already be calling another check (like `is_excluded`).
