from test_frontend_renderer import run_node


def test_packed_geometry_preserves_float32_values_indices_and_small_vectors():
    run_node(r"""
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
import { decodeSceneTransport } from './web/scene-transport.mjs';
const packed=(type,values)=>{
  const bytes=new ArrayBuffer(values.length*4), view=new DataView(bytes);
  values.forEach((v,i)=>type==='f32'?view.setFloat32(i*4,v,true):view.setUint32(i*4,v,true));
  return {$studioBuffer:type,length:values.length,data:Buffer.from(bytes).toString('base64')};
};
const position=[10,20,30];
const scene=decodeSceneTransport({scene:{meshes:[{positions:packed('f32',[.1,-2,3,4,5,6,7,8,9]),indices:packed('u32',[0,1,2]),position}],resources:[]}});
const mesh=scene.scene.meshes[0];
assert.ok(mesh.positions instanceof Float32Array);
assert.equal(mesh.positions[0],Math.fround(.1));
assert.deepEqual(mesh.indices,[0,1,2]);
assert.equal(mesh.position,position);
const geometry=new THREE.BufferGeometry();
geometry.setAttribute('position',new THREE.Float32BufferAttribute(mesh.positions,3));
geometry.setIndex(mesh.indices);
assert.equal(geometry.index.count,3);
assert.equal(geometry.attributes.position.count,3);
assert.deepEqual(decodeSceneTransport({meshes:[{positions:[0,1,2]}]}),{meshes:[{positions:[0,1,2]}]});
""")


def test_invalid_packed_buffers_reject_sizes_types_and_nonfinite_geometry():
    run_node(r"""
import assert from 'node:assert/strict';
import { decodeSceneTransport } from './web/scene-transport.mjs';
for(const value of [
  {$studioBuffer:'f64',length:1,data:'AAAAAA=='},
  {$studioBuffer:'f32',length:-1,data:''},
  {$studioBuffer:'f32',length:1,data:'AAAA'},
  {$studioBuffer:'f32',length:1,data:'!!!!AA=='},
  {$studioBuffer:'f32',length:1,data:'AAAAgA=='},
  {$studioBuffer:'f32',length:1,data:'AAAAAA=A'},
  {$studioBuffer:'u32',length:1000000000,data:''},
]) {
  // Negative zero is a valid finite float, checked separately below.
  if(value.data==='AAAAgA==') continue;
  assert.throws(()=>decodeSceneTransport(value));
}
const bad=Buffer.alloc(4);bad.writeFloatLE(Infinity);
assert.throws(()=>decodeSceneTransport({$studioBuffer:'f32',length:1,data:bad.toString('base64')}));
const zero=decodeSceneTransport({$studioBuffer:'f32',length:1,data:'AAAAgA=='});
assert.ok(Object.is(zero[0],-0));
assert.throws(()=>decodeSceneTransport({$studioBuffer:'f32',length:1,data:'AAAAAA==AAAAAA=='}));
""")
