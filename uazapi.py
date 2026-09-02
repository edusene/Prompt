"""
uazapi.py — Cliente UAZAPI.
Referência completa dos endpoints: LLMs-uazapi.txt.
"""

import os
import requests

_BASE_URL = os.getenv("UAZAPI_BASE_URL", "").rstrip("/")
_TOKEN = os.getenv("UAZAPI_INSTANCE_TOKEN", "")
_HEADERS = {"token": _TOKEN, "Content-Type": "application/json"}


# ── HTTP helpers ─────────────────────────────────────────────────────────────


def _request(method: str, endpoint: str, body: dict | None = None, params: dict | None = None) -> dict:
    """Retorna sempre {ok, status, data, error}."""
    url = f"{_BASE_URL}{endpoint}"
    try:
        r = requests.request(method, url, json=body, params=params, headers=_HEADERS, timeout=15)
        data = None
        try:
            data = r.json()
        except Exception:
            data = r.text
        if r.ok:
            return {"ok": True, "status": r.status_code, "data": data}
        return {"ok": False, "status": r.status_code, "error": f"HTTP {r.status_code}: {str(data)[:200]}"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "status": 0, "error": f"Não foi possível conectar em {url}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "status": 0, "error": f"Timeout ao conectar em {url}"}
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


# ── Mensagens ────────────────────────────────────────────────────────────────


def send_text(number: str, text: str) -> dict:
    return _request("POST", "/send/text", {"number": number, "text": text})


def send_presence(number: str, presence: str) -> dict:
    return _request("POST", "/send/presence", {"number": number, "presence": presence})


def mark_read(number: str, message_id: str) -> dict:
    return _request("PUT", "/send/read", {"number": number, "messageId": message_id})


# ── Instância ────────────────────────────────────────────────────────────────


def get_qr_code() -> dict:
    return _request("GET", "/instance/connect")


def get_instance_status() -> dict:
    return _request("GET", "/instance/status")


def get_instance_profile() -> dict:
    return _request("GET", "/instance/profile")


def disconnect_instance() -> dict:
    return _request("DELETE", "/instance/logout")


def restart_instance() -> dict:
    return _request("POST", "/instance/restart", {})


# ── Webhook ──────────────────────────────────────────────────────────────────


def configure_webhook(webhook_url: str, events: list[str] | None = None) -> dict:
    if events is None:
        events = ["message"]
    return _request("PUT", "/webhook", {"webhookUrl": webhook_url, "events": events})


def get_webhook() -> dict:
    return _request("GET", "/webhook")
