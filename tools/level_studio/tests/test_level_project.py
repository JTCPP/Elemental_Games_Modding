"""Native synthetic XBR projects: reversible catalog edits, history and export.

Archive/index serialization is real; fixtures contain only generated graph data.
These checks do not substitute for a runtime test of the commercial game.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from level_archive import named_resources, read_index
from renderer_parser import read_sections
from test_level_archive import make_archive, make_index
from test_transform_backend import GraphBackend, find, transform_fixture


def digest(data):
    return hashlib.sha256(data).hexdigest()


def native_level(identifier, named_graph=True):
    raw, _ = transform_fixture()
    resources = [(section.tag, raw[section.offset:section.offset + section.size])
                 for section in read_sections(raw)]
    resources += [("levl", b"synthetic native entry points; scripts unchanged\0"),
                  ("coll", b"synthetic collision bytes remain identical\0")]
    names = [(4, "levels/" + identifier)]
    if named_graph:
        names.insert(0, (0, "levels/" + identifier + "/graph"))
    return make_archive(resources, names)


@pytest.fixture
def level_backend(tmp_path):
    gamedata = tmp_path / "source/gamedata"
    (gamedata / "index").mkdir(parents=True)
    records = []
    for identifier in ("w1", "w2", "a5", "selector", "training_room"):
        payload = native_level(identifier, named_graph=identifier != "w2")
        (gamedata / (identifier + ".xbr")).write_bytes(payload)
        records.extend({"key": row["key"], "tag": row["tag"], "archive": identifier + ".xbr"}
                       for row in named_resources(payload))
    (gamedata / "shared.xbr").write_bytes(make_archive([("txtr", b"shared asset bytes")], [(0, "textures/shared")]))
    records.append({"key": "textures/shared", "tag": "txtr", "archive": "shared.xbr"})
    (gamedata / "index/index.xbr").write_bytes(make_index(records))
    return GraphBackend(gamedata.parent, tmp_path / "project", tmp_path / "exports",
                        texture_dir=tmp_path / "textures")


def source_bytes(backend):
    return {path.relative_to(backend.source_dir).as_posix(): path.read_bytes()
            for path in backend.source_dir.rglob("*") if path.is_file()}


def reopen(backend):
    return GraphBackend(backend.source_dir, backend.project_dir, backend.exports_dir,
                        texture_dir=backend.texture_dir)


def catalog_ids(backend):
    return {row["id"] for row in backend.catalog()}


def test_creation_clones_original_source_and_preserves_all_resource_payloads(level_backend):
    backend = level_backend
    originals = source_bytes(backend)
    source = backend._source_path("w1").read_bytes()
    item = find(backend.get_scene("w1"), "emerald")
    backend.transform("w1", item["id"], position=[100, 200, 300])
    result = backend.create_level("w1", "my_water", "  Mon royaume  ", "water")
    assert result["activeLevel"] == "my_water" and result["catalogChanged"]
    record = next(row for row in backend.catalog() if row["id"] == "my_water")
    assert record["name"] == "Mon royaume" and record["custom"]
    assert record["templateOrigin"] == "w1"
    clone = backend._source_path("my_water").read_bytes()
    assert [(s.tag, clone[s.offset:s.offset + s.size]) for s in read_sections(clone)] == [
        (s.tag, source[s.offset:s.offset + s.size]) for s in read_sections(source)]
    assert {row["key"] for row in named_resources(clone)} == {"levels/custom/my_water", "levels/custom/my_water/graph"}
    assert find(backend.get_scene("my_water"), "emerald")["position"] == pytest.approx(item["position"])
    assert find(backend.get_scene("w1"), "emerald")["position"] == pytest.approx([100, 200, 300])
    assert source_bytes(backend) == originals
    persisted = reopen(backend)
    assert persisted._project["version"] == 3
    assert persisted._source_path("my_water").read_bytes() == clone
    assert persisted.level_management()["canUndo"]


@pytest.mark.parametrize("identifier", ["../water", "a/b", "WATER", "1water", "", "con", "nul", "lpt1", "com9",
                                       "always", "default", "a" * 41, "a\0b", None])
def test_unsafe_duplicate_and_reserved_creation_ids_do_not_change_project(level_backend, identifier):
    backend = level_backend
    before = copy.deepcopy(backend._project)
    originals = source_bytes(backend)
    with pytest.raises(ValueError):
        backend.create_level("w1", identifier, "Name", "water")
    assert backend._project == before and source_bytes(backend) == originals
    assert not backend.project_path.exists()


@pytest.mark.parametrize("name,family", [("", "water"), (" ", "water"), ("a" * 81, "water"), ("bad\nname", "water"), (None, "water"), ("Name", "unknown")])
def test_invalid_names_and_family_are_rejected_without_writes(level_backend, name, family):
    backend = level_backend
    with pytest.raises(ValueError):
        backend.create_level("w1", "new_water", name, family)
    assert not backend.project_dir.exists()


def test_duplicate_original_custom_template_and_index_key_collision_are_rejected(level_backend):
    backend = level_backend
    with pytest.raises(ValueError):
        backend.create_level("w1", "a5", "Duplicate original", "water")
    with pytest.raises(ValueError):
        backend.create_level("../w1", "new_water", "Bad template", "water")
    backend.create_level("w1", "my_water", "Water clone", "water")
    before = backend.project_path.read_bytes()
    with pytest.raises(ValueError):
        backend.create_level("w1", "my_water", "Duplicate custom", "water")
    assert backend.project_path.read_bytes() == before
    index = backend.gamedata_dir / "index/index.xbr"
    entries = read_index(index.read_bytes())
    entries.append({"key": "levels/custom/conflict", "tag": "levl", "archive": "foreign.xbr"})
    index.write_bytes(make_index(entries))
    with pytest.raises(ValueError, match="registre"):
        backend.create_level("w1", "conflict", "Conflict", "water")
    assert "conflict" not in catalog_ids(backend)


@pytest.mark.parametrize("version", [1, 2])
def test_existing_project_versions_load_without_rewriting_source_or_project(level_backend, version):
    backend = level_backend
    item = find(backend.get_scene("w1"), "emerald")
    backend.move("w1", item["id"], [12, 30, 40])
    project = json.loads(backend.project_path.read_text("utf-8"))
    project["version"] = version
    project.pop("levelManagement")
    project.pop("historySerial")
    for state in project["levels"].values():
        for row in state["undo"] + state["redo"]:
            row.pop("_seq", None)
    backend.project_path.write_text(json.dumps(project), encoding="utf-8")
    disk_before = backend.project_path.read_bytes()
    originals = source_bytes(backend)
    loaded = reopen(backend)
    assert backend.project_path.read_bytes() == disk_before
    assert source_bytes(loaded) == originals
    assert find(loaded.get_scene("w1"), "emerald")["position"] == pytest.approx([12, 30, 40])
    loaded.undo("w1")
    assert find(loaded.get_scene("w1"), "emerald")["position"] == pytest.approx(item["position"])


def test_delete_compatible_chains_restore_and_protected_entry_levels(level_backend):
    backend = level_backend
    originals = source_bytes(backend)
    backend.create_level("w1", "water_one", "First", "water")
    backend.create_level("water_one", "water_two", "Second", "water")
    with pytest.raises(ValueError):
        backend.delete_level("w1", "a5")
    backend.delete_level("w1", "water_one")
    backend.delete_level("water_one", "water_two")
    assert backend._level_destination("w1") == "water_two"
    assert {row["replacement"] for row in backend.level_management()["deleted"]} == {"water_two"}
    with pytest.raises(ValueError):
        backend.delete_level("water_two", "w1")
    backend.restore_level("water_one")
    assert backend._level_destination("w1") == "water_one"
    assert {"water_one", "water_two"}.issubset(catalog_ids(backend))
    for identifier in ("selector", "training_room"):
        with pytest.raises(ValueError, match="techniques"):
            backend.delete_level(identifier, "water_two")
    assert source_bytes(backend) == originals
    backend._level_manager()["deleted"] = {"water_one": "water_two", "water_two": "water_one"}
    with pytest.raises(ValueError, match="Cycle"):
        backend._initialize_level_management()


def test_global_undo_redo_restores_one_operation_at_a_time_across_catalog_and_transforms(level_backend):
    backend = level_backend
    item = find(backend.get_scene("w1"), "emerald")
    backend.create_level("w1", "water_one", "One", "water")
    backend.transform("w1", item["id"], position=[101, 202, 303])
    backend.create_level("w1", "water_two", "Two", "water")
    custom = find(backend.get_scene("water_one"), "emerald")
    backend.transform("water_one", custom["id"], position=[40, 50, 60])
    backend.delete_level("w1", "water_one")
    backend.undo("water_one")
    assert "w1" in catalog_ids(backend)
    backend.undo("w1")
    assert find(backend.get_scene("water_one"), "emerald")["position"] == pytest.approx(custom["position"])
    backend.undo("w1")
    assert "water_two" not in catalog_ids(backend)
    backend.undo("water_one")
    assert find(backend.get_scene("w1"), "emerald")["position"] == pytest.approx(item["position"])
    backend.undo("w1")
    assert "water_one" not in catalog_ids(backend)
    backend = reopen(backend)  # All pending global redo must survive closing the editor.
    backend.redo("w1")
    assert "water_one" in catalog_ids(backend) and "water_two" not in catalog_ids(backend)
    assert find(backend.get_scene("w1"), "emerald")["position"] == pytest.approx(item["position"])
    backend.redo("w1")
    assert "water_two" not in catalog_ids(backend)  # This Ctrl Shift Z redoes only the source transform.
    assert find(backend.get_scene("w1"), "emerald")["position"] == pytest.approx([101, 202, 303])
    backend.redo("w1")
    assert "water_two" in catalog_ids(backend)
    backend.redo("w1")
    assert find(backend.get_scene("water_one"), "emerald")["position"] == pytest.approx([40, 50, 60])
    backend.redo("water_one")
    assert "w1" not in catalog_ids(backend) and not backend._combined_history()["canRedo"]


def test_new_operation_invalidates_all_global_redo_and_catalog_save_failure_rolls_back(level_backend, monkeypatch):
    backend = level_backend
    item = find(backend.get_scene("w1"), "emerald")
    backend.create_level("w1", "water_one", "One", "water")
    backend.move("w1", item["id"], [30, 40, 50])
    backend.undo("w1")
    assert backend._state("w1")["redo"]
    backend.create_level("w1", "water_two", "Two", "water")
    assert not backend._combined_history()["canRedo"]
    backend.undo("w1")
    assert backend._level_manager()["redo"]
    backend.move("w1", item["id"], [60, 70, 80])
    assert not backend._combined_history()["canRedo"]
    before = copy.deepcopy(backend._project)
    disk = backend.project_path.read_bytes()
    monkeypatch.setattr(backend, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        backend.delete_level("w1", "water_one")
    assert backend._project == before
    assert backend.project_path.read_bytes() == disk


def test_export_registers_custom_archive_aliases_original_keys_and_preserves_collision_bytes(level_backend):
    backend = level_backend
    originals = source_bytes(backend)
    backend.create_level("w2", "water_clone", "Water clone", "water")
    backend.delete_level("w2", "water_clone")
    result = backend.export()
    folder = Path(result["directory"])
    plan = json.loads((folder / "iso-plan.json").read_text("utf-8"))
    assert plan["removedFiles"] == [{"path": "gamedata/w2.xbr", "sourceSha256": digest(originals["gamedata/w2.xbr"])}]
    assert {row["path"] for row in plan["gameFiles"]} == {"gamedata/water_clone.xbr", "gamedata/index/index.xbr"}
    clone = (folder / "gamedata/water_clone.xbr").read_bytes()
    resources = named_resources(clone)
    assert {row["key"] for row in resources if row["tag"] == "levl"} == {"levels/custom/water_clone", "levels/w2"}
    assert len({row["resourceIndex"] for row in resources if row["tag"] == "levl"}) == 1
    entries = read_index((folder / "gamedata/index/index.xbr").read_bytes())
    assert next(row for row in entries if row["key"] == "levels/w2")["archive"] == "water_clone.xbr"
    assert next(row for row in entries if row["key"] == "textures/shared")["archive"] == "shared.xbr"
    original_level = originals["gamedata/w2.xbr"]
    assert [(s.tag, clone[s.offset:s.offset + s.size]) for s in read_sections(clone)] == [
        (s.tag, original_level[s.offset:s.offset + s.size]) for s in read_sections(original_level)]
    assert source_bytes(backend) == originals
    assert result["sourceUnchanged"] and result["removedFiles"] == plan["removedFiles"]


def test_export_keeps_deleted_archive_when_other_registered_resources_use_it(level_backend):
    backend = level_backend
    backend.create_level("w1", "water_clone", "Water clone", "water")
    backend.delete_level("w1", "water_clone")
    folder = Path(backend.export()["directory"])
    plan = json.loads((folder / "iso-plan.json").read_text("utf-8"))
    assert plan["removedFiles"] == []
    entries = read_index((folder / "gamedata/index/index.xbr").read_bytes())
    assert next(row for row in entries if row["key"] == "levels/w1/graph")["archive"] == "w1.xbr"
    assert next(row for row in entries if row["key"] == "levels/w1")["archive"] == "water_clone.xbr"


def test_tampered_managed_source_rejects_export_before_output_directory_created(level_backend):
    backend = level_backend
    backend.create_level("w1", "water_clone", "Water clone", "water")
    managed = backend._source_path("water_clone")
    managed.write_bytes(managed.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="altérée"):
        backend.export()
    assert not backend.exports_dir.exists()


def test_catalog_creation_and_catalog_history_roll_back_completely_on_save_failure(level_backend, monkeypatch):
    backend = level_backend
    backend.create_level("w1", "water_one", "One", "water")
    before = copy.deepcopy(backend._project)
    disk = backend.project_path.read_bytes()
    originals = source_bytes(backend)
    monkeypatch.setattr(backend, "save", lambda: (_ for _ in ()).throw(OSError("cannot save")))
    with pytest.raises(OSError, match="cannot save"):
        backend.level_history(True)
    assert backend._project == before and backend.project_path.read_bytes() == disk
    with pytest.raises(OSError, match="cannot save"):
        backend.create_level("w1", "water_two", "Two", "water")
    assert backend._project == before and backend.project_path.read_bytes() == disk
    assert source_bytes(backend) == originals


def test_new_transform_clears_redo_of_every_other_level(level_backend):
    backend = level_backend
    backend.create_level("w1", "water_one", "One", "water")
    items = {level: find(backend.get_scene(level), "emerald")["id"] for level in ("w1", "a5", "water_one")}
    backend.move("w1", items["w1"], [10, 20, 30])
    backend.move("a5", items["a5"], [40, 50, 60])
    backend.undo("water_one")
    backend.undo("water_one")
    assert backend._state("w1")["redo"] and backend._state("a5")["redo"]
    backend.move("water_one", items["water_one"], [70, 80, 90])
    assert not backend._state("w1")["redo"] and not backend._state("a5")["redo"]
    assert not backend._combined_history()["canRedo"]


def test_reset_save_disk_failure_preserves_other_level_redo_and_global_order(level_backend, monkeypatch):
    backend = level_backend
    backend.create_level("w1", "water_one", "One", "water")
    for level, position in (("w1", [10, 20, 30]), ("a5", [40, 50, 60])):
        item = find(backend.get_scene(level), "emerald")
        backend.move(level, item["id"], position)
    backend.undo("a5")
    before = copy.deepcopy(backend._project)
    disk = backend.project_path.read_bytes()
    originals = source_bytes(backend)
    assert before["levels"]["a5"]["redo"]
    assert before["levels"]["w1"]["edits"]
    with monkeypatch.context() as failing_disk:
        failing_disk.setattr(Path, "write_text", lambda *args, **kwargs:
                             (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            backend.reset_level("w1")
    assert backend._project == before
    assert backend.project_path.read_bytes() == disk
    assert source_bytes(backend) == originals
    # The operation undone in A5 still replays after the failed W1 reset.
    backend.redo("w1")
    assert find(backend.get_scene("a5"), "emerald")["position"] == pytest.approx([40, 50, 60])
    assert find(backend.get_scene("w1"), "emerald")["position"] == pytest.approx([10, 20, 30])


def test_no_change_move_keeps_global_catalog_undo_available(level_backend):
    backend = level_backend
    backend.create_level("w1", "water_one", "One", "water")
    item = find(backend.get_scene("a5"), "emerald")
    result = backend.move("a5", item["id"], item["position"])
    assert result["canUndo"] and not result["canRedo"]
    assert not backend._state("a5")["undo"]
    backend.undo("a5")
    assert "water_one" not in catalog_ids(backend)


def test_export_chain_aliases_deleted_custom_key_to_retained_clone_without_exporting_deleted_custom(level_backend):
    backend = level_backend
    backend.create_level("w1", "water_one", "One", "water")
    backend.create_level("water_one", "water_two", "Two", "water")
    backend.delete_level("w1", "water_one")
    backend.delete_level("water_one", "water_two")
    folder = Path(backend.export()["directory"])
    plan = json.loads((folder / "iso-plan.json").read_text("utf-8"))
    assert {row["path"] for row in plan["gameFiles"]} == {"gamedata/water_two.xbr", "gamedata/index/index.xbr"}
    assert not (folder / "gamedata/water_one.xbr").exists()
    entries = read_index((folder / "gamedata/index/index.xbr").read_bytes())
    for key in ("levels/w1", "levels/custom/water_one", "levels/custom/water_two"):
        assert next(row for row in entries if row["key"] == key)["archive"] == "water_two.xbr"


def test_export_rejects_new_registry_collision_before_creating_any_export(level_backend):
    backend = level_backend
    backend.create_level("w1", "water_one", "One", "water")
    path = backend.gamedata_dir / "index/index.xbr"
    entries = read_index(path.read_bytes())
    entries.append({"key": "levels/custom/water_one", "tag": "levl", "archive": "foreign.xbr"})
    path.write_bytes(make_index(entries))
    with pytest.raises(ValueError):
        backend.export()
    assert not backend.exports_dir.exists()


def test_malicious_project_archive_name_is_rejected_before_any_outside_file_read(level_backend):
    backend = level_backend
    backend.create_level("w1", "water_one", "One", "water")
    project = json.loads(backend.project_path.read_text("utf-8"))
    project["levelManagement"]["created"]["water_one"]["archive"] = "../outside.xbr"
    backend.project_path.write_text(json.dumps(project), encoding="utf-8")
    disk = backend.project_path.read_bytes()
    with pytest.raises(ValueError, match="personnalisé"):
        reopen(backend)
    assert backend.project_path.read_bytes() == disk
