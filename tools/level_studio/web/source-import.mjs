/** Local disc import: no disc data is sent outside the localhost server. */
export function createSourceImport({ api, openDialog, onSourceOpened, translate, formatNumber, setBusy, toast, document: doc = document }) {
  let working = false;
  let currentJob = null;
  let progressHost = null;
  let progressText = null;
  const input = doc.getElementById('isoFileInput');
  const node = (tag, text, className) => {
    const el = doc.createElement(tag);
    if (text) el.textContent = translate(text);
    if (className) el.className = className;
    return el;
  };
  function progress(message, value) {
    if (progressText) progressText.textContent = translate(message);
    if (progressHost) {
      if (Number.isFinite(value)) progressHost.value = Math.max(0, Math.min(1, value));
      else progressHost.removeAttribute('value');
    }
  }
  function begin() {
    working = true; setBusy(true);
    input.value = '';
    progress('Lecture de l’ISO…');
  }
  function end() { working = false; setBusy(false); }
  async function open(id) {
    if (working) return;
    begin();
    try {
      await api('/api/open-source', { id });
      doc.getElementById('infoDialog').close();
      await onSourceOpened();
      toast('Source ouverte · le projet précédent est conservé.');
    } catch (error) { progress(error.message); toast(error.message, true); }
    finally { end(); }
  }
  async function follow(job) {
    currentJob = job.id;
    while (job.status !== 'ready' && job.status !== 'error') {
      const percent = Number.isFinite(job.progress) ? formatNumber(job.progress * 100, { maximumFractionDigits: 0 }) : '';
      progress(job.status === 'extracting' ? `${translate('Extraction locale')} · ${percent}%` : 'Lecture de l’ISO…', job.progress);
      await new Promise(resolve => setTimeout(resolve, 750));
      job = await api(`/api/import-iso?id=${encodeURIComponent(currentJob)}`);
    }
    if (job.status === 'error') throw new Error(job.error || translate('Import ISO impossible.'));
    progress('Import terminé · ouverture du jeu…', 1);
    await api('/api/open-import', { id: job.id });
    doc.getElementById('infoDialog').close();
    await onSourceOpened();
    toast('ISO importée · projet séparé ouvert.');
  }
  async function importPath(path) {
    if (working || !path.trim()) return;
    begin();
    try { await follow(await api('/api/import-iso', { path: path.trim() })); }
    catch (error) { progress(error.message); toast(error.message, true); }
    finally { end(); }
  }
  async function importFile(file) {
    if (working || !file) return;
    if (!/\.(iso|xiso)$/i.test(file.name)) { toast('Choisissez un fichier .iso.', true); return; }
    if (!file.size || file.size > 16 * 1024 ** 3) { toast('La taille de l’ISO doit être comprise entre 1 octet et 16 Go.', true); return; }
    begin();
    try {
      const job = await new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open('POST', '/api/import-iso-upload');
        request.setRequestHeader('Content-Type', 'application/octet-stream');
        request.setRequestHeader('X-File-Name', encodeURIComponent(file.name));
        request.upload.onprogress = event => {
          const value = event.lengthComputable ? event.loaded / event.total : undefined;
          progress(`${translate('Copie locale de l’ISO')} · ${formatNumber((value || 0) * 100, { maximumFractionDigits: 0 })}%`, value);
        };
        request.onerror = () => reject(new Error(translate('Le serveur local ne répond pas.')));
        request.onabort = () => reject(new Error(translate('Import interrompu.')));
        request.onload = () => {
          try {
            const result = JSON.parse(request.responseText);
            if (request.status < 200 || request.status >= 300 || result.error) reject(new Error(result.error || translate('Import ISO impossible.')));
            else resolve(result);
          } catch { reject(new Error(translate('Réponse d’import illisible.'))); }
        };
        request.send(file);
      });
      await follow(job);
    } catch (error) { progress(error.message); toast(error.message, true); }
    finally { end(); }
  }
  async function show() {
    const body = openDialog('Ouvrir le jeu', 'IMPORT ISO · PROJETS');
    body.append(node('p', 'Importez votre ISO Xbox d’Azurik. Le jeu est extrait localement et chaque import dispose de son propre projet.', 'dialog-note'));
    const row = node('div', '', 'iso-import-actions');
    const browse = node('button', 'Choisir un fichier ISO', 'button primary');
    browse.disabled = working; browse.addEventListener('click', () => input.click()); row.append(browse); body.append(row);
    const label = node('label', 'Ou saisir le chemin local de l’ISO', 'settings-row');
    const path = node('input'); path.type = 'text'; path.id = 'isoPathInput'; path.placeholder = translate('Chemin complet vers votre fichier .iso'); path.setAttribute('data-i18n-ignore', '');
    const start = node('button', 'Importer ce chemin', 'button secondary'); start.disabled = working;
    start.addEventListener('click', () => importPath(path.value));
    path.addEventListener('keydown', event => { if (event.key === 'Enter') importPath(path.value); });
    label.append(path, start); body.append(label);
    progressHost = node('progress'); progressHost.max = 1; progressHost.value = 0; progressHost.className = 'iso-progress';
    progressText = node('p', working ? 'Import en cours…' : 'Votre dump et vos projets existants sont conservés.', 'dialog-note');
    progressText.setAttribute('role', 'status'); body.append(progressHost, progressText);
    try {
      const sources = await api('/api/sources');
      const list = node('div', '', 'iso-source-list');
      if (sources.originalAvailable) {
        const original = node('button', 'Revenir au dump d’origine', 'button secondary');
        original.disabled = working || sources.active === 'original'; original.addEventListener('click', () => open('original')); list.append(original);
      }
      for (const source of sources.imports || []) {
        if (source.status !== 'ready') continue;
        const button = node('button', '', 'button secondary');
        button.textContent = `${translate('Ouvrir')} · ${source.name}`;
        button.setAttribute('data-i18n-ignore', ''); button.disabled = working || sources.active === source.id;
        button.addEventListener('click', () => open(source.id)); list.append(button);
      }
      body.append(list);
    } catch (error) { progress(error.message); }
  }
  input.addEventListener('change', () => { const file = input.files?.[0]; importFile(file); });
  return { show, get working() { return working; } };
}
