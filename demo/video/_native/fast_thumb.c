/*
 * fast_thumb.c — C helper for video thumbnail strip processing.
 */

#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif

/*
 * Split a horizontal BGRA tile strip (W*N × H) into N individual
 * BGRA frame buffers (W × H each).
 */
API void split_strip_bgra(const uint8_t *strip, uint8_t *out,
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
 */
API void bgra_to_rgb(const uint8_t *bgra, uint8_t *rgb, int n_pixels)
{
    for (int i = 0; i < n_pixels; i++) {
        rgb[i * 3 + 0] = bgra[i * 4 + 2];
        rgb[i * 3 + 1] = bgra[i * 4 + 1];
        rgb[i * 3 + 2] = bgra[i * 4 + 0];
    }
}