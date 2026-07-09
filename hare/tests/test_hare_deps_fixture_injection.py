import json

from hare.query.deps import production_deps
from hare.services.api.claude import query_model_with_streaming


def test_production_deps_uses_real_model_without_env(monkeypatch):
    monkeypatch.delenv("HARE_MODEL_FIXTURE", raising=False)
    deps = production_deps()
    assert deps.call_model is query_model_with_streaming


def test_production_deps_uses_fixture_when_env_set(monkeypatch, tmp_path):
    fx = tmp_path / "fx.json"
    fx.write_text(
        json.dumps(
            {
                "kind": "scripted",
                "responses": [
                    {
                        "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "hi"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HARE_MODEL_FIXTURE", str(fx))
    deps = production_deps()
    assert deps.call_model is not query_model_with_streaming
