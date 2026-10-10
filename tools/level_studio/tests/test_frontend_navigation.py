"""Exercise the free camera with real Three.js vectors and browser input rules."""
from test_frontend_renderer import run_node


SETUP = """
import assert from 'node:assert/strict';
import * as THREE from './web/vendor/three.module.js';
import {createFreeNavigation, navigationKeys, navigationSpeed} from './web/navigation.mjs';
class Surface {
 constructor(tagName='DIV') { this.tagName=tagName;this.style={};this.listeners=new Map();this.captures=new Set(); }
 addEventListener(type,callback) { const list=this.listeners.get(type)||[];list.push(callback);this.listeners.set(type,list); }
 removeEventListener(type,callback) { this.listeners.set(type,(this.listeners.get(type)||[]).filter(other=>other!==callback)); }
 dispatch(type,event={}) { for(const callback of [...(this.listeners.get(type)||[])])callback(event); }
 contains(other) { return other===this||other===canvas; }
 focus() { documentTarget.activeElement=this; }
 closest() { return null; }
 setPointerCapture(id) { this.captures.add(id); }
 hasPointerCapture(id) { return this.captures.has(id); }
 releasePointerCapture(id) { this.captures.delete(id); }
}
const documentTarget=new Surface(), windowTarget=new Surface();
const viewport=new Surface(), canvas=new Surface('CANVAS');
documentTarget.activeElement=viewport;
const camera=new THREE.PerspectiveCamera();camera.up.set(0,0,1);camera.position.set(10,20,30);camera.lookAt(10,21,30);camera.updateMatrixWorld();
const orbit={target:new THREE.Vector3(10,21,30),enabled:true,enableDamping:true,update(){}};
let locale='fr',speed=1,radius=100,blocked=false;
const navigation=createFreeNavigation({THREE,camera,orbit,viewport,element:canvas,getLocale:()=>locale,getSpeed:()=>speed,getRadius:()=>radius,getBlocked:()=>blocked,windowTarget,documentTarget});
const event=(key,extra={})=>({key,target:viewport,prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...extra});
const direction=()=>camera.getWorldDirection(new THREE.Vector3());
const approximately=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-9,`${actual} != ${expected}`);
const move=(key,dt=.05)=>{navigation.handleKeyDown(event(key));navigation.update(dt);navigation.handleKeyUp(event(key));};
navigation.enable(true);
"""


def test_french_and_english_keys_follow_labels_instead_of_physical_codes():
    run_node(SETUP + """
assert.deepEqual(navigationKeys('fr'),['z','q','s','d']);assert.deepEqual(navigationKeys('en'),['w','q','s','d']);
const origin=camera.position.clone();const forward=event('Z',{code:'KeyW'});
assert.equal(navigation.handleKeyDown(forward),true);assert.equal(forward.prevented,true);assert.equal(forward.stopped,true);
navigation.update(.05);navigation.handleKeyUp(forward);approximately(camera.position.y-origin.y,.6);
move('q');approximately(camera.position.x-origin.x,-.6);move('d');approximately(camera.position.x,origin.x);
move('s');assert.ok(camera.position.distanceTo(origin)<1e-9);
locale='en';navigation.update(0);move('z');assert.ok(camera.position.distanceTo(origin)<1e-9);
move('w');approximately(camera.position.y-origin.y,.6);move('q');approximately(camera.position.x-origin.x,-.6);
move('a');approximately(camera.position.x-origin.x,-.6);
""")


def test_looking_rotates_through_full_turn_at_fixed_position_and_reaches_both_poles():
    run_node(SETUP + """
const position=camera.position.clone(), initial=direction();
assert.equal(navigation.beginLook(event('',{button:2,pointerId:7,clientX:3000,clientY:3000})),true);
navigation.dragLook(event('',{pointerId:7,clientX:3000-Math.PI/.003,clientY:3000}));
assert.ok(direction().distanceTo(initial.clone().negate())<1e-9);assert.ok(camera.position.distanceTo(position)<1e-12);
navigation.dragLook(event('',{pointerId:7,clientX:3000-2*Math.PI/.003,clientY:3000}));
assert.ok(direction().distanceTo(initial)<1e-9);assert.ok(camera.position.distanceTo(position)<1e-12);
navigation.dragLook(event('',{pointerId:7,clientX:0,clientY:-10000}));assert.ok(direction().z>.999999);
navigation.dragLook(event('',{pointerId:7,clientX:0,clientY:10000}));assert.ok(direction().z<-.999999);
assert.ok(camera.quaternion.toArray().every(Number.isFinite));assert.ok(camera.position.distanceTo(position)<1e-12);
assert.equal(navigation.looking,true);assert.equal(canvas.hasPointerCapture(7),true);
navigation.endLook({pointerId:7});assert.equal(navigation.looking,false);assert.equal(canvas.hasPointerCapture(7),false);assert.equal(orbit.enabled,false);
""")


def test_speed_dropdown_modifiers_diagonals_and_stalled_frames_have_predictable_distances():
    run_node(SETUP + """
assert.equal(navigationSpeed('slow'),.2);assert.equal(navigationSpeed('normal'),1);assert.equal(navigationSpeed('fast'),5);assert.equal(navigationSpeed('5'),5);assert.equal(navigationSpeed(NaN),1);
let origin=camera.position.clone();speed=.2;move('z');approximately(camera.position.distanceTo(origin),.12);
origin=camera.position.clone();speed=5;move('z');approximately(camera.position.distanceTo(origin),3);
speed=1;origin=camera.position.clone();navigation.handleKeyDown(event('Shift'));move('z');navigation.handleKeyUp(event('Shift'));approximately(camera.position.distanceTo(origin),3);
origin=camera.position.clone();navigation.handleKeyDown(event('Alt'));move('z');navigation.handleKeyUp(event('Alt'));approximately(camera.position.distanceTo(origin),.12);
origin=camera.position.clone();navigation.handleKeyDown(event('z'));navigation.handleKeyDown(event('d'));navigation.update(.05);navigation.reset();approximately(camera.position.distanceTo(origin),.6);
origin=camera.position.clone();move('z',20);approximately(camera.position.distanceTo(origin),.6);
origin=camera.position.clone();move('z',-1);assert.ok(camera.position.distanceTo(origin)<1e-12);
""")


def test_text_fields_dialogs_toolbar_buttons_and_nonviewport_focus_never_move_camera():
    run_node(SETUP + """
const origin=camera.position.clone();
for(const control of [new Surface('INPUT'),new Surface('SELECT'),new Surface('TEXTAREA'),new Surface('BUTTON'),Object.assign(new Surface(),{isContentEditable:true}),new Surface()]) {
 documentTarget.activeElement=control;assert.equal(navigation.handleKeyDown(event('z',{target:control})),false);navigation.update(.05);
}
documentTarget.activeElement=viewport;blocked=true;assert.equal(navigation.handleKeyDown(event('z')),false);navigation.update(.05);
blocked=false;assert.equal(navigation.handleKeyDown(event('z',{target:new Surface('INPUT')})),false);navigation.update(.05);
assert.ok(camera.position.distanceTo(origin)<1e-12);
""")


def test_blur_focus_loss_visibility_and_language_changes_clear_held_input_and_drag():
    run_node(SETUP + """
const origin=camera.position.clone();
navigation.handleKeyDown(event('z'));windowTarget.dispatch('blur');navigation.update(.05);assert.ok(camera.position.distanceTo(origin)<1e-12);
navigation.handleKeyDown(event('z'));locale='en';navigation.update(.05);assert.ok(camera.position.distanceTo(origin)<1e-12);
navigation.handleKeyDown(event('w'));windowTarget.dispatch('languagechange');navigation.update(.05);assert.ok(camera.position.distanceTo(origin)<1e-12);
navigation.handleKeyDown(event('w'));documentTarget.hidden=true;documentTarget.dispatch('visibilitychange');navigation.update(.05);assert.ok(camera.position.distanceTo(origin)<1e-12);
documentTarget.hidden=false;navigation.handleKeyDown(event('w'));documentTarget.activeElement=new Surface('INPUT');documentTarget.dispatch('focusin');documentTarget.activeElement=viewport;navigation.update(.05);assert.ok(camera.position.distanceTo(origin)<1e-12);
navigation.beginLook(event('',{button:2,pointerId:2,clientX:0,clientY:0}));windowTarget.dispatch('blur');assert.equal(navigation.looking,false);assert.equal(canvas.hasPointerCapture(2),false);
""")


def test_save_and_undo_shortcuts_remain_available_and_vertical_motion_is_z_up():
    run_node(SETUP + """
for(const shortcut of [event('s',{ctrlKey:true}),event('z',{ctrlKey:true}),event('w',{metaKey:true})]) {assert.equal(navigation.handleKeyDown(shortcut),false);assert.equal(shortcut.prevented,false);}
const origin=camera.position.clone();move(' ');approximately(camera.position.z-origin.z,.6);
move('Control');assert.ok(camera.position.distanceTo(origin)<1e-9);
assert.equal(navigation.handleKeyDown(event('a')),false);assert.equal(navigation.handleKeyDown(event('ArrowUp')),true);navigation.reset();
""")


def test_mode_switch_and_cleanup_preserve_pose_and_release_all_listeners():
    run_node(SETUP + """
const position=camera.position.clone(), quaternion=camera.quaternion.clone(), target=orbit.target.clone();
orbit.update=()=>{camera.position.addScalar(100);camera.lookAt(0,0,0);orbit.target.set(1,2,3);};
navigation.enable(false);assert.equal(orbit.enabled,true);assert.ok(camera.position.distanceTo(position)<1e-12);assert.ok(camera.quaternion.angleTo(quaternion)<1e-7);assert.ok(orbit.target.distanceTo(target)<1e-12);
navigation.enable(true);assert.equal(orbit.enabled,false);assert.ok(camera.position.distanceTo(position)<1e-12);
navigation.handleKeyDown(event('z'));navigation.beginLook(event('',{button:2,pointerId:2,clientX:0,clientY:0}));navigation.dispose();
assert.equal(navigation.enabled,false);assert.equal(navigation.looking,false);assert.equal(orbit.enabled,true);assert.equal(canvas.captures.size,0);
for(const surface of [windowTarget,documentTarget,canvas])for(const callbacks of surface.listeners.values())assert.equal(callbacks.length,0);
navigation.enable(true);navigation.update(.05);assert.ok(camera.position.distanceTo(position)<1e-12);assert.equal(navigation.enabled,false);
""")


def test_right_strafe_remains_stable_near_vertical_view_and_moves_target_with_eye():
    run_node(SETUP + """
navigation.beginLook(event('',{button:2,pointerId:1,clientX:0,clientY:0}));
navigation.dragLook(event('',{pointerId:1,clientX:0,clientY:-10000}));navigation.endLook({pointerId:1});
const position=camera.position.clone(),target=orbit.target.clone(),eyeDirection=direction();move('d');
approximately(camera.position.x-position.x,.6);approximately(camera.position.y-position.y,0);approximately(camera.position.z-position.z,0);
assert.ok(orbit.target.clone().sub(target).distanceTo(camera.position.clone().sub(position))<1e-12);assert.ok(direction().distanceTo(eyeDirection)<1e-12);
""")


def test_gizmo_or_pending_mutation_blocks_camera_and_does_not_resume_stale_input():
    run_node(SETUP + """
navigation.handleKeyDown(event('z'));
navigation.beginLook(event('',{button:2,pointerId:1,clientX:0,clientY:0}));
const position=camera.position.clone(),orientation=direction();
blocked=true;navigation.update(.05);
assert.ok(camera.position.distanceTo(position)<1e-12);assert.ok(direction().distanceTo(orientation)<1e-12);
assert.equal(navigation.looking,false);assert.equal(canvas.captures.size,0);assert.equal(orbit.enabled,false);
assert.equal(navigation.beginLook(event('',{button:2,pointerId:1,clientX:0,clientY:0})),false);
blocked=false;navigation.update(.05);assert.ok(camera.position.distanceTo(position)<1e-12);
move('z');approximately(camera.position.distanceTo(position),.6);
""")


def test_keyboard_look_exceeds_full_turn_without_moving_eye_or_changing_target_distance():
    run_node(SETUP + """
const position=camera.position.clone(),distance=camera.position.distanceTo(orbit.target);
const arrow=event('ArrowLeft');assert.equal(navigation.handleKeyDown(arrow),true);assert.equal(arrow.prevented,true);
for(let index=0;index<130;index++)navigation.update(.05);
navigation.handleKeyUp(event('ArrowLeft'));
const angle=130*.05*1.2;
assert.ok(angle>2*Math.PI);assert.ok(direction().distanceTo(new THREE.Vector3(-Math.sin(angle),Math.cos(angle),0))<1e-9);
assert.ok(camera.position.distanceTo(position)<1e-12);approximately(camera.position.distanceTo(orbit.target),distance);
navigation.handleKeyDown(event('ArrowRight'));for(let index=0;index<130;index++)navigation.update(.05);navigation.handleKeyUp(event('ArrowRight'));
assert.ok(direction().distanceTo(new THREE.Vector3(0,1,0))<1e-9);assert.ok(camera.position.distanceTo(position)<1e-12);
""")


def test_keyboard_look_up_down_have_correct_sign_pole_limits_and_precision_modifiers():
    run_node(SETUP + """
const position=camera.position.clone();
move('ArrowUp');approximately(direction().z,Math.sin(.06));move('ArrowDown');approximately(direction().z,0);
navigation.handleKeyDown(event('Alt'));move('ArrowUp');navigation.handleKeyUp(event('Alt'));approximately(direction().z,Math.sin(.012));
navigation.handleKeyDown(event('Shift'));move('ArrowUp');navigation.handleKeyUp(event('Shift'));approximately(direction().z,Math.sin(.312));
speed=100;move('ArrowUp');approximately(direction().z,Math.sin(.372));
navigation.handleKeyDown(event('ArrowUp'));for(let index=0;index<100;index++)navigation.update(.05);navigation.handleKeyUp(event('ArrowUp'));assert.ok(direction().z>.999999);
navigation.handleKeyDown(event('ArrowDown'));for(let index=0;index<100;index++)navigation.update(.05);navigation.handleKeyUp(event('ArrowDown'));assert.ok(direction().z<-.999999);
assert.ok(camera.position.distanceTo(position)<1e-12);assert.ok(camera.quaternion.toArray().every(Number.isFinite));
""")


def test_keyboard_look_leaves_text_controls_and_orbit_mode_untouched():
    run_node(SETUP + """
const position=camera.position.clone(),orientation=direction();
for(const control of [new Surface('INPUT'),new Surface('SELECT'),new Surface('TEXTAREA'),Object.assign(new Surface(),{isContentEditable:true}),new Surface()]) {
 documentTarget.activeElement=control;const arrow=event('ArrowUp',{target:control});assert.equal(navigation.handleKeyDown(arrow),false);assert.equal(arrow.prevented,false);navigation.update(.05);
}
documentTarget.activeElement=viewport;
assert.equal(navigation.handleKeyDown(event('ArrowLeft',{ctrlKey:true})),false);
navigation.enable(false);assert.equal(navigation.handleKeyDown(event('ArrowLeft')),false);navigation.update(.05);
assert.ok(camera.position.distanceTo(position)<1e-12);assert.ok(direction().distanceTo(orientation)<1e-12);
""")


def test_webview_body_focus_and_physical_azerty_equivalent_move_without_entering_text_fields():
    run_node(SETUP + """
const origin=camera.position.clone();documentTarget.body=new Surface('BODY');documentTarget.activeElement=documentTarget.body;
assert.equal(navigation.handleKeyDown(event('w',{code:'KeyW',target:documentTarget.body})),true);navigation.update(.05);navigation.handleKeyUp(event('w',{code:'KeyW'}));approximately(camera.position.y-origin.y,.6);
documentTarget.activeElement=new Surface('INPUT');assert.equal(navigation.handleKeyDown(event('w',{code:'KeyW'})),false);navigation.update(.05);approximately(camera.position.y-origin.y,.6);
documentTarget.activeElement=viewport;const previous=camera.position.clone();move('a');assert.ok(camera.position.distanceTo(previous)<1e-12);
move('a');assert.equal(navigation.handleKeyDown(event('a',{code:'KeyA'})),true);navigation.update(.05);navigation.handleKeyUp(event('a',{code:'KeyA'}));approximately(camera.position.x-previous.x,-.6);
""")


def test_right_drag_from_orbit_mode_enters_free_look_and_blocks_other_canvas_handlers():
    run_node(SETUP + """
navigation.enable(false);assert.equal(orbit.enabled,true);const position=camera.position.clone();let stopped=0;
const down=event('',{button:2,pointerId:4,clientX:0,clientY:0,stopImmediatePropagation(){stopped++;}});
assert.equal(navigation.beginLook(down),true);assert.equal(navigation.enabled,true);assert.equal(orbit.enabled,false);assert.equal(documentTarget.activeElement,canvas);assert.equal(stopped,1);
navigation.dragLook(event('',{pointerId:4,clientX:100,clientY:100,stopImmediatePropagation(){stopped++;}}));assert.equal(stopped,2);assert.ok(camera.position.distanceTo(position)<1e-12);
navigation.endLook({pointerId:4});move('z');assert.ok(camera.position.distanceTo(position)>.5);
""")
