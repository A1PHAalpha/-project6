from types import SimpleNamespace

import rag_engine.llm_client as llm


def test_chat_completion_uses_env_config(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "2345")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.7")
    monkeypatch.setenv("LLM_TIMEOUT", "12")
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["create"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"ok": true}'),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=1, completion_tokens=2, total_tokens=3
                ),
                id="req_test",
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm, "_client", None)

    _, _, meta = llm.chat_completion([{"role": "user", "content": "hi"}])

    assert captured["init"]["timeout"] == 12
    assert captured["create"]["max_tokens"] == 2345
    assert captured["create"]["temperature"] == 0.7
    assert meta["_finish_reason"] == "stop"


def test_chat_completion_marks_length_finish_reason(monkeypatch):
    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"truncated":'),
                        finish_reason="length",
                    )
                ],
                usage=None,
                id="req_length",
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm, "_client", None)

    _, _, meta = llm.chat_completion([{"role": "user", "content": "hi"}])

    assert meta["_finish_reason"] == "length"


def test_invalid_max_tokens_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "not-a-number")
    assert llm.get_max_tokens() == llm.DEFAULT_MAX_TOKENS


def test_invalid_timeout_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT", "-5")
    assert llm.get_timeout() == llm.DEFAULT_TIMEOUT_SECONDS
