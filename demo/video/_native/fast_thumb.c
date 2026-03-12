/*
 * fast_thumb.c — C helper for video thumbnail strip processing.
 *
 * Provides two hot-path functions that replace pure-Python loops:
 *
 *   split_strip_bgra()   — split a horizontal BGRA tile strip into
 *                           individual frame buffers.
 *   bgra_to_rgb()        — convert a BGRA buffer to RGB888 in-place.
 *
 * Build (Linux / macOS):
 *   gcc -O3 -march=native -shared -fPIC -o fast_thumb.so fast_thumb.c
 *
 * Build (Windows):
 *   cl /O2 /LD fast_thumb.c /Fe:fast_thumb.dll
 *
 * These are called via ctypes from Python — no CPython API dependency,
 * no build system integration required.
 */

#include <stdint.h>
#include <string.h>

/*
 * Split a horizontal BGRA tile strip (W*N × H) into N individual
 * BGRA frame buffers (W × H each).
 *
 * The strip is laid out row-by-row: each row of the strip contains
 * N tiles side-by-side.  This function de-interleaves the rows into
 * separate contiguous buffers.
 *
 * Parameters:
 *   strip   — input buffer, size = thumb_w * count * thumb_h * 4
 *   out     — output buffer, size = thumb_w * thumb_h * 4 * count
 *             (frames are contiguous: frame0 | frame1 | ... | frameN-1)
 *   thumb_w — width of one thumbnail in pixels
 *   thumb_h — height of one thumbnail in pixels
 *   count   — number of tiles (frames) in the strip
 */
void split_strip_bgra(const uint8_t *strip, uint8_t *out,
                       int thumb_w, int thumb_h, int count)
{
    const int frame_row_bytes = thumb_w * 4;
    const int strip_row_bytes = thumb_w * count * 4;
    const int frame_bytes     = thumb_w * thumb_h * 4;

    for (int y = 0; y < thumb_h; y++) {
        const uint8_t *row_src = strip + y * strip_row_bytes;
        for (int i = 0; i < count; i++) {
            uint8_t *dst = out + i * frame_bytes + y * frame_row_bytes;
            const uint8_t *src = row_src + i * frame_row_bytes;
            memcpy(dst, src, frame_row_bytes);
        }
    }
}

/*
 * Convert BGRA pixels to RGB888.
 *
 * Parameters:
 *   bgra    — input BGRA buffer, size = n_pixels * 4
 *   rgb     — output RGB buffer, size = n_pixels * 3
 *   n_pixels — total number of pixels
 */
void bgra_to_rgb(const uint8_t *bgra, uint8_t *rgb, int n_pixels)
{
    for (int i = 0; i < n_pixels; i++) {
        rgb[i * 3 + 0] = bgra[i * 4 + 2];  /* R ← B offset in BGRA */
        rgb[i * 3 + 1] = bgra[i * 4 + 1];  /* G */
        rgb[i * 3 + 2] = bgra[i * 4 + 0];  /* B ← R offset in BGRA */
    }
}
