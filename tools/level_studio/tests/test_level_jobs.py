"""ISO job state and loopback API contracts; writer mocked, no game ISO used."""
import copy
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import level_iso
import level_jobs
from level_jobs import LevelBuildJobs
from server import StudioServer
from test_level_project import level_backend, source_bytes
from test_server import StubBackend, request


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    queued = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            self.target, self.args, self.started = target, args, False
            assert daemon is True

        def start(self):
            queued.append(self)

        def run(self):
            if not self.started:
                self.started = True
                self.target(*self.args)

    monkeypatch.setattr(level_jobs, "threading", SimpleNamespace(Thread=DeferredThread, RLock=threading.RLock))
    exports = tmp_path / "exports"
    export = exports / "fresh"
    export.mkdir(parents=True)
    (export / "iso-plan.json").write_text('{"format":"azurik-level-iso"}', encoding="utf-8")
    source_root = tmp_path / "dump"
    source_root.mkdir()
    source = tmp_path / "my original.iso"
    source.write_bytes(b"synthetic original image - not a game")
    destination = tmp_path / "my modded.iso"
    seen = []

    def writer(input_iso, export_dir, output_iso, progress):
        seen.append((input_iso, export_dir, output_iso))
        progress(25, "Copying original assets")
        output_iso.write_bytes(input_iso.read_bytes() + b" generated mod")
        progress(90, "Verifying the new image")
        return {"format": "azurik-level-iso", "sourceUnchanged": True, "executableUnchanged": True,
                "imageTestedInGame": False, "files": [{"path": "gamedata/new.xbr"}]}

    monkeypatch.setattr(level_iso, "build_iso", writer)
    value = SimpleNamespace(manager=LevelBuildJobs(), export=export, exports=exports, source_root=source_root,
                            source=source, output=destination, queued=queued, seen=seen)
    value.start = lambda manager=None, **changes: (manager or value.manager).start(
        str(changes.get("directory", export)), str(changes.get("input_path", source)),
        str(changes.get("output_path", destination)), exports, source_root)
    yield value
    # A deferred job must finish and release any process-wide busy guard.
    for worker in queued:
        worker.run()


def test_background_job_returns_queued_then_verified_report_without_mutating_input(jobs):
    original = jobs.source.read_bytes()
    job = jobs.start()
    assert job["status"] == "queued" and job["progress"] == 0
    assert not jobs.output.exists()
    job["message"] = "external mutation"
    assert jobs.manager.status(job["id"])["message"] != "external mutation"
    jobs.queued[0].run()
    complete = jobs.manager.status(job["id"])
    assert complete["status"] == "ready" and complete["progress"] == 100
    assert complete["report"]["sourceUnchanged"] and complete["report"]["imageTestedInGame"] is False
    assert jobs.source.read_bytes() == original
    assert jobs.seen == [(jobs.source, jobs.export, jobs.output)]
    report = Path(complete["reportPath"])
    assert json.loads(report.read_text("utf-8")) == complete["report"]
    complete["report"]["files"].clear()
    assert jobs.manager.status(job["id"])["report"]["files"]


@pytest.mark.parametrize("case", ["outside_export", "missing_plan", "inside_dump", "same_file", "existing_output",
                                 "existing_report", "missing_input", "foreign_extension", "output_extension", "missing_parent"])
def test_path_conflicts_reject_before_job_or_writer_and_keep_existing_files(jobs, case, tmp_path):
    changes = {}
    if case == "outside_export":
        other = tmp_path / "outside"
        other.mkdir()
        (other / "iso-plan.json").write_text("{}")
        changes["directory"] = other
    elif case == "missing_plan":
        (jobs.export / "iso-plan.json").unlink()
    elif case == "inside_dump":
        changes["output_path"] = jobs.source_root / "modified.iso"
    elif case == "same_file":
        changes["output_path"] = jobs.source
    elif case == "existing_output":
        jobs.output.write_bytes(b"keep existing output")
    elif case == "existing_report":
        jobs.output.with_name(jobs.output.name + ".report.json").write_bytes(b"keep existing report")
    elif case == "missing_input":
        changes["input_path"] = tmp_path / "missing.iso"
    elif case == "foreign_extension":
        foreign = tmp_path / "source.txt"
        foreign.write_bytes(b"foreign")
        changes["input_path"] = foreign
    elif case == "output_extension":
        changes["output_path"] = tmp_path / "modified.txt"
    elif case == "missing_parent":
        changes["output_path"] = tmp_path / "missing/modified.iso"
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError):
        jobs.start(**changes)
    assert jobs.queued == [] and jobs.seen == []
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("values", [(None, "input.iso", "output.iso"), (" ", "input.iso", "output.iso"),
                                    ("export", [], "output.iso"), ("export", "input.iso", None)])
def test_missing_non_string_arguments_and_unknown_status_are_rejected(jobs, values):
    with pytest.raises(ValueError):
        jobs.manager.start(*values, jobs.exports, jobs.source_root)
    for identifier in ("missing", None, [], 1):
        with pytest.raises(ValueError):
            jobs.manager.status(identifier)
    assert jobs.queued == []


def test_only_one_build_runs_at_a_time_and_failed_job_allows_next_build(jobs, monkeypatch, tmp_path):
    first = jobs.start()
    with pytest.raises(ValueError, match="déjà"):
        jobs.start(output_path=tmp_path / "second.iso")
    monkeypatch.setattr(level_iso, "build_iso", lambda *a, **k: (_ for _ in ()).throw(ValueError("truncated image")))
    jobs.queued[0].run()
    failed = jobs.manager.status(first["id"])
    assert failed["status"] == "failed" and failed["error"] == "truncated image"
    assert not jobs.output.exists() and not Path(str(jobs.output) + ".report.json").exists()
    assert jobs.start(output_path=tmp_path / "second.iso")["status"] == "queued"


def test_process_wide_single_build_guard_also_covers_a_second_manager(jobs, tmp_path):
    jobs.start()
    second_manager = LevelBuildJobs()
    with pytest.raises(ValueError, match="déjà"):
        jobs.start(manager=second_manager, output_path=tmp_path / "other.iso")


def test_progress_can_be_polled_as_snapshot_during_the_actual_worker(jobs, monkeypatch):
    job = jobs.start()
    states = []

    def writer(input_iso, export_dir, output_iso, progress):
        for percent in (1, 50, 99):
            progress(percent, "working")
            states.append(jobs.manager.status(job["id"]))
        output_iso.write_bytes(b"verified mock result")
        return {"sourceUnchanged": True, "imageTestedInGame": False}

    monkeypatch.setattr(level_iso, "build_iso", writer)
    jobs.queued[0].run()
    assert [state["progress"] for state in states] == [1, 50, 99]
    assert all(state["status"] == "running" for state in states)
    assert jobs.manager.status(job["id"])["status"] == "ready"


def test_report_race_preserves_external_report_and_keeps_verified_output(jobs, monkeypatch):
    job = jobs.start()
    report = Path(str(jobs.output) + ".report.json")

    def writer(input_iso, export_dir, output_iso, progress):
        output_iso.write_bytes(b"verified mock result")
        report.write_bytes(b"externally created report")
        return {"sourceUnchanged": True, "imageTestedInGame": False}

    monkeypatch.setattr(level_iso, "build_iso", writer)
    jobs.queued[0].run()
    ready = jobs.manager.status(job["id"])
    assert ready["status"] == "ready" and ready["report"]["reportWarning"]
    assert report.read_bytes() == b"externally created report"
    assert jobs.output.read_bytes() == b"verified mock result"


def test_worker_start_failure_does_not_leave_application_permanently_busy(jobs, monkeypatch):
    original = level_jobs.threading.Thread

    class BrokenThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("worker unavailable")

    monkeypatch.setattr(level_jobs.threading, "Thread", BrokenThread)
    with pytest.raises(RuntimeError, match="worker unavailable"):
        jobs.start()
    monkeypatch.setattr(level_jobs.threading, "Thread", original)
    assert jobs.start()["status"] == "queued"


@pytest.fixture
def live_server(tmp_path):
    backend = StubBackend()
    backend.source_dir = tmp_path / "source"
    backend.project_dir = tmp_path / "project"
    backend.exports_dir = tmp_path / "exports"
    app = StudioServer(("127.0.0.1", 0), backend, imports_dir=tmp_path / "imports")
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    yield app
    app.shutdown()
    app.server_close()
    thread.join(timeout=3)


@pytest.mark.parametrize("route,method,payload,expected", [
    ("create", "create_level", {"template": "w1", "id": "my_water", "name": "My level", "family": "water"},
     ("w1", "my_water", "My level", "water")),
    ("delete", "delete_level", {"level": "w1", "replacement": "my_water"}, ("w1", "my_water")),
    ("restore", "restore_level", {"level": "w1"}, ("w1",)),
    ("undo", "level_history", {}, (True,)),
    ("redo", "level_history", {}, (False,)),
])
def test_loopback_level_routes_pass_exact_contract_and_keep_catalog_results(live_server, route, method, payload, expected):
    received = []
    result = {"catalogChanged": True, "activeLevel": "my_water", "canUndo": True, "canRedo": False}
    setattr(live_server.backend, method, lambda *args: received.append(args) or copy.deepcopy(result))
    status, _, body = request(live_server, "POST", "/api/levels/" + route, payload,
                              {"Content-Type": "application/json"})
    assert status == 200 and received == [expected]
    assert json.loads(body) == result


def test_catalog_includes_management_and_build_jobs_poll_exact_id(live_server):
    manager = {"created": [], "deleted": [{"id": "w1", "replacement": "my_water"}], "canUndo": True, "canRedo": False}
    live_server.backend.level_management = lambda: manager
    status, _, body = request(live_server, "GET", "/api/catalog")
    assert status == 200 and json.loads(body)["levelManagement"] == manager
    calls = []
    live_server.level_builds = SimpleNamespace(
        start=lambda *args: calls.append(args) or {"id": "job1", "status": "queued", "progress": 0},
        status=lambda identifier: {"id": identifier, "status": "running", "progress": 40})
    status, _, body = request(live_server, "POST", "/api/build-iso",
        {"directory": "C:/my mod", "input": "C:/original.iso", "output": "C:/modified.iso"}, {"Content-Type": "application/json"})
    assert status == 200 and json.loads(body)["status"] == "queued"
    assert calls == [("C:/my mod", "C:/original.iso", "C:/modified.iso", live_server.backend.exports_dir, live_server.backend.source_dir)]
    status, _, body = request(live_server, "GET", "/api/build-iso?id=job%20%2F%3F")
    assert status == 200 and json.loads(body)["id"] == "job /?"


@pytest.mark.parametrize("route", ["levels/create", "levels/delete", "levels/restore", "levels/undo", "levels/redo", "build-iso"])
def test_new_mutations_keep_cross_origin_guards_and_never_dispatch(live_server, route):
    def forbidden(*args):
        pytest.fail("Cross-origin request reached level mutation")
    for name in ("create_level", "delete_level", "restore_level", "level_history"):
        setattr(live_server.backend, name, forbidden)
    live_server.level_builds = SimpleNamespace(start=forbidden)
    status, _, _ = request(live_server, "POST", "/api/" + route, {},
                          {"Content-Type": "application/json", "Origin": "https://untrusted.example"})
    assert status == 403


@pytest.fixture
def native_server(level_backend, tmp_path):
    app = StudioServer(("127.0.0.1", 0), level_backend, imports_dir=tmp_path / "imports")
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    yield app
    app.shutdown()
    app.server_close()
    thread.join(timeout=3)


def test_native_create_delete_export_restore_and_global_undo_through_actual_http(native_server):
    app = native_server
    originals = source_bytes(app.backend)
    headers = {"Content-Type": "application/json"}

    def post(route, payload):
        status, _, body = request(app, "POST", "/api/" + route, payload, headers)
        assert status == 200, body
        return json.loads(body)

    assert post("levels/create", {"template": "w2", "id": "my_water", "name": "HTTP water", "family": "water"})["activeLevel"] == "my_water"
    post("levels/delete", {"level": "w2", "replacement": "my_water"})
    exported = post("export", {})
    plan = json.loads((Path(exported["directory"]) / "iso-plan.json").read_text("utf-8"))
    assert {row["path"] for row in plan["gameFiles"]} == {"gamedata/my_water.xbr", "gamedata/index/index.xbr"}
    assert plan["removedFiles"][0]["path"] == "gamedata/w2.xbr"
    restored = post("levels/restore", {"level": "w2"})
    assert restored["activeLevel"] == "w2"
    assert post("undo", {"level": "my_water"})["catalogChanged"]
    status, _, body = request(app, "GET", "/api/catalog")
    catalog = json.loads(body)
    assert status == 200 and "w2" not in {row["id"] for row in catalog["levels"]}
    assert catalog["levelManagement"]["deleted"][0]["replacement"] == "my_water"
    assert source_bytes(app.backend) == originals


@pytest.mark.parametrize("route,payload", [
    ("levels/create", {"template": [], "id": "my_water", "name": "Bad template", "family": "water"}),
    ("levels/create", {"template": "w1", "id": "my_water", "name": "Bad family", "family": []}),
    ("levels/delete", {"level": [], "replacement": "w1"}),
    ("levels/restore", {"level": {}}),
])
def test_malformed_level_input_returns_client_error_without_mutating_native_project(native_server, route, payload):
    before = copy.deepcopy(native_server.backend._project)
    status, _, body = request(native_server, "POST", "/api/" + route, payload, {"Content-Type": "application/json"})
    assert status == 400 and json.loads(body)["error"]
    assert native_server.backend._project == before
