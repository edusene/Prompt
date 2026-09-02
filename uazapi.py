"""
uazapi.py — Cliente UAZAPI.
Referência completa dos endpoints: LLMs-uazapi.txt.
"""

import os
import requests

_BASE_URL = os.getenv("UAZAPI_BASE_URL", "").rstrip("/")
_TOKEN = os.getenv("UAZAPI_INSTANCE_TOKEN", "")
_HEADERS = {"token": _TOKEN, "Content-Type": "application/json"}


# ── HTTP helper ──────────────────────────────────────────────────────────────


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
        print(f"[uazapi] {method} {endpoint} → {r.status_code} | {str(data)[:300]}")
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
    """POST /instance/connect — retorna QR code."""
    return _request("POST", "/instance/connect", {})


def get_instance_status() -> dict:
    """GET /instance/status — retorna status da conexão."""
    return _request("GET", "/instance/status")


def disconnect_instance() -> dict:
    """Tenta desconectar — endpoints variam por servidor."""
    r = _request("DELETE", "/instance/logout")
    if not r["ok"]:
        r = _request("POST", "/instance/logout", {})
    if not r["ok"]:
        r = _request("POST", "/instance/disconnect", {})
    return r


def restart_instance() -> dict:
    """Tenta reiniciar — endpoints variam por servidor."""
    for method, path in [("POST", "/instance/restart"), ("PUT", "/instance/restart"), ("GET", "/instance/restart"), ("POST", "/instance/reconnect"), ("PUT", "/instance/reconnect")]:
        r = _request(method, path, {} if method != "GET" else None)
        if r["ok"]:
            return r
    return {"ok": False, "status": 0, "error": "Endpoint de reinício não encontrado neste servidor"}


# ── Webhook ──────────────────────────────────────────────────────────────────


def configure_webhook(webhook_url: str, events: list[str] | None = None) -> dict:
    """Configura webhook — PUT /webhook (documentação oficial), com fallback POST."""
    if events is None:
        events = ["message"]
    body = {"webhookUrl": webhook_url, "events": events}
    # Documentação oficial: PUT /webhook
    r = _request("PUT", "/webhook", body)
    if not r["ok"]:
        # Fallback para servidores que só aceitam POST
        r = _request("POST", "/webhook", body)
    return r


def get_webhook() -> dict:
    return _request("GET", "/webhook")


# ── Instância (endpoints oficiais UAZAPI) ────────────────────────────────────


def get_qr_code() -> dict:
    """GET /instance/connect — retorna QR code (documentação oficial)."""
    return _request("GET", "/instance/connect")


def get_instance_status() -> dict:
    """GET /instance/status — retorna status da conexão."""
    return _request("GET", "/instance/status")


def disconnect_instance() -> dict:
    """Desconecta instância — DELETE /instance/logout (documentação oficial)."""
    return _request("DELETE", "/instance/logout")


def restart_instance() -> dict:
    """Reinicia instância — POST /instance/restart (documentação oficial)."""
    return _request("POST", "/instance/restart", {})
