"""Verify source reflection coordinates against retail CPU/VSH constants."""
from test_frontend_lighting_shader import SETUP
from test_frontend_renderer import run_node


REFLECTION = """
mesh.worldPositionVerified=true;mesh.coordinateSpace='world';
const transform={parametersVerified:true,scale:5,offset:[0,0],rotationDegrees:0,matrixMode:0,scrollVelocity:[0,0]};
mesh.uvs=[0,0,1,0,0,1];
mesh.textureStages=[{stage:0,textureId:'texture',flags:0x1000,combinerMode:500,uvTransform:transform},{stage:1,textureId:'texture',flags:0x801,combinerMode:500,uvTransform:transform}];
const layeredInfo={...info,technique:4,blendType:500,depthWrite:2};
const object=new THREE.Mesh();object.position.set(...mesh.position);object.updateMatrixWorld(true);
const camera=new THREE.PerspectiveCamera();camera.position.set(5,-7,3);camera.updateMatrixWorld(true);
"""


def test_two_fog_layers_apply_source_scale_and_world_reflection_from_first_frame():
    run_node(SETUP + REFLECTION + """
mesh.platformRender={sourceVerified:true,gameVisible:.75,visibility:1};
const material=make('texture',true,layeredInfo,mesh.textureStages[0],null,mesh);
assert.equal(material.userData.layeredCombiner,true);assert.equal(material.userData.retailReflection2D,true);
assert.equal(material.opacity,.75);assert.equal(material.depthWrite,false);
material.onBeforeRender(null,null,camera,null,object);
const program=shader();material.onBeforeCompile(program);
assert.match(program.vertexShader,/vMapUv = uv \\* 5\\.00000000/);
assert.match(program.vertexShader,/vGameLayerUv1 = retailReflectionViewUv \\* 5\\.00000000/);
assert.match(program.vertexShader,/2\\.0 \\* dot\\(retailSourceNormal, retailToEye\\) \\* retailSourceNormal - retailToEye/);
assert.equal((program.vertexShader.match(/attribute vec3 retailSourceNormal;/g)||[]).length,1);
assert.deepEqual(program.uniforms.retailEyeLocal.value.toArray(),[5,-7,3]);
assert.equal(material.userData.renderLimit,'');
assert.match(program.fragmentShader,/diffuseColor\\.a \\*= gameLayer1\\.a/);
""")


def test_reflection_eye_and_basis_match_original_xbe_uploads_with_nonuniform_transform():
    run_node(SETUP + REFLECTION + """
const proof=JSON.parse(fs.readFileSync('tests/fixtures/a5-generated-uv-emulate.json','utf8')).scenarios[1];
mesh.worldMatrix=proof.worldMatrix;
camera.matrixWorldInverse.set(...proof.viewMatrix);camera.matrixWorld.copy(camera.matrixWorldInverse.clone().invert());
const material=make('texture',true,layeredInfo,mesh.textureStages[0],null,mesh);const program=shader();
material.onBeforeCompile(program);material.onBeforeRender(null,null,camera,null,object);
const world=program.uniforms.retailSourceWorld.value;
for(let r=0;r<3;r++)for(let c=0;c<4;c++)assert.ok(Math.abs(world.elements[c*4+r]-proof.c103Through106[r*4+c])<2e-5);
const viewWorld=program.uniforms.retailReflectionView.value;
for(let r=0;r<3;r++)for(let c=0;c<4;c++)assert.ok(Math.abs(viewWorld.elements[c*4+r]-proof.c100Through102[r*4+c])<2e-5);
assert.ok(program.uniforms.retailEyeLocal.value.distanceTo(new THREE.Vector3(...proof.c103Through106.slice(12,15)))<2e-5);
camera.position.set(8,9,10);camera.updateMatrixWorld(true);material.onBeforeRender(null,null,camera,null,object);
const expected=camera.position.clone().applyMatrix4(new THREE.Matrix4().set(...mesh.worldMatrix).invert());
assert.ok(expected.distanceTo(program.uniforms.retailEyeLocal.value)<1e-10);
""")


def test_generated_coordinates_require_verified_world_geometry_and_the_exact_source_opcode():
    run_node(SETUP + REFLECTION + """
for(const change of [{worldPositionVerified:false},{coordinateSpace:'asset'},{sourceNormals:[]},{worldMatrix:new Array(16).fill(0)}]) {
 const changed={...mesh,...change};const material=make('texture',true,layeredInfo,mesh.textureStages[0],null,changed);
 assert.equal(material.userData.retailReflection2D,false);assert.equal(material.userData.layeredCombiner,false);
}
for(const flags of [0x800,0x201,0x401]) {
 const changed={...mesh,textureStages:[mesh.textureStages[0],{...mesh.textureStages[1],flags}]};
 const material=make('texture',true,layeredInfo,mesh.textureStages[0],null,changed);assert.equal(material.userData.retailReflection2D,false);
}
const context={textures:new Map([['texture',new THREE.Texture()]]),materials:new Map(),textureMetadata:[{id:'texture',kind:'cube'}]};
assert.equal(make('texture',true,layeredInfo,mesh.textureStages[0],context,mesh).userData.retailReflection2D,false);
""")


def test_unlit_generated_layers_have_one_source_normal_attribute_and_per_instance_uniforms():
    run_node(SETUP + REFLECTION + """
const material=make('texture',false,{...layeredInfo,flags:0},mesh.textureStages[0],null,mesh);const program=shader();material.onBeforeCompile(program);
assert.equal(material.userData.sourceLighting,null);assert.equal((program.vertexShader.match(/attribute vec3 retailSourceNormal;/g)||[]).length,1);
const other={...mesh,position:[10,20,30]};assert.notEqual(make('texture',false,{...layeredInfo,flags:0},mesh.textureStages[0],null,other),material);
""")


def test_identity_uv_transform_uses_world_coordinates_instead_of_view_coordinates():
    run_node(SETUP + REFLECTION + """
mesh.textureStages[1].uvTransform={...transform,scale:1};
const material=make('texture',true,layeredInfo,mesh.textureStages[0],null,mesh);const program=shader();material.onBeforeCompile(program);
assert.match(program.vertexShader,/vGameLayerUv1 = retailReflectionWorldUv \\* 1\\.00000000/);
assert.match(program.vertexShader,/retailReflectionWorldUv = \\(mat3\\(retailSourceWorld\\) \\* retailReflectedLocal\\)\\.xy \\* vec2\\(1\\.0, -1\\.0\\)/);
const transformed=make('texture',true,layeredInfo,mesh.textureStages[0],null,{...mesh,textureStages:[mesh.textureStages[0],{...mesh.textureStages[1],uvTransform:transform}]});
assert.notEqual(transformed.customProgramCacheKey(),material.customProgramCacheKey());
""")
