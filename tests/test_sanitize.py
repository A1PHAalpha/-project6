"""sanitize_text / sanitize_evidence_list 脱敏规则单测。"""
from rag_engine.sanitize import sanitize_evidence_list, sanitize_text


def test_masks_username_password():
    out = sanitize_text("username admin password S3cret-Pass!")
    assert "S3cret-Pass!" not in out
    assert "[REDACTED]" in out


def test_masks_username_privilege_password():
    out = sanitize_text("username admin privilege 15 password Secret123")
    assert "Secret123" not in out


def test_masks_enable_secret_with_type():
    out = sanitize_text("enable secret 5 $1$mERr$abcdefghijklmnop")
    assert "$1$mERr$abcdefghijklmnop" not in out
    assert "[REDACTED]" in out


def test_masks_enable_password_with_type():
    out = sanitize_text("enable password 7 08324D5C1C")
    assert "08324D5C1C" not in out


def test_masks_snmp_community():
    out = sanitize_text("snmp-server community publicRo RO")
    assert "publicRo" not in out


def test_masks_crypto_isakmp_key():
    out = sanitize_text("crypto isakmp key MySecretKey address 10.0.0.1")
    assert "MySecretKey" not in out
    assert "[REDACTED]" in out


def test_masks_key_string():
    out = sanitize_text(" key-string SuperSecret")
    assert "SuperSecret" not in out


def test_masks_tacacs_key():
    out = sanitize_text("tacacs-server host 10.0.0.9 key TacKey123")
    assert "TacKey123" not in out


def test_masks_line_vty_password_block():
    text = "line vty 0 4\n password VtySecret\n transport input ssh"
    out = sanitize_text(text)
    assert "VtySecret" not in out


def test_plaintext_password_absent():
    out = sanitize_text("user admin password P@ssw0rd! privilege 15")
    assert "P@ssw0rd!" not in out


def test_empty_text_returns_empty():
    assert sanitize_text("") == ""


def test_sanitize_evidence_list_does_not_mutate_source():
    evidence = [{"device_name": "HQ-R1", "summary": "password secret123"}]
    out = sanitize_evidence_list(evidence)
    assert "secret123" not in out[0]["summary"]
    assert "secret123" in evidence[0]["summary"]
