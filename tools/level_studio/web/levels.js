/** Project level management. The backend owns native registration and safe ISO writes. */
import { catalogEntries } from './level-catalog.mjs';
export const LEVEL_FAMILIES = [
  ['air', 'Air'], ['water', 'Eau'], ['earth', 'Terre'], ['fire', 'Feu'],
  ['death', 'Mort'], ['life', 'Vie'], ['perathia', 'Perathia'], ['cinematic', 'Cinématique'],
];

export function validateLevelDraft(value, existing = []) {
  const draft = {
    template: String(value.template || '').trim(), id: String(value.id || '').trim(),
    name: String(value.name || '').trim(), family: String(value.family || '').trim(),
  };
  if (!/^[a-z][a-z0-9_]{0,39}$/u.test(draft.id)
      || /^(?:always|default|con|prn|aux|nul|(?:com|lpt)[1-9])$/u.test(draft.id)) {
    throw new Error('L’identifiant doit contenir de 1 à 40 caractères : lettres minuscules, chiffres ou tirets bas, avec une lettre au début.');
  }
  if (!draft.name || [...draft.name].length > 80 || /[\u0000-\u001f\u007f]/u.test(draft.name)) {
    throw new Error('Le nom doit contenir de 1 à 80 caractères.');
  }
  if (!existing.some(level => level.id === draft.template)) throw new Error('Choisissez un niveau modèle disponible.');
  if (existing.some(level => level.id === draft.id)) throw new Error('Cet identifiant de niveau existe déjà.');
  if (!LEVEL_FAMILIES.some(([id]) => id === draft.family)) throw new Error('Choisissez une famille de niveaux.');
  return draft;
}

export function createLevelManager({ api, openDialog, closeDialog, toast, getContext = () => ({}),
  refreshCatalog, translate = value => value, onChanged }) {
  let working = false, session = 0, controls = [], buildJob = null, operation = 0, workKind = null;
  const context = () => getContext() || {};
  const notify = async (result, action, level) => {
    if (onChanged) await onChanged(result, { action, level });
    else if (refreshCatalog) await refreshCatalog(result);
  };
  const setWorking = value => {
    working = value;
    for (const [control, disabled] of controls) control.disabled = disabled || value;
  };
  const acquire = kind => { workKind = kind; setWorking(true); return ++operation; };
  const release = token => { if (token === operation) { workKind = null; setWorking(false); } };
  function start(title, cancelLabel = 'Annuler et fermer') {
    const current = ++session;
    controls = [];
    const body = openDialog(translate(title), translate('GESTION DES NIVEAUX'));
    const doc = body.ownerDocument || globalThis.document;
    const active = () => current === session && body.isConnected !== false
      && (body.closest?.('dialog')?.open ?? true);
    const node = (tag, text = '', className = '', raw = false) => {
      const element = doc.createElement(tag);
      if (text) element.textContent = raw ? text : translate(text);
      if (raw) element.setAttribute('data-i18n-ignore', '');
      if (className) element.className = className;
      return element;
    };
    const button = (text, handler, primary = false, disabled = false) => {
      const element = node('button', text, `button ${primary ? 'primary' : 'secondary'}`);
      element.type = 'button'; element.disabled = disabled || working;
      controls.push([element, disabled]); element.addEventListener('click', handler);
      return element;
    };
    const field = (parent, text, input, id) => {
      input.id = id; input.setAttribute('data-i18n-ignore', '');
      const label = node('label'); label.htmlFor = id;
      label.append(node('span', text), input); parent.append(label);
      controls.push([input, false]); input.disabled = working;
      return input;
    };
    const error = node('p', '', 'asset-import-error'); error.setAttribute('role', 'alert');
    const report = failure => {
      if (!active()) return;
      error.textContent = translate(String(failure?.message || failure));
      toast?.(error.textContent, true);
    };
    const cancel = button(cancelLabel, () => { ++session; closeDialog(); });
    controls.pop(); cancel.disabled = false;
    return { body, node, button, field, error, report, cancel, active };
  }
  function options(view, select, levels) {
    for (const level of levels) {
      const option = view.node('option', `${level.name || level.label || level.id} · ${level.id}`, '', true);
      option.value = level.id; select.append(option);
    }
    if (levels.length) select.value = levels[0].id;
  }
  async function catalogFor(view) {
    try {
      const catalog = await api('/api/catalog');
      if (!view.active()) return null;
      return { ...catalog, levels: Array.isArray(catalog.levels) ? catalog.levels : [],
        levelManagement: catalog.levelManagement || {} };
    } catch (error) { view.report(error); return null; }
  }
  async function mutate(view, action, payload, message, keepOpen = false) {
    if (working || !view.active()) return null;
    const token = acquire('levels'); view.error.textContent = '';
    try {
      const result = await api(`/api/levels/${action}`, payload);
      await notify(result, action, payload.level || result.activeLevel || payload.id);
      toast?.(translate(message));
      if (view.active() && !keepOpen) { ++session; closeDialog(); }
      return result;
    } catch (error) { view.report(error); return null; }
    finally { release(token); }
  }
  async function showCreate() {
    const view = start('Créer un niveau depuis un modèle');
    view.body.append(view.node('p', 'Le nouveau niveau copie le niveau modèle depuis les fichiers d’origine : les modifications actuelles du projet ne sont pas copiées.', 'dialog-note'));
    view.body.append(view.error);
    const catalog = await catalogFor(view);
    if (!catalog) return;
    const templates = catalogEntries(catalog.levels, 'levels').filter(level => level.canClone !== false);
    if (!templates.length) {
      view.body.append(view.node('p', 'Importez votre jeu pour créer un niveau.'), view.cancel); return;
    }
    const form = view.node('form', '', 'asset-import-form');
    const template = view.field(form, 'Niveau modèle d’origine', view.node('select'), 'levelTemplate');
    options(view, template, templates);
    if (templates.some(level => level.id === context().level)) template.value = context().level;
    const id = view.field(form, 'Identifiant du nouveau niveau', view.node('input'), 'newLevelId');
    id.type = 'text'; id.maxLength = 40; id.required = true; id.pattern = '[a-z][a-z0-9_]{0,39}';
    id.placeholder = translate('Exemple : mon_niveau'); id.autocomplete = 'off'; id.spellcheck = false;
    const name = view.field(form, 'Nom du niveau', view.node('input'), 'newLevelName');
    name.type = 'text'; name.maxLength = 80; name.required = true;
    const family = view.field(form, 'Famille du niveau', view.node('select'), 'newLevelFamily');
    for (const [value, text] of LEVEL_FAMILIES) {
      const option = view.node('option', text); option.value = value; family.append(option);
    }
    const chooseFamily = () => {
      const value = catalog.levels.find(level => level.id === template.value)?.family;
      family.value = LEVEL_FAMILIES.some(([key]) => key === value) ? value : 'perathia';
    };
    chooseFamily(); template.addEventListener('change', chooseFamily);
    const submit = async event => {
      event?.preventDefault(); if (working) return;
      try {
        const deleted = (catalog.levelManagement.deleted || []).map(level => typeof level === 'string' ? { id: level } : level);
        const payload = validateLevelDraft({ template: template.value, id: id.value, name: name.value, family: family.value }, catalog.levels);
        if (deleted.some(level => level.id === payload.id)) throw new Error('Cet identifiant est réservé à un niveau supprimé. Restaurez-le ou choisissez un autre identifiant.');
        await mutate(view, 'create', payload, 'Niveau créé depuis le modèle d’origine.');
      } catch (error) { view.report(error); }
    };
    form.addEventListener('submit', submit);
    const actions = view.node('div', '', 'asset-import-actions');
    actions.append(view.cancel, view.button('Créer le niveau', submit, true));
    form.append(actions); view.body.append(form);
  }
  async function showDelete(levelId = context().level) {
    const view = start('Supprimer un niveau du mod');
    view.body.append(view.node('p', 'Les fichiers source restent intacts. Le niveau sera retiré du mod et ses entrées seront redirigées vers le niveau de remplacement choisi. Vous pourrez annuler cette opération.', 'dialog-note'), view.error);
    const catalog = await catalogFor(view);
    if (!catalog) return;
    const selected = catalog.levels.find(level => level.id === levelId);
    if (selected?.technical) {
      view.body.append(view.node('p', 'Le sélecteur et la salle d’entraînement sont des niveaux techniques conservés par le jeu.'), view.cancel); return;
    }
    if (!selected || catalog.levels.length < 2) {
      view.body.append(view.node('p', selected ? 'Le dernier niveau ne peut pas être supprimé.' : 'Choisissez un niveau disponible.'), view.cancel); return;
    }
    const destinations = catalog.levels.filter(level => level.id !== selected.id
      && (selected.templateOrigin === undefined || level.templateOrigin === selected.templateOrigin));
    if (!destinations.length) {
      view.body.append(view.node('p', 'Créez d’abord un niveau depuis ce modèle pour conserver les points d’entrée du jeu.'), view.cancel); return;
    }
    view.body.append(view.node('p', `${selected.name || selected.label || selected.id} · ${selected.id}`, '', true));
    const form = view.node('form', '', 'asset-import-form');
    const replacement = view.field(form, 'Niveau de remplacement conservé', view.node('select'), 'replacementLevel');
    options(view, replacement, destinations);
    const submit = async event => {
      event?.preventDefault(); if (working) return;
      if (!destinations.some(level => level.id === replacement.value)) {
        view.report(new Error('Choisissez un niveau de remplacement conservé.')); return;
      }
      await mutate(view, 'delete', { level: selected.id, replacement: replacement.value }, 'Niveau supprimé du mod · opération annulable.');
    };
    form.addEventListener('submit', submit);
    const actions = view.node('div', '', 'asset-import-actions');
    actions.append(view.cancel, view.button('Supprimer du mod', submit, true));
    form.append(actions); view.body.append(form);
  }
  async function showManage() {
    const view = start('Gérer les niveaux');
    view.body.append(view.node('p', 'Créez un niveau depuis un modèle d’origine, supprimez-le du mod ou restaurez un niveau supprimé. Ces opérations font partie de l’historique du projet.', 'dialog-note'), view.error);
    const catalog = await catalogFor(view);
    if (!catalog) return;
    const playable = catalogEntries(catalog.levels, 'levels');
    const actions = view.node('div', '', 'asset-import-actions');
    const history = async action => {
      const result = await mutate(view, action, {}, action === 'undo' ? 'Opération sur les niveaux annulée.' : 'Opération sur les niveaux rétablie.', true);
      if (result && view.active()) await showManage();
    };
    actions.append(view.button('Créer un niveau depuis un modèle', showCreate, true, !playable.some(level => level.canClone !== false)),
      view.button('Annuler une opération de niveau', () => history('undo'), false, !catalog.levelManagement.canUndo),
      view.button('Rétablir une opération de niveau', () => history('redo'), false, !catalog.levelManagement.canRedo));
    view.body.append(actions);
    const list = view.node('div', '', 'iso-source-list');
    for (const level of playable) {
      const row = view.node('div');
      row.append(view.node('p', `${level.name || level.label || level.id} · ${level.id}`, '', true),
        view.button('Supprimer du mod', () => showDelete(level.id), false, playable.length < 2 || level.technical || level.canClone === false));
      list.append(row);
    }
    view.body.append(list, view.node('h3', 'Niveaux supprimés'));
    const deleted = catalog.levelManagement.deleted || [];
    if (!deleted.length) view.body.append(view.node('p', 'Aucun niveau supprimé.'));
    for (const entry of deleted) {
      const level = typeof entry === 'string' ? { id: entry } : entry;
      const row = view.node('div', '', 'asset-import-actions');
      const label = level.name || level.id;
      row.append(view.node('span', `${label} · ${level.id}`, '', true), view.button('Restaurer le niveau supprimé', async () => {
        const result = await mutate(view, 'restore', { level: level.id }, 'Niveau supprimé restauré.', true);
        if (result && view.active()) await showManage();
      }));
      view.body.append(row);
    }
    const close = view.button('Revenir au niveau', () => { ++session; closeDialog(); });
    controls.pop(); close.disabled = false; view.body.append(close);
  }
  async function showBuildIso(directory = context().exportDirectory || '') {
    const view = start('Construire une ISO du mod', 'Fermer');
    view.body.append(view.node('p', 'Exportez le mod, puis indiquez son dossier et votre ISO source. Une nouvelle ISO sera créée ; l’ISO source et les fichiers de sauvegarde sont conservés. Testez ensuite le résultat dans le jeu.', 'dialog-note'));
    const form = view.node('form', '', 'asset-import-form');
    const pathField = (text, id, value) => {
      const input = view.field(form, text, view.node('input'), id);
      input.type = 'text'; input.required = true; input.value = value; input.spellcheck = false;
      return input;
    };
    const exportDirectory = pathField('Dossier du mod exporté', 'isoExportDirectory', directory);
    const source = pathField('Chemin de l’ISO source', 'isoSourcePath', context().sourceIsoPath || '');
    const output = pathField('Chemin de la nouvelle ISO', 'isoOutputPath', '');
    const suggestedOutput = () => { if (!output.value && /\.(iso|xiso)$/iu.test(source.value.trim())) output.value = source.value.trim().replace(/\.(iso|xiso)$/iu, '_modded.iso'); };
    suggestedOutput(); source.addEventListener('change', suggestedOutput);
    const progress = view.node('progress', '', 'iso-progress'); progress.max = 1; progress.value = 0;
    const status = view.node('p', 'Construction locale · aucune donnée envoyée en ligne.'); status.setAttribute('role', 'status');
    const resultPath = view.node('code', '', 'export-path', true); resultPath.hidden = true;
    const showProgress = job => {
      status.textContent = translate(job.message || 'Construction de l’ISO en cours…');
      if (Number.isFinite(job.progress)) progress.value = Math.max(0, Math.min(1, job.progress / 100));
      else progress.removeAttribute('value');
    };
    const pending = job => ['queued', 'running', 'building'].includes(job.status);
    const validJob = job => {
      if (!job || !['queued', 'running', 'building', 'ready', 'completed', 'error', 'failed'].includes(job.status)) {
        throw new Error('La construction a renvoyé un état de suivi invalide.');
      }
      return job;
    };
    async function follow(job) {
      buildJob = validJob(job);
      while (pending(job) && view.active()) {
        if (!job.id) throw new Error('La construction n’a pas renvoyé d’identifiant de suivi.');
        showProgress(job);
        await new Promise(resolve => setTimeout(resolve, 750));
        if (!view.active()) return;
        job = validJob(await api(`/api/build-iso?id=${encodeURIComponent(job.id)}`));
        buildJob = job;
      }
      if (!view.active()) return;
      if (['error', 'failed'].includes(job.status)) throw new Error(job.error || 'Construction de l’ISO impossible.');
      progress.value = 1; status.textContent = translate('ISO créée · vérifiez le mod dans le jeu.');
      resultPath.textContent = String(job.output || job.path || output.value.trim()); resultPath.hidden = false;
      toast?.(translate('Nouvelle ISO créée.'));
    }
    async function build(event) {
      event?.preventDefault(); if (working || !view.active()) return;
      const payload = { directory: exportDirectory.value.trim(), input: source.value.trim(), output: output.value.trim() };
      const normalized = value => value.replace(/\\/gu, '/').toLowerCase();
      if (!payload.directory || /\u0000/u.test(payload.directory) || !/\.(iso|xiso)$/iu.test(payload.input)
        || !/\.(iso|xiso)$/iu.test(payload.output) || /\u0000/u.test(payload.input + payload.output)) {
        view.report(new Error('Indiquez un dossier de mod et deux chemins de fichiers ISO valides.')); return;
      }
      if (normalized(payload.input) === normalized(payload.output)) {
        view.report(new Error('La nouvelle ISO doit avoir un chemin différent de l’ISO source.')); return;
      }
      const token = acquire('build'); view.error.textContent = '';
      try { await follow(await api('/api/build-iso', payload)); }
      catch (error) { view.report(error); }
      finally { release(token); }
    }
    form.addEventListener('submit', build);
    const actions = view.node('div', '', 'asset-import-actions');
    actions.append(view.cancel, view.button('Construire la nouvelle ISO', build, true));
    form.append(actions); view.body.append(form, progress, status, resultPath, view.error);
    if (buildJob && pending(buildJob) && (!working || workKind === 'build')) {
      const token = acquire('build');
      try { await follow(buildJob); } catch (error) { view.report(error); }
      finally { release(token); }
    }
  }
  return { showCreate, showDelete, showManage, showBuildIso, get working() { return working; } };
}
