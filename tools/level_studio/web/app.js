import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';
import { TransformControls } from '/vendor/TransformControls.js';
import { effectiveParentRows } from '/source-transform.mjs';
import { createFreeNavigation } from '/navigation.mjs';
import { initLanguage, getLanguage, onLanguageChange, translate, translateLevelName, formatNumber, formatSize } from '/i18n.mjs';
import { createSourceImport } from '/source-import.mjs';
import { parseModelFiles, readPngFile } from '/asset-import.mjs';
import { decodeSceneTransport } from '/scene-transport.mjs';
import { createLevelManager } from '/levels.js';
import { catalogSection, catalogEntries, catalogView } from '/level-catalog.mjs';

const $ = id => document.getElementById(id);
const number = value => formatNumber(Math.round(Number(value) || 0));
const round = value => Math.round(Number(value) * 1000) / 1000;
const size = bytes => formatSize(bytes);
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const vector = array => new THREE.Vector3(...(Array.isArray(array) ? array : [0, 0, 0]));
const samePosition = (a, b) => a.every((n, i) => Math.abs(n - b[i]) < 0.0001);
const sourceOffset = value => typeof value === 'number' ? `0x${value.toString(16).toUpperCase()}` : (value ?? '—');
const isEditable = item => !!item && !item.locked && (item.previewEditable || (item.editable !== false && state.data?.capabilities?.[item.kind === 'mesh' ? 'meshTranslation' : 'entityTranslation'] !== false));
const groupLabel = value => translate(({ air: 'Domaine de l’Air', earth: 'Domaine de la Terre', fire: 'Domaine du Feu', water: 'Domaine de l’Eau', life: 'Domaine de la Vie', perathia: 'Perathia', death: 'Domaine de la Mort', cinematic: 'Cinématiques', other: 'Autres niveaux' })[value] || value || 'Niveaux du jeu');
const isWorldSpace = item => !!(item?.worldPositionVerified || item?.coordinateSpace === 'world' || item?.instance);
const isTransformEditable = (item, mode) => !!item?.localRotation && !!item?.localScale && !item.locked && (item.previewEditable || (item.editable !== false && state.data?.capabilities?.[`${item.kind === 'mesh' ? 'mesh' : 'entity'}${mode === 'rotate' ? 'Rotation' : 'Scale'}`] !== false && Number.isFinite(item.editBinding?.[mode === 'rotate' ? 'rotationOffset' : 'scaleOffset'])));
const matrixFromRows = values => new THREE.Matrix4().set(...values);
const rowsFromMatrix = matrix => { const e = matrix.elements; return [0, 1, 2, 3].flatMap(r => [0, 1, 2, 3].map(c => e[c * 4 + r])); };
const eulerOrder = item => item?.previewEditable ? 'ZYX' : ['ZYX', 'XZY', 'YXZ', 'XYZ', 'ZXY', 'YZX'][nodeMap.get(item?.editBinding?.transformNode)?.rotationOrder || 0];

const state = {
  levels: [], level: null, data: null, catalog: null, catalogSection: 'levels', catalogLast: {}, items: new Map(), selected: null, sky: 'day', detail: 'close',
  filter: 'all', query: '', textures: true, grid: true, wire: false, collisions: false,
  helpers: true, models: true, fly: true, assetTab: 'textures', assetQuery: '', assetScope: 'level', assetPage: 0, archive: '', libraryData: null, libraryLoading: false, libraryError: '', libraryRequest: 0, nodeFilter: '', tool: 'translate', animation: true, animationFps: 12, exposure: 1, background: 'studio', pending: 0, unsaved: false,
  canUndo: false, canRedo: false, busy: false, loading: false, request: 0,
};
initLanguage();

const viewport = $('viewport');
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
// The game's fixed combiner operates on stored RGB values. Avoid applying
// Three's sRGB decode/encode around those original byte-space operations.
renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
renderer.toneMapping = THREE.NoToneMapping;
renderer.setClearColor(0x000000, 0);
renderer.domElement.setAttribute('aria-label', 'Rendu 3D des données Azurik');
renderer.domElement.tabIndex = 0;
viewport.prepend(renderer.domElement);

const scene = new THREE.Scene();
// Retail C1AA0 renders the sky with its own serialized camera origin, then
// restores the world camera. Moving through the level must not leave it behind.
const skyScene = new THREE.Scene();
const skyWorld = new THREE.Group();
skyScene.add(skyWorld);
const skyCamera = new THREE.PerspectiveCamera(50, 1, 0.01, 1000);
skyCamera.up.set(0, 0, 1);
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000000);
camera.up.set(0, 0, 1);
camera.position.set(80, -100, 80);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.enableDamping = true;
orbit.dampingFactor = 0.07;
orbit.screenSpacePanning = true;
orbit.maxPolarAngle = Math.PI;
const transform = new TransformControls(camera, renderer.domElement);
transform.setMode('translate');
transform.setSpace('world');
transform.setSize(0.85);
const gizmoHelper = typeof transform.getHelper === 'function' ? transform.getHelper() : transform;
scene.add(gizmoHelper);

const world = new THREE.Group();
const entityGroup = new THREE.Group();
scene.add(world, entityGroup);
let collisionMesh = null;
let grid = null;
let selectionBox = null;
let worldBounds = new THREE.Box3();
let worldRadius = 100;
let textureResources = new Map();
const textureCache = new Map();
let renderDirty = true;
const materialResources = new Map();
const animationResources = new Map();
let nodeMap = new Map();
let nodeAncestors = new Map();
let cameraFacingItems = [];
let cameraFacingQuaternion = null;
const previewExposure = { value: 1 };
const transformParent = new THREE.Object3D();
transformParent.matrixAutoUpdate = false;
const transformProxy = new THREE.Object3D();
transformParent.add(transformProxy);
scene.add(transformParent);
let transformPreview = null;
let dialogAnimation = null;
let characterCatalog = [];
let dialogModel = null;
let libraryArchives = null;
let libraryController = null;
const libraryCache = new Map();
const assetPageSize = 72;
let dragOrigin = null;
let pointerOrigin = null;
let toastTimeout = null;
let pendingMutation = Promise.resolve();
let fpsFrames = 0;
let fpsMotionFrames = 0;
let fpsStart = performance.now();
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const clock = new THREE.Clock();
const cameraHudDirection = new THREE.Vector3();
let cameraHudTime = 0;
const freeNavigation = createFreeNavigation({ THREE, camera, orbit, viewport, element: renderer.domElement,
  getLocale: getLanguage, getSpeed: () => Number($('flySpeedSelect').value), getRadius: () => worldRadius,
  getBlocked: () => state.loading || state.busy || state.importing || transform.dragging || $('infoDialog').open,
  windowTarget: window, documentTarget: document,
  onEnabledChange: value => { state.fly = value; $('flyBtn').classList.toggle('active', value); $('flyBtn').setAttribute('aria-pressed', String(value)); updateNavigationHint(); } });

function setStatus(message, error = false) {
  $('statusText').textContent = translate(message);
  $('connectionDot').classList.toggle('error', error);
}

function toast(message, error = false) {
  clearTimeout(toastTimeout);
  $('toast').textContent = translate(message);
  $('toast').classList.toggle('error', error);
  $('toast').classList.add('visible');
  toastTimeout = setTimeout(() => $('toast').classList.remove('visible'), error ? 6000 : 4000);
}

async function api(path, body, options = {}) {
  const headers = { ...options.headers, 'X-Studio-Geometry': 'packed-v1' };
  const response = await fetch(path, body === undefined ? { ...options, headers } : {
    ...options,
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  let data;
  try { data = await response.json(); } catch { throw new Error(`Le serveur a renvoyé une réponse illisible (${response.status}).`); }
  if (!response.ok || data.error) throw new Error(data.error || data.message || `Erreur ${response.status}`);
  return decodeSceneTransport(data);
}

function overlay(title, text, error = false) {
  $('overlayTitle').textContent = translate(title);
  $('overlayText').textContent = translate(text);
  $('sceneOverlay').classList.toggle('error', error);
  $('sceneOverlay').hidden = false;
  $('retryBtn').hidden = !error;
}

function updateActions() {
  const available = !!state.data && !state.loading && !state.busy && !state.importing;
  $('saveBtn').disabled = !available;
  $('exportBtn').disabled = !available || (state.pending === 0 && !state.data?.previewCount);
  $('exportBtn').title = state.pending ? 'Créer une copie des fichiers modifiés' : 'Aucune modification à exporter';
  $('undoBtn').disabled = !available || !state.canUndo;
  $('redoBtn').disabled = !available || !state.canRedo;
  $('levelSelect').disabled = state.loading || state.busy || state.importing || !catalogEntries(state.levels, state.catalogSection).length;
  document.querySelectorAll('[data-catalog-section]').forEach(button => {
    button.disabled = state.loading || state.busy || state.importing || !catalogEntries(state.levels, button.dataset.catalogSection).length;
  });
  transform.enabled = available;
  const editable = available && isEditable(state.items.get(state.selected));
  ['posX', 'posY', 'posZ', 'resetPositionBtn'].forEach(id => { $(id).disabled = !editable; });
  ['rotX', 'rotY', 'rotZ'].forEach(id => { $(id).disabled = !available || !isTransformEditable(state.items.get(state.selected), 'rotate'); });
  ['scaleX', 'scaleY', 'scaleZ'].forEach(id => { $(id).disabled = !available || !isTransformEditable(state.items.get(state.selected), 'scale'); });
  $('resetTransformBtn').disabled = !available || !(isTransformEditable(state.items.get(state.selected), 'rotate') || isTransformEditable(state.items.get(state.selected), 'scale'));
  $('isolateBtn').disabled = !available || !state.selected;
  $('lockBtn').disabled = !available || !state.selected || (state.items.get(state.selected)?.locked && !state.items.get(state.selected)?.lockedExplicitly);
  $('lockAllBtn').disabled = !available;
  $('unlockAllBtn').disabled = !available;
  const selectedMesh = state.items.get(state.selected)?.kind === 'mesh';
  const deletedAddition = /^import-/u.test(state.selected || '') && selectedMesh;
  const actions = { save: available, export: available && (state.pending > 0 || state.data?.previewCount > 0), undo: available && state.canUndo,
    redo: available && state.canRedo, duplicate: available && selectedMesh && !state.items.get(state.selected)?.locked,
    delete: available && deletedAddition && !state.items.get(state.selected)?.locked, resetLevel: available,
    importModel: available, replaceModel: available && selectedMesh && !state.items.get(state.selected)?.locked,
    importTexture: available, replaceTexture: available && !!state.data?.textures?.length, importIso: !state.loading && !state.busy && !state.importing,
    createLevel: available, deleteLevel: available && !state.catalog?.levels?.find(level => level.id === state.level)?.technical && state.catalog?.levels?.find(level => level.id === state.level)?.canClone !== false,
    manageLevels: available, buildIso: available && !!state.exportDirectory };
  document.querySelectorAll('[data-action]').forEach(button => { if (button.dataset.action in actions) button.disabled = !actions[button.dataset.action]; });
  $('importIsoBtn').disabled = state.loading || state.busy || state.importing;
  const selected = state.items.get(state.selected);
  $('lockBtn').querySelector('span').textContent = translate(selected?.locked ? 'Déverrouiller' : 'Verrouiller');
  $('lockBtn').querySelector('use').setAttribute('href', selected?.locked ? '#i-unlock' : '#i-lock');
  $('lockBtn').setAttribute('aria-pressed', String(!!selected?.locked));
  $('dirtyDot').hidden = !state.unsaved;
  const pending = state.pending ? `${number(state.pending)} modification${state.pending > 1 ? 's' : ''} du jeu` : 'Aucune modification';
  $('pendingStatus').textContent = translate(pending) + (state.data?.previewCount ? ` · ${number(state.data.previewCount)} ${translate('modifications d’aperçu')}` : '');
}

function disposeObject(object, disposeMaterials = true) {
  object.traverse(child => {
    child.geometry?.dispose();
    const materials = child.material ? (Array.isArray(child.material) ? child.material : [child.material]) : [];
    if (disposeMaterials) materials.forEach(material => material.dispose());
  });
  object.removeFromParent();
}

function cleanScene(reuseTextures = false) {
  transform.detach();
  if (selectionBox) { disposeObject(selectionBox); selectionBox = null; }
  [...world.children].forEach(object => disposeObject(object, false));
  [...skyWorld.children].forEach(object => disposeObject(object, false));
  [...entityGroup.children].forEach(object => { object.children.filter(child => !child.userData.model).forEach(child => child.material?.dispose()); disposeObject(object, false); });
  if (collisionMesh) { disposeObject(collisionMesh); collisionMesh = null; }
  if (grid) { disposeObject(grid); grid = null; }
  if (!reuseTextures) { new Set(textureCache.values()).forEach(texture => texture.dispose()); textureCache.clear(); }
  textureResources.clear();
  materialResources.forEach(material => material.dispose());
  materialResources.clear();
  animationResources.clear();
  transformPreview = null;
  state.items.clear();
  cameraFacingItems = [];
  cameraFacingQuaternion = null;
  state.selected = null;
  $('selectionLabel').hidden = true;
}

function gameMaterial(textureId, hasColors, info = {}, stage = {}, context = null, source = null, reflected = false) {
  const resources = context?.textures || textureResources;
  const cache = context?.materials || materialResources;
  const sourceHasColors = hasColors;
  // Techniques 0/1 share d6a60's primary RGB combiner; d6cd0 (technique 4)
  // starts with that same flag-controlled operation. Other programs must
  // not inherit an unverified multiplication by their vertex RGB.
  hasColors = hasColors && (info.technique === undefined || [0, 1, 4, 6].includes(info.technique));
  const sourceOpacity = Number.isFinite(info.opacity) && info.opacity >= 0 && info.opacity <= 1 ? info.opacity : 1;
  // 79510 -> C5F20 -> C3BBC -> D6D3B: these instance channels multiply
  // GSHD opacity. In A5, Fog_Sphere uses gameVisible=.75; ignoring it
  // turns the enclosing fog geometry into an opaque black background.
  const platform = source?.platformRender;
  // Keep the existing inspectable preview of discrete gameVisible=0/1
  // states: their gameState/script connections are not simulated. Apply
  // the authored fractional opacities (including A5 fog=.75). Explicit
  // day/night sky selection takes over this channel for its chosen phase.
  // Serialized values remain untouched, and visibility uses the eye/LOD.
  const platformOpacity = platform?.sourceVerified && [platform.gameVisible, platform.visibility].every(value => Number.isFinite(value) && value >= 0 && value <= 1) ? (source.sceneRole === 'sky' || platform.gameVisible === 0 ? 1 : platform.gameVisible) * platform.visibility : 1;
  const opacity = sourceOpacity * platformOpacity;
  const alphaRef = Number.isFinite(info.alphaReference) && info.alphaReference >= 0 && info.alphaReference <= 1 ? info.alphaReference : 0.1;
  const map = textureId !== undefined && textureId !== null ? resources.get(String(textureId)) : null;
  const stages = source?.textureStages || [];
  const textureMetadata = context?.textureMetadata || state.data?.textures || [];
  // D6120 inherits mode 1 for flag 0x1000. Opcode 9 then selects V7
  // (UV0), opcode 10 selects V8 (UV1): 9A250 / table 1ABA88. The opaque
  // runtime textureSlot field never selects a coordinate channel.
  const layers = stages.map((entry, index) => {
    const flags = entry.flags || 0;
    const inherited = !!(flags & 0x1000);
    const mode = inherited ? 1 : flags & 3;
    const channel = mode === 1 ? 0 : mode === 2 ? 1 : -1;
    // D620F opcode16: fragment38 reflects the local eye vector. 9A250
    // uses fragment41/view rows with a UV transform, or 44/world X/-Y.
    const reflection = info.technique === 4 && !inherited && mode === 1 && (flags & 0xe00) === 0x800 && retailCoordinatesVerified(source);
    const transform = entry.uvTransform;
    const scrolling = transform?.scrollVelocity?.some(value => value !== 0);
    const resource = textureMetadata.find(texture => String(texture.id) === String(index === 0 ? textureId : entry.textureId));
    return {
      id: index === 0 ? textureId : entry.textureId,
      channel,
      reflection,
      reflectionTransformed: reflection && (transform?.scale !== 1 || (scrolling ? [0, 0] : transform?.offset || []).some(value => value !== 0)),
      mode: entry.combinerMode === 500 ? 1 : entry.combinerMode,
      scale: transform?.scale,
      // Clock units are not verified. Evaluate scrolling coordinates at
      // gameTime=0, where the game ignores the authored static offset.
      offset: scrolling ? [0, 0] : transform?.offset,
      valid: resource?.kind !== 'cube' && entry.stage === index && channel >= 0 && (reflection || inherited || !(flags & 0xe00)) &&
        transform?.parametersVerified && transform.rotationDegrees === 0 && transform.matrixMode !== 2 &&
        [transform.scale, ...(transform.offset || [])].every(Number.isFinite) && transform.offset?.length === 2 &&
        (reflection || (channel === 0 ? source?.uvs?.length : source?.uv2?.length) >= source.positions.length / 3 * 2) &&
        resources.has(String(index === 0 ? textureId : entry.textureId)),
    };
  });
  // D6CD0: exactly 2/3 layers. RGB operators are the verified table at
  // 1A99C0; diffuse-alpha interpolation (mode 4) still needs its V0 alpha.
  const layered = info.technique === 4 && [2, 3].includes(layers.length) &&
    layers.every((entry, index) => entry.valid && (index === 0 || [1, 2, 3, 5, 6, 7].includes(entry.mode)));
  // D6ED0 routes source layers 0/2/1 to GPU T0/T1/T2 and emits
  // 0A190C39: lerp(source0.rgb, source1.rgb, source2.a). Its final
  // optional MODULATE2X uses V0 RGB, and alpha is only C0 opacity.
  const weighted = info.technique === 6 && layers.length === 3 && layers.every(entry => entry.valid) && (!(info.flags & 0x10) || hasColors);
  const terrainLimit = info.technique === 6 && !weighted ?
    layers.some(entry => entry.channel === 1 && !(source?.uv2?.length >= source.positions.length / 3 * 2)) ? 'UV1 absent : mélange du terrain non reproduit' :
      info.flags & 0x10 && !hasColors ? 'Couleur de sommet absente : éclairage du terrain non reproduit' : 'Coordonnées ou couches du terrain non décodées' : '';
  if (info.technique === 6 && !weighted) hasColors = false;
  const combined = layered || weighted;
  const reflection = combined && layers.some(entry => entry.reflection);
  const layerKey = combined ? JSON.stringify(layers.map(({ id, channel, reflection, reflectionTransformed, mode, scale, offset }) => [id, channel, reflection, reflectionTransformed, mode, scale, offset])) : '';
  const layerProgramKey = combined ? JSON.stringify(layers.map(({ channel, reflection, reflectionTransformed, mode, scale, offset }) => [channel, reflection, reflectionTransformed, mode, scale, offset])) : '';
  // Advanced Xbox combiners compute alpha from several inputs. T0 alpha
  // alone is not coverage (e.g. Azurik technique 8 stores a material mask).
  // D7D80 dispatches both techniques 0 and 1 to D6A60. The parameter
  // changes generated coordinates, not the T0 × opacity alpha combiner.
  const primaryCombiner = [0, 1].includes(info.technique);
  const complexAlpha = Number.isInteger(info.technique) && !primaryCombiner && !combined;
  const alphaFunction = complexAlpha ? 0 : info.alphaFunction === 500 ? 5 : info.alphaFunction ?? 0;
  // D5C00 checks every used stage, including secondary alpha masks.
  const automaticBlend = opacity < 1 || (layered ? stages.some(entry => !!(entry.flags & 0x20000000)) : !!(stage.flags & 0x20000000));
  const blendType = weighted ? (opacity < 1 ? 4 : 0) : primaryCombiner || layered ? (info.blendType === 500 ? (automaticBlend ? 4 : 0) : info.blendType ?? (automaticBlend ? 4 : 0)) : (opacity < 1 ? 4 : 0);
  const uv = stage.uvTransform;
  const simpleUV = info.technique === 0 && uv?.parametersVerified && (stage.flags & 0x1000 || (stage.flags & 3) === 1) && !(stage.flags & 0xe00) && uv.rotationDegrees === 0 && uv.matrixMode !== 2 && !(uv.scrollVelocity || []).some(value => value !== 0);
  const uvKey = simpleUV ? `${uv.scale}:${uv.offset}` : '';
  const side = retailCullSide(info.flags, reflected);
  const lighting = retailLightingFor(source, hasColors);
  const lightingKey = lighting ? JSON.stringify([lighting, source.worldMatrix, source.position]) : '';
  const reflectionKey = reflection ? JSON.stringify([source.worldMatrix, source.position]) : '';
  const key = `${textureId ?? 'none'}:${info.technique}:${hasColors ? 'vertex' : 'neutral'}:${opacity}:${alphaFunction}:${alphaRef}:${blendType}:${info.depthWrite}:${uvKey}:${complexAlpha}:${layerKey}:${terrainLimit}:${side}:${lightingKey}:${reflectionKey}`;
  if (cache.has(key)) return cache.get(key);
  const material = new THREE.MeshBasicMaterial({
    color: hasColors || map ? 0xffffff : 0x99b0bc,
    map: context || state.textures ? map || null : null,
    vertexColors: hasColors,
    side,
    alphaTest: 0,
    opacity, transparent: blendType !== 0, depthWrite: info.depthWrite === 1 ? true : info.depthWrite === 2 ? false : blendType === 0,
    wireframe: context ? false : state.wire,
  });
  material.userData.originalMap = map || null;
  material.userData.animationId = !context && animationResources.has(String(textureId)) ? String(textureId) : null;
  material.userData.layeredCombiner = combined;
  material.userData.weightedTerrain = weighted;
  material.userData.renderLimit = terrainLimit || (info.technique === 4 && !layered ? 'Effet de matière partiellement reproduit' : '');
  material.userData.sourceLighting = lighting;
  material.userData.retailReflection2D = reflection;
  // Keep shared, immutable variants. Mutating one shared material's side
  // would also change raycasting and rendering of unrelated instances.
  material.userData.retailCullVariant = value => gameMaterial(textureId, sourceHasColors, info, stage, context, source, value);
  const blendFactors = { 1: [THREE.DstColorFactor, THREE.ZeroFactor], 2: [THREE.DstColorFactor, THREE.SrcColorFactor], 3: [THREE.OneFactor, THREE.OneFactor], 4: [THREE.SrcAlphaFactor, THREE.OneMinusSrcAlphaFactor], 5: [THREE.SrcAlphaFactor, THREE.OneFactor], 6: [THREE.OneFactor, THREE.OneMinusSrcAlphaFactor] };
  if (blendFactors[blendType]) { material.blending = THREE.CustomBlending; [material.blendSrc, material.blendDst] = blendFactors[blendType]; material.blendEquation = THREE.AddEquation; }
  const comparisons = { 1: 'false', 2: 'alphaByte < alphaRefByte', 3: 'alphaByte == alphaRefByte', 4: 'alphaByte <= alphaRefByte', 5: 'alphaByte > alphaRefByte', 6: 'alphaByte != alphaRefByte', 7: 'alphaByte >= alphaRefByte', 8: 'true' };
  const compare = comparisons[alphaFunction];
  material.onBeforeCompile = shader => {
    shader.uniforms.previewExposure = previewExposure;
    shader.fragmentShader = `uniform float previewExposure;\n${shader.fragmentShader}`;
    const alphaComparison = compare ? `float alphaByte = floor(diffuseColor.a * 255.0 + 0.5); float alphaRefByte = ${Math.floor(alphaRef * 255).toFixed(1)}; if (!(${compare})) discard;` : '';
    shader.fragmentShader = shader.fragmentShader.replace('#include <alphatest_fragment>', complexAlpha ? `diffuseColor.a = ${opacity.toFixed(8)};` : `${weighted ? `diffuseColor.a = ${opacity.toFixed(8)};` : ''}${alphaComparison}`);
    shader.fragmentShader = shader.fragmentShader.replace('#include <opaque_fragment>', 'outgoingLight *= previewExposure;\n#include <opaque_fragment>');
    if (combined) {
      const coordinate = entry => `${entry.reflection ? entry.reflectionTransformed ? 'retailReflectionViewUv' : 'retailReflectionWorldUv' : entry.channel === 0 ? 'uv' : 'uv1'} * ${entry.scale.toFixed(8)} + vec2(${entry.offset.map(value => value.toFixed(8)).join(',')})`;
      const varyingDeclarations = layers.slice(1).map((entry, index) => `varying vec2 vGameLayerUv${index + 1};`).join('\n');
      if (layers.some(entry => entry.channel === 1)) shader.vertexShader = `#ifndef USE_UV1\nattribute vec2 uv1;\n#endif\n${shader.vertexShader}`;
      shader.vertexShader = `${varyingDeclarations}\n${shader.vertexShader}`;
      if (reflection) {
        shader.uniforms.retailSourceWorld = { value: new THREE.Matrix4() };
        shader.uniforms.retailSourceInverse = { value: new THREE.Matrix4() };
        shader.uniforms.retailReflectionView = { value: new THREE.Matrix4() };
        shader.uniforms.retailEyeLocal = { value: new THREE.Vector3() };
        shader.vertexShader = `${lighting ? '' : 'attribute vec3 retailSourceNormal;\n'}uniform mat4 retailSourceWorld;\nuniform mat4 retailSourceInverse;\nuniform mat4 retailReflectionView;\nuniform vec3 retailEyeLocal;\n${shader.vertexShader}`;
        material.userData.retailReflectionUniforms ||= new Set();
        material.userData.retailReflectionUniforms.add(shader.uniforms);
        updateRetailReflection(material, source, material.userData.retailReflectionObject, material.userData.retailReflectionCamera);
      }
      // 9A250 uses fragment41 (view XY) when a UV transform follows,
      // but fragment44 (world X/-Y) for the untransformed opcode16.
      const generated = reflection ? 'vec3 retailLocalPosition = (retailSourceInverse * modelMatrix * vec4(position, 1.0)).xyz;\nvec3 retailToEye = normalize(retailEyeLocal - retailLocalPosition);\nvec3 retailReflectedLocal = 2.0 * dot(retailSourceNormal, retailToEye) * retailSourceNormal - retailToEye;\nvec2 retailReflectionWorldUv = (mat3(retailSourceWorld) * retailReflectedLocal).xy * vec2(1.0, -1.0);\nvec2 retailReflectionViewUv = (mat3(retailReflectionView) * retailReflectedLocal).xy;\n' : '';
      shader.vertexShader = shader.vertexShader.replace('#include <uv_vertex>', `#include <uv_vertex>\n#ifdef USE_MAP\n${generated}vMapUv = ${coordinate(layers[0])};\n${layers.slice(1).map((entry, index) => `vGameLayerUv${index + 1} = ${coordinate(entry)};`).join('\n')}\n#endif`);
      const uniforms = layers.slice(1).map((entry, index) => {
        const name = `gameLayerMap${index + 1}`;
        shader.uniforms[name] = { value: resources.get(String(entry.id)) };
        return `uniform sampler2D ${name};`;
      }).join('\n');
      shader.fragmentShader = `${varyingDeclarations}\n${uniforms}\n${shader.fragmentShader}`;
      const expressions = {
        1: (rgb, sample) => `${rgb} * ${sample}`,
        2: (rgb, sample) => `2.0 * ${rgb} * ${sample}`,
        3: (rgb, sample) => `${rgb} + ${sample}`,
        5: (rgb, sample) => `4.0 * ${rgb} * ${sample}`,
        6: (rgb, sample) => `${rgb} + ${sample} - vec3(0.5)`,
        7: (rgb, sample) => `2.0 * (${rgb} + ${sample} - vec3(0.5))`,
      };
      if (weighted) {
        shader.fragmentShader = shader.fragmentShader.replace('#include <map_fragment>', '#include <map_fragment>\n#ifdef USE_MAP\nvec4 gameTerrainColor = texture2D(gameLayerMap1, vGameLayerUv1);\nvec4 gameTerrainMask = texture2D(gameLayerMap2, vGameLayerUv2);\ndiffuseColor.rgb = mix(diffuseColor.rgb, gameTerrainColor.rgb, gameTerrainMask.a);\n#endif');
        // Vertex MODULATE2X follows the blend, not each sampled layer.
        shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', '#include <color_fragment>\n#ifdef USE_MAP\ndiffuseColor.rgb = clamp(diffuseColor.rgb, 0.0, 1.0);\n#endif');
      } else {
        const operations = layers.slice(1).map((entry, index) => {
          const number = index + 1;
          return `vec4 gameLayer${number} = texture2D(gameLayerMap${number}, vGameLayerUv${number});\ndiffuseColor.rgb = clamp(${expressions[entry.mode]('diffuseColor.rgb', `gameLayer${number}.rgb`)}, 0.0, 1.0);\ndiffuseColor.a *= gameLayer${number}.a;`;
        }).join('\n');
        // The Xbox clamps each combiner result. Alpha multiplies T0, T1,
        // optional T2, and opacity independently of those RGB operators.
        shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', `#include <color_fragment>\n#ifdef USE_MAP\ndiffuseColor.rgb = clamp(diffuseColor.rgb, 0.0, 1.0);\n${operations}\n#endif`);
      }
      material.userData.layerUniformSets ||= new Set();
      material.userData.layerUniformSets.add(shader.uniforms);
    } else if (simpleUV && [uv.scale, ...uv.offset].every(Number.isFinite)) shader.vertexShader = shader.vertexShader.replace('#include <uv_vertex>', `#include <uv_vertex>\n#ifdef USE_MAP\nvMapUv = vMapUv * ${uv.scale.toFixed(8)} + vec2(${uv.offset.map(value => value.toFixed(8)).join(',')});\n#endif`);
    if (lighting) installRetailLighting(shader, lighting, source, material);
  };
  material.onBeforeRender = (_renderer, _scene, _camera, _geometry, object) => {
    if (lighting) updateRetailLighting(material, lighting, source, object);
    if (reflection) updateRetailReflection(material, source, object, _camera);
    if (!combined) return;
    const uniformSets = material.userData.layerUniformSets;
    if (!uniformSets) return;
    layers.slice(1).forEach((entry, index) => {
      const animation = !context && animationResources.get(String(entry.id));
      const texture = animation ? animation.frames[animation.frame] : resources.get(String(entry.id));
      uniformSets.forEach(uniforms => {
        const uniform = uniforms[`gameLayerMap${index + 1}`];
        if (uniform.value !== texture) { uniform.value = texture; material.needsUpdate = true; }
      });
    });
  };
  material.customProgramCacheKey = () => `${info.technique}:${alphaFunction}:${Math.floor(alphaRef * 255)}:${uvKey}:${complexAlpha}:${opacity}:${layerProgramKey}:${lighting ? lighting.directionals.length + ':lit' : 'baked'}`;
  cache.set(key, material);
  return material;
}

function retailLightingFor(source, hasColors) {
  const lighting = source?.sourceLighting;
  const rgb = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
  if (!hasColors || !source?.textureStages?.length || !lighting?.sourceVerified || !rgb(lighting.ambient) || !rgb(lighting.emissive) ||
      !Array.isArray(lighting.directionals) || lighting.directionals.length > 5 ||
      !lighting.directionals.every(light => rgb(light.direction) && Math.hypot(...light.direction) > 0 && rgb(light.diffuse)) ||
      source.sourceNormals?.length !== source.positions.length || !source.sourceNormals.every(Number.isFinite) ||
      source.worldMatrix?.length !== 16 || !source.worldMatrix.every(Number.isFinite) ||
      Math.abs(new THREE.Matrix4().set(...source.worldMatrix).determinant()) < 1e-12) return null;
  return lighting;
}

function retailCoordinatesVerified(source) {
  return source?.worldPositionVerified === true && source.coordinateSpace === 'world' &&
    source.sourceNormals?.length === source.positions?.length && source.sourceNormals.every(Number.isFinite) &&
    source.worldMatrix?.length === 16 && source.worldMatrix.every(Number.isFinite) &&
    Math.abs(new THREE.Matrix4().set(...source.worldMatrix).determinant()) > 1e-12;
}

function retailSourceMatrix(source, object) {
  if (!object) return null;
  const centre = source.position || [0, 0, 0];
  const matrix = object.matrixWorld.clone().multiply(new THREE.Matrix4().makeTranslation(-centre[0], -centre[1], -centre[2])).multiply(matrixFromRows(source.worldMatrix));
  return Math.abs(matrix.determinant()) < 1e-12 ? null : matrix;
}

function retailSourceTransform(material, source, object) {
  if (!object) return null;
  const caches = material.userData.retailSourceTransformCaches ||= {
    lastObject: null, lastTransform: null, instances: new WeakMap(),
  };
  let cache = caches.lastObject === object ? caches.lastTransform : caches.instances.get(object);
  if (!cache) {
    cache = { matrix: new THREE.Matrix4(), world: new THREE.Matrix4(),
      inverse: new THREE.Matrix4(), version: 0, valid: false };
    caches.instances.set(object, cache);
  }
  caches.lastObject = object; caches.lastTransform = cache;
  // A material can be shared by several instances. Compare both the instance
  // and its complete world matrix, including shear and mirrored live edits.
  if (!cache.version || !cache.matrix.equals(object.matrixWorld)) {
    cache.matrix.copy(object.matrixWorld);
    const world = retailSourceMatrix(source, object);
    cache.valid = !!world;
    if (world) { cache.world.copy(world); cache.inverse.copy(world).invert(); }
    cache.version++;
  }
  return cache.valid ? cache : null;
}

function updateRetailReflection(material, source, object, camera) {
  if (object) material.userData.retailReflectionObject = object;
  if (camera) material.userData.retailReflectionCamera = camera;
  const sets = material.userData.retailReflectionUniforms;
  if (!sets || !object || !camera) return;
  const transform = retailSourceTransform(material, source, object);
  if (!transform) return;
  const cache = material.userData.retailReflectionCache ||= {
    source: null, worldVersion: -1, uniformCount: -1, camera: new THREE.Matrix4(), view: new THREE.Matrix4(),
    eye: new THREE.Vector3(), viewWorld: new THREE.Matrix4(),
  };
  if (cache.source === transform && cache.worldVersion === transform.version && cache.uniformCount === sets.size &&
      cache.camera.equals(camera.matrixWorld) && cache.view.equals(camera.matrixWorldInverse)) return;
  cache.source = transform; cache.worldVersion = transform.version; cache.uniformCount = sets.size;
  cache.camera.copy(camera.matrixWorld); cache.view.copy(camera.matrixWorldInverse);
  cache.eye.setFromMatrixPosition(camera.matrixWorld).applyMatrix4(transform.inverse);
  cache.viewWorld.copy(camera.matrixWorldInverse).multiply(transform.world);
  sets.forEach(uniforms => {
    uniforms.retailSourceWorld.value.copy(transform.world);
    uniforms.retailSourceInverse.value.copy(transform.inverse);
    uniforms.retailEyeLocal.value.copy(cache.eye);
    uniforms.retailReflectionView.value.copy(cache.viewWorld);
  });
}

function installRetailLighting(shader, lighting, source, material) {
  // Retail fragments 45/47/50: V5 + emission + ambient, then each
  // max(dot(source normal, local direction), 0) * material/light diffuse.
  // The source normal remains in its original quantized local space.
  const count = lighting.directionals.length;
  const multiplier = source.textureStages?.length ? 2 : 1;
  shader.uniforms.retailBaseLight = { value: new THREE.Vector3(...lighting.emissive.map((value, axis) => value + lighting.ambient[axis])) };
  if (count) {
    shader.uniforms.retailLightDirections = { value: lighting.directionals.map(() => new THREE.Vector3()) };
    shader.uniforms.retailLightDiffuse = { value: lighting.directionals.map(light => new THREE.Vector3(...light.diffuse)) };
  }
  const declarations = `attribute vec3 retailSourceNormal;\nuniform vec3 retailBaseLight;\n${count ? `uniform vec3 retailLightDirections[${count}];\nuniform vec3 retailLightDiffuse[${count}];` : ''}`;
  shader.vertexShader = `${declarations}\n${shader.vertexShader}`;
  const additions = lighting.directionals.map((_, index) => `retailLight += max(dot(retailSourceNormal, retailLightDirections[${index}]), 0.0) * retailLightDiffuse[${index}];`).join('\n');
  // vtxD0 is clamped before raster interpolation (xemu glsl/vsh.c).
  // The texture combiner's MODULATE2X then uses this saturated color.
  shader.vertexShader = shader.vertexShader.replace('#include <color_vertex>', `#include <color_vertex>\n#ifdef USE_COLOR\nvec3 retailLight = retailBaseLight;\n${additions}\nvColor = ${multiplier.toFixed(1)} * clamp(vColor / ${multiplier.toFixed(1)} + retailLight, 0.0, 1.0);\n#endif`);
  material.userData.retailLightingUniforms ||= new Set();
  material.userData.retailLightingUniforms.add(shader.uniforms);
  updateRetailLighting(material, lighting, source, material.userData.retailLightingObject);
}

function updateRetailLighting(material, lighting, source, object) {
  if (object) material.userData.retailLightingObject = object;
  const sets = material.userData.retailLightingUniforms;
  if (!sets || !object || !lighting.directionals.length) return;
  // The editor's geometry is baked into world coordinates and recentered.
  // Recover its current source-world matrix, including live gizmo edits
  // and camera-facing parents, without modifying the dump's normals.
  const transform = retailSourceTransform(material, source, object);
  if (!transform) return;
  const previous = material.userData.retailLightingCache;
  if (previous?.source === transform && previous.worldVersion === transform.version && previous.uniformCount === sets.size) return;
  sets.forEach(uniforms => lighting.directionals.forEach((light, index) => {
    // 9B052..9B148 transforms the light ray by inverse world, normalizes
    // it and negates it; the local vertex normal itself is not normalized.
    uniforms.retailLightDirections.value[index].set(...light.direction).transformDirection(transform.inverse).negate();
  }));
  material.userData.retailLightingCache = { source: transform, worldVersion: transform.version, uniformCount: sets.size };
}

function retailCullSide(flags, reflected = false) {
  // D7FAA: only GSHD 0x20 disables culling. 8F62A / 90E77 otherwise
  // cull clockwise triangles, independently of the world determinant.
  // Three flips FRONT_FACE for negative object matrices. BackSide
  // cancels that automatic flip; baked vertices need no compensation.
  if (!Number.isInteger(flags) || flags & 0x20) return THREE.DoubleSide;
  return reflected ? THREE.BackSide : THREE.FrontSide;
}

function updateRetailCulling(object) {
  object.updateWorldMatrix(true, true);
  object.traverse(child => {
    if (!child.isMesh) return;
    const reflected = child.matrixWorld.determinant() < 0;
    const variant = material => material?.userData.retailCullVariant?.(reflected) || material;
    child.material = Array.isArray(child.material) ? child.material.map(variant) : variant(child.material);
  });
}

function buildScene(data, preserveCamera = false, restoreId = null) {
  const savedCamera = camera.position.clone();
  const savedTarget = orbit.target.clone();
  const savedVisibility = preserveCamera ? new Map([...state.items.values()].map(item => [item.id, item.visible])) : new Map();
  cleanScene(true);
  state.data = data;
  nodeMap = new Map((data.nodes || []).map(node => [node.index, node]));
  nodeAncestors = new Map();
  state.pending = data.pendingCount ?? data.stats?.pendingCount ?? state.pending;
  state.canUndo = data.history?.canUndo ?? data.canUndo ?? state.pending > 0;
  state.canRedo = data.history?.canRedo ?? data.canRedo ?? false;
  const textures = data.textures || [];
  const loader = new THREE.TextureLoader();
  const texturesByUrl = new Map();
  const loadTexture = url => {
    if (texturesByUrl.has(url)) return texturesByUrl.get(url);
    if (textureCache.has(url)) { const resource = textureCache.get(url); texturesByUrl.set(url, resource); return resource; }
    const resource = loader.load(url, () => { renderDirty = true; }, undefined, () => { setStatus('Une texture n’a pas pu être chargée ; géométrie disponible.', true); });
    resource.colorSpace = THREE.NoColorSpace;
    resource.wrapS = resource.wrapT = THREE.RepeatWrapping;
    resource.magFilter = THREE.LinearFilter;
    resource.minFilter = THREE.LinearMipmapLinearFilter;
    resource.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    resource.flipY = false;
    texturesByUrl.set(url, resource);
    textureCache.set(url, resource);
    return resource;
  };
  for (const texture of textures) {
    if (!texture.url) continue;
    textureResources.set(String(texture.id), loadTexture(texture.url));
    if (texture.kind === 'animation' && texture.frameUrls?.length) {
      const frames = texture.frameUrls.map((url, i) => { const resource = loadTexture(url); textureResources.set(`${texture.id}:frame:${i}`, resource); return resource; });
      animationResources.set(String(texture.id), { entry: texture, frames, frame: 0, manual: false });
    }
  }
  for (const [url, resource] of textureCache) {
    if (!texturesByUrl.has(url)) { resource.dispose(); textureCache.delete(url); }
  }
  for (const entry of data.meshes || []) {
    if (!entry.positions?.length) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(entry.positions, 3));
    geometry.computeBoundingBox();
    const centre = entry.position || geometry.boundingBox.getCenter(new THREE.Vector3()).toArray();
    geometry.translate(-centre[0], -centre[1], -centre[2]);
    if (entry.indices?.length) geometry.setIndex(entry.indices);
    // Retail shader builder d6a60: flag 0x10 selects MODULATE2X; otherwise
    // stage zero selects the texture. Unused zero colors must not blacken it.
    const colorFlag = entry.material?.flags;
    const fixedCombiner = [0, 1, 4, 6].includes(entry.material?.technique);
    const hasColors = entry.colors?.length >= entry.positions.length && (entry.material?.technique === undefined || fixedCombiner && (!entry.textureStages?.length || !!(colorFlag & 0x10)));
    if (hasColors) geometry.setAttribute('color', new THREE.Float32BufferAttribute(fixedCombiner && entry.textureStages?.length ? entry.colors.map(value => value * 2) : entry.colors, 3));
    if (entry.sourceNormals?.length === entry.positions.length) geometry.setAttribute('retailSourceNormal', new THREE.Float32BufferAttribute(entry.sourceNormals, 3));
    if (entry.uvs?.length >= entry.positions.length / 3 * 2) geometry.setAttribute('uv', new THREE.Float32BufferAttribute(entry.uvs, 2));
    // The dump's second UV set is Three's channel-1 attribute. Retain it
    // without selecting it from the opaque runtime textureSlot field.
    if (entry.uv2?.length >= entry.positions.length / 3 * 2) geometry.setAttribute('uv1', new THREE.Float32BufferAttribute(entry.uv2, 2));
    const parts = (entry.parts || []).filter(part => part.count > 0);
    const materials = [];
    const textureIds = new Set(parts.map(part => String(part.textureId ?? 'none')));
    if (textureIds.size <= 1) {
      materials.push(gameMaterial(parts[0]?.textureId ?? entry.textureId, hasColors, entry.material, entry.textureStages?.[0], null, entry));
    } else {
      const materialIndices = new Map();
      for (const part of parts) {
        const key = String(part.textureId ?? 'none');
        if (!materialIndices.has(key)) { materialIndices.set(key, materials.length); materials.push(gameMaterial(part.textureId, hasColors, entry.material, entry.textureStages?.[0], null, entry)); }
        const index = materialIndices.get(key);
        const lastGroup = geometry.groups.at(-1);
        if (lastGroup && lastGroup.materialIndex === index && lastGroup.start + lastGroup.count === (part.start || 0)) lastGroup.count += part.count;
        else geometry.addGroup(part.start || 0, part.count, index);
      }
    }
    if (!materials.length) materials.push(gameMaterial(entry.textureId, hasColors, entry.material, entry.textureStages?.[0], null, entry));
    geometry.computeBoundingSphere();
    const object = new THREE.Mesh(geometry, materials.length === 1 && !geometry.groups.length ? materials[0] : materials);
    object.position.copy(vector(entry.position || centre));
    object.userData = { id: entry.id, kind: 'mesh', source: entry };
    object.name = entry.name || entry.id;
    object.visible = savedVisibility.get(entry.id) ?? (state.detail === 'source' && entry.lodControlled ? entry.authoredVisible !== false : entry.visible !== false);
    (entry.sceneRole === 'sky' ? skyWorld : world).add(object);
    state.items.set(entry.id, { ...entry, kind: 'mesh', object, position: object.position.toArray(), originalPosition: entry.originalPosition || centre, visible: object.visible });
  }
  configureEnvironment();
  world.updateMatrixWorld(true);
  worldBounds.makeEmpty();
  worldBounds.copy(overviewBounds(world.children));
  if (worldBounds.isEmpty()) {
    (data.objects || []).forEach(item => worldBounds.expandByPoint(vector(item.position)));
    if (worldBounds.isEmpty()) worldBounds.set(new THREE.Vector3(-50, -50, -20), new THREE.Vector3(50, 50, 20));
  }
  worldRadius = Math.max(worldBounds.getSize(new THREE.Vector3()).length() / 2, 1);
  const markerSize = Math.min(1.2, Math.max(worldRadius * 0.003, 0.4));
  for (const entry of data.objects || []) {
    if (!entry.position?.length) continue;
    const geometry = new THREE.OctahedronGeometry(markerSize);
    const markerColor = {spawn: 0x61d8cb, camera: 0x8ba4ff, light: 0xf4dc80}[entry.kind] || 0xe4b17d;
    const material = new THREE.MeshBasicMaterial({ color: markerColor, wireframe: true, depthTest: true, transparent: true, opacity: 0.9 });
    const object = new THREE.Group();
    const marker = new THREE.Mesh(geometry, material);
    marker.visible = state.helpers;
    object.add(marker);
    const model = data.entityModels?.[entry.modelKey || entry.archetype || entry.name];
    if (model?.meshes?.length && entry.placementMatrix?.length === 16) {
      const modelRoot = new THREE.Group();
      const placement = entry.placementMatrix.slice(); placement[3] = placement[7] = placement[11] = 0;
      const modelScale = Number.isFinite(entry.modelScale) && entry.modelScale > 0 ? entry.modelScale : 1;
      for (const index of [0, 1, 2, 4, 5, 6, 8, 9, 10]) placement[index] *= modelScale;
      modelRoot.matrix.copy(matrixFromRows(placement)); modelRoot.matrixAutoUpdate = false;
      modelRoot.userData.model = true; modelRoot.visible = state.models;
      for (const mesh of model.meshes) {
        const meshGeometry = new THREE.BufferGeometry();
        meshGeometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.positions, 3));
        if (mesh.indices?.length) meshGeometry.setIndex(mesh.indices);
        if (mesh.uvs?.length) meshGeometry.setAttribute('uv', new THREE.Float32BufferAttribute(mesh.uvs, 2));
        if (mesh.uv2?.length >= mesh.positions.length / 3 * 2) meshGeometry.setAttribute('uv1', new THREE.Float32BufferAttribute(mesh.uv2, 2));
        const fixedCombiner = [0, 1, 4, 6].includes(mesh.material?.technique);
        const hasColors = mesh.colors?.length === mesh.positions.length && (mesh.material?.technique === undefined || fixedCombiner && (!mesh.textureStages?.length || !!(mesh.material?.flags & 0x10)));
        if (hasColors) meshGeometry.setAttribute('color', new THREE.Float32BufferAttribute(fixedCombiner && mesh.textureStages?.length ? mesh.colors.map(value => value * 2) : mesh.colors, 3));
        if (mesh.sourceNormals?.length === mesh.positions.length) meshGeometry.setAttribute('retailSourceNormal', new THREE.Float32BufferAttribute(mesh.sourceNormals, 3));
        const parts = (mesh.parts || []).filter(part => part.count > 0);
        const ids = new Set(parts.map(part => String(part.textureId ?? mesh.textureStages?.[0]?.textureId ?? 'none')));
        let materials;
        if (ids.size <= 1) materials = gameMaterial(parts[0]?.textureId ?? mesh.textureStages?.[0]?.textureId, hasColors, mesh.material, mesh.textureStages?.[0], null, mesh);
        else { materials = parts.map((part, i) => { meshGeometry.addGroup(part.start || 0, part.count, i); return gameMaterial(part.textureId, hasColors, mesh.material, mesh.textureStages?.[0], null, mesh); }); }
        const visual = new THREE.Mesh(meshGeometry, materials); visual.userData.id = entry.id; visual.userData.kind = 'entity'; modelRoot.add(visual);
      }
      object.add(modelRoot);
      marker.scale.setScalar(0.45);
      marker.visible = state.helpers && !state.models;
    }
    object.position.copy(vector(entry.position));
    object.userData = { id: entry.id, kind: 'entity', source: entry };
    object.name = entry.name || entry.id;
    marker.userData = { id: entry.id, kind: 'entity' };
    const visible = savedVisibility.get(entry.id) ?? (state.detail === 'source' && entry.lodControlled ? entry.authoredVisible !== false : entry.visible !== false);
    object.visible = visible && (state.helpers || (state.models && !!model));
    entityGroup.add(object);
    state.items.set(entry.id, { ...entry, gameKind: entry.kind, kind: 'entity', object, model, marker, originalPosition: entry.originalPosition || entry.position, visible });
  }
  if (data.collisions?.positions?.length) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.collisions.positions, 3));
    if (data.collisions.indices?.length) geometry.setIndex(data.collisions.indices);
    collisionMesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x65d5c2, wireframe: true, side: THREE.DoubleSide, transparent: true, opacity: 0.25, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }));
    collisionMesh.visible = state.collisions;
    scene.add(collisionMesh);
  }
  const gridSize = Math.max(10, Math.ceil(worldRadius * 2 / 10) * 10);
  grid = new THREE.GridHelper(gridSize, 40, 0x658b9d, 0x3e6379);
  grid.rotation.x = Math.PI / 2;
  grid.position.copy(worldBounds.getCenter(new THREE.Vector3()));
  grid.position.z = worldBounds.min.z - worldRadius * 0.0003;
  grid.material.transparent = true;
  grid.material.opacity = 0.2;
  grid.material.depthWrite = false;
  grid.visible = state.grid;
  scene.add(grid);
  camera.far = Math.max(1000, worldRadius * 100);
  camera.near = Math.max(0.05, worldRadius / 100000);
  orbit.minDistance = Math.max(0.1, worldRadius / 10000);
  orbit.maxDistance = worldRadius * 30;
  camera.updateProjectionMatrix();
  if (preserveCamera) {
    camera.position.copy(savedCamera); orbit.target.copy(savedTarget); orbit.update();
  } else {
    const terrain = (data.meshes || []).find(item => item.visible !== false && /^LandShape$/i.test(item.name || ''));
    const staticTerrain = (data.meshes || []).some(item => (item.staticGeometry || item.sceneRole === 'static') && item.sceneRole !== 'sky' && state.items.get(item.id)?.object.visible);
    const start = (data.objects || []).find(item => item.kind === 'spawn' && /startspot/i.test(item.nodeName || item.name));
    // D2's mauve cave walls enclose the playable area. An outside overview
    // exposes its authored green underlay instead of the in-level backdrop.
    const origin = state.levels.find(level => level.id === state.level)?.templateOrigin || state.level;
    if (['w1', 'd2'].includes(origin) && start) {
      frameStart(start);
    } else if (staticTerrain && (state.level === 'w1' || !terrain)) {
      frameObject(null, true);
    } else if (terrain && state.items.has(terrain.id)) {
      frameObject(state.items.get(terrain.id).object, true);
    } else if (start) {
      const distance = Math.min(60, Math.max(20, worldRadius * 0.04));
      orbit.target.copy(vector(start.position));
      camera.position.copy(orbit.target).add(new THREE.Vector3(distance * 0.7, -distance, distance * 0.65));
      orbit.update();
    } else frameObject(null, true);
  }
  renderHierarchy();
  skyWorld.updateMatrixWorld(true); entityGroup.updateMatrixWorld(true);
  updateRetailCulling(scene);
  cameraFacingItems = [...state.items.values()].filter(item => item.cameraDependent && nodeMap.get(item.nodeIndex)?.worldMatrix?.length === 16).map(item => ({ item, sourceInverse: matrixFromRows(nodeMap.get(item.nodeIndex).worldMatrix).invert(), objectMatrix: item.object.matrix.clone() }));
  renderNodeFilter();
  renderInspector();
  renderAssets();
  renderCapabilities();
  renderSummary();
  if (restoreId && state.items.has(restoreId)) selectItem(restoreId, true);
  $('sceneCount').textContent = number(state.items.size);
  const stats = data.stats || {};
  updateVisibleStats();
  $('sourceSize').textContent = size(stats.sourceBytes || 0);
  updateAssetCounts();
  $('textureFormat').textContent = textures.length ? `ASSETS DU JEU · ${[...new Set(textures.map(item => item.format).filter(Boolean))].slice(0, 3).join(' / ')}` : 'DUMP ORIGINAL';
  updateActions();
  renderDirty = true;
  freeNavigation.syncFromCamera();
}

async function initialize() {
  state.loading = true;
  overlay('Ouverture du projet', 'Lecture du catalogue des niveaux…');
  updateActions();
  try {
    const catalog = await api('/api/catalog');
    renderLevelCatalog(catalog);
    $('sourcePath').textContent = catalog.sourceDir ? catalog.sourceDir.split(/[\\/]/).filter(Boolean).at(-1) : translate('Changements enregistrés dans le projet local');
    $('sourcePath').title = catalog.sourceDir || '';
    let lastLevel;
    try { lastLevel = localStorage.getItem('azurik.lastLevel'); } catch { /* storage may be unavailable */ }
    const initial = state.levels.find(level => level.id === state.level || level.id === lastLevel) || state.levels.find(level => level.id === 'training_room') || state.levels[0];
    if (!initial) {
      state.loading = false;
      showEmptyCatalog();
      updateActions();
      return;
    }
    state.loading = false;
    await loadLevel(initial.id);
    api('/api/characters').then(result => { characterCatalog = result.models || []; updateAssetCounts(); if (state.assetTab === 'models') renderAssets(); }).catch(() => {});
  } catch (error) {
    state.loading = false;
    overlay('Projet indisponible', error.message, true);
    setStatus(error.message, true);
    updateActions();
  }
}

function renderLevelCatalog(catalog) {
  state.catalog = catalog; state.levels = Array.isArray(catalog.levels) ? catalog.levels : [];
  renderLevelOptions(state.level);
}

function renderLevelOptions(preferredId = null, section = state.catalogSection) {
  const view = catalogView(state.levels, section, preferredId);
  state.catalogSection = view.section;
  const select = $('levelSelect'); select.replaceChildren();
  const groups = new Map();
  for (const level of view.entries) {
    const label = level.custom && level.family === 'cinematic' ? translate('Niveaux personnalisés') : groupLabel(level.group || level.family);
    let group = groups.get(label);
    if (!group) { group = document.createElement('optgroup'); group.label = label; select.append(group); groups.set(label, group); }
    const option = document.createElement('option'); option.value = level.id;
    option.textContent = level.custom ? level.name : translateLevelName(level.name || level.label || level.id);
    if (level.custom) option.setAttribute('data-i18n-ignore', '');
    group.append(option);
  }
  if (!view.entries.length) {
    const option = document.createElement('option'); option.value = '';
    option.textContent = translate(view.section === 'cinematics' ? 'Aucune cinématique disponible.' : 'Aucun niveau disponible.');
    select.append(option);
  }
  select.value = view.selectedId || '';
  $('levelSelectLabel').textContent = translate(view.section === 'cinematics' ? 'Cinématique active' : 'Niveau actif');
  $('levelCount').textContent = `${number(view.entries.length)} ${translate(view.section === 'cinematics' ? 'CINÉMATIQUES' : 'NIVEAUX')}`;
  document.querySelectorAll('[data-catalog-section]').forEach(button => {
    const active = button.dataset.catalogSection === view.section;
    button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    button.querySelector('.catalog-count').textContent = number(view.counts[button.dataset.catalogSection]);
  });
  return view;
}

async function switchCatalogSection(section) {
  if (state.loading || state.busy || state.importing || section === state.catalogSection) return;
  const entries = catalogEntries(state.levels, section);
  if (!entries.length) return;
  const target = entries.find(level => level.id === state.catalogLast[section]) || entries[0];
  renderLevelOptions(target.id, section);
  await loadLevel(target.id);
}

function showEmptyCatalog() {
  state.level = null; state.data = null; state.pending = 0; cleanScene();
  renderHierarchy(); renderInspector(); renderSummary(); renderCapabilities(); renderAssets(); updateVisibleStats();
  $('sceneCount').textContent = '0'; $('sourceSize').textContent = size(0);
  $('levelGroup').textContent = translate('Données originales du jeu');
  $('headerLevel').textContent = translate('Importez votre jeu'); $('viewportLevel').textContent = 'AZURIK';
  overlay('Importez votre jeu', 'Choisissez un ISO Xbox d’Azurik ou un dump compatible pour commencer.');
  setStatus('Aucune source ouverte · import ISO disponible');
}

async function refreshLevelCatalog(result = {}) {
  renderLevelCatalog(await api('/api/catalog'));
  const target = state.levels.find(level => level.id === result.activeLevel)
    || state.levels.find(level => level.id === state.level) || state.levels[0];
  if (target) await loadLevel(target.id);
  else showEmptyCatalog();
  state.unsaved = true; updateActions();
}

async function loadLevel(id, options = {}) {
  const level = state.levels.find(entry => entry.id === id);
  if (!level) return;
  const request = ++state.request;
  state.loading = true;
  renderLevelOptions(id);
  const name = level.custom ? level.name : translateLevelName(level.name || level.label || id);
  overlay(`Ouverture · ${name}`, 'Décodage de la géométrie, des collisions et des assets du jeu…');
  setStatus(`Lecture du niveau ${name}…`);
  updateActions();
  try {
    const data = await api(`/api/scene?level=${encodeURIComponent(id)}`);
    if (request !== state.request) return;
    state.level = id;
    state.catalogLast[catalogSection(level)] = id;
    $('levelSelect').value = id;
    $('headerLevel').textContent = name;
    $('viewportLevel').textContent = `AZURIK / ${name.toUpperCase()}`;
    $('levelGroup').textContent = groupLabel(level?.group || level?.family);
    buildScene(data, options.preserveCamera, options.restoreId);
    state.loading = false;
    $('sceneOverlay').hidden = true;
    try { localStorage.setItem('azurik.lastLevel', id); } catch { /* optional preference */ }
    if (state.fly) renderer.domElement.focus({ preventScroll: true });
    setStatus(`${name} ouvert · ${number((data.meshes || []).length)} géométries · ${number((data.objects || []).length)} repères d’entités`);
    updateActions();
  } catch (error) {
    if (request !== state.request) return;
    state.loading = false;
    overlay(`Impossible d’ouvrir ${name}`, error.message, true);
    setStatus(error.message, true);
    updateActions();
  }
}

function nodeForItem(item) {
  const index = item?.editBinding?.transformNode ?? item?.nodeIndex;
  return nodeMap.get(index) || null;
}

function ancestorNodes(item) {
  const index = item.nodeIndex ?? item.editBinding?.transformNode;
  if (nodeAncestors.has(index)) return nodeAncestors.get(index);
  let node = nodeMap.get(index);
  const result = []; const visited = new Set();
  while (node && !visited.has(node.index)) {
    result.push(node); visited.add(node.index); node = nodeMap.get(node.parent);
  }
  nodeAncestors.set(index, result); return result;
}

function nodePath(item) { return ancestorNodes(item).reverse().map(node => node.name || node.type).filter(Boolean).join(' / '); }

function matchesNodeFilter(item) {
  if (!state.nodeFilter) return true;
  const [kind, value] = state.nodeFilter.split(':');
  return ancestorNodes(item).some(node => kind === 'node' ? node.index === Number(value) : node.type === value);
}

function renderNodeFilter() {
  const select = $('nodeFilter'); select.replaceChildren();
  const all = document.createElement('option'); all.value = ''; all.textContent = 'Tous les groupes du niveau'; select.append(all);
  const used = new Map(); state.items.forEach(item => ancestorNodes(item).forEach(node => used.set(node.index, node)));
  const types = [...new Set([...used.values()].map(node => node.type))].sort();
  const categories = document.createElement('optgroup'); categories.label = 'Catégories du graphe';
  for (const type of types) { const option = document.createElement('option'); option.value = `type:${type}`; option.textContent = type; categories.append(option); }
  if (types.length) select.append(categories);
  const groups = document.createElement('optgroup'); groups.label = 'Groupes de placement';
  const boundNodes = new Set([...state.items.values()].map(item => item.editBinding?.transformNode).filter(Number.isFinite));
  [...used.values()].filter(node => boundNodes.has(node.index)).sort((a, b) => (a.name || '').localeCompare(b.name || '')).forEach(node => { const option = document.createElement('option'); option.value = `node:${node.index}`; option.textContent = `${node.name || node.type} · ${node.index}`; groups.append(option); });
  if (groups.children.length) select.append(groups);
  if ([...select.options].some(option => option.value === state.nodeFilter)) select.value = state.nodeFilter;
  else state.nodeFilter = '';
}

function applyItemVisibility(item) {
  if (item.kind === 'entity') {
    item.marker.visible = state.helpers && !(state.models && item.model);
    item.object.children.filter(child => child.userData.model).forEach(child => { child.visible = state.models; });
    item.object.visible = item.visible && (state.helpers || (state.models && !!item.model));
  } else if (item.sceneRole === 'sky') {
    const variants = state.data?.environment?.sky?.variants || [];
    const variant = variants.find(entry => entry.id === state.sky);
    item.object.visible = item.visible && state.sky !== 'off' && (!variant || variant.meshIds.includes(item.id));
  } else item.object.visible = item.visible;
}

function configureEnvironment() {
  const sky = state.data?.environment?.sky;
  const ready = sky?.available && sky?.geometryAvailable;
  const select = $('skySelect');
  select.replaceChildren();
  for (const variant of sky?.variants || []) {
    const option = document.createElement('option'); option.value = variant.id; option.textContent = `Ciel · ${variant.label}`; select.append(option);
  }
  const off = document.createElement('option'); off.value = 'off'; off.textContent = ready ? 'Ciel · masqué' : sky?.available ? 'Ciel non décodé' : 'Ciel absent de ce niveau'; select.append(off);
  select.disabled = !ready;
  if (ready && state.sky !== 'off' && !(sky.variants || []).some(variant => variant.id === state.sky)) state.sky = sky.variants?.[0]?.id || 'off';
  select.value = ready ? state.sky : 'off';
  state.items.forEach(applyItemVisibility);
  if (ready) {
    const bounds = new THREE.Box3().setFromObject(skyWorld);
    if (!bounds.isEmpty()) {
      // The source sky is rendered around its serialized anchor. Its
      // bounds can exceed the previous fixed far distance of 1000.
      const extent = vector(sky.anchor).distanceTo(bounds.getCenter(new THREE.Vector3())) + bounds.getSize(new THREE.Vector3()).length() / 2;
      skyCamera.far = Math.max(1000, extent * 1.1);
    }
  }
  $('detailSelect').value = state.detail;
}

function retailRenderDistance(object, renderCamera) {
  // C1E17..C1E88: distance from the world AABB centre to the camera.
  // C1FAC adds MeshOffset. The channel changes sorting, never geometry.
  const bounds = object.geometry?.boundingBox;
  const cache = object.userData.retailSortBounds ||= {
    geometry: null, matrix: new THREE.Matrix4(), source: new THREE.Box3(),
    world: new THREE.Box3(), centre: new THREE.Vector3(), valid: false,
  };
  if (!cache.valid || cache.geometry !== object.geometry || !cache.matrix.equals(object.matrixWorld) ||
      cache.hasBounds !== !!bounds || (bounds && !cache.source.equals(bounds))) {
    cache.geometry = object.geometry; cache.matrix.copy(object.matrixWorld); cache.hasBounds = !!bounds;
    if (bounds) { cache.source.copy(bounds); cache.world.copy(bounds).applyMatrix4(object.matrixWorld).getCenter(cache.centre); }
    else object.getWorldPosition(cache.centre);
    cache.valid = true;
  }
  const platform = object.userData.source?.platformRender;
  const bias = platform?.sourceVerified && Number.isFinite(platform.meshOffset) ? platform.meshOffset : 0;
  return cache.centre.distanceTo(renderCamera.position) + bias;
}

function retailTransparentSort(a, b) {
  // Retail C7B90 sorts transparent entries from far to near, including
  // the platform bias. Preserve Three's explicit editor overlay orders.
  return a.groupOrder - b.groupOrder || a.renderOrder - b.renderOrder ||
    (b.object.userData.retailSortDistance ?? b.z) - (a.object.userData.retailSortDistance ?? a.z) || a.id - b.id;
}

renderer.setTransparentSort(retailTransparentSort);

function updateRenderDistances(root, renderCamera) {
  root.updateMatrixWorld();
  root.traverseVisible(object => {
    if (object.isMesh && (Array.isArray(object.material) ? object.material : [object.material]).some(material => material?.transparent)) {
      object.userData.retailSortDistance = retailRenderDistance(object, renderCamera);
    }
  });
}

function renderViewport() {
  updateCameraFacing();
  renderer.autoClear = false;
  renderer.clear();
  const sky = state.data?.environment?.sky;
  if (sky?.available && sky?.geometryAvailable && state.sky !== 'off') {
    skyCamera.position.copy(vector(sky.anchor));
    skyCamera.quaternion.copy(camera.quaternion);
    skyCamera.fov = camera.fov; skyCamera.zoom = camera.zoom; skyCamera.aspect = camera.aspect;
    skyCamera.updateProjectionMatrix();
    updateRenderDistances(skyWorld, skyCamera);
    renderer.render(skyScene, skyCamera);
    renderer.clearDepth();
  }
  updateRenderDistances(world, camera);
  renderer.render(scene, camera);
}

function updateCameraFacing() {
  if (!cameraFacingItems.length || transformPreview) return;
  if (cameraFacingQuaternion?.equals(camera.quaternion)) return;
  // Game camera identity faces +Y with Z up. Three's camera faces -Z.
  const gameQuaternion = camera.quaternion.clone().multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2)).toArray();
  const matrices = new Map();
  const worldForNode = index => {
    if (matrices.has(index)) return matrices.get(index);
    const node = nodeMap.get(index);
    if (!node) return new THREE.Matrix4();
    let matrix;
    if (!node.cameraDependent) matrix = matrixFromRows(node.worldMatrix);
    else {
      const parent = worldForNode(node.parent);
      const effective = node.inheritMode ? matrixFromRows(effectiveParentRows(node, rowsFromMatrix(parent), gameQuaternion)) : parent;
      matrix = effective.clone().multiply(matrixFromRows(node.localMatrix));
    }
    matrices.set(index, matrix); return matrix;
  };
  for (const { item, sourceInverse, objectMatrix } of cameraFacingItems) {
    item.object.matrixAutoUpdate = false;
    item.object.matrix.copy(worldForNode(item.nodeIndex)).multiply(sourceInverse).multiply(objectMatrix);
    item.object.matrixWorldNeedsUpdate = true;
  }
  if (state.items.get(state.selected)?.cameraDependent) selectionBox?.update();
  (cameraFacingQuaternion ||= new THREE.Quaternion()).copy(camera.quaternion);
}

function updateVisibleStats() {
  const triangles = [...state.items.values()].filter(item => item.object.visible).reduce((sum, item) => sum + (item.kind === 'mesh' ? (item.indices?.length || item.positions?.length / 3 || 0) / 3 : state.models ? item.model?.triangleCount || 0 : 0), 0);
  $('triangles').textContent = `${number(triangles)} triangles visibles`;
  const decor = [...state.items.values()].filter(item => item.kind === 'mesh' && item.sceneRole !== 'sky');
  const shown = decor.filter(item => item.object.visible).length;
  const recovered = decor.filter(item => item.object.visible && item.editorVisible && !item.authoredVisible).length;
  $('sceneCoverage').textContent = `${number(shown)} parties du décor${recovered ? ` · ${number(recovered)} rétablies` : ''}`;
  $('sceneCoverage').title = 'Parties placées dans le graphe du niveau ; les groupes de détail alternatifs sont conservés et restent consultables.';
}

function linkedItems(item) {
  const index = item?.editBinding?.transformNode;
  if (!Number.isFinite(index)) return item ? [item] : [];
  return [...state.items.values()].filter(other => other.id === item.id || ancestorNodes(other).some(node => node.index === index));
}

function canPreviewTransform(item) {
  const node = nodeForItem(item);
  if (!node || !item.editBinding?.parentWorldMatrix?.length) return false;
  if (['rotatePivot', 'rotatePivotTranslation', 'scalePivot', 'scalePivotTranslation'].some(key => (node[key] || [0, 0, 0]).some(value => Math.abs(value) > 0.00001))) return false;
  const matrix = matrixFromRows(item.editBinding.parentWorldMatrix);
  const axes = [new THREE.Vector3().setFromMatrixColumn(matrix, 0), new THREE.Vector3().setFromMatrixColumn(matrix, 1), new THREE.Vector3().setFromMatrixColumn(matrix, 2)];
  const lengths = axes.map(axis => axis.length());
  return matrix.determinant() > 0 && Math.min(...lengths) > 0.00001 && Math.max(...lengths) / Math.min(...lengths) < 1.0001 && axes.every((axis, i) => axes.every((other, j) => i === j || Math.abs(axis.dot(other)) / lengths[i] / lengths[j] < 0.0001));
}

function applyPreviewWorldMatrix(object, worldMatrix) {
  const local = object.parent ? object.parent.matrixWorld.clone().invert().multiply(worldMatrix) : worldMatrix;
  // Descendants can contain inherited nonuniform scale or a camera-facing
  // basis. TRS decomposition cannot represent every resulting matrix.
  object.matrix.copy(local);
  local.decompose(object.position, object.quaternion, object.scale);
  object.matrixAutoUpdate = false;
  object.matrixWorldNeedsUpdate = true;
  updateRetailCulling(object);
}

function restoreTransformPreview() {
  if (!transformPreview) return;
  for (const { item, localMatrix, matrixAutoUpdate } of transformPreview.items) {
    item.object.matrix.copy(localMatrix);
    localMatrix.decompose(item.object.position, item.object.quaternion, item.object.scale);
    item.object.matrixAutoUpdate = matrixAutoUpdate;
    item.object.matrixWorldNeedsUpdate = true;
    updateRetailCulling(item.object);
  }
  transformPreview = null;
}

function configureGizmo(item = state.items.get(state.selected)) {
  transform.detach(); restoreTransformPreview();
  $('gizmoNotice').hidden = true;
  transform.setMode(state.tool); transform.setSpace(state.tool === 'translate' ? 'world' : 'local');
  $('toolSpace').textContent = state.tool === 'translate' ? 'Monde' : 'Local';
  if (!item) return;
  if (item.locked) { $('gizmoNotice').textContent = translate(item.lockedExplicitly ? 'Objet verrouillé · déverrouillez-le pour le modifier.' : 'Verrouillé par un parent · déverrouillez le parent ou le niveau.'); $('gizmoNotice').hidden = false; return; }
  if (state.tool === 'translate') { if (isEditable(item)) transform.attach(item.object); return; }
  if (item.previewEditable && isTransformEditable(item, state.tool)) {
    transformParent.matrix.identity();
    transformProxy.position.copy(item.object.position);
    transformProxy.quaternion.setFromEuler(new THREE.Euler(...item.localRotation, 'ZYX'));
    transformProxy.scale.copy(vector(item.localScale));
    transformParent.updateMatrixWorld(true); transform.attach(transformProxy); return;
  }
  if (!isTransformEditable(item, state.tool)) { $('gizmoNotice').textContent = 'Ce bloc ne possède pas ce canal de transformation modifiable.'; $('gizmoNotice').hidden = false; return; }
  if (!canPreviewTransform(item)) { $('gizmoNotice').textContent = 'Pivot ou parent complexe : utilisez les champs locaux. La géométrie sera reconstruite après validation.'; $('gizmoNotice').hidden = false; return; }
  const node = nodeForItem(item);
  transformParent.matrix.copy(matrixFromRows(item.editBinding.parentWorldMatrix));
  transformProxy.position.copy(vector(item.localPosition || node.position));
  transformProxy.quaternion.setFromEuler(new THREE.Euler(...item.localRotation, eulerOrder(item)));
  transformProxy.scale.copy(vector(item.localScale));
  transformParent.updateMatrixWorld(true);
  transform.attach(transformProxy);
}

function setTool(mode) {
  state.tool = mode;
  [['moveTool', 'translate'], ['rotateTool', 'rotate'], ['scaleTool', 'scale']].forEach(([id, value]) => { $(id).classList.toggle('active', mode === value); });
  configureGizmo();
}

function renderHierarchy() {
  const container = $('hierarchy');
  container.replaceChildren();
  const allItems = [...state.items.values()];
  const matching = allItems.filter(item => (state.filter === 'all' || item.kind === state.filter) && matchesNodeFilter(item) && `${item.name} ${item.id} ${item.tag || ''} ${item.gameKind || ''} ${nodePath(item)}`.toLowerCase().includes(state.query));
  const groups = [
    { matches: item => item.kind === 'mesh' && isWorldSpace(item), label: 'Géométrie du niveau', icon: 'cube' },
    { matches: item => item.kind === 'mesh' && !isWorldSpace(item), label: 'Primitives de modèles', icon: 'cube' },
    { matches: item => item.kind === 'entity', label: 'Repères d’entités', icon: 'pin' },
  ];
  for (const group of groups) {
    const items = matching.filter(group.matches);
    if (!items.length) continue;
    const section = document.createElement('div'); section.className = 'tree-group';
    const heading = document.createElement('button'); heading.className = 'tree-heading'; heading.innerHTML = icon('chevron');
    heading.append(document.createTextNode(group.label));
    const count = document.createElement('span'); count.textContent = number(items.length); heading.append(count);
    const list = document.createElement('div');
    heading.addEventListener('click', () => { list.hidden = !list.hidden; heading.classList.toggle('closed', list.hidden); });
    section.append(heading, list);
    for (const item of items) {
      const row = document.createElement('div');
      row.className = `tree-row ${item.kind}${state.selected === item.id ? ' selected' : ''}${!item.visible ? ' dimmed' : ''}`;
      row.dataset.id = item.id;
      const area = document.createElement('div'); area.className = 'tree-name-area'; area.innerHTML = icon(group.icon);
      const label = document.createElement('span'); label.className = 'tree-name'; label.textContent = item.name || item.id; label.title = `${item.name || item.id}\n${item.id}`; area.append(label);
      label.dataset.i18nIgnore = '';
      if (item.locked) { const badge = document.createElement('span'); badge.className = 'lock-badge'; badge.textContent = '🔒'; badge.title = translate('Objet verrouillé'); area.append(badge); }
      if (!samePosition(item.object.position.toArray(), item.originalPosition)) { const dot = document.createElement('span'); dot.className = 'tree-child-tag'; dot.title = 'Position modifiée'; area.append(dot); }
      area.addEventListener('click', () => selectItem(item.id));
      area.addEventListener('dblclick', () => frameObject(item.object));
      const eye = document.createElement('button'); eye.className = 'eye-button'; eye.innerHTML = icon(item.visible ? 'eye' : 'hide'); eye.title = item.visible ? 'Masquer dans la vue' : 'Afficher dans la vue'; eye.setAttribute('aria-label', eye.title);
      eye.addEventListener('click', () => {
        item.visible = !item.visible;
        applyItemVisibility(item);
        if (!item.visible && state.selected === item.id) selectItem(null);
        renderHierarchy();
        updateVisibleStats();
      });
      row.append(area, eye); list.append(row);
    }
    container.append(section);
  }
  if (!matching.length) { const empty = document.createElement('div'); empty.className = 'empty-small'; empty.textContent = state.query ? 'Aucun résultat pour cette recherche.' : 'Aucun élément de ce type.'; container.append(empty); }
}

function selectItem(id, force = false) {
  if (!force && (state.loading || state.busy)) return;
  state.selected = id && state.items.has(id) ? id : null;
  transform.detach();
  if (selectionBox) { disposeObject(selectionBox); selectionBox = null; }
  const item = state.items.get(state.selected);
  let visibilityChanged = false;
  if (item) {
    if (!item.visible) { item.visible = true; applyItemVisibility(item); visibilityChanged = true; }
    configureGizmo(item);
    if (item.kind === 'mesh' || item.model) {
      selectionBox = new THREE.BoxHelper(item.object, 0x7fe0cc);
      selectionBox.material.transparent = true; selectionBox.material.opacity = 0.65;
      selectionBox.material.depthTest = false;
      selectionBox.renderOrder = 900;
      scene.add(selectionBox);
    }
    $('selectionLabel').querySelector('span').textContent = item.name || item.id;
    $('selectionLabel').querySelector('use').setAttribute('href', item.kind === 'mesh' ? '#i-cube' : '#i-pin');
    $('selectionLabel').hidden = false;
  } else $('selectionLabel').hidden = true;
  if (visibilityChanged) renderHierarchy();
  else $('hierarchy').querySelectorAll('.tree-row').forEach(row => row.classList.toggle('selected', row.dataset.id === state.selected));
  renderInspector();
  updateVisibleStats();
  updateActions();
  const row = [...$('hierarchy').querySelectorAll('.tree-row')].find(element => element.dataset.id === id);
  if (row?.parentElement.hidden) { row.parentElement.hidden = false; row.parentElement.previousElementSibling?.classList.remove('closed'); }
  row?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
}

function renderInspector() {
  const item = state.items.get(state.selected);
  $('inspectorEmpty').hidden = !!item;
  $('inspectorContent').hidden = !item;
  if (!item) return;
  $('selectedName').textContent = item.name || item.id;
  $('selectedType').textContent = item.kind === 'mesh' ? isWorldSpace(item) ? 'GÉOMÉTRIE DU NIVEAU' : 'PRIMITIVE DE MODÈLE' : 'REPÈRE D’ENTITÉ';
  $('inspectorSpace').textContent = isWorldSpace(item) ? 'MONDE' : 'SOURCE';
  $('selectedIcon').querySelector('use').setAttribute('href', item.kind === 'mesh' ? '#i-cube' : '#i-pin');
  $('selectedTag').textContent = item.tag || item.gameKind || (item.kind === 'mesh' ? 'Mesh du jeu' : 'Entité du jeu');
  $('selectedId').textContent = item.id;
  refreshPositionFields();
  const node = nodeForItem(item);
  $('selectionWarning').textContent = item.locked ? 'Objet verrouillé · déverrouillez-le pour le modifier.' : item.assetWarning || (item.previewOnly ? 'Bloc d’aperçu sauvegardé dans le projet et l’export d’assets. Ce bloc n’est pas intégré dans les fichiers du jeu.' : !isEditable(item)
    ? 'Ce bloc est consultable. Son format ne permet pas encore un déplacement fiable ; la modification est désactivée.'
    : item.kind === 'mesh'
    ? isWorldSpace(item)
      ? 'Le déplacement modifie le placement du décor et ses parties liées. Les collisions restent à leur position d’origine : vérifiez-les dans le jeu.'
      : 'Cette primitive est affichée dans ses coordonnées source. Modifier ses sommets affecte le modèle utilisé par le jeu ; ses placements et les collisions demandent une vérification dans le jeu.'
    : isWorldSpace(item)
      ? item.model ? 'Modèle original en pose de liaison, sans animation de squelette. Le placement est celui du graphe du jeu ; les comportements ne sont pas exécutés.' : 'Ce repère représente une position du graphe du jeu. Son modèle et ses comportements ne sont pas exécutés dans cette vue.'
      : 'Ce repère affiche les coordonnées enregistrées dans le fichier. Leur rattachement à un parent n’est pas encore résolu : la position mondiale reste à confirmer. Le modèle animé n’est pas décodé.');
  const details = [
    ['Type', item.kind === 'mesh' ? item.instance ? 'Instance de scène' : isWorldSpace(item) ? 'Géométrie statique' : 'Primitive de modèle' : item.gameKind || 'Entité'],
    ['Adresse du bloc', sourceOffset(item.sourceOffset ?? item.coordOffset)],
    ['Coordonnées', isWorldSpace(item) ? 'Monde original · Z ↑' : 'Source locale · Z ↑'],
    ['Déplacement', isEditable(item) ? 'Pris en charge' : 'Lecture seule'],
    ['Protection', item.locked ? 'Verrouillé' : 'Déverrouillé'],
    ['Export dans le jeu', item.previewOnly ? 'Aperçu uniquement' : 'Placement source'],
  ];
  if (item.editBinding) {
    const count = [...state.items.values()].filter(other => other.editBinding?.editOffset === item.editBinding.editOffset).length;
    details.push(['Placement sérialisé', sourceOffset(item.editBinding.editOffset)], ['Parties liées', number(count)]);
  }
  if (item.modelReplaced) details.push(['Modèle remplacé', item.assetGameExportable ? 'Export dans le jeu pris en charge' : 'Aperçu uniquement']);
  if (node) details.push(['Nœud de transformation', node.name || String(node.index)], ['Groupe du niveau', nodePath(item)]);
  if (item.model) {
    details.push(['Modèle original', item.model.name || item.modelKey || item.archetype || item.name], ['Pose affichée', 'Liaison · non animée']);
    if (item.modelScale !== undefined) details.push(['Échelle de base du modèle', `${formatNumber(round(item.modelScale), { maximumFractionDigits: 3 })} ×`]);
    if (item.modelBinding) details.push(['Correspondance du jeu', `${item.modelBinding.matchedName} → ${item.modelBinding.body}`]);
    details.push(['Bibliothèque du modèle', item.model.sourceArchive || 'characters.xbr']);
  }
  if (item.kind === 'mesh') {
    details.push(['Sommets', number(item.positions?.length / 3)], ['Triangles', number((item.indices?.length || item.positions?.length / 3 || 0) / 3)], ['Sous-parties', number(item.parts?.length || 1)]);
    const mapped = (item.parts || []).filter(part => part.textureId !== undefined && part.textureId !== null).length;
    details.push(['Matériaux texturés', `${mapped} / ${(item.parts || []).length || 1}`]);
    const materials = Array.isArray(item.object.material) ? item.object.material : [item.object.material];
    const lighting = materials.find(material => material?.userData.sourceLighting)?.userData.sourceLighting;
    if (lighting) details.push(['Éclairage du décor', `${lighting.directionals.length} lumières directionnelles du jeu · couleurs d’origine`]);
    if (materials.some(material => material?.userData.retailReflection2D)) details.push(['Coordonnées des reflets', 'Calcul original du jeu · texture 2D']);
    if (item.sourceLighting?.limit) details.push(['Éclairage partiel', item.sourceLighting.limit]);
    if (item.platformRender?.sourceVerified) {
      details.push(['Opacité source de la plateforme', round(item.platformRender.gameVisible)],
        ['Facteur d’opacité de l’aperçu', round(materials[0]?.opacity ?? 1)],
        ['Biais de distance du rendu', round(item.platformRender.meshOffset)]);
      if (item.platformRender.gameVisible === 0) details.push(['État dynamique', 'Conservé visible pour inspection · scripts non simulés']);
    }
    const limitations = [...new Set(materials.map(material => material?.userData.renderLimit).filter(Boolean))];
    if (limitations.length) details.push(['Rendu partiel', limitations.join(' · ')]);
  }
  if (item.origin !== undefined) details.push(['Origine du bloc', Array.isArray(item.origin) ? item.origin.map(round).join(' · ') : String(item.origin)]);
  const dl = $('sourceDetails'); dl.replaceChildren();
  for (const [key, value] of details) { const dt = document.createElement('dt'); dt.textContent = key; const dd = document.createElement('dd'); dd.textContent = String(value); dl.append(dt, dd); }
}

function refreshPositionFields() {
  const item = state.items.get(state.selected);
  if (!item) return;
  ['posX', 'posY', 'posZ'].forEach((id, i) => { if (document.activeElement !== $(id)) $(id).value = round(item.object.position.getComponent(i)); });
  const rotation = transformPreview ? new THREE.Euler().setFromQuaternion(transformProxy.quaternion, eulerOrder(item)).toArray().slice(0, 3) : item.localRotation || [0, 0, 0];
  const scale = transformPreview ? transformProxy.scale.toArray() : item.localScale || [1, 1, 1];
  ['rotX', 'rotY', 'rotZ'].forEach((id, i) => { if (document.activeElement !== $(id)) $(id).value = round(THREE.MathUtils.radToDeg(rotation[i])); });
  ['scaleX', 'scaleY', 'scaleZ'].forEach((id, i) => { if (document.activeElement !== $(id)) $(id).value = round(scale[i]); });
}

function renderSummary() {
  const stats = state.data?.stats || {};
  const values = [['Géométries', stats.meshCount ?? state.data?.meshes?.length ?? 0], ['Entités repérées', stats.objectCount ?? state.data?.objects?.length ?? 0], ['Triangles visuels', stats.triangleCount ?? 0], ['Faces de collision', stats.collisionFaceCount ?? 0]];
  const container = $('dataSummary'); container.replaceChildren();
  values.forEach(([label, value]) => { const row = document.createElement('div'); row.className = 'summary-line'; const name = document.createElement('span'); name.textContent = label; const count = document.createElement('strong'); count.textContent = number(value); row.append(name, count); container.append(row); });
}

function renderCapabilities() {
  const data = state.data || {};
  const capabilities = data.capabilities || {};
  const textureCount = (data.textures || []).length;
  const rows = [
    [data.environment?.sky?.available ? 'Ciel original · caméra dédiée' : 'Aucun ciel déclaré dans ce niveau', data.environment?.sky?.available ? 'on' : 'off'],
    [(data.meshes || []).some(isWorldSpace) ? 'Géométrie de scène placée' : 'Modèles statiques décodés', (data.meshes || []).length ? 'on' : 'off'],
    [textureCount ? `${number(textureCount)} textures Xbox décodées` : 'Textures : données non décodées', textureCount ? 'on' : 'partial'],
    [data.collisions?.positions?.length ? 'Collision du niveau consultable' : 'Collision indisponible', data.collisions?.positions?.length ? 'on' : 'off'],
    [Object.keys(data.entityModels || {}).length ? 'Modèles originaux · pose de liaison' : 'Entités : repères de position', Object.keys(data.entityModels || {}).length ? 'on' : 'partial'],
    [capabilities.rotation || capabilities.meshRotation ? 'Rotation et échelle locales' : 'Transformations : selon le bloc source', capabilities.rotation || capabilities.meshRotation ? 'on' : 'partial'],
    [animationResources.size ? `${animationResources.size} animations de texture · aperçu` : 'Textures fixes', animationResources.size ? 'on' : 'off'],
  ];
  const container = $('capabilities'); container.replaceChildren();
  rows.forEach(([label, status]) => { const row = document.createElement('div'); const dot = document.createElement('span'); dot.className = `capability-dot ${status}`; row.append(dot, document.createTextNode(label)); container.append(row); });
  const warnings = $('warnings'); warnings.replaceChildren();
  const note = document.createElement('p'); note.textContent = 'Rendu des fichiers du dump : les effets, scripts et animations du moteur ne sont pas exécutés.'; warnings.append(note);
  const entries = [...new Set([...(data.warnings || []), ...(capabilities.warnings || [])].map(entry => typeof entry === 'string' ? entry : entry.message || JSON.stringify(entry)))];
  if (entries.length) {
    const details = document.createElement('details');
    const summary = document.createElement('summary'); summary.textContent = `${entries.length} informations sur le rendu et les modifications`; details.append(summary);
    entries.forEach(warning => { const p = document.createElement('p'); p.textContent = warning; details.append(p); });
    warnings.append(details);
  }
  $('collisionBtn').disabled = !data.collisions?.positions?.length;
  $('textureBtn').disabled = !textureCount;
}

function modelAssets() {
  const result = new Map();
  for (const entry of characterCatalog) result.set(entry.name, { ...entry, kind: 'character', description: `${entry.boneCount} os · pose de liaison` });
  for (const [key, entry] of Object.entries(state.data?.entityModels || {})) result.set(entry.name || key, { ...entry, name: entry.name || key, label: key === 'azurik-player' ? 'Azurik · pose de liaison' : key, kind: 'character', description: `${number(entry.triangleCount)} triangles · ${entry.meshCount || entry.meshes?.length || 0} parties` });
  for (const entry of state.data?.assets || []) result.set(entry.id || entry.name, { ...entry, kind: 'primitive', description: `${number(entry.vertices ?? entry.vertexCount ?? entry.positions?.length / 3)} sommets · ${number(entry.triangles ?? 0)} triangles · ressource du niveau` });
  for (const entry of state.data?.meshes || []) if (entry.previewEditable && /^import-/u.test(entry.id)) {
    result.set(entry.id, { ...entry, kind: 'primitive', description: `${number(entry.indices?.length / 3)} triangles · ${translate('Ajout du projet · aperçu')}` });
  }
  return [...result.values()];
}

function materialAssets() {
  const materials = new Map();
  for (const mesh of state.data?.meshes || []) {
    if (!mesh.material) continue;
    const key = `${mesh.shaderResource}:${mesh.shaderIndex}`;
    if (!materials.has(key)) materials.set(key, { name: `Matière ${mesh.shaderIndex ?? materials.size}`, key, resource: mesh.shaderResource, ...mesh.material, stages: mesh.textureStages || [], meshIds: [] });
    materials.get(key).meshIds.push(mesh.id);
  }
  return [...materials.values()];
}

function updateAssetCounts() {
  const library = state.assetScope === 'library';
  $('textureCount').textContent = number((library ? state.libraryData?.textures : state.data?.textures)?.length || 0);
  $('modelCount').textContent = number(library ? state.libraryData?.models?.length || 0 : modelAssets().length);
  $('materialCount').textContent = number(library ? 0 : materialAssets().length);
  $('referenceCount').textContent = number(library ? 0 : state.data?.references?.length || 0);
  document.querySelectorAll('[data-tab="materials"], [data-tab="references"]').forEach(button => { button.disabled = library; button.title = library ? 'Disponible pour le niveau actif' : ''; });
}

function setAssetTab(tab) {
  state.assetTab = tab; state.assetPage = 0;
  document.querySelectorAll('[data-tab]').forEach(button => button.classList.toggle('active', button.dataset.tab === tab));
  renderAssets();
}

function libraryLoadingMessage(message) {
  $('libraryStatus').textContent = message;
  $('libraryStatus').classList.remove('error');
  $('libraryRetry').hidden = true;
}

async function openLibrary(archiveId = state.archive, refresh = false) {
  const request = ++state.libraryRequest;
  libraryController?.abort(); libraryController = new AbortController();
  state.libraryLoading = true; state.libraryError = ''; state.assetPage = 0;
  if (archiveId !== state.archive) state.libraryData = null;
  libraryLoadingMessage(libraryArchives ? 'Lecture de l’archive…' : 'Lecture du catalogue du jeu…');
  $('archiveSelect').disabled = true; renderAssets();
  try {
    if (!libraryArchives || refresh && !archiveId) {
      const catalog = await api('/api/library', undefined, { signal: libraryController.signal });
      if (request !== state.libraryRequest) return;
      libraryArchives = catalog.archives || [];
      const select = $('archiveSelect'); select.replaceChildren();
      for (const archive of libraryArchives) {
        const option = document.createElement('option'); option.value = archive.id; option.textContent = archive.label || archive.file || archive.id; select.append(option);
      }
    }
    const archive = libraryArchives.find(entry => entry.id === archiveId) || libraryArchives.find(entry => entry.id === 'characters') || libraryArchives[0];
    if (!archive) throw new Error('Aucune archive du jeu n’est disponible dans la bibliothèque.');
    state.archive = archive.id; $('archiveSelect').value = archive.id;
    libraryLoadingMessage(`Décodage · ${archive.label || archive.file || archive.id}`);
    const data = !refresh && libraryCache.has(archive.id) ? libraryCache.get(archive.id) : await api(`/api/library?archive=${encodeURIComponent(archive.id)}`, undefined, { signal: libraryController.signal });
    if (request !== state.libraryRequest) return;
    if (!libraryCache.has(archive.id) && libraryCache.size >= 8) libraryCache.delete(libraryCache.keys().next().value);
    libraryCache.set(archive.id, data); state.libraryData = data;
    state.libraryLoading = false; $('archiveSelect').disabled = false;
    const total = (data.textures?.length || 0) + (data.models?.length || 0);
    $('libraryStatus').textContent = `${number(total)} ressources · ${libraryArchives.length} archives`;
    $('libraryStatus').title = (data.warnings || []).map(entry => typeof entry === 'string' ? entry : entry.message || '').filter(Boolean).join('\n');
    renderAssets();
  } catch (error) {
    if (request !== state.libraryRequest || error.name === 'AbortError') return;
    state.libraryLoading = false; state.libraryData = null; state.libraryError = error.message;
    $('archiveSelect').disabled = !libraryArchives?.length;
    $('libraryStatus').textContent = 'Bibliothèque indisponible'; $('libraryStatus').classList.add('error'); $('libraryRetry').hidden = false;
    renderAssets();
  }
}

function setAssetScope(scope) {
  state.assetScope = scope; state.assetPage = 0; $('assetScope').value = scope;
  document.querySelector('.workspace').classList.toggle('library-mode', scope === 'library');
  $('archiveSelect').hidden = scope !== 'library';
  if (scope === 'library') {
    if (['materials', 'references'].includes(state.assetTab)) setAssetTab('textures');
    openLibrary();
  } else {
    ++state.libraryRequest; libraryController?.abort(); state.libraryLoading = false;
    $('libraryStatus').textContent = 'Assets du niveau actif'; $('libraryStatus').title = ''; $('libraryStatus').classList.remove('error'); $('libraryRetry').hidden = true;
    renderAssets();
  }
}

function renderAssetState(title, message, loading = false) {
  const container = $('assetContent'); const empty = document.createElement('div'); empty.className = 'assets-empty'; empty.innerHTML = loading ? '<div class="library-spinner"></div>' : icon('folder');
  const text = document.createElement('div'); const heading = document.createElement('strong'); heading.textContent = title; const paragraph = document.createElement('p'); paragraph.textContent = message; text.append(heading, paragraph); empty.append(text); container.append(empty); $('assetPager').hidden = true;
}

function renderAssets() {
  const container = $('assetContent'); container.replaceChildren();
  $('assetPager').hidden = true;
  updateAssetCounts();
  if (state.assetScope === 'library' && state.libraryLoading) { renderAssetState('Lecture des ressources originales', 'Le catalogue et les aperçus sont préparés depuis l’archive sélectionnée.', true); return; }
  if (state.assetScope === 'library' && state.libraryError) { renderAssetState('Archive indisponible', state.libraryError); return; }
  const source = state.assetScope === 'library' ? (state.assetTab === 'textures' ? state.libraryData?.textures || [] : state.libraryData?.models || []) : state.assetTab === 'textures' ? state.data?.textures || [] : state.assetTab === 'models' ? modelAssets() : state.assetTab === 'materials' ? materialAssets() : state.data?.references || [];
  const entries = source.filter(entry => `${entry.label || ''} ${entry.name || ''} ${entry.id || ''} ${entry.kind || ''} ${entry.format || ''} ${entry.key || ''}`.toLowerCase().includes(state.assetQuery));
  if (!entries.length) {
    const empty = document.createElement('div'); empty.className = 'assets-empty'; empty.innerHTML = icon(state.assetTab === 'textures' ? 'image' : 'folder');
    const text = document.createElement('div'); const title = document.createElement('strong'); const explanation = document.createElement('p');
    title.textContent = state.assetQuery ? 'Aucun asset correspondant' : 'Aucune ressource décodée dans cette catégorie';
    explanation.textContent = state.assetQuery ? 'Essayez un nom, un identifiant ou un format différent.' : 'Les ressources identifiées dans les fichiers originaux apparaîtront ici.';
    text.append(title, explanation); empty.append(text); container.append(empty); return;
  }
  const list = document.createElement('div'); list.className = state.assetTab === 'textures' ? 'texture-grid' : 'references-grid';
  const pageCount = Math.max(1, Math.ceil(entries.length / assetPageSize));
  state.assetPage = THREE.MathUtils.clamp(state.assetPage, 0, pageCount - 1);
  const pageStart = state.assetPage * assetPageSize;
  $('assetPager').hidden = entries.length <= assetPageSize;
  $('assetRange').textContent = `${number(pageStart + 1)}–${number(Math.min(pageStart + assetPageSize, entries.length))} / ${number(entries.length)} ressources`;
  $('assetPage').textContent = `${state.assetPage + 1} / ${pageCount}`;
  $('assetPrev').disabled = state.assetPage === 0; $('assetNext').disabled = state.assetPage === pageCount - 1;
  for (const entry of entries.slice(pageStart, pageStart + assetPageSize)) {
    if (state.assetTab === 'textures') {
      const card = document.createElement('div'); card.className = 'texture-card'; card.tabIndex = 0; card.setAttribute('role', 'button'); card.setAttribute('aria-label', `Aperçu de ${entry.name || entry.id}`);
      const thumb = document.createElement('div'); thumb.className = 'texture-thumb'; const image = document.createElement('img'); image.src = entry.url; image.alt = entry.name || String(entry.id); image.loading = 'lazy'; thumb.append(image);
      if (entry.kind === 'animation' || entry.kind === 'cube') { const badge = document.createElement('span'); badge.className = 'animation-tag'; badge.textContent = entry.kind === 'animation' ? `${entry.frameCount} IMAGES` : 'CUBE'; thumb.append(badge); }
      const info = document.createElement('div'); info.className = 'texture-info'; const title = document.createElement('strong'); title.textContent = entry.name || `Texture ${entry.id}`; const dimensions = document.createElement('span'); dimensions.textContent = `${entry.width || '?'} × ${entry.height || '?'} · ${entry.format || 'Xbox'}`; info.append(title, dimensions); card.append(thumb, info);
      card.addEventListener('click', () => showTexture(entry)); card.addEventListener('keydown', event => { if (event.key === 'Enter') showTexture(entry); }); list.append(card);
    } else {
      const card = document.createElement('div'); card.className = 'reference-card'; card.innerHTML = icon(state.assetTab === 'models' ? 'cube' : state.assetTab === 'materials' ? 'wire' : 'folder'); const text = document.createElement('div'); const title = document.createElement('strong'); title.textContent = entry.label || entry.name || entry.file || String(entry.id || 'Référence'); title.title = entry.name || title.textContent; const description = document.createElement('span'); description.textContent = entry.description || (state.assetTab === 'materials' ? `Technique ${entry.technique} · ${entry.stages.length} couche(s) · ${entry.meshIds.length} géométrie(s)` : state.assetTab === 'models' ? `${entry.kind === 'character' ? 'Pose de liaison' : 'Modèle statique'} · ${number(entry.triangleCount ?? entry.triangles)} triangles · ${entry.sourceArchive || 'niveau actif'}` : [entry.kind || entry.type || 'Référence', entry.offset !== undefined ? sourceOffset(entry.offset) : ''].filter(Boolean).join(' · ')); text.append(title, description); card.append(text);
      if (['models', 'materials'].includes(state.assetTab)) { card.tabIndex = 0; card.setAttribute('role', 'button'); const action = () => state.assetTab === 'models' ? showModel(entry) : showMaterial(entry); card.addEventListener('click', action); card.addEventListener('keydown', event => { if (event.key === 'Enter') action(); }); }
      list.append(card);
    }
  }
  container.append(list);
}

function showMaterial(entry) {
  const body = openDialog(entry.name, 'MATIÈRE ORIGINALE');
  const rows = [['Ressource', sourceOffset(entry.resource)], ['Technique', entry.technique], ['Opacité', entry.opacity], ['Comparaison alpha', entry.alphaFunction === 500 ? 'Automatique · supérieur' : entry.alphaFunction], ['Seuil alpha', entry.alphaReference], ['Mode de mélange', entry.blendType], ['Écriture profondeur', entry.depthWrite], ['Géométries liées', entry.meshIds.length]];
  const details = document.createElement('dl'); details.className = 'source-details modal-source-details';
  for (const [key, value] of rows) { const dt = document.createElement('dt'); dt.textContent = key; const dd = document.createElement('dd'); dd.textContent = value ?? '—'; details.append(dt, dd); }
  body.append(details);
  for (const [i, stage] of entry.stages.entries()) { const row = document.createElement('p'); row.textContent = `Couche ${i} · ${stage.textureId || 'sans texture'} · flags ${sourceOffset(stage.flags)}`; body.append(row); }
  const note = document.createElement('p'); note.className = 'dialog-note'; note.textContent = 'Valeurs lues dans le jeu. Les combinaisons à plusieurs couches et les coordonnées générées ne sont pas encore entièrement reproduites.'; body.append(note);
  const button = document.createElement('button'); button.className = 'button secondary'; button.textContent = 'Sélectionner une géométrie liée'; button.addEventListener('click', () => { $('infoDialog').close(); selectItem(entry.meshIds[0]); frameObject(state.items.get(entry.meshIds[0])?.object); }); body.append(button);
}

function openDialog(title, eyebrow = 'AZURIK LEVEL STUDIO') {
  cleanDialogPreview();
  $('dialogTitle').textContent = title; $('dialogEyebrow').textContent = eyebrow; $('dialogBody').replaceChildren();
  if (!$('infoDialog').open) $('infoDialog').showModal();
  freeNavigation.reset(); orbit.enabled = !state.fly && !transform.dragging;
  return $('dialogBody');
}

function showTexture(entry) {
  const body = openDialog(entry.name || `Texture ${entry.id}`, 'TEXTURE DU DUMP');
  const image = document.createElement('img'); image.className = 'texture-preview'; image.src = entry.url; image.alt = entry.name || 'Texture du jeu';
  const details = document.createElement('div'); details.className = 'modal-details'; const dimensions = document.createElement('span'); dimensions.textContent = `${entry.width} × ${entry.height} pixels`; const format = document.createElement('span'); format.textContent = entry.format || 'Texture Xbox'; details.append(dimensions, format); body.append(image, details);
  if (entry.replaced || /^import-tex-/u.test(String(entry.id))) {
    const status = document.createElement('div'); status.className = `asset-import-result${entry.gameExportable ? '' : ' preview-only'}`;
    status.textContent = translate(entry.gameExportable ? 'Modification compatible avec l’export dans le jeu.' : 'Texture du projet · aperçu et export d’assets uniquement.'); body.append(status);
  }
  if (state.assetScope === 'level') {
    const replace = document.createElement('button'); replace.className = 'button secondary'; replace.textContent = 'Remplacer cette texture';
    replace.addEventListener('click', () => showAssetImport('texture', entry.id));
    const actions = document.createElement('div'); actions.className = 'asset-import-actions'; actions.append(replace); body.append(actions);
  }
  if (entry.kind === 'animation' && entry.frameUrls?.length) {
    const controls = document.createElement('div'); controls.className = 'animation-controls';
    const button = document.createElement('button'); button.className = 'tool-button active'; button.innerHTML = icon('pause'); button.title = 'Pause / lecture de cet aperçu'; button.setAttribute('aria-label', button.title);
    const range = document.createElement('input'); range.type = 'range'; range.min = '0'; range.max = String(entry.frameUrls.length - 1); range.step = '1'; range.value = '0'; range.setAttribute('aria-label', 'Choisir une image de l’animation');
    const output = document.createElement('output');
    dialogAnimation = { entry, image, range, output, paused: false, frame: 0 };
    range.addEventListener('input', () => { dialogAnimation.paused = true; dialogAnimation.frame = Number(range.value); image.src = entry.frameUrls[dialogAnimation.frame]; output.textContent = `${dialogAnimation.frame + 1} / ${entry.frameCount}`; const animation = animationResources.get(String(entry.id)); if (animation) { animation.manual = true; setAnimationFrame(animation, dialogAnimation.frame); } button.innerHTML = icon('play'); });
    button.addEventListener('click', () => { dialogAnimation.paused = !dialogAnimation.paused; const animation = animationResources.get(String(entry.id)); if (animation) animation.manual = dialogAnimation.paused; button.innerHTML = icon(dialogAnimation.paused ? 'play' : 'pause'); });
    controls.append(button, range, output); body.append(controls);
    const note = document.createElement('p'); note.className = 'dialog-note'; note.textContent = 'Séquence d’images originale. La cadence est un réglage d’aperçu ; le rythme et les effets du moteur ne sont pas simulés.'; body.append(note);
  }
  if (entry.kind === 'cube' && entry.faceUrls?.length) {
    const controls = document.createElement('div'); controls.className = 'animation-controls';
    entry.faceUrls.forEach((url, i) => { const button = document.createElement('button'); button.className = 'button secondary'; button.textContent = `Face ${i + 1}`; button.addEventListener('click', () => { image.src = url; }); controls.append(button); }); body.append(controls);
    const note = document.createElement('p'); note.className = 'dialog-note'; note.textContent = 'Six faces originales. Leur orientation dans le moteur reste à vérifier.'; body.append(note);
  }
}

function cleanDialogPreview() {
  if (dialogAnimation) { const animation = animationResources.get(String(dialogAnimation.entry.id)); if (animation) animation.manual = false; dialogAnimation = null; }
  if (dialogModel) { dialogModel.orbit.dispose(); dialogModel.resize.disconnect(); disposeObject(dialogModel.root); dialogModel.textures.forEach(texture => texture.dispose()); dialogModel.renderer.dispose(); dialogModel = null; }
}

async function showModel(entry) {
  if (state.items.has(entry.id)) { $('infoDialog').close(); selectItem(entry.id); frameObject(state.items.get(entry.id).object); return; }
  const fromLibrary = !!(entry.archive && entry.id);
  const body = openDialog(entry.label || entry.name, entry.kind === 'static' ? 'MODÈLE SOURCE · ASSEMBLAGE STATIQUE' : 'MODÈLE SOURCE · POSE DE LIAISON');
  const loading = document.createElement('p'); loading.textContent = 'Lecture du modèle original…'; body.append(loading);
  if (!fromLibrary && entry.kind !== 'character') { loading.textContent = 'Réserve de géométrie du niveau. Sélectionnez une instance dans la hiérarchie ou ouvrez un assemblage statique depuis la bibliothèque du jeu.'; return; }
  try {
    const data = await api(fromLibrary ? `/api/library/model?archive=${encodeURIComponent(entry.archive)}&id=${encodeURIComponent(entry.id)}` : `/api/character?name=${encodeURIComponent(entry.name)}`);
    if (!$('infoDialog').open || !$('dialogBody').contains(loading)) return;
    if (!(data.meshes || []).some(mesh => mesh.indices?.length >= 3)) { loading.textContent = 'Cette ressource ne possède pas encore un assemblage de triangles validé. Les sommets ne sont pas triangulés automatiquement.'; return; }
    loading.remove();
    const host = document.createElement('div'); host.className = 'model-preview'; body.append(host);
    const modelRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); modelRenderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5)); modelRenderer.outputColorSpace = THREE.LinearSRGBColorSpace; host.append(modelRenderer.domElement);
    const modelScene = new THREE.Scene(); const root = new THREE.Group(); modelScene.add(root);
    const textures = new Map(); const modelMaterials = new Map(); const loader = new THREE.TextureLoader();
    for (const texture of data.textures || []) { const map = loader.load(texture.url); map.colorSpace = THREE.NoColorSpace; map.flipY = false; map.wrapS = map.wrapT = THREE.RepeatWrapping; textures.set(String(texture.id), map); }
    for (const mesh of data.meshes || []) {
      if (!mesh.indices?.length) continue;
      const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.positions, 3)); if (mesh.indices?.length) geometry.setIndex(mesh.indices); if (mesh.uvs?.length) geometry.setAttribute('uv', new THREE.Float32BufferAttribute(mesh.uvs, 2));
      if (mesh.uv2?.length >= mesh.positions.length / 3 * 2) geometry.setAttribute('uv1', new THREE.Float32BufferAttribute(mesh.uv2, 2));
      const fixedCombiner = [0, 1, 4, 6].includes(mesh.material?.technique);
      const hasColors = mesh.colors?.length === mesh.positions.length && (mesh.material?.technique === undefined || fixedCombiner && (!mesh.textureStages?.length || !!(mesh.material?.flags & 0x10)));
      if (hasColors) geometry.setAttribute('color', new THREE.Float32BufferAttribute(fixedCombiner && mesh.textureStages?.length ? mesh.colors.map(value => value * 2) : mesh.colors, 3));
      if (mesh.sourceNormals?.length === mesh.positions.length) geometry.setAttribute('retailSourceNormal', new THREE.Float32BufferAttribute(mesh.sourceNormals, 3));
      const make = textureId => gameMaterial(textureId, hasColors, mesh.material, mesh.textureStages?.[0], { textures, materials: modelMaterials, textureMetadata: data.textures }, mesh);
      const parts = (mesh.parts || []).filter(part => part.count > 0); const ids = new Set(parts.map(part => String(part.textureId ?? mesh.textureStages?.[0]?.textureId ?? 'none')));
      let materials; if (ids.size <= 1) materials = make(parts[0]?.textureId ?? mesh.textureStages?.[0]?.textureId); else materials = parts.map((part, i) => { geometry.addGroup(part.start || 0, part.count, i); return make(part.textureId); }); root.add(new THREE.Mesh(geometry, materials));
    }
    updateRetailCulling(root);
    const bounds = new THREE.Box3().setFromObject(root); const center = bounds.getCenter(new THREE.Vector3()); const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, 0.1);
    const modelCamera = new THREE.PerspectiveCamera(40, 1, Math.max(radius / 1000, 0.001), radius * 100); modelCamera.up.set(0, 0, 1); modelCamera.position.copy(center).add(new THREE.Vector3(0.7, -1, 0.45).normalize().multiplyScalar(radius * 3));
    const controls = new OrbitControls(modelCamera, modelRenderer.domElement); controls.target.copy(center); controls.enableDamping = true; controls.update();
    const resize = new ResizeObserver(() => { modelRenderer.setSize(host.clientWidth, host.clientHeight, false); modelCamera.aspect = host.clientWidth / host.clientHeight; modelCamera.updateProjectionMatrix(); }); resize.observe(host);
    dialogModel = { renderer: modelRenderer, scene: modelScene, camera: modelCamera, orbit: controls, resize, root, textures };
    const details = document.createElement('div'); details.className = 'modal-details'; details.textContent = [`${number(data.triangleCount)} triangles`, `${data.meshCount || data.meshes.length} parties`, Number.isFinite(data.boneCount) ? `${data.boneCount} os` : null, data.sourceArchive].filter(Boolean).join(' · '); body.append(details);
    const note = document.createElement('p'); note.className = 'dialog-note'; note.textContent = `${data.pose === 'static' || entry.kind === 'static' ? 'Assemblage statique original.' : 'Modèle original en pose de liaison.'} Les matières complexes sont approximées avec leur couleur principale. Les animations du squelette, les effets et l’éclairage du moteur ne sont pas exécutés dans cet aperçu.`; body.append(note);
    if (data.warnings?.length) { const warnings = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = `${data.warnings.length} information(s) sur cette ressource`; warnings.className = 'model-source-warnings'; warnings.append(summary); for (const warning of data.warnings) { const paragraph = document.createElement('p'); paragraph.textContent = typeof warning === 'string' ? warning : warning.message || JSON.stringify(warning); warnings.append(paragraph); } body.append(warnings); }
  } catch (error) { loading.textContent = `Modèle indisponible : ${error.message}`; }
}

function setAnimationFrame(animation, frame) {
  animation.frame = frame % animation.frames.length;
  const map = animation.frames[animation.frame];
  materialResources.forEach(material => { if (material.userData.animationId === String(animation.entry.id)) { material.userData.originalMap = map; if (state.textures) material.map = map; } });
}

function updateAnimations(now) {
  let changed = false;
  if (state.animation) animationResources.forEach(animation => { if (!animation.manual) { const frame = Math.floor(now / 1000 * state.animationFps) % animation.frames.length; if (frame !== animation.frame) { setAnimationFrame(animation, frame); changed = true; } } });
  if (dialogAnimation) {
    const view = dialogAnimation; if (!view.paused && state.animation) view.frame = Math.floor(now / 1000 * state.animationFps) % view.entry.frameUrls.length;
    if (view.image.dataset.frame !== String(view.frame)) {
      view.image.src = view.entry.frameUrls[view.frame]; view.image.dataset.frame = String(view.frame);
      view.range.value = String(view.frame); view.output.textContent = `${view.frame + 1} / ${view.entry.frameCount}`;
    }
  }
  return changed;
}

function overviewBounds(objects) {
  const visible = objects.filter(object => object.visible);
  // Non-depth-writing transparent effects still render normally, but a
  // kilometre-wide fog volume must not push the overview kilometres away.
  // A selected effect keeps its own complete bounds for explicit framing.
  const solids = visible.filter(object => !object.isMesh || !(Array.isArray(object.material) ? object.material : [object.material]).every(material => material?.transparent && material.depthWrite === false));
  const bounds = new THREE.Box3();
  (solids.length ? solids : visible).forEach(object => bounds.union(new THREE.Box3().setFromObject(object)));
  return bounds;
}

function frameObject(object = null, immediate = false) {
  if (!state.data && world.children.length === 0) return;
  const box = object ? new THREE.Box3().setFromObject(object) : new THREE.Box3();
  if (!object) box.copy(overviewBounds(world.children));
  if (box.isEmpty() && !object) box.copy(worldBounds);
  if (box.isEmpty()) return;
  const centre = box.getCenter(new THREE.Vector3());
  const dimensions = box.getSize(new THREE.Vector3());
  const radius = Math.max(dimensions.length() / 2, worldRadius * 0.005);
  const distance = radius / Math.sin(THREE.MathUtils.degToRad(camera.getEffectiveFOV() / 2)) * 1.14 / Math.min(camera.aspect, 1);
  const direction = camera.position.clone().sub(orbit.target);
  if (immediate || direction.lengthSq() < 0.01) direction.set(0.8, -1, 0.75);
  direction.normalize();
  camera.position.copy(centre).addScaledVector(direction, distance);
  orbit.target.copy(centre); orbit.update();
  if (state.fly) freeNavigation.syncFromCamera();
  setStatus(object ? `Sélection cadrée · ${object.name}` : 'Vue de l’ensemble du niveau');
}

async function commitPosition(id, target, previous = null) {
  const item = state.items.get(id);
  if (!isEditable(item) || state.busy || state.loading) return;
  const before = previous || item.position || item.object.position.toArray();
  if (!target.every(Number.isFinite)) { toast('Entrez des coordonnées numériques valides.', true); refreshPositionFields(); return; }
  if (samePosition(before, target)) return;
  const currentLevel = state.level;
  item.object.position.copy(vector(target)); item.object.updateMatrixWorld(); selectionBox?.update();
  state.busy = true; updateActions();
  try {
    const result = await api('/api/move', { level: currentLevel, id, position: target });
    item.position = result.position || target;
    item.object.position.copy(vector(item.position));
    for (const [relatedId, position] of Object.entries(result.relatedPositions || {})) {
      const related = state.items.get(relatedId);
      if (!related) continue;
      related.position = position;
      related.object.position.copy(vector(position));
      related.object.updateMatrixWorld();
    }
    selectionBox?.update();
    state.pending = result.pendingCount ?? state.pending + 1;
    state.canUndo = result.canUndo ?? true; state.canRedo = result.canRedo ?? false; state.unsaved = true;
    if (item.kind === 'mesh') {
      const updated = result.scene || await api(`/api/scene?level=${encodeURIComponent(currentLevel)}`);
      if (state.level === currentLevel) buildScene(updated, true, id);
    }
    renderHierarchy(); refreshPositionFields();
    setStatus(`Position modifiée · ${item.name || id}`);
    if (result.warning) toast(result.warning);
  } catch (error) {
    item.object.position.copy(vector(before)); item.position = before; selectionBox?.update(); refreshPositionFields();
    toast(`Déplacement refusé : ${error.message}`, true); setStatus(error.message, true);
  } finally { state.busy = false; updateActions(); }
}

function startCameraPose(position, placementMatrix, fallbackDirection) {
  // This is an editor view based on the authored +Y heading, not a claim
  // to reproduce any scripted or runtime game camera.
  let forward = Array.isArray(placementMatrix) && placementMatrix.length === 16 ? new THREE.Vector3(placementMatrix[1], placementMatrix[5], 0) : fallbackDirection.clone();
  forward.z = 0;
  if (![forward.x, forward.y].every(Number.isFinite) || forward.lengthSq() < 0.000001) forward.set(0, 1, 0);
  forward.normalize();
  return {
    position: position.clone().addScaledVector(forward, -12).add(new THREE.Vector3(0, 0, 5)),
    target: position.clone().add(new THREE.Vector3(0, 0, 1.5)),
  };
}

function frameStart(start) {
  const position = state.items.get(start.id)?.object.position || vector(start.position);
  const pose = startCameraPose(position, start.placementMatrix, orbit.target.clone().sub(camera.position));
  camera.position.copy(pose.position); orbit.target.copy(pose.target); orbit.update();
  setStatus('Vue d’éditeur derrière le départ du niveau');
}

function cameraPreset(value) {
  if (state.fly) setFly(false);
  if (value === 'start') {
    const starts = (state.data?.objects || []).filter(item => item.kind === 'spawn');
    const start = starts.find(item => /startspot/i.test(item.nodeName || item.name)) || starts[0];
    if (!start) { toast('Ce niveau ne déclare pas de point de départ.'); return; }
    frameStart(start); return;
  }
  const directions = { perspective: [0.8, -1, 0.75], top: [0, -0.00001, 1], front: [0, -1, 0], side: [1, 0, 0] };
  const distance = Math.max(camera.position.distanceTo(orbit.target), worldRadius * 0.02);
  camera.position.copy(orbit.target).addScaledVector(vector(directions[value] || directions.perspective).normalize(), distance); orbit.update();
}

async function captureViewport() {
  if (!state.data || state.loading || state.capturing) return;
  state.capturing = true; $('captureBtn').disabled = true;
  try {
    renderViewport();
    const output = document.createElement('canvas'); output.width = renderer.domElement.width; output.height = renderer.domElement.height;
    const context = output.getContext('2d');
    if (state.background === 'studio') { const gradient = context.createRadialGradient(output.width * 0.48, output.height * 0.35, 0, output.width * 0.48, output.height * 0.35, Math.max(output.width, output.height) * 0.75); gradient.addColorStop(0, '#304350'); gradient.addColorStop(0.48, '#1e2d37'); gradient.addColorStop(1, '#15222c'); context.fillStyle = gradient; } else context.fillStyle = ({ night: '#0a131e', neutral: '#666666', black: '#000000' })[state.background];
    context.fillRect(0, 0, output.width, output.height); context.drawImage(renderer.domElement, 0, 0);
    const capture = await api('/api/capture', { image: output.toDataURL('image/png') });
    const body = openDialog('Capture enregistrée', 'CAPTURE DU NIVEAU');
    const image = document.createElement('img'); image.className = 'texture-preview'; image.src = capture.url; image.alt = `Capture du niveau ${state.level}`;
    const details = document.createElement('p'); details.className = 'modal-details'; details.textContent = `${output.width} × ${output.height} pixels · PNG`;
    const path = document.createElement('code'); path.className = 'export-path'; path.textContent = capture.path;
    const link = document.createElement('a'); link.className = 'button secondary'; link.download = capture.path.split(/[\\/]/).pop(); link.href = `${capture.url}?download=1`; link.textContent = 'Télécharger le PNG';
    body.append(image, details, path, link);
    toast('Capture PNG enregistrée dans le dossier du projet.');
  } catch (error) { toast(`Capture impossible : ${error.message}`, true); }
  finally { state.capturing = false; $('captureBtn').disabled = state.loading || !state.data; }
}

function showViewSettings() {
  const body = openDialog('Visualisation du niveau', 'RÉGLAGES D’APERÇU');
  const row = document.createElement('label'); row.className = 'settings-row'; row.append(document.createTextNode('Fond de la vue'));
  const select = document.createElement('select'); select.className = 'compact-select';
  for (const [value, label] of [['studio', 'Studio bleu'], ['night', 'Nuit'], ['neutral', 'Gris neutre'], ['black', 'Noir']]) { const option = document.createElement('option'); option.value = value; option.textContent = label; select.append(option); } select.value = state.background;
  select.addEventListener('change', () => { state.background = select.value; if (state.background === 'studio') renderer.setClearColor(0x000000, 0); else renderer.setClearColor(({ night: 0x0a131e, neutral: 0x666666, black: 0x000000 })[state.background], 1); }); row.append(select); body.append(row);
  const exposureRow = document.createElement('label'); exposureRow.className = 'settings-row'; exposureRow.append(document.createTextNode('Luminosité d’aperçu'));
  const exposure = document.createElement('input'); exposure.type = 'range'; exposure.min = '0.25'; exposure.max = '2'; exposure.step = '0.05'; exposure.value = state.exposure;
  const output = document.createElement('output'); output.textContent = `${state.exposure.toFixed(2)} ×`;
  exposure.addEventListener('input', () => { state.exposure = Number(exposure.value); previewExposure.value = state.exposure; output.textContent = `${state.exposure.toFixed(2)} ×`; }); exposureRow.append(exposure, output); body.append(exposureRow);
  const reset = document.createElement('button'); reset.className = 'button secondary'; reset.textContent = 'Restaurer les valeurs de lecture'; reset.addEventListener('click', () => { state.exposure = previewExposure.value = 1; exposure.value = '1'; output.textContent = '1.00 ×'; }); body.append(reset);
  const note = document.createElement('p'); note.className = 'dialog-note'; note.textContent = 'Ces réglages ne modifient aucun fichier du jeu. La luminosité à 1× conserve les couleurs lues. L’aperçu ne reproduit pas l’ensemble des effets et de l’éclairage du moteur Xbox.'; body.append(note);
}

async function commitTransform(id, rotation, scale) {
  const item = state.items.get(id);
  if (!item || item.locked || state.busy || state.loading) return;
  if (!rotation.every(Number.isFinite) || !scale.every(Number.isFinite) || scale.some(value => Math.abs(value) < 0.0001 || Math.abs(value) > 10000)) { toast('Entrez une rotation valide et une échelle non nulle.', true); configureGizmo(item); refreshPositionFields(); return; }
  if (samePosition(item.localRotation || [0, 0, 0], rotation) && samePosition(item.localScale || [1, 1, 1], scale)) { configureGizmo(item); refreshPositionFields(); return; }
  const body = { level: state.level, id };
  if (isTransformEditable(item, 'rotate')) body.rotation = rotation;
  if (isTransformEditable(item, 'scale')) body.scale = scale;
  if (!body.rotation && !body.scale) return;
  state.busy = true; updateActions();
  try {
    const result = await api('/api/transform', body);
    const data = result.scene || await api(`/api/scene?level=${encodeURIComponent(state.level)}`);
    buildScene(data, true, id);
    state.unsaved = true; setStatus(`Transformation locale modifiée · ${item.name || id}`);
    if (result.warning) toast(result.warning);
  } catch (error) {
    restoreTransformPreview();
    configureGizmo(item); refreshPositionFields(); toast(`Transformation refusée : ${error.message}`, true);
  } finally { transformPreview = null; state.busy = false; updateActions(); }
}

async function historyAction(action) {
  if (state.busy || state.loading || !state.data) return;
  state.busy = true; updateActions();
  const selectedId = state.selected;
  try {
    const result = await api(`/api/${action}`, { level: state.level });
    if (result.catalogChanged) { await refreshLevelCatalog(result); return; }
    const data = result.scene || result;
    if (data.meshes && data.id !== state.level) { await refreshLevelCatalog({ activeLevel: data.id }); return; }
    if (data.meshes) buildScene(data, true, selectedId);
    else if (data.id && state.items.has(data.id)) {
      const item = state.items.get(data.id); item.object.position.copy(vector(data.position)); item.position = data.position;
      state.pending = data.pendingCount ?? state.pending; state.canUndo = data.canUndo ?? state.canUndo; state.canRedo = data.canRedo ?? state.canRedo; renderInspector(); renderHierarchy();
    } else await loadLevel(state.level, { preserveCamera: true, restoreId: selectedId });
    state.unsaved = true;
    setStatus(action === 'undo' ? 'Dernière modification annulée' : 'Modification rétablie');
  } catch (error) { toast(error.message, true); }
  finally { state.busy = false; updateActions(); }
}

async function saveProject() {
  if (state.busy || state.loading || !state.data) return;
  state.busy = true; updateActions();
  try {
    const result = await api('/api/save', {});
    state.unsaved = false;
    toast('Projet enregistré. Les fichiers d’origine restent intacts.');
    setStatus(`Projet enregistré${result.path ? ` · ${result.path}` : ''}`);
  } catch (error) { toast(`Enregistrement impossible : ${error.message}`, true); }
  finally { state.busy = false; updateActions(); }
}

async function exportMod() {
  if (state.busy || state.loading || !state.data) return;
  state.busy = true; updateActions(); $('exportBtn').querySelector('span').textContent = 'Export en cours…';
  try {
    const result = await api('/api/export', {});
    state.exportDirectory = result.directory || result.path;
    const body = openDialog('Votre mod est exporté', 'EXPORT DU JEU');
    const changeCount = (result.editCount ?? state.pending) + (result.levelManagement?.changeCount || 0);
    const description = document.createElement('p'); description.textContent = `${number(result.fileCount)} fichier${result.fileCount > 1 ? 's' : ''} exporté${result.fileCount > 1 ? 's' : ''} · ${number(changeCount)} modification${changeCount > 1 ? 's' : ''}.`;
    const path = document.createElement('code'); path.className = 'export-path'; path.textContent = result.directory || result.path || 'Consultez le dossier exports du projet.';
    const note = document.createElement('p'); note.className = 'dialog-note'; note.textContent = 'Les fichiers d’origine du dump sont conservés. Testez le niveau exporté dans le jeu : les collisions, scripts et modèles animés ne sont pas simulés par cette vue.';
    body.append(description, path, note);
    if (result.gameFiles?.length) {
      const build = document.createElement('button'); build.className = 'button primary';
      build.textContent = translate('Construire une ISO du mod');
      build.addEventListener('click', () => levelManager.showBuildIso(state.exportDirectory)); body.append(build);
    }
    if (navigator.clipboard && (result.directory || result.path)) { const copy = document.createElement('button'); copy.className = 'button secondary'; copy.textContent = 'Copier le chemin'; copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(result.directory || result.path); copy.textContent = 'Chemin copié'; } catch { toast('Le navigateur n’autorise pas la copie automatique.', true); } }); body.append(copy); }
    state.unsaved = false;
    setStatus(`Mod exporté · ${result.directory || result.path || 'Dossier exports'}`);
  } catch (error) { toast(`Export impossible : ${error.message}`, true); }
  finally { state.busy = false; $('exportBtn').querySelector('span').textContent = 'Exporter le mod'; updateActions(); }
}

function toggle(key, buttonId, apply) {
  state[key] = !state[key];
  $(buttonId).classList.toggle('active', state[key]); $(buttonId).setAttribute('aria-pressed', String(state[key]));
  apply(state[key]);
}

async function changeLock(locked, all = false) {
  if (state.busy || state.loading || !state.level || (!all && !state.selected)) return;
  const selected = state.selected;
  state.busy = true; transform.detach(); updateActions();
  try {
    const result = await api('/api/lock', { level: state.level, ...(all ? { all: true } : { id: selected }), locked });
    buildScene(result.scene || result, true, selected);
    state.unsaved = true;
    toast(all ? locked ? 'Tous les éléments sont verrouillés.' : 'Tous les éléments sont déverrouillés.' : locked ? 'Objet verrouillé.' : 'Objet déverrouillé.');
  } catch (error) { toast(error.message, true); }
  finally { state.busy = false; configureGizmo(); updateActions(); }
}

async function assetMutation(action, body, restoreId = state.selected) {
  if (!state.level || state.loading || state.busy || state.importing) return null;
  state.busy = true; freeNavigation.reset(); transform.detach(); updateActions();
  try {
    const result = await api(`/api/${action}`, { level: state.level, ...body });
    buildScene(result.scene || await api(`/api/scene?level=${encodeURIComponent(state.level)}`), true, result.id || restoreId);
    state.unsaved = true; renderDirty = true;
    return result;
  } catch (error) { toast(error.message, true); throw error; }
  finally { state.busy = false; updateActions(); }
}

function showAssetResult(result, title) {
  const body = openDialog(title, 'AZURIK LEVEL STUDIO 2.1.1');
  const status = document.createElement('div'); status.className = `asset-import-result${result.gameExportable ? '' : ' preview-only'}`;
  status.textContent = translate(result.gameExportable ? 'Modification compatible avec l’export dans le jeu.' : 'Ajout conservé dans le projet et l’export d’assets. Aperçu uniquement dans le niveau ; non intégré au jeu.');
  body.append(status);
  if (result.warning) { const warning = document.createElement('p'); warning.textContent = result.warning; body.append(warning); }
  const note = document.createElement('p'); note.textContent = 'Ctrl Z annule cette opération ; Ctrl Maj Z la rétablit.'; body.append(note);
  const close = document.createElement('button'); close.className = 'button primary'; close.textContent = 'Revenir au niveau'; close.addEventListener('click', () => $('infoDialog').close()); body.append(close);
}

async function duplicateSelection() {
  const item = state.items.get(state.selected);
  if (!item || item.kind !== 'mesh' || item.locked) return;
  try {
    const result = await assetMutation('duplicate', { id: item.id, offset: [Math.max(worldRadius * 0.005, 1), 0, 0] });
    if (result) showAssetResult(result, 'Modèle dupliqué');
  } catch { /* assetMutation already reports the failure */ }
}

async function deleteAddition() {
  const item = state.items.get(state.selected);
  if (!item || item.locked || item.kind !== 'mesh' || !/^import-/u.test(item.id)) return;
  try { if (await assetMutation('delete-asset', { id: item.id }, null)) toast('Ajout supprimé · Ctrl Z pour le restaurer.'); }
  catch { /* failure is already visible */ }
}

function showResetLevel() {
  if (!state.level || state.loading || state.busy) return;
  const body = openDialog('Restaurer le niveau d’origine', 'AZURIK LEVEL STUDIO 2.1.1');
  const paragraph = document.createElement('p'); paragraph.textContent = 'Rétablir les positions, transformations, modèles et textures du niveau actif depuis le dump source, puis supprimer les ajouts du niveau. Les autres niveaux sont conservés. Ctrl Z permet de récupérer vos modifications.';
  const actions = document.createElement('div'); actions.className = 'asset-import-actions';
  const cancel = document.createElement('button'); cancel.className = 'button secondary'; cancel.textContent = 'Annuler'; cancel.addEventListener('click', () => $('infoDialog').close());
  const restore = document.createElement('button'); restore.className = 'button primary'; restore.textContent = 'Restaurer ce niveau';
  restore.addEventListener('click', async () => {
    restore.disabled = true;
    try {
      const result = await assetMutation('reset-level', {}, null);
      if (result) { $('infoDialog').close(); state.items.forEach(item => { item.visible = item.lodControlled ? item.editorVisible !== false : true; applyItemVisibility(item); }); renderHierarchy(); updateVisibleStats(); toast('Niveau d’origine restauré · Ctrl Z pour annuler.'); }
    } catch { restore.disabled = false; }
  });
  actions.append(cancel, restore); body.append(paragraph, actions);
}

function showAssetImport(kind, targetId = null) {
  if (!state.level || state.loading || state.busy) return;
  const replacing = targetId !== null;
  const title = kind === 'model' ? replacing ? 'Remplacer le modèle sélectionné' : 'Importer un modèle' : replacing ? 'Remplacer une texture du niveau' : 'Importer une texture PNG';
  const body = openDialog(title, 'AZURIK LEVEL STUDIO 2.1.1');
  const form = document.createElement('form'); form.className = 'asset-import-form';
  const note = document.createElement('p'); note.className = 'dialog-note';
  note.textContent = kind === 'model' ? 'OBJ, glTF 2.0, GLB ou JSON · 12 Mo maximum. Pour un glTF, sélectionnez aussi son fichier .bin. La géométrie est assemblée ; choisissez la texture ci-dessous. Les ajouts et duplications restent des aperçus. Un remplacement de même topologie peut être exporté dans le jeu et affecter ses autres instances.' : 'PNG · 8 Mo maximum. Un remplacement compatible conserve les dimensions et le format Xbox du jeu. Une texture partagée est remplacée dans toutes ses utilisations. Les autres imports restent dans le projet et l’export d’assets.';
  body.append(note);
  let fieldIndex = 0;
  const field = (text, control) => {
    control.id = `asset-${kind}-${++fieldIndex}`; control.name = control.id; control.setAttribute('aria-label', translate(text));
    const label = document.createElement('label'); label.htmlFor = control.id;
    const caption = document.createElement('span'); caption.textContent = translate(text); label.append(caption, control); form.append(label); return control;
  };
  const input = document.createElement('input'); input.type = 'file'; input.required = true; input.multiple = kind === 'model'; input.accept = kind === 'model' ? '.obj,.gltf,.glb,.json,.bin' : '.png';
  field('Fichier à importer', input);
  const name = document.createElement('input'); name.type = 'text'; name.maxLength = 120; name.required = true; field('Nom de l’asset', name);
  let axis, texture, coordinateSpace;
  if (kind === 'model') {
    axis = document.createElement('select');
    for (const [value, label] of [['z', 'Z vers le haut · Azurik'], ['y', 'Y vers le haut · glTF standard']]) { const option = document.createElement('option'); option.value = value; option.textContent = label; axis.append(option); }
    field('Orientation du modèle', axis);
    if (replacing) {
      coordinateSpace = document.createElement('select');
      for (const [value, label] of [['asset', 'Coordonnées locales du modèle'], ['world', 'Coordonnées du niveau (monde)']]) {
        const option = document.createElement('option'); option.value = value; option.textContent = label; coordinateSpace.append(option);
      }
      field('Coordonnées du modèle', coordinateSpace);
    }
    texture = document.createElement('select'); const none = document.createElement('option'); none.value = ''; none.textContent = 'Conserver la texture · ou sans texture'; texture.append(none);
    for (const entry of state.data.textures || []) { const option = document.createElement('option'); option.value = String(entry.id); option.textContent = `${entry.name || entry.id} · ${entry.width} × ${entry.height}`; texture.append(option); }
    field('Texture du modèle', texture);
  } else if (replacing) {
    texture = document.createElement('select');
    for (const entry of state.data.textures || []) { const option = document.createElement('option'); option.value = String(entry.id); option.textContent = `${entry.name || entry.id} · ${entry.width} × ${entry.height} · ${entry.format || 'Xbox'}`; texture.append(option); }
    texture.value = String(targetId); field('Texture à remplacer', texture);
  }
  const summary = document.createElement('output'); summary.className = 'asset-import-summary'; form.append(summary);
  const error = document.createElement('p'); error.className = 'asset-import-error'; error.setAttribute('role', 'alert'); error.hidden = true; form.append(error);
  const submit = document.createElement('button'); submit.type = 'submit'; submit.className = 'button primary'; submit.disabled = true; submit.textContent = replacing ? 'Remplacer' : 'Importer'; form.append(submit); body.append(form);
  let payload = null, readRequest = 0;
  const readFile = async () => {
    const request = ++readRequest; payload = null; submit.disabled = true; error.hidden = true;
    const file = [...input.files].find(entry => kind === 'model' ? /\.(obj|json|gltf|glb)$/iu.test(entry.name) : /\.png$/iu.test(entry.name));
    if (!file) { summary.textContent = ''; return; }
    if (!name.value) name.value = file.name.replace(/\.[^.]+$/u, '');
    summary.textContent = translate('Lecture de l’asset…');
    try {
      const result = kind === 'model' ? await parseModelFiles(input.files, axis.value) : await readPngFile(file);
      if (request !== readRequest || !form.isConnected) return;
      payload = result;
      summary.textContent = kind === 'model' ? `${number(result.positions.length / 3)} ${translate('sommets')} · ${number(result.indices.length / 3)} triangles` : `${file.name} · ${size(file.size)}`;
      submit.disabled = false;
    } catch (failure) { if (request === readRequest) { summary.textContent = ''; error.textContent = translate(failure.message); error.hidden = false; } }
  };
  input.addEventListener('change', () => { if (axis && /\.(gltf|glb)$/iu.test([...input.files].find(file => /\.(gltf|glb)$/iu.test(file.name))?.name || '')) axis.value = 'y'; readFile(); });
  axis?.addEventListener('change', readFile);
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (!payload || !name.value.trim()) return;
    submit.disabled = true; error.hidden = true;
    try {
      const action = `${replacing ? 'replace' : 'import'}-${kind}`;
      const model = kind === 'model' ? { ...payload, ...(texture.value ? { textureId: texture.value } : {}), ...(coordinateSpace ? { coordinateSpace: coordinateSpace.value } : {}) } : null;
      const request = { name: name.value.trim(), ...(replacing ? { id: kind === 'texture' ? texture.value : targetId } : {}),
        ...(kind === 'model' ? { model, ...(!replacing ? { position: orbit.target.toArray() } : {}) } : { image: payload }) };
      const result = await assetMutation(action, request);
      if (result) showAssetResult(result, replacing ? 'Asset remplacé' : 'Asset importé');
    } catch (failure) { error.textContent = translate(failure.message); error.hidden = false; submit.disabled = false; }
  });
}

function setFly(enabled) {
  state.fly = enabled;
  freeNavigation.enable(enabled);
  $('flyBtn').classList.toggle('active', enabled); $('flyBtn').setAttribute('aria-pressed', String(enabled));
  updateNavigationHint();
  if (enabled) renderer.domElement.focus({ preventScroll: true });
  toast(enabled ? 'Caméra libre activée · clic droit pour regarder sur place. Espace monte, Ctrl descend. Maj accélère, Alt ralentit.' : 'Caméra orbitale activée');
}

function updateNavigationHint() {
  $('cameraHint').replaceChildren();
  const hint = state.fly ? `${getLanguage() === 'fr' ? 'Z / Q / S / D' : 'W / Q / S / D'} · ${translate('Clic droit : regarder à 360°')} · ${translate('Flèches : regarder sur place')} · ${translate('Maj : rapide · Alt : lent')}` : translate('Souris : orbiter · Molette : zoomer · F : cadrer');
  $('cameraHint').textContent = hint;
  $('navigationMode').textContent = translate(state.fly ? 'Caméra libre' : 'Caméra orbitale');
}

transform.addEventListener('dragging-changed', event => { orbit.enabled = !event.value && !state.fly; });
transform.addEventListener('mouseDown', () => {
  const item = state.items.get(state.selected); dragOrigin = item?.object.position.toArray() || null;
  if (item && state.tool !== 'translate') {
    transformParent.updateMatrixWorld(true);
    transformPreview = { inverse: transformProxy.matrixWorld.clone().invert(), items: linkedItems(item).map(changed => { changed.object.updateMatrixWorld(true); return { item: changed, matrix: changed.object.matrixWorld.clone(), localMatrix: changed.object.matrix.clone(), matrixAutoUpdate: changed.object.matrixAutoUpdate }; }) };
  }
});
transform.addEventListener('objectChange', () => {
  if (transformPreview) {
    transformParent.updateMatrixWorld(true); const delta = transformProxy.matrixWorld.clone().multiply(transformPreview.inverse);
    for (const { item, matrix } of transformPreview.items) applyPreviewWorldMatrix(item.object, delta.clone().multiply(matrix));
  }
  refreshPositionFields(); selectionBox?.update();
});
transform.addEventListener('mouseUp', () => {
  const item = state.items.get(state.selected);
  if (item && dragOrigin) pendingMutation = state.tool === 'translate' ? commitPosition(item.id, item.object.position.toArray(), dragOrigin) : commitTransform(item.id, new THREE.Euler().setFromQuaternion(transformProxy.quaternion, eulerOrder(item)).toArray().slice(0, 3), transformProxy.scale.toArray());
  dragOrigin = null;
});

renderer.domElement.addEventListener('contextmenu', event => event.preventDefault());
renderer.domElement.addEventListener('pointerdown', event => {
  if (state.loading) return;
  viewport.focus({ preventScroll: true });
  pointerOrigin = { x: event.clientX, y: event.clientY, time: performance.now(), button: event.button, gizmo: !!transform.axis };
}, { capture: true });
renderer.domElement.addEventListener('pointerup', event => {
  const origin = pointerOrigin; pointerOrigin = null;
  if (!origin || event.button !== 0 || origin.gizmo || transform.dragging || state.busy || state.loading || Math.hypot(event.clientX - origin.x, event.clientY - origin.y) > 5) return;
  const bounds = renderer.domElement.getBoundingClientRect();
  pointer.set((event.clientX - bounds.left) / bounds.width * 2 - 1, -(event.clientY - bounds.top) / bounds.height * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const targets = [...state.items.values()].filter(item => item.object.visible && item.sceneRole !== 'sky').map(item => item.object);
  const hits = raycaster.intersectObjects(targets, true).filter(hit => { let object = hit.object; while (object) { if (!object.visible) return false; object = object.parent; } return true; });
  selectItem(hits[0]?.object.userData.id || null);
});

const resize = new ResizeObserver(() => {
  const width = viewport.clientWidth; const height = viewport.clientHeight;
  if (!width || !height) return;
  renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
  renderDirty = true;
});
resize.observe(viewport);

const renderedPosition = new THREE.Vector3();
const renderedQuaternion = new THREE.Quaternion();
let renderedZoom = NaN;
// Editor controls may change visibility or the selection without changing the
// camera. Mark those changes once; a static level does not need 60 GPU draws.
for (const type of ['click', 'change', 'input', 'keydown']) document.addEventListener(type, () => { renderDirty = true; }, { capture: true });

function animate() {
  requestAnimationFrame(animate);
  const delta = Math.min(clock.getDelta(), 0.05);
  if (document.hidden) { freeNavigation.reset(); return; }
  freeNavigation.update(delta);
  if (!state.fly) orbit.update();
  const now = performance.now();
  const animated = updateAnimations(now);
  const cameraChanged = !camera.position.equals(renderedPosition) || !camera.quaternion.equals(renderedQuaternion) || camera.zoom !== renderedZoom;
  if (renderDirty || animated || cameraChanged || transform.dragging) {
    renderViewport(); renderDirty = false;
    renderedPosition.copy(camera.position); renderedQuaternion.copy(camera.quaternion); renderedZoom = camera.zoom;
    fpsFrames++;
    if (cameraChanged || transform.dragging) fpsMotionFrames++;
  }
  if (dialogModel) { dialogModel.orbit.update(); dialogModel.renderer.render(dialogModel.scene, dialogModel.camera); }
  $('cameraPosition').hidden = !state.fly;
  if (state.fly && now - cameraHudTime > 150) {
    camera.getWorldDirection(cameraHudDirection);
    const decimal = value => formatNumber(value, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const heading = THREE.MathUtils.radToDeg(Math.atan2(cameraHudDirection.y, cameraHudDirection.x));
    const pitch = THREE.MathUtils.radToDeg(Math.asin(THREE.MathUtils.clamp(cameraHudDirection.z, -1, 1)));
    $('cameraPosition').textContent = `${['X', 'Y', 'Z'].map((axis, i) => `${axis} ${decimal(camera.position.getComponent(i))}`).join(' · ')} | H ${decimal(heading)}° · V ${decimal(pitch)}°`;
    cameraHudTime = now;
  }
  if (now - fpsStart >= 900) {
    const idleAnimation = !fpsMotionFrames && state.animation && animationResources.size > 0;
    const rate = Math.round(fpsFrames * 1000 / (now - fpsStart));
    $('fps').textContent = fpsFrames ? idleAnimation ? `${translate('Textures')} · ${rate} ${translate('ips')}` : `${rate} FPS` : translate('Repos');
    $('fps').title = idleAnimation
      ? getLanguage() === 'fr' ? 'Vue immobile : seules les animations de textures rafraîchissent le rendu. Déplacez la caméra pour mesurer les FPS.' : 'Still view: only texture animations refresh the rendering. Move the camera to measure FPS.'
      : getLanguage() === 'fr' ? 'Images rendues par seconde pendant les déplacements.' : 'Rendered frames per second while moving.';
    fpsStart = now; fpsFrames = 0; fpsMotionFrames = 0;
  }
}
animate();

$('levelSelect').addEventListener('change', event => loadLevel(event.target.value));
document.querySelectorAll('[data-catalog-section]').forEach(button => button.addEventListener('click', () => switchCatalogSection(button.dataset.catalogSection)));
$('sceneSearch').addEventListener('input', event => { state.query = event.target.value.trim().toLowerCase(); renderHierarchy(); });
$('assetSearch').addEventListener('input', event => { state.assetQuery = event.target.value.trim().toLowerCase(); state.assetPage = 0; renderAssets(); $('assetContent').scrollTop = 0; });
$('assetScope').addEventListener('change', event => setAssetScope(event.target.value));
$('archiveSelect').addEventListener('change', event => openLibrary(event.target.value));
$('libraryRetry').addEventListener('click', () => openLibrary(state.archive, true));
$('assetPrev').addEventListener('click', () => { state.assetPage--; renderAssets(); $('assetContent').scrollTop = 0; });
$('assetNext').addEventListener('click', () => { state.assetPage++; renderAssets(); $('assetContent').scrollTop = 0; });
$('nodeFilter').addEventListener('change', event => { state.nodeFilter = event.target.value; renderHierarchy(); });
$('isolateBtn').addEventListener('click', () => { const item = state.items.get(state.selected); if (!item) return; const ids = new Set(linkedItems(item).map(other => other.id)); state.items.forEach(other => { other.visible = ids.has(other.id); applyItemVisibility(other); }); renderHierarchy(); updateVisibleStats(); frameObject(item.object); toast('Sélection et parties liées isolées dans la vue.'); });
$('restoreVisibilityBtn').addEventListener('click', () => { state.items.forEach(item => { item.visible = item.lodControlled ? (state.detail === 'source' ? item.authoredVisible !== false : item.editorVisible !== false) : true; applyItemVisibility(item); }); renderHierarchy(); updateVisibleStats(); toast('Décor affiché avec une seule version de chaque groupe de distance.'); });
$('cameraPreset').addEventListener('change', event => cameraPreset(event.target.value));
$('zoomSelect').addEventListener('change', event => { camera.zoom = Number(event.target.value); camera.updateProjectionMatrix(); });
$('captureBtn').addEventListener('click', captureViewport);
$('viewSettingsBtn').addEventListener('click', showViewSettings);
$('skySelect').addEventListener('change', event => { state.sky = event.target.value; state.items.forEach(applyItemVisibility); renderHierarchy(); updateVisibleStats(); });
$('detailSelect').addEventListener('change', event => {
  state.detail = event.target.value;
  state.items.forEach(item => { if (item.lodControlled) { item.visible = state.detail === 'source' ? item.authoredVisible !== false : item.editorVisible !== false; applyItemVisibility(item); } });
  renderHierarchy(); updateVisibleStats();
  setStatus(state.detail === 'source' ? 'Visibilité enregistrée au chargement du jeu' : 'Décor en détail maximal · groupes de distance résolus');
});
$('modelsBtn').addEventListener('click', () => toggle('models', 'modelsBtn', () => { state.items.forEach(applyItemVisibility); updateVisibleStats(); }));
$('animationBtn').addEventListener('click', () => toggle('animation', 'animationBtn', value => { $('animationBtn').querySelector('use').setAttribute('href', value ? '#i-pause' : '#i-play'); }));
$('animationFps').addEventListener('change', event => { state.animationFps = THREE.MathUtils.clamp(Number(event.target.value) || 12, 1, 60); event.target.value = state.animationFps; setStatus(`Cadence d’aperçu des textures : ${state.animationFps} images / seconde`); });
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => { state.filter = button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(item => item.classList.toggle('active', item === button)); renderHierarchy(); }));
document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => { setAssetTab(button.dataset.tab); $('assetContent').scrollTop = 0; }));
$('retryBtn').addEventListener('click', () => state.levels.length ? loadLevel($('levelSelect').value || catalogEntries(state.levels, state.catalogSection)[0]?.id || state.levels[0].id) : initialize());
$('frameBtn').addEventListener('click', () => frameObject(state.items.get(state.selected)?.object));
$('frameAllBtn').addEventListener('click', () => frameObject());
$('flyBtn').addEventListener('click', () => setFly(!state.fly));
$('gridBtn').addEventListener('click', () => toggle('grid', 'gridBtn', value => { if (grid) grid.visible = value; }));
$('wireBtn').addEventListener('click', () => toggle('wire', 'wireBtn', value => materialResources.forEach(material => { material.wireframe = value; })));
$('textureBtn').addEventListener('click', () => toggle('textures', 'textureBtn', value => materialResources.forEach(material => { material.map = value ? material.userData.originalMap : null; material.needsUpdate = true; })));
$('collisionBtn').addEventListener('click', () => toggle('collisions', 'collisionBtn', value => { if (collisionMesh) collisionMesh.visible = value; }));
$('helpersBtn').addEventListener('click', () => toggle('helpers', 'helpersBtn', () => { state.items.forEach(applyItemVisibility); if (!state.items.get(state.selected)?.object.visible) selectItem(null); }));
$('assetsCollapse').addEventListener('click', () => { const collapsed = document.querySelector('.workspace').classList.toggle('assets-collapsed'); $('assetsCollapse').title = collapsed ? 'Déployer le navigateur d’assets' : 'Réduire le navigateur d’assets'; });
$('moveTool').addEventListener('click', () => setTool('translate'));
$('rotateTool').addEventListener('click', () => setTool('rotate'));
$('scaleTool').addEventListener('click', () => setTool('scale'));
$('undoBtn').addEventListener('click', () => historyAction('undo'));
$('redoBtn').addEventListener('click', () => historyAction('redo'));
$('saveBtn').addEventListener('click', saveProject);
$('exportBtn').addEventListener('click', exportMod);
['posX', 'posY', 'posZ'].forEach(id => {
  $(id).addEventListener('change', () => {
    const item = state.items.get(state.selected);
    if (!item) return;
    const target = item.object.position.toArray();
    target[['posX', 'posY', 'posZ'].indexOf(id)] = $(id).value.trim() === '' ? NaN : Number($(id).value);
    pendingMutation = commitPosition(item.id, target);
  });
  $(id).addEventListener('keydown', event => { if (event.key === 'Enter') event.target.blur(); if (event.key === 'Escape') { event.target.value = round(state.items.get(state.selected)?.object.position.getComponent(['posX', 'posY', 'posZ'].indexOf(id)) || 0); event.target.blur(); } });
});
$('resetPositionBtn').addEventListener('click', () => { const item = state.items.get(state.selected); if (item) commitPosition(item.id, [...item.originalPosition]); });
$('resetTransformBtn').addEventListener('click', () => { const item = state.items.get(state.selected); if (item) pendingMutation = commitTransform(item.id, [...(item.originalLocalRotation || item.localRotation)], [...(item.originalLocalScale || item.localScale)]); });
[['rotX', 'rotY', 'rotZ'], ['scaleX', 'scaleY', 'scaleZ']].forEach((ids, group) => ids.forEach((id, index) => {
  $(id).addEventListener('change', () => {
    const item = state.items.get(state.selected); if (!item) return;
    const rotation = [...(item.localRotation || [0, 0, 0])]; const scale = [...(item.localScale || [1, 1, 1])];
    const value = $(id).value.trim() === '' ? NaN : Number($(id).value);
    if (group === 0) rotation[index] = THREE.MathUtils.degToRad(value); else scale[index] = value;
    pendingMutation = commitTransform(item.id, rotation, scale);
  });
  $(id).addEventListener('keydown', event => { if (event.key === 'Enter') event.target.blur(); if (event.key === 'Escape') { const item = state.items.get(state.selected); $(id).value = round(group === 0 ? THREE.MathUtils.radToDeg(item?.localRotation?.[index] || 0) : item?.localScale?.[index] ?? 1); event.target.blur(); } });
}));
$('helpBtn').addEventListener('click', () => {
  const body = openDialog('Commandes et raccourcis');
  const keys = getLanguage() === 'fr' ? 'Z / Q / S / D' : 'W / Q / S / D';
  const rows = [['Orbiter autour du niveau', 'Caméra orbitale · clic gauche + glisser'], ['Regarder à 360° sur place', 'Caméra libre · clic droit + glisser'], ['Flèches : regarder sur place', '← / → / ↑ / ↓'], ['Avancer / gauche / reculer / droite', keys], ['Vitesse de déplacement', 'Menu Vitesse · Maj accélère · Alt ralentit'], ['Monter / descendre en caméra libre', 'Espace / Ctrl'], ['Zoomer', 'Molette'], ['Déplacer / tourner / redimensionner', 'W / E / R · caméra orbitale / repère monde / local'], ['Rotation et échelle précises', 'Champs locaux de l’inspecteur · degrés'], ['Verrouiller la sélection', 'Bouton Verrouiller dans l’inspecteur'], ['Sélectionner un élément', 'Clic sur la vue ou la hiérarchie'], ['Cadrer la sélection / tout le niveau', 'F / Home'], ['Annuler / rétablir', 'Ctrl Z / Ctrl Maj Z'], ['Enregistrer le projet', 'Ctrl S'], ['Désélectionner', 'Échap']];
  rows.push(['Caméra libre / orbitale', 'Tab · vue 3D active'], ['Dupliquer la sélection', 'Ctrl D'], ['Supprimer l’ajout sélectionné', 'Suppr'], ['Restaurer le niveau d’origine', 'Édition → Restaurer le niveau d’origine'], ['Importer / remplacer modèles et textures', 'Menu Importation'], ['Exporter le mod', 'Ctrl Maj E']);
  rows.push(['Créer / supprimer / restaurer les niveaux', 'Menu Niveaux'], ['Construire une ISO du mod', 'Menu Niveaux → Construire une ISO du mod']);
  const grid = document.createElement('div'); grid.className = 'shortcut-grid';
  rows.forEach(row => row.forEach(value => { const span = document.createElement('span'); span.textContent = translate(value); grid.append(span); }));
  body.append(grid);
  const note = document.createElement('p'); note.className = 'dialog-note'; note.textContent = 'Cliquez dans la vue 3D pour activer les déplacements. Les raccourcis de navigation sont suspendus dans les champs de texte et les fenêtres de dialogue.'; body.append(note);
});
$('dialogClose').addEventListener('click', () => $('infoDialog').close());
$('infoDialog').addEventListener('close', () => { cleanDialogPreview(); renderer.domElement.focus({ preventScroll: true }); renderDirty = true; });
$('infoDialog').addEventListener('click', event => { if (event.target === $('infoDialog')) { const bounds = $('infoDialog').getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) $('infoDialog').close(); } });

window.addEventListener('keydown', async event => {
  if ($('infoDialog').open) return;
  const active = document.activeElement;
  const editing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(active?.tagName) || active?.isContentEditable || active?.closest?.('[contenteditable="true"], [role="textbox"]');
  if (freeNavigation.consumesKey(event)) return;
  const key = event.key.toLowerCase();
  if ((event.ctrlKey || event.metaKey) && key === 's') { event.preventDefault(); document.activeElement?.blur(); await pendingMutation; saveProject(); return; }
  if (editing) return;
  if (event.ctrlKey || event.metaKey) {
    if (key === 's') { event.preventDefault(); saveProject(); }
    if (key === 'z') { event.preventDefault(); historyAction(event.shiftKey ? 'redo' : 'undo'); }
    if (key === 'y') { event.preventDefault(); historyAction('redo'); }
    if (key === 'd') { event.preventDefault(); duplicateSelection(); }
    if (key === 'e' && event.shiftKey) { event.preventDefault(); exportMod(); }
    return;
  }
  if (key === 'tab' && viewport.contains(document.activeElement)) { event.preventDefault(); setFly(!state.fly); renderer.domElement.focus({ preventScroll: true }); return; }
  if (key === 'delete') { event.preventDefault(); deleteAddition(); return; }
  if (key === 'f' && !freeNavigation.looking) { event.preventDefault(); frameObject(state.items.get(state.selected)?.object); }
  if (event.code === 'Home') { event.preventDefault(); frameObject(); }
  if (event.code === 'Escape') {
    const menu = document.querySelector('.editor-menu[open]');
    if (menu) { menu.open = false; renderer.domElement.focus({ preventScroll: true }); return; }
    selectItem(null); freeNavigation.reset();
  }
  if (!freeNavigation.looking && !event.repeat && ['w', 'e', 'r'].includes(key) && !(state.fly && key === 'w')) setTool({ w: 'translate', e: 'rotate', r: 'scale' }[key]);
});
window.addEventListener('beforeunload', event => { if (state.unsaved || state.importing) { event.preventDefault(); event.returnValue = ''; } });

const sourceImport = createSourceImport({ api, openDialog, translate, formatNumber, toast,
  setBusy: value => { state.importing = value; updateActions(); },
  onSourceOpened: async () => {
    ++state.request; state.level = null; state.data = null; state.catalog = null; state.catalogSection = 'levels'; state.catalogLast = {}; state.exportDirectory = null; state.unsaved = false; state.pending = 0; state.canUndo = false; state.canRedo = false;
    characterCatalog = []; libraryArchives = null; libraryCache.clear(); libraryController?.abort();
    state.libraryData = null; state.libraryError = ''; state.archive = ''; state.assetScope = 'level';
    $('assetScope').value = 'level'; freeNavigation.reset(); cleanScene(); await initialize();
  } });
$('importIsoBtn').addEventListener('click', sourceImport.show);
const levelManager = createLevelManager({ api, openDialog, closeDialog: () => $('infoDialog').close(), toast, translate,
  getContext: () => ({ level: state.level, catalog: state.catalog, exportDirectory: state.exportDirectory,
    sourceIsoPath: state.catalog?.sourceIsoPath }), onChanged: refreshLevelCatalog });
const menuActions = {
  importIso: sourceImport.show, save: saveProject, export: exportMod, undo: () => historyAction('undo'), redo: () => historyAction('redo'),
  duplicate: duplicateSelection, delete: deleteAddition, resetLevel: showResetLevel,
  importModel: () => showAssetImport('model'), replaceModel: () => showAssetImport('model', state.selected),
  importTexture: () => showAssetImport('texture'), replaceTexture: () => showAssetImport('texture', state.data?.textures?.[0]?.id ?? null),
  freeCamera: () => setFly(true), orbitCamera: () => setFly(false), frame: () => frameObject(state.items.get(state.selected)?.object),
  frameAll: () => frameObject(), viewSettings: showViewSettings, help: () => $('helpBtn').click(),
  createLevel: levelManager.showCreate, deleteLevel: () => levelManager.showDelete(state.level),
  manageLevels: levelManager.showManage, buildIso: () => levelManager.showBuildIso(),
};
const closeMenus = () => document.querySelectorAll('.editor-menu[open]').forEach(menu => { menu.open = false; });
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => {
  closeMenus(); menuActions[button.dataset.action]?.();
  if (!$('infoDialog').open) renderer.domElement.focus({ preventScroll: true });
}));
document.querySelectorAll('.editor-menu').forEach(menu => menu.addEventListener('toggle', () => {
  if (menu.open) document.querySelectorAll('.editor-menu').forEach(other => { if (other !== menu) other.open = false; });
}));
document.addEventListener('pointerdown', event => { if (!event.target.closest('.editor-menu')) closeMenus(); });
$('lockBtn').addEventListener('click', () => changeLock(!state.items.get(state.selected)?.locked));
$('lockAllBtn').addEventListener('click', () => changeLock(true, true));
$('unlockAllBtn').addEventListener('click', () => changeLock(false, true));
$('flySpeedSelect').addEventListener('change', () => { freeNavigation.reset(); if (state.fly) renderer.domElement.focus({ preventScroll: true }); });
onLanguageChange(() => {
  freeNavigation.reset(); updateNavigationHint();
  renderLevelOptions($('levelSelect').value || state.level);
  if (state.level) {
    const level = state.levels.find(entry => entry.id === state.level);
    const name = level?.custom ? level.name : translateLevelName(level?.name || state.level);
    $('headerLevel').textContent = name; $('viewportLevel').textContent = `AZURIK / ${name.toUpperCase()}`;
    $('levelGroup').textContent = groupLabel(level?.group || level?.family);
  }
  if (state.data) { renderHierarchy(); renderInspector(); renderSummary(); renderCapabilities(); renderAssets(); $('sourceSize').textContent = size(state.data.stats?.sourceBytes || 0); const level = state.levels.find(level => level.id === state.level); const name = level?.custom ? level.name : translateLevelName(level?.name || state.level); setStatus(`${name} ouvert · ${number(state.data.meshes?.length || 0)} géométries · ${number(state.data.objects?.length || 0)} repères d’entités`); }
  updateActions();
});
updateNavigationHint();
freeNavigation.enable(state.fly, { focusViewport: false });

initialize();
