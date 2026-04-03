#include <math.h>
#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif

API void split_strip_bgra(const uint8_t *strip, uint8_t *out,
                          int thumb_w, int thumb_h, int count)
{
    const int frame_row_bytes = thumb_w * 4;
    const int strip_row_bytes = thumb_w * count * 4;
    const int frame_bytes = thumb_w * thumb_h * 4;

    for (int y = 0; y < thumb_h; y++) {
        const uint8_t *row_src = strip + y * strip_row_bytes;
        for (int i = 0; i < count; i++) {
            uint8_t *dst = out + i * frame_bytes + y * frame_row_bytes;
            const uint8_t *src = row_src + i * frame_row_bytes;
            memcpy(dst, src, frame_row_bytes);
        }
    }
}

API void split_strip_bgra_to_rgb(const uint8_t *strip, uint8_t *rgb_out,
                                 int thumb_w, int thumb_h, int count)
{
    const int strip_row_bytes = thumb_w * count * 4;
    const int rgb_frame_bytes = thumb_w * thumb_h * 3;

    for (int y = 0; y < thumb_h; y++) {
        const uint8_t *row_src = strip + y * strip_row_bytes;
        for (int i = 0; i < count; i++) {
            const uint8_t *src = row_src + i * thumb_w * 4;
            uint8_t *dst = rgb_out + i * rgb_frame_bytes + y * thumb_w * 3;
            for (int x = 0; x < thumb_w; x++) {
                dst[x * 3 + 0] = src[x * 4 + 2];
                dst[x * 3 + 1] = src[x * 4 + 1];
                dst[x * 3 + 2] = src[x * 4 + 0];
            }
        }
    }
}

API void snap_to_keyframes(const double *targets, int n_targets,
                           const double *keyframes, int n_keyframes,
                           int *out_indices, double *out_times)
{
    for (int i = 0; i < n_targets; i++) {
        double target = targets[i];
        out_indices[i] = i;

        if (n_keyframes == 0) {
            out_times[i] = target;
            continue;
        }

        int lo = 0;
        int hi = n_keyframes;
        while (lo < hi) {
            int mid = lo + (hi - lo) / 2;
            if (keyframes[mid] < target) {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }

        if (lo < n_keyframes && lo > 0) {
            double diff_hi = fabs(keyframes[lo] - target);
            double diff_lo = fabs(keyframes[lo - 1] - target);
            out_times[i] = (diff_hi <= diff_lo) ? keyframes[lo] : keyframes[lo - 1];
        } else if (lo < n_keyframes) {
            out_times[i] = keyframes[lo];
        } else {
            out_times[i] = keyframes[lo - 1];
        }
    }
}
