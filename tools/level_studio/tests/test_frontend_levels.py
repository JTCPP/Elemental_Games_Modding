"""Exercise level-management dialogs and local ISO jobs without commercial game files."""
from test_frontend_renderer import run_node


SETUP = r"""
import assert from 'node:assert/strict';
import {createLevelManager,validateLevelDraft,LEVEL_FAMILIES} from './web/levels.js';
class Element {
 constructor(tag,ownerDocument){this.tagName=tag;this.ownerDocument=ownerDocument;this.children=[];this.listeners={};this.attributes={};this.value='';this.textContent='';this.disabled=false;this.isConnected=true;this.open=true;}
 append(...elements){for(const child of elements){child.parentElement=this;this.children.push(child);}}
 replaceChildren(...elements){this.children=[];this.append(...elements);}
 setAttribute(key,value){this.attributes[key]=value;}
 removeAttribute(key){delete this.attributes[key];}
 addEventListener(type,callback){(this.listeners[type]??=[]).push(callback);}
 closest(tag){for(let element=this;element;element=element.parentElement)if(element.tagName===tag)return element;return null;}
 async emit(type){return Promise.all((this.listeners[type]||[]).map(callback=>callback({preventDefault(){}})));}
 async click(){if(!this.disabled)return this.emit('click');}
}
const doc={createElement(tag){return new Element(tag,doc);}};
const dialog=doc.createElement('dialog'),body=doc.createElement('div');dialog.append(body);
const calls=[],messages=[],changes=[],refreshed=[],titles=[],translations=[];
let closed=0,catalog={levels:[{id:'w1',name:'Water W1',family:'water'},{id:'a5',name:'Air A5',family:'air'},{id:'custom_one',name:'Custom one',family:'life',custom:true}],levelManagement:{deleted:[],canUndo:false,canRedo:false}};
let context={level:'w1'},handler=async(path,payload)=>path==='/api/catalog'?catalog:{activeLevel:payload?.id||payload?.replacement||'w1'};
const api=async(path,payload)=>{calls.push([path,payload]);return handler(path,payload);};
const factory=(overrides={})=>createLevelManager({api,
 openDialog(title,eyebrow){titles.push([title,eyebrow]);dialog.open=true;body.replaceChildren();return body;},
 closeDialog(){closed++;dialog.open=false;},toast:(value,error)=>messages.push({value,error}),getContext:()=>context,
 translate:value=>{translations.push(value);return value;},refreshCatalog:async result=>refreshed.push(result),
 onChanged:async(result,operation)=>changes.push({result,operation}),...overrides});
let manager=factory();
const nodes=(root=body)=>[root,...root.children.flatMap(child=>nodes(child))];
const field=id=>nodes().find(element=>element.id===id);
const button=text=>nodes().find(element=>element.tagName==='button'&&element.textContent===text);
const text=()=>nodes().map(element=>element.textContent).join('\n');
const submit=()=>nodes().find(element=>element.tagName==='form').emit('submit');
const settle=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};
"""


def test_level_id_name_template_and_family_boundaries_match_native_namespace():
    run_node(SETUP + r"""
const draft={template:'w1',id:' new_level_2 ',name:' Mon niveau ',family:'life'};
assert.deepEqual(validateLevelDraft(draft,catalog.levels),{template:'w1',id:'new_level_2',name:'Mon niveau',family:'life'});
for(const id of ['','W1','1new','a-b','../level','always','default','con','lpt9','com1','a'.repeat(41),'a\0b'])assert.throws(()=>validateLevelDraft({...draft,id},catalog.levels));
assert.equal(validateLevelDraft({...draft,id:'a'.repeat(40),name:'a'.repeat(80)},catalog.levels).id.length,40);
for(const name of ['  ','a'.repeat(81),'name\nsecond','name\0'])assert.throws(()=>validateLevelDraft({...draft,name},catalog.levels));
for(const change of [{template:'missing'},{id:'a5'},{family:'unknown'}])assert.throws(()=>validateLevelDraft({...draft,...change},catalog.levels));
assert.deepEqual(LEVEL_FAMILIES.map(([id])=>id),['air','water','earth','fire','death','life','perathia','cinematic']);
""")


def test_create_uses_original_template_and_one_post_with_raw_user_name():
    run_node(SETUP + r"""
await manager.showCreate();assert.equal(field('levelTemplate').value,'w1');assert.equal(field('newLevelFamily').value,'water');
assert.match(text(),/les modifications actuelles du projet ne sont pas copiées/);
for(const [,label] of LEVEL_FAMILIES)assert.ok(translations.includes(label));
field('levelTemplate').value='a5';await field('levelTemplate').emit('change');assert.equal(field('newLevelFamily').value,'air');
field('newLevelId').value='new_water';field('newLevelName').value='<script>My level</script>';field('newLevelFamily').value='life';
let resolve;handler=async(path,payload)=>path==='/api/catalog'?catalog:new Promise(done=>resolve=done);
const first=submit();await settle();assert.equal(button('Créer le niveau').disabled,true);await submit();
assert.equal(calls.filter(([path])=>path==='/api/levels/create').length,1);
resolve({activeLevel:'new_water'});await first;
assert.deepEqual(calls.find(([path])=>path==='/api/levels/create')[1],{template:'a5',id:'new_water',name:'<script>My level</script>',family:'life'});
assert.equal(closed,1);assert.equal(changes.length,1);assert.equal(refreshed.length,0);assert.equal(manager.working,false);
assert.deepEqual(changes[0].operation,{action:'create',level:'new_water'});
""")


def test_invalid_and_deleted_ids_do_not_create_and_api_failure_can_be_retried():
    run_node(SETUP + r"""
catalog.levelManagement.deleted=[{id:'removed'}];await manager.showCreate();
field('newLevelId').value='removed';field('newLevelName').value='Removed';await submit();
field('newLevelId').value='W1';await submit();assert.equal(calls.some(([path])=>path==='/api/levels/create'),false);
field('newLevelId').value='new_level';handler=async()=>{throw new Error('<img src=x onerror=alert(1)> failed');};await submit();
assert.equal(dialog.open,true);assert.equal(changes.length,0);assert.equal(button('Créer le niveau').disabled,false);
assert.match(text(),/<img src=x onerror=alert\(1\)> failed/);assert.equal(nodes().some(element=>element.tagName==='img'),false);
handler=async()=>({activeLevel:'new_level'});await submit();assert.equal(changes.length,1);assert.equal(manager.working,false);
""")


def test_delete_excludes_self_requires_retained_destination_and_preserves_source_note():
    run_node(SETUP + r"""
for(const id of ['w1','custom_one']){
 await manager.showDelete(id);
 assert.equal(field('replacementLevel').children.some(option=>option.value===id),false);
 assert.match(text(),/fichiers source restent intacts/);assert.match(text(),/entrées seront redirigées/);
 field('replacementLevel').value=id;await submit();assert.equal(calls.filter(([path])=>path==='/api/levels/delete').length,id==='w1'?0:1);
 field('replacementLevel').value=id==='w1'?'a5':'w1';await submit();
 assert.deepEqual(calls.filter(([path])=>path==='/api/levels/delete').at(-1)[1],{level:id,replacement:id==='w1'?'a5':'w1'});
}
catalog.levels=[catalog.levels[0]];await manager.showDelete('w1');assert.match(text(),/dernier niveau/);
assert.equal(field('replacementLevel'),undefined);assert.equal(button('Supprimer du mod'),undefined);
""")


def test_manager_restores_deleted_levels_and_undo_redo_have_independent_endpoints():
    run_node(SETUP + r"""
catalog.levelManagement={deleted:[{id:'old_level',name:'<img src=x>'}],canUndo:true,canRedo:false};
await manager.showManage();assert.equal(button('Rétablir une opération de niveau').disabled,true);
assert.match(text(),/<img src=x>/);assert.equal(nodes().some(element=>element.tagName==='img'),false);
await button('Restaurer le niveau supprimé').click();
assert.deepEqual(calls.find(([path])=>path==='/api/levels/restore')[1],{level:'old_level'});
await button('Annuler une opération de niveau').click();assert.deepEqual(calls.find(([path])=>path==='/api/levels/undo')[1],{});
catalog.levelManagement.canRedo=true;await manager.showManage();await button('Rétablir une opération de niveau').click();
assert.deepEqual(calls.find(([path])=>path==='/api/levels/redo')[1],{});assert.equal(changes.length,3);assert.equal(dialog.open,true);
""")


def test_empty_catalog_and_stale_catalog_response_do_not_replace_a_new_dialog():
    run_node(SETUP + r"""
catalog.levels=[];await manager.showCreate();assert.match(text(),/Importez votre jeu/);assert.equal(field('newLevelId'),undefined);
catalog.levels=[{id:'w1',name:'Water W1',family:'water'},{id:'a5',name:'Air A5',family:'air'}];
let resolve;let reads=0;handler=async()=>++reads===1?new Promise(done=>resolve=done):catalog;
const first=manager.showCreate();await manager.showDelete('w1');resolve(catalog);await first;
assert.ok(field('replacementLevel'));assert.equal(field('newLevelId'),undefined);
manager=factory({onChanged:undefined});handler=async(path,payload)=>path==='/api/catalog'?catalog:{activeLevel:'a5'};
await manager.showDelete('w1');await submit();assert.equal(refreshed.length,1);assert.equal(changes.length,0);
""")


def test_iso_build_preserves_literal_local_paths_and_reports_verified_output_without_runtime_claim():
    run_node(SETUP + r"""
context={exportDirectory:'C:\\Mods\\my export',sourceIsoPath:'C:\\Games\\Azurik été.xiso'};
await manager.showBuildIso();assert.equal(field('isoOutputPath').value,'C:\\Games\\Azurik été_modded.iso');
field('isoOutputPath').value='C:\\Tests\\<new> game.iso';
handler=async()=>({id:'job',status:'ready',output:'C:\\Tests\\<new> game.iso',progress:1});
await submit();assert.deepEqual(calls.find(([path])=>path==='/api/build-iso')[1],{directory:'C:\\Mods\\my export',input:'C:\\Games\\Azurik été.xiso',output:'C:\\Tests\\<new> game.iso'});
assert.equal(dialog.open,true);assert.match(text(),/vérifiez le mod dans le jeu/);assert.match(text(),/<new> game.iso/);
assert.equal(nodes().find(element=>element.tagName==='progress').value,1);assert.equal(changes.length,0);
assert.equal(nodes().find(element=>element.tagName==='code').attributes['data-i18n-ignore'],'');
""")


def test_iso_build_rejects_missing_paths_same_source_output_and_repeated_submissions():
    run_node(SETUP + r"""
await manager.showBuildIso();await submit();assert.equal(calls.length,0);
field('isoExportDirectory').value='C:/mods';field('isoSourcePath').value='C:\\Games\\source.iso';field('isoOutputPath').value='c:/games/SOURCE.ISO';
await submit();assert.equal(calls.length,0);assert.match(text(),/chemin différent/);
field('isoOutputPath').value='C:/modded.iso';let resolve;handler=async()=>new Promise(done=>resolve=done);
const first=submit();await settle();assert.equal(manager.working,true);await submit();assert.equal(calls.length,1);
resolve({status:'ready',output:'C:/modded.iso'});await first;assert.equal(manager.working,false);
assert.equal(button('Construire la nouvelle ISO').disabled,false);
""")


def test_build_polling_uses_encoded_id_stops_when_closed_and_resumes_job_without_rebuilding():
    run_node(SETUP + r"""
globalThis.setTimeout=callback=>{queueMicrotask(callback);return 1;};
context={exportDirectory:'C:/mods',sourceIsoPath:'C:/source.iso'};
await manager.showBuildIso();
handler=async(path,payload)=>{if(payload){dialog.open=false;return {id:'job /?é',status:'queued',progress:0};}return {id:'job /?é',status:'ready',output:'C:/built.iso'};};
await submit();assert.equal(calls.length,1);assert.equal(manager.working,false);
await manager.showBuildIso();assert.equal(calls.filter(([,payload])=>payload!==undefined).length,1);
assert.equal(calls.at(-1)[0],'/api/build-iso?id=job%20%2F%3F%C3%A9');assert.match(text(),/C:\/built.iso/);
// Closing during the wait must also stop before the next status request.
await manager.showBuildIso();handler=async()=>({id:'other',status:'building',progress:.4});
globalThis.setTimeout=callback=>{dialog.open=false;queueMicrotask(callback);return 1;};
const before=calls.length;await submit();assert.equal(calls.length,before+1);assert.equal(manager.working,false);
""")


def test_iso_job_failure_is_visible_and_never_claims_a_successful_build():
    run_node(SETUP + r"""
globalThis.setTimeout=callback=>{queueMicrotask(callback);return 1;};
context={exportDirectory:'C:/mods',sourceIsoPath:'C:/source.iso'};await manager.showBuildIso();
let reads=0;handler=async()=>++reads===1?{id:'bad',status:'running',progress:.5}:{id:'bad',status:'error',error:'Output exists'};
await submit();assert.match(text(),/Output exists/);assert.equal(messages.at(-1).error,true);
assert.equal(nodes().find(element=>element.tagName==='code').hidden,true);
assert.equal(manager.working,false);assert.equal(button('Construire la nouvelle ISO').disabled,false);
""")


def test_immediate_reopen_resumes_build_and_old_watcher_cannot_unlock_new_operation():
    run_node(SETUP + r"""
const waiting=[];globalThis.setTimeout=callback=>{waiting.push(callback);return waiting.length;};
context={exportDirectory:'C:/mods',sourceIsoPath:'C:/source.iso'};await manager.showBuildIso();
handler=async()=>({id:'job',status:'building',progress:.2});const first=submit();await settle();
dialog.open=false;const reopened=manager.showBuildIso();await settle();assert.equal(waiting.length,2);
waiting.shift()();await first;assert.equal(manager.working,true);assert.equal(button('Construire la nouvelle ISO').disabled,true);
handler=async()=>({id:'job',status:'ready',output:'C:/modded.iso'});waiting.shift()();await reopened;
assert.equal(manager.working,false);assert.equal(calls.filter(([,payload])=>payload!==undefined).length,1);
assert.match(text(),/C:\/modded.iso/);
""")


def test_malformed_build_job_never_polls_forever_or_claims_success():
    run_node(SETUP + r"""
context={exportDirectory:'C:/mods',sourceIsoPath:'C:/source.iso'};await manager.showBuildIso();
for(const job of [{},{status:'unknown'},{status:'queued'}]){
 handler=async()=>job;const before=calls.length;await submit();
 assert.equal(calls.length,before+1);assert.equal(manager.working,false);
 assert.equal(nodes().find(element=>element.tagName==='code').hidden,true);
 assert.equal(messages.at(-1).error,true);
}
""")


def test_delete_only_offers_same_template_entry_points_and_missing_clone_guidance():
    run_node(SETUP + r"""
catalog.levels=[{id:'w1',templateOrigin:'w1'},{id:'a5',templateOrigin:'a5'},{id:'my_water',templateOrigin:'w1',custom:true}];
await manager.showDelete('w1');assert.deepEqual(field('replacementLevel').children.map(option=>option.value),['my_water']);
field('replacementLevel').value='a5';await submit();assert.equal(calls.some(([path])=>path==='/api/levels/delete'),false);
await manager.showDelete('a5');assert.equal(field('replacementLevel'),undefined);assert.match(text(),/conserver les points d’entrée/);
""")


def test_iso_progress_uses_backend_percentage_scale_including_first_percent():
    run_node(SETUP + r"""
const waiting=[];globalThis.setTimeout=callback=>{waiting.push(callback);return waiting.length;};
context={exportDirectory:'C:/mods',sourceIsoPath:'C:/source.iso'};await manager.showBuildIso();
handler=async()=>({id:'job',status:'running',progress:1});const first=submit();await settle();
assert.equal(nodes().find(element=>element.tagName==='progress').value,.01);
dialog.open=false;waiting.shift()();await first;
""")


def test_manager_and_templates_show_only_gameplay_but_validate_against_full_catalog():
    run_node(SETUP + r"""
catalog.levels.push({id:'movie_scene',name:'Cinematic unique title',family:'cinematic',canClone:false},
 {id:'native_scene',name:'Another movie title',family:'air',nativeKey:'movies/scenes/intro'},
 {id:'custom_cinematic_family',name:'Playable custom level',family:'cinematic',custom:true});
catalog.levelManagement={deleted:[{id:'removed_level',name:'Restorable level'}],canUndo:true,canRedo:true};
await manager.showManage();
assert.doesNotMatch(text(),/Cinematic unique title|Another movie title/);
assert.match(text(),/Playable custom level|Restorable level/);
assert.equal(button('Annuler une opération de niveau').disabled,false);
assert.equal(button('Restaurer le niveau supprimé').disabled,false);
await manager.showCreate();
assert.deepEqual(field('levelTemplate').children.map(option=>option.value),['w1','a5','custom_one','custom_cinematic_family']);
field('newLevelId').value='movie_scene';field('newLevelName').value='My new level';
await submit();assert.equal(calls.some(([path])=>path==='/api/levels/create'),false);
assert.match(text(),/Cet identifiant de niveau existe déjà/);
catalog.levels=catalog.levels.filter(level=>level.family==='cinematic'&&!level.custom);
await manager.showManage();assert.equal(button('Créer un niveau depuis un modèle').disabled,true);
""")
