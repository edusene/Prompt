"""
uazapi.py — Cliente UAZAPI (free.uazapi.com).
Endpoints testados e confirmados: POST /instance/connect, POST /webhook, GET /instance/status.
"""

import os
import threading
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
        # errors="replace": logs podem conter emoji do WhatsApp e o stdout do
        # Windows pode estar em cp1252 — nunca deixar o print derrubar o fluxo.
        print(f"[uazapi] {method} {endpoint} -> {r.status_code} | {str(data)[:300]}", flush=True)
        if r.ok:
            return {"ok": True, "status": r.status_code, "data": data}
        return {"ok": False, "status": r.status_code, "error": f"HTTP {r.status_code}: {str(data)[:200]}"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "status": 0, "error": f"Connection error: {url}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "status": 0, "error": f"Timeout: {url}"}
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


def normalize_number(number: str) -> str:
    """Limpa o número para o formato que a UAZAPI espera.

    Aceita formatos com +, espaços, hífens e/ou sufixo de JID (@s.whatsapp.net, @lid).
    Retorna o PN (ex.: 5569992170341) ou o JID <lid>@lid quando for um LID.
    """
    s = str(number or "").strip()
    if not s:
        return ""
    # Mantém o LID com seu sufixo próprio (a UAZAPI resolve só assim).
    if s.lower().endswith("@lid"):
        return s
    # Remove qualquer sufixo de JID comum (@s.whatsapp.net, @g.us, @broadcast...).
    if "@" in s:
        s = s.split("@")[0]
    # Remove tudo que não seja dígito.
    digits = "".join(c for c in s if c.isdigit())
    return digits


def _as_lid_jid(number: str) -> str:
    """Se 'number' é um LID puro (dígitos), devolve o JID no formato <lid>@lid."""
    digits = "".join(c for c in str(number or "") if c.isdigit())
    if not digits:
        return str(number or "")
    return f"{digits}@lid"


def send_text(number: str, text: str) -> dict:
    number = normalize_number(number)
    if not number:
        return {"ok": False, "status": 400, "error": "Número vazio/inválido."}
    r = _request("POST", "/send/text", {"number": number, "text": text})
    # A UAZAPI sempre devolve o chatid canônico (PN real). Expõe o PN resolvido
    # para que painel/webhook gravem tudo sob a mesma identidade do contato.
    if r.get("ok"):
        data = r.get("data")
        if isinstance(data, dict) and data.get("chatid"):
            r["resolved"] = str(data["chatid"]).split("@")[0]
        return r
    # Quando o remetente veio como LID no webhook, o número puro não é resolvido
    # pela UAZAPI ("no LID found"). Tenta de novo com o sufixo @lid.
    if "LID" in str(r.get("error", "")).upper():
        lid_jid = _as_lid_jid(number)
        if lid_jid and lid_jid != number:
            print(f"[uazapi] retry com LID jid {lid_jid}")
            r2 = _request("POST", "/send/text", {"number": lid_jid, "text": text})
            if r2.get("ok"):
                # Expõe também o número real (PN) resolvido para uso no painel/tela.
                r2["data"] = r2.get("data") or {}
                if isinstance(r2["data"], dict) and r2["data"].get("chatid"):
                    r2["resolved"] = str(r2["data"]["chatid"]).split("@")[0]
                return r2
    return r


def send_presence(number: str, presence: str) -> dict:
    number = normalize_number(number)
    if not number:
        return {"ok": False, "status": 400, "error": "Número vazio/inválido."}
    return _request("POST", "/message/presence", {"number": number, "presence": presence})


def mark_read(number: str, message_id: str = "") -> dict:
    """Marca o chat como lido na UAZAPI v3 (POST /chat/read ou /message/markread)."""
    number = normalize_number(number)
    if not number:
        return {"ok": False, "status": 400, "error": "Número vazio/inválido."}
    # v3: /chat/read marca todo o chat (não precisa do messageId).
    r = _request("POST", "/chat/read", {"number": f"{number}@s.whatsapp.net", "read": True})
    if not r.get("ok") and message_id:
        # Fallback: marca apenas mensagens específicas.
        r2 = _request("POST", "/message/markread", {"id": [str(message_id)]})
        if r2.get("ok"):
            return r2
    return r


def check_numbers(numbers: list[str]) -> list[dict]:
    """POST /chat/check — resolve PN/LID. Retorna a lista de itens da UAZAPI
    (cada item: query, isInWhatsapp, jid, lid, verifiedName, error)."""
    r = _request("POST", "/chat/check", {"numbers": numbers})
    if r.get("ok") and isinstance(r.get("data"), list):
        return r["data"]
    return []


_lid_cache: dict[str, str] = {}
_lid_cache_lock = threading.Lock()


def resolve_lid_to_pn(number: str) -> str:
    """Se 'number' for um LID (ex.: 223656663867638, 15-16 dígitos, não é um PN),
    resolve para o PN canônico via POST /chat/check (com cache em memória).

    O /chat/check só converte LID→PN quando o LID é enviado com o sufixo '@lid'.
    Consultamos de uma vez o número puro e a forma @lid e decidimos:
      - puro existe no WhatsApp -> é um PN (o 'jid' traz a forma canônica);
      - só a forma @lid resolve -> é um LID e o 'jid' traz o PN real.
    PNs plausíveis (10-13 dígitos) nem chegam a consumir chamada de API.
    """
    n = normalize_number(number)
    if n.lower().endswith("@lid"):
        n = "".join(c for c in n.split("@")[0] if c.isdigit())
    if not n or not n.isdigit():
        return number
    if 10 <= len(n) <= 13:
        return n  # Parece um PN (DDI+DDD+número): nada a resolver.
    with _lid_cache_lock:
        if n in _lid_cache:
            return _lid_cache[n]
    items = check_numbers([n, f"{n}@lid"])
    by_query = {str(it.get("query", "")): it for it in items if isinstance(it, dict)}
    pn = ""
    plain = by_query.get(n) or {}
    if plain.get("isInWhatsapp") and plain.get("jid"):
        pn = str(plain["jid"]).split("@")[0]
    else:
        lid_item = by_query.get(f"{n}@lid") or {}
        jid = str(lid_item.get("jid", ""))
        if lid_item.get("isInWhatsapp") and jid.endswith("@s.whatsapp.net"):
            pn = jid.split("@")[0]
    if not pn:
        pn = n  # não achou: mantém o próprio número
    with _lid_cache_lock:
        _lid_cache[n] = pn
    print(f"[uazapi] resolve_lid_to_pn: {n} -> {pn}")
    return pn


def get_avatar(number: str) -> str:
    """POST /chat/avatar — retorna a URL da foto de perfil do contato (ou '').
    Aceita PN ou LID (a UAZAPI resolve ambos). Nunca lança exceção."""
    try:
        n = normalize_number(number)
        if not n:
            return ""
        # normalize_number preserva sufixo @lid; para avatares precisamos de jid
        if "@" not in n:
            n = f"{n}@s.whatsapp.net"
        r = _request("POST", "/chat/avatar", {"number": n})
        if r.get("ok") and isinstance(r.get("data"), dict):
            return str(r["data"].get("url", "") or "")
        return ""
    except Exception as e:
        print(f"[uazapi] get_avatar erro: {e}")
        return ""


def chat_archive(number: str, archive: bool) -> dict:
    """Arquiva/desarquiva o chat no WhatsApp (best-effort)."""
    return _request("PUT", "/chat/archive", {"number": number, "archive": archive})


def chat_pin(number: str, pin: bool) -> dict:
    """Fixap/desafixa o chat no WhatsApp (best-effort)."""
    return _request("PUT", "/chat/pin", {"number": number, "pin": pin})


def chat_mute(number: str, mute: bool, mute_expiration: int = 0) -> dict:
    """Silencia/ativa o som do chat no WhatsApp. mute_expiration 0 = para sempre."""
    return _request("PUT", "/chat/mute", {"number": number, "mute": mute, "muteExpiration": mute_expiration})


def chat_delete(number: str) -> dict:
    """Apaga o chat no WhatsApp (best-effort)."""
    return _request("DELETE", "/chat", {"number": number})


def get_qr_code() -> dict:
    """GET /instance/connect — retorna QR code base64 (conforme docs uazapi)."""
    return _request("GET", "/instance/connect")


def get_instance_status() -> dict:
    """GET /instance/status — retorna {instance: {status, qrcode, ...}}."""
    return _request("GET", "/instance/status")


def disconnect_instance() -> dict:
    """Desconecta instância (DELETE /instance/logout conforme docs uazapi)."""
    r = _request("DELETE", "/instance/logout")
    if not r["ok"]:
        # Fallback: POST /instance/logout (algumas instâncias usam POST)
        r = _request("POST", "/instance/logout", {})
    if not r["ok"]:
        status = r.get("status", 0)
        if status == 405:
            return {"ok": False, "status": 405,
                    "error": "Operacao dedesconexao nao suportada por esta instancia. "
                             "Use o painel da UAZAPI para desconectar."}
    return r


def restart_instance() -> dict:
    """Reinicia instância (POST /instance/restart conforme docs uazapi)."""
    r = _request("POST", "/instance/restart", {})
    if not r["ok"]:
        # Fallback: PUT /instance/restart
        r = _request("PUT", "/instance/restart", {})
    if not r["ok"]:
        status = r.get("status", 0)
        if status == 405:
            return {"ok": False, "status": 405,
                    "error": "Operacao dereinicio nao suportada por esta instancia. "
                             "Use o painel da UAZAPI para reiniciar."}
    return r


def configure_webhook(webhook_url: str, events: list[str] | None = None) -> dict:
    """Configura webhook — POST /webhook com campo 'url' (free.uazapi.com)."""
    if events is None:
        events = ["message", "connection"]
    body = {"url": webhook_url, "events": events, "enabled": True}
    return _request("POST", "/webhook", body)


def get_webhook() -> dict:
    return _request("GET", "/webhook")


def config_problem() -> str | None:
    """Retorna mensagem de erro se a UAZAPI não estiver configurada, ou None se OK."""
    base = _BASE_URL or ""
    token = _TOKEN or ""
    if not token or "cole_o_token" in token:
        return "UAZAPI_INSTANCE_TOKEN nao configurado no .env"
    if (not base
            or "seudominio" in base.lower()
            or "placeholder" in base.lower()
            or not base.startswith("http")):
        return "UAZAPI_BASE_URL nao configurado no .env"
    return None
