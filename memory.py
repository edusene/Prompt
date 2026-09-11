"""
memory.py — Histórico de conversa por usuário em memória.
Usa formato interno: {"role": "user"|"assistant", "parts": [{"text": "..."}]}
Suporta parts multimodais (text + inline_data para imagens).
"""

import threading
import os

MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

_lock = threading.Lock()
_store: dict[str, list[dict]] = {}


def append(user: str, role: str, text: str) -> None:
    """Adiciona uma mensagem de texto ao histórico do usuário."""
    entry = {"role": role, "parts": [{"text": text}]}
    _append_entry(user, entry)


def append_multimodal(user: str, role: str, parts: list[dict]) -> None:
    """Adiciona uma mensagem com parts multimodais (text + inline_data)."""
    entry = {"role": role, "parts": parts}
    _append_entry(user, entry)


def _append_entry(user: str, entry: dict) -> None:
    with _lock:
        if user not in _store:
            _store[user] = []
        _store[user].append(entry)
        if len(_store[user]) > MAX_HISTORY:
            _store[user] = _store[user][-MAX_HISTORY:]


def get(user: str) -> list[dict]:
    """Retorna o histórico pronto para enviar ao LLM."""
    with _lock:
        return list(_store.get(user, []))


def reset(user: str) -> None:
    """Limpa o histórico de um usuário."""
    with _lock:
        _store.pop(user, None)
