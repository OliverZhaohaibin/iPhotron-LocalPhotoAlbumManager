#version 330 core
out vec2 vScreenUV;

uniform bool uApplyCropTransform;
uniform vec4 uCropRect;

void main() {
    const vec2 POS[3] = vec2[3](
        vec2(-1.0, -1.0),
        vec2( 3.0, -1.0),
        vec2(-1.0,  3.0)
    );
    const vec2 UVS[3] = vec2[3](
        vec2(0.0, 0.0),
        vec2(2.0, 0.0),
        vec2(0.0, 2.0)
    );
    // ``UVS`` spans 0..2 so the interpolation covers the entire viewport when we
    // divide by two.  The shader keeps the optional uniforms so the crop-aware
    // fragment stage can share the same uniform interface even though the
    // vertex stage only needs to forward the screen-space coordinates.
    vec2 screen_uv = UVS[gl_VertexID] * 0.5;
    vScreenUV = screen_uv;
    gl_Position = vec4(POS[gl_VertexID], 0.0, 1.0);
}
