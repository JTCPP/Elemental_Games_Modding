"""Exercise local asset parsers and actual geometry conversion, without a GUI."""
from test_frontend_renderer import run_node


IMPORTS = """
import assert from 'node:assert/strict';
import {parseObj,parseModel,validateModel,parseModelFiles,readPngFile,gltfLocalBufferName} from './web/asset-import.mjs';
"""


def test_obj_negative_indices_uv_seams_polygon_triangulation_and_axis_conversion():
    run_node(IMPORTS + r"""
const obj=`v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nvn 0 0 1\nf -4/1/1 -3/2/1 -2/3/1 -1/4/1`;
const model=parseObj(obj);assert.deepEqual(model.indices,[0,1,2,0,2,3]);assert.equal(model.positions.length,12);assert.deepEqual(model.uvs,[0,0,1,0,1,1,0,1]);assert.deepEqual(model.normals.slice(0,3),[0,0,1]);
const converted=parseModel(obj,'sample.obj','y');assert.deepEqual(converted.positions.slice(6,9),[1,-0,1]);assert.deepEqual(converted.normals.slice(0,3),[0,-1,0]);
const seam=parseObj(`v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nf 1/1 2/1 3/1\nf 1/2 3/1 2/1`);assert.equal(seam.positions.length,12);assert.deepEqual(seam.indices,[0,1,2,3,2,1]);
const plain=parseObj(`v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3`);assert.equal(plain.uvs,undefined);assert.equal(plain.normals,undefined);
""")


def test_geometry_validation_rejects_malformed_indices_values_dimensions_and_oversize():
    run_node(IMPORTS + r"""
const good={positions:[0,0,0,1,0,0,0,1,0],indices:[0,1,2]};
assert.deepEqual(validateModel(good),good);assert.deepEqual(parseModel(JSON.stringify({model:good}),'sample.json'),good);
for(const model of [ {...good,positions:[0,0]}, {...good,positions:[0,0,NaN,1,0,0,0,1,0]}, {...good,positions:[100001,0,0,1,0,0,0,1,0]}, {...good,indices:[0,1,9]}, {...good,indices:[0,1,1]}, {...good,indices:[0,1,1.5]}, {...good,uvs:[0,0]}, {...good,normals:[0,0,1]}, {...good,positions:Array(300003).fill(0)} ])assert.throws(()=>validateModel(model));
assert.throws(()=>parseObj('v 0 0 0\nf 1 2 3'));
assert.throws(()=>parseObj('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 2 3'));
assert.throws(()=>parseModel('{','bad.json'));
assert.throws(()=>parseModel('','bad.exe'));
""")


def test_png_signature_size_format_and_reader_failures_are_bounded():
    run_node(IMPORTS + """
class Reader {readAsDataURL(file){this.result=file.result;this.onload();}}
const file={name:'texture.png',size:20,result:'data:image/png;base64,iVBORw0KGgoAAAA'};
assert.equal(await readPngFile(file,Reader),file.result);
await assert.rejects(readPngFile({...file,name:'texture.jpg'},Reader));
await assert.rejects(readPngFile({...file,size:8*1024*1024+1},Reader));
await assert.rejects(readPngFile({...file,result:'data:image/png;base64,YQ=='},Reader));
class Broken {readAsDataURL(){this.onerror();}}
await assert.rejects(readPngFile(file,Broken));
""")


def test_gltf_local_only_resource_policy_rejects_remote_paths_and_traversal():
    run_node(IMPORTS + r"""
assert.equal(gltfLocalBufferName('mesh.bin'),'mesh.bin');assert.equal(gltfLocalBufferName('data:application/octet-stream;base64,AQID'),null);
for(const uri of ['https://example.com/model.bin','file:///C:/secret.bin','//example.com/model.bin','../mesh.bin','a/../mesh.bin','C:\\\\mesh.bin','mesh.bin?token=x','mesh.bin#fragment','%2e%2e%2fmesh.bin','%zz',undefined])assert.throws(()=>gltfLocalBufferName(uri));
await assert.rejects(parseModelFiles([{name:'remote.gltf',size:80,text:async()=>JSON.stringify({asset:{version:'2.0'},buffers:[{uri:'https://example.com/model.bin',byteLength:4}]})}]));
await assert.rejects(parseModelFiles([{name:'missing.gltf',size:80,text:async()=>JSON.stringify({asset:{version:'2.0'},buffers:[{uri:'mesh.bin',byteLength:4}]})}]));
""")


GLTF_SETUP = """
globalThis.ProgressEvent=class {constructor(type,data){this.type=type;Object.assign(this,data);}};
globalThis.FileReader=class {readAsDataURL(blob){blob.arrayBuffer().then(bytes=>{this.result='data:application/octet-stream;base64,'+Buffer.from(bytes).toString('base64');this.onload();});}};
const binary=new Float32Array([0,0,0,1,0,0,0,1,0]).buffer;
const document={asset:{version:'2.0'},buffers:[{uri:'mesh.bin',byteLength:36}],bufferViews:[{buffer:0,byteLength:36}],accessors:[{bufferView:0,componentType:5126,count:3,type:'VEC3',min:[0,0,0],max:[1,1,0]}],meshes:[{primitives:[{attributes:{POSITION:0}}]}],nodes:[{mesh:0,translation:[2,3,4]}],scenes:[{nodes:[0]}],scene:0};
const file={name:'model.gltf',size:500,text:async()=>JSON.stringify(document)};
const buffer={name:'mesh.bin',size:36,arrayBuffer:async()=>binary};
"""


def test_real_gltf_loader_assembles_local_buffer_geometry_and_preserves_node_transforms():
    run_node(IMPORTS + GLTF_SETUP + """
const model=await parseModelFiles([file,buffer],'z');assert.deepEqual(model.positions,[2,3,4,3,3,4,2,4,4]);assert.deepEqual(model.indices,[0,1,2]);
const converted=await parseModelFiles([file,buffer],'y');assert.deepEqual(converted.positions,[2,-4,3,3,-4,3,2,-4,4]);
""")


def test_real_glb_loader_reads_embedded_binary_without_external_files():
    run_node(IMPORTS + GLTF_SETUP + """
delete document.buffers[0].uri;
const json=Buffer.from(JSON.stringify(document)),padding=(4-json.length%4)%4,jsonBlock=Buffer.concat([json,Buffer.alloc(padding,32)]),bin=Buffer.from(binary);
const glb=Buffer.alloc(12+8+jsonBlock.length+8+bin.length);glb.writeUInt32LE(0x46546c67,0);glb.writeUInt32LE(2,4);glb.writeUInt32LE(glb.length,8);glb.writeUInt32LE(jsonBlock.length,12);glb.writeUInt32LE(0x4e4f534a,16);jsonBlock.copy(glb,20);const offset=20+jsonBlock.length;glb.writeUInt32LE(bin.length,offset);glb.writeUInt32LE(0x004e4942,offset+4);bin.copy(glb,offset+8);
const model=await parseModelFiles([{name:'model.glb',size:glb.length,arrayBuffer:async()=>glb.buffer.slice(glb.byteOffset,glb.byteOffset+glb.byteLength)}],'z');assert.deepEqual(model.positions,[2,3,4,3,3,4,2,4,4]);
await assert.rejects(parseModelFiles([{name:'broken.glb',size:10,arrayBuffer:async()=>new ArrayBuffer(10)}]));
""")
