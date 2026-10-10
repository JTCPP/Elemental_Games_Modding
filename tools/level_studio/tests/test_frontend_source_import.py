"""Exercise local import UI outcomes without uploading game data."""
from test_frontend_renderer import run_node


SETUP = """
import assert from 'node:assert/strict';
import {createSourceImport} from './web/source-import.mjs';
class Element {
 constructor(tag='div'){this.tagName=tag;this.textContent='';this.value='';this.children=[];this.listeners={};this.attributes={};this.closed=false;}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(key,value){this.attributes[key]=value;}
 removeAttribute(key){delete this[key];}
 addEventListener(type,callback){this.listeners[type]=callback;}
 click(){this.listeners.click?.();}
 close(){this.closed=true;}
}
const input=new Element('input'),dialog=new Element('dialog'),body=new Element();
const doc={getElementById(id){return id==='isoFileInput'?input:dialog;},createElement(tag){return new Element(tag);}};
const calls=[],busy=[],messages=[];let opened=0;let job={id:'123',status:'ready',name:'Azurik.iso'};
const api=async(path,payload)=>{calls.push([path,payload]);if(path==='/api/sources')return {originalAvailable:true,active:'original',imports:[]};if(path==='/api/import-iso')return job;return {};};
const importer=createSourceImport({api,openDialog:()=>body,onSourceOpened:async()=>{opened++;},translate:v=>v,formatNumber:v=>Math.round(v).toString(),setBusy:v=>busy.push(v),toast:(v,error)=>messages.push({v,error}),document:doc});
await importer.show();
const pathRow=body.children.find(row=>row.children.some(child=>child.id==='isoPathInput'));
const path=pathRow.children.find(child=>child.id==='isoPathInput'), start=pathRow.children.find(child=>child.tagName==='button');
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
"""


def test_import_opens_only_ready_job_and_preserves_local_path_literal():
    run_node(SETUP + """
path.value=' C:/Games/Azurik - Rise of Perathia.iso ';start.click();await settle();
assert.deepEqual(calls.find(row=>row[0]==='/api/import-iso')[1],{path:'C:/Games/Azurik - Rise of Perathia.iso'});
assert.deepEqual(calls.find(row=>row[0]==='/api/open-import')[1],{id:'123'});
assert.equal(opened,1);assert.equal(dialog.closed,true);assert.deepEqual(busy,[true,false]);assert.equal(importer.working,false);
""")


def test_invalid_iso_job_does_not_switch_existing_source():
    run_node(SETUP + """
job={id:'bad',status:'error',error:'Unrelated or truncated disc'};
path.value='C:/Other.iso';start.click();await settle();
assert.equal(calls.some(row=>row[0]==='/api/open-import'),false);assert.equal(opened,0);assert.equal(dialog.closed,false);
assert.equal(messages.at(-1).error,true);assert.deepEqual(busy,[true,false]);assert.equal(importer.working,false);
""")


def test_empty_path_and_invalid_file_are_rejected_before_any_upload():
    run_node(SETUP + """
path.value='  ';start.click();await settle();assert.deepEqual(busy,[]);
input.files=[{name:'Other.zip',size:100}];input.listeners.change();await settle();
assert.equal(calls.some(row=>row[0]==='/api/import-iso'),false);assert.equal(messages.at(-1).error,true);assert.equal(opened,0);
input.files=[{name:'Azurik.iso',size:17*1024**3}];input.listeners.change();await settle();assert.equal(importer.working,false);
""")


def test_browser_file_copy_uses_only_same_origin_endpoint_and_handles_rejection():
    run_node(SETUP + """
let request;
globalThis.XMLHttpRequest=class {
 constructor(){request=this;this.upload={};this.headers={};this.status=400;this.responseText=JSON.stringify({error:'Invalid ISO'});}
 open(method,url){this.method=method;this.url=url;}
 setRequestHeader(key,value){this.headers[key]=value;}
 send(file){this.file=file;this.onload();}
};
const file={name:'Azurik été.iso',size:2048};input.files=[file];input.listeners.change();await settle();
assert.equal(request.url,'/api/import-iso-upload');assert.equal(request.method,'POST');assert.equal(request.headers['Content-Type'],'application/octet-stream');
assert.equal(decodeURIComponent(request.headers['X-File-Name']),file.name);assert.equal(request.file,file);
assert.equal(opened,0);assert.equal(messages.at(-1).v,'Invalid ISO');assert.equal(importer.working,false);assert.deepEqual(busy,[true,false]);
""")
