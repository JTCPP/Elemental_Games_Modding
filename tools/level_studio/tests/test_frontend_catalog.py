"""Keep playable level selection separate from native cinematic resources."""
from html.parser import HTMLParser
from pathlib import Path

from test_frontend_renderer import run_node


ROOT = Path(__file__).resolve().parents[1]
IMPORTS = """
import assert from 'node:assert/strict';
import {catalogSection,catalogEntries,catalogView} from './web/level-catalog.mjs';
const entries=[{id:'w1',family:'water'},{id:'d2',family:'death'},
 {id:'airship_landing',family:'cinematic'},{id:'diskreplace_1',group:'cinematic'},
 {id:'native_movie',family:'air',nativeKey:'movies/scenes/test'},
 {id:'my_level',family:'cinematic',custom:true,nativeKey:'levels/custom/my_level'}];
"""


def test_native_role_separates_cinematics_and_preserves_custom_gameplay_clones():
    run_node(IMPORTS + """
assert.equal(catalogSection({family:'cinematic',nativeKey:'levels/water/w1'}),'levels');
assert.equal(catalogSection({family:'water',nativeKey:'MOVIES\\\\SCENES\\\\ENDING'}),'cinematics');
assert.equal(catalogSection({custom:true,family:'cinematic'}),'levels');
assert.equal(catalogSection({id:'w1'}),'levels');
assert.deepEqual(catalogEntries(entries,'levels').map(entry=>entry.id),['w1','d2','my_level']);
assert.deepEqual(catalogEntries(entries,'cinematics').map(entry=>entry.id),['airship_landing','diskreplace_1','native_movie']);
assert.equal(catalogEntries([null,undefined,{},...entries],'levels').length,3);
assert.equal(catalogEntries(null,'levels').length,0);
assert.equal(entries.length,6);assert.equal(entries[5].family,'cinematic');
""")


def test_open_restore_and_catalog_refresh_choose_the_matching_section():
    run_node(IMPORTS + """
const initial=catalogView(entries,'levels');
assert.equal(initial.selectedId,'w1');assert.equal(initial.section,'levels');
assert.deepEqual(initial.counts,{levels:3,cinematics:3});
const movie=catalogView(entries,'levels','diskreplace_1');
assert.equal(movie.section,'cinematics');assert.equal(movie.selectedId,'diskreplace_1');
assert.equal(movie.entries.length,3);assert.ok(movie.entries.every(entry=>entry.family!=='water'));
const restored=catalogView(entries,'cinematics','d2');
assert.equal(restored.section,'levels');assert.equal(restored.selectedId,'d2');
const removed=catalogView(entries.filter(entry=>entry.id!=='d2'),'levels','d2');
assert.equal(removed.selectedId,'w1');assert.equal(removed.counts.levels,2);
assert.deepEqual(catalogView([],'cinematics','removed'),{section:'cinematics',entries:[],selectedId:null,counts:{levels:0,cinematics:0}});
assert.equal(catalogView(entries,'unknown').section,'levels');
""")


DOM = """
import fs from 'node:fs';
import {setLanguage,translate,translateLevelName,formatNumber} from './web/i18n.mjs';
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.attributes={};this.dataset={};this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};}
 replaceChildren(){this.children=[];this.value='';}
 append(child){this.children.push(child);}
 setAttribute(name,value){this.attributes[name]=value;}
 querySelector(){return this.count;}
}
const elements=Object.fromEntries(['levelSelect','levelSelectLabel','levelCount'].map(id=>[id,new Element()]));
const buttons=['levels','cinematics'].map(section=>{const element=new Element('button');element.dataset.catalogSection=section;element.count=new Element();return element;});
const document={createElement:tag=>new Element(tag),querySelectorAll:()=>buttons};
const $=id=>elements[id],number=value=>formatNumber(value);
const groupLabel=value=>translate(({water:'Domaine de l’Eau',death:'Domaine de la Mort',cinematic:'Cinématiques'})[value]||value);
const state={level:null,levels:entries,catalogSection:'levels',catalogLast:{}};
const source=fs.readFileSync('./web/app.js','utf8');
const actual=source.slice(source.indexOf('function renderLevelCatalog('),source.indexOf('async function refreshLevelCatalog('));
const loaded=[];
const loadLevel=async id=>{state.level=id;state.catalogLast[catalogSection(entries.find(entry=>entry.id===id))]=id;loaded.push(id);};
const ui=new Function('state','document','$','number','translate','translateLevelName','groupLabel','catalogView','catalogEntries','loadLevel',`${actual};return {render:renderLevelCatalog,options:renderLevelOptions,switch:switchCatalogSection};`)(state,document,$,number,translate,translateLevelName,groupLabel,catalogView,catalogEntries,loadLevel);
const optionIds=()=>elements.levelSelect.children.flatMap(group=>group.tag==='optgroup'?group.children:[group]).map(option=>option.value);
"""


def test_actual_catalog_controls_remember_selection_and_leave_custom_names_unchanged():
    run_node(IMPORTS + DOM + """
entries[5].name='Eau — W1';setLanguage('fr');
ui.render({levels:entries});
assert.deepEqual(optionIds(),['w1','d2','my_level']);
assert.equal(elements.levelCount.textContent,'3 NIVEAUX');
assert.equal(elements.levelSelectLabel.textContent,'Niveau actif');
assert.equal(buttons[0].attributes['aria-pressed'],'true');assert.equal(buttons[1].count.textContent,'3');
await loadLevel('d2');await ui.switch('cinematics');
assert.equal(loaded.at(-1),'airship_landing');assert.equal(state.catalogSection,'cinematics');
assert.deepEqual(optionIds(),['airship_landing','diskreplace_1','native_movie']);
assert.equal(elements.levelSelectLabel.textContent,'Cinématique active');
assert.equal(elements.levelCount.textContent,'3 CINÉMATIQUES');
await ui.switch('levels');assert.equal(loaded.at(-1),'d2');
state.loading=true;await ui.switch('cinematics');assert.equal(loaded.at(-1),'d2');state.loading=false;
state.level='diskreplace_1';ui.render({levels:entries});assert.equal(state.catalogSection,'cinematics');
ui.options('my_level');setLanguage('en');ui.options('my_level');
assert.equal(elements.levelSelectLabel.textContent,'Active level');assert.equal(elements.levelCount.textContent,'3 LEVELS');
const custom=elements.levelSelect.children.flatMap(group=>group.children).find(option=>option.value==='my_level');
assert.equal(custom.textContent,'Eau — W1');assert.ok('data-i18n-ignore' in custom.attributes);
ui.options('diskreplace_1');assert.equal(elements.levelCount.textContent,'3 CINEMATICS');assert.equal(elements.levelSelectLabel.textContent,'Active cinematic');
ui.render({levels:[]});assert.deepEqual(optionIds(),['']);assert.equal(elements.levelSelect.children[0].textContent,'No cinematics available.');
""")


def test_catalog_controls_have_accessible_labels_and_remain_outside_asset_tabs():
    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.sections = []
            self.group = None
            self.label = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get('id') == 'catalogSections':
                self.group = attrs
            if 'data-catalog-section' in attrs:
                assert tag == 'button'
                self.sections.append(attrs)
            if attrs.get('id') == 'levelSelectLabel':
                self.label = attrs

    controls = Controls()
    controls.feed((ROOT / 'web/index.html').read_text('utf-8'))
    assert controls.group['role'] == 'group'
    assert controls.group['aria-label'] == 'Type de scène'
    assert [button['data-catalog-section'] for button in controls.sections] == ['levels', 'cinematics']
    assert all(button['aria-controls'] == 'levelSelect' for button in controls.sections)
    assert all('data-tab' not in button for button in controls.sections)
    assert controls.label['for'] == 'levelSelect'
