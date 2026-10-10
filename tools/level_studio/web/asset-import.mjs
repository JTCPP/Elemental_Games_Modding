/** Bounded local model import. No external material or texture files are read. */
export const MAX_MODEL_FILE_BYTES = 12 * 1024 * 1024;
export const MAX_TEXTURE_FILE_BYTES = 8 * 1024 * 1024;
const MAX_VERTICES = 100000;
const MAX_INDICES = 600000;

function numbers(value, label, stride, maximum) {
  if (!Array.isArray(value) || !value.length || value.length % stride || value.length > maximum
      || value.some(number => typeof number !== 'number' || !Number.isFinite(number) || Math.abs(number) > 100000)) {
    throw new Error(`${label} : données invalides ou trop volumineuses.`);
  }
  return value.slice();
}

export function validateModel(value) {
  const source = value?.model || value;
  const positions = numbers(source?.positions, 'Sommets', 3, MAX_VERTICES * 3);
  const indices = numbers(source?.indices, 'Triangles', 3, MAX_INDICES);
  const count = positions.length / 3;
  if (indices.some(index => !Number.isInteger(index) || index < 0 || index >= count)) {
    throw new Error('Un triangle référence un sommet absent.');
  }
  for (let index = 0; index < indices.length; index += 3) {
    if (new Set(indices.slice(index, index + 3)).size !== 3) throw new Error('Les triangles dégénérés ne sont pas acceptés.');
  }
  const model = { positions, indices };
  if (source.uvs !== undefined) {
    model.uvs = numbers(source.uvs, 'Coordonnées UV', 2, MAX_VERTICES * 2);
    if (model.uvs.length !== count * 2) throw new Error('Une coordonnée UV est requise par sommet.');
  }
  if (source.normals !== undefined) {
    model.normals = numbers(source.normals, 'Normales', 3, MAX_VERTICES * 3);
    if (model.normals.length !== positions.length) throw new Error('Une normale est requise par sommet.');
  }
  if (source.textureId !== undefined && source.textureId !== null) model.textureId = String(source.textureId);
  return model;
}

export function parseObj(text) {
  const vertices = [], coordinates = [], normals = [], positions = [], uvs = [], outputNormals = [], indices = [];
  const corners = new Map();
  let completeUvs = true, completeNormals = true;
  const indexFor = (word, list, required = true) => {
    if (word === undefined || word === '') {
      if (required) throw new Error('Un triangle OBJ ne référence pas de sommet.');
      return null;
    }
    const value = Number(word);
    const result = value < 0 ? list.length + value : value - 1;
    if (!Number.isInteger(value) || value === 0 || result < 0 || result >= list.length) {
      throw new Error('Indice OBJ hors limites.');
    }
    return result;
  };
  const read = (words, stride) => {
    const result = words.slice(0, stride).map(Number);
    if (result.length !== stride || result.some(value => !Number.isFinite(value) || Math.abs(value) > 100000)) {
      throw new Error('Coordonnée OBJ invalide.');
    }
    return result;
  };
  for (const raw of String(text).split(/\r?\n/u)) {
    const [type, ...words] = raw.split('#', 1)[0].trim().split(/\s+/u);
    if (type === 'v') vertices.push(read(words, 3));
    else if (type === 'vt') coordinates.push(read(words, 2));
    else if (type === 'vn') normals.push(read(words, 3));
    else if (type === 'f') {
      if (words.length < 3 || words.length > 1024) throw new Error('Une face OBJ doit avoir de 3 à 1024 sommets.');
      const face = words.map(word => {
        const fields = word.split('/');
        const vertex = indexFor(fields[0], vertices), uv = indexFor(fields[1], coordinates, false), normal = indexFor(fields[2], normals, false);
        const key = `${vertex}/${uv}/${normal}`;
        if (!corners.has(key)) {
          corners.set(key, positions.length / 3);
          positions.push(...vertices[vertex]);
          uvs.push(...(uv === null ? [0, 0] : coordinates[uv]));
          outputNormals.push(...(normal === null ? [0, 0, 0] : normals[normal]));
        }
        completeUvs &&= uv !== null; completeNormals &&= normal !== null;
        return corners.get(key);
      });
      for (let index = 1; index < face.length - 1; index++) indices.push(face[0], face[index], face[index + 1]);
    }
    if (vertices.length > MAX_VERTICES || positions.length > MAX_VERTICES * 3 || indices.length > MAX_INDICES) {
      throw new Error('Le modèle OBJ dépasse la limite de taille.');
    }
  }
  return validateModel({ positions, indices, ...(completeUvs ? { uvs } : {}), ...(completeNormals ? { normals: outputNormals } : {}) });
}

export function parseModel(text, filename = '', upAxis = 'z') {
  if (new TextEncoder().encode(String(text)).byteLength > MAX_MODEL_FILE_BYTES) throw new Error('Le fichier modèle dépasse 12 Mo.');
  let model;
  if (/\.obj$/iu.test(filename)) model = parseObj(text);
  else if (/\.json$/iu.test(filename)) {
    let decoded;
    try { decoded = JSON.parse(text); } catch { throw new Error('Le fichier JSON est illisible.'); }
    model = validateModel(decoded);
  } else throw new Error('Choisissez un modèle OBJ ou JSON.');
  if (upAxis === 'y') {
    for (const values of [model.positions, model.normals].filter(Boolean)) {
      for (let index = 0; index < values.length; index += 3) {
        const y = values[index + 1]; values[index + 1] = -values[index + 2]; values[index + 2] = y;
      }
    }
  }
  return model;
}

export function readPngFile(file, Reader = globalThis.FileReader) {
  if (!file || !/\.png$/iu.test(file.name) || file.size > MAX_TEXTURE_FILE_BYTES || !file.size) {
    return Promise.reject(new Error('Choisissez une texture PNG de 8 Mo maximum.'));
  }
  return new Promise((resolve, reject) => {
    const reader = new Reader();
    reader.onerror = () => reject(new Error('La texture est illisible.'));
    reader.onload = () => {
      const data = String(reader.result || '');
      if (!/^data:image\/png;base64,iVBORw0KGgo/u.test(data)) reject(new Error('Ce fichier n’est pas un PNG valide.'));
      else resolve(data);
    };
    reader.readAsDataURL(file);
  });
}

/** Reject external URLs before Three's loader can make any request. */
export function gltfLocalBufferName(uri) {
  if (/^data:application\/(?:octet-stream|gltf-buffer);base64,/iu.test(uri)) return null;
  let decoded;
  try { decoded = typeof uri === 'string' ? decodeURIComponent(uri) : null; } catch { throw new Error('Nom de tampon glTF invalide.'); }
  if (decoded === null || /[:\\?#]/u.test(decoded) || decoded.startsWith('/') || decoded.split('/').includes('..')) {
    throw new Error('Le modèle glTF doit utiliser uniquement des fichiers locaux sélectionnés.');
  }
  const filename = decoded.split('/').at(-1);
  if (!filename || /[\\/:?#]/u.test(filename)) throw new Error('Nom de tampon glTF invalide.');
  return filename;
}

function readDataUrl(value) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onerror = () => reject(new Error('Tampon du modèle illisible.'));
    reader.onload = () => resolve(String(reader.result)); reader.readAsDataURL(value);
  });
}

export async function parseModelFiles(files, upAxis = 'z') {
  const list = Array.from(files || []);
  const file = list.find(entry => /\.(obj|json|gltf|glb)$/iu.test(entry.name));
  if (!file || list.reduce((sum, entry) => sum + entry.size, 0) > MAX_MODEL_FILE_BYTES) {
    throw new Error('Choisissez un modèle et ses tampons locaux, de 12 Mo maximum.');
  }
  if (!/\.(gltf|glb)$/iu.test(file.name)) return parseModel(await file.text(), file.name, upAxis);
  let document, binary;
  if (/\.glb$/iu.test(file.name)) {
    const bytes = await file.arrayBuffer(), view = new DataView(bytes);
    if (bytes.byteLength < 20 || view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2 || view.getUint32(8, true) !== bytes.byteLength) {
      throw new Error('Fichier GLB 2.0 invalide.');
    }
    for (let offset = 12; offset + 8 <= bytes.byteLength;) {
      const length = view.getUint32(offset, true), kind = view.getUint32(offset + 4, true); offset += 8;
      if (offset + length > bytes.byteLength) throw new Error('Bloc GLB tronqué.');
      if (kind === 0x4e4f534a) document = JSON.parse(new TextDecoder().decode(new Uint8Array(bytes, offset, length)).trim());
      if (kind === 0x004e4942) binary = bytes.slice(offset, offset + length);
      offset += length;
    }
  } else document = JSON.parse(await file.text());
  if (document?.asset?.version !== '2.0') throw new Error('Seuls les modèles glTF 2.0 sont pris en charge.');
  if (document.extensionsRequired?.some(extension => ['KHR_draco_mesh_compression', 'EXT_meshopt_compression'].includes(extension))) {
    throw new Error('Exportez le modèle glTF sans compression Draco ou Meshopt.');
  }
  for (const buffer of document.buffers || []) {
    if (!buffer.uri && binary) buffer.uri = await readDataUrl(new Blob([binary], { type: 'application/octet-stream' }));
    const name = gltfLocalBufferName(buffer.uri);
    if (name !== null) {
      const matches = list.filter(entry => entry.name === name);
      if (matches.length !== 1) throw new Error(`Sélectionnez aussi le tampon du modèle : ${name}`);
      buffer.uri = await readDataUrl(new Blob([await matches[0].arrayBuffer()], { type: 'application/octet-stream' }));
    }
  }
  // Geometry is imported with an explicit level texture selection. Ignore
  // glTF material images rather than fetching unselected files or remote URLs.
  delete document.images; delete document.textures; delete document.materials; delete document.animations;
  for (const mesh of document.meshes || []) for (const primitive of mesh.primitives || []) delete primitive.material;
  const [{ GLTFLoader }, THREE] = await Promise.all([import('./vendor/GLTFLoader.js'), import('./vendor/three.module.js')]);
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(uri => {
    if (!uri.startsWith('data:')) throw new Error('Ressource externe glTF refusée.');
    return uri;
  });
  const gltf = await new GLTFLoader(manager).parseAsync(JSON.stringify(document), '');
  const positions = [], indices = [], uvs = [], normals = [];
  let completeUvs = true, completeNormals = true;
  const point = new THREE.Vector3(), normalMatrix = new THREE.Matrix3();
  gltf.scene.updateMatrixWorld(true);
  gltf.scene.traverse(mesh => {
    if (!mesh.isMesh) return;
    const geometry = mesh.geometry, position = geometry.getAttribute('position');
    if (!position) return;
    const offset = positions.length / 3, uv = geometry.getAttribute('uv'), normal = geometry.getAttribute('normal');
    if (offset + position.count > MAX_VERTICES || indices.length + (geometry.index?.count ?? position.count) > MAX_INDICES) {
      throw new Error('Le modèle assemblé dépasse 100 000 sommets ou 200 000 triangles.');
    }
    normalMatrix.getNormalMatrix(mesh.matrixWorld);
    for (let index = 0; index < position.count; index++) {
      point.fromBufferAttribute(position, index).applyMatrix4(mesh.matrixWorld); positions.push(point.x, point.y, point.z);
      uvs.push(uv ? uv.getX(index) : 0, uv ? uv.getY(index) : 0);
      if (normal) { point.fromBufferAttribute(normal, index).applyNormalMatrix(normalMatrix); normals.push(point.x, point.y, point.z); }
      else normals.push(0, 0, 0);
    }
    completeUvs &&= !!uv; completeNormals &&= !!normal;
    const triangles = geometry.index ? Array.from(geometry.index.array) : Array.from({ length: position.count }, (_, index) => index);
    if (mesh.matrixWorld.determinant() < 0) for (let index = 0; index < triangles.length; index += 3) [triangles[index + 1], triangles[index + 2]] = [triangles[index + 2], triangles[index + 1]];
    for (const index of triangles) indices.push(index + offset);
    geometry.dispose();
    (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).forEach(material => material?.dispose());
  });
  return parseModel(JSON.stringify({ positions, indices, ...(completeUvs ? { uvs } : {}), ...(completeNormals ? { normals } : {}) }), 'import.json', upAxis);
}
