/** Decode the opt-in packed geometry transport without changing project data. */
const MAX_BUFFER_BYTES = 128 * 1024 * 1024;
const MAX_TOTAL_BYTES = 512 * 1024 * 1024;
const LITTLE_ENDIAN = new Uint8Array(new Uint32Array([0x01020304]).buffer)[0] === 4;

export function decodeSceneTransport(data) {
  let totalBytes = 0;
  function decode(value, depth = 0) {
    if (value === null || typeof value !== 'object') return value;
    if (depth > 64) throw new Error('Scene transport nesting is too deep');
    if (Object.hasOwn(value, '$studioBuffer')) {
      const { $studioBuffer: type, length, data: encoded } = value;
      if (!['f32', 'u32'].includes(type) || !Number.isSafeInteger(length) || length < 0
          || length * 4 > MAX_BUFFER_BYTES || typeof encoded !== 'string'
          || encoded.length !== Math.ceil(length * 4 / 3) * 4
          || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) {
        throw new Error('Invalid packed scene buffer');
      }
      totalBytes += length * 4;
      if (totalBytes > MAX_TOTAL_BYTES) throw new Error('Packed scene exceeds the memory limit');
      const binary = atob(encoded);
      if (binary.length !== length * 4) throw new Error('Packed scene buffer size mismatch');
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
      let result;
      if (LITTLE_ENDIAN) result = type === 'f32' ? new Float32Array(bytes.buffer) : new Uint32Array(bytes.buffer);
      else {
        result = type === 'f32' ? new Float32Array(length) : new Uint32Array(length);
        const view = new DataView(bytes.buffer);
        for (let index = 0; index < length; index++) {
          result[index] = type === 'f32' ? view.getFloat32(index * 4, true) : view.getUint32(index * 4, true);
        }
      }
      if (type === 'f32') {
        for (const number of result) if (!Number.isFinite(number)) throw new Error('Non-finite scene geometry');
        return result;
      }
      // Three.js setIndex accepts an ordinary array or a BufferAttribute.
      return Array.from(result);
    }
    if (Array.isArray(value)) {
      // Small coordinates and legacy uncompressed geometry remain ordinary arrays.
      if (value.length && typeof value[0] !== 'object') return value;
      for (let index = 0; index < value.length; index++) value[index] = decode(value[index], depth + 1);
    } else {
      for (const key of Object.keys(value)) value[key] = decode(value[key], depth + 1);
    }
    return value;
  }
  return decode(data);
}
