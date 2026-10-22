"""Short-lived, one-time confirmations for terminal write commands."""

import hashlib
import secrets
import threading
import time


class WriteConfirmationStore:
    def __init__(self, ttl_seconds: int = 120):
        self._ttl = ttl_seconds
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _hash(self, command: str) -> str:
        return hashlib.sha256((command or "").strip().encode("utf-8")).hexdigest()

    def _prune(self, now: float) -> None:
        for token, item in list(self._items.items()):
            if now > item["expires_at"]:
                self._items.pop(token, None)

    def issue(self, session_id: str, command: str) -> dict:
        token = f"wc_{secrets.token_urlsafe(24)}"
        issued_at = time.monotonic()
        with self._lock:
            self._prune(issued_at)
            self._items[token] = {
                "session_id": session_id,
                "command_hash": self._hash(command),
                "expires_at": issued_at + self._ttl,
            }
        return {
            "confirmation_id": token,
            "expires_at": issued_at + self._ttl,
        }

    def consume(self, session_id: str, command: str, confirmation_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(confirmation_id)
            if not item:
                return False
            if now > item["expires_at"]:
                self._items.pop(confirmation_id, None)
                return False
            if item["session_id"] != session_id:
                return False
            if item["command_hash"] != self._hash(command):
                return False
            self._items.pop(confirmation_id, None)
            return True

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            for token, item in list(self._items.items()):
                if item["session_id"] == session_id:
                    self._items.pop(token, None)
