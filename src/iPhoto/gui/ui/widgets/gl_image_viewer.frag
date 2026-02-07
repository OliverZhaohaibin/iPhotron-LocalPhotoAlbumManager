#version 330 core
in vec2 vUV;
out vec4 FragColor;

uniform sampler2D uTex;

uniform float uBrilliance;
uniform float uExposure;
uniform float uHighlights;
uniform float uShadows;
uniform float uBrightness;
uniform float uContrast;
uniform float uBlackPoint;
uniform float uSaturation;
uniform float uVibrance;
uniform float uColorCast;
uniform vec3  uGain;
uniform vec4  uBWParams;
uniform bool  uBWEnabled;
uniform sampler2D uCurveLUT;  // 256x1 RGB LUT texture for curve adjustment
uniform bool  uCurveEnabled;
uniform sampler2D uLevelsLUT; // 256x1 RGB LUT texture for levels adjustment
uniform bool  uLevelsEnabled;
uniform float uWBWarmth;      // [-1,1]
uniform float uWBTemperature; // [-1,1]
uniform float uWBTint;        // [-1,1]
uniform bool  uWBEnabled;
uniform float uTime;

// Selective Color uniforms
// uSCRange0[i] = (centerHue, widthHue, hueShift, satAdj)
// uSCRange1[i] = (lumAdj, satGateLo, satGateHi, enabled)
uniform vec4  uSCRange0[6];
uniform vec4  uSCRange1[6];
uniform bool  uSCEnabled;

uniform vec2  uViewSize;
uniform vec2  uTexSize;
uniform float uScale;
uniform vec2  uPan;
uniform float uImgScale;
uniform vec2  uImgOffset;
uniform float uCropCX;
uniform float uCropCY;
uniform float uCropW;
uniform float uCropH;
uniform mat3  uPerspectiveMatrix;
uniform int   uRotate90;  // 0, 1, 2, 3 for 0°, 90°, 180°, 270° CCW rotation

float clamp01(float x) { return clamp(x, 0.0, 1.0); }

float luminance(vec3 color) {
    // Use Rec. 709 coefficients to match the CPU preview pipeline.
    return dot(color, vec3(0.2126, 0.7152, 0.0722));
}

float apply_channel(float value,
                    float exposure,
                    float brightness,
                    float brilliance,
                    float highlights,
                    float shadows,
                    float contrast_factor,
                    float black_point)
{
    float adjusted = value + exposure + brightness;
    float mid_distance = value - 0.5;
    adjusted += brilliance * (1.0 - pow(mid_distance * 2.0, 2.0));

    if (adjusted > 0.65) {
        float ratio = (adjusted - 0.65) / 0.35;
        adjusted += highlights * ratio;
    } else if (adjusted < 0.35) {
        float ratio = (0.35 - adjusted) / 0.35;
        adjusted += shadows * ratio;
    }

    adjusted = (adjusted - 0.5) * contrast_factor + 0.5;

    if (black_point > 0.0)
        adjusted -= black_point * (1.0 - adjusted);
    else if (black_point < 0.0)
        adjusted -= black_point * adjusted;

    return clamp01(adjusted);
}

vec3 apply_color_transform(vec3 rgb,
                           float saturation,
                           float vibrance,
                           float colorCast,
                           vec3 gain)
{
    vec3 mixGain = (1.0 - colorCast) + gain * colorCast;
    rgb *= mixGain;

    float luma = dot(rgb, vec3(0.299, 0.587, 0.114));
    vec3  chroma = rgb - vec3(luma);
    float sat_amt = 1.0 + saturation;
    float vib_amt = 1.0 + vibrance;
    float w = 1.0 - clamp(abs(luma - 0.5) * 2.0, 0.0, 1.0);
    float chroma_scale = sat_amt * (1.0 + (vib_amt - 1.0) * w);
    chroma *= chroma_scale;
    return clamp(vec3(luma) + chroma, 0.0, 1.0);
}

float gamma_neutral_signed(float gray, float neutral_adjust) {
    // Positive values brighten neutrals, negative values darken them.
    float magnitude = 0.6 * abs(neutral_adjust);
    float gamma = (neutral_adjust >= 0.0) ? pow(2.0, -magnitude) : pow(2.0, magnitude);
    return pow(clamp(gray, 0.0, 1.0), gamma);
}

float contrast_tone_signed(float gray, float tone_adjust) {
    // Apply an S-curve controlled by the tone adjustment.
    float x = clamp(gray, 0.0, 1.0);
    float epsilon = 1e-6;
    float logit = log(clamp(x, epsilon, 1.0 - epsilon) / clamp(1.0 - x, epsilon, 1.0 - epsilon));
    float k = (tone_adjust >= 0.0) ? mix(1.0, 2.2, tone_adjust) : mix(1.0, 0.6, -tone_adjust);
    float y = 1.0 / (1.0 + exp(-logit * k));
    return clamp(y, 0.0, 1.0);
}

float grain_noise(vec2 uv, float grain_amount) {
    // Match the CPU preview noise so thumbnails and the live view stay consistent.
    if (grain_amount <= 0.0) {
        return 0.0;
    }
    float noise = fract(sin(dot(uv, vec2(12.9898, 78.233))) * 43758.5453);
    return (noise - 0.5) * 0.2 * grain_amount;
}

vec2 apply_inverse_perspective(vec2 uv) {
    vec2 centered = uv * 2.0 - 1.0;
    vec3 warped = uPerspectiveMatrix * vec3(centered, 1.0);
    float denom = warped.z;
    if (abs(denom) < 1e-5) {
        denom = (denom >= 0.0) ? 1e-5 : -1e-5;
    }
    vec2 restored = warped.xy / denom;
    return restored * 0.5 + 0.5;
}

vec2 apply_rotation_90(vec2 uv, int rotate_steps) {
    // Apply discrete 90-degree rotations
    // Note: These are CW rotations to match the logical coordinate swap direction
    int steps = rotate_steps % 4;
    if (steps == 1) {
        // 90° CW: (x,y) -> (y, 1-x)
        return vec2(uv.y, 1.0 - uv.x);
    } else if (steps == 2) {
        // 180°: (x,y) -> (1-x, 1-y)
        return vec2(1.0 - uv.x, 1.0 - uv.y);
    } else if (steps == 3) {
        // 270° CW (or 90° CCW): (x,y) -> (1-y, x)
        return vec2(1.0 - uv.y, uv.x);
    }
    // steps == 0: no rotation
    return uv;
}

vec3 wb_warmth_adjust(vec3 c, float w) {
    if (w == 0.0) return c;
    float scale = 0.15 * w;
    vec3 temp_gain = vec3(1.0 + scale, 1.0, 1.0 - scale);
    vec3 luma_coeff = vec3(0.2126, 0.7152, 0.0722);
    float orig_luma = dot(c, luma_coeff);
    c = c * temp_gain;
    float new_luma = dot(c, luma_coeff);
    if (new_luma > 0.001) {
        c *= (orig_luma / new_luma);
    }
    return c;
}

vec3 wb_temp_tint_adjust(vec3 c, float temp, float tint) {
    if (temp == 0.0 && tint == 0.0) return c;
    vec3 luma_coeff = vec3(0.2126, 0.7152, 0.0722);
    float orig_luma = dot(c, luma_coeff);
    float temp_scale = 0.3 * temp;
    vec3 temp_gain = vec3(1.0 + temp_scale * 0.8, 1.0, 1.0 - temp_scale);
    float tint_scale = 0.2 * tint;
    vec3 tint_gain = vec3(1.0 + tint_scale * 0.5, 1.0 - tint_scale * 0.5, 1.0 + tint_scale * 0.5);
    c = c * temp_gain * tint_gain;
    float new_luma = dot(c, luma_coeff);
    if (new_luma > 0.001) {
        c *= (orig_luma / new_luma);
    }
    return c;
}

vec3 apply_wb(vec3 c, float warmth, float temperature, float tint) {
    c = wb_warmth_adjust(c, warmth);
    c = wb_temp_tint_adjust(c, temperature, tint);
    return c;
}

// --- Selective Color helpers (matching CPU pipeline) ---
float sc_hue_dist(float h1, float h2){
    float d = abs(h1 - h2);
    return min(d, 1.0 - d);
}

vec3 sc_rgb2hsl(vec3 c){
    float r=c.r, g=c.g, b=c.b;
    float maxc = max(r, max(g,b));
    float minc = min(r, min(g,b));
    float l = (maxc + minc) * 0.5;
    float s = 0.0;
    float h = 0.0;
    float d = maxc - minc;
    if (d > 1e-6){
        s = d / (1.0 - abs(2.0*l - 1.0));
        if (maxc == r){
            h = (g - b) / d;
            h = mod(h, 6.0);
        } else if (maxc == g){
            h = (b - r) / d + 2.0;
        } else {
            h = (r - g) / d + 4.0;
        }
        h /= 6.0;
        if (h < 0.0) h += 1.0;
    }
    return vec3(h, s, l);
}

float sc_hue2rgb(float p, float q, float t){
    if (t < 0.0) t += 1.0;
    if (t > 1.0) t -= 1.0;
    if (t < 1.0/6.0) return p + (q - p) * 6.0 * t;
    if (t < 1.0/2.0) return q;
    if (t < 2.0/3.0) return p + (q - p) * (2.0/3.0 - t) * 6.0;
    return p;
}

vec3 sc_hsl2rgb(vec3 hsl){
    float h=hsl.x, s=hsl.y, l=hsl.z;
    float r,g,b;
    if (s < 1e-6){
        r=g=b=l;
    }else{
        float q = (l < 0.5) ? (l * (1.0 + s)) : (l + s - l*s);
        float p = 2.0*l - q;
        r = sc_hue2rgb(p,q,h + 1.0/3.0);
        g = sc_hue2rgb(p,q,h);
        b = sc_hue2rgb(p,q,h - 1.0/3.0);
    }
    return vec3(r,g,b);
}

vec3 sc_apply_range(vec3 rgb, int i){
    vec3 hsl = sc_rgb2hsl(rgb);
    vec4 p0 = uSCRange0[i];
    vec4 p1 = uSCRange1[i];
    float enabled = p1.w;
    if (enabled < 0.5) return rgb;

    float center = p0.x;
    float width  = clamp(p0.y, 0.001, 0.5);
    float hueShiftN = clamp(p0.z, -1.0, 1.0);
    float satAdjN   = clamp(p0.w, -1.0, 1.0);
    float lumAdjN   = clamp(p1.x, -1.0, 1.0);
    float gateLo    = clamp(p1.y, 0.0, 1.0);
    float gateHi    = clamp(p1.z, 0.0, 1.0);

    float feather = max(0.001, width * 0.50);
    float d = sc_hue_dist(hsl.x, center);
    float m = 1.0 - smoothstep(width, width + feather, d);
    m *= smoothstep(gateLo, gateHi, hsl.y);

    if (m < 1e-5) return rgb;

    float hueShift = hueShiftN * (30.0/360.0);
    float satScale = 1.0 + satAdjN;
    float lumLift  = lumAdjN * 0.25;

    vec3 hsl2 = hsl;
    hsl2.x = fract(hsl2.x + hueShift);
    hsl2.y = clamp(hsl2.y * satScale, 0.0, 1.0);
    hsl2.z = clamp(hsl2.z + lumLift, 0.0, 1.0);

    vec3 rgb2 = sc_hsl2rgb(hsl2);
    return mix(rgb, rgb2, clamp(m, 0.0, 1.0));
}

vec3 apply_selective_color(vec3 c){
    for (int i=0; i<6; i++){
        c = sc_apply_range(c, i);
    }
    return c;
}

vec3 apply_bw(vec3 color, vec2 uv) {
    float intensity = clamp(uBWParams.x, -1.0, 1.0);
    float neutrals = clamp(uBWParams.y, -1.0, 1.0);
    float tone = clamp(uBWParams.z, -1.0, 1.0);
    float grain = clamp(uBWParams.w, 0.0, 1.0);

    if (abs(intensity) <= 1e-4 && abs(neutrals) <= 1e-4 && abs(tone) <= 1e-4 && grain <= 0.0) {
        return color;
    }

    float g0 = luminance(color);

    // Anchors that define the soft, neutral, and rich looks driven by the master slider.
    float g_soft = pow(g0, 0.85);
    float g_neutral = g0;
    float g_rich = contrast_tone_signed(g0, 0.35);

    float gray;
    if (intensity >= 0.0) {
        gray = mix(g_neutral, g_rich, intensity);
    } else {
        gray = mix(g_soft, g_neutral, intensity + 1.0);
    }

    gray = gamma_neutral_signed(gray, neutrals);
    gray = contrast_tone_signed(gray, tone);
    gray += grain_noise(uv * uTexSize, grain);
    gray = clamp(gray, 0.0, 1.0);

    return vec3(gray);
}

vec3 apply_curve(vec3 color) {
    // Apply curve LUT lookup for each RGB channel
    // The LUT is a 256x1 texture where x coordinate is the input value
    // and the RGB values at that position are the output for each channel
    float r = texture(uCurveLUT, vec2(color.r, 0.5)).r;
    float g = texture(uCurveLUT, vec2(color.g, 0.5)).g;
    float b = texture(uCurveLUT, vec2(color.b, 0.5)).b;
    return vec3(r, g, b);
}

vec3 apply_levels(vec3 color) {
    // Apply levels LUT lookup for each RGB channel (same format as curve LUT)
    float r = texture(uLevelsLUT, vec2(color.r, 0.5)).r;
    float g = texture(uLevelsLUT, vec2(color.g, 0.5)).g;
    float b = texture(uLevelsLUT, vec2(color.b, 0.5)).b;
    return vec3(r, g, b);
}

void main() {
    if (uScale <= 0.0) {
        discard;
    }

    float safeImgScale = max(uImgScale, 1e-6);

    vec2 fragPx = vec2(gl_FragCoord.x - 0.5, gl_FragCoord.y - 0.5);
    vec2 viewCentre = uViewSize * 0.5;
    vec2 viewVector = fragPx - viewCentre;
    vec2 screenVector = viewVector - uPan;
    vec2 texVector = (screenVector / uScale - uImgOffset) / safeImgScale;
    vec2 texPx = texVector + (uTexSize * 0.5);
    vec2 uv = texPx / uTexSize;

    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        discard;
    }

    uv.y = 1.0 - uv.y;
    vec2 uv_corrected = uv;

    // Perform crop test in Logical/Screen space.
    // The crop box is defined by the user on the screen (post-perspective/straighten),
    // so we must mask pixels based on their screen position (uv_corrected).
    float crop_min_x = uCropCX - uCropW * 0.5;
    float crop_max_x = uCropCX + uCropW * 0.5;
    float crop_min_y = uCropCY - uCropH * 0.5;
    float crop_max_y = uCropCY + uCropH * 0.5;

    if (uv_corrected.x < crop_min_x || uv_corrected.x > crop_max_x ||
        uv_corrected.y < crop_min_y || uv_corrected.y > crop_max_y) {
        discard;
    }

    // Apply perspective correction
    vec2 uv_perspective = apply_inverse_perspective(uv_corrected);

    // Check perspective bounds (Valid Image Area)
    // This clips any invalid texture regions (black borders) created by the perspective transform.
    if (uv_perspective.x < 0.0 || uv_perspective.x > 1.0 ||
        uv_perspective.y < 0.0 || uv_perspective.y > 1.0) {
        discard;
    }
    
    // Apply rotation to get final texture sampling coordinates
    vec2 uv_tex = apply_rotation_90(uv_perspective, uRotate90);

    // Sample the texture at the computed texture-space coordinates
    vec4 texel = texture(uTex, uv_tex);
    vec3 c = texel.rgb;

    float exposure_term    = uExposure   * 1.5;
    float brightness_term  = uBrightness * 0.75;
    float brilliance_term  = uBrilliance * 0.6;
    float contrast_factor  = 1.0 + uContrast;

    c.r = apply_channel(c.r, exposure_term, brightness_term, brilliance_term,
                        uHighlights, uShadows, contrast_factor, uBlackPoint);
    c.g = apply_channel(c.g, exposure_term, brightness_term, brilliance_term,
                        uHighlights, uShadows, contrast_factor, uBlackPoint);
    c.b = apply_channel(c.b, exposure_term, brightness_term, brilliance_term,
                        uHighlights, uShadows, contrast_factor, uBlackPoint);

    c = apply_color_transform(c, uSaturation, uVibrance, uColorCast, uGain);

    // Apply white balance adjustment after color but before curve
    if (uWBEnabled) {
        c = apply_wb(c, uWBWarmth, uWBTemperature, uWBTint);
    }

    // Apply curve adjustment after color but before B&W
    if (uCurveEnabled) {
        c = apply_curve(c);
    }

    // Apply levels adjustment after curve but before B&W
    if (uLevelsEnabled) {
        c = apply_levels(c);
    }

    // Apply selective color after levels, before B&W
    if (uSCEnabled) {
        c = apply_selective_color(c);
    }

    if (uBWEnabled) {
        c = apply_bw(c, uv_tex);
    }
    FragColor = vec4(clamp(c, 0.0, 1.0), 1.0);
}
