"""Reversible project-owned levels and native registry export plans."""
from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

FAMILIES = {'air', 'water', 'earth', 'fire', 'death', 'life', 'perathia', 'cinematic'}
CUSTOM_ID = re.compile(r'[a-z][a-z0-9_]{0,39}')
LEVEL_ID = re.compile(r'[A-Za-z0-9_-]{1,63}')
HASH = re.compile(r'[a-f0-9]{64}')
TECHNICAL_LEVELS = {'selector', 'training_room'}
LEVEL_WARNING = ('Les niveaux créés reprennent la géométrie, les collisions, les scripts et les identifiants '
                 'du modèle source. Le registre natif est exporté; le fonctionnement complet doit être testé dans le jeu.')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ProjectLevels:
    def _initialize_level_management(self):
        manager = self._project.setdefault('levelManagement', {'created': {}, 'deleted': {}, 'undo': [], 'redo': []})
        if (not isinstance(manager, dict) or set(manager) != {'created', 'deleted', 'undo', 'redo'}
                or any(not isinstance(manager[k], dict) for k in ('created', 'deleted'))
                or any(not isinstance(manager[k], list) for k in ('undo', 'redo'))
                or len(manager['created']) > 128 or len(manager['deleted']) > 1024
                or any(len(manager[k]) > 128 for k in ('undo', 'redo'))):
            raise ValueError('Gestion des niveaux invalide dans le projet.')
        for identifier, record in manager['created'].items():
            if (not isinstance(identifier, str) or not CUSTOM_ID.fullmatch(identifier)
                    or not isinstance(record, dict)
                    or set(record) != {'name', 'family', 'template', 'archive', 'sourceHash'}
                    or not isinstance(record['name'], str) or not 1 <= len(record['name']) <= 80
                    or not isinstance(record['family'], str) or record['family'] not in FAMILIES
                    or not isinstance(record['template'], str) or not LEVEL_ID.fullmatch(record['template'])
                    or not isinstance(record['sourceHash'], str) or not HASH.fullmatch(record['sourceHash'])
                    or record['archive'] != record['sourceHash'] + '.xbr'):
                raise ValueError('Définition de niveau personnalisé invalide.')
        sources = self._project.setdefault('levelSources', {})
        if not isinstance(sources, dict) or len(sources) > 1024:
            raise ValueError('Archives de l’historique des niveaux invalides.')
        for identifier, record in manager['created'].items():
            if identifier in sources and sources[identifier] != record:
                raise ValueError('Identifiant de niveau archivé ambigu.')
            sources.setdefault(identifier, copy.deepcopy(record))
        for identifier, record in sources.items():
            if (not isinstance(identifier, str) or not CUSTOM_ID.fullmatch(identifier)
                    or not isinstance(record, dict) or not isinstance(record.get('sourceHash'), str)
                    or not HASH.fullmatch(record['sourceHash']) or record.get('archive') != record['sourceHash'] + '.xbr'):
                raise ValueError('Archive de l’historique invalide.')
        for identifier, destination in manager['deleted'].items():
            if (not isinstance(identifier, str) or not LEVEL_ID.fullmatch(identifier)
                    or not isinstance(destination, str) or not LEVEL_ID.fullmatch(destination)
                    or identifier == destination or identifier in TECHNICAL_LEVELS):
                raise ValueError('Redirection de niveau invalide.')
            self._level_destination(identifier)

    def _level_manager(self):
        return self._project['levelManagement']

    def _level_destination(self, identifier):
        deleted, visited = self._level_manager()['deleted'], set()
        while identifier in deleted:
            if identifier in visited:
                raise ValueError('Cycle dans les redirections de niveaux.')
            visited.add(identifier)
            identifier = deleted[identifier]
        return identifier

    def _managed_source_path(self, identifier):
        record = self._project.get('levelSources', {}).get(identifier)
        if not record:
            return None
        root = (self.project_dir / 'levels').resolve()
        path = (root / record['archive']).resolve()
        if (not path.is_relative_to(root) or not path.is_file()
                or path.stat().st_size > 256 * 1024 * 1024):
            raise ValueError('Archive du niveau personnalisé absente ou trop volumineuse.')
        return path

    def _level_name(self, identifier, fallback=None):
        return self._level_manager()['created'].get(identifier, {}).get('name', fallback or identifier)

    def _level_active(self, identifier):
        return (identifier not in self._level_manager()['deleted'] and
                (identifier in self._level_manager()['created'] or (self.gamedata_dir / (identifier + '.xbr')).is_file()))

    def _template_origin(self, identifier):
        created, visited = self._level_manager()['created'], set()
        while identifier in created:
            if identifier in visited:
                raise ValueError('Cycle dans les modèles de niveaux.')
            visited.add(identifier)
            identifier = created[identifier]['template']
        return identifier

    def level_management(self):
        manager = self._level_manager()
        return {'created': [{'id': key, **value} for key, value in sorted(manager['created'].items())],
                'deleted': [{'id': key, 'replacement': self._level_destination(key),
                             'name': self._level_name(key)} for key in sorted(manager['deleted'])],
                'canUndo': bool(manager['undo']), 'canRedo': bool(manager['redo']),
                'changeCount': len(manager['created']) + len(manager['deleted'])}

    def _catalog_snapshot(self):
        manager = self._level_manager()
        return copy.deepcopy({'created': manager['created'], 'deleted': manager['deleted'],
                              'levels': self._project['levels'], 'levelSources': self._project.get('levelSources', {})})

    def _apply_catalog_snapshot(self, snapshot, preserve_history=True):
        if not isinstance(snapshot, dict) or set(snapshot) != {'created', 'deleted', 'levels', 'levelSources'}:
            raise ValueError('Historique de niveaux invalide.')
        manager = self._level_manager()
        manager['created'], manager['deleted'] = copy.deepcopy(snapshot['created']), copy.deepcopy(snapshot['deleted'])
        if preserve_history:
            current = self._project['levels']
            for identifier, state in snapshot['levels'].items():
                stacks = {key: current.get(identifier, state).get(key, []) for key in ('undo', 'redo')}
                current[identifier] = copy.deepcopy(state)
                current[identifier].update(stacks)
            # Inactive custom levels retain their history and immutable archive
            # so redo can replay each later item operation after recreating them.
            self._project['levelSources'].update(copy.deepcopy(snapshot['levelSources']))
        else:
            self._project['levels'] = copy.deepcopy(snapshot['levels'])
            self._project['levelSources'] = copy.deepcopy(snapshot['levelSources'])
        self._initialize_level_management()
        self._cache.clear()
        self._cache_stamps.clear()
        getattr(self, '_prepared_scenes', {}).clear()

    def _seed_history_order(self):
        serial = self._project.get('historySerial', 0)
        if type(serial) is not int or not 0 <= serial <= 10**12:
            raise ValueError('Ordre de l’historique invalide.')
        stacks = [state[k] for state in self._project['levels'].values() for k in ('undo', 'redo')]
        stacks += [self._level_manager()[k] for k in ('undo', 'redo')]
        for stack in stacks:
            for row in stack:
                if not isinstance(row, dict):
                    continue  # Existing operation validation reports this when used.
                value = row.get('_seq')
                if value is None:
                    serial += 1
                    row['_seq'] = serial
                elif type(value) is not int or not 0 < value <= 10**12:
                    raise ValueError('Ordre d’opération invalide.')
                else:
                    serial = max(serial, value)
        self._project['historySerial'] = serial

    def _clear_global_redo(self):
        self._level_manager()['redo'].clear()
        for state in self._project['levels'].values():
            state['redo'].clear()

    def _record_history_order(self):
        # New item operations have no sequence yet. Undo/redo retain theirs.
        if any(isinstance(row, dict) and '_seq' not in row for state in self._project['levels'].values()
               for row in state['undo']):
            self._clear_global_redo()
        self._seed_history_order()

    def _history_candidate(self, undo):
        key = 'undo' if undo else 'redo'
        candidates = []
        for level, state in self._project['levels'].items():
            if state[key]:
                candidates.append((state[key][-1].get('_seq', 0), 'item', level))
        stack = self._level_manager()[key]
        if stack:
            candidates.append((stack[-1].get('_seq', 0), 'catalog', None))
        return (max(candidates) if undo else min(candidates)) if candidates else None

    def _combined_history(self):
        return {'canUndo': self._history_candidate(True) is not None,
                'canRedo': self._history_candidate(False) is not None}

    def _level_result(self, active=None, changed=True):
        available = {row['id'] for row in self.catalog()}
        if active not in available:
            active = next(iter(sorted(available)), None)
        return {'changed': changed, 'catalogChanged': True, 'activeLevel': active,
                'levelManagement': self.level_management(), **self._combined_history(),
                'pendingCount': self.pending_count(), 'previewCount': self.preview_count()}

    def _catalog_commit(self, before, action, active):
        after = self._catalog_snapshot()
        if before == after:
            return self._level_result(active, False)
        manager = self._level_manager()
        previous_redo = copy.deepcopy(manager['redo'])
        previous_undo = copy.deepcopy(manager['undo'])
        previous_version = self._project['version']
        previous_serial = self._project['historySerial']
        self._clear_global_redo()
        after = self._catalog_snapshot()
        self._project['historySerial'] += 1
        manager['undo'].append({'action': action, 'before': before, 'after': after,
                                'activeLevel': active, '_seq': self._project['historySerial']})
        self._project['version'] = 3
        if len(manager['undo']) > 128:
            del manager['undo'][:-128]
        try:
            self.save()
        except Exception:
            manager['undo'] = previous_undo
            manager['redo'] = previous_redo
            self._apply_catalog_snapshot(before, preserve_history=False)
            self._project['version'] = previous_version
            self._project['historySerial'] = previous_serial
            raise
        return self._level_result(active)

    def create_level(self, template, identifier, display_name, family='perathia'):
        from level_archive import clone_level, read_index
        with self._lock:
            if (not isinstance(identifier, str) or not CUSTOM_ID.fullmatch(identifier)
                    or identifier.upper() in {'CON', 'PRN', 'AUX', 'NUL', 'ALWAYS', 'DEFAULT'}
                    or re.fullmatch(r'(?:com|lpt)[1-9]', identifier)):
                raise ValueError('Utilisez un identifiant unique : lettres minuscules, chiffres et _ (40 caractères).')
            if (not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 80
                    or any(ord(c) < 32 for c in display_name)
                    or not isinstance(family, str) or family not in FAMILIES):
                raise ValueError('Nom ou royaume du niveau invalide.')
            if len(self._level_manager()['created']) >= 128:
                raise ValueError('Le projet contient déjà 128 niveaux personnalisés.')
            if identifier in self._project['levelSources'] or any(p.stem.casefold() == identifier for p in self.gamedata_dir.glob('*.xbr')):
                raise ValueError('Cet identifiant est déjà utilisé dans le jeu ou le projet.')
            if not isinstance(template, str) or template not in {row['id'] for row in self.catalog()}:
                raise ValueError('Niveau modèle introuvable.')
            index_path = self.gamedata_dir / 'index/index.xbr'
            entries = read_index(index_path.read_bytes())
            original = self._source_path(template).read_bytes()
            if template in self._level_manager()['created'] and sha(original) != self._level_manager()['created'][template]['sourceHash']:
                raise ValueError('Le niveau modèle personnalisé a changé.')
            cloned, registrations = clone_level(original, template, identifier)
            if any((row['tag'], row['key']) in {(e['tag'], e['key']) for e in entries} for row in registrations):
                raise ValueError('Une ressource portant ce nom existe déjà dans le registre du jeu.')
            digest = sha(cloned)
            root = self.project_dir / 'levels'
            root.mkdir(parents=True, exist_ok=True)
            archive = root / (digest + '.xbr')
            if archive.exists():
                if sha(archive.read_bytes()) != digest:
                    raise ValueError('Archive personnalisée altérée.')
            else:
                with archive.open('xb') as stream:
                    stream.write(cloned)
            before = self._catalog_snapshot()
            self._level_manager()['created'][identifier] = {'name': display_name.strip(), 'family': family,
                'template': self._template_origin(template), 'archive': archive.name, 'sourceHash': digest}
            self._project['levelSources'][identifier] = copy.deepcopy(self._level_manager()['created'][identifier])
            state = self._state(identifier)
            state['sourceHash'] = digest
            return self._catalog_commit(before, 'create', identifier)

    def delete_level(self, level, replacement):
        with self._lock:
            available = {row['id'] for row in self.catalog()}
            if not isinstance(level, str) or not isinstance(replacement, str):
                raise ValueError('Choisissez un autre niveau conservé pour rediriger les entrées du niveau supprimé.')
            if level in TECHNICAL_LEVELS:
                raise ValueError('Le sélecteur et la salle d’entraînement sont des niveaux techniques conservés par le jeu.')
            if level not in available or replacement not in available or level == replacement:
                raise ValueError('Choisissez un autre niveau conservé pour rediriger les entrées du niveau supprimé.')
            if self._template_origin(level) != self._template_origin(replacement):
                raise ValueError('Créez d’abord un niveau depuis ce modèle pour conserver les points d’entrée du jeu.')
            before = self._catalog_snapshot()
            self._level_manager()['deleted'][level] = replacement
            return self._catalog_commit(before, 'delete', replacement)

    def restore_level(self, level):
        with self._lock:
            if not isinstance(level, str):
                raise ValueError('Identifiant de niveau invalide.')
            if level not in self._level_manager()['deleted']:
                return self._level_result(level, False)
            before = self._catalog_snapshot()
            del self._level_manager()['deleted'][level]
            return self._catalog_commit(before, 'restore', level)

    def level_history(self, undo=True):
        with self._lock:
            candidate = self._history_candidate(undo)
            if candidate is None:
                return self._level_result(changed=False)
            if candidate[1] != 'catalog':
                raise ValueError('Annulez d’abord les modifications récentes avec Ctrl Z, puis la gestion des niveaux.')
            manager = self._level_manager()
            source, destination = (manager['undo'], manager['redo']) if undo else (manager['redo'], manager['undo'])
            row = source[-1]
            current = self._catalog_snapshot()
            self._apply_catalog_snapshot(row['before'] if undo else row['after'])
            destination.append(source.pop())
            try:
                self.save()
            except Exception:
                source.append(destination.pop())
                self._apply_catalog_snapshot(current, preserve_history=False)
                raise
            return self._level_result(row.get('activeLevel'))

    def _global_history(self, level, undo):
        with self._lock:
            candidate = self._history_candidate(undo)
            if candidate and candidate[1] == 'catalog':
                return self.level_history(undo)
            target = candidate[2] if candidate else level
            result = self._history(target, undo)
            result['activeLevel'] = target
            result.update(self._combined_history())
            return result

    def _prepare_level_export(self, prepared):
        from level_archive import alias_level, named_resources, read_index, update_index
        manager = self._level_manager()
        files = dict(prepared)
        deleted, created = manager['deleted'], manager['created']
        plan = {'format': 'azurik-level-iso', 'version': 1, 'gameFiles': [], 'removedFiles': []}
        plan['levelOperations'] = {
            'created': [{'id': level, 'template': record['template']} for level, record in sorted(created.items()) if level not in deleted],
            'deleted': [{'id': level, 'replacement': self._level_destination(level)} for level in sorted(deleted)]}
        if created or deleted:
            index_path = self.gamedata_dir / 'index/index.xbr'
            original_index = index_path.read_bytes()
            index = read_index(original_index)
            indexed = {(row['tag'], row['key']): row['archive'].casefold() for row in index}
            add, remove, aliases = [], [], {}
            for level, record in created.items():
                if level in deleted:
                    continue
                payload = self._source_path(level).read_bytes()
                if sha(payload) != record['sourceHash']:
                    raise ValueError('Archive du niveau personnalisé altérée.')
                files.setdefault(level, payload)
                registrations = named_resources(payload)
                if any((row['tag'], row['key']) in indexed for row in registrations):
                    raise ValueError('Export annulé : une ressource personnalisée existe déjà dans le registre source.')
                add.extend({'key': row['key'], 'tag': row['tag'], 'archive': level + '.xbr'}
                           for row in registrations)
            for level in deleted:
                payload = self._source_path(level).read_bytes()
                destination = self._level_destination(level)
                self._source_path(destination)
                rows = [row for row in named_resources(payload) if row['tag'] == 'levl']
                if not rows:
                    raise ValueError('Niveau supprimé sans ressource LEVL enregistrable.')
                if level not in created and any(indexed.get((row['tag'], row['key'])) != (level + '.xbr').casefold() for row in rows):
                    raise ValueError('Export annulé : le registre du niveau supprimé a changé.')
                keys = [row['key'] for row in rows]
                aliases.setdefault(destination, []).extend(keys)
                remove.extend({'key': row['key'], 'tag': row['tag']} for row in rows)
                files.pop(level, None)
                if level not in created and not any(e['archive'].casefold() == (level + '.xbr').casefold()
                                                  and e['tag'] != 'levl' for e in index):
                    plan['removedFiles'].append({'path': 'gamedata/' + level + '.xbr', 'sourceSha256': sha(payload)})
            for level, keys in aliases.items():
                payload = files.get(level, self._source_path(level).read_bytes())
                target = next((row['key'] for row in named_resources(payload) if row['tag'] == 'levl'), None)
                files[level] = alias_level(payload, sorted(set(keys)), target_key=target)
                add.extend({'key': key, 'tag': 'levl', 'archive': level + '.xbr'} for key in sorted(set(keys)))
            files['index/index'] = update_index(original_index, add=add, remove=remove)
        for level, payload in sorted(files.items()):
            original = self.gamedata_dir / (level + '.xbr')
            plan['gameFiles'].append({'path': 'gamedata/' + level + '.xbr', 'sha256': sha(payload),
                                      'sourceSha256': sha(original.read_bytes()) if original.is_file() else None})
        return files, plan
