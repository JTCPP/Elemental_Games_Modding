"""Browser rendering paths checked without taking over the QA browser."""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the browser numeric checks")
    subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT,
                   capture_output=True, text=True, check=True)


def test_affine_preview_preserves_shear_and_restores_automatic_and_manual_matrices():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function applyPreviewWorldMatrix('),source.indexOf('function configureGizmo('));
const helpers=new Function('THREE','updateRetailCulling',`let transformPreview=null; ${functions}; return {apply:applyPreviewWorldMatrix,restore:restoreTransformPreview,set:v=>transformPreview=v,get:()=>transformPreview};`)(THREE,()=>{});
const target=new THREE.Matrix4().set(1,.4,0,9,0,2,.3,-4,.2,0,.8,6,0,0,0,1);
for(const automatic of [true,false]) {
  const parent=new THREE.Object3D();parent.position.set(3,7,-2);parent.rotation.set(.3,-.4,.7);parent.scale.set(2,1,3);
  const object=new THREE.Object3D();parent.add(object);object.position.set(2,-1,4);object.rotation.set(.5,.2,-.3);object.scale.set(1,2,.5);
  parent.updateMatrixWorld(true);
  if(!automatic) {object.matrix.set(1,.2,0,2,0,1,0,3,0,0,1,4,0,0,0,1);object.matrixAutoUpdate=false;object.matrixWorldNeedsUpdate=true;parent.updateMatrixWorld(true);}
  const original=object.matrix.clone();
  helpers.set({items:[{item:{object},localMatrix:original,matrixAutoUpdate:automatic}]});
  helpers.apply(object,target);parent.updateMatrixWorld(true);
  assert.equal(object.matrixAutoUpdate,false);
  object.matrixWorld.elements.forEach((value,index)=>assert.ok(Math.abs(value-target.elements[index])<1e-12));
  helpers.restore();parent.updateMatrixWorld(true);
  assert.equal(object.matrixAutoUpdate,automatic);assert.equal(helpers.get(),null);
  object.matrix.elements.forEach((value,index)=>assert.ok(Math.abs(value-original.elements[index])<1e-12));
}
""")


def test_actual_cloud_metadata_combines_all_three_alphas_and_secondary_transparency_flags():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function gameMaterial('),source.indexOf('function buildScene('));
const textures=new Map(['surf-0016','surf-0017','surf-0018'].map(id=>[id,new THREE.Texture()]));
const make=new Function('THREE','state','textureResources','materialResources','animationResources','previewExposure',`${functions};return gameMaterial;`)(THREE,{textures:true,wire:false},textures,new Map(),new Map(),{value:1});
const uvTransform={parametersVerified:true,scale:1,rotationDegrees:0,offset:[0,0],scrollVelocity:[.005,0],matrixMode:0};
const stages=[0,1,2].map(stage=>({stage,textureId:['surf-0016','surf-0017','surf-0018'][stage],flags:stage?0x20001000:0x1000,combinerMode:500,uvTransform}));
const mesh={positions:[0,0,0,1,0,0,0,1,0],uvs:[0,0,1,0,0,1],textureStages:stages};
const material=make(stages[0].textureId,false,{technique:4,flags:0,opacity:1,alphaFunction:0,alphaReference:.1,depthWrite:0,blendType:500},stages[0],null,mesh);
assert.equal(material.userData.layeredCombiner,true);assert.equal(material.transparent,true);
assert.equal(material.blendSrc,THREE.SrcAlphaFactor);assert.equal(material.blendDst,THREE.OneMinusSrcAlphaFactor);
const shader={uniforms:{},vertexShader:THREE.ShaderLib.basic.vertexShader,fragmentShader:THREE.ShaderLib.basic.fragmentShader};material.onBeforeCompile(shader);
assert.match(shader.fragmentShader,/diffuseColor\\.a \\*= gameLayer1\\.a;/);assert.match(shader.fragmentShader,/diffuseColor\\.a \\*= gameLayer2\\.a;/);
assert.doesNotMatch(shader.fragmentShader,/diffuseColor\\.a = 1\\.00000000/);
assert.match(shader.vertexShader,/vGameLayerUv1 = uv \\*/);assert.match(shader.vertexShader,/vGameLayerUv2 = uv \\*/);
""")


def test_game_camera_conversion_keeps_y_forward_and_z_up():
    run_node("""
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const camera=new THREE.PerspectiveCamera();camera.up.set(0,0,1);camera.lookAt(0,1,0);
const game=camera.quaternion.clone().multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),-Math.PI/2));
assert.ok(new THREE.Vector3(0,1,0).applyQuaternion(game).distanceTo(new THREE.Vector3(0,1,0))<1e-12);
assert.ok(new THREE.Vector3(0,0,1).applyQuaternion(game).distanceTo(new THREE.Vector3(0,0,1))<1e-12);
""")


def test_technique_one_uses_verified_primary_alpha_while_unknown_mask_programs_stay_partial():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function gameMaterial('),source.indexOf('function buildScene('));
const textures=new Map([['alpha',new THREE.Texture()]]);
const make=new Function('THREE','state','textureResources','materialResources','animationResources','previewExposure',`${functions};return gameMaterial;`)(THREE,{textures:true,wire:false},textures,new Map(),new Map(),{value:1});
const info={opacity:1,alphaFunction:500,alphaReference:.1,blendType:500,depthWrite:0};
for(const technique of [0,1]) {
  const material=make('alpha',false,{...info,technique},{flags:0x20000800});
  assert.equal(material.transparent,true);assert.equal(material.blendSrc,THREE.SrcAlphaFactor);
  const shader={uniforms:{},vertexShader:THREE.ShaderLib.basic.vertexShader,fragmentShader:THREE.ShaderLib.basic.fragmentShader};material.onBeforeCompile(shader);
  assert.match(shader.fragmentShader,/alphaByte > alphaRefByte/);
  assert.doesNotMatch(shader.fragmentShader,/diffuseColor\\.a = 1\\.00000000/);
}
const unsupported=make('alpha',true,{...info,technique:8},{flags:0x20000800});
assert.equal(unsupported.transparent,false);assert.equal(unsupported.vertexColors,false);
const shader={uniforms:{},vertexShader:THREE.ShaderLib.basic.vertexShader,fragmentShader:THREE.ShaderLib.basic.fragmentShader};unsupported.onBeforeCompile(shader);
assert.match(shader.fragmentShader,/diffuseColor\\.a = 1\\.00000000/);
""")


def test_terrain_blend_uses_mask_for_rgb_and_preserves_opaque_coverage():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function gameMaterial('),source.indexOf('function buildScene('));
const textures=new Map(['rock','grass','mask'].map(id=>[id,new THREE.Texture()]));
const make=new Function('THREE','state','textureResources','materialResources','animationResources','previewExposure',`${functions};return gameMaterial;`)(THREE,{textures:true,wire:false},textures,new Map(),new Map(),{value:1});
const uvTransform={parametersVerified:true,scale:1,rotationDegrees:0,offset:[0,0],scrollVelocity:[0,0],matrixMode:0};
const stages=['rock','grass','mask'].map((textureId,stage)=>({stage,textureId,flags:stage===2?0x20001000:0x1000,combinerMode:500,uvTransform}));
const mesh={positions:[0,0,0,1,0,0,0,1,0],uvs:[0,0,1,0,0,1],textureStages:stages};
const info={technique:6,flags:16,opacity:1,alphaFunction:500,alphaReference:.1,blendType:500,depthWrite:0};
const terrain=make('rock',true,info,stages[0],null,mesh);
assert.equal(terrain.userData.weightedTerrain,true);assert.equal(terrain.vertexColors,true);
assert.equal(terrain.transparent,false);assert.equal(terrain.depthWrite,true);
const shader={uniforms:{},vertexShader:THREE.ShaderLib.basic.vertexShader,fragmentShader:THREE.ShaderLib.basic.fragmentShader};terrain.onBeforeCompile(shader);
assert.match(shader.fragmentShader,/mix\\(diffuseColor\\.rgb, gameTerrainColor\\.rgb, gameTerrainMask\\.a\\)/);
assert.match(shader.fragmentShader,/diffuseColor\\.a = 1\\.00000000/);
assert.ok(shader.fragmentShader.indexOf('gameTerrainMask.a')<shader.fragmentShader.indexOf('#include <color_fragment>'));
assert.doesNotMatch(shader.fragmentShader,/diffuseColor\\.a \\*= gameLayer/);
assert.equal(shader.uniforms.gameLayerMap1.value,textures.get('grass'));assert.equal(shader.uniforms.gameLayerMap2.value,textures.get('mask'));
const faded=make('rock',true,{...info,opacity:.5,blendType:3},stages[0],null,mesh);
assert.equal(faded.transparent,true);assert.equal(faded.blendSrc,THREE.SrcAlphaFactor);assert.equal(faded.blendDst,THREE.OneMinusSrcAlphaFactor);
const layered=make('rock',true,{...info,technique:4},stages[0],null,mesh);
assert.notEqual(terrain.customProgramCacheKey(),layered.customProgramCacheKey());
const missingUV=make('rock',true,info,stages[0],null,{...mesh,textureStages:stages.map((entry,index)=>index===1?{...entry,flags:2}:entry)});
assert.equal(missingUV.userData.weightedTerrain,false);
assert.match(missingUV.userData.renderLimit,/UV1 absent/);
const missingLighting=make('rock',false,info,stages[0],null,mesh);assert.equal(missingLighting.userData.weightedTerrain,false);
""")


def test_retail_culling_preserves_world_winding_for_baked_and_runtime_negative_scales():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const materials=source.slice(source.indexOf('function gameMaterial('),source.indexOf('function buildScene('));
const preview=source.slice(source.indexOf('function applyPreviewWorldMatrix('),source.indexOf('function configureGizmo('));
const helpers=new Function('THREE','state','textureResources','materialResources','animationResources','previewExposure',`let transformPreview=null; ${materials}; ${preview}; return {make:gameMaterial,update:updateRetailCulling,apply:applyPreviewWorldMatrix,restore:restoreTransformPreview,set:v=>transformPreview=v};`)(THREE,{textures:true,wire:false},new Map(),new Map(),new Map(),{value:1});
const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute([0,0,0,1,0,0,0,1,0],3));geometry.setIndex([0,1,2]);
const front=helpers.make(null,false,{technique:0,flags:0x10});
const twoSided=helpers.make(null,false,{technique:0,flags:0x30});
assert.equal(front.side,THREE.FrontSide);assert.equal(twoSided.side,THREE.DoubleSide);
assert.notEqual(front,twoSided);assert.equal(helpers.make(null,false,{technique:0,flags:0}).side,THREE.FrontSide);
assert.equal(helpers.make(null,false,{technique:0}).side,THREE.DoubleSide);
const positive=new THREE.Mesh(geometry,front);positive.updateMatrixWorld(true);
const runtime=new THREE.Mesh(geometry,front);runtime.scale.x=-1;helpers.update(runtime);
assert.equal(runtime.material.side,THREE.BackSide);assert.equal(positive.material.side,THREE.FrontSide);
assert.notEqual(runtime.material,positive.material);
const bakedGeometry=geometry.clone();bakedGeometry.applyMatrix4(new THREE.Matrix4().makeScale(-1,1,1));
const baked=new THREE.Mesh(bakedGeometry,front);helpers.update(baked);assert.equal(baked.material,front);
const raycaster=new THREE.Raycaster();
const hit=(mesh,x,z)=>{raycaster.set(new THREE.Vector3(x,.25,z),new THREE.Vector3(0,0,-Math.sign(z)));return raycaster.intersectObject(mesh).length;};
assert.equal(hit(positive,.25,1),1);assert.equal(hit(positive,.25,-1),0);
for(const mesh of [runtime,baked]) {assert.equal(hit(mesh,-.25,1),0);assert.equal(hit(mesh,-.25,-1),1);}
// Three's negative-matrix FRONT_FACE flip XOR BackSide restores a constant
// CCW world front, while a baked reflection keeps the original FrontSide.
assert.equal((runtime.material.side===THREE.BackSide)!==(runtime.matrixWorld.determinant()<0),false);
assert.equal((baked.material.side===THREE.BackSide)!==(baked.matrixWorld.determinant()<0),false);
const mirroredDouble=new THREE.Mesh(geometry,twoSided);mirroredDouble.scale.x=-1;helpers.update(mirroredDouble);
assert.equal(mirroredDouble.material,twoSided);assert.equal(hit(mirroredDouble,-.25,1),1);assert.equal(hit(mirroredDouble,-.25,-1),1);
const initial=positive.matrix.clone();helpers.set({items:[{item:{object:positive},localMatrix:initial,matrixAutoUpdate:true}]});
helpers.apply(positive,new THREE.Matrix4().makeScale(-1,1,1));assert.equal(positive.material,runtime.material);
helpers.restore();assert.equal(positive.material,front);assert.equal(positive.matrixAutoUpdate,true);
""")


def test_editor_start_camera_uses_source_y_heading_without_double_translation():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const helper=source.slice(source.indexOf('function startCameraPose('),source.indexOf('function frameStart('));
const pose=new Function('THREE',`${helper};return startCameraPose;`)(THREE);
const position=new THREE.Vector3(10,20,3),fallback=new THREE.Vector3(1,0,7);
const matrix=[0,-3,0,300,2,0,0,400,0,0,.5,500,0,0,0,1];
const camera=pose(position,matrix,fallback);
assert.deepEqual(camera.position.toArray(),[22,20,8]);assert.deepEqual(camera.target.toArray(),[10,20,4.5]);
assert.deepEqual(position.toArray(),[10,20,3]);assert.deepEqual(fallback.toArray(),[1,0,7]);
const absent=pose(position,null,fallback);assert.deepEqual(absent.position.toArray(),[-2,20,8]);
const vertical=pose(position,[1,0,0,0,0,0,1,0,0,1,0,0,0,0,0,1],fallback);
assert.deepEqual(vertical.position.toArray(),[10,8,8]);
assert.ok(camera.position.distanceTo(camera.target)>12);
""")


def test_a5_platform_opacity_keeps_sky_visible_and_source_black_materials_opaque():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function gameMaterial('),source.indexOf('function buildScene('));
const textures=new Map(['fog0','fog1'].map(id=>[id,new THREE.Texture()]));
const make=new Function('THREE','state','textureResources','materialResources','animationResources','previewExposure',`${functions};return gameMaterial;`)(THREE,{textures:true,wire:false},textures,new Map(),new Map(),{value:1});
const uvTransform={parametersVerified:true,scale:5,rotationDegrees:0,offset:[0,0],scrollVelocity:[0,0],matrixMode:0};
const stages=[{stage:0,textureId:'fog0',flags:0x1000,combinerMode:500,uvTransform},{stage:1,textureId:'fog1',flags:0x801,combinerMode:500,uvTransform}];
const mesh={positions:[0,0,0,1,0,0,0,1,0],uvs:[0,0,1,0,0,1],textureStages:stages,platformRender:{sourceVerified:true,visibility:1,gameVisible:.75,meshOffset:2000000}};
const info={technique:4,flags:16,opacity:1,alphaFunction:0,alphaReference:.1,depthWrite:2,blendType:500};
const fog=make('fog0',true,info,stages[0],null,mesh);
assert.equal(fog.opacity,.75);assert.equal(fog.transparent,true);assert.equal(fog.depthWrite,false);assert.equal(fog.depthTest,true);
assert.equal(fog.vertexColors,true);assert.equal(fog.blendSrc,THREE.SrcAlphaFactor);assert.equal(fog.blendDst,THREE.OneMinusSrcAlphaFactor);
const shader={uniforms:{},vertexShader:THREE.ShaderLib.basic.vertexShader,fragmentShader:THREE.ShaderLib.basic.fragmentShader};fog.onBeforeCompile(shader);
assert.match(shader.fragmentShader,/diffuseColor\\.a = 0\\.75000000/);
// Shared GSHD and vertex data stay unchanged. Opacity belongs to an instance.
assert.equal(info.opacity,1);assert.deepEqual(mesh.positions,[0,0,0,1,0,0,0,1,0]);
const ordinary=make('fog0',true,info,stages[0],null,{...mesh,platformRender:undefined});
assert.notEqual(ordinary,fog);assert.equal(ordinary.opacity,1);assert.equal(ordinary.transparent,false);
const faded=make('fog0',true,{...info,opacity:.5},stages[0],null,{...mesh,platformRender:{...mesh.platformRender,visibility:.5}});
assert.equal(faded.opacity,.1875);
const undecoded=make('fog0',true,info,stages[0],null,{...mesh,platformRender:{...mesh.platformRender,sourceVerified:false}});
assert.equal(undecoded,ordinary);
const blackWorld=make(null,true,{technique:0,flags:16,opacity:1});
assert.equal(blackWorld.vertexColors,true);assert.equal(blackWorld.transparent,false);assert.equal(blackWorld.opacity,1);
// Authored day alpha=0 is a runtime gameState value. Choosing the day
// preview must show its original sky while keeping that source value.
const skySource={...mesh,sceneRole:'sky',platformRender:{...mesh.platformRender,gameVisible:0}};
const day=make('fog0',false,{technique:0,flags:0,opacity:1},stages[0],null,skySource);
assert.equal(day.opacity,1);assert.equal(skySource.platformRender.gameVisible,0);
// Do not hide the previously inspectable script-controlled world states.
const dormantWorld=make('fog0',false,{technique:0,flags:0,opacity:1},stages[0],null,{...skySource,sceneRole:undefined});
assert.equal(dormantWorld.opacity,1);
""")


def test_transparent_order_keeps_retail_distance_bias_separate_from_geometry():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function retailRenderDistance('),source.indexOf('renderer.setTransparentSort('));
const {distance,sort}=new Function('THREE',`${functions};return {distance:retailRenderDistance,sort:retailTransparentSort};`)(THREE);
const camera=new THREE.PerspectiveCamera();
const entries=[10,0,100,20].map((x,index)=>{
 const geometry=new THREE.BoxGeometry(2,2,2);geometry.computeBoundingBox();
 const object=new THREE.Mesh(geometry);object.position.set(x,0,0);object.updateMatrixWorld(true);
 object.userData.source={platformRender:{sourceVerified:true,meshOffset:index===1?2000000:0}};
 const matrix=object.matrixWorld.clone(),vertices=Array.from(geometry.attributes.position.array);
 object.userData.retailSortDistance=distance(object,camera);
 assert.deepEqual(object.matrixWorld.elements,matrix.elements);assert.deepEqual(Array.from(geometry.attributes.position.array),vertices);
 return {id:index,groupOrder:0,renderOrder:0,z:0,object};
});
assert.deepEqual(entries.slice().sort(sort).map(entry=>entry.id),[1,2,3,0]);
entries[1].object.userData.source.platformRender.meshOffset=-900;
entries[1].object.userData.retailSortDistance=distance(entries[1].object,camera);
assert.deepEqual(entries.slice().sort(sort).map(entry=>entry.id),[2,3,0,1]);
entries[1].renderOrder=10;assert.equal(entries.slice().sort(sort).at(-1).id,1);
const parent=new THREE.Group();parent.position.set(5,2,-3);parent.rotation.z=.8;parent.scale.set(2,3,4);
const mesh=new THREE.Mesh(new THREE.BoxGeometry(2,2,2));mesh.geometry.computeBoundingBox();parent.add(mesh);parent.updateMatrixWorld(true);
assert.ok(Math.abs(distance(mesh,camera)-new THREE.Vector3(5,2,-3).length())<1e-12);
""")


def test_level_overview_frames_solids_without_hiding_or_shrinking_fog():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function overviewBounds('),source.indexOf('function frameObject('));
const bounds=new Function('THREE',`${functions};return overviewBounds;`)(THREE);
const floor=new THREE.Mesh(new THREE.BoxGeometry(200,200,20),new THREE.MeshBasicMaterial());
const fog=new THREE.Mesh(new THREE.SphereGeometry(2800),new THREE.MeshBasicMaterial({transparent:true,opacity:.75,depthWrite:false}));
floor.updateMatrixWorld(true);fog.updateMatrixWorld(true);
assert.deepEqual(bounds([floor,fog]).getSize(new THREE.Vector3()).toArray(),[200,200,20]);
assert.equal(fog.visible,true);assert.equal(fog.geometry.parameters.radius,2800);
// An explicit selection and a scene containing only effects keep full bounds.
assert.ok(new THREE.Box3().setFromObject(fog).getSize(new THREE.Vector3()).x>5000);
assert.ok(bounds([fog]).getSize(new THREE.Vector3()).x>5000);
floor.visible=false;assert.ok(bounds([floor,fog]).getSize(new THREE.Vector3()).x>5000);
""")
