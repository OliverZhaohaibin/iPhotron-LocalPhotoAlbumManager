/*
 * fast_thumb.c — C helper for video thumbnail strip processing.
 *
 * Provides high-performance pixel operations via ctypes:
 *   - split_strip_bgra          Split BGRA tile strip → N BGRA frames
 *   - bgra_to_rgb               Convert BGRA → RGB888 (single buffer)
 *   - split_strip_bgra_to_rgb   Split + convert in one pass (no BGRA intermediates)
 *   - bgra_to_rgb_multi         Batch convert N concatenated BGRA frames → RGB
 *   - rotate_bgra               Rotate BGRA frame by 90/180/270°, optional vflip
 *   - snap_to_keyframes         Binary-search snap of target times → keyframes
 *   - scale_bilinear_bgra       Fast bilinear downscale of BGRA frame
 */

#include <stdint.h>
#include <string.h>
#include <math.h>

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif

/* ------------------------------------------------------------------ */
/*  split_strip_bgra — Split horizontal BGRA tile strip → N frames    */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/*  bgra_to_rgb — Convert BGRA → RGB888                              */
/* ------------------------------------------------------------------ */

API void bgra_to_rgb(const uint8_t *bgra, uint8_t *rgb, int n_pixels)
{
    for (int i = 0; i < n_pixels; i++) {
        rgb[i * 3 + 0] = bgra[i * 4 + 2];
        rgb[i * 3 + 1] = bgra[i * 4 + 1];
        rgb[i * 3 + 2] = bgra[i * 4 + 0];
    }
}

/* ------------------------------------------------------------------ */
/*  split_strip_bgra_to_rgb — Combined split + BGRA→RGB in one pass  */
/*                                                                     */
/*  Takes a horizontal BGRA tile strip (W*N × H) and produces N       */
/*  concatenated RGB888 frames (W × H × 3 each) without allocating    */
/*  intermediate BGRA buffers.  This is the hot path for caching       */
/*  after contact-sheet extraction.                                    */
/* ------------------------------------------------------------------ */

API void split_strip_bgra_to_rgb(const uint8_t *strip, uint8_t *rgb_out,
                                 int thumb_w, int thumb_h, int count)
{
    const int frame_row_px    = thumb_w;
    const int strip_row_bytes = thumb_w * count * 4;
    const int rgb_frame_bytes = thumb_w * thumb_h * 3;

    for (int y = 0; y < thumb_h; y++) {
        const uint8_t *row_src = strip + y * strip_row_bytes;
        for (int i = 0; i < count; i++) {
            const uint8_t *src = row_src + i * frame_row_px * 4;
            uint8_t *dst = rgb_out + i * rgb_frame_bytes + y * frame_row_px * 3;
            for (int x = 0; x < frame_row_px; x++) {
                dst[x * 3 + 0] = src[x * 4 + 2];  /* R */
                dst[x * 3 + 1] = src[x * 4 + 1];  /* G */
                dst[x * 3 + 2] = src[x * 4 + 0];  /* B */
            }
        }
    }
}

/* ------------------------------------------------------------------ */
/*  bgra_to_rgb_multi — Batch convert N concatenated BGRA → RGB      */
/*                                                                     */
/*  Equivalent to calling bgra_to_rgb N times but eliminates N ctypes */
/*  round-trips.  Input: N×(W×H×4) bytes, output: N×(W×H×3) bytes.  */
/* ------------------------------------------------------------------ */

API void bgra_to_rgb_multi(const uint8_t *bgra, uint8_t *rgb,
                           int pixels_per_frame, int n_frames)
{
    const int total = pixels_per_frame * n_frames;
    for (int i = 0; i < total; i++) {
        rgb[i * 3 + 0] = bgra[i * 4 + 2];
        rgb[i * 3 + 1] = bgra[i * 4 + 1];
        rgb[i * 3 + 2] = bgra[i * 4 + 0];
    }
}

/* ------------------------------------------------------------------ */
/*  rotate_bgra — Rotate BGRA frame (90/180/270°), optional vflip    */
/*                                                                     */
/*  src:     input BGRA buffer (src_w × src_h × 4 bytes)             */
/*  dst:     output BGRA buffer (dst_w × dst_h × 4 bytes)            */
/*  src_w/h: input dimensions                                         */
/*  degrees: 0, 90, 180, or 270                                       */
/*  vflip:   1 to apply vertical flip after rotation, 0 otherwise     */
/*                                                                     */
/*  Caller must allocate dst with correct rotated dimensions:          */
/*    degrees 0/180 → dst is src_w × src_h                            */
/*    degrees 90/270 → dst is src_h × src_w                           */
/* ------------------------------------------------------------------ */

API void rotate_bgra(const uint8_t *src, uint8_t *dst,
                     int src_w, int src_h, int degrees, int vflip)
{
    int dst_w, dst_h;

    if (degrees == 90 || degrees == 270) {
        dst_w = src_h;
        dst_h = src_w;
    } else {
        dst_w = src_w;
        dst_h = src_h;
    }

    for (int sy = 0; sy < src_h; sy++) {
        for (int sx = 0; sx < src_w; sx++) {
            int dx, dy;
            switch (degrees) {
                case 90:
                    dx = src_h - 1 - sy;
                    dy = sx;
                    break;
                case 180:
                    dx = src_w - 1 - sx;
                    dy = src_h - 1 - sy;
                    break;
                case 270:
                    dx = sy;
                    dy = src_w - 1 - sx;
                    break;
                default:  /* 0 */
                    dx = sx;
                    dy = sy;
                    break;
            }
            if (vflip) {
                dy = dst_h - 1 - dy;
            }
            const uint8_t *s = src + (sy * src_w + sx) * 4;
            uint8_t *d = dst + (dy * dst_w + dx) * 4;
            memcpy(d, s, 4);
        }
    }
}

/* ------------------------------------------------------------------ */
/*  snap_to_keyframes — Binary-search snap targets to keyframes       */
/*                                                                     */
/*  For each target_time, finds the nearest keyframe using binary     */
/*  search.  Outputs parallel arrays of (original_index, snapped_time).*/
/* ------------------------------------------------------------------ */

API void snap_to_keyframes(const double *targets, int n_targets,
                           const double *keyframes, int n_keyframes,
                           int *out_indices, double *out_times)
{
    for (int i = 0; i < n_targets; i++) {
        double t = targets[i];
        out_indices[i] = i;

        if (n_keyframes == 0) {
            out_times[i] = t;
            continue;
        }

        /* Binary search: find first keyframe >= t */
        int lo = 0, hi = n_keyframes;
        while (lo < hi) {
            int mid = lo + (hi - lo) / 2;
            if (keyframes[mid] < t)
                lo = mid + 1;
            else
                hi = mid;
        }
        /* lo is the insertion point (first keyframe >= t).
         * Match Python bisect_left tie-breaking: when equidistant,
         * prefer the later (higher) keyframe (keyframes[lo]).
         */
        double best;
        if (lo < n_keyframes && lo > 0) {
            double diff_hi = fabs(keyframes[lo] - t);
            double diff_lo = fabs(keyframes[lo - 1] - t);
            /* <= matches Python min() which picks first candidate
             * (keyframes[pos]) when equal. */
            best = (diff_hi <= diff_lo) ? keyframes[lo] : keyframes[lo - 1];
        } else if (lo < n_keyframes) {
            best = keyframes[lo];
        } else {
            best = keyframes[lo - 1];
        }
        out_times[i] = best;
    }
}

/* ------------------------------------------------------------------ */
/*  scale_bilinear_bgra — Fast bilinear downscale of BGRA frame      */
/*                                                                     */
/*  Pure-C bilinear interpolation.  For thumbnail generation, the     */
/*  destination is always smaller than the source, so area-averaging  */
/*  with bilinear weights gives good quality at high speed.           */
/* ------------------------------------------------------------------ */

API void scale_bilinear_bgra(const uint8_t *src, int src_w, int src_h,
                             uint8_t *dst, int dst_w, int dst_h)
{
    if (dst_w <= 0 || dst_h <= 0 || src_w <= 0 || src_h <= 0)
        return;

    /* Handle degenerate 1-pixel-wide or 1-pixel-tall sources:
     * bilinear interpolation needs at least 2×2, so replicate the
     * single row/column to fill the destination.                   */
    if (src_w == 1 && src_h == 1) {
        for (int i = 0; i < dst_w * dst_h; i++)
            memcpy(dst + i * 4, src, 4);
        return;
    }
    if (src_h == 1) {
        /* Single row — interpolate horizontally only */
        const double x_ratio = (double)(src_w - 1) / (dst_w > 1 ? dst_w - 1 : 1);
        for (int dy = 0; dy < dst_h; dy++) {
            uint8_t *drow = dst + dy * dst_w * 4;
            for (int dx = 0; dx < dst_w; dx++) {
                double gx = dx * x_ratio;
                int sx = (int)gx;
                double fx = gx - sx;
                if (sx >= src_w - 1) { sx = src_w - 2; fx = 1.0; }
                if (sx < 0) { sx = 0; fx = 0.0; }
                const uint8_t *p0 = src + sx * 4;
                const uint8_t *p1 = p0 + 4;
                for (int c = 0; c < 4; c++) {
                    double v = p0[c] * (1.0 - fx) + p1[c] * fx;
                    int iv = (int)(v + 0.5);
                    if (iv < 0) iv = 0;
                    if (iv > 255) iv = 255;
                    drow[dx * 4 + c] = (uint8_t)iv;
                }
            }
        }
        return;
    }
    if (src_w == 1) {
        /* Single column — interpolate vertically only */
        const double y_ratio = (double)(src_h - 1) / (dst_h > 1 ? dst_h - 1 : 1);
        for (int dy = 0; dy < dst_h; dy++) {
            double gy = dy * y_ratio;
            int sy = (int)gy;
            double fy = gy - sy;
            if (sy >= src_h - 1) { sy = src_h - 2; fy = 1.0; }
            if (sy < 0) { sy = 0; fy = 0.0; }
            const uint8_t *p0 = src + sy * 4;
            const uint8_t *p1 = p0 + 4;
            uint8_t *drow = dst + dy * dst_w * 4;
            for (int dx = 0; dx < dst_w; dx++) {
                for (int c = 0; c < 4; c++) {
                    double v = p0[c] * (1.0 - fy) + p1[c] * fy;
                    int iv = (int)(v + 0.5);
                    if (iv < 0) iv = 0;
                    if (iv > 255) iv = 255;
                    drow[dx * 4 + c] = (uint8_t)iv;
                }
            }
        }
        return;
    }

    const double x_ratio = (double)(src_w - 1) / (dst_w > 1 ? dst_w - 1 : 1);
    const double y_ratio = (double)(src_h - 1) / (dst_h > 1 ? dst_h - 1 : 1);

    for (int dy = 0; dy < dst_h; dy++) {
        double gy = dy * y_ratio;
        int sy = (int)gy;
        double fy = gy - sy;
        if (sy >= src_h - 1) { sy = src_h - 2; fy = 1.0; }
        if (sy < 0) { sy = 0; fy = 0.0; }

        const uint8_t *row0 = src + sy * src_w * 4;
        const uint8_t *row1 = row0 + src_w * 4;
        uint8_t *drow = dst + dy * dst_w * 4;

        for (int dx = 0; dx < dst_w; dx++) {
            double gx = dx * x_ratio;
            int sx = (int)gx;
            double fx = gx - sx;
            if (sx >= src_w - 1) { sx = src_w - 2; fx = 1.0; }
            if (sx < 0) { sx = 0; fx = 0.0; }

            const uint8_t *p00 = row0 + sx * 4;
            const uint8_t *p10 = p00 + 4;
            const uint8_t *p01 = row1 + sx * 4;
            const uint8_t *p11 = p01 + 4;

            double w00 = (1.0 - fx) * (1.0 - fy);
            double w10 = fx * (1.0 - fy);
            double w01 = (1.0 - fx) * fy;
            double w11 = fx * fy;

            for (int c = 0; c < 4; c++) {
                double v = p00[c] * w00 + p10[c] * w10
                         + p01[c] * w01 + p11[c] * w11;
                int iv = (int)(v + 0.5);
                if (iv < 0) iv = 0;
                if (iv > 255) iv = 255;
                drow[dx * 4 + c] = (uint8_t)iv;
            }
        }
    }
}