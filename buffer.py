"""
buffer.py — Debounce buffer por usuário.
Cada mensagem nova reseta o timer; flush só ocorre após BUFFER_SECONDS de silêncio.
"""

import threading
from typing import Callable


class MessageBuffer:
    def __init__(self, wait_seconds: float, on_flush: Callable[[str, list[str]], None]):
        self._wait = wait_seconds
        self._on_flush = on_flush
        self._lock = threading.Lock()
        # {user: {"texts": [...], "timer": Timer | None}}
        self._state: dict[str, dict] = {}

    def add(self, user: str, text: str) -> None:
        with self._lock:
            if user not in self._state:
                self._state[user] = {"texts": [], "timer": None}

            self._state[user]["texts"].append(text)

            # Cancela timer anterior e cria novo (debounce)
            timer = self._state[user]["timer"]
            if timer is not None:
                timer.cancel()

            new_timer = threading.Timer(self._wait, self._flush, args=[user])
            self._state[user]["timer"] = new_timer
            new_timer.start()

    def _flush(self, user: str) -> None:
        with self._lock:
            if user not in self._state:
                return
            texts = self._state[user]["texts"]
            if not texts:
                return
            # Limpa estado antes de chamar o callback (evita race condition)
            self._state[user] = {"texts": [], "timer": None}

        print(f"[buffer] flush user={user} msgs={len(texts)}")
        self._on_flush(user, texts)
