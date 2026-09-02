"""
memory.py — Histórico de conversa por usuário em memória.
Usa formato nativo do Gemini: {"role": "user"|"model", "parts": [{"text": "..."}]}
"""

import threading
import os

MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

_lock = threading.Lock()
_store: dict[str, list[dict]] = {}


def append(user: str, role: str, text: str) -> None:
    """Adiciona uma mensagem ao histórico do usuário e trunca se necessário."""
    entry = {"role": role, "parts": [{"text": text}]}
    with _lock:
        if user not in _store:
            _store[user] = []
        _store[user].append(entry)
        # Mantém no máximo MAX_HISTORY mensagens (mais recentes)
        if len(_store[user]) > MAX_HISTORY:
            _store[user] = _store[user][-MAX_HISTORY:]


def get(user: str) -> list[dict]:
    """Retorna o histórico pronto para enviar ao Gemini."""
    with _lock:
        return list(_store.get(user, []))


def reset(user: str) -> None:
    """Limpa o histórico de um usuário."""
    with _lock:
        _store.pop(user, None)
