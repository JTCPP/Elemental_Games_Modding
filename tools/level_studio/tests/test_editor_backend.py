"""Source preservation, stale-patch rejection and persisted edit history."""
import copy
import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editor_backend import MESH_WARNING, StudioBackend, scan_objects
from scene_graph import identity, translation, local_matrix, multiply, transform_point


def fixture_data():
    data = bytearray(1024)
    data[:4] = b"xobx"
    struct.pack_into("<I", data, 4, 4)
    struct.pack_into("<II", data, 0x0C, 1, 0x60)
    struct.pack_into("<I4sII", data, 0x40, len(data) - 0x60, b"node", 8, len(data) - 0x60)
    struct.pack_into("<f", data, 96, 1)
    struct.pack_into("<4f", data, 100, 10, 20, 30, 0)
    struct.pack_into("<3f", data, 124, 1, 1, 1)
    struct.pack_into("<f", data, 140, 1)
    struct.pack_into("<f", data, 192, 1)
    data[196:204] = b"emerald\0"
    struct.pack_into("<3f", data, 0x220, 50, 60, 70)
    return bytes(data)


class FixtureBackend(StudioBackend):
    """Replace the geometry parser only; exercise real object/byte editing."""
    def _parse_scene(self, data, level):
        origin = list(struct.unpack_from("<3f", data, 0x220))
        objects = scan_objects(data)
        nodes = []
        for index, (point, offset) in enumerate(((objects[0]["position"], 100), (origin, 0x220))):
            nodes.append({"index": index, "type": "transform", "parent": -1, "position": point,
                          "localMatrix": translation(point), "worldMatrix": translation(point)})
        def binding(index, offset, point):
            return {"transformNode": index, "editOffset": offset, "originalLocalPosition": point,
                    "parentWorldMatrix": identity(), "parentWorldInverse": identity()}
        objects[0].update(editable=True, nodeIndex=0, editBinding=binding(0, 100, objects[0]["position"]))
        return {"meshes": [{"id": "mesh-00000200", "name": "Verified fixture mesh",
                            "tag": "surf", "sourceOffset": 0x200, "origin": origin,
                            "position": [50.5, 60.5, 70], "originalPosition": [50.5, 60.5, 70],
                            "positions": [50, 60, 70, 51, 60, 70, 50, 61, 70],
                            "colors": [1, 1, 1] * 3, "indices": [0, 1, 2], "uvs": [],
                            "parts": [{"start": 0, "count": 3, "partIndex": 0}], "editable": True,
                            "nodeIndex": 1, "editBinding": binding(1, 0x220, origin)}],
                "nodes": nodes, "objects": objects,
                "textures": [], "collisions": {"positions": [50, 60, 70, 51, 60, 70, 50, 61, 70], "indices": [0, 1, 2]},
                "warnings": []}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.gamedata = self.source / "gamedata"
        self.gamedata.mkdir(parents=True)
        for level in ("w1", "town", "life", "airship", "a1"):
            (self.gamedata / f"{level}.xbr").write_bytes(fixture_data())
        self.backend = self.make_backend()

    def tearDown(self):
        self.temporary.cleanup()

    def make_backend(self):
        return FixtureBackend(self.source, self.root / "project", self.root / "exports", texture_dir=self.root / "textures")

    def test_catalog_contains_special_levels_and_cache_is_bounded(self):
        self.assertEqual({row["id"] for row in self.backend.catalog()}, {"w1", "town", "life", "airship", "a1"})
        for level in ("w1", "town", "life"):
            self.backend.get_scene(level)
        self.assertEqual(len(self.backend._cache), 2)

    def test_entity_move_persisted_history_and_original_bytes(self):
        original = (self.gamedata / "w1.xbr").read_bytes()
        scene = self.backend.get_scene("w1")
        entity = scene["objects"][0]
        self.assertEqual(entity["position"], [10, 20, 30])
        self.backend.move("w1", entity["id"], [40, 50, 60])
        self.backend.move("w1", entity["id"], [41, 51, 61])
        self.assertEqual(self.backend.pending_count(), 1)
        reopened = self.make_backend()
        self.assertTrue(reopened.undo("w1")["changed"])
        self.assertEqual(reopened.get_scene("w1")["objects"][0]["position"], [40, 50, 60])
        self.assertTrue(reopened.redo("w1")["changed"])
        self.assertEqual(reopened.get_scene("w1")["objects"][0]["position"], [41, 51, 61])
        reopened.move("w1", entity["id"], [10, 20, 30])
        self.assertEqual(reopened.pending_count(), 0)
        self.assertEqual((self.gamedata / "w1.xbr").read_bytes(), original)

    def test_export_changes_only_validated_coordinate_bytes_and_never_source(self):
        original = (self.gamedata / "w1.xbr").read_bytes()
        self.backend.move("w1", "entity-00000064", [40, 50, 60])
        result = self.backend.export()
        exported = (Path(result["path"]) / "gamedata" / "w1.xbr").read_bytes()
        self.assertEqual(len(original), len(exported))
        self.assertEqual(struct.unpack_from("<3f", exported, 100), (40, 50, 60))
        self.assertTrue(all(a == b for i, (a, b) in enumerate(zip(original, exported)) if not 100 <= i < 112))
        self.assertEqual((self.gamedata / "w1.xbr").read_bytes(), original)
        self.assertEqual(result["files"][0]["sourceSha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(result["files"][0]["exportSha256"], hashlib.sha256(exported).hexdigest())
        self.assertNotEqual(result["path"], self.backend.export()["path"])

    def test_mesh_move_translates_render_vertices_and_warns_collision_unchanged(self):
        scene = self.backend.get_scene("w1")
        collision = copy.deepcopy(scene["collisions"])
        result = self.backend.move("w1", "mesh-00000200", [60.5, 80.5, 100])
        self.assertEqual(result["warning"], MESH_WARNING)
        moved = self.backend.get_scene("w1")
        self.assertEqual(moved["meshes"][0]["origin"], [60, 80, 100])
        self.assertEqual(moved["meshes"][0]["positions"][:3], [60, 80, 100])
        self.assertEqual(moved["collisions"], collision)
        export = self.backend.export()
        payload = (Path(export["path"]) / "gamedata" / "w1.xbr").read_bytes()
        self.assertEqual(struct.unpack_from("<3f", payload, 0x220), (60, 80, 100))
        self.assertIn(MESH_WARNING, export["warnings"])

    def test_nan_path_traversal_unknown_and_changed_source_are_rejected(self):
        for bad in ([float("nan"), 0, 0], [float("inf"), 0, 0], [True, 0, 0], [50000, 0, 0]):
            with self.assertRaises(ValueError):
                self.backend.move("w1", "entity-00000064", bad)
        with self.assertRaises(ValueError):
            self.backend.get_scene("../w1")
        with self.assertRaises(ValueError):
            self.backend.move("w1", "entity-ffffffff", [1, 2, 3])
        self.backend.move("w1", "entity-00000064", [40, 50, 60])
        changed = bytearray(fixture_data())
        changed[-1] = 7
        (self.gamedata / "w1.xbr").write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.backend.export()
        self.assertFalse((self.root / "exports").exists())
        with self.assertRaises(ValueError):
            self.make_backend().get_scene("w1")

    def test_project_cannot_invent_an_arbitrary_export_offset(self):
        self.backend.move("w1", "entity-00000064", [40, 50, 60])
        project = json.loads(self.backend.project_path.read_text("utf-8"))
        project["levels"]["w1"]["edits"]["entity-00000064"]["offset"] = 500
        self.backend.project_path.write_text(json.dumps(project), "utf-8")
        with self.assertRaisesRegex(ValueError, "offset"):
            self.make_backend().export()
        with self.assertRaises(ValueError):
            FixtureBackend(self.source, self.source / "project", self.root / "exports", texture_dir=self.root / "textures")

    def test_entity_scanner_rejects_nonfinite_existing_records(self):
        data = bytearray(fixture_data())
        # Prevent the alternate candidate coordinate offsets validating zeros.
        for offset in (100, 80, 52, 56):
            struct.pack_into("<4f", data, offset, float("nan"), 1, 2, 0)
        self.assertEqual(scan_objects(bytes(data)), [])

    def test_rotation_and_scale_require_decoded_channels(self):
        item = self.backend.get_scene("w1")["objects"][0]
        before = copy.deepcopy(self.backend._project)
        for requested in ({"rotation": [0, 0, 1]}, {"scale": [2, 2, 2]}):
            with self.assertRaisesRegex(ValueError, "validé"):
                self.backend.transform("w1", item["id"], **requested)
            self.assertEqual(self.backend._project, before)

    def test_world_move_exports_inverse_rotated_scaled_parent_coordinates(self):
        class RotatedBackend(FixtureBackend):
            def _parse_scene(self, data, level):
                scene = super()._parse_scene(data, level)
                parent = local_matrix([0, 0, 0], [0, 0, 3.141592653589793 / 2], [2, 3, 1])
                scene["nodes"].append({"index": 2, "type": "transform", "parent": -1,
                                       "position": [0, 0, 0], "localMatrix": parent, "worldMatrix": parent})
                node = scene["nodes"][0]
                node["parent"] = 2
                node["worldMatrix"] = multiply(parent, node["localMatrix"])
                item = scene["objects"][0]
                item["originalPosition"] = item["position"] = transform_point(node["worldMatrix"], [0, 0, 0])
                return scene
        backend = RotatedBackend(self.source, self.root / "rotated-project", self.root / "rotated-export", texture_dir=self.root / "textures")
        item = backend.get_scene("w1")["objects"][0]
        target = [item["position"][0] + 6, item["position"][1] + 4, item["position"][2] + 2]
        backend.move("w1", item["id"], target)
        rendered = backend.get_scene("w1")["objects"][0]["position"]
        for actual, expected in zip(rendered, target):
            self.assertAlmostEqual(actual, expected)
        result = backend.export()
        payload = (Path(result["path"]) / "gamedata/w1.xbr").read_bytes()
        self.assertEqual(struct.unpack_from("<3f", payload, 100), (12, 18, 32))

    def test_shared_parts_use_one_node_edit_and_undo_restores_whole_group(self):
        _, cached = self.backend._load("w1")
        other = copy.deepcopy(cached["meshes"][0])
        other["id"] = "mesh-second-part"
        other["position"] = other["originalPosition"] = [55.5, 60.5, 70]
        cached["meshes"].append(other)
        first = self.backend.move("w1", "mesh-00000200", [60.5, 80.5, 100])
        self.assertEqual(first["relatedPositions"][other["id"]], [65.5, 80.5, 100])
        self.backend.move("w1", other["id"], [66.5, 80.5, 100])
        self.assertEqual(self.backend.pending_count(), 1)
        self.assertEqual(self.backend.get_scene("w1")["meshes"][0]["position"], [61.5, 80.5, 100])
        self.backend.undo("w1")
        self.assertEqual(self.backend.get_scene("w1")["meshes"][0]["position"], [60.5, 80.5, 100])
        self.backend.undo("w1")
        self.assertEqual(self.backend.pending_count(), 0)
        self.assertEqual(self.backend.get_scene("w1")["meshes"][1]["position"], [55.5, 60.5, 70])


if __name__ == "__main__":
    unittest.main()
