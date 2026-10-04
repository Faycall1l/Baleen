import { NodeProgram } from 'sigma/rendering';
import type { ProgramInfo } from 'sigma/rendering';
import type { RenderParams } from 'sigma/types';
import { floatColor } from 'sigma/utils';
import type { NodeDisplayData } from 'sigma/types';

// Square node program with a thin outer border (R23): the terminal's
// instrument shape. Two triangles (6 vertices); the border metric is the
// Chebyshev distance so rings stay square. Mirrors the bordered-disc
// program's scale (same size math) so squares and discs share sizing,
// and picking works through the same id attribute.
const CORNERS: ReadonlyArray<readonly [number, number]> = [
  [-1, -1],
  [1, -1],
  [-1, 1],
  [1, -1],
  [1, 1],
  [-1, 1],
];

const VERTEX_SHADER = /* glsl */ `
attribute vec2 a_position;
attribute float a_size;
attribute vec2 a_corner;

uniform mat3 u_matrix;
uniform float u_sizeRatio;
uniform float u_correctionRatio;

varying vec2 v_diffVector;
varying float v_radius;

#ifdef PICKING_MODE
attribute vec4 a_id;
varying vec4 v_color;
#else
attribute vec4 a_borderColor_1;
attribute vec4 a_borderColor_2;
varying vec4 v_borderColor_1;
varying vec4 v_borderColor_2;
#endif

void main() {
  float size = a_size * u_correctionRatio / u_sizeRatio * 4.0;
  vec2 diffVector = a_corner * (size / 2.0);
  vec2 position = a_position + diffVector;
  gl_Position = vec4(
    (u_matrix * vec3(position, 1)).xy,
    0,
    1
  );

  v_radius = size / 2.0;
  v_diffVector = diffVector;

  #ifdef PICKING_MODE
  v_color = a_id;
  #else
  v_borderColor_1 = a_borderColor_1;
  v_borderColor_2 = a_borderColor_2;
  #endif
}
`;

const FRAGMENT_SHADER = /* glsl */ `
precision highp float;

varying vec2 v_diffVector;
varying float v_radius;

#ifdef PICKING_MODE
varying vec4 v_color;
#else
varying vec4 v_borderColor_1;
varying vec4 v_borderColor_2;
#endif

uniform float u_correctionRatio;

const float bias = 255.0 / 254.0;
const vec4 transparent = vec4(0.0, 0.0, 0.0, 0.0);

void main(void) {
  float dist = max(abs(v_diffVector.x), abs(v_diffVector.y));
  float aaBorder = 2.0 * u_correctionRatio;

  #ifdef PICKING_MODE
  if (dist > v_radius)
    gl_FragColor = transparent;
  else {
    gl_FragColor = v_color;
    gl_FragColor.a *= bias;
  }
  #else
  float borderSize_1 = v_radius * 0.16;
  float adjustedBorderSize_1 = v_radius - borderSize_1;
  vec4 borderColor_1 = v_borderColor_1;
  borderColor_1.a *= bias;
  vec4 borderColor_2 = v_borderColor_2;
  borderColor_2.a *= bias;
  if (dist > v_radius) {
    gl_FragColor = transparent;
  } else if (dist > adjustedBorderSize_1 - aaBorder) {
    gl_FragColor = mix(borderColor_2, borderColor_1, (dist - adjustedBorderSize_1 + aaBorder) / aaBorder);
  } else {
    gl_FragColor = borderColor_2;
  }
  #endif
}
`;

export class SquareNodeProgram extends NodeProgram {
  getDefinition() {
    return {
      VERTICES: 6,
      VERTEX_SHADER_SOURCE: VERTEX_SHADER,
      FRAGMENT_SHADER_SOURCE: FRAGMENT_SHADER,
      METHOD: WebGLRenderingContext.TRIANGLES,
      UNIFORMS: ['u_sizeRatio', 'u_correctionRatio', 'u_matrix'],
      ATTRIBUTES: [
        { name: 'a_position', size: 2, type: WebGLRenderingContext.FLOAT },
        { name: 'a_id', size: 4, type: WebGLRenderingContext.UNSIGNED_BYTE, normalized: true },
        { name: 'a_size', size: 1, type: WebGLRenderingContext.FLOAT },
        { name: 'a_borderColor_1', size: 4, type: WebGLRenderingContext.UNSIGNED_BYTE, normalized: true },
        { name: 'a_borderColor_2', size: 4, type: WebGLRenderingContext.UNSIGNED_BYTE, normalized: true },
      ],
      CONSTANT_ATTRIBUTES: [{ name: 'a_corner', size: 2, type: WebGLRenderingContext.FLOAT }],
      CONSTANT_DATA: CORNERS.map(([x, y]) => [x, y]),
    };
  }

  processVisibleItem(nodeIndex: number, startIndex: number, data: NodeDisplayData): void {
    const array = this.array;
    // borderColor rides the node attributes like in the bordered-disc
    // program; the display-data type just doesn't declare it.
    const attrs = data as unknown as Record<string, unknown>;
    array[startIndex++] = data.x;
    array[startIndex++] = data.y;
    array[startIndex++] = nodeIndex;
    array[startIndex++] = data.size;
    array[startIndex++] = floatColor((attrs.borderColor as string) || '#000000');
    array[startIndex++] = floatColor(data.color);
  }

  setUniforms(params: RenderParams, programInfo: ProgramInfo): void {
    const { gl, uniformLocations } = programInfo;
    gl.uniform1f(uniformLocations.u_correctionRatio, params.correctionRatio);
    gl.uniform1f(uniformLocations.u_sizeRatio, params.sizeRatio);
    gl.uniformMatrix3fv(uniformLocations.u_matrix, false, params.matrix);
  }
}
