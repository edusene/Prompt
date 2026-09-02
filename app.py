"""
app.py — Flask webhook + pipeline de flush do agente WhatsApp + Gemini.
+ Painel CRM com autenticação, SSE e API REST completa.
"""

import os
import json
import time

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request, jsonify, Response, redirect, url_for, render_template, session, send_from_directory

import memory
import llm
import database
import events
from uazapi import send_text, send_presence, mark_read
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
    contact = database.get_contact(user)
    if contact and not contact["auto_mode"]:
        print(f"[flush] user={user} modo=humano, ignorando agente")
        return

    send_presence(user, "composing")

    user_turn = "\n".join(texts)
    memory.append(user, "user", user_turn)

    reply = llm.generate_reply(memory.get(user), FULL_SYSTEM_PROMPT)
    memory.append(user, "model", reply)

    chunks = split_reply(reply)
    if not chunks:
        send_presence(user, "paused")
        return

    for chunk in chunks:
        send_presence(user, "composing")
        time.sleep(1 + len(chunk) / 200)
        send_text(user, chunk)
        send_msg_out_to_panel(user, chunk)

    send_presence(user, "paused")


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


# ── Healthcheck ───────────────────────────────────────────────────────────────

@app.get("/favicon.ico")
def favicon():
    return send_from_directory("static", "favicon.ico", mimetype="image/x-icon")


@app.get("/")
def healthcheck():
    return "ok"


# ── Webhook config (UAZAPI chama PUT aqui para configurar) ────────────────────

@app.put("/webhook")
def webhook_config():
    """Endpoint chamado pela UAZAPI para configurar/validar o webhook."""
    data = request.get_json(silent=True) or {}
    webhook_url = data.get("webhookUrl", "")
    events = data.get("events", [])
    print(f"[webhook-config] url={webhook_url} events={events}")
    return jsonify({"ok": True, "message": "Webhook configurado com sucesso"})


# ── Webhook (preservado integralmente) ────────────────────────────────────────

@app.post("/webhook")
def webhook():
    data = request.get_json(silent=True) or {}

    if data.get("event") != "message":
        return jsonify({"ok": True})

    msg = data.get("data", {})

    if msg.get("fromMe") or msg.get("isGroup") or msg.get("type") != "text":
        return jsonify({"ok": True})

    raw_from = msg.get("from", "")
    number = raw_from.split("@")[0]
    body = msg.get("body", "").strip()
    message_id = msg.get("id", "")

    if not number or not body:
        return jsonify({"ok": True})

    print(f"[webhook] from={number} len={len(body)}")

    mark_read(number, message_id)

    # Persistir no banco e notificar o painel
    contact = database.upsert_contact(number)
    conv = database.get_or_create_conversation(number)
    saved = database.save_message(
        conversation_id=conv["id"],
        phone=number,
        direction="in",
        body=body,
        sender="user",
        msg_type="text",
        uazapi_id=message_id,
    )
    if saved:
        database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        database.increment_unread(conv["id"])
        events.emit("new_message", {
            "conversation_id": conv["id"],
            "contact_name": contact.get("name", ""),
            "phone": number,
            "body": body,
            "message_id": message_id,
        })

    buffer.add(number, body)

    return jsonify({"ok": True})


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


@app.get("/api/instance/status")
@login_required
def api_instance_status():
    from uazapi import get_instance_status
    r = get_instance_status()
    if not r["ok"]:
        err = r["error"]
        if "401" in err:
            err = "Token invalido. Verifique as configuracoes."
        return jsonify({"status": "error", "error": err})
    data = r["data"] or {}
    inst = data.get("instance", data) if isinstance(data, dict) else {}
    status = inst.get("status", "unknown")
    return jsonify({"status": status})


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
    convs = database.list_conversations(status=status, limit=limit, offset=offset)
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
    database.reset_unread(conv_id)
    return jsonify({"ok": True})


@app.post("/api/conversations/<int:conv_id>/tags")
@login_required
def api_conversations_add_tag(conv_id):
    data = request.get_json(silent=True) or {}
    tag_id = data.get("tag_id")
    if not tag_id:
        return jsonify({"error": "tag_id required"}), 400
    database.add_tag_to_conversation(conv_id, tag_id)
    return jsonify({"ok": True})


@app.delete("/api/conversations/<int:conv_id>/tags/<int:tag_id>")
@login_required
def api_conversations_remove_tag(conv_id, tag_id):
    with database._lock:
        conn = database._get_conn()
        conn.execute("DELETE FROM conversation_tags WHERE conversation_id=? AND tag_id=?", (conv_id, tag_id))
        conn.commit()
        conn.close()
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

    send_text(phone, body)

    contact = database.upsert_contact(phone)
    conv = database.get_or_create_conversation(phone)
    database.save_message(
        conversation_id=conv["id"],
        phone=phone,
        direction="out",
        body=body,
        sender="human",
        msg_type="text",
    )
    database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    events.emit("message_sent", {"conversation_id": conv["id"], "phone": phone, "body": body})
    return jsonify({"ok": True})


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
    return jsonify({"ok": True})


@app.delete("/api/contacts/<int:contact_id>/tags/<int:tag_id>")
@login_required
def api_contacts_remove_tag(contact_id, tag_id):
    database.remove_tag_from_contact(contact_id, tag_id)
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
    return jsonify(tag)


@app.put("/api/tags/<int:tag_id>")
@login_required
def api_tags_update(tag_id):
    data = request.get_json(silent=True) or {}
    database.update_tag(tag_id, name=data.get("name", ""), color=data.get("color", ""))
    return jsonify({"ok": True})


@app.delete("/api/tags/<int:tag_id>")
@login_required
def api_tags_delete(tag_id):
    database.delete_tag(tag_id)
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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
