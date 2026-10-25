"""
meta.py — Cliente Meta WhatsApp Cloud API (Graph API).
Envio de mensagens, templates, mídia, indicadores, business profile.
"""

import os
import io
import requests

_DATAFY_BASE = "https://cloud.datafyapi.com.br/v1"
_AUTOZAP_BASE_PADRAO = "https://eduwhatsapi-elpj.onrender.com/api/v1"


def _carregar_config() -> None:
    """Gateway escolhido por WHATSAPP_PROVIDER: "datafy" (padrao) ou "autozap"
    (API propria do Eduardo, 25/10). A Datafy continua configurada para voltar
    trocando uma variavel."""
    global _PROVIDER, _PHONE_ID, _WABA_ID, _TOKEN, _BASE
    _PROVIDER = (os.getenv("WHATSAPP_PROVIDER", "datafy") or "datafy").strip().lower()
    if _PROVIDER == "autozap":
        _PHONE_ID = os.getenv("AUTOZAP_PHONE_NUMBER_ID", "")
        _WABA_ID = ""
        _TOKEN = os.getenv("AUTOZAP_API_KEY", "")
        _BASE = (os.getenv("AUTOZAP_API_URL", "") or _AUTOZAP_BASE_PADRAO).rstrip("/")
    else:
        _PHONE_ID = os.getenv("DATAFY_PHONE_NUMBER_ID", "")
        _WABA_ID = os.getenv("DATAFY_WABA_ID", "")
        _TOKEN = os.getenv("DATAFY_TOKEN", "")
        _BASE = _DATAFY_BASE


_PROVIDER = _PHONE_ID = _WABA_ID = _TOKEN = _BASE = ""
_carregar_config()


def provider() -> str:
    return _PROVIDER


def _autozap() -> bool:
    return _PROVIDER == "autozap"


def _wa_url(sufixo: str) -> str:
    """URL do AutoZap para o numero conectado: /whatsapp/{phone_number_id}/..."""
    return f"{_BASE}/whatsapp/{_PHONE_ID}/{sufixo}"


def _desembrulhar(res: dict) -> dict:
    """O AutoZap responde {"data": ...}; os chamadores esperam o objeto da Meta."""
    if res.get("ok") and isinstance(res.get("data"), dict) and "data" in res["data"]:
        res = dict(res, data=res["data"]["data"])
    return res


def _headers(content_type="application/json"):
    h = {"Authorization": f"Bearer {_TOKEN}"}
    if content_type:
        h["Content-Type"] = content_type
    return h


def _request(method, url, body=None, params=None, extra_headers=None):
    h = _headers()
    if extra_headers:
        h.update(extra_headers)
    try:
        r = requests.request(method, url, json=body, params=params,
                             headers=h, timeout=30)
        data = None
        try:
            data = r.json()
        except Exception:
            data = r.text
        short = str(data)[:300]
        url_path = url.split("//", 1)[-1].split("/", 1)[-1][:60] if "//" in url else url[:60]
        print(f"[meta] {method} {url_path} -> {r.status_code} | {short}", flush=True)
        if r.ok:
            return {"ok": True, "status": r.status_code, "data": data}
        return {"ok": False, "status": r.status_code,
                "error": f"HTTP {r.status_code}: {str(data)[:300]}"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "status": 0, "error": f"Connection error: {url}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "status": 0, "error": f"Timeout: {url}"}
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


def _messages_url():
    return f"{_BASE}/{_PHONE_ID}/messages"


def _send(payload):
    if not _autozap():
        return _request("POST", _messages_url(), body=payload)
    # O AutoZap poe o messaging_product ele mesmo.
    corpo = {k: v for k, v in payload.items() if k not in ("messaging_product", "recipient_type")}
    res = _request("POST", _wa_url("messages"), body=corpo)
    if res.get("ok") and isinstance(res.get("data"), dict):
        # Mesmo formato da Meta para quem le contacts/messages da resposta.
        d = res["data"].get("data") or {}
        res = dict(res, data=d.get("meta_response") or {
            "contacts": [{"wa_id": d.get("contact_wa_id", "")}],
            "messages": [{"id": d.get("wamid", "")}]})
    return res


# ── Envio de mensagens ───────────────────────────────────────────────────────


def send_text(number: str, text: str) -> dict:
    if not number:
        return {"ok": False, "status": 400, "error": "Numero vazio/invalido."}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "text",
        "text": {"body": text},
    }
    return _send(payload)


def send_image(number: str, media_id: str, caption: str = "") -> dict:
    if not number or not media_id:
        return {"ok": False, "status": 400, "error": "number e media_id requeridos."}
    img = {"id": media_id}
    if caption:
        img["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "image",
        "image": img,
    }
    return _send(payload)


def send_audio(number: str, media_id: str) -> dict:
    if not number or not media_id:
        return {"ok": False, "status": 400, "error": "number e media_id requeridos."}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "audio",
        "audio": {"id": media_id},
    }
    return _send(payload)


def send_document(number: str, media_id: str, filename: str = "", caption: str = "") -> dict:
    if not number or not media_id:
        return {"ok": False, "status": 400, "error": "number e media_id requeridos."}
    doc = {"id": media_id}
    if filename:
        doc["filename"] = filename
    if caption:
        doc["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "document",
        "document": doc,
    }
    return _send(payload)


def send_video(number: str, media_id: str, caption: str = "") -> dict:
    if not number or not media_id:
        return {"ok": False, "status": 400, "error": "number e media_id requeridos."}
    vid = {"id": media_id}
    if caption:
        vid["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "video",
        "video": vid,
    }
    return _send(payload)


def send_template(number: str, template_name: str, language_code: str = "pt_BR",
                  body_params: list[str] | None = None,
                  header_params: list[dict] | None = None) -> dict:
    if not number or not template_name:
        return {"ok": False, "status": 400, "error": "number e template_name requeridos."}
    components = []
    if header_params:
        components.append({"type": "header", "parameters": header_params})
    if body_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": v} for v in body_params],
        })
    tpl = {"name": template_name, "language": {"code": language_code}}
    if components:
        tpl["components"] = components
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "template",
        "template": tpl,
    }
    return _send(payload)


def send_interactive_list(number: str, header_text: str, body_text: str,
                          button_text: str, sections: list[dict]) -> dict:
    if not number:
        return {"ok": False, "status": 400, "error": "number requerido."}
    interactive = {
        "type": "list",
        "body": {"text": body_text},
        "action": {"button": button_text, "sections": sections},
    }
    if header_text:
        interactive["header"] = {"type": "text", "text": header_text}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "interactive",
        "interactive": interactive,
    }
    return _send(payload)


def send_interactive_buttons(number: str, body_text: str,
                             buttons: list[dict], header_text: str = "",
                             footer_text: str = "") -> dict:
    if not number:
        return {"ok": False, "status": 400, "error": "number requerido."}
    interactive = {
        "type": "button",
        "body": {"text": body_text},
        "action": {"buttons": buttons},
    }
    if header_text:
        interactive["header"] = {"type": "text", "text": header_text}
    if footer_text:
        interactive["footer"] = {"text": footer_text}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "interactive",
        "interactive": interactive,
    }
    return _send(payload)


# ── Indicadores e leitura ────────────────────────────────────────────────────


def mark_read(message_id: str) -> dict:
    if not message_id:
        return {"ok": False, "status": 400, "error": "message_id vazio."}
    if _autozap():
        return _request("POST", _wa_url("messages/read"), body={"message_id": message_id})
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    return _send(payload)


def send_typing(message_id: str) -> dict:
    if not message_id:
        return {"ok": False, "status": 400, "error": "message_id vazio."}
    if _autozap():
        return _request("POST", _wa_url("messages/read"),
                        body={"message_id": message_id, "typing": True})
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }
    return _send(payload)


# ── Mídia (upload/download) ─────────────────────────────────────────────────


def upload_media_bytes(file_bytes: bytes, mime_type: str, filename: str = "file") -> dict:
    url = _wa_url("media") if _autozap() else f"{_BASE}/{_PHONE_ID}/media"
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {_TOKEN}"},
                          data=None if _autozap() else {"messaging_product": "whatsapp"},
                          files={"file": (filename, io.BytesIO(file_bytes), mime_type)},
                          timeout=60)
        data = r.json() if r.ok else {"error": r.text[:300]}
        if r.ok and _autozap() and isinstance(data, dict):
            data = data.get("data") or {}
        print(f"[meta] POST /media upload_bytes -> {r.status_code} | {str(data)[:200]}", flush=True)
        if r.ok:
            return {"ok": True, "status": r.status_code, "data": data}
        return {"ok": False, "status": r.status_code, "error": str(data)[:300]}
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


def get_media_url(media_id: str) -> dict:
    url = f"{_BASE}/{media_id}"
    params = {"phone_number_id": _PHONE_ID}
    return _request("GET", url, params=params)


def get_webhook_media_url(media_id: str) -> dict:
    """Obtém URL de download de mídia recebida via webhook (Datafy, válida 30 dias)."""
    if _autozap():
        # O AutoZap entrega o arquivo direto (stream), sem URL intermediaria:
        # baixa aqui para saber o tipo, e download_media_bytes devolve o que ficou.
        url = _wa_url(f"media/{media_id}")
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {_TOKEN}"}, timeout=60)
        except Exception as e:
            return {"ok": False, "status": 0, "error": str(e)}
        print(f"[meta] GET media/{media_id[:20]} -> {r.status_code} len={len(r.content)}", flush=True)
        if not r.ok or not r.content:
            return {"ok": False, "status": r.status_code, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        _BAIXADAS[url] = r.content
        mime = (r.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
        return {"ok": True, "status": r.status_code, "data": {"url": url, "mime_type": mime}}
    url = f"https://cloud.datafyapi.com.br/media/{media_id}"
    return _request("GET", url)


# Midia ja baixada por get_webhook_media_url (AutoZap): {url: bytes}, usada uma vez.
_BAIXADAS: dict[str, bytes] = {}


def download_media_bytes(media_url: str) -> bytes:
    if media_url in _BAIXADAS:
        return _BAIXADAS.pop(media_url)
    try:
        r = requests.get(media_url, headers={"Authorization": f"Bearer {_TOKEN}"}, timeout=60)
        if r.ok:
            return r.content
        print(f"[meta] download_media falhou: {r.status_code}", flush=True)
        return b""
    except Exception as e:
        print(f"[meta] download_media erro: {e}", flush=True)
        return b""


# ── Templates ────────────────────────────────────────────────────────────────


def list_templates() -> dict:
    if _autozap():
        return _request("GET", _wa_url("templates"))
    url = f"{_BASE}/{_WABA_ID}/message_templates"
    return _request("GET", url)


def get_template(template_id: str) -> dict:
    if _autozap():
        # O AutoZap nao tem consulta por id: procura na lista.
        res = list_templates()
        if not res.get("ok"):
            return res
        lista = res.get("data") or {}
        lista = lista.get("data", []) if isinstance(lista, dict) else lista
        achado = next((t for t in lista if str(t.get("id")) == str(template_id)), None)
        return {"ok": True, "status": 200, "data": achado} if achado else \
            {"ok": False, "status": 404, "error": "Template nao encontrado"}
    url = f"{_BASE}/{template_id}"
    return _request("GET", url)


def delete_template(template_name: str) -> dict:
    if _autozap():
        return _desembrulhar(_request("DELETE", _wa_url(f"templates/{template_name}")))
    url = f"{_BASE}/{_WABA_ID}/message_templates"
    params = {"name": template_name}
    return _request("DELETE", url, params=params)


def create_template(name: str, category: str, language: str, body: str,
                    header_text: str = "", buttons: list | None = None) -> dict:
    """Cria um template na WABA via Meta Graph API.
    category: MARKETING | UTILITY | AUTHENTICATION
    language: ex 'pt_BR'
    body: texto do template, use {{1}}, {{2}} etc para variaveis
    header_text: (opcional) texto do header
    buttons: (opcional) lista de botoes, ex [{"type": "QUICK_REPLY", "text": "Sim"}]
    """
    components = []
    if header_text:
        components.append({
            "type": "HEADER",
            "format": "TEXT",
            "text": header_text,
        })
    if body:
        components.append({
            "type": "BODY",
            "text": body,
        })
    if buttons:
        components.append({
            "type": "BUTTONS",
            "buttons": buttons,
        })
    payload = {
        "name": name,
        "category": category.upper(),
        "language": language,
        "components": components,
    }
    if _autozap():
        return _desembrulhar(_request("POST", _wa_url("templates"), body=payload))
    url = f"{_BASE}/{_WABA_ID}/message_templates"
    return _request("POST", url, body=payload)


# ── Business Profile / Phone Info ────────────────────────────────────────────


def get_phone_info() -> dict:
    if _autozap():
        # Channel do AutoZap: verified_name, display_phone_number, quality_rating, status.
        return _desembrulhar(_request("GET", f"{_BASE}/channels/{_PHONE_ID}"))
    url = f"{_BASE}/{_PHONE_ID}"
    params = {"fields": "verified_name,display_phone_number,quality_rating,name_status,status"}
    return _request("GET", url, params=params)


def get_business_profile() -> dict:
    if _autozap():
        return _request("GET", _wa_url("business_profile"))
    url = f"{_BASE}/{_PHONE_ID}/whatsapp_business_profile"
    params = {"fields": "about,address,description,email,profile_picture_url,websites,vertical"}
    return _request("GET", url, params=params)


def update_business_profile(**kwargs) -> dict:
    if _autozap():
        return _desembrulhar(_request("POST", _wa_url("business_profile"), body=kwargs))
    url = f"{_BASE}/{_PHONE_ID}/whatsapp_business_profile"
    body = {"messaging_product": "whatsapp", **kwargs}
    return _request("POST", url, body=body)


# ── Configuração ─────────────────────────────────────────────────────────────


def config_problem() -> str | None:
    if _autozap():
        if not _TOKEN.startswith(("sk_live_", "sk_test_")):
            return "AUTOZAP_API_KEY nao configurada (sk_live_...)"
        if not _PHONE_ID:
            return "AUTOZAP_PHONE_NUMBER_ID nao configurado"
        return None
    if not _TOKEN or "cole_o" in _TOKEN.lower():
        return "DATAFY_TOKEN nao configurado no .env"
    if not _PHONE_ID or "cole_o" in _PHONE_ID.lower():
        return "DATAFY_PHONE_NUMBER_ID nao configurado no .env"
    if not _WABA_ID or "cole_o" in _WABA_ID.lower():
        return "DATAFY_WABA_ID nao configurado no .env"
    return None


# ── Datafy extras ────────────────────────────────────────────────────────────


def reload_config() -> None:
    """Recarrega variaveis de ambiente nos modulos (apos salvar .env pelo painel)."""
    from dotenv import load_dotenv
    load_dotenv(override=True)
    _carregar_config()


def get_phone_id() -> str:
    return _PHONE_ID


def get_waba_id() -> str:
    return _WABA_ID
