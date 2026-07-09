import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "hare" / "alignment"))

from golden_normalize import normalize_result  # noqa: E402


def test_normalize_strips_nondeterminism():
    raw = {
        "case_id": "x",
        "priority": "P1",
        "status": "ok",
        "stdout": "session 4f0c1234-1111-2222-3333-444455556666 done in 1234ms "
        "cost $0.000123 at /tmp/abc/file",
        "events": [
            {"session_id": "abc-123", "duration_ms": 42, "uuid": "deadbeef"}
        ],
        "state": {"exit_code": 0},
        "duration_ms": 999,
    }
    out = normalize_result(raw, sandbox_root="/tmp/abc")
    # uuid / 毫秒 / cost / 沙箱绝对路径 都被占位符替换
    assert "4f0c1234-1111-2222-3333-444455556666" not in out["stdout"]
    assert "1234ms" not in out["stdout"]
    assert "/tmp/abc" not in out["stdout"]
    assert "$0.000123" not in out["stdout"]
    assert out["events"][0]["session_id"] == "<UUID>"
    assert out["events"][0]["duration_ms"] == "<DURATION>"
    # duration_ms 顶层字段被丢弃(纯计时,不参与比对)
    assert "duration_ms" not in out
    # 非易变字段保持原样
    assert out["state"]["exit_code"] == 0
