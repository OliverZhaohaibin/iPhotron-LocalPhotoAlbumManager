#version 440

layout(location = 0) in vec2 v_texcoord;
layout(location = 0) out vec4 fragColor;

// Plane textures: Y and UV (NV12/P010 interleaved chroma)
layout(binding = 1) uniform sampler2D tex_y;
layout(binding = 2) uniform sampler2D tex_uv;
// Fallback RGBA texture when frame is already in RGB format
layout(binding = 3) uniform sampler2D tex_rgba;

layout(std140, binding = 0) uniform buf {
    // 0 = NV12 (8-bit), 1 = P010 (10-bit), 2 = RGBA passthrough
    int u_format;
    // Color space: 0 = BT.601, 1 = BT.709, 2 = BT.2020
    int u_colorspace;
    // Transfer function: 0 = gamma/SDR, 1 = PQ (ST.2084), 2 = HLG (STD-B67)
    int u_transfer;
    // Color range: 0 = limited/video, 1 = full
    int u_range;
    // Letterbox color (RGBA)
    vec4 u_letterbox_color;
    // Video rect within viewport: (x, y, w, h) normalised to [0,1]
    vec4 u_video_rect;
};

// ---------------------------------------------------------------
// YUV → RGB conversion matrices
// ---------------------------------------------------------------
// BT.601 (SD)
const mat3 yuv2rgb_601 = mat3(
    1.0,     1.0,     1.0,
    0.0,    -0.34414, 1.772,
    1.402,  -0.71414, 0.0
);

// BT.709 (HD)
const mat3 yuv2rgb_709 = mat3(
    1.0,     1.0,     1.0,
    0.0,    -0.18732, 1.8556,
    1.5748, -0.46812, 0.0
);

// BT.2020 (UHD / HDR)
const mat3 yuv2rgb_2020 = mat3(
    1.0,     1.0,     1.0,
    0.0,    -0.16455, 1.8814,
    1.4746, -0.57135, 0.0
);

// ---------------------------------------------------------------
// Transfer function helpers
// ---------------------------------------------------------------

// PQ (ST.2084) EOTF: normalised signal → linear light
vec3 pq_eotf(vec3 e) {
    const float m1 = 0.1593017578125;
    const float m2 = 78.84375;
    const float c1 = 0.8359375;
    const float c2 = 18.8515625;
    const float c3 = 18.6875;

    vec3 ep = pow(max(e, vec3(0.0)), vec3(1.0 / m2));
    vec3 num = max(ep - c1, vec3(0.0));
    vec3 den = c2 - c3 * ep;
    return pow(num / max(den, vec3(1e-6)), vec3(1.0 / m1));
}

// HLG OETF inverse (signal → scene-linear)
vec3 hlg_eotf(vec3 e) {
    const float a = 0.17883277;
    const float b = 0.28466892;  // 1 - 4*a
    const float c = 0.55991073;  // 0.5 - a*ln(4*a)

    vec3 result;
    for (int i = 0; i < 3; i++) {
        float v = e[i];
        if (v <= 0.5)
            result[i] = (v * v) / 3.0;
        else
            result[i] = (exp((v - c) / a) + b) / 12.0;
    }
    return result;
}

// Simple Reinhard-style HDR→SDR tone mapping
vec3 tonemap_reinhard(vec3 linear_rgb) {
    // Scale from scene linear to a reasonable SDR range
    // PQ peak is 10000 nits, HLG nominal is ~1000 nits
    // We map so that SDR reference white (≈203 nits) → 1.0
    float luma = dot(linear_rgb, vec3(0.2627, 0.6780, 0.0593));
    float mapped_luma = luma / (1.0 + luma);
    float scale = (luma > 1e-6) ? mapped_luma / luma : 0.0;
    return linear_rgb * scale;
}

// BT.2020 → BT.709 colour space conversion (3×3 gamut mapping)
const mat3 bt2020_to_bt709 = mat3(
    1.6605, -0.1246, -0.0182,
   -0.5876,  1.1329, -0.1006,
   -0.0728, -0.0083,  1.1187
);

// sRGB OETF (linear → display gamma)
vec3 linear_to_srgb(vec3 linear_rgb) {
    vec3 result;
    for (int i = 0; i < 3; i++) {
        float v = linear_rgb[i];
        if (v <= 0.0031308)
            result[i] = 12.92 * v;
        else
            result[i] = 1.055 * pow(v, 1.0 / 2.4) - 0.055;
    }
    return result;
}

void main()
{
    // Determine if pixel falls inside video rect or in letterbox
    vec2 uv = v_texcoord;

    // Map from viewport UV to video-relative UV
    vec2 video_uv = (uv - u_video_rect.xy) / max(u_video_rect.zw, vec2(1e-6));

    // If outside video rect, draw letterbox color
    if (video_uv.x < 0.0 || video_uv.x > 1.0 ||
        video_uv.y < 0.0 || video_uv.y > 1.0) {
        fragColor = vec4(u_letterbox_color.rgb, 1.0);
        return;
    }

    vec3 rgb;

    if (u_format == 2) {
        // RGBA passthrough
        rgb = texture(tex_rgba, video_uv).rgb;
    } else {
        // YUV sampling
        float y_raw = texture(tex_y, video_uv).r;
        vec2 uv_raw = texture(tex_uv, video_uv).rg;

        float y, u, v;

        if (u_range == 0) {
            // Limited / video range
            if (u_format == 1) {
                // P010: 10-bit data in upper bits of 16-bit, already normalised by GL
                y = (y_raw * 255.0 - 16.0) / 219.0;
                u = (uv_raw.r * 255.0 - 128.0) / 224.0;
                v = (uv_raw.g * 255.0 - 128.0) / 224.0;
            } else {
                // NV12: 8-bit
                y = (y_raw * 255.0 - 16.0) / 219.0;
                u = (uv_raw.r * 255.0 - 128.0) / 224.0;
                v = (uv_raw.g * 255.0 - 128.0) / 224.0;
            }
        } else {
            // Full range
            y = y_raw;
            u = uv_raw.r - 0.5;
            v = uv_raw.g - 0.5;
        }

        vec3 yuv_vec = vec3(y, u, v);

        if (u_colorspace == 2)
            rgb = yuv2rgb_2020 * yuv_vec;
        else if (u_colorspace == 0)
            rgb = yuv2rgb_601 * yuv_vec;
        else
            rgb = yuv2rgb_709 * yuv_vec;
    }

    // Transfer function processing
    if (u_transfer == 1) {
        // PQ (ST.2084) → linear → tone map → BT.709 → sRGB
        vec3 linear_rgb = pq_eotf(rgb);
        // PQ returns scene-referred values normalised to [0, 10000] nits
        // Scale to a display-referred range
        linear_rgb *= 100.0;  // bring up from normalised PQ scale
        linear_rgb = tonemap_reinhard(linear_rgb);
        if (u_colorspace == 2)
            linear_rgb = bt2020_to_bt709 * linear_rgb;
        rgb = linear_to_srgb(clamp(linear_rgb, vec3(0.0), vec3(1.0)));
    } else if (u_transfer == 2) {
        // HLG (STD-B67) → linear → tone map → BT.709 → sRGB
        vec3 linear_rgb = hlg_eotf(rgb);
        linear_rgb = tonemap_reinhard(linear_rgb);
        if (u_colorspace == 2)
            linear_rgb = bt2020_to_bt709 * linear_rgb;
        rgb = linear_to_srgb(clamp(linear_rgb, vec3(0.0), vec3(1.0)));
    }
    // else: SDR — rgb is already in display gamma space

    fragColor = vec4(clamp(rgb, vec3(0.0), vec3(1.0)), 1.0);
}
