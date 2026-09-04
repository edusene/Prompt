"""
app.py — Flask webhook + pipeline de flush do agente WhatsApp + Gemini.
+ Painel CRM com autenticação, SSE e API REST completa.
"""

import os
import sys
import json
import time
import threading
import subprocess

from dotenv import load_dotenv

# override=True: o .env do projeto vence placeholders/eventuais variáveis globais
# (ex.: UAZAPI_BASE_URL=https://seudominio.uazapi.com definida no Windows).
load_dotenv(override=True)

# Windows: o stdout pode estar em cp1252/charmap; emoji do WhatsApp nos logs
# (prints de payload) lançaria UnicodeEncodeError e quebraria o pipeline.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from flask import Flask, request, jsonify, Response, redirect, url_for, render_template, session, send_from_directory

import memory
import llm
import database
import events
import uazapi
from uazapi import send_text, send_presence, mark_read, chat_archive, chat_pin, chat_mute, chat_delete
from buffer import MessageBuffer
from auth import login_required, do_login, do_logout

# ── Config ──────────────────────────────────────────────────────────────────
BUFFER_SECONDS = float(os.getenv("BUFFER_SECONDS", "8"))
PORT = int(os.getenv("PORT", "5000"))
SECRET_KEY = os.getenv("SECRET_KEY", "troque_esta_chave_secreta_aleatoria")
SYSTEM_PROMPT_DIR = os.getenv("SYSTEM_PROMPT_DIR", "knowledge")

_BASE_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "Você é um assistente útil que responde no WhatsApp. "
    "Seja breve, natural, use quebras de parágrafo (\\n\\n) quando fizer sentido dividir ideias.",
)


def _load_knowledge() -> str:
    """Lê todos os .md/.txt da pasta knowledge e retorna concatenado."""
    import glob as _glob
    path = os.path.join(os.path.dirname(__file__), SYSTEM_PROMPT_DIR)
    if not os.path.isdir(path):
        return ""
    files = sorted(_glob.glob(os.path.join(path, "*.md")) + _glob.glob(os.path.join(path, "*.txt")))
    parts = []
    for f in files:
        if os.path.basename(f) == "README.md":
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                content = fh.read().strip()
                if content:
                    parts.append(content)
        except Exception as e:
            print(f"[knowledge] erro lendo {f}: {e}")
    return "\n\n".join(parts)


_KNOWLEDGE = _load_knowledge()
FULL_SYSTEM_PROMPT = f"{_BASE_PROMPT}\n\n---\n\n{_KNOWLEDGE}" if _KNOWLEDGE else _BASE_PROMPT

if _KNOWLEDGE:
    print(f"[knowledge] {len(_KNOWLEDGE)} chars carregados de {SYSTEM_PROMPT_DIR}/")


def _knowledge_dir() -> str:
    return os.path.join(os.path.dirname(__file__), SYSTEM_PROMPT_DIR)


def _list_knowledge_files() -> list[dict]:
    path = _knowledge_dir()
    if not os.path.isdir(path):
        return []
    files = sorted([f for f in os.listdir(path)
                    if f.endswith(('.md', '.txt')) and f != 'README.md'])
    result = []
    for f in files:
        fp = os.path.join(path, f)
        result.append({"name": f, "size": os.path.getsize(fp)})
    return result


def _knowledge_path(filename: str) -> str:
    safe = os.path.basename(filename)
    return os.path.join(_knowledge_dir(), safe)


def _knowledge_git_status() -> dict:
    try:
        r = subprocess.run(
            ['git', 'status', '--porcelain', 'knowledge/'],
            capture_output=True, text=True, timeout=10
        )
        changed = [l for l in r.stdout.strip().split('\n') if l.strip()]
        return {"dirty": len(changed) > 0, "count": len(changed)}
    except Exception:
        return {"dirty": False, "count": 0}


def reload_knowledge():
    global _KNOWLEDGE, FULL_SYSTEM_PROMPT
    _KNOWLEDGE = _load_knowledge()
    FULL_SYSTEM_PROMPT = f"{_BASE_PROMPT}\n\n---\n\n{_KNOWLEDGE}" if _KNOWLEDGE else _BASE_PROMPT
    print(f"[knowledge] recarregado: {len(_KNOWLEDGE)} chars")

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


# ── Flush handler ─────────────────────────────────────────────────────────────

def handle_flush(user: str, texts: list[str]) -> None:
    """Executado numa thread do Timer quando o debounce dispara."""
    try:
        contact = database.get_contact(user)
        if contact and not contact["auto_mode"]:
            print(f"[flush] user={user} modo=humano, ignorando agente")
            return
        if not contact:
            # Contato novo criado pelo webhook tem auto_mode=1 por padrão.
            print(f"[flush] user={user} sem contato no banco (auto_mode=1 por padrão)")

        send_presence(user, "composing")

        user_turn = "\n".join(texts)
        memory.append(user, "user", user_turn)

        try:
            reply = llm.generate_reply(memory.get(user), FULL_SYSTEM_PROMPT)
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
    event = data.get("event")

    # Evento de conexão (connection.open / connection.close / ...) → atualiza
    # o status no painel em tempo real e invalida o cache de status.
    if event == "connection":
        status_raw = str(data.get("data", {}).get("status", "")).replace("connection.", "", 1)
        status = status_raw.lower() if status_raw else "unknown"
        if status in ("closed", "disconnected", "logout"):
            status = "close"
        elif status in ("connected",):
            status = "open"
        if status in ("open", "close", "connecting", "qr", "error"):
            _invalidate_status_cache()
            events.emit("wa_status", {"status": status})
        return jsonify({"ok": True})

    # Ignora eventos que não são de mensagem
    if event and event not in ("message", "messages", "messages.upsert"):
        return jsonify({"ok": True})

    # Extrai o payload da mensagem de forma resiliente
    msg = data.get("data") or data.get("message") or data.get("payload") or data
    if isinstance(msg, list) and len(msg) > 0:
        msg = msg[0]
    if not isinstance(msg, dict):
        msg = {}

    # Ignora mensagens enviadas pelo próprio número ou mensagens de grupo
    if msg.get("fromMe") or msg.get("isGroup") or msg.get("participant"):
        return jsonify({"ok": True})

    # Extrai e limpa o número de telefone do remetente.
    # A UAZAPI v3 fornece sender_pn (número real) e sender_lid (LID) quando disponíveis.
    # Preferimos o PN (número real) para exibição e envio; LID fica como fallback.
    raw_from = (
        msg.get("sender_pn") or msg.get("sender_lid")
        or msg.get("from", "")
        or msg.get("remoteJid", "")
        or msg.get("chatId", "")
        or msg.get("chatid", "")
        or msg.get("sender", "")
    )
    if not raw_from:
        return jsonify({"ok": True})

    phone_clean = str(raw_from).split("@")[0].split(":")[0]
    number = "".join([c for c in phone_clean if c.isdigit()])
    if not number:
        return jsonify({"ok": True})

    # UAZAPI v3: o remetente pode chegar como LID (ex.: 223656663867638) em vez do
    # PN real. Resolve para o PN canônico ANTES de gravar/bufferizar, para que
    # contato, conversa, painel e envio usem sempre a mesma identidade.
    number = uazapi.resolve_lid_to_pn(number)

    push_name = msg.get("pushName") or msg.get("notifyName") or msg.get("senderName") or msg.get("name") or ""
    message_id = msg.get("id", "") or msg.get("messageid", "")
    if not message_id and isinstance(msg.get("key"), dict):
        message_id = msg.get("key", {}).get("id", "")

    # Determina o texto / corpo da mensagem (atendendo a mídias, botões e variados tipos)
    msg_type = msg.get("type") or msg.get("messageType", "text")
    body = (
        msg.get("body")
        or msg.get("text")
        or msg.get("caption")
        or (msg.get("message", {}).get("conversation") if isinstance(msg.get("message"), dict) else "")
        or (msg.get("message", {}).get("extendedTextMessage", {}).get("text") if isinstance(msg.get("message"), dict) else "")
        or (msg.get("message", {}).get("buttonsResponseMessage", {}).get("selectedButtonId") if isinstance(msg.get("message"), dict) else "")
        or (msg.get("message", {}).get("listResponseMessage", {}).get("singleSelectReply", {}).get("selectedRowId") if isinstance(msg.get("message"), dict) else "")
    )
    if isinstance(body, str):
        body = body.strip()
    else:
        body = ""

    if not body:
        if msg_type == "image":
            body = "📷 [Imagem]"
        elif msg_type == "video":
            body = "🎥 [Vídeo]"
        elif msg_type in ("audio", "ptt"):
            body = "🎵 [Áudio]"
        elif msg_type == "document":
            doc_name = ""
            if isinstance(msg.get("media"), dict):
                doc_name = msg.get("media", {}).get("fileName", "")
            body = f"📄 [Documento: {doc_name}]" if doc_name else "📄 [Documento]"
        elif msg_type == "sticker":
            body = "📱 [Figurinha]"
        elif msg_type == "location":
            body = "📍 [Localização]"
        elif msg_type in ("contact", "vcard"):
            body = "👤 [Contato]"
        elif msg_type in ("button_reply", "list_reply"):
            body = msg.get("buttonText") or "🔘 [Resposta de Opção]"
        elif msg_type != "text":
            body = f"[{str(msg_type).capitalize()}]"

    if not body:
        return jsonify({"ok": True})

    print(f"[webhook] from={number} ({push_name}) type={msg_type} len={len(body)}")

    mark_read(number, str(message_id))

    # Persistir no banco e notificar o painel
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

    # Avatar do contato (best-effort, não bloqueia o fluxo): a URL do WhatsApp
    # expira, então atualiza quando o contato ainda não tem foto.
    if not contact.get("avatar_url"):
        avatar = uazapi.get_avatar(number)
        if avatar:
            database.update_contact(contact["id"], avatar_url=avatar)

    buffer.add(number, body)

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
    def stream():
        q = events.subscribe()
        try:
            while True:
                data = q.get(timeout=30)
                yield f"data: {data}\n\n"
        except Exception:
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
        st = inst.get("status", "")
    print(f"[qr] status: {st}")

    if st == "open":
        return jsonify({"info": "WhatsApp ja esta conectado."})

    r = get_qr_code()
    print(f"[qr] POST /instance/connect -> ok={r['ok']} data={str(r.get('data', r.get('error', '')))[:500]}")

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
    _invalidate_status_cache()
    events.emit("wa_status", {"status": "close"})
    r = disconnect_instance()
    if not r["ok"]:
        err = r["error"]
        if "401" in err:
            err = "Token invalido. Verifique as configuracoes."
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})


@app.post("/api/instance/restart")
@login_required
def api_instance_restart():
    from uazapi import restart_instance
    _invalidate_status_cache()
    events.emit("wa_status", {"status": "connecting"})
    r = restart_instance()
    if not r["ok"]:
        err = r["error"]
        if "401" in err:
            err = "Token invalido. Verifique as configuracoes."
        return jsonify({"ok": False, "error": err}), 400
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
    with database._lock:
        conn = database._get_conn()
        conn.execute("DELETE FROM conversation_tags WHERE conversation_id=? AND tag_id=?", (conv_id, tag_id))
        conn.commit()
        conn.close()
    events.emit("tags_updated", {"conversation_id": conv_id, "tag_id": tag_id, "action": "remove"})
    return jsonify({"ok": True})


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
    )
    database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    events.emit("message_sent", {"conversation_id": conv["id"], "phone": contact_phone, "body": body})
    return jsonify({"ok": True, "phone": contact_phone, "resolved": resolved})


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


# ── Knowledge ────────────────────────────────────────────────────────────────

@app.get("/api/knowledge")
@login_required
def api_knowledge_list():
    return jsonify(_list_knowledge_files())


@app.get("/api/knowledge/<filename>")
@login_required
def api_knowledge_get(filename):
    path = _knowledge_path(filename)
    if not os.path.isfile(path):
        return jsonify({"error": "Arquivo nao encontrado"}), 404
    with open(path, encoding="utf-8") as f:
        content = f.read()
    return jsonify({"filename": filename, "content": content})


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
    path = _knowledge_path(filename)
    if os.path.isfile(path):
        return jsonify({"error": "Arquivo ja existe"}), 409
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    reload_knowledge()
    print(f"[knowledge] criado: {filename}")
    return jsonify({"ok": True, "filename": filename})


@app.put("/api/knowledge/<filename>")
@login_required
def api_knowledge_update(filename):
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    path = _knowledge_path(filename)
    if not os.path.isfile(path):
        return jsonify({"error": "Arquivo nao encontrado"}), 404
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    reload_knowledge()
    print(f"[knowledge] salvo: {filename}")
    return jsonify({"ok": True})


@app.delete("/api/knowledge/<filename>")
@login_required
def api_knowledge_delete(filename):
    if filename == "README.md":
        return jsonify({"error": "Nao e possivel deletar README.md"}), 400
    path = _knowledge_path(filename)
    if not os.path.isfile(path):
        return jsonify({"error": "Arquivo nao encontrado"}), 404
    os.remove(path)
    reload_knowledge()
    print(f"[knowledge] deletado: {filename}")
    return jsonify({"ok": True})


@app.get("/api/knowledge/status")
@login_required
def api_knowledge_status():
    return jsonify(_knowledge_git_status())


@app.post("/api/knowledge/publish")
@login_required
def api_knowledge_publish():
    try:
        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            return jsonify({"ok": False, "error": "GITHUB_TOKEN nao configurado no .env"}), 400

        remote_url = f"https://{token}@github.com/edusene/Prompt.git"

        check = subprocess.run(['git', 'remote', 'get-url', 'origin'],
                               capture_output=True, text=True, timeout=10)
        if check.returncode == 0:
            subprocess.run(['git', 'remote', 'set-url', 'origin', remote_url],
                           capture_output=True, timeout=10)
        else:
            subprocess.run(['git', 'remote', 'add', 'origin', remote_url],
                           capture_output=True, timeout=10)

        subprocess.run(['git', 'config', 'user.email', 'bot@prompt.app'],
                       capture_output=True, timeout=10)
        subprocess.run(['git', 'config', 'user.name', 'Knowledge Bot'],
                       capture_output=True, timeout=10)

        subprocess.run(['git', 'checkout', '-B', 'main'], capture_output=True, timeout=10)

        subprocess.run(['git', 'add', 'knowledge/'], capture_output=True, timeout=10)

        has_staged = subprocess.run(['git', 'diff', '--cached', '--quiet'],
                                    capture_output=True, timeout=10)
        if has_staged.returncode == 0:
            print("[knowledge] nada para publicar (diff vazio)")
            return jsonify({"ok": True, "message": "Nada para publicar"})

        r = subprocess.run(
            ['git', 'commit', '-m', 'chore: update knowledge via panel'],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            err = (r.stdout + r.stderr).strip()
            if 'nothing to commit' in err:
                return jsonify({"ok": True, "message": "Nada para publicar"})
            print(f"[knowledge] commit ERRO: {err}")
            return jsonify({"ok": False, "error": f"Commit falhou: {err[:200]}"}), 500

        push = subprocess.run(['git', 'push', '--set-upstream', 'origin', 'main'],
                              capture_output=True, text=True, timeout=30)
        if push.returncode != 0:
            err = push.stderr.strip() or push.stdout.strip()
            print(f"[knowledge] push ERRO: {err}")
            return jsonify({"ok": False, "error": f"Falha ao push: {err[:200]}"}), 500

        print("[knowledge] publicado no git")
        return jsonify({"ok": True, "message": "Publicado no GitHub"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
