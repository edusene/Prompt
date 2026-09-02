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


def _post(endpoint: str, body: dict) -> None:
    url = f"{_BASE_URL}{endpoint}"
    try:
        r = requests.post(url, json=body, headers=_HEADERS, timeout=10)
        if not r.ok:
            print(f"[uazapi] POST {endpoint} erro {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[uazapi] POST {endpoint} exceção: {e}")


def _put(endpoint: str, body: dict) -> None:
    url = f"{_BASE_URL}{endpoint}"
    try:
        r = requests.put(url, json=body, headers=_HEADERS, timeout=10)
        if not r.ok:
            print(f"[uazapi] PUT {endpoint} erro {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[uazapi] PUT {endpoint} exceção: {e}")


def _get(endpoint: str, params: dict | None = None) -> dict | None:
    url = f"{_BASE_URL}{endpoint}"
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        if r.ok:
            return r.json()
        print(f"[uazapi] GET {endpoint} erro {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[uazapi] GET {endpoint} exceção: {e}")
    return None


def _delete(endpoint: str, body: dict | None = None) -> dict | None:
    url = f"{_BASE_URL}{endpoint}"
    try:
        r = requests.delete(url, json=body, headers=_HEADERS, timeout=10)
        if r.ok:
            return r.json()
        print(f"[uazapi] DELETE {endpoint} erro {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[uazapi] DELETE {endpoint} exceção: {e}")
    return None


# ── Mensagens (existente — MVP) ──────────────────────────────────────────────


def send_text(number: str, text: str) -> None:
    """POST /send/text"""
    _post("/send/text", {"number": number, "text": text})


def send_presence(number: str, presence: str) -> None:
    """POST /send/presence  |  presence: 'composing' | 'paused'"""
    _post("/send/presence", {"number": number, "presence": presence})


def mark_read(number: str, message_id: str) -> None:
    """PUT /send/read"""
    _put("/send/read", {"number": number, "messageId": message_id})


# ── Instância ────────────────────────────────────────────────────────────────


def get_qr_code() -> dict | None:
    """GET /instance/connect — {qrcode, pairingCode}"""
    return _get("/instance/connect")


def get_instance_status() -> dict | None:
    """GET /instance/status — {status: open|connecting|close|qr}"""
    return _get("/instance/status")


def get_instance_profile() -> dict | None:
    """GET /instance/profile — {name, phone, about}"""
    return _get("/instance/profile")


def disconnect_instance() -> dict | None:
    """DELETE /instance/logout"""
    return _delete("/instance/logout")


def restart_instance() -> None:
    """POST /instance/restart"""
    _post("/instance/restart", {})


# ── Mídia ────────────────────────────────────────────────────────────────────


def send_image(number: str, image_url: str, caption: str = "") -> None:
    """POST /send/image"""
    body: dict = {"number": number, "image": image_url}
    if caption:
        body["caption"] = caption
    _post("/send/image", body)


def send_document(number: str, doc_url: str, filename: str, caption: str = "") -> None:
    """POST /send/document"""
    body: dict = {"number": number, "document": doc_url, "fileName": filename}
    if caption:
        body["caption"] = caption
    _post("/send/document", body)


# ── Contatos ─────────────────────────────────────────────────────────────────


def get_contact_info(number: str) -> dict | None:
    """GET /contact/info?number=..."""
    return _get("/contact/info", params={"number": number})


def check_number(number: str) -> dict | None:
    """GET /chat/check-number?number=..."""
    return _get("/chat/check-number", params={"number": number})
