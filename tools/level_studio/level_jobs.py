"""Background ISO construction with progress for the desktop editor."""
from pathlib import Path
import copy
import json
import threading
from uuid import uuid4


class LevelBuildJobs:
    _process_lock = threading.RLock()
    _active_build = None
    def __init__(self):
        self._lock = threading.RLock()
        self._jobs = {}

    def status(self, identifier):
        with self._lock:
            if not isinstance(identifier, str) or identifier not in self._jobs:
                raise ValueError('Construction d’ISO inconnue.')
            return copy.deepcopy(self._jobs[identifier])

    def start(self, directory, input_path, output_path, exports_root, source_root):
        if any(not isinstance(value, str) or not value.strip() for value in (directory, input_path, output_path)):
            raise ValueError('Choisissez l’export, l’ISO source et une nouvelle ISO de destination.')
        export = Path(directory).resolve()
        source, output = Path(input_path).resolve(), Path(output_path).resolve()
        report = output.with_name(output.name + '.report.json')
        if not export.is_relative_to(Path(exports_root).resolve()) or not (export / 'iso-plan.json').is_file():
            raise ValueError('Choisissez un dossier exporté par ce projet.')
        if output.is_relative_to(Path(source_root).resolve()):
            raise ValueError('L’ISO de destination doit être séparée du dump source.')
        if source == output or output.exists() or report.exists():
            raise ValueError('Choisissez un nouveau fichier; les ISO et rapports existants sont conservés.')
        if not source.is_file() or source.suffix.lower() not in ('.iso', '.xiso'):
            raise ValueError('L’ISO source est introuvable.')
        if output.suffix.lower() not in ('.iso', '.xiso') or not output.parent.is_dir():
            raise ValueError('Le dossier de destination doit exister et le fichier porter l’extension .iso.')
        with self._process_lock, self._lock:
            if LevelBuildJobs._active_build is not None:
                raise ValueError('Une ISO est déjà en construction. Attendez sa vérification.')
            identifier = uuid4().hex
            self._jobs[identifier] = {'id': identifier, 'status': 'queued', 'progress': 0,
                                      'message': 'Préparation de l’ISO', 'output': str(output)}
            LevelBuildJobs._active_build = identifier
            try:
                threading.Thread(target=self._run, args=(identifier, source, export, output, report), daemon=True).start()
            except Exception:
                LevelBuildJobs._active_build = None
                del self._jobs[identifier]
                raise
            return self.status(identifier)

    def _run(self, identifier, source, export, output, report):
        def progress(percent, message):
            with self._lock:
                self._jobs[identifier].update(status='running', progress=percent, message=message)
        try:
            from level_iso import build_iso
            result = build_iso(source, export, output, progress=progress)
            # The game writer verifies the whole image before returning.
            try:
                with report.open('x', encoding='utf-8') as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
            except OSError as exc:
                # Preserve a verified image even if only its report could not be saved.
                result['reportWarning'] = str(exc)
            with self._lock:
                self._jobs[identifier].update(status='ready', progress=100,
                    message='ISO construite et vérifiée', report=result, reportPath=str(report))
        except Exception as exc:
            with self._lock:
                self._jobs[identifier].update(status='failed', error=str(exc), message=str(exc))
        finally:
            with self._process_lock:
                if LevelBuildJobs._active_build == identifier:
                    LevelBuildJobs._active_build = None
