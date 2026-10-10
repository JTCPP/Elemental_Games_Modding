// Retail D5800/9A8C0 constants and VSH fragments 45/47/50/52.
// Eligibility and selected lights must be established from source records.
const ZERO = Object.freeze([0, 0, 0]);

function numericVector(value, length) {
  return (Array.isArray(value) || ArrayBuffer.isView(value)) &&
    value.length === length && Array.from(value).every(Number.isFinite);
}

export function validateRetailLightingConfig(config) {
  if (!config || config.sourceVerified !== true || config.mode !== 'source' ||
      !numericVector(config.emissive, 3) ||
      (config.ambient !== undefined && !numericVector(config.ambient, 3)) ||
      !Array.isArray(config.directionals) || config.directionals.length > 5) return false;
  return config.directionals.every(light => light &&
    numericVector(light.direction, 3) && Math.hypot(...light.direction) > 0 &&
    numericVector(light.diffuse, 3));
}

/**
 * Evaluate Xbox oD0 RGB, before texture modulation.
 * Config ambient is the verified sum material.ambient × light.ambient.
 * Each light diffuse RGB is
 * the verified material.diffuse × runtimeLight.diffuse GPU constant.
 * Directions point towards the light in the same space as the normals.
 * Normals retain source quantization: ordinary VSH fragment1 does not
 * normalize v3. 9A8C0 normalizes only the light direction.
 * Invalid/unsupported input returns null so callers keep their fallback.
 */
export function retailLightingColors(colors, normals, config) {
  if (!validateRetailLightingConfig(config) ||
      !(Array.isArray(colors) || ArrayBuffer.isView(colors)) ||
      !colors.length || colors.length % 3 || !Array.from(colors).every(Number.isFinite)) return null;
  if (config.directionals.length &&
      (!(Array.isArray(normals) || ArrayBuffer.isView(normals)) ||
       normals.length !== colors.length || !Array.from(normals).every(Number.isFinite))) return null;

  const ambient = config.ambient ?? ZERO;
  const base = Array.from(config.emissive, (value, axis) => value + ambient[axis]);
  const lights = config.directionals.map(light => {
    const length = Math.hypot(...light.direction);
    return {direction: Array.from(light.direction, v => v / length), diffuse: light.diffuse};
  });
  const output = new Float32Array(colors.length);
  for (let offset = 0; offset < colors.length; offset += 3) {
    const diffuse = [0, 0, 0];
    for (const light of lights) {
      const dot = Math.max(0, normals[offset] * light.direction[0] +
        normals[offset + 1] * light.direction[1] + normals[offset + 2] * light.direction[2]);
      for (let axis = 0; axis < 3; axis++) diffuse[axis] += dot * light.diffuse[axis];
    }
    // NV2A clamps D0 at vertex output, before interpolation and the
    // pixel-stage MODULATE2X. The caller must apply that factor later.
    for (let axis = 0; axis < 3; axis++) output[offset + axis] =
      Math.min(1, Math.max(0, colors[offset + axis] + base[axis] + diffuse[axis]));
  }
  return output;
}
