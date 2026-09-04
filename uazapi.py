"""
uazapi.py — Cliente UAZAPI (free.uazapi.com).
Endpoints testados e confirmados: POST /instance/connect, POST /webhook, GET /instance/status.
"""

import os
import requests

_BASE_URL = os.getenv("UAZAPI_BASE_URL", "").rstrip("/")
_TOKEN = os.getenv("UAZAPI_INSTANCE_TOKEN", "")
_HEADERS = {"token": _TOKEN, "Content-Type": "application/json"}


def _request(method: str, endpoint: str, body: dict | None = None, params: dict | None = None) -> dict:
    url = f"{_BASE_URL}{endpoint}"
    try:
        r = requests.request(method, url, json=body, params=params, headers=_HEADERS, timeout=15)
        data = None
        try:
            data = r.json()
        except Exception:
            data = r.text
        print(f"[uazapi] {method} {endpoint} -> {r.status_code} | {str(data)[:300]}")
        if r.ok:
            return {"ok": True, "status": r.status_code, "data": data}
        return {"ok": False, "status": r.status_code, "error": f"HTTP {r.status_code}: {str(data)[:200]}"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "status": 0, "error": f"Connection error: {url}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "status": 0, "error": f"Timeout: {url}"}
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


def send_text(number: str, text: str) -> dict:
    return _request("POST", "/send/text", {"number": number, "text": text})


def send_presence(number: str, presence: str) -> dict:
    return _request("POST", "/send/presence", {"number": number, "presence": presence})


def mark_read(number: str, message_id: str) -> dict:
    return _request("PUT", "/send/read", {"number": number, "messageId": message_id})


def get_qr_code() -> dict:
    """POST /instance/connect — retorna QR code base64."""
    return _request("POST", "/instance/connect", {})


def get_instance_status() -> dict:
    """GET /instance/status — retorna {instance: {status, qrcode, ...}}."""
    return _request("GET", "/instance/status")


def disconnect_instance() -> dict:
    """Desconecta instância (free tier: PUT /instance/logout)."""
    r = _request("PUT", "/instance/logout", {})
    if not r["ok"]:
        r = _request("POST", "/instance/logout", {})
    if not r["ok"]:
        r = _request("DELETE", "/instance/logout")
    return r


def restart_instance() -> dict:
    """Reinicia instância (free tier: PUT /instance/restart)."""
    r = _request("PUT", "/instance/restart", {})
    if not r["ok"]:
        r = _request("POST", "/instance/restart", {})
    return r


def configure_webhook(webhook_url: str, events: list[str] | None = None) -> dict:
    """Configura webhook — POST /webhook com campo 'url' (free.uazapi.com)."""
    if events is None:
        events = ["message", "connection"]
    body = {"url": webhook_url, "events": events, "enabled": True}
    return _request("POST", "/webhook", body)


def get_webhook() -> dict:
    return _request("GET", "/webhook")
