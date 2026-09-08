"""
app.py — Flask webhook + pipeline de flush do agente WhatsApp + Gemini.
+ Painel CRM com autenticação, SSE e API REST completa.
"""

import os
import sys
import json
import time
import threading

from dotenv import load_dotenv

# override=True: o .env do projeto vence placeholders/eventuais variáveis globais
# (ex.: UAZAPI_BASE_URL=https://seudominio.uazapi.com definida no Windows).
load_dotenv(override=True)

# Windows: o stdout pode estar em cp1252/charmap; emoji do WhatsApp nos logs
# (prints de payload) lançaria UnicodeEncodeError e quebraria o pipeline.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

from flask import Flask, request, jsonify, Response, redirect, url_for, render_template, session

import memory
import llm
import database
import events
import uazapi
import knowledge_rag
from uazapi import send_text, send_presence, mark_read, chat_archive, chat_pin, chat_mute, chat_delete, download_media
from buffer import MessageBuffer
from auth import login_required, do_login, do_logout

# Tipos de mensagem que cargamos media desde UAZAPI para mostrar en el panel.
# "media" = tipo genérico que algumas instâncias v3 enviam para qualquer mídia;
# nesses casos o tipo concreto (image/video/audio/...) é resolvido pelo mimeType.
MEDIA_TYPES = {"image", "video", "audio", "ptt", "document", "sticker", "gif", "media"}

_MEDIA_TYPE_MAP = {
    "image": "image", "imagem": "image", "imagemessage": "image", "photo": "image",
    "video": "video", "videomessage": "video",
    "audio": "audio", "audiomessage": "audio", "ptt": "ptt", "voice": "ptt",
    "sticker": "sticker", "stickermessage": "sticker", "figura": "sticker",
    "document": "document", "documentmessage": "document", "file": "document",
    "gif": "gif", "mediamessage": "media",
}


def _norm_msg_type(raw) -> str:
    """Normaliza o tipo vindo da UAZAPI para um dos MEDIA_TYPES."""
    t = str(raw or "").strip().lower()
    return _MEDIA_TYPE_MAP.get(t, t)


def _mime_to_kind(mime: str) -> str:
    """Converte um mimeType em tipo concreto de mídia."""
    mime = str(mime or "").lower()
    if mime.startswith("image/"):
        return "sticker" if "sticker" in mime else "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "ptt" if "ogg" in mime or "opus" in mime else "audio"
    if mime:
        return "document"
    return ""


def _resolve_msg_type(msg: dict, msg_type) -> str:
    """Resolve o tipo concreto da mensagem.

    Instâncias v3 às vezes enviam só "media" (genérico). Nesse caso deduzimos
    pelo mimeType ou pelas chaves do payload aninhado (imageMessage etc.),
    para que a mídia seja baixada e renderizada no painel.
    """
    t = _norm_msg_type(msg_type)
    if t and t not in ("media", "unknown"):
        return t
    media = msg.get("media") if isinstance(msg.get("media"), dict) else {}
    mime = str(
        media.get("mimeType")
        or msg.get("mimeType") or msg.get("Mimetype") or msg.get("MIMEType")
        or (msg.get("message", {}).get("mimeType") if isinstance(msg.get("message"), dict) else "")
        or ""
    )
    kind = _mime_to_kind(mime)
    if kind:
        return kind
    # Chaves típicas do payload aninhado do Baileys/v3.
    nested = msg.get("message") if isinstance(msg.get("message"), dict) else {}
    for key in ("imageMessage", "videoMessage", "audioMessage", "stickerMessage",
                "documentMessage", "pttMessage"):
        if key in nested:
            return _MEDIA_TYPE_MAP.get(key.lower(), "media")
    return t or "text"


def _is_media_type(msg_type: str) -> bool:
    return str(msg_type or "").lower().strip() in MEDIA_TYPES


def _extract_media_url(msg: dict) -> str:
    """Extrai a URL temporária da mídia do payload do webhook, se houver.

    Muitos payloads (v2 e v3) trazem media.url direto — nesse caso não
    precisamos do /send/download-media: a URL já serve para renderizar.
    Suporta formatos v2, v3-A, v3-B e WhatsApp native (imageMessage etc.).
    """
    # (1) media dict direto: msg.media.url, msg.Media.url
    for cand in [msg.get("media"), msg.get("Media")]:
        if isinstance(cand, dict):
            u = str(cand.get("url") or cand.get("link") or cand.get("MediaUrl") or cand.get("mediaUrl") or "")
            if u.startswith("http"):
                return u
    # (2) Aninhado em msg.message.media
    nested = msg.get("message")
    if isinstance(nested, dict):
        for cand in [nested.get("media"), nested.get("Media")]:
            if isinstance(cand, dict):
                u = str(cand.get("url") or cand.get("link") or cand.get("MediaUrl") or "")
                if u.startswith("http"):
                    return u
    # (3) WhatsApp native: msg.imageMessage.url, msg.videoMessage.url, etc.
    for msg_key in ("imageMessage", "videoMessage", "audioMessage", "documentMessage",
                    "stickerMessage", "pttMessage", "gifMessage"):
        sub = msg.get(msg_key)
        if isinstance(sub, dict):
            u = str(sub.get("url") or sub.get("directPath") or "")
            if u.startswith("http"):
                return u
    # (4) content como dict com url/link
    content = msg.get("content")
    if isinstance(content, dict):
        u = str(content.get("url") or content.get("link") or content.get("mediaUrl") or "")
        if u.startswith("http"):
            return u
    # (5) Chaves soltas no msg
    for key in ("url", "mediaUrl", "MediaUrl", "media_url", "mediaLink"):
        u = str(msg.get(key) or "")
        if u.startswith("http"):
            return u
    return ""


def _maybe_store_media_hint(saved, msg: dict) -> None:
    """Se o payload trouxer URL de mídia, guarda direto (sem download)."""
    if not saved:
        return
    try:
        if str(saved.get("media_url") or ""):
            return
        url = _extract_media_url(msg)
        if url:
            database.update_message_media(saved["id"], url)
            print(f"[media] msg={saved['id']} url do payload guardada")
        else:
            _media_keys = [k for k in msg.keys() if "media" in k.lower() or "url" in k.lower() or "image" in k.lower() or "video" in k.lower() or "audio" in k.lower()]
            nested = msg.get("message")
            _nested_media_keys = list(nested.keys())[:10] if isinstance(nested, dict) else []
            print(f"[media] msg={saved['id']} payload SEM URL media_keys={_media_keys} nested_keys={_nested_media_keys}")
    except Exception as e:
        print(f"[media] erro guardando url do payload: {e}")


def _download_media_async(message_db_id, number, message_id):
    """Descarga la media (imagen/audio/video/sticker) en background.

    Delega en _fetch_and_store_media, que también usa el endpoint on-demand:
    así una misma mensagem nunca se baixa duas vezes em paralelo.
    """
    try:
        m = database.get_message(message_db_id)
        if not m:
            return
        uri = _fetch_and_store_media(m)
        if uri:
            print(f"[media] msg={message_db_id} guardado len={len(uri)}")
        else:
            print(f"[media] falha download id={message_id}")
    except Exception as e:
        print(f"[media] erro download id={message_id}: {e}")


# Una lock por mensagem: evita downloads duplicados em paralelo
# (webhook em background + pedido on-demand do painel).
_media_download_locks: dict[int, threading.Lock] = {}
_media_locks_guard = threading.Lock()


def _get_media_lock(msg_id: int) -> threading.Lock:
    with _media_locks_guard:
        lock = _media_download_locks.get(msg_id)
        if not lock:
            lock = threading.Lock()
            _media_download_locks[msg_id] = lock
        return lock


def _fetch_and_store_media(m: dict) -> str:
    """Baixa a media da mensagem agora e guarda em media_url (data-URI).

    Usado tanto pelo webhook (background) quanto on-demand pelo endpoint do
    painel — cobre mensagens antigas que chegaram antes do suporte a media e
    casos em que o download em background falhou. Retorna o data-URI ou ''.
    """
    msg_id = m["id"]
    lock = _get_media_lock(msg_id)
    with lock:
        # Recheck: outro thread pode ter baixado enquanto esperávamos o lock.
        fresh = database.get_message(msg_id)
        if fresh and str(fresh.get("media_url") or "").strip():
            return fresh["media_url"]  # data-URI ou URL do payload
        uazapi_id = str((fresh or {}).get("uazapi_id") or "")
        phone = str((fresh or {}).get("phone") or "")
        if not uazapi_id or not phone:
            return ""
        r = download_media(phone, uazapi_id)
        if not r.get("ok"):
            print(f"[media] falha download msg={msg_id} uazapi_id={uazapi_id}: {r.get('error')}")
            return ""
        d = r.get("data")
        data_uri = ""
        media_url = ""
        if isinstance(d, dict):
            b64 = str(d.get("base64") or d.get("base64Data") or "")
            mime = str(d.get("mimeType") or d.get("mimetype") or "application/octet-stream")
            media_url = str(d.get("url") or d.get("link") or d.get("mediaUrl") or d.get("FileURL") or "")
            if b64:
                data_uri = f"data:{mime};base64,{b64}"
        elif isinstance(d, str):
            if d.startswith("http"):
                media_url = d
            elif len(d) > 512:  # base64 cru sem envelope
                data_uri = f"data:application/octet-stream;base64,{d}"
        result = data_uri or media_url
        if not result:
            print(f"[media] resposta sem base64/url msg={msg_id}: {str(d)[:120]}")
            return ""
        database.update_message_media(msg_id, result)
        print(f"[media] msg={msg_id} guardado len={len(result)}")
        return result

# ── Config ──────────────────────────────────────────────────────────────────
BUFFER_SECONDS = float(os.getenv("BUFFER_SECONDS", "8"))
PORT = int(os.getenv("PORT", "5000"))
SECRET_KEY = os.getenv("SECRET_KEY", "troque_esta_chave_secreta_aleatoria")

_BASE_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "Você é um assistente útil que responde no WhatsApp. "
    "Seja breve, natural, use quebras de parágrafo (\\n\\n) quando fizer sentido dividir ideias.",
)


FULL_SYSTEM_PROMPT = _BASE_PROMPT + (
    "\n\nVocê pode receber e analisar mídias enviadas pelo cliente: "
    "imagens (fotos, prints, documentos escaneados), áudios (mensagens de voz), "
    "vídeos e figurinhas. "
    "Quando receber uma imagem, descreva o que vê e responda de acordo. "
    "Quando receber um áudio, transcreva mentalmente o conteúdo e responda ao que foi dito. "
    "Seja útil e objetivo ao lidar com mídias."
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def split_reply(text: str) -> list[str]:
    """
    Divide a resposta em chunks para envio como mensagens separadas.
    Critério 1: separa por parágrafo duplo (\\n\\n).
    Critério 2: pedaços > 800 chars são quebrados por '. ' (mantendo o ponto).
    Remove strings vazias.
    """
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) <= 800:
            chunks.append(para)
        else:
            sentences = para.split(". ")
            current = ""
            for i, sentence in enumerate(sentences):
                piece = sentence if i == len(sentences) - 1 else sentence + "."
                if current and len(current) + 1 + len(piece) > 800:
                    chunks.append(current.strip())
                    current = piece
                else:
                    current = (current + " " + piece).strip() if current else piece
            if current:
                chunks.append(current.strip())
    return [c for c in chunks if c]


# ── Multimodal parts builder ─────────────────────────────────────────────────

_GEMINI_MIME_MAP = {
    "image/jpeg": "image/jpeg", "image/png": "image/png", "image/webp": "image/webp",
    "image/gif": "image/gif",
    "audio/ogg": "audio/ogg", "audio/mpeg": "audio/mpeg", "audio/mp4": "audio/mp4",
    "audio/wav": "audio/wav",
    "video/mp4": "video/mp4", "video/webm": "video/webm",
    "application/pdf": "application/pdf",
}


def _parse_data_uri(data_uri: str) -> tuple[str, str]:
    """Extrai (mime, base64_data) de um data-URI data:image/jpeg;base64,..."""
    m = __import__("re").match(r"^data:([^;,]+);base64,(.+)$", data_uri, __import__("re").DOTALL)
    if m:
        return m.group(1), m.group(2)
    return "", data_uri


def _build_user_parts(items: list[dict]) -> list[dict]:
    """Monta parts multimodais para o turno do usuário.

    Para cada item, busca a mensagem no banco por msg_id.
    Se tiver media_url (data-URI ou URL), inclui como inline_data.
    Sempre inclui o texto (mesmo que placeholder como "[Imagem]").
    """
    import re as _re
    parts = []
    seen_media = set()

    for item in items:
        text = item.get("text", "")
        msg_id = item.get("msg_id")

        if msg_id:
            msg = database.get_message(msg_id)
            media_url = str((msg or {}).get("media_url") or "").strip()
            msg_type = str((msg or {}).get("msg_type") or "text")

            if media_url and msg_type != "text" and media_url not in seen_media:
                seen_media.add(media_url)
                mime = ""
                b64 = ""

                if media_url.startswith("data:"):
                    mime, b64 = _parse_data_uri(media_url)
                elif media_url.startswith("http"):
                    # URL externa: baixa e converte pra data-URI inline
                    try:
                        import requests as _req
                        resp = _req.get(media_url, timeout=15)
                        if resp.status_code == 200:
                            ct = resp.headers.get("Content-Type", "")
                            mime = ct.split(";")[0].strip() if ct else ""
                            import base64 as _b64
                            b64 = _b64.b64encode(resp.content).decode()
                    except Exception as e:
                        print(f"[flush] WARN download media falhou msg={msg_id}: {e}")

                if mime and b64:
                    # Descrição para o modelo entender o contexto
                    media_desc = {
                        "image": "O cliente enviou uma imagem.",
                        "video": "O cliente enviou um vídeo.",
                        "audio": "O cliente enviou um áudio.",
                        "ptt": "O cliente enviou um áudio (mensagem de voz).",
                        "sticker": "O cliente enviou uma figurinha.",
                        "document": "O cliente enviou um documento.",
                    }.get(msg_type, f"O cliente enviou mídia ({msg_type}).")

                    if text and not text.startswith("["):
                        media_desc += f" Legenda: {text}"

                    parts.append({"text": media_desc})
                    parts.append({
                        "inline_data": {
                            "mime_type": mime,
                            "data": b64,
                        }
                    })
                    continue  # Já adicionou texto + mídia, não precisa adicionar texto de novo

        # Texto puro (sem mídia ou mídia não encontrada)
        if text:
            parts.append({"text": text})

    if not parts:
        parts.append({"text": "[Mensagem sem conteúdo de texto]"})

    return parts


# ── Flush handler ─────────────────────────────────────────────────────────────

def handle_flush(user: str, items: list[dict]) -> None:
    """Executado numa thread do Timer quando o debounce dispara.

    items: [{"text": "...", "msg_id": 123}, ...]
    """
    try:
        contact = database.get_contact(user)
        if contact and not contact["auto_mode"]:
            print(f"[flush] user={user} modo=humano, ignorando agente")
            return
        if not contact:
            print(f"[flush] user={user} sem contato no banco (auto_mode=1 por padrão)")

        send_presence(user, "composing")

        # Monta parts multimodais: texto + mídia inline (imagem, áudio, etc.)
        user_parts = _build_user_parts(items)
        text_only = "\n".join(it["text"] for it in items)
        memory.append_multimodal(user, "user", user_parts)

        # RAG: busca contexto relevante no banco de knowledge
        dynamic_prompt = FULL_SYSTEM_PROMPT
        try:
            context = knowledge_rag.search_context(text_only, top_k=3)
            if context:
                dynamic_prompt += f"\n\nContexto relevante da empresa:\n{context}"
        except Exception as e:
            print(f"[flush] WARN rag search falhou: {e}")

        try:
            reply = llm.generate_reply(
                history=memory.get(user),
                system=dynamic_prompt,
            )
        except Exception as e:
            print(f"[flush] ERRO llm p/ {user}: {e}")
            send_presence(user, "paused")
            return
        memory.append(user, "model", reply)
        print(f"[flush] user={user} reply_len={len(reply)}")

        chunks = split_reply(reply)
        if not chunks:
            send_presence(user, "paused")
            return

        for chunk in chunks:
            send_presence(user, "composing")
            time.sleep(1 + len(chunk) / 200)
            res = send_text(user, chunk)
            if not res.get("ok"):
                print(f"[flush] ERRO ao enviar p/ {user}: {res.get('error')}")
                continue
            send_msg_out_to_panel(user, chunk)

        send_presence(user, "paused")
    except Exception as e:
        # Nunca deixa a thread do Timer morrer em silêncio.
        print(f"[flush] ERRO inesperado p/ {user}: {e}")


def send_msg_out_to_panel(user: str, text: str) -> None:
    """Notifica o painel de uma mensagem enviada pelo agente."""
    conv = database.get_or_create_conversation(user)
    database.save_message(
        conversation_id=conv["id"],
        phone=user,
        direction="out",
        body=text,
        sender="agent",
        msg_type="text",
    )
    database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    events.emit("message_sent", {"conversation_id": conv["id"], "phone": user, "body": text})


# ── App & Buffer ──────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

buffer = MessageBuffer(wait_seconds=BUFFER_SECONDS, on_flush=handle_flush)

database.init_db()

# ── Auto-configurar webhook na UAZAPI ───────────────────────────────────────
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
print(f"[config] UAZAPI: {os.getenv('UAZAPI_BASE_URL', '') or 'NÃO CONFIGURADO'}")
print(f"[config] WEBHOOK: {WEBHOOK_URL or 'NÃO CONFIGURADO'}")

if WEBHOOK_URL:
    from uazapi import configure_webhook
    r = configure_webhook(WEBHOOK_URL)
    if r["ok"]:
        print(f"[webhook] configurado: {WEBHOOK_URL}")
    else:
        print(f"[webhook] ERRO: {r['error']}")


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/login")
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("panel_page"))
    return render_template("login.html")


@app.post("/login")
def login_action():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if do_login(username, password):
        return redirect(url_for("panel_page"))
    return render_template("login.html", error="Credenciais inválidas")


@app.get("/logout")
def logout_action():
    do_logout()
    return redirect(url_for("login_page"))


# ── Webhook ──────────────────────────────────────────────────────────────────
@app.post("/webhook")
def webhook():
    data = request.get_json(silent=True) or {}
    print(f"[webhook] <<< raw_keys={list(data.keys())[:8]} EventType={data.get('EventType')} event={data.get('event')}", flush=True)

    # ── Normalizar os 3 formatos UAZAPI ──
    # Formato v2: {"event": "message", "data": {"from": "...", "body": "..."}}
    # Formato v3-A: {"EventType": "message", "event": {"Chat": "...", "IsFromMe": false}}
    # Formato v3-B: {"EventType": "message", "chat": "...", "message": {...}}
    event_type = data.get("EventType")
    event_payload = data.get("event")

    if event_type:
        data["event"] = event_type
        if isinstance(event_payload, dict):
            data["data"] = event_payload
        elif not data.get("data") and isinstance(data.get("message"), dict):
            data["data"] = data["message"]

    event = data.get("event")
    if not event:
        print(f"[webhook] RAW s/ event: {str(data)[:500]}")
        return jsonify({"ok": True})

    print(f"[webhook] event={event} keys={list(data.keys())[:8]}")

    # Ignora recibos de entrega/leitura (não são mensagens)
    inner = data.get("data") or data.get("message") or data
    if isinstance(inner, dict):
        wa_type = str(inner.get("Type", "")).lower()
        if wa_type in ("delivered", "sent", "read", "failed", "played"):
            print(f"[webhook] IGNORANDO receipt: type={wa_type}", flush=True)
            return jsonify({"ok": True})

    # Evento de conexão
    if event == "connection":
        status_raw = ""
        if isinstance(data.get("data"), dict):
            status_raw = str(data["data"].get("status", "")).replace("connection.", "", 1)
        status = status_raw.lower() if status_raw else "unknown"
        if status in ("closed", "disconnected", "logout"):
            status = "close"
        elif status in ("connected",):
            status = "open"
        if status in ("open", "close", "connecting", "qr", "error"):
            _invalidate_status_cache()
            events.emit("wa_status", {"status": status})
        if status == "open" and WEBHOOK_URL:
            from uazapi import configure_webhook
            threading.Thread(target=configure_webhook, args=(WEBHOOK_URL,), daemon=True).start()
        return jsonify({"ok": True})

    # Ignora eventos que não são de mensagem
    if event not in ("message", "messages", "messages.upsert"):
        print(f"[webhook] IGNORANDO event: {event}", flush=True)
        return jsonify({"ok": True})

    # Extrai o payload da mensagem
    msg = data.get("data") or data.get("message") or data
    if isinstance(msg, list) and len(msg) > 0:
        msg = msg[0]
    if not isinstance(msg, dict):
        msg = {}

    # ── Mapear campos v3 → v2 ──
    if not msg.get("from") and msg.get("Chat"):
        msg["from"] = msg["Chat"]
    if not msg.get("id") and msg.get("MessageIDs"):
        ids = msg["MessageIDs"]
        msg["id"] = ids[0] if isinstance(ids, list) and ids else ""
    if "IsFromMe" in msg and "fromMe" not in msg:
        msg["fromMe"] = msg["IsFromMe"]
    if "IsGroup" in msg and "isGroup" not in msg:
        msg["isGroup"] = msg["IsGroup"]
    if msg.get("SenderName") and not msg.get("pushName"):
        msg["pushName"] = msg["SenderName"]
    if msg.get("MsgType") and not msg.get("type"):
        msg["type"] = msg["MsgType"]
    if not msg.get("from") and msg.get("chat"):
        msg["from"] = msg["chat"]
    if not msg.get("from") and msg.get("chatid"):
        msg["from"] = msg["chatid"]

    # Detecta fromMe em todos os formatos
    def _check_from_me(obj: dict) -> bool:
        if not isinstance(obj, dict):
            return False
        for key in ("fromMe", "IsFromMe"):
            fm = obj.get(key)
            if fm is True or str(fm).lower() == "true":
                return True
        k = obj.get("key")
        if isinstance(k, dict):
            for key in ("fromMe", "IsFromMe"):
                fm2 = k.get(key)
                if fm2 is True or str(fm2).lower() == "true":
                    return True
        return False

    _is_from_me = _check_from_me(msg)
    if not _is_from_me and isinstance(data.get("data"), list):
        for _item in data["data"]:
            if _check_from_me(_item):
                _is_from_me = True
                break
    _is_group = msg.get("isGroup") or msg.get("IsGroup")
    _has_participant = msg.get("participant")

    print(f"[webhook] fromMe={_is_from_me} IsFromMe={msg.get('IsFromMe')} fromMe_field={msg.get('fromMe')} group={_is_group} chat={data.get('chat') if not isinstance(data.get('chat'), dict) else 'DICT'} sender_pn={msg.get('sender_pn')} chatid={msg.get('chatid')}")

    if _is_from_me:
        _chat_raw = data.get("chat")
        _chat_type = type(_chat_raw).__name__
        print(f"[webhook] fromMe DETAIL: to={msg.get('to')} from={msg.get('from')} "
              f"chat_type={_chat_type} chat_keys={list(_chat_raw.keys())[:10] if isinstance(_chat_raw, dict) else 'N/A'} "
              f"chatid={msg.get('chatid')} chatlid={msg.get('chatlid')} Chat={msg.get('Chat')} "
              f"remoteJid={msg.get('remoteJid')} sender_pn={msg.get('sender_pn')} "
              f"msg_keys={list(msg.keys())[:15]}")

    # Grupo e participant: ignora completamente
    if _is_group or _has_participant:
        reason = "group" if _is_group else "participant"
        print(f"[webhook] IGNORANDO: {reason} event={event}")
        return jsonify({"ok": True})

    # Extrai número de telefone
    # fromMe=true → destinatário (wa_chatid dentro do dict chat, ou chatid no msg)
    # fromMe=false → remetente (sender_pn, from, etc.)
    if _is_from_me:
        chat_dict = data.get("chat") if isinstance(data.get("chat"), dict) else None
        raw_from = (
            (chat_dict.get("wa_chatid") if chat_dict else None)
            or (chat_dict.get("id") if chat_dict else None)
            or msg.get("chatid") or msg.get("chatlid")
            or msg.get("Chat") or msg.get("to", "")
            or msg.get("remoteJid") or msg.get("chatId")
            or msg.get("sender_pn") or msg.get("from", "")
        )
    else:
        raw_from = (
            msg.get("sender_pn") or msg.get("sender_lid")
            or msg.get("from", "")
            or msg.get("remoteJid", "")
            or msg.get("chatId", "")
            or msg.get("chatid", "")
            or msg.get("Sender", "")
            or msg.get("chat", "")
        )
    if not raw_from:
        print(f"[webhook] SEM REMETENTE: {str(msg)[:200]}")
        return jsonify({"ok": True})

    phone_clean = str(raw_from).split("@")[0].split(":")[0]
    number = "".join([c for c in phone_clean if c.isdigit()])
    if not number:
        print(f"[webhook] NUM INVALIDO: raw_from={raw_from} phone_clean={phone_clean}")
        return jsonify({"ok": True})

    number = uazapi.resolve_lid_to_pn(number)
    print(f"[webhook] number={number} raw_from={raw_from}")

    if _is_from_me:
        _owner_raw = msg.get("owner", "") or ""
        _owner_digits = "".join([c for c in str(_owner_raw).split("@")[0] if c.isdigit()])
        _sender_raw = msg.get("sender_pn", "") or _owner_raw or ""
        _sender_digits = "".join([c for c in str(_sender_raw).split("@")[0] if c.isdigit()])
        if _sender_digits and number == _sender_digits:
            print(f"[webhook] WARN fromMe: number={number} == sender={_sender_digits} "
                  f"— destinatario pode estar errado! owner={_owner_raw} keys={list(msg.keys())[:15]}")

    # push_name: FromMe → nome do destinatário no dict chat; caso contrário → pushName do remetente
    if _is_from_me:
        _chat_raw = data.get("chat")
        if isinstance(_chat_raw, dict):
            push_name = (
                _chat_raw.get("name") or _chat_raw.get("wa_contactName")
                or _chat_raw.get("wa_name") or ""
            )
        else:
            push_name = msg.get("pushName") or msg.get("notifyName") or msg.get("senderName") or msg.get("name") or msg.get("groupName") or ""
    else:
        push_name = msg.get("pushName") or msg.get("notifyName") or msg.get("senderName") or msg.get("name") or ""
    message_id = msg.get("id", "") or msg.get("messageid", "")
    if not message_id and isinstance(msg.get("key"), dict):
        message_id = msg.get("key", {}).get("id", "")

    # Extrai corpo da mensagem
    msg_type = _resolve_msg_type(msg, msg.get("type") or msg.get("messageType") or msg.get("MsgType") or msg.get("mediaType") or "text")
    body = (
        msg.get("body")
        or msg.get("Body")
        or msg.get("content")
        or msg.get("text")
        or msg.get("caption")
        or (msg.get("message", {}).get("conversation") if isinstance(msg.get("message"), dict) else "")
        or (msg.get("message", {}).get("extendedTextMessage", {}).get("text") if isinstance(msg.get("message"), dict) else "")
        or (msg.get("message", {}).get("buttonsResponseMessage", {}).get("selectedButtonId") if isinstance(msg.get("message"), dict) else "")
        or (msg.get("message", {}).get("listResponseMessage", {}).get("singleSelectReply", {}).get("selectedRowId") if isinstance(msg.get("message"), dict) else "")
    )
    if isinstance(body, dict):
        body = body.get("conversation") or body.get("text") or body.get("message") or ""
    if isinstance(body, str):
        body = body.strip()
    else:
        body = ""

    if not body:
        if msg_type == "image":
            body = "[Imagem]"
        elif msg_type == "video":
            body = "[Video]"
        elif msg_type in ("audio", "ptt"):
            body = "[Audio]"
        elif msg_type == "document":
            doc_name = ""
            if isinstance(msg.get("media"), dict):
                doc_name = msg.get("media", {}).get("fileName", "")
            body = f"[Documento: {doc_name}]" if doc_name else "[Documento]"
        elif msg_type == "sticker":
            body = "[Figurinha]"
        elif msg_type == "location":
            body = "[Localizacao]"
        elif msg_type in ("contact", "vcard"):
            body = "[Contato]"
        elif msg_type in ("button_reply", "list_reply"):
            body = msg.get("buttonText") or msg.get("content") or "[Resposta de Opcao]"
        elif msg_type != "text":
            body = f"[{str(msg_type).capitalize()}]"

    if not body:
        print(f"[webhook] SEM BODY: msg_type={msg_type} from={number}")
        return jsonify({"ok": True})

    print(f"[webhook] from={number} ({push_name}) type={msg_type} len={len(body)} fromMe={_is_from_me}")

    # ── fromMe: salva no painel mas NÃO responde ──
    if _is_from_me:
        try:
            contact = database.upsert_contact(number, name=push_name) if push_name else database.upsert_contact(number)
            conv = database.get_or_create_conversation(number)
            saved = database.link_or_save_outgoing(
                conversation_id=conv["id"],
                phone=number,
                body=body,
                uazapi_id=str(message_id) if message_id else None,
                sender="human",
                msg_type=str(msg_type) if msg_type else "text",
            )
            print(f"[webhook] fromMe msg salva: id={saved['id'] if saved else None}")
            if saved and _is_media_type(str(msg_type)) and message_id:
                _maybe_store_media_hint(saved, msg)
                threading.Thread(target=_download_media_async,
                                 args=(saved["id"], number, str(message_id)), daemon=True).start()
            database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            events.emit("message_sent", {
                "conversation_id": conv["id"],
                "phone": number,
                "body": body,
            })
        except Exception as e:
            print(f"[webhook] ERRO fromMe: {e}")
        return jsonify({"ok": True})

    # ── Mensagem de outro usuário: processa normalmente ──
    mark_read(number, str(message_id))

    contact = database.upsert_contact(number, name=push_name) if push_name else database.upsert_contact(number)
    conv = database.get_or_create_conversation(number)
    saved = database.save_message(
        conversation_id=conv["id"],
        phone=number,
        direction="in",
        body=body,
        sender="user",
        msg_type=str(msg_type) if msg_type else "text",
        uazapi_id=str(message_id) if message_id else None,
    )
    if saved:
        database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        database.increment_unread(conv["id"])
        events.emit("new_message", {
            "conversation_id": conv["id"],
            "contact_name": contact.get("name", ""),
            "phone": number,
            "body": body,
            "message_id": str(message_id),
        })
        print(f"[webhook] MSG SALVA conv={conv['id']} from={number} len={len(body)}")
    else:
        print(f"[webhook] MSG DUPLICADA msg_id={message_id}")

    if saved and _is_media_type(str(msg_type)) and message_id:
        _maybe_store_media_hint(saved, msg)
        threading.Thread(target=_download_media_async,
                         args=(saved["id"], number, str(message_id)), daemon=True).start()

    if not contact.get("avatar_url"):
        avatar = uazapi.get_avatar(number)
        if avatar:
            database.update_contact(contact["id"], avatar_url=avatar)

    buffer.add(number, body, msg_id=saved["id"] if saved else None)

    return jsonify({"ok": True})


@app.get("/")
def index():
    return redirect(url_for("panel_page"))


# ── Panel page ────────────────────────────────────────────────────────────────

@app.get("/panel")
@login_required
def panel_page():
    return render_template("index.html")


# ── SSE ───────────────────────────────────────────────────────────────────────

@app.get("/api/events")
@login_required
def sse_stream():
    import queue as _queue
    def stream():
        q = events.subscribe()
        try:
            while True:
                try:
                    data = q.get(timeout=20)
                    yield f"data: {data}\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            events.unsubscribe(q)
    return Response(stream(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/api/dashboard")
@login_required
def api_dashboard():
    return jsonify(database.get_dashboard_stats())


# ── Cache de status da instância ─────────────────────────────────────────────
# Evita martelar a UAZAPI: múltiplas abas/polls compartilham o mesmo resultado.
_status_lock = threading.Lock()
_status_cache: dict = {"payload": None, "expires": 0.0}
_STATUS_TTL = 15  # segundos


def _get_cached_status() -> dict | None:
    with _status_lock:
        if _status_cache["payload"] is not None and time.time() < _status_cache["expires"]:
            return dict(_status_cache["payload"])
    return None


def _set_cached_status(payload: dict) -> None:
    with _status_lock:
        _status_cache["payload"] = dict(payload)
        _status_cache["expires"] = time.time() + _STATUS_TTL


def _invalidate_status_cache() -> None:
    with _status_lock:
        _status_cache["payload"] = None
        _status_cache["expires"] = 0.0
# ── Instance ──────────────────────────────────────────────────────────────────

@app.get("/api/instance/diagnose")
@login_required
def api_instance_diagnose():
    from uazapi import _request
    results = {}
    tests = [
        ("GET  /instance/status",  "GET",  "/instance/status",  None),
        ("POST /instance/connect", "POST", "/instance/connect", {}),
        ("GET  /webhook",          "GET",  "/webhook",          None),
        ("POST /webhook",          "POST", "/webhook",          {"url": "https://test.com/hook", "events": ["message"], "enabled": True}),
    ]
    for label, method, endpoint, body in tests:
        r = _request(method, endpoint, body)
        results[label] = {"ok": r["ok"], "status": r["status"], "error": r.get("error", "")}
    return jsonify(results)


def _normalize_wa_status(raw: str) -> str:
    """Normaliza o status retornado pela UAZAPI para um valor conhecido pelo painel."""
    s = (raw or "").strip().lower()
    if s in ("connected", "open", "online", "connection",
             "connection.open", "connection.connected"):
        return "open"
    if s in ("disconnected", "close", "closed", "offline", "logout",
             "connection.close", "connection.disconnected", "login"):
        return "close"
    if s in ("connecting", "connection.connecting", "qr", "scanning"):
        return "connecting" if "connecting" in s else "qr"
    return s if s in ("open", "close", "connecting", "qr", "error") else "error"


@app.get("/api/instance/status")
@login_required
def api_instance_status():
    from uazapi import config_problem
    problem = config_problem()
    if problem:
        return jsonify({"status": "error", "error": problem})

    cached = _get_cached_status()
    if cached is not None:
        return jsonify(cached)

    from uazapi import get_instance_status
    r = get_instance_status()
    if not r["ok"]:
        err = r["error"]
        if "401" in err:
            err = "Token invalido. Verifique as configuracoes."
        return jsonify({"status": "error", "error": err})
    data = r["data"] or {}
    inst = data.get("instance", data) if isinstance(data, dict) else {}
    # A UAZAPI v3 também expõe status.connected (booleano) separado do instance.status.
    status_obj = data.get("status", {}) if isinstance(data, dict) else {}
    connected = bool(status_obj.get("connected")) if isinstance(status_obj, dict) else None
    raw_status = str(inst.get("status", "") or "")
    if not raw_status and connected is not None:
        raw_status = "connected" if connected else "disconnected"
    status = _normalize_wa_status(raw_status)
    if status == "connecting":
        status = "qr" if not raw_status or "qr" in raw_status else "connecting"
    payload = {"status": status}
    _set_cached_status(payload)
    return jsonify(payload)


@app.get("/api/instance/qr")
@login_required
def api_instance_qr():
    from uazapi import get_qr_code, get_instance_status

    status_r = get_instance_status()
    st = ""
    if status_r["ok"]:
        data = status_r["data"] or {}
        inst = data.get("instance", data) if isinstance(data, dict) else {}
        # UAZAPI v3 pode retornar status aninhado: {"status": {"connected": true}}
        status_obj = data.get("status", {}) if isinstance(data, dict) else {}
        connected = bool(status_obj.get("connected")) if isinstance(status_obj, dict) else None
        raw_status = str(inst.get("status", "") or "")
        if not raw_status and connected is not None:
            raw_status = "connected" if connected else "disconnected"
        st = _normalize_wa_status(raw_status)
    print(f"[qr] status: {st}")

    if st == "open":
        return jsonify({"info": "WhatsApp ja esta conectado."})

    r = get_qr_code()
    print(f"[qr] GET /instance/connect -> ok={r['ok']} data={str(r.get('data', r.get('error', '')))[:500]}")

    if not r["ok"]:
        err = r["error"]
        if "401" in err:
            err = "Token invalido. Verifique as configuracoes."
        return jsonify({"error": err})

    qr_data = r["data"] or {}
    if isinstance(qr_data, dict):
        inst = qr_data.get("instance", qr_data)
        qr_value = inst.get("qrcode") or qr_data.get("qrcode") or inst.get("paircode") or ""
        if qr_value:
            return jsonify({"qrcode": qr_value, "pairingCode": inst.get("paircode", "")})
        return jsonify({"raw": qr_data, "error": "Resposta sem campo 'qrcode'."})
    return jsonify({"raw": str(qr_data), "error": "Formato de resposta inesperado."})


@app.post("/api/instance/disconnect")
@login_required
def api_instance_disconnect():
    from uazapi import disconnect_instance
    r = disconnect_instance()
    if not r["ok"]:
        err = r["error"]
        if "401" in err:
            err = "Token invalido. Verifique as configuracoes."
        return jsonify({"ok": False, "error": err}), 400
    _invalidate_status_cache()
    events.emit("wa_status", {"status": "close"})
    return jsonify({"ok": True})


@app.post("/api/instance/restart")
@login_required
def api_instance_restart():
    from uazapi import restart_instance
    r = restart_instance()
    if not r["ok"]:
        err = r["error"]
        if "401" in err:
            err = "Token invalido. Verifique as configuracoes."
        return jsonify({"ok": False, "error": err}), 400
    _invalidate_status_cache()
    events.emit("wa_status", {"status": "connecting"})
    return jsonify({"ok": True})


# ── Conversations ─────────────────────────────────────────────────────────────

@app.get("/api/conversations")
@login_required
def api_conversations_list():
    status = request.args.get("status", "")
    limit = int(request.args.get("limit", "100"))
    offset = int(request.args.get("offset", "0"))
    arch = request.args.get("archived", "")
    archived = None if arch == "" else (arch == "1" or arch.lower() == "true")
    if status == "archived":
        status = ""
        archived = True
    convs = database.list_conversations(status=status, archived=archived, limit=limit, offset=offset)
    return jsonify(convs)


@app.get("/api/conversations/<int:conv_id>")
@login_required
def api_conversations_detail(conv_id):
    conv = database.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    contact = database.get_contact_by_id(conv["contact_id"])
    messages = database.get_messages(conv_id, limit=200)
    # No enviamos el base64 de media en el payload (se pide bajo demanda por
    # /api/messages/<id>/media): mantiene ligera la lista y la carga rápida.
    for m in messages:
        mu = m.get("media_url") or ""
        if mu.startswith("data:"):
            m["media_url"] = ""
    tags = database.get_conversation_tags(conv_id)
    return jsonify({"conversation": conv, "contact": contact, "messages": messages, "tags": tags})


@app.put("/api/conversations/<int:conv_id>/status")
@login_required
def api_conversations_update_status(conv_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status", "")
    if status not in ("new", "open", "waiting", "resolved", "closed"):
        return jsonify({"error": "invalid status"}), 400
    database.update_conversation(conv_id, status=status)
    events.emit("conversation_updated", {"conversation_id": conv_id, "status": status})
    return jsonify({"ok": True})


@app.put("/api/conversations/<int:conv_id>/assign")
@login_required
def api_conversations_assign(conv_id):
    data = request.get_json(silent=True) or {}
    assigned_to = data.get("assigned_to", "")
    database.update_conversation(conv_id, assigned_to=assigned_to)
    events.emit("conversation_updated", {"conversation_id": conv_id, "assigned_to": assigned_to})
    return jsonify({"ok": True})


@app.put("/api/conversations/<int:conv_id>/mode")
@login_required
def api_conversations_toggle_mode(conv_id):
    conv = database.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    contact = database.get_contact_by_id(conv["contact_id"])
    if not contact:
        return jsonify({"error": "contact not found"}), 404
    new_mode = 0 if contact["auto_mode"] else 1
    database.update_contact(contact["id"], auto_mode=new_mode)
    events.emit("conversation_updated", {"conversation_id": conv_id, "auto_mode": new_mode})
    return jsonify({"ok": True, "auto_mode": new_mode})


@app.put("/api/conversations/<int:conv_id>/read")
@login_required
def api_conversations_mark_read(conv_id):
    conv = database.get_conversation(conv_id)
    database.reset_unread(conv_id)
    if conv:
        contact = database.get_contact_by_id(conv["contact_id"])
        if contact and contact.get("phone"):
            number = str(contact["phone"]).replace("@s.whatsapp.net", "")
            try:
                mark_read(number)
            except Exception:
                pass
    events.emit("conversation_updated", {"conversation_id": conv_id, "unread_count": 0})
    return jsonify({"ok": True})


@app.put("/api/conversations/<int:conv_id>/unread")
@login_required
def api_conversations_mark_unread(conv_id):
    """Marca como não lida (painel-only; UAZAPI não expone endpoint)."""
    database.mark_unread(conv_id)
    events.emit("conversation_updated", {"conversation_id": conv_id, "unread_count": 1})
    return jsonify({"ok": True})


@app.put("/api/conversations/<int:conv_id>/flags")
@login_required
def api_conversations_update_flags(conv_id):
    """Atualiza flags (archived/muted/pinned/favorite). Sincroniza UAZAPI quando aplicable."""
    data = request.get_json(silent=True) or {}
    fields = {}
    for key in ("archived", "muted", "pinned", "favorite"):
        if key in data:
            fields[key] = 1 if data[key] else 0

    conv = database.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    if fields:
        database.update_conversation(conv_id, **fields)
        events.emit("conversation_updated", {"conversation_id": conv_id, **fields})

    # Sincronización con WhatsApp real (best-effort, solo quando conectado/configurado)
    contact = database.get_contact_by_id(conv["contact_id"]) if conv else None
    phone = (contact or {}).get("phone", "")
    if fields and phone:
        number = str(phone).replace("@s.whatsapp.net", "")
        if "archived" in fields:
            chat_archive(number, bool(fields["archived"]))
        if "pinned" in fields:
            chat_pin(number, bool(fields["pinned"]))
        if "muted" in fields:
            chat_mute(number, bool(fields["muted"]))

    return jsonify({"ok": True, **fields})


@app.post("/api/conversations/<int:conv_id>/clear")
@login_required
def api_conversations_clear(conv_id):
    """Elimina todas as mensagens da conversa (painel-only)."""
    database.clear_conversation(conv_id)
    events.emit("conversation_updated", {"conversation_id": conv_id, "cleared": True})
    return jsonify({"ok": True})


@app.delete("/api/conversations/<int:conv_id>")
@login_required
def api_conversations_delete(conv_id):
    """Apaga a conversa (mensagens + conversa). Sincroniza UAZAPI (DELETE /chat) best-effort."""
    conv = database.get_conversation(conv_id)
    contact = database.get_contact_by_id(conv["contact_id"]) if conv else None
    phone = (contact or {}).get("phone", "")
    database.delete_conversation(conv_id)
    if phone:
        chat_delete(str(phone).replace("@s.whatsapp.net", ""))
    events.emit("conversation_updated", {"conversation_id": conv_id, "deleted": True})
    return jsonify({"ok": True})


@app.post("/api/conversations/<int:conv_id>/tags")
@login_required
def api_conversations_add_tag(conv_id):
    data = request.get_json(silent=True) or {}
    tag_id = data.get("tag_id")
    if not tag_id:
        return jsonify({"error": "tag_id required"}), 400
    database.add_tag_to_conversation(conv_id, tag_id)
    events.emit("tags_updated", {"conversation_id": conv_id, "tag_id": tag_id, "action": "add"})
    return jsonify({"ok": True})


@app.delete("/api/conversations/<int:conv_id>/tags/<int:tag_id>")
@login_required
def api_conversations_remove_tag(conv_id, tag_id):
    import db
    db.execute("DELETE FROM conversation_tags WHERE conversation_id=? AND tag_id=?", (conv_id, tag_id))
    events.emit("tags_updated", {"conversation_id": conv_id, "tag_id": tag_id, "action": "remove"})
    return jsonify({"ok": True})


@app.post("/api/conversations/bulk")
@login_required
def api_conversations_bulk():
    """Executa uma ação em massa sobre múltiplas conversas."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    action = data.get("action", "")
    params = data.get("params") or {}
    if not ids or not isinstance(ids, list):
        return jsonify({"error": "ids required"}), 400

    processed = 0
    errors = []
    for cid in ids:
        try:
            conv = database.get_conversation(cid)
            if not conv:
                errors.append({"id": cid, "error": "not found"})
                continue

            if action in ("archive", "mute", "pin", "favorite"):
                fields = {}
                if action == "archive":
                    fields["archived"] = 1 if params.get("archived", True) else 0
                elif action == "mute":
                    fields["muted"] = 1 if params.get("muted", True) else 0
                elif action == "pin":
                    fields["pinned"] = 1 if params.get("pinned", True) else 0
                elif action == "favorite":
                    fields["favorite"] = 1 if params.get("favorite", True) else 0
                database.update_conversation(cid, **fields)
                events.emit("conversation_updated", {"conversation_id": cid, **fields})
                contact = database.get_contact_by_id(conv["contact_id"]) if conv else None
                phone = (contact or {}).get("phone", "")
                if phone:
                    number = str(phone).replace("@s.whatsapp.net", "")
                    if action == "archive":
                        chat_archive(number, bool(fields["archived"]))
                    elif action == "pin":
                        chat_pin(number, bool(fields["pinned"]))
                    elif action == "mute":
                        chat_mute(number, bool(fields["muted"]))

            elif action == "status":
                status = params.get("status", "")
                if status not in ("new", "open", "waiting", "resolved", "closed"):
                    errors.append({"id": cid, "error": "invalid status"})
                    continue
                database.update_conversation(cid, status=status)
                events.emit("conversation_updated", {"conversation_id": cid, "status": status})

            elif action == "mode":
                contact = database.get_contact_by_id(conv["contact_id"])
                if contact:
                    new_mode = 0 if contact["auto_mode"] else 1
                    database.update_contact(contact["id"], auto_mode=new_mode)
                    events.emit("conversation_updated", {"conversation_id": cid, "auto_mode": new_mode})

            elif action == "read":
                database.reset_unread(cid)
                events.emit("conversation_updated", {"conversation_id": cid, "unread_count": 0})

            elif action == "unread":
                database.mark_unread(cid)
                events.emit("conversation_updated", {"conversation_id": cid, "unread_count": 1})

            elif action == "clear":
                database.clear_conversation(cid)
                events.emit("conversation_updated", {"conversation_id": cid, "cleared": True})

            elif action == "delete":
                contact = database.get_contact_by_id(conv["contact_id"]) if conv else None
                phone = (contact or {}).get("phone", "")
                database.delete_conversation(cid)
                if phone:
                    chat_delete(str(phone).replace("@s.whatsapp.net", ""))
                events.emit("conversation_updated", {"conversation_id": cid, "deleted": True})

            elif action == "add_tag":
                tag_id = params.get("tag_id")
                if tag_id:
                    database.add_tag_to_conversation(cid, tag_id)
                    events.emit("tags_updated", {"conversation_id": cid, "tag_id": tag_id, "action": "add"})

            elif action == "remove_tag":
                tag_id = params.get("tag_id")
                if tag_id:
                    db.execute("DELETE FROM conversation_tags WHERE conversation_id=? AND tag_id=?", (cid, tag_id))
                    events.emit("tags_updated", {"conversation_id": cid, "tag_id": tag_id, "action": "remove"})
            else:
                errors.append({"id": cid, "error": f"unknown action: {action}"})
                continue

            processed += 1
        except Exception as e:
            errors.append({"id": cid, "error": str(e)})

    return jsonify({"ok": True, "processed": processed, "errors": errors})


@app.get("/api/settings/agent-mode")
@login_required
def api_get_agent_mode():
    val = database.get_setting("agent_mode", "1")
    return jsonify({"auto_mode": int(val)})


@app.put("/api/settings/agent-mode")
@login_required
def api_set_agent_mode():
    data = request.get_json(silent=True) or {}
    auto = 1 if data.get("auto_mode", True) else 0
    database.set_setting("agent_mode", str(auto))
    return jsonify({"ok": True, "auto_mode": auto})


# ── Messages ──────────────────────────────────────────────────────────────────

@app.post("/api/messages/send")
@login_required
def api_messages_send():
    data = request.get_json(silent=True) or {}
    phone = data.get("phone", "").strip()
    body = data.get("body", "").strip()
    if not phone or not body:
        return jsonify({"error": "phone and body required"}), 400

    res = send_text(phone, body)
    if not res.get("ok"):
        err = res.get("error") or "Falha ao enviar mensagem pelo WhatsApp."
        print(f"[api] ERRO ao enviar p/ {phone}: {err}")
        return jsonify({"error": err}), (res.get("status") or 500)

    # Se o envio via LID resolveu o número real (PN), usa/grava essa identidade.
    resolved = res.get("resolved") or ""
    contact_phone = resolved if resolved else phone
    contact = database.upsert_contact(contact_phone)
    conv = database.get_or_create_conversation(contact_phone)
    database.save_message(
        conversation_id=conv["id"],
        phone=contact_phone,
        direction="out",
        body=body,
        sender="human",
        msg_type="text",
        uazapi_id=res.get("message_id", ""),
    )
    database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    events.emit("message_sent", {"conversation_id": conv["id"], "phone": contact_phone, "body": body})
    return jsonify({"ok": True, "phone": contact_phone, "resolved": resolved})


@app.get("/api/messages/<int:msg_id>/media")
@login_required
def api_messages_media(msg_id):
    """Devuelve el data-URI base64 de la media de un mensaje (imagen/audio/video/sticker).

    Se la media aún no está guardada (mensajes antiguas o download en background
    fallido), la baixa on-demand desde UAZAPI y la persiste — así la media
    siempre se visualiza en el panel, no solo la etiqueta textual.
    """
    m = database.get_message(msg_id)
    if not m:
        return jsonify({"error": "not found"}), 404
    media_url = str(m.get("media_url", "") or "").strip()
    # Se não há nada guardado (mensagens antigas ou download em background
    # falho), baixa on-demand desde UAZAPI e persiste.
    if not media_url:
        try:
            media_url = _fetch_and_store_media(m)
        except Exception as e:
            print(f"[media] erro on-demand msg={msg_id}: {e}")
            media_url = ""
    return jsonify({
        "msg_id": m["id"],
        "msg_type": m.get("msg_type", "text"),
        "media_url": media_url,
        "body": m.get("body", "") or "",
        "ready": bool(media_url),
    })


# ── Contacts ──────────────────────────────────────────────────────────────────

@app.get("/api/contacts")
@login_required
def api_contacts_list():
    limit = int(request.args.get("limit", "100"))
    offset = int(request.args.get("offset", "0"))
    return jsonify(database.list_contacts(limit=limit, offset=offset))


@app.post("/api/contacts")
@login_required
def api_contacts_create():
    data = request.get_json(silent=True) or {}
    phone = data.get("phone", "").strip()
    if not phone:
        return jsonify({"error": "phone required"}), 400
    contact = database.upsert_contact(
        phone,
        name=data.get("name", ""),
        email=data.get("email", ""),
        company=data.get("company", ""),
        notes=data.get("notes", ""),
    )
    return jsonify(contact)


@app.get("/api/contacts/<int:contact_id>")
@login_required
def api_contacts_detail(contact_id):
    contact = database.get_contact_by_id(contact_id)
    if not contact:
        return jsonify({"error": "not found"}), 404
    tags = database.get_contact_tags(contact_id)
    tasks = database.list_tasks(contact_id)
    return jsonify({"contact": contact, "tags": tags, "tasks": tasks})


@app.put("/api/contacts/<int:contact_id>")
@login_required
def api_contacts_update(contact_id):
    data = request.get_json(silent=True) or {}
    database.update_contact(contact_id, **data)
    return jsonify({"ok": True})


@app.delete("/api/contacts/<int:contact_id>")
@login_required
def api_contacts_delete(contact_id):
    database.delete_contact(contact_id)
    return jsonify({"ok": True})


@app.post("/api/contacts/<int:contact_id>/tags")
@login_required
def api_contacts_add_tag(contact_id):
    data = request.get_json(silent=True) or {}
    tag_id = data.get("tag_id")
    if not tag_id:
        return jsonify({"error": "tag_id required"}), 400
    database.add_tag_to_contact(contact_id, tag_id)
    events.emit("tags_updated", {"contact_id": contact_id, "tag_id": tag_id, "action": "add"})
    return jsonify({"ok": True})


@app.delete("/api/contacts/<int:contact_id>/tags/<int:tag_id>")
@login_required
def api_contacts_remove_tag(contact_id, tag_id):
    database.remove_tag_from_contact(contact_id, tag_id)
    events.emit("tags_updated", {"contact_id": contact_id, "tag_id": tag_id, "action": "remove"})
    return jsonify({"ok": True})


# ── Tags ──────────────────────────────────────────────────────────────────────

@app.get("/api/tags")
@login_required
def api_tags_list():
    return jsonify(database.list_tags())


@app.post("/api/tags")
@login_required
def api_tags_create():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    tag = database.create_tag(name, color=data.get("color", "#6366f1"))
    events.emit("tags_updated", {"tag": tag, "action": "create"})
    return jsonify(tag)


@app.put("/api/tags/<int:tag_id>")
@login_required
def api_tags_update(tag_id):
    data = request.get_json(silent=True) or {}
    database.update_tag(tag_id, name=data.get("name", ""), color=data.get("color", ""))
    events.emit("tags_updated", {"tag_id": tag_id, "action": "update"})
    return jsonify({"ok": True})


@app.delete("/api/tags/<int:tag_id>")
@login_required
def api_tags_delete(tag_id):
    database.delete_tag(tag_id)
    events.emit("tags_updated", {"tag_id": tag_id, "action": "delete"})
    return jsonify({"ok": True})


# ── Templates ─────────────────────────────────────────────────────────────────

@app.get("/api/templates")
@login_required
def api_templates_list():
    return jsonify(database.list_templates())


@app.post("/api/templates")
@login_required
def api_templates_create():
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    body = data.get("body", "").strip()
    if not title or not body:
        return jsonify({"error": "title and body required"}), 400
    tpl = database.create_template(title, body)
    return jsonify(tpl)


@app.put("/api/templates/<int:tpl_id>")
@login_required
def api_templates_update(tpl_id):
    data = request.get_json(silent=True) or {}
    database.update_template(tpl_id, title=data.get("title", ""), body=data.get("body", ""))
    return jsonify({"ok": True})


@app.delete("/api/templates/<int:tpl_id>")
@login_required
def api_templates_delete(tpl_id):
    database.delete_template(tpl_id)
    return jsonify({"ok": True})


# ── Tasks ─────────────────────────────────────────────────────────────────────

@app.get("/api/tasks")
@login_required
def api_tasks_list():
    contact_id = request.args.get("contact_id", type=int)
    return jsonify(database.list_tasks(contact_id=contact_id))


@app.post("/api/tasks")
@login_required
def api_tasks_create():
    data = request.get_json(silent=True) or {}
    contact_id = data.get("contact_id")
    title = data.get("title", "").strip()
    if not contact_id or not title:
        return jsonify({"error": "contact_id and title required"}), 400
    task = database.create_task(contact_id, title, data.get("description", ""), data.get("due_date", ""))
    return jsonify(task)


@app.put("/api/tasks/<int:task_id>")
@login_required
def api_tasks_update(task_id):
    data = request.get_json(silent=True) or {}
    database.update_task(task_id, **data)
    return jsonify({"ok": True})


@app.delete("/api/tasks/<int:task_id>")
@login_required
def api_tasks_delete(task_id):
    database.delete_task(task_id)
    return jsonify({"ok": True})


# ── Search ────────────────────────────────────────────────────────────────────

@app.get("/api/search")
@login_required
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    return jsonify(database.search_messages(q))


# ── Knowledge (RAG via Supabase) ─────────────────────────────────────────────

@app.get("/api/knowledge")
@login_required
def api_knowledge_list():
    return jsonify(knowledge_rag.list_files())


@app.get("/api/knowledge/<filename>")
@login_required
def api_knowledge_get(filename):
    file_data = knowledge_rag.get_file(filename)
    if not file_data:
        return jsonify({"error": "Arquivo nao encontrado"}), 404
    return jsonify(file_data)


@app.post("/api/knowledge")
@login_required
def api_knowledge_create():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "").strip()
    content = data.get("content", "")
    if not filename:
        return jsonify({"error": "filename obrigatorio"}), 400
    if not filename.endswith(('.md', '.txt')):
        return jsonify({"error": "Extensao invalida (.md ou .txt)"}), 400
    existing = knowledge_rag.get_file(filename)
    if existing:
        return jsonify({"error": "Arquivo ja existe"}), 409
    knowledge_rag.save_knowledge(filename, content)
    print(f"[knowledge] criado: {filename}")
    return jsonify({"ok": True, "filename": filename})


@app.put("/api/knowledge/<filename>")
@login_required
def api_knowledge_update(filename):
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    knowledge_rag.save_knowledge(filename, content)
    print(f"[knowledge] salvo: {filename}")
    return jsonify({"ok": True})


@app.delete("/api/knowledge/<filename>")
@login_required
def api_knowledge_delete(filename):
    existing = knowledge_rag.get_file(filename)
    if not existing:
        return jsonify({"error": "Arquivo nao encontrado"}), 404
    knowledge_rag.delete_file(filename)
    print(f"[knowledge] deletado: {filename}")
    return jsonify({"ok": True})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
