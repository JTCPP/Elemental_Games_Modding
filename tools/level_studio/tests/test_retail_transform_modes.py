"""Independent matrices recorded by executing the original retail XBE."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scene_graph import effective_parent_matrix, local_matrix, multiply

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/transform-mode-xbe-emulation.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[f"parent-{index // 4}-mode-{case['mode']}" for index, case in enumerate(CASES)])
def test_parent_modes_match_original_xbe_execution(case):
    parent = local_matrix([10, 20, 30], case["parentRotation"], [1, 1, 1])
    node = {"inheritMode": case["mode"], "position": [2, 3, 4], "rotatePivot": [1, 0, 0]}
    local = local_matrix(node["position"], [.2, .3, .4], [1, 1, 1], node["rotatePivot"])
    world = multiply(effective_parent_matrix(node, parent), local)
    assert world == pytest.approx(case["matrix"], abs=1e-5)


def test_browser_parent_modes_match_the_same_retail_fixtures():
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("Node is only needed to check the browser's numeric helper")
    script = """
import { effectiveParentRows } from './web/source-transform.mjs';
let input = ''; for await (const chunk of process.stdin) input += chunk;
const rows = JSON.parse(input).map(({node, parent}) => effectiveParentRows(node, parent));
process.stdout.write(JSON.stringify(rows));
"""
    rows = [{"node": {"inheritMode": case["mode"], "position": [2, 3, 4], "rotatePivot": [1, 0, 0]},
             "parent": local_matrix([10, 20, 30], case["parentRotation"], [1, 1, 1])} for case in CASES]
    result = subprocess.run([node_path, "--input-type=module", "-e", script], cwd=ROOT,
                            input=json.dumps(rows), capture_output=True, text=True, check=True)
    local = local_matrix([2, 3, 4], [.2, .3, .4], [1, 1, 1], [1, 0, 0])
    for effective, case in zip(json.loads(result.stdout), CASES):
        assert multiply(effective, local) == pytest.approx(case["matrix"], abs=1e-5)
