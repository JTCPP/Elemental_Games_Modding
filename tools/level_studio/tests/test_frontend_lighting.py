"""Numeric RGB lighting regressions without taking over the QA browser."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for browser lighting numeric checks")
    subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT,
                   capture_output=True, text=True, check=True)


def test_emission_lighting_does_not_whiten_black_unlit_geometry():
    run_node("""
import assert from 'node:assert/strict';
import {retailLightingColors,validateRetailLightingConfig} from './web/retail-lighting.mjs';
const config={sourceVerified:true,mode:'source',emissive:[0,0,0],directionals:[]};
assert.equal(validateRetailLightingConfig(config),true);
assert.deepEqual(Array.from(retailLightingColors([0,0,0],null,config)),[0,0,0]);
config.emissive=[.0625,.125,.25];
assert.deepEqual(Array.from(retailLightingColors([0,0,0],null,config)),config.emissive);
const input=new Float32Array([.125,.25,.5]);
assert.deepEqual(Array.from(retailLightingColors(input,null,config)),[.1875,.375,.75]);
assert.deepEqual(Array.from(input),[.125,.25,.5]);
""")


def test_directional_constants_match_executed_retail_9a8c0():
    run_node("""
import assert from 'node:assert/strict';
import {retailLightingColors} from './web/retail-lighting.mjs';
// Original 9A8C0 audit: c107=[.08,.06,.04], light diffuse=[.8,.45,.2],
// toLight=[0,0,1]. Test the source VSH Lambert output on opposite normals.
const config={sourceVerified:true,mode:'source',emissive:[0,0,0],ambient:[.08,.06,.04],
 directionals:[{direction:[0,0,2],diffuse:[.8,.45,.2]}]};
const normals=[0,0,1, 0,0,-1, 0,0,.5];
const output=retailLightingColors(new Float32Array(9),normals,config);
const expected=[.88,.51,.24, .08,.06,.04, .48,.285,.14];
output.forEach((value,index)=>assert.ok(Math.abs(value-expected[index])<1e-6));
assert.deepEqual(normals,[0,0,1,0,0,-1,0,0,.5]);
""")


def test_d0_is_clamped_before_texture_modulate2x_and_summed_lights():
    run_node("""
import assert from 'node:assert/strict';
import {retailLightingColors} from './web/retail-lighting.mjs';
const config={sourceVerified:true,mode:'source',emissive:[0,0,-.5],
 directionals:[{direction:[1,0,0],diffuse:[.8,.2,.1]},
 {direction:[1,0,0],diffuse:[.8,.2,.1]}]};
const result=retailLightingColors([.25,.25,.25],[1,0,0],config);
assert.deepEqual(Array.from(result),[1,Math.fround(.65),0]);
// A .25 texel under MODULATE2X yields .5 from the clamped red D0=1.
assert.equal(result[0]*2*.25,.5);
""")


def test_unsupported_lighting_retains_callers_existing_render():
    run_node("""
import assert from 'node:assert/strict';
import {retailLightingColors,validateRetailLightingConfig} from './web/retail-lighting.mjs';
const base={sourceVerified:true,mode:'source',emissive:[0,0,0],
 directionals:[{direction:[0,0,1],diffuse:[1,1,1]}]};
for(const config of [{...base,sourceVerified:false},{...base,mode:'guess'},
 {...base,emissive:[NaN,0,0]}, {...base,directionals:[{direction:[0,0,0],diffuse:[1,1,1]}]}]) {
 assert.equal(validateRetailLightingConfig(config),false);
 assert.equal(retailLightingColors([0,0,0],[0,0,1],config),null);
}
assert.equal(retailLightingColors([0,0,0],[],base),null);
assert.equal(retailLightingColors([0,0],[],base),null);
assert.equal(retailLightingColors([0,NaN,0],[0,0,1],base),null);
""")
