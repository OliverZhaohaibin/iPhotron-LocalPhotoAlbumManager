## 2025-02-23 - Pillow Draft Optimization
**Learning:** When generating small thumbnails from large JPEGs using Pillow, `img.draft()` can significantly speed up loading by using the JPEG decoder's internal downscaling (DCT scaling).
**Action:** Use `img.draft(mode, size)` before loading pixel data (e.g., before `transpose` or `resize`) when the target size is much smaller than the original. For 16x16 micro-thumbnails, `draft`ing to 64x64 provided a ~4.4x speedup.
