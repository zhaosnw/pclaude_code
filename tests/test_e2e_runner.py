import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from e2e_runner import run_case  # noqa: E402


def test_run_case_injects_fixture_and_snapshots_files():
    case = {
        "case_id": "smoke.hello",
        "priority": "P1",
        "entrypoint": {"argv": ["-p", "hi"]},
        "fixture": "alignment/fixtures/single_turn_hello.json",
        "expected": {"exit_code": 0, "stdout_kind": "text"},
        "policy": {},
    }
    result = run_case(case)
    assert result["state"]["exit_code"] == 0, result["stderr"]
    assert "Hello from fixture." in result["stdout"]
    # files 是 (相对路径, sha256) 的快照列表,不再恒为空
    assert isinstance(result["files"], list)
    # sandbox_root 被透出供 normalizer 抹路径
    assert result["sandbox_root"]


def test_run_case_seeds_files_into_sandbox():
    # seed README.md 进沙箱;由于 fixture 不读它,这里只验证种子机制不报错且文件被快照
    case = {
        "case_id": "smoke.seed",
        "priority": "P1",
        "entrypoint": {"argv": ["-p", "hi"]},
        "fixture": "alignment/fixtures/single_turn_hello.json",
        "fs": {"seed": ["README.md"]},
        "expected": {"exit_code": 0},
        "policy": {},
    }
    result = run_case(case)
    snap_paths = {f["path"] for f in result["files"]}
    assert "README.md" in snap_paths
