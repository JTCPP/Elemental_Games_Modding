"""The exported index stays local and every resource remains reachable."""
import json
from pathlib import Path
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from asset_index import write_index


class ScriptReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current = {"attrs": dict(attrs), "text": ""}
            self.scripts.append(self.current)

    def handle_data(self, text):
        if self.current is not None:
            self.current["text"] += text

    def handle_endtag(self, tag):
        if tag == "script":
            self.current = None


def read_index(path):
    reader = ScriptReader()
    reader.feed(path.read_text(encoding="utf-8"))
    assert len(reader.scripts) == 2
    return json.loads(reader.scripts[0]["text"]), reader.scripts[1]["text"]


def test_index_escapes_embedded_names_and_preserves_relative_asset_links(tmp_path):
    name = '</script><script>alert("x")</script>&< Émeraude\u2028'
    path = write_index(tmp_path / "collection", [{"archive": "characters", "kind": "character",
                       "name": name, "id": "body-12", "path": "characters/Modèles/Azurik #1.gltf",
                       "triangles": 6625, "vertices": 3100}], {"gltfFiles": 1, "name": name})
    assert path == (tmp_path / "collection" / "index.html").resolve()
    data, script = read_index(path)
    assert data["rows"][0]["name"] == name
    assert data["summary"]["name"] == name
    assert data["rows"][0]["url"] == "characters/Mod%C3%A8les/Azurik%20%231.gltf"
    assert data["rows"][0]["triangles"] == 6625
    assert "alert(" not in script
    assert "fetch(" not in script and "XMLHttpRequest" not in script
    assert "innerHTML" not in script
    assert "connect-src 'none'" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["../dump/model.gltf", "a/../../model.gltf", "/model.gltf",
                                 "C:/dump/model.gltf", "https://example.com/model.gltf", "", "\0"])
def test_index_rejects_paths_outside_the_exported_collection(tmp_path, path):
    with pytest.raises(ValueError):
        write_index(tmp_path, [{"archive": "w1", "kind": "model", "path": path}], {})
    assert not (tmp_path / "index.html").exists()


def test_index_paginates_all_rows_and_filters_accented_source_names_offline(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the offline index")
    rows = [{"archive": "archive-b" if i % 2 else "archive-a",
             "kind": "texture" if i % 3 == 0 else "static",
             "name": f"Roche d’été {i:03d}", "id": f"asset-{i}",
             "path": f"archive-{i % 2}/asset-{i}.png" if i % 3 == 0 else f"archive-{i % 2}/asset-{i}.gltf",
             "width": 256, "height": 128, "triangles": i * 10, "vertices": i * 5}
            for i in range(160)]
    rows[159].update(path="archive-1/sequence.json", images=["archive-1/asset-159.png", "archive-1/frame-é#1.png"])
    data, script = read_index(write_index(tmp_path, rows, {"gltfFiles": 106}))
    # A small DOM adapter executes the actual shipped script and its events.
    # It avoids opening another QA browser or requiring any remote library.
    harness = r"""
import assert from 'node:assert/strict';
class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.value='';this.textContent='';this.dataset={};this.namespaceURI='http://www.w3.org/2000/svg';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes.flatMap(node=>node?.tag==='#fragment'?node.children:[node]);}
  setAttribute(name,value){this[name]=value;}
  addEventListener(name,callback){(this.listeners[name]||=[]).push(callback);}
  emit(name,extra={}){for(const callback of this.listeners[name]||[])callback({target:this,preventDefault(){},...extra});}
  focus(){} remove(){} showModal(){this.open=true;} close(){this.open=false;}
  classList={add(){}};
}
const elements=new Map();
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
globalThis.document={getElementById:get,createElement:tag=>new Element(tag),createElementNS:(ns,tag)=>new Element(tag),createTextNode:text=>({textContent:text}),createDocumentFragment:()=>new Element('#fragment'),querySelectorAll:()=>[]};
get('asset-data').textContent=PAYLOAD;get('sort').value='name';get('pageSize').value='48';
SCRIPT
const names=()=>get('grid').children.map(card=>card.children[1].children[0].textContent);
assert.equal(get('rowTotal').textContent,'160');assert.equal(names().length,48);assert.equal(names()[0],'Roche d’été 000');
get('nextPage').emit('click');assert.equal(get('pageNumber').value,'2');assert.equal(names()[0],'Roche d’été 048');
get('pageNumber').value='4';get('pageNumber').emit('change');assert.equal(names().length,16);assert.equal(names()[15],'Roche d’été 159');assert.equal(get('nextPage').disabled,true);
get('pageNumber').value='999';get('pageNumber').emit('change');assert.equal(get('pageNumber').value,'4');
get('archiveFilter').value='archive-b';get('archiveFilter').emit('change');assert.equal(get('pageNumber').value,'1');assert.match(get('resultCount').textContent,/80/);
get('kindFilter').value='texture';get('kindFilter').emit('change');assert.equal(names().length,27);assert.ok(names().includes('Roche d’été 159'));
get('search').value='roche ete 159';get('search').emit('keydown',{key:'Enter'});assert.deepEqual(names(),['Roche d’été 159']);
const preview=get('grid').children[0].children[0];preview.emit('click');assert.equal(get('textureDialog').open,true);assert.equal(get('largeTexture').src,'archive-1/asset-159.png');
assert.equal(get('imageControls').hidden,false);assert.equal(get('textureDocument').href,'archive-1/sequence.json');
get('imageNumber').value='1';get('imageNumber').emit('input');assert.equal(get('largeTexture').src,'archive-1/frame-%C3%A9%231.png');assert.equal(get('downloadTexture').download,'frame-é#1.png');
get('search').value='introuvable';get('search').emit('keydown',{key:'Enter'});assert.equal(names().length,0);assert.equal(get('empty').hidden,false);
get('resetFilters').emit('click');assert.equal(names().length,48);assert.equal(get('empty').hidden,true);
get('pageSize').value='192';get('pageSize').emit('change');assert.equal(names().length,160);assert.equal(get('nextPage').disabled,true);
get('kindFilter').value='static';get('kindFilter').emit('change');get('sort').value='triangles';get('sort').emit('change');assert.equal(names()[0],'Roche d’été 158');
const action=get('grid').children[0].children[1].children[3];assert.equal(action.tag,'a');assert.equal(action.href,'archive-0/asset-158.gltf');assert.equal(action.download,'asset-158.gltf');
""".replace("PAYLOAD", json.dumps(json.dumps(data, ensure_ascii=False))).replace("SCRIPT", script)
    script_path = tmp_path / "exercise-index.mjs"
    script_path.write_text(harness, encoding="utf-8")
    subprocess.run([node, str(script_path)], check=True,
                   capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
