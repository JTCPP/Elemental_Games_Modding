"""Check the real browser material path for retail vertex illumination."""
from test_frontend_renderer import run_node


SETUP = """
import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
const source=fs.readFileSync('./web/app.js','utf8');
const functions=source.slice(source.indexOf('function gameMaterial('),source.indexOf('function buildScene('));
const make=new Function('THREE','state','textureResources','materialResources','animationResources','previewExposure','matrixFromRows',`${functions};return gameMaterial;`)(THREE,{textures:true,wire:false},new Map([['texture',new THREE.Texture()]]),new Map(),new Map(),{value:1},values=>new THREE.Matrix4().set(...values));
const mesh={positions:[5,6,7,6,6,7,5,7,7],position:[5,6,7],sourceNormals:[0,0,1,0,0,1,0,0,1],worldMatrix:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],textureStages:[{stage:0,textureId:'texture',flags:0x1000}],sourceLighting:{sourceVerified:true,ambient:[0,0,0],emissive:[0,0,0],directionals:[{direction:[0,0,-1],diffuse:[.2,.3,.4]}]}};
const info={technique:0,flags:16,opacity:1,alphaFunction:0,blendType:0};
const shader=()=>({uniforms:{},vertexShader:THREE.ShaderLib.basic.vertexShader,fragmentShader:THREE.ShaderLib.basic.fragmentShader});
"""


def test_black_source_vertices_receive_only_verified_light_and_saturate_before_interpolation():
    run_node(SETUP + """
const material=make('texture',true,info,mesh.textureStages[0],null,mesh);
const program=shader(); material.onBeforeCompile(program);
assert.match(program.vertexShader,/attribute vec3 retailSourceNormal/);
assert.match(program.vertexShader,/max\\(dot\\(retailSourceNormal, retailLightDirections\\[0\\]\\), 0\\.0\\)/);
assert.match(program.vertexShader,/vColor = 2\\.0 \\* clamp\\(vColor \\/ 2\\.0 \\+ retailLight, 0\\.0, 1\\.0\\)/);
assert.match(program.fragmentShader,/#include <color_fragment>/);
const object=new THREE.Mesh();object.position.set(...mesh.position);object.updateMatrixWorld(true);
material.onBeforeRender(null,null,null,null,object);
assert.ok(program.uniforms.retailLightDirections.value[0].distanceTo(new THREE.Vector3(0,0,1))<1e-12);
assert.deepEqual(program.uniforms.retailLightDiffuse.value[0].toArray(),[.2,.3,.4]);
assert.deepEqual(program.uniforms.retailBaseLight.value.toArray(),[0,0,0]);
assert.deepEqual(mesh.sourceNormals,[0,0,1,0,0,1,0,0,1]);
""")


def test_live_rotation_reflection_and_scale_use_inverse_world_light_direction():
    run_node(SETUP + """
const original=new THREE.Matrix4().makeRotationY(.3).scale(new THREE.Vector3(2,3,4));mesh.worldMatrix=[0,1,2,3].flatMap(r=>[0,1,2,3].map(c=>original.elements[c*4+r]));
const material=make('texture',true,info,mesh.textureStages[0],null,mesh);const program=shader();material.onBeforeCompile(program);
const object=new THREE.Mesh();object.position.set(...mesh.position);object.rotation.set(.2,-.5,.6);object.scale.set(-1,2,.5);object.updateMatrixWorld(true);
material.onBeforeRender(null,null,null,null,object);
const current=object.matrixWorld.clone().multiply(new THREE.Matrix4().makeTranslation(-5,-6,-7)).multiply(original);
const expected=new THREE.Vector3(0,0,-1).transformDirection(current.invert()).negate();
assert.ok(expected.distanceTo(program.uniforms.retailLightDirections.value[0])<1e-12);
object.rotation.set(0,0,0);object.scale.set(1,1,1);object.updateMatrixWorld(true);material.onBeforeRender(null,null,null,null,object);
assert.ok(new THREE.Vector3(0,0,-1).transformDirection(original.clone().invert()).negate().distanceTo(program.uniforms.retailLightDirections.value[0])<1e-12);
""")


def test_missing_normals_unverified_lights_and_texture_only_sky_do_not_gain_invented_light():
    run_node(SETUP + """
for(const changed of [{...mesh,sourceNormals:[]},{...mesh,textureStages:[]},{...mesh,sourceLighting:{...mesh.sourceLighting,sourceVerified:false}}]) {
 const material=make('texture',true,info,mesh.textureStages[0],null,changed);const program=shader();material.onBeforeCompile(program);
 assert.equal(material.userData.sourceLighting,null);assert.doesNotMatch(program.vertexShader,/retailBaseLight/);
}
const sky=make('texture',false,{...info,flags:0},mesh.textureStages[0],null,mesh);assert.equal(sky.userData.sourceLighting,null);
const dark={...mesh,sourceLighting:{...mesh.sourceLighting,directionals:[]}};
const black=make('texture',true,info,mesh.textureStages[0],null,dark);const program=shader();black.onBeforeCompile(program);
assert.deepEqual(program.uniforms.retailBaseLight.value.toArray(),[0,0,0]);assert.equal(program.uniforms.retailLightDirections,undefined);
assert.doesNotMatch(program.vertexShader,/uniform vec3 retailLightDirections\\[0\\]/);
""")


def test_material_cache_separates_source_light_sets_and_emission():
    run_node(SETUP + """
const first=make('texture',true,info,mesh.textureStages[0],null,mesh);
const other={...mesh,sourceLighting:{...mesh.sourceLighting,emissive:[.1,0,0]}};
const second=make('texture',true,info,mesh.textureStages[0],null,other);
assert.notEqual(first,second);assert.equal(make('texture',true,info,mesh.textureStages[0],null,mesh),first);
""")
