import control.terminal_confirmations as confirmations


def test_confirmation_is_bound_to_session_and_command():
    store = confirmations.WriteConfirmationStore(ttl_seconds=30)
    issued = store.issue("term_1", "configure terminal")
    assert store.consume("term_1", "configure terminal", issued["confirmation_id"]) is True
    assert store.consume("term_1", "configure terminal", issued["confirmation_id"]) is False
    assert store.consume("term_2", "configure terminal", issued["confirmation_id"]) is False
    assert store.consume("term_1", "shutdown", issued["confirmation_id"]) is False


def test_expired_confirmation_is_rejected(monkeypatch):
    store = confirmations.WriteConfirmationStore(ttl_seconds=1)
    now = 1000.0
    monkeypatch.setattr(confirmations.time, "monotonic", lambda: now)
    issued = store.issue("term_1", "configure terminal")
    monkeypatch.setattr(confirmations.time, "monotonic", lambda: now + 2)
    assert store.consume("term_1", "configure terminal", issued["confirmation_id"]) is False
