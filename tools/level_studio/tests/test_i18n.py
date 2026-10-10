"""Language switching must preserve game identifiers and editor state."""
from html.parser import HTMLParser
from pathlib import Path

from test_frontend_renderer import run_node


ROOT = Path(__file__).resolve().parents[1]
IMPORTS = """
import assert from 'node:assert/strict';
import { initLanguage, setLanguage, getLanguage, getLocale, onLanguageChange,
  applyLanguage, translate, translateLevelName, formatNumber, formatSize,
  languageStorageKey } from './web/i18n.mjs';
"""


def test_real_messages_counts_and_source_names_survive_translation():
    run_node(IMPORTS + """
setLanguage('en');
assert.equal(translate('Enregistrer'),'Save');
assert.equal(translate('34 NIVEAUX'),'34 LEVELS');
assert.equal(translate('102 NIVEAUX'),'102 LEVELS');
assert.equal(translate('11 modifications du jeu'),'11 game changes');
assert.equal(translate('1 modification du jeu'),'1 game change');
assert.equal(translate('11 modifications du jeu · 2 modifications d’aperçu'),'11 game changes · 2 preview changes');
assert.equal(translate('Air — A5 ouvert · 1,193 géométries · 168 repères d’entités'),'Air — A5 open · 1,193 geometries · 168 entity markers');
assert.equal(translate('Position modifiée · Landing_Pad:armShape1'),'Position changed · Landing_Pad:armShape1');
assert.equal(translate('import.progress',{percent:25}),'Importing ISO · 25%');
assert.equal(translate('lock.count',{count:12}),'12 elements locked');
assert.equal(translate('Ciel · Tous les éléments'),'Sky · All elements');
assert.equal(translate('Flèches : regarder sur place'),'Arrow keys: look from your position');
assert.equal(translate('Position de la caméra'),'Camera position');
assert.equal(translate('Vue'),'View');
assert.equal(translate('Déplacement refusé : Cet objet est verrouillé. Déverrouillez-le avant de le déplacer.'),'Movement refused: This object is locked. Unlock it before moving it.');
assert.equal(translate('Dossier gamedata introuvable : C:/Game/Nuit/gamedata'),'Gamedata folder not found: C:/Game/Nuit/gamedata');
assert.equal(translate('7 textures cubiques : les six faces sont extraites ; leur orientation dans le jeu et les reflets Xbox ne sont pas reproduits.'),'7 cube textures: all six faces are extracted; their game orientation and Xbox reflections are not reproduced.');
const sourceNames=['Landing_Pad:armShape1','Fog:mountainsShape1','A5.xbr','[SOURCE] Eau : Surface','C:/Game/Nuit/Models.xbr','NODE_800'];
for(const name of sourceNames) assert.equal(translate(name),name);
setLanguage('fr');
assert.equal(translate('Enregistrer'),'Enregistrer');
assert.equal(translate('import.progress',{percent:25}),'Import de l’ISO · 25 %');
assert.equal(setLanguage('de'),'fr');
""")


def test_numbers_sizes_and_only_editor_level_aliases_follow_the_selected_language():
    run_node(IMPORTS + """
setLanguage('en');
assert.equal(getLocale(),'en-US');
assert.equal(formatNumber(1193),'1,193');
assert.equal(formatNumber(.25,{maximumFractionDigits:2}),'0.25');
assert.equal(formatSize(10*1048576),'10.0 MB');
assert.equal(formatSize(2*1024),'2 KB');
assert.equal(translateLevelName('Eau — W1'),'Water — W1');
assert.equal(translateLevelName('Perathia — Ville'),'Perathia — Town');
assert.equal(translateLevelName('Salle d’entraînement'),'Training Room');
assert.equal(translateLevelName('Eau_Structure:W1Shape'),'Eau_Structure:W1Shape');
setLanguage('fr');
assert.equal(getLocale(),'fr-FR');
assert.match(formatNumber(1193),/^1[\u202f\u00a0]193$/u);
assert.equal(formatNumber(.25,{maximumFractionDigits:2}),'0,25');
assert.equal(formatSize(10*1048576),'10,0 Mo');
assert.equal(translateLevelName('Eau — W1'),'Eau — W1');
""")


# The module touches only ordinary DOM text/attributes. A small DOM fixture
# exercises actual language toggles and observer callbacks without WebGL.
DOM = r"""
class Element {
  constructor(tag='span',attributes={}) {this.nodeType=1;this.tagName=tag;this.attributes={...attributes};this.dataset={};this.children=[];this.parentElement=null;this.events={};}
  hasAttribute(name){return name in this.attributes;}
  getAttribute(name){return this.attributes[name] ?? null;}
  setAttribute(name,value){this.attributes[name]=value;}
  addEventListener(name,callback){this.events[name]=callback;}
  matches(selector){
    if(selector==='[data-i18n-ignore]') return this.hasAttribute('data-i18n-ignore');
    if(selector==='[translate="no"]') return this.getAttribute('translate')==='no';
    if(selector==='dd') return this.tagName==='dd';
    if(selector.startsWith('#') && !selector.includes(' ')) return selector.slice(1)===this.attributes.id;
    if(selector.startsWith('.') && !selector.includes(' ')) return (this.attributes.class||'').split(' ').includes(selector.slice(1));
    if(/^[a-z]+$/.test(selector)) return this.tagName===selector;
    return false;
  }
  closest(selectors){for(let element=this;element;element=element.parentElement)if(selectors.split(',').some(selector=>element.matches(selector.trim())))return element;return null;}
  append(...nodes){for(const node of nodes){node.parentElement=this;node.ownerDocument=this.ownerDocument;this.children.push(node);}return this;}
  get textContent(){return this.children.map(node=>node.nodeType===3?node.data:node.textContent).join('');}
}
const text=data=>({nodeType:3,data,parentElement:null});
const body=new Element('body');
const html=new Element('html');
const languageSelect=new Element('select',{id:'languageSelect','aria-label':'Langue de l’interface'});
const document={body,documentElement:html,getElementById:id=>id==='languageSelect'?languageSelect:null,createTreeWalker:root=>{
  const nodes=[];const visit=node=>{for(const child of node.children||[]){nodes.push(child);visit(child);}};visit(root);
  let i=-1;return {nextNode(){return ++i<nodes.length;},get currentNode(){return nodes[i];}};
}};
body.ownerDocument=document;html.ownerDocument=document;body.append(languageSelect);
const make=(label,attributes={},tag='span')=>{const element=new Element(tag,attributes);element.ownerDocument=document;element.append(text(label));body.append(element);return element;};
const storage=new Map();globalThis.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)};
let changed;
globalThis.MutationObserver=class {constructor(callback){changed=callback;}observe(){}disconnect(){}};
"""


def test_dom_translation_switches_in_place_handles_new_statuses_and_persists_language():
    run_node(IMPORTS + DOM + """
const save=make('Enregistrer',{title:'Enregistrer le projet','aria-label':'Enregistrer'});
const status=make('11 modifications du jeu');
const refusal=make('Déplacement refusé : Cet objet est verrouillé. Déverrouillez-le avant de le déplacer.');
storage.set(languageStorageKey,'en');
initLanguage(document);
assert.equal(document.documentElement.lang,'en');
assert.equal(languageSelect.value,'en');
assert.equal(save.textContent,'Save');
assert.equal(save.getAttribute('title'),'Save project');
assert.equal(save.getAttribute('aria-label'),'Save');
assert.equal(status.textContent,'11 game changes');
assert.equal(refusal.textContent,'Movement refused: This object is locked. Unlock it before moving it.');
const calls=[];const unsubscribe=onLanguageChange(value=>calls.push(value));
languageSelect.events.change({target:{value:'fr'}});
assert.equal(save.textContent,'Enregistrer');
assert.equal(status.textContent,'11 modifications du jeu');
assert.equal(refusal.textContent,'Déplacement refusé : Cet objet est verrouillé. Déverrouillez-le avant de le déplacer.');
assert.equal(storage.get(languageStorageKey),'fr');
assert.equal(document.documentElement.lang,'fr');
setLanguage('en');
status.children[0].data='12 modifications du jeu';
changed([{type:'characterData',target:status.children[0]}]);
assert.equal(status.textContent,'12 game changes');
const warning=make('Cet élément est verrouillé. Déverrouillez-le pour le modifier.');
changed([{type:'childList',addedNodes:[warning]}]);
assert.equal(warning.textContent,'This element is locked. Unlock it to edit it.');
save.setAttribute('title','Aucune modification à exporter');
changed([{type:'attributes',target:save}]);
assert.equal(save.getAttribute('title'),'No changes to export');
setLanguage('fr');
assert.equal(status.textContent,'12 modifications du jeu');
assert.equal(save.getAttribute('title'),'Aucune modification à exporter');
assert.equal(warning.textContent,'Cet élément est verrouillé. Déverrouillez-le pour le modifier.');
assert.deepEqual(calls,['fr','en','fr']);
unsubscribe();setLanguage('en');assert.equal(calls.length,3);
""")


def test_names_that_look_like_ui_and_source_values_are_never_translated():
    run_node(IMPORTS + DOM + """
const hierarchyName=make('Nuit',{class:'tree-name',title:'Nuit'});
const selectedName=make('Déplacer',{id:'selectedName'});
const filename=make('C:/Game/Nuit/Enregistrer.xbr',{id:'sourcePath'});
const explicitSource=make('Textures',{'data-i18n-ignore':''});
const noTranslate=make('Monde',{translate:'no'});
const dt=make('Nœud de transformation',{},'dt');
const dd=make('Nuit',{},'dd');dd.previousElementSibling=dt;
const input=make('',{value:'1.234',placeholder:'Rechercher dans la scène'},'input');
initLanguage(document);setLanguage('en');
assert.equal(hierarchyName.textContent,'Nuit');assert.equal(hierarchyName.getAttribute('title'),'Nuit');
assert.equal(selectedName.textContent,'Déplacer');assert.equal(filename.textContent,'C:/Game/Nuit/Enregistrer.xbr');
assert.equal(explicitSource.textContent,'Textures');assert.equal(noTranslate.textContent,'Monde');
assert.equal(dt.textContent,'Transform node');assert.equal(dd.textContent,'Nuit');
assert.equal(input.getAttribute('placeholder'),'Search scene');assert.equal(input.getAttribute('value'),'1.234');
setLanguage('fr');assert.equal(dd.textContent,'Nuit');assert.equal(hierarchyName.textContent,'Nuit');
""")


def test_control_ids_values_and_accessible_labels_are_available_to_the_editor():
    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = {}
            self.options = {}
            self.current_select = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get("id"):
                self.elements[attrs["id"]] = (tag, attrs)
            if tag == "select":
                self.current_select = attrs.get("id")
            elif tag == "option" and self.current_select:
                self.options.setdefault(self.current_select, []).append(attrs.get("value"))

        def handle_endtag(self, tag):
            if tag == "select":
                self.current_select = None

    controls = Controls()
    controls.feed((ROOT / "web" / "index.html").read_text("utf-8"))
    assert controls.options["languageSelect"] == ["fr", "en"]
    assert controls.options["flySpeedSelect"] == ["0.2", "1", "5"]
    assert controls.elements["languageSelect"][1]["aria-label"]
    assert controls.elements["flySpeedSelect"][1]["aria-label"]
    assert controls.elements["isoFileInput"][1]["accept"] == ".iso,.xiso"
    for control in ("importIsoBtn", "lockBtn", "lockAllBtn", "unlockAllBtn"):
        assert controls.elements[control][0] == "button"
