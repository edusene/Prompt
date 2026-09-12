"""
app.py — Flask webhook + pipeline de flush do agente WhatsApp + OpenAI.
+ Painel CRM com autenticação, SSE e API REST completa.
Migrado para Meta WhatsApp Cloud API.
"""

import os
import sys
import json
import time
import threading
import hmac
import hashlib

from dotenv import load_dotenv

load_dotenv(override=True)

# Windows: stdout pode estar em cp1252
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
import meta

from meta import send_text, mark_read, send_typing, get_media_url, download_media_bytes
from meta import get_phone_info, list_templates, get_template, delete_template
from meta import get_business_profile, update_business_profile
from buffer import MessageBuffer
from auth import login_required, do_login, do_logout

# ── Config ──────────────────────────────────────────────────────────────────
BUFFER_SECONDS = float(os.getenv("BUFFER_SECONDS", "8"))
PORT = int(os.getenv("PORT", "5000"))
SECRET_KEY = os.getenv("SECRET_KEY", "troque_esta_chave_secreta_aleatoria")
DATAFY_WEBHOOK_SECRET = os.getenv("DATAFY_WEBHOOK_SECRET", "")

_BASE_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "Voce e um assistente util que responde no WhatsApp. "
    "Seja breve, natural, use quebras de paragrafo (\\n\\n) quando fizer sentido dividir ideias.",
)

FULL_SYSTEM_PROMPT = _BASE_PROMPT + (
    "\n\nVoce pode receber e analisar midias enviadas pelo cliente: "
    "imagens (fotos, prints, documentos escaneados), audios (mensagens de voz), "
    "videos e figurinhas. "
    "Quando receber uma imagem, descreva o que ve e responda de acordo. "
    "Quando receber um audio, transcreva mentalmente o conteudo e responda ao que foi dito. "
    "Seja util e objetivo ao lidar com midias."
)

# ── Knowledge Tool (busca seletiva via function calling) ──────────────────────
import knowledge_rag

KNOWLEDGE_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": (
            "Busca informacoes na base de conhecimento da empresa. "
            "Use quando o usuario perguntar sobre produtos, precos, servicos, "
            "horarios, politicas, ou qualquer informacao que esteja na base. "
            "Retorna o conteudo do arquivo solicitado."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Nome do arquivo knowledge a buscar (ex: 01-produtos.md)",
                },
            },
            "required": ["filename"],
        },
    },
}


def _build_knowledge_index() -> str:
    descriptions = knowledge_rag.get_file_descriptions()
    if not descriptions:
        return ""
    lines = []
    for d in descriptions:
        desc = (d.get("description") or "").strip()
        if desc:
            lines.append(f"- {d['filename']}: {desc}")
        else:
            lines.append(f"- {d['filename']}")
    return "\n".join(lines)


def _knowledge_tool_handler(filename: str) -> str:
    content = knowledge_rag.get_file_content(filename)
    if not content:
        return f"Arquivo '{filename}' nao encontrado na base de conhecimento."
    return content


# ── Helpers ──────────────────────────────────────────────────────────────────

def split_reply(text: str) -> list[str]:
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

_OPENAI_SUPPORTED_IMAGES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
}


def _parse_data_uri(data_uri: str) -> tuple[str, str]:
    m = __import__("re").match(r"^data:([^;,]+);base64,(.+)$", data_uri, __import__("re").DOTALL)
    if m:
        return m.group(1), m.group(2)
    return "", data_uri


def _build_user_parts(items: list[dict]) -> list[dict]:
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

                media_desc = {
                    "image": "O cliente enviou uma imagem.",
                    "video": "O cliente enviou um video.",
                    "audio": "O cliente enviou um audio.",
                    "ptt": "O cliente enviou um audio (mensagem de voz).",
                    "sticker": "O cliente enviou uma figurinha.",
                    "document": "O cliente enviou um documento.",
                }.get(msg_type, f"O cliente enviou midia ({msg_type}).")

                if text and not text.startswith("["):
                    media_desc += f" Legenda: {text}"

                parts.append({"text": media_desc})

                if mime and b64 and mime in _OPENAI_SUPPORTED_IMAGES:
                    parts.append({
                        "inline_data": {
                            "mime_type": mime,
                            "data": b64,
                        }
                    })

                continue

        if text:
            parts.append({"text": text})

    if not parts:
        parts.append({"text": "[Mensagem sem conteudo de texto]"})

    return parts


# ── Flush handler ─────────────────────────────────────────────────────────────

def handle_flush(user: str, items: list[dict]) -> None:
    try:
        global_mode = int(database.get_setting("agent_mode", "1"))
        if not global_mode:
            print(f"[flush] user={user} agente GLOBAL desligado, ignorando")
            return

        contact = database.get_contact(user)
        if contact and not contact["auto_mode"]:
            print(f"[flush] user={user} modo=humano, ignorando agente")
            return
        if not contact:
            print(f"[flush] user={user} sem contato no banco (auto_mode=1 por padrao)")

        # Typing indicator (Meta: combinado com mark_read via typing_indicator)
        last_wa_msg_id = items[-1].get("wa_message_id", "") if items else ""
        if last_wa_msg_id:
            send_typing(last_wa_msg_id)

        user_parts = _build_user_parts(items)
        text_only = "\n".join(it["text"] for it in items)
        memory.append_multimodal(user, "user", user_parts)

        dynamic_prompt = FULL_SYSTEM_PROMPT
        knowledge_index = _build_knowledge_index()
        if knowledge_index:
            dynamic_prompt += (
                "\n\nBase de conhecimento disponivel (use a tool search_knowledge "
                "para buscar o conteudo quando o usuario precisar):\n"
                + knowledge_index
            )

        try:
            reply = llm.generate_reply(
                history=memory.get(user),
                system=dynamic_prompt,
                tools=[KNOWLEDGE_TOOL],
                tool_handlers={"search_knowledge": _knowledge_tool_handler},
            )
        except Exception as e:
            print(f"[flush] ERRO llm p/ {user}: {e}")
            return
        memory.append(user, "assistant", reply)
        print(f"[flush] user={user} reply_len={len(reply)}")

        chunks = split_reply(reply)
        if not chunks:
            return

        for chunk in chunks:
            time.sleep(1 + len(chunk) / 200)
            res = send_text(user, chunk)
            if not res.get("ok"):
                print(f"[flush] ERRO ao enviar p/ {user}: {res.get('error')}")
                continue
            send_msg_out_to_panel(user, chunk)

        # Meta: indicador some automaticamente apos 25s ou envio

        try:
            conv = database.get_or_create_conversation(user)
            database.reset_unread(conv["id"])
            events.emit("conversation_updated", {"conversation_id": conv["id"], "unread_count": 0})
            print(f"[flush] user={user} conv={conv['id']} marcada como lida (agente respondeu)")
        except Exception as e:
            print(f"[flush] WARN reset_unread falhou p/ {user}: {e}")
    except Exception as e:
        print(f"[flush] ERRO inesperado p/ {user}: {e}")


def send_msg_out_to_panel(user: str, text: str) -> None:
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


# ── Download de midia async ──────────────────────────────────────────────────

_media_download_locks: dict[int, threading.Lock] = {}
_media_locks_guard = threading.Lock()


def _get_media_lock(msg_id: int) -> threading.Lock:
    with _media_locks_guard:
        lock = _media_download_locks.get(msg_id)
        if not lock:
            lock = threading.Lock()
            _media_download_locks[msg_id] = lock
        return lock


def _download_media_async(message_db_id: int, wa_media_id: str) -> None:
    """Baixa midia da Meta via media_id e armazena como data-URI."""
    try:
        m = database.get_message(message_db_id)
        if not m:
            return
        lock = _get_media_lock(message_db_id)
        with lock:
            fresh = database.get_message(message_db_id)
            if fresh and str(fresh.get("media_url") or "").strip():
                return
            if not wa_media_id:
                return
            info = get_media_url(wa_media_id)
            if not info.get("ok") or not isinstance(info.get("data"), dict):
                print(f"[media] falha get_media_url msg={message_db_id}: {info.get('error')}")
                return
            url = info["data"].get("url", "")
            mime = info["data"].get("mime_type", "application/octet-stream")
            if not url:
                print(f"[media] URL vazia msg={message_db_id}")
                return
            data = download_media_bytes(url)
            if not data:
                print(f"[media] download vazio msg={message_db_id}")
                return
            import base64
            b64 = base64.b64encode(data).decode()
            data_uri = f"data:{mime};base64,{b64}"
            database.update_message_media(message_db_id, data_uri)
            print(f"[media] msg={message_db_id} guardado len={len(data_uri)}")
    except Exception as e:
        print(f"[media] ERRO download msg={message_db_id}: {e}")


def _fetch_and_store_media(m: dict) -> str:
    """Busca media on-demand (para o painel)."""
    msg_id = m["id"]
    lock = _get_media_lock(msg_id)
    with lock:
        fresh = database.get_message(msg_id)
        if fresh and str(fresh.get("media_url") or "").strip():
            return fresh["media_url"]
        wa_media_id = str((fresh or {}).get("wa_message_id") or "")
        if not wa_media_id:
            return ""
        info = get_media_url(wa_media_id)
        if not info.get("ok") or not isinstance(info.get("data"), dict):
            return ""
        url = info["data"].get("url", "")
        mime = info["data"].get("mime_type", "application/octet-stream")
        if not url:
            return ""
        data = download_media_bytes(url)
        if not data:
            return ""
        import base64
        b64 = base64.b64encode(data).decode()
        data_uri = f"data:{mime};base64,{b64}"
        database.update_message_media(msg_id, data_uri)
        return data_uri


# ── Mapeamento de tipos de midia ─────────────────────────────────────────────

_MEDIA_PLACEHOLDERS = {
    "image": lambda m: f"[Imagem]{(' ' + m.get('caption', '')) if m.get('caption') else ''}",
    "video": lambda m: f"[Video]{(' ' + m.get('caption', '')) if m.get('caption') else ''}",
    "audio": lambda m: "[Audio]" if not m.get("audio", {}).get("voice") else "[Audio (mensagem de voz)]",
    "document": lambda m: f"[Documento: {m.get('document', {}).get('filename', '')}]",
    "sticker": lambda m: "[Figurinha]",
    "location": lambda m: "[Localizacao]",
    "contacts": lambda m: "[Contato]",
}


def _extract_body_from_meta_message(msg_data: dict) -> tuple[str, str, str]:
    """Extrai (body, msg_type, wa_media_id) de uma mensagem Meta.
    Retorna (body, msg_type, wa_media_id).
    """
    msg_type = msg_data.get("type", "text")
    wa_media_id = ""

    if msg_type == "text":
        return msg_data.get("text", {}).get("body", ""), "text", ""

    if msg_type in _MEDIA_PLACEHOLDERS:
        media_obj = msg_data.get(msg_type, {})
        wa_media_id = media_obj.get("id", "")
        body = _MEDIA_PLACEHOLDERS[msg_type](msg_data)
        return body, msg_type, wa_media_id

    if msg_type == "interactive":
        interactive = msg_data.get("interactive", {})
        itype = interactive.get("type", "")
        if itype == "button_reply":
            br = interactive.get("button_reply", {})
            return br.get("title", br.get("id", "")), "button_reply", ""
        if itype == "list_reply":
            lr = interactive.get("list_reply", {})
            return lr.get("title", lr.get("id", "")), "list_reply", ""
        return f"[Interativo: {itype}]", itype, ""

    return f"[{msg_type}]", msg_type, ""


# ── App & Buffer ──────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

buffer = MessageBuffer(wait_seconds=BUFFER_SECONDS, on_flush=handle_flush)

database.init_db()

print(f"[config] Datafy API: PHONE_ID={os.getenv('DATAFY_PHONE_NUMBER_ID', '') or 'N/A'}")
print(f"[config] Datafy API: WABA_ID={os.getenv('DATAFY_WABA_ID', '') or 'N/A'}")

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
    return render_template("login.html", error="Credenciais invalidas")


@app.get("/logout")
def logout_action():
    do_logout()
    return redirect(url_for("login_page"))


# ── Webhook ──────────────────────────────────────────────────────────────────

@app.get("/webhook")
def webhook_verify():
    """Para a Datafy API essa rota não é chamada da mesma maneira que a Meta chama com GET."""
    return "Datafy API endpoint", 200


@app.post("/webhook")
def webhook():
    # Validação Datafy
    signature = request.headers.get("x-datafy-signature-256", "")
    timestamp = request.headers.get("x-datafy-timestamp", "")
    raw_body = request.get_data(as_text=True)

    if DATAFY_WEBHOOK_SECRET and signature and timestamp:
        expected = "sha256=" + hmac.new(
            DATAFY_WEBHOOK_SECRET.encode("utf-8"),
            f"{timestamp}.{raw_body}".encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("utf-8")):
            print("[webhook] Assinatura invalida (Datafy)")
            return "Forbidden", 401

        try:
            req_ts = int(timestamp)
            if abs(time.time() - req_ts) > 300:
                print("[webhook] Timestamp fora do limite de 5 minutos (Datafy)")
                return "Forbidden", 401
        except ValueError:
            print("[webhook] Timestamp invalido (Datafy)")
            return "Forbidden", 401

    data = request.get_json(silent=True) or {}

    # Validacao basica Meta / Datafy
    if data.get("object") != "whatsapp_business_account":
        print(f"[webhook] object invalido: {data.get('object')}")
        return jsonify({"ok": True})

    # Itera entry -> changes -> value
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if change.get("field") != "messages":
                continue

            # Status updates (entrega/leitura) -> ignorar
            if "statuses" in value:
                for status in value["statuses"]:
                    print(f"[webhook] status: {status.get('status')} id={status.get('id', '')[:30]}")
                continue

            # Mensagens recebidas
            if "messages" not in value:
                continue

            contacts = value.get("contacts", [])
            messages = value["messages"]

            if not messages:
                continue

            msg_data = messages[0]
            number = msg_data.get("from", "")
            message_id = msg_data.get("id", "")
            timestamp = msg_data.get("timestamp", "")

            if not number:
                print("[webhook] SEM REMETENTE")
                continue

            # push_name e avatar do contato
            push_name = ""
            profile_picture_url = ""
            if contacts:
                profile = contacts[0].get("profile", {})
                push_name = profile.get("name", "")
                profile_picture_url = profile.get("profile_picture_url", "")

            # Extrai body e tipo
            body, msg_type, wa_media_id = _extract_body_from_meta_message(msg_data)

            if not body:
                print(f"[webhook] SEM BODY: msg_type={msg_type} from={number}")
                continue

            print(f"[webhook] from={number} ({push_name}) type={msg_type} len={len(body)} msg_id={message_id[:30]}")

            # Marca como lido
            mark_read(str(message_id))

            # Salva contato e conversa
            contact_kwargs = {}
            if push_name:
                contact_kwargs["name"] = push_name
            if profile_picture_url:
                contact_kwargs["avatar_url"] = profile_picture_url
            contact = database.upsert_contact(number, **contact_kwargs)
            conv = database.get_or_create_conversation(number)
            saved = database.save_message(
                conversation_id=conv["id"],
                phone=number,
                direction="in",
                body=body,
                sender="user",
                msg_type=str(msg_type) if msg_type else "text",
                wa_message_id=str(message_id) if message_id else None,
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

                # Download de midia em background
                if wa_media_id and msg_type != "text":
                    threading.Thread(
                        target=_download_media_async,
                        args=(saved["id"], wa_media_id),
                        daemon=True,
                    ).start()
            else:
                print(f"[webhook] MSG DUPLICADA msg_id={message_id}")

            buffer.add(number, body,
                       msg_id=saved["id"] if saved else None,
                       wa_message_id=str(message_id) if message_id else "",
                       wa_media_id=wa_media_id)

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
            number = str(contact["phone"])
            # Busca a ultima mensagem para mark_read na Meta
            msgs = database.get_messages(conv_id, limit=1)
            if msgs:
                wa_mid = msgs[-1].get("wa_message_id", "")
                if wa_mid:
                    try:
                        mark_read(wa_mid)
                    except Exception:
                        pass
    events.emit("conversation_updated", {"conversation_id": conv_id, "unread_count": 0})
    return jsonify({"ok": True})


@app.put("/api/conversations/<int:conv_id>/unread")
@login_required
def api_conversations_mark_unread(conv_id):
    database.mark_unread(conv_id)
    events.emit("conversation_updated", {"conversation_id": conv_id, "unread_count": 1})
    return jsonify({"ok": True})


@app.put("/api/conversations/<int:conv_id>/flags")
@login_required
def api_conversations_update_flags(conv_id):
    """Atualiza flags (archived/muted/pinned/favorite). DB only — Meta nao suporta."""
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

    return jsonify({"ok": True, **fields})


@app.post("/api/conversations/<int:conv_id>/clear")
@login_required
def api_conversations_clear(conv_id):
    database.clear_conversation(conv_id)
    events.emit("conversation_updated", {"conversation_id": conv_id, "cleared": True})
    return jsonify({"ok": True})


@app.delete("/api/conversations/<int:conv_id>")
@login_required
def api_conversations_delete(conv_id):
    database.delete_conversation(conv_id)
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
                database.delete_conversation(cid)
                events.emit("conversation_updated", {"conversation_id": cid, "deleted": True})

            elif action == "add_tag":
                tag_id = params.get("tag_id")
                if tag_id:
                    database.add_tag_to_conversation(cid, tag_id)
                    events.emit("tags_updated", {"conversation_id": cid, "tag_id": tag_id, "action": "add"})

            elif action == "remove_tag":
                tag_id = params.get("tag_id")
                if tag_id:
                    import db
                    db.execute("DELETE FROM conversation_tags WHERE conversation_id=? AND tag_id=?", (cid, tag_id))
                    events.emit("tags_updated", {"conversation_id": cid, "tag_id": tag_id, "action": "remove"})
            else:
                errors.append({"id": cid, "error": f"unknown action: {action}"})
                continue

            processed += 1
        except Exception as e:
            errors.append({"id": cid, "error": str(e)})

    return jsonify({"ok": True, "processed": processed, "errors": errors})


# ── Settings ──────────────────────────────────────────────────────────────────

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
        err = res.get("error") or "Falha ao enviar mensagem."
        print(f"[api] ERRO ao enviar p/ {phone}: {err}")
        return jsonify({"error": err}), (res.get("status") or 500)

    # Extrai wa_id da resposta Meta
    data_res = res.get("data") or {}
    contacts = data_res.get("contacts", [])
    wa_id = contacts[0].get("wa_id", phone) if contacts else phone
    messages_res = data_res.get("messages", [])
    meta_msg_id = messages_res[0].get("id", "") if messages_res else ""

    contact = database.upsert_contact(wa_id)
    conv = database.get_or_create_conversation(wa_id)
    database.save_message(
        conversation_id=conv["id"],
        phone=wa_id,
        direction="out",
        body=body,
        sender="human",
        msg_type="text",
        wa_message_id=meta_msg_id,
    )
    database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    events.emit("message_sent", {"conversation_id": conv["id"], "phone": wa_id, "body": body})
    return jsonify({"ok": True, "phone": wa_id, "message_id": meta_msg_id})


@app.get("/api/messages/<int:msg_id>/media")
@login_required
def api_messages_media(msg_id):
    m = database.get_message(msg_id)
    if not m:
        return jsonify({"error": "not found"}), 404
    media_url = str(m.get("media_url", "") or "").strip()
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


@app.get("/api/knowledge/descriptions")
@login_required
def api_knowledge_descriptions():
    return jsonify(knowledge_rag.get_file_descriptions())


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
    description = data.get("description", "").strip()
    if not filename:
        return jsonify({"error": "filename obrigatorio"}), 400
    if not filename.endswith(('.md', '.txt')):
        return jsonify({"error": "Extensao invalida (.md ou .txt)"}), 400
    existing = knowledge_rag.get_file(filename)
    if existing:
        return jsonify({"error": "Arquivo ja existe"}), 409
    knowledge_rag.save_knowledge(filename, content, description=description)
    print(f"[knowledge] criado: {filename}")
    return jsonify({"ok": True, "filename": filename})


@app.put("/api/knowledge/<filename>")
@login_required
def api_knowledge_update(filename):
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    description = data.get("description", "").strip()
    knowledge_rag.save_knowledge(filename, content, description=description)
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


# ── WhatsApp Cloud API (Meta) ────────────────────────────────────────────────

@app.get("/api/whatsapp/status")
@login_required
def api_whatsapp_status():
    """Status do numero: quality rating, display name, etc."""
    problem = meta.config_problem()
    if problem:
        return jsonify({"error": problem}), 400
    r = get_phone_info()
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Erro ao consultar status")}), 500
    return jsonify(r.get("data", {}))


@app.get("/api/whatsapp/templates")
@login_required
def api_whatsapp_templates():
    """Lista templates aprovados na WABA."""
    problem = meta.config_problem()
    if problem:
        return jsonify({"error": problem}), 400
    r = list_templates()
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Erro ao listar templates")}), 500
    return jsonify(r.get("data", {}))


@app.get("/api/whatsapp/templates/<template_id>")
@login_required
def api_whatsapp_template_detail(template_id):
    """Detalhe de um template."""
    problem = meta.config_problem()
    if problem:
        return jsonify({"error": problem}), 400
    r = get_template(template_id)
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Template nao encontrado")}), 404
    return jsonify(r.get("data", {}))


@app.delete("/api/whatsapp/templates/<template_name>")
@login_required
def api_whatsapp_template_delete(template_name):
    """Deleta um template da WABA."""
    problem = meta.config_problem()
    if problem:
        return jsonify({"error": problem}), 400
    r = delete_template(template_name)
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Erro ao deletar template")}), 500
    return jsonify({"ok": True})


@app.get("/api/whatsapp/profile")
@login_required
def api_whatsapp_profile():
    """Business profile do numero."""
    problem = meta.config_problem()
    if problem:
        return jsonify({"error": problem}), 400
    r = get_business_profile()
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Erro ao consultar profile")}), 500
    return jsonify(r.get("data", {}))


@app.post("/api/whatsapp/profile")
@login_required
def api_whatsapp_profile_update():
    """Atualiza business profile."""
    problem = meta.config_problem()
    if problem:
        return jsonify({"error": problem}), 400
    data = request.get_json(silent=True) or {}
    allowed = ("about", "address", "description", "email", "websites", "vertical")
    kwargs = {k: data[k] for k in allowed if k in data}
    if not kwargs:
        return jsonify({"error": "nenhum campo para atualizar"}), 400
    r = update_business_profile(**kwargs)
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Erro ao atualizar profile")}), 500
    return jsonify({"ok": True})


@app.post("/api/whatsapp/send-template")
@login_required
def api_whatsapp_send_template():
    """Envia uma template message para um numero."""
    data = request.get_json(silent=True) or {}
    phone = data.get("phone", "").strip()
    template_name = data.get("template_name", "").strip()
    language = data.get("language", "pt_BR")
    body_params = data.get("body_params")
    header_params = data.get("header_params")
    if not phone or not template_name:
        return jsonify({"error": "phone and template_name required"}), 400
    from meta import send_template
    r = send_template(phone, template_name, language, body_params, header_params)
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Erro ao enviar template")}), (r.get("status") or 500)
    return jsonify({"ok": True, "data": r.get("data", {})})


@app.post("/api/whatsapp/send-interactive")
@login_required
def api_whatsapp_send_interactive():
    """Envia mensagem interativa (botoes ou lista)."""
    data = request.get_json(silent=True) or {}
    phone = data.get("phone", "").strip()
    msg_type = data.get("type", "button")
    if not phone:
        return jsonify({"error": "phone required"}), 400

    if msg_type == "list":
        from meta import send_interactive_list
        r = send_interactive_list(
            phone,
            header_text=data.get("header", ""),
            body_text=data.get("body", ""),
            button_text=data.get("button_text", "Opcoes"),
            sections=data.get("sections", []),
        )
    elif msg_type == "button":
        from meta import send_interactive_buttons
        r = send_interactive_buttons(
            phone,
            body_text=data.get("body", ""),
            buttons=data.get("buttons", []),
            header_text=data.get("header", ""),
            footer_text=data.get("footer", ""),
        )
    else:
        return jsonify({"error": f"tipo interativo invalido: {msg_type}"}), 400

    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Erro ao enviar interativo")}), (r.get("status") or 500)
    return jsonify({"ok": True, "data": r.get("data", {})})


@app.post("/api/whatsapp/upload-media")
@login_required
def api_whatsapp_upload_media():
    """Upload de midia para o servidor Meta (retorna media_id)."""
    problem = meta.config_problem()
    if problem:
        return jsonify({"error": problem}), 400
    if "file" not in request.files:
        return jsonify({"error": "file required"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "filename required"}), 400
    import mimetypes
    mime = mimetypes.guess_type(f.filename)[0] or "application/octet-stream"
    file_bytes = f.read()
    from meta import upload_media_bytes
    r = upload_media_bytes(file_bytes, mime, f.filename)
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Erro ao upload")}), (r.get("status") or 500)
    data = r.get("data", {})
    return jsonify({"ok": True, "media_id": data.get("id", ""), "data": data})


# ── WhatsApp — Conexao e Configuracao (Datafy) ──────────────────────────────

_ENV_KEYS = [
    ("DATAFY_TOKEN", "Token Datafy", True),
    ("DATAFY_PHONE_NUMBER_ID", "Phone Number ID", False),
    ("DATAFY_WABA_ID", "WABA ID", False),
    ("DATAFY_WEBHOOK_SECRET", "Webhook Secret", True),
    ("WEBHOOK_URL", "URL Webhook", False),
    ("OPENAI_API_KEY", "OpenAI API Key", True),
    ("OPENAI_MODEL", "Modelo OpenAI", False),
    ("BUFFER_SECONDS", "Buffer (segundos)", False),
    ("MAX_HISTORY", "Max Historico", False),
    ("SYSTEM_PROMPT", "System Prompt", False),
]


def _mask_value(val: str, is_secret: bool) -> str:
    if not is_secret or not val or len(val) <= 8:
        return val
    return "*" * (len(val) - 4) + val[-4:]


def _read_env_file() -> dict[str, str]:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    result = {}
    if not os.path.isfile(env_path):
        return result
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result


def _write_env_file(updates: dict[str, str]) -> None:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    lines = []
    existing_keys = set()
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    existing_keys.add(k)
                    if k in updates:
                        lines.append(f"{k}={updates[k]}\n")
                    else:
                        lines.append(line)
                else:
                    lines.append(line)
    for k, v in updates.items():
        if k not in existing_keys:
            lines.append(f"{k}={v}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


@app.get("/api/whatsapp/connection")
@login_required
def api_whatsapp_connection():
    """Testa conexao com a Datafy API e retorna dados da instância."""
    r_me = meta.get_me()
    if not r_me.get("ok"):
        return jsonify({"ok": False, "error": r_me.get("error", "Erro ao conectar com Datafy")})

    r_phone = meta.get_phone_info()
    phone_data = r_phone.get("data", {}) if r_phone.get("ok") else {}

    me_data = r_me.get("data", {})
    return jsonify({
        "ok": True,
        "phone_number_id": me_data.get("phone_number_id", _PHONE_ID),
        "waba_id": me_data.get("waba_id", _WABA_ID),
        "display_name": phone_data.get("verified_name", ""),
        "display_phone": phone_data.get("display_phone_number", ""),
        "quality_rating": phone_data.get("quality_rating", ""),
        "name_status": phone_data.get("name_status", ""),
        "config_problem": meta.config_problem(),
    })


@app.get("/api/whatsapp/config")
@login_required
def api_whatsapp_config_get():
    """Retorna valores atuais do .env (secrets mascarados)."""
    env = _read_env_file()
    result = {}
    for key, label, is_secret in _ENV_KEYS:
        raw = env.get(key, os.getenv(key, ""))
        result[key] = {"label": label, "value": _mask_value(raw, is_secret), "is_secret": is_secret}
    return jsonify(result)


@app.post("/api/whatsapp/config")
@login_required
def api_whatsapp_config_post():
    """Salva novos valores no .env e recarrega configuracao."""
    data = request.get_json(silent=True) or {}
    updates = {}
    for key, _, _ in _ENV_KEYS:
        if key in data:
            val = str(data[key]).strip()
            if val.startswith("*" * 4):
                continue
            updates[key] = val
    if not updates:
        return jsonify({"ok": False, "error": "Nenhum campo para atualizar"}), 400
    try:
        _write_env_file(updates)
        os.environ.update(updates)
        meta.reload_config()
        print(f"[config] .env atualizado: {list(updates.keys())}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
