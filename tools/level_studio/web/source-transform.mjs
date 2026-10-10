// Row-major source matrices; mirrors retail 68190, not Three's decomposition.
const identity = () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const point = (m, p) => [0, 1, 2].map(r => p.reduce((sum, v, c) => sum + m[r * 4 + c] * v, m[r * 4 + 3]));
const product = ([x, y, z, w], [X, Y, Z, W]) => [w * X + x * W + y * Z - z * Y, w * Y - x * Z + y * W + z * X, w * Z + x * Y - y * X + z * W, w * W - x * X - y * Y - z * Z];
const quaternionMatrix = ([x, y, z, w]) => [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0, 2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0, 2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0, 0, 0, 0, 1];
function matrixQuaternion(m) {
  const trace = m[0] + m[5] + m[10];
  if (trace > 0) { const root = Math.sqrt(trace + 1), f = 0.5 / root; return [(m[9] - m[6]) * f, (m[2] - m[8]) * f, (m[4] - m[1]) * f, 0.5 * root]; }
  const i = [0, 1, 2].reduce((best, axis) => m[axis * 5] > m[best * 5] ? axis : best, 0), j = (i + 1) % 3, k = (i + 2) % 3;
  const root = Math.sqrt(m[i * 5] - m[j * 5] - m[k * 5] + 1), f = 0.5 / root, q = [0, 0, 0, 0];
  q[i] = 0.5 * root; q[j] = (m[i * 4 + j] + m[j * 4 + i]) * f; q[k] = (m[i * 4 + k] + m[k * 4 + i]) * f; q[3] = (m[k * 4 + j] - m[j * 4 + k]) * f;
  return q;
}
const normalized = v => { const length = Math.hypot(...v); return length ? v.map(value => value / length) : v; };
export function effectiveParentRows(node, parent, cameraQuaternion = [0, 0, 0, 1]) {
  const mode = node.inheritMode || 0;
  if (!mode) return parent.slice();
  let m = identity();
  if (mode === 2) {
    const q = matrixQuaternion(parent), [x, y, z, w] = q;
    const d = normalized([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]);
    const half = normalized([d[0], d[1], d[2] + 1]);
    m = quaternionMatrix(product(q, [-half[1], half[0], 0, half[2]]));
  } else if (mode === 3) {
    m = quaternionMatrix(cameraQuaternion);
    const scale = Math.hypot(parent[0], parent[4], parent[8]);
    for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) m[r * 4 + c] *= scale;
  } else if (mode !== 1) throw new Error('Mode de transformation non décodé');
  const p = node.position.map((v, axis) => v + (node.rotatePivot?.[axis] || 0));
  const world = point(parent, p), local = point(m, p);
  for (let axis = 0; axis < 3; axis++) m[axis * 4 + 3] = world[axis] - local[axis];
  return m;
}
