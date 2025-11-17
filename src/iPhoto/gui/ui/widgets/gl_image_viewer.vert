#version 330 core
out vec2 vUV;
// Perspective correction is performed in the fragment shader so this vertex
// stage intentionally stays minimal and simply provides a full-screen triangle.
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
    vUV = UVS[gl_VertexID];
    gl_Position = vec4(POS[gl_VertexID], 0.0, 1.0);
}
