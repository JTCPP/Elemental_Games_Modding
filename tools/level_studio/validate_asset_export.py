"""Check every exported file, PNG, glTF attribute/index and relative link."""
from array import array
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit


def linked_file(document, uri, root):
    if not isinstance(uri, str) or urlsplit(uri).scheme or urlsplit(uri).netloc or "\\" in uri:
        raise ValueError("Lien non local ou URI de fichier non valide.")
    file = (document.parent / unquote(uri)).resolve()
    if not file.is_relative_to(root.resolve()) or not file.is_file():
        raise ValueError(f"Lien exporté absent/hors du pack : {uri}")
    return file


def validate_gltf(file, root):
    file, root = Path(file), Path(root)
    doc = json.loads(file.read_text(encoding="utf-8"))
    if doc.get("asset", {}).get("version") != "2.0":
        raise ValueError("Le document n’est pas un glTF 2.0.")
    buffers = []
    for buffer in doc.get("buffers", []):
        binary = linked_file(file, buffer["uri"], root).read_bytes()
        if len(binary) != buffer["byteLength"]:
            raise ValueError("Longueur du buffer glTF incohérente.")
        buffers.append(binary)
    components = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
    widths = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
    values = []
    for accessor in doc["accessors"]:
        view = doc["bufferViews"][accessor["bufferView"]]
        code, size = components[accessor["componentType"]]
        width, count = widths[accessor["type"]], accessor["count"]
        if count <= 0 or view.get("byteStride"):
            raise ValueError("Accessor vide ou entrelacement inattendu.")
        offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        length = count * width * size
        binary = buffers[view["buffer"]]
        if offset % size or view.get("byteOffset", 0) % 4 or accessor.get("byteOffset", 0) + length > view["byteLength"] or offset + length > len(binary):
            raise ValueError("Accessor hors des bornes/alignment glTF.")
        decoded = array(code)
        decoded.frombytes(binary[offset:offset + length])
        if sys.byteorder != "little":
            decoded.byteswap()
        if code == "f" and not all(math.isfinite(v) for v in decoded):
            raise ValueError("Attribut glTF non fini.")
        for key, function in (("min", min), ("max", max)):
            if key in accessor:
                if len(accessor[key]) != width or any(abs(function(decoded[axis::width]) - accessor[key][axis]) > 1e-6 * max(1., abs(accessor[key][axis])) for axis in range(width)):
                    raise ValueError("Bornes de l’accessor différentes des données.")
        values.append(decoded)
    for image in doc.get("images", []):
        if linked_file(file, image["uri"], root).suffix.lower() != ".png":
            raise ValueError("Image liée non PNG.")
    for texture in doc.get("textures", []):
        if not 0 <= texture["source"] < len(doc.get("images", [])):
            raise ValueError("Texture hors du catalogue d’images.")
    triangles = 0
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            if primitive.get("mode", 4) != 4:
                raise ValueError("Primitive exportée non triangulée.")
            position = doc["accessors"][primitive["attributes"]["POSITION"]]
            count = position["count"]
            if position["type"] != "VEC3" or position["componentType"] != 5126 or "min" not in position or "max" not in position:
                raise ValueError("Accessor POSITION non conforme.")
            for semantic, index in primitive["attributes"].items():
                attribute = doc["accessors"][index]
                if attribute["count"] != count:
                    raise ValueError("Nombre de sommets différent entre attributs.")
                if semantic == "NORMAL":
                    normal = values[index]
                    if any(abs(math.sqrt(sum(v*v for v in normal[start:start+3]))-1)>2e-5 for start in range(0,len(normal),3)):
                        raise ValueError("Normale glTF non unitaire.")
            indices = values[primitive["indices"]]
            if len(indices) % 3 or min(indices) < 0 or max(indices) >= count:
                raise ValueError("Indices hors des sommets de la primitive.")
            if not 0 <= primitive["material"] < len(doc["materials"]):
                raise ValueError("Matériau de primitive absent.")
            triangles += len(indices) // 3
    for material in doc["materials"]:
        texture = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if texture and not 0 <= texture["index"] < len(doc.get("textures", [])):
            raise ValueError("Texture principale du matériau absente.")
        for row in material.get("extras", {}).get("textureStageLinks", []):
            index = row.get("gltfTexture")
            if index is not None and not 0 <= index < len(doc.get("textures", [])):
                raise ValueError("Lien de texture secondaire absent.")
    for node in doc["nodes"]:
        if "mesh" in node and not 0 <= node["mesh"] < len(doc["meshes"]):
            raise ValueError("Nœud glTF lié à un mesh absent.")
        if any(not 0 <= child < len(doc["nodes"]) for child in node.get("children", [])):
            raise ValueError("Enfant glTF absent.")
    return {"triangles": triangles, "meshes": len(doc["meshes"])}


def validate_export(root, *, verify_hashes=True):
    from PIL import Image
    root = Path(root).resolve()
    stats = {"gltfFiles": 0, "pngFiles": 0, "triangles": 0, "filesChecked": 0, "bytesChecked": 0, "errors": []}
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = linked_file(root / "manifest.json", entry["path"], root)
        if path.stat().st_size != entry["bytes"]:
            raise ValueError(f"Fichier incomplet : {entry['path']}")
        if verify_hashes and hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Empreinte différente : {entry['path']}")
        stats["filesChecked"] += 1
        stats["bytesChecked"] += entry["bytes"]
        if path.suffix == ".gltf":
            checked = validate_gltf(path, root)
            stats["gltfFiles"] += 1
            stats["triangles"] += checked["triangles"]
        elif path.suffix == ".png":
            with Image.open(path) as image:
                if image.format != "PNG" or min(image.size) <= 0:
                    raise ValueError("PNG exporté non valide.")
                image.verify()
            stats["pngFiles"] += 1
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    for row in index["ressources"]:
        if row.get("fichier"):
            linked_file(root / "index.json", row["fichier"], root)
        for image in row.get("images", []):
            linked_file(root / "index.json", image, root)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--skip-hashes", action="store_true")
    args = parser.parse_args()
    print(json.dumps(validate_export(args.directory, verify_hashes=not args.skip_hashes), ensure_ascii=False))
