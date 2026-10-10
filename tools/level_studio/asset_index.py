"""Self-contained, offline browser for an exported Azurik asset pack.

The index embeds its data and uses relative local links. It neither reads
the game dump nor starts a server; the exporter owns the asset extraction.
"""
from __future__ import annotations

import json
import math
from pathlib import Path, PurePosixPath
from urllib.parse import quote


_KINDS = {"texture", "model", "static", "lod", "character"}


def _relative_path(value) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("Une ressource doit avoir un chemin relatif valide.")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part == ".." or ":" in part for part in path.parts):
        raise ValueError("Le chemin d’une ressource doit rester dans l’archive exportée.")
    return path.as_posix()


def _rows_for_index(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        kind = str(row.get("kind", "model"))
        if kind not in _KINDS:
            raise ValueError(f"Type de ressource non pris en charge : {kind}")
        path = _relative_path(row.get("path"))
        item = {"archive": str(row.get("archive", "")), "kind": kind,
                "name": str(row.get("name") or row.get("id") or PurePosixPath(path).name),
                "id": str(row.get("id", "")), "path": path,
                "url": quote(path, safe="/")}
        if item["archive"] == "characters.xbr" and item["name"] == "characters/garret4":
            item["aliases"] = ["Azurik"]
        if kind == "texture":
            images = [_relative_path(image) for image in (row.get("images") or []) if image]
            if not images and PurePosixPath(path).suffix.lower() == ".png":
                images = [path]
            item["images"] = images
            item["imageUrls"] = [quote(image, safe="/") for image in images]
        for key in ("width", "height", "triangles", "vertices"):
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                item[key] = int(value)
        result.append(item)
    return result


def write_index(pack: Path, rows: list[dict], summary: dict) -> Path:
    """Write pack/index.html and return its absolute path.

    Rows use archive/kind/name/id/path, with width/height/triangles/vertices
    optional. An optional images list exposes cube faces or animation frames.
    Every path is relative to the pack; archive files stay local.
    Export summary values are retained in the embedded data for provenance.
    """
    payload = json.dumps({"rows": _rows_for_index(rows), "summary": summary},
                         ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # application/json is still an HTML raw-text element. Prevent source
    # names from closing it; all UI strings are subsequently textContent.
    payload = (payload.replace("<", "\\u003c").replace(">", "\\u003e")
               .replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    pack = Path(pack).resolve()
    pack.mkdir(parents=True, exist_ok=True)
    output = pack / "index.html"
    output.write_text(_HTML.replace("__ASSET_DATA__", payload), encoding="utf-8")
    return output


_HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' file: data:; connect-src 'none'; object-src 'none'; base-uri 'none'">
<title>Azurik Studio · Bibliothèque exportée</title>
<style>
:root{font-family:"Segoe UI",Arial,sans-serif;color:#dce8f4;background:#080f18;font-synthesis:none;--muted:#8194aa;--line:#263547;--accent:#69d9cd;--panel:#111c29;--focus:#8bdff1}
*{box-sizing:border-box}body{margin:0;min-width:340px}button,input,select,a{font:inherit}button,a,input,select{-webkit-tap-highlight-color:transparent}button,select{cursor:pointer}button,a{touch-action:manipulation}button{color:inherit}button:disabled{cursor:default;opacity:.38}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--focus);outline-offset:3px}a{color:#9adef0;text-decoration:none}a:hover{color:white}svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.65;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}[hidden]{display:none!important}
.app{height:100vh;height:100dvh;display:flex;flex-direction:column;background:radial-gradient(ellipse at 15% -30%,#17395370,transparent 55%),#080f18}
.header{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:20px 30px;border-bottom:1px solid var(--line);background:#0e1927c9;flex-shrink:0}.brand{display:flex;align-items:center;gap:12px}.brand-symbol{width:39px;height:39px;display:grid;place-items:center;color:var(--accent);background:#1d49433d;border:1px solid #3c81745e;border-radius:11px}.brand-symbol svg{width:24px;height:24px}.brand strong{display:block;font-size:17px;letter-spacing:.025em}.brand small{display:block;margin-top:3px;color:var(--muted);font-size:11px;letter-spacing:.12em;text-transform:uppercase}.header-actions{display:flex;align-items:center;gap:22px;font-size:12px}.local-badge{display:flex;align-items:center;gap:7px;color:#9cc9bd;background:#17352d69;padding:6px 10px;border-radius:20px;font-size:11px}.local-badge i{width:5px;height:5px;background:var(--accent);border-radius:50%;box-shadow:0 0 9px #69d9cd70}
.intro{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:23px 30px 21px;flex-shrink:0}.intro h1{font-size:24px;font-weight:600;margin:0;letter-spacing:-.02em}.intro p{margin:6px 0 0;color:var(--muted);font-size:12px;line-height:1.5}.stats{display:flex;gap:26px;flex-shrink:0}.stat strong{display:block;font-size:20px;line-height:1.15;font-weight:550;font-variant-numeric:tabular-nums}.stat span{display:block;color:var(--muted);font-size:10px;margin-top:5px;text-transform:uppercase;letter-spacing:.09em}
.toolbar{display:grid;grid-template-columns:minmax(250px,1fr) 180px 164px 160px;gap:10px;margin:0 30px 16px;flex-shrink:0}.search{position:relative}.search svg{position:absolute;left:12px;top:12px;width:17px;height:17px;color:#91a5b8}.search input{width:100%;height:41px;background:#111e2c;border:1px solid #304559;color:inherit;border-radius:7px;padding:0 37px;font-size:12px}.search input::placeholder{color:#72899f}.search:focus-within input{border-color:#71c6c0}.clear{position:absolute;right:6px;top:5px;width:30px;height:30px;padding:0;border:0;background:transparent;border-radius:4px;color:#91a5b8;font-size:21px}.clear:hover{background:#263d51}.control{display:flex;align-items:center;position:relative;background:#111e2c;border:1px solid var(--line);border-radius:7px}.control svg{width:15px;height:15px;margin-left:11px;color:#87a0b7}.control select{height:39px;width:100%;min-width:0;padding:0 24px 0 8px;border:0;color:#c9d8e7;background:transparent;font-size:12px;outline-offset:0}.control option{background:#152234;color:#dce8f4}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.catalogue{display:flex;flex-direction:column;min-height:0;flex:1;margin:0 30px}.result-bar{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:0 0 12px;color:var(--muted);font-size:11px}.result-bar strong{font-weight:500;color:#bccede}.hint{display:flex;align-items:center;gap:6px;color:#91a9b9}.hint svg{width:14px;height:14px}.scroll{overflow:auto;min-height:0;flex:1;padding:0 8px 10px 0;scrollbar-color:#365069 #101a27;scrollbar-width:thin}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:13px;align-content:start}.card{border:1px solid #25384c;border-radius:9px;background:linear-gradient(150deg,#142333,#101a28);overflow:hidden;min-width:0;transition:border-color .12s,box-shadow .12s}.card:hover{border-color:#466b82;box-shadow:0 5px 18px #0003}.preview{height:132px;display:flex;align-items:center;justify-content:center;position:relative;background:#0a1320;border-bottom:1px solid #263748;overflow:hidden}.preview.texture{background-color:#142130;background-image:linear-gradient(45deg,#192938 25%,transparent 25%),linear-gradient(-45deg,#192938 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#192938 75%),linear-gradient(-45deg,transparent 75%,#192938 75%);background-size:20px 20px;background-position:0 0,0 10px,10px -10px,-10px 0}.preview img{max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;display:block}.preview-button{border:0;color:inherit;width:100%;padding:8px;cursor:zoom-in;font:inherit}.preview-button:focus-visible{outline-offset:-4px}.model-symbol{width:70px;height:70px;stroke-width:1.05;color:#7190b0;opacity:.85}.model-format{position:absolute;right:11px;bottom:11px;font-size:10px;color:#738aa3;letter-spacing:.08em}.preview-failed{font-size:11px;color:#a8bdcc}.badge{position:absolute;left:10px;top:10px;padding:4px 6px;background:#081421de;border:1px solid #3a53686b;border-radius:4px;font-size:9px;font-weight:600;letter-spacing:.08em;color:#a1bece}.badge.texture{color:#96d8ca}.badge.character{color:#c2b4f1}.badge.lod{color:#decd9c}.card-body{padding:12px 13px 11px}.card-name{margin:0 0 5px;font-size:12px;font-weight:600;color:#e1eaf3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.card-source{font-size:10px;color:#849bb0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.card-metrics{font-size:10px;color:#a7bdcc;min-height:14px;margin:11px 0 10px;display:flex;justify-content:space-between;gap:8px;font-variant-numeric:tabular-nums}.card-action{width:100%;height:30px;display:flex;align-items:center;justify-content:center;gap:7px;color:#b5d2df;background:#1a324445;border:1px solid #34536b7a;border-radius:5px;font-size:10px;transition:background .12s}.card-action:hover{background:#25495b;color:#dcfaf8;border-color:#539086}.card-action svg{width:13px;height:13px}.empty{display:grid;place-items:center;text-align:center;min-height:250px}.empty svg{width:34px;height:34px;color:#62849b}.empty h2{font-size:16px;font-weight:500;margin:14px 0 6px}.empty p{font-size:12px;color:var(--muted);margin:0}.empty button{margin-top:18px;background:#173344;border:1px solid #32596b;border-radius:5px;padding:8px 12px;font-size:11px}
.pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px 0;border-top:1px solid var(--line);font-size:11px;color:var(--muted);flex-shrink:0}.page-buttons{display:flex;align-items:center;gap:8px}.page-button{height:30px;padding:0 11px;display:flex;align-items:center;gap:7px;background:#122233;border:1px solid #304358;border-radius:5px;font-size:11px}.page-button:not(:disabled):hover{border-color:#4d8494;background:#1a3547}.page-button svg{width:13px;height:13px}.page-jump{display:flex;align-items:center;gap:6px;margin:0 6px}.page-jump input{width:51px;background:#101e2d;border:1px solid #2c4459;border-radius:4px;text-align:center;height:29px;color:#dce8f4;font-size:11px;font-variant-numeric:tabular-nums;-moz-appearance:textfield}.page-jump input::-webkit-inner-spin-button{-webkit-appearance:none}.page-count{font-variant-numeric:tabular-nums;white-space:nowrap}.page-size{display:flex;align-items:center;gap:7px}.page-size select{border:1px solid #2c4459;background:#101e2d;border-radius:4px;padding:5px;color:#c6d6e6;font-size:11px}
.footer{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:12px 30px;background:#0c1622;border-top:1px solid #243345;font-size:10px;color:#71879c;flex-shrink:0}.footer a{font-size:10px;color:#93adbd}.footer-links{display:flex;gap:17px}.offline-error{padding:28px;margin:30px;border:1px solid #735a37;border-radius:8px;color:#e4caa5;background:#33281b;font-size:13px;line-height:1.6}
dialog{color:#dce8f4;background:#101d2b;border:1px solid #3b586f;border-radius:12px;padding:0;max-width:min(980px,calc(100vw - 40px));width:860px;box-shadow:0 24px 100px #0009}dialog::backdrop{background:#030914db;backdrop-filter:blur(4px)}.dialog-header{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:17px 21px;border-bottom:1px solid #2a3c4f}.dialog-header h2{margin:0;font-size:15px;font-weight:600;overflow-wrap:anywhere}.dialog-header p{margin:4px 0 0;color:#819ab0;font-size:11px}.close-dialog{width:30px;height:30px;background:#203449;border:1px solid #3b536c;color:#b8cbdb;border-radius:5px;font-size:20px}.large-preview{padding:20px;min-height:140px;max-height:65vh;display:flex;align-items:center;justify-content:center;background:#09121e}.large-preview img{max-width:100%;max-height:calc(65vh - 40px);object-fit:contain}.image-controls{display:flex;align-items:center;gap:14px;padding:12px 21px;background:#132536;font-size:11px}.image-controls input{min-width:80px;flex:1;accent-color:#69d9cd}.image-controls span{font-variant-numeric:tabular-nums;white-space:nowrap}.dialog-bottom{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:15px 21px;border-top:1px solid #26394e;font-size:11px;flex-wrap:wrap}.dialog-bottom a{display:flex;align-items:center;gap:7px}.help-body{padding:20px 24px 25px;line-height:1.8;font-size:13px;color:#b4c7d8}.help-body p{margin:0 0 13px}.help-body p:last-child{margin:0}.help-body strong{color:#e2eef8}.help-body code{font-size:12px;color:#aad9cf;background:#1d3c3b73;border-radius:3px;padding:1px 5px}.help-body a{display:inline-flex;align-items:center;gap:6px}
@media(min-width:1550px){.header,.footer{padding-left:38px;padding-right:38px}.intro{padding:27px 38px}.toolbar{margin-left:38px;margin-right:38px;grid-template-columns:minmax(350px,1fr) 215px 185px 190px}.catalogue{margin-left:38px;margin-right:38px}.grid{grid-template-columns:repeat(auto-fill,minmax(238px,1fr));gap:16px}.preview{height:154px}.card-body{padding:14px 15px}.card-name{font-size:13px}.card-action{height:32px}.stats{gap:38px}}
@media(max-width:980px){.header{padding:16px 20px}.intro{padding:20px}.toolbar{margin:0 20px 16px;grid-template-columns:minmax(230px,1fr) 160px 140px}.control.sort{grid-column:3;grid-row:2}.search{grid-column:1/4}.catalogue{margin:0 20px}.grid{grid-template-columns:repeat(auto-fill,minmax(195px,1fr))}.stats{gap:18px}.stat strong{font-size:17px}.footer{padding:11px 20px}.footer-links{gap:12px}.header-actions{gap:14px}.local-badge{display:none}}
@media(max-width:660px){.app{height:auto;min-height:100dvh}.header-actions>a{display:none}.intro{align-items:flex-start;flex-direction:column;gap:18px}.intro h1{font-size:22px}.stats{width:100%;justify-content:space-between;gap:12px}.toolbar{grid-template-columns:1fr 1fr}.search{grid-column:1/3}.control.sort{grid-column:1/3;grid-row:auto}.catalogue{flex:1}.scroll{overflow:visible;padding-right:0}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.preview{height:112px}.card-body{padding:10px}.card-name{font-size:11px}.card-action{font-size:9px}.card-source{font-size:9px}.card-metrics{display:block;line-height:1.5;min-height:28px;margin-top:7px}.card-metrics span{display:block}.result-bar .hint{display:none}.pagination{flex-wrap:wrap;justify-content:center}.page-size{display:none}.page-buttons{order:2;justify-content:center;width:100%}.footer{flex-direction:column;gap:9px;text-align:center}.footer-links{gap:15px}.page-jump{margin:0}.page-button{padding:0 8px}.dialog-bottom{font-size:10px}.large-preview{padding:12px}}
</style>
</head>
<body>
<div class="app">
  <header class="header">
    <div class="brand"><div class="brand-symbol" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m12 2 8.5 5v10L12 22l-8.5-5V7Z"/><path d="m3.5 7 8.5 5 8.5-5M12 12v10M8 4.4l8.5 5v5"/></svg></div><div><strong>AZURIK STUDIO</strong><small>Collection des ressources originales</small></div></div>
    <div class="header-actions"><span class="local-badge"><i></i>Disponible hors ligne</span><a href="LISEZ-MOI.txt">Lire le guide</a><button id="helpButton" class="page-button">Utiliser les fichiers</button></div>
  </header>
<section class="intro" aria-labelledby="title"><div><h1 id="title">Bibliothèque du jeu</h1><p>Textures et modèles extraits des archives originales d’Azurik.</p></div><div class="stats"><div class="stat"><strong id="archiveTotal">—</strong><span>Archives graphiques</span></div><div class="stat"><strong id="imageTotal">—</strong><span>Images</span></div><div class="stat"><strong id="modelTotal">—</strong><span>Fichiers 3D</span></div><div class="stat"><strong id="rowTotal">—</strong><span>Ressources</span></div></div></section>
  <section class="toolbar" aria-label="Rechercher et filtrer la bibliothèque">
    <div class="search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg><label class="sr-only" for="search">Rechercher une ressource</label><input id="search" type="search" placeholder="Rechercher un nom, une archive ou un fichier…" autocomplete="off"><button id="clearSearch" class="clear" aria-label="Effacer la recherche" hidden>×</button></div>
    <div class="control"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h7l2 2h9v12H3ZM3 6V4h7l2 2"/></svg><label class="sr-only" for="archiveFilter">Archive</label><select id="archiveFilter"><option value="">Toutes les archives</option></select></div>
    <div class="control"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h6v6H4ZM14 4h6v6h-6ZM4 14h6v6H4ZM14 14h6v6h-6Z"/></svg><label class="sr-only" for="kindFilter">Type de ressource</label><select id="kindFilter"><option value="">Tous les types</option><option value="texture">Textures</option><option value="model">Modèles</option><option value="static">Décors statiques</option><option value="character">Personnages</option><option value="lod">Variantes de détail</option></select></div>
    <div class="control sort"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4v16m-4-4 4 4 4-4M14 6h6m-6 6h4m-4 6h2"/></svg><label class="sr-only" for="sort">Trier les résultats</label><select id="sort"><option value="name">Nom · A à Z</option><option value="archive">Archive · A à Z</option><option value="triangles">Modèles les plus détaillés</option><option value="dimensions">Images les plus grandes</option></select></div>
  </section>
  <main class="catalogue" aria-label="Ressources exportées">
    <div class="result-bar"><strong id="resultCount" aria-live="polite">Chargement de la collection…</strong><span class="hint"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h10v16H7ZM10 8h4m-4 4h4m-4 4h2"/></svg>Les fichiers restent dans leurs dossiers d’origine.</span></div>
    <div id="scroll" class="scroll"><div id="grid" class="grid"></div><div id="empty" class="empty" hidden><div><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4M8 10.5h5"/></svg><h2>Aucune ressource trouvée</h2><p>Essayez un autre nom ou élargissez les filtres.</p><button id="resetFilters">Afficher toute la collection</button></div></div></div>
    <nav class="pagination" aria-label="Pages de résultats"><span id="pageRange">—</span><div class="page-buttons"><button id="prevPage" class="page-button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg>Précédent</button><label class="page-jump">Page <input id="pageNumber" type="number" min="1" value="1" aria-label="Aller à la page"><span id="pageCount" class="page-count">sur 1</span></label><button id="nextPage" class="page-button">Suivant<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 6 6 6-6 6"/></svg></button></div><label class="page-size">Par page <select id="pageSize"><option value="48">48</option><option value="96">96</option><option value="192">192</option></select></label></nav>
  </main>
  <footer class="footer"><span>Collection locale · Sources originales préservées</span><div class="footer-links"><a href="index.csv" download="index.csv">Liste des fichiers</a><a href="validation.json">Rapport de validation</a><a href="LISEZ-MOI.txt">Guide de l’archive</a></div></footer>
</div>
<dialog id="textureDialog" aria-labelledby="textureTitle"><div class="dialog-header"><div><h2 id="textureTitle">Texture</h2><p id="textureDetails"></p></div><button class="close-dialog" data-close="textureDialog" aria-label="Fermer l’aperçu">×</button></div><div class="large-preview"><img id="largeTexture" alt=""></div><div id="imageControls" class="image-controls" hidden><label for="imageNumber">Image</label><input id="imageNumber" type="range" min="0" value="0" step="1"><span id="imageCount"></span></div><div class="dialog-bottom"><span id="textureArchive"></span><a id="downloadTexture" href="#" download>Télécharger l’image <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4"/></svg></a><a id="openTexture" href="#" target="_blank" rel="noopener">Ouvrir l’image ↗</a><a id="textureDocument" href="#" target="_blank" rel="noopener" hidden>Données des images ↗</a></div></dialog>
<dialog id="helpDialog" aria-labelledby="helpTitle"><div class="dialog-header"><h2 id="helpTitle">Utiliser votre collection</h2><button class="close-dialog" data-close="helpDialog" aria-label="Fermer l’aide">×</button></div><div class="help-body"><p><strong>Textures.</strong> Les images PNG peuvent être consultées ici, téléchargées ou ouvertes dans votre logiciel habituel. Les aperçus utilisent les images de l’archive.</p><p><strong>Modèles.</strong> Dans votre logiciel 3D, importez le fichier <code>.gltf</code> depuis le dossier extrait de la collection. Ce fichier utilise son <code>.bin</code> associé et ses textures : gardez-les ensemble. Ils se trouvent déjà aux bonnes places ; conservez l’organisation des dossiers lorsque vous déplacez ou partagez la collection.</p><p><strong>Personnages et variantes.</strong> Les personnages sont extraits en pose de liaison. Les variantes de détail sont proposées séparément. Les animations, les scripts et les effets du moteur ne sont pas exécutés par cette page.</p><p><strong>Tout est local.</strong> La recherche et les filtres fonctionnent sans connexion. Vous pouvez revenir à cette bibliothèque en ouvrant <code>index.html</code>.</p><p><a href="LISEZ-MOI.txt">Consulter le guide complet ↗</a></p></div></dialog>
<noscript><p class="offline-error">Activez JavaScript dans votre navigateur pour rechercher et parcourir cette collection. Vous pouvez aussi consulter <a href="index.csv">la liste complète des fichiers</a>.</p></noscript>
<script id="asset-data" type="application/json">__ASSET_DATA__</script>
<script>
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const numbers = new Intl.NumberFormat('fr-FR');
  const collator = new Intl.Collator('fr-FR', {numeric:true,sensitivity:'base'});
  const labels = {texture:'TEXTURE',model:'MODÈLE',static:'DÉCOR',character:'PERSONNAGE',lod:'VARIANTE'};
  const plural = {texture:'texture',model:'modèle',static:'décor',character:'personnage',lod:'variante'};
  const icon = kind => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('aria-hidden','true');
    const path = document.createElementNS(svg.namespaceURI,'path');
    path.setAttribute('d',kind==='image'?'M3 3h18v18H3ZM3 16l5-5 4 4 3-3 6 6M16 7h.01':kind==='download'?'M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4':'m12 2 9 5v10l-9 5-9-5V7Zm0 10 9-5M12 12 3 7M12 12v10M7.5 4.5l9 5v10');
    svg.append(path); return svg;
  };
  let data;
  try { data = JSON.parse($('asset-data').textContent); }
  catch { const error=document.createElement('p');error.className='offline-error';error.textContent='La liste de cette collection ne peut pas être lue. Consultez le guide et la liste des fichiers.';$('grid').append(error);$('resultCount').textContent='Collection indisponible';return; }
  const fold = value => String(value).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('fr-FR');
  const rows = data.rows.map((row,index) => ({...row,index,search:fold([row.name,row.archive,row.id,row.path,...(row.aliases||[])].join(' '))}));
  const archives = [...new Set(rows.map(row=>row.archive))].sort(collator.compare);
  const texturePaths = new Set(rows.filter(row=>row.kind==='texture').flatMap(row=>row.images));
  const modelPaths = new Set(rows.filter(row=>row.kind!=='texture').map(row=>row.path));
  $('archiveTotal').textContent=numbers.format(archives.length);$('imageTotal').textContent=numbers.format(texturePaths.size);
  $('modelTotal').textContent=numbers.format(modelPaths.size);$('rowTotal').textContent=numbers.format(rows.length);
  const archiveCounts=new Map();for(const row of rows)archiveCounts.set(row.archive,(archiveCounts.get(row.archive)||0)+1);
  for(const archive of archives){const option=document.createElement('option');option.value=archive;option.textContent=`${archive || 'Sans archive'} · ${numbers.format(archiveCounts.get(archive))}`;$('archiveFilter').append(option);}
  let page=1,pageSize=48,filtered=rows,queryTimer,activeTexture;
  const sourceText = row => row.archive || 'Archive originale';
  function setImage(index) {
    const row=activeTexture;index=Math.max(0,Math.min(index,row.imageUrls.length-1));
    $('largeTexture').src=row.imageUrls[index];$('largeTexture').alt=row.name;
    $('downloadTexture').href=row.imageUrls[index];$('downloadTexture').download=row.images[index].split('/').pop();$('openTexture').href=row.imageUrls[index];
    $('imageNumber').value=String(index);$('imageCount').textContent=`${numbers.format(index+1)} / ${numbers.format(row.images.length)}`;
  }
  function showTexture(row) {
    activeTexture=row;
    $('textureTitle').textContent=row.name;
    $('textureDetails').textContent=[Number.isFinite(row.width)&&Number.isFinite(row.height)?`${numbers.format(row.width)} × ${numbers.format(row.height)} pixels`:null,row.images.length>1?`${numbers.format(row.images.length)} images dans l’ordre de l’archive`:'Image originale'].filter(Boolean).join(' · ');
    $('textureArchive').textContent=sourceText(row);$('imageControls').hidden=row.images.length<2;$('imageNumber').max=String(row.images.length-1);
    $('textureDocument').hidden=!row.path.toLowerCase().endsWith('.json');$('textureDocument').href=row.url;
    setImage(0);
    $('textureDialog').showModal();
  }
  function makeCard(row) {
    const card=document.createElement('article');card.className='card';
    const texture=row.kind==='texture',hasPreview=texture&&row.imageUrls.length>0;const preview=document.createElement(hasPreview?'button':'div');preview.className=`preview${hasPreview?' texture preview-button':''}`;
    if(hasPreview){preview.type='button';preview.setAttribute('aria-label',`Aperçu de ${row.name}`);preview.addEventListener('click',()=>showTexture(row));const img=document.createElement('img');img.src=row.imageUrls[0];img.alt='';img.loading='lazy';img.decoding='async';img.addEventListener('error',()=>{img.remove();const text=document.createElement('span');text.className='preview-failed';text.textContent='Aperçu indisponible';preview.append(text);},{once:true});preview.append(img);}
    else {const symbol=icon(texture?'image':'model');symbol.classList.add('model-symbol');preview.append(symbol);const format=document.createElement('span');format.className='model-format';format.textContent=texture?'Données des images':row.path.toLowerCase().endsWith('.glb')?'GLB':'glTF';preview.append(format);}
    const badge=document.createElement('span');badge.className=`badge ${row.kind}`;badge.textContent=texture&&row.images.length>1?'IMAGES':labels[row.kind]||'RESSOURCE';preview.append(badge);
    const body=document.createElement('div');body.className='card-body';const name=document.createElement('h2');name.className='card-name';name.textContent=row.name;name.title=row.name;
    const source=document.createElement('div');source.className='card-source';source.textContent=sourceText(row);source.title=row.path;
    const metrics=document.createElement('div');metrics.className='card-metrics';const main=document.createElement('span');const secondary=document.createElement('span');
    if(texture){main.textContent=Number.isFinite(row.width)&&Number.isFinite(row.height)?`${numbers.format(row.width)} × ${numbers.format(row.height)}`:'Images originales';secondary.textContent=row.images.length>1?`${numbers.format(row.images.length)} images`:'PNG';}
    else {main.textContent=Number.isFinite(row.triangles)?`${numbers.format(row.triangles)} triangles`:'Modèle original';secondary.textContent=row.kind==='character'?'Pose de liaison':Number.isFinite(row.vertices)?`${numbers.format(row.vertices)} sommets`:row.kind==='lod'?'Variante de détail':'';}
    metrics.append(main,secondary);
    const action=document.createElement(hasPreview?'button':'a');action.className='card-action';action.title=row.path;
    if(hasPreview){action.type='button';action.addEventListener('click',()=>showTexture(row));action.append(icon('image'),document.createTextNode(row.images.length>1?'Voir les images':'Voir la texture'));}
    else {action.href=row.url;action.download=row.path.split('/').pop();action.append(icon('download'),document.createTextNode(texture?'Télécharger la ressource':row.path.toLowerCase().endsWith('.glb')?'Fichier GLB':'Fichier glTF'));}
    body.append(name,source,metrics,action);card.append(preview,body);return card;
  }
  function renderPage() {
    const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.max(1,Math.min(page,pages));
    const start=(page-1)*pageSize,end=Math.min(start+pageSize,filtered.length);const fragment=document.createDocumentFragment();
    for(let i=start;i<end;i++)fragment.append(makeCard(filtered[i]));$('grid').replaceChildren(fragment);$('empty').hidden=filtered.length!==0;$('scroll').scrollTop=0;
    const kind=$('kindFilter').value;const noun=kind?plural[kind]:'ressource';$('resultCount').textContent=`${numbers.format(filtered.length)} ${noun}${filtered.length!==1?'s':''}${filtered.length!==rows.length?` sur ${numbers.format(rows.length)}`:''}`;
    $('pageRange').textContent=filtered.length?`${numbers.format(start+1)}–${numbers.format(end)} sur ${numbers.format(filtered.length)}`:'Aucun résultat';
    $('pageNumber').value=String(page);$('pageNumber').max=String(pages);$('pageNumber').disabled=!filtered.length;$('pageCount').textContent=`sur ${numbers.format(pages)}`;
    $('prevPage').disabled=page<=1;$('nextPage').disabled=page>=pages;
  }
  function filter() {
    const terms=fold($('search').value.trim()).split(/\s+/).filter(Boolean),archive=$('archiveFilter').value,kind=$('kindFilter').value,sort=$('sort').value;
    filtered=rows.filter(row=>(!archive||row.archive===archive)&&(!kind||row.kind===kind)&&terms.every(term=>row.search.includes(term)));
    filtered.sort((a,b)=>{let primary=0;if(sort==='archive')primary=collator.compare(a.archive,b.archive);else if(sort==='triangles')primary=(b.triangles||0)-(a.triangles||0);else if(sort==='dimensions')primary=(b.width||0)*(b.height||0)-(a.width||0)*(a.height||0);return primary||collator.compare(a.name,b.name)||collator.compare(a.archive,b.archive)||a.index-b.index;});
    page=1;$('clearSearch').hidden=!$('search').value;renderPage();
  }
  $('search').addEventListener('input',()=>{clearTimeout(queryTimer);queryTimer=setTimeout(filter,120);});
  $('search').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();$('search').value='';clearTimeout(queryTimer);filter();}else if(event.key==='Enter'){clearTimeout(queryTimer);filter();}});
  $('clearSearch').addEventListener('click',()=>{$('search').value='';clearTimeout(queryTimer);filter();$('search').focus();});
  for(const id of ['archiveFilter','kindFilter','sort'])$(id).addEventListener('change',filter);
  $('pageSize').addEventListener('change',()=>{pageSize=Number($('pageSize').value);page=1;renderPage();});
  $('prevPage').addEventListener('click',()=>{page--;renderPage();});$('nextPage').addEventListener('click',()=>{page++;renderPage();});
  $('pageNumber').addEventListener('change',()=>{page=Number.parseInt($('pageNumber').value,10)||1;renderPage();});
  $('resetFilters').addEventListener('click',()=>{$('search').value='';$('archiveFilter').value='';$('kindFilter').value='';$('sort').value='name';clearTimeout(queryTimer);filter();});
  $('helpButton').addEventListener('click',()=>$('helpDialog').showModal());
  $('imageNumber').addEventListener('input',()=>{if(activeTexture)setImage(Number($('imageNumber').value));});
  for(const button of document.querySelectorAll('[data-close]'))button.addEventListener('click',()=>$(button.dataset.close).close());
  for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();}});
  filter();
})();
</script>
</body>
</html>
"""
