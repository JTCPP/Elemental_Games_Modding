"""Rendering caches preserve live edits while avoiding redundant matrix work."""
from test_frontend_lighting_shader import SETUP
from test_frontend_reflection import REFLECTION
from test_frontend_renderer import run_node


def test_stationary_light_and_reflection_reuse_one_inverse_and_new_programs_receive_uniforms():
    run_node(SETUP + REFLECTION + """
const material=make('texture',true,layeredInfo,mesh.textureStages[0],null,mesh);
const program=shader();material.onBeforeCompile(program);
const originalInvert=THREE.Matrix4.prototype.invert;
let inversions=0;
THREE.Matrix4.prototype.invert=function(){inversions++;return originalInvert.call(this);};
try {
 material.onBeforeRender(null,null,camera,null,object);
 assert.equal(inversions,1); // Both lighting and reflection use the same inverse.
 const initial={eye:program.uniforms.retailEyeLocal.value.toArray(),light:program.uniforms.retailLightDirections.value[0].toArray()};
 for(let frame=0;frame<120;frame++)material.onBeforeRender(null,null,camera,null,object);
 assert.equal(inversions,1);
 assert.deepEqual(program.uniforms.retailEyeLocal.value.toArray(),initial.eye);
 assert.deepEqual(program.uniforms.retailLightDirections.value[0].toArray(),initial.light);
 const second=shader();material.onBeforeCompile(second);
 material.onBeforeRender(null,null,camera,null,object);
 assert.equal(inversions,1);
 assert.deepEqual(second.uniforms.retailEyeLocal.value.toArray(),initial.eye);
 assert.deepEqual(second.uniforms.retailLightDirections.value[0].toArray(),initial.light);
 // Camera travel changes reflection coordinates without changing object lighting.
 camera.position.set(10,20,-30);camera.updateMatrixWorld(true);
 const beforeDraw=inversions;material.onBeforeRender(null,null,camera,null,object);
 assert.equal(inversions,beforeDraw);
 assert.ok(program.uniforms.retailEyeLocal.value.distanceTo(camera.position)<1e-12);
 assert.deepEqual(program.uniforms.retailLightDirections.value[0].toArray(),initial.light);
} finally {THREE.Matrix4.prototype.invert=originalInvert;}
""")


def test_live_mirrored_shear_edits_shared_instances_and_undo_invalidate_light_and_reflection():
    run_node(SETUP + REFLECTION + """
const material=make('texture',true,layeredInfo,mesh.textureStages[0],null,mesh);
const program=shader();material.onBeforeCompile(program);
const original=object.matrix.clone();
const changed=new THREE.Matrix4().set(-2,.4,0,8,0,3,.6,-5,.2,0,4,12,0,0,0,1);
const other=new THREE.Mesh();other.position.set(30,40,50);other.updateMatrixWorld(true);
function verify(target){
 material.onBeforeRender(null,null,camera,null,target);
 const expected=target.matrixWorld.clone().multiply(new THREE.Matrix4().makeTranslation(...mesh.position.map(x=>-x))).multiply(new THREE.Matrix4().set(...mesh.worldMatrix));
 const inverse=expected.clone().invert();
 assert.ok(program.uniforms.retailLightDirections.value[0].distanceTo(new THREE.Vector3(0,0,-1).transformDirection(inverse).negate())<1e-12);
 assert.ok(program.uniforms.retailEyeLocal.value.distanceTo(camera.position.clone().applyMatrix4(inverse))<1e-12);
 program.uniforms.retailSourceWorld.value.elements.forEach((v,i)=>assert.ok(Math.abs(v-expected.elements[i])<1e-12));
}
verify(object);
object.matrixAutoUpdate=false;object.matrix.copy(changed);object.updateMatrixWorld(true);verify(object);
verify(other);verify(object); // A shared material must never inherit another instance's uniforms.
object.matrix.copy(original);object.updateMatrixWorld(true);verify(object);
object.matrix.makeScale(0,1,1);object.updateMatrixWorld(true);
material.onBeforeRender(null,null,camera,null,object); // Singular edits do not install NaN uniforms.
assert.ok(program.uniforms.retailSourceInverse.value.elements.every(Number.isFinite));
object.matrix.copy(changed);object.updateMatrixWorld(true);verify(object);
""")


def test_transparent_sort_reuses_bounds_but_keeps_camera_bias_and_live_geometry_changes():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function retailRenderDistance('),source.indexOf('function renderViewport('));
const renderer={setTransparentSort(){}};
const {distance,update}=new Function('THREE','renderer',`${functions};return {distance:retailRenderDistance,update:updateRenderDistances};`)(THREE,renderer);
const camera=new THREE.PerspectiveCamera();camera.position.set(15,9,-2);
const root=new THREE.Group();root.rotation.z=.7;root.scale.set(-2,3,4);
const geometry=new THREE.BoxGeometry(2,4,6);geometry.computeBoundingBox();
const mesh=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({transparent:true}));
mesh.position.set(3,7,11);root.add(mesh);root.updateMatrixWorld(true);
mesh.userData.source={platformRender:{sourceVerified:true,meshOffset:100}};
const reference=()=>geometry.boundingBox.clone().applyMatrix4(mesh.matrixWorld).getCenter(new THREE.Vector3()).distanceTo(camera.position)+mesh.userData.source.platformRender.meshOffset;
const expected=reference(), originalApply=THREE.Box3.prototype.applyMatrix4;
let transforms=0;
THREE.Box3.prototype.applyMatrix4=function(m){transforms++;return originalApply.call(this,m);};
try {
 assert.ok(Math.abs(distance(mesh,camera)-expected)<1e-12);
 for(let frame=0;frame<120;frame++){camera.position.x+=.1;update(root,camera);}
 assert.equal(transforms,1); // Camera motion only recomputes scalar distances.
 assert.ok(Math.abs(mesh.userData.retailSortDistance-reference())<1e-12);
 const before=transforms;
 mesh.visible=false;root.position.z=20;update(root,camera);assert.equal(transforms,before);
 mesh.visible=true;update(root,camera);assert.equal(transforms,before+1);
 assert.ok(Math.abs(mesh.userData.retailSortDistance-reference())<1e-12);
 geometry.boundingBox.max.x+=10;
 const previous=transforms;update(root,camera);assert.equal(transforms,previous+1);
 assert.ok(Math.abs(mesh.userData.retailSortDistance-reference())<1e-12);
 mesh.userData.source.platformRender.meshOffset=-900;update(root,camera);
 assert.ok(Math.abs(mesh.userData.retailSortDistance-reference())<1e-12);
} finally {THREE.Box3.prototype.applyMatrix4=originalApply;}
assert.equal(mesh.visible,true);assert.equal(root.children.length,1);
assert.deepEqual(Array.from(geometry.attributes.position.array),Array.from(new THREE.BoxGeometry(2,4,6).attributes.position.array));
""")


def test_camera_facing_nodes_keep_full_turns_without_rebuilding_for_texture_only_frames():
    run_node("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
import {effectiveParentRows} from './web/source-transform.mjs';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function updateCameraFacing('),source.indexOf('function updateVisibleStats('));
const identity=new THREE.Matrix4();const rows=m=>[0,1,2,3].flatMap(r=>[0,1,2,3].map(c=>m.elements[c*4+r]));
const camera=new THREE.PerspectiveCamera();camera.up.set(0,0,1);camera.lookAt(0,1,0);
const object=new THREE.Mesh();const item={object,nodeIndex:1,cameraDependent:true};
const nodes=new Map([[0,{worldMatrix:rows(identity),cameraDependent:false}],[1,{parent:0,cameraDependent:true,inheritMode:3,position:[0,0,0],localMatrix:rows(identity)}]]);
let effectiveCalls=0;
const effective=(...args)=>{effectiveCalls++;return effectiveParentRows(...args);};
const update=new Function('THREE','cameraFacingItems','transformPreview','nodeMap','camera','effectiveParentRows','matrixFromRows','rowsFromMatrix','state','selectionBox',`let cameraFacingQuaternion=null;${functions};return updateCameraFacing;`)(THREE,[{item,sourceInverse:identity.clone(),objectMatrix:identity.clone()}],null,nodes,camera,effective,a=>new THREE.Matrix4().set(...a),rows,{items:new Map(),selected:null},null);
update();const initial=object.matrix.clone();assert.equal(effectiveCalls,1);
for(let frame=0;frame<120;frame++)update();assert.equal(effectiveCalls,1);assert.ok(object.matrix.equals(initial));
camera.position.set(25,30,40);update();assert.equal(effectiveCalls,1);
camera.quaternion.setFromAxisAngle(new THREE.Vector3(0,0,1),2.4);update();assert.equal(effectiveCalls,2);
assert.ok(!object.matrix.equals(initial));
const gameQuaternion=camera.quaternion.clone().multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),-Math.PI/2));
const expected=new THREE.Matrix4().set(...effectiveParentRows(nodes.get(1),rows(identity),gameQuaternion.toArray()));
object.matrix.elements.forEach((v,i)=>assert.ok(Math.abs(v-expected.elements[i])<1e-12));
""")
