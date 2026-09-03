"""
test_webhook.py — Simula mensagens recebidas do WhatsApp (formato UAZAPI)
contra o servidor Flask local, sem precisar de WhatsApp real.

Uso:
    python test_webhook.py "Olá, tudo bem?"
    python test_webhook.py "Oi" "Quero saber os preços" "Pode me ajudar?" --delay 2
    python test_webhook.py "Olá" --phone 5511987654321 --name "Maria" --timeout 90
    python test_webhook.py "Só registrar" --no-wait

O que ele faz:
    1. Verifica se o servidor está no ar (GET /).
    2. Envia POST /webhook com payload no formato UAZAPI (IDs únicos automáticos).
    3. Aguarda o debounce + resposta do agente, consultando o SQLite (DB_PATH do .env)
       e imprime as mensagens enviadas pelo agente/humano após o início do teste.

Observação: se a instância UAZAPI estiver conectada, a resposta do agente é enviada
de verdade via WhatsApp para o --phone informado. Use um número fictício para testes.
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "whatsapp.db")

# Garante saída UTF-8 no terminal do Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def check_server(base_url: str) -> bool:
    """GET / deve responder 'ok'."""
    try:
        r = requests.get(base_url + "/", timeout=5)
        return r.status_code == 200
    except requests.exceptions.RequestException:
        return False


def send_message(base_url: str, phone: str, name: str, body: str, msg_id: str) -> bool:
    """Envia POST /webhook simulando evento 'message' da UAZAPI."""
    payload = {
        "event": "message",
        "data": {
            "id": msg_id,
            "from": f"{phone}@s.whatsapp.net",
            "fromMe": False,
            "body": body,
            "type": "text",
            "pushName": name,
            "isGroup": False,
        },
    }
    try:
        r = requests.post(base_url + "/webhook", json=payload, timeout=15)
        ok = r.status_code == 200 and r.json().get("ok") is True
        print(f"[envio] id={msg_id} body={body[:60]!r} -> HTTP {r.status_code} {'OK' if ok else 'FALHOU'}")
        return ok
    except requests.exceptions.RequestException as e:
        print(f"[envio] ERRO de conexão: {e}")
        return False


def fetch_new_out_messages(phone: str, since_iso: str) -> list[dict]:
    """Busca no SQLite mensagens de saída (agente/humano) criadas após `since_iso`."""
    if not os.path.isfile(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT sender, body, created_at FROM messages "
        "WHERE phone=? AND direction='out' AND created_at > ? "
        "ORDER BY created_at ASC",
        (phone, since_iso),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def wait_for_replies(phone: str, since_iso: str, timeout: float) -> None:
    """Faz polling no banco até aparecer resposta do agente ou estourar o timeout."""
    print(f"[aguardando] resposta do agente (timeout {timeout:.0f}s, debounce + Gemini + envio)...")
    seen = 0
    start = time.time()
    last_dot = 0
    while time.time() - start < timeout:
        rows = fetch_new_out_messages(phone, since_iso)
        for row in rows[seen:]:
            label = {"agent": "🤖 AGENTE", "human": "🧑 HUMANO"}.get(row["sender"], row["sender"])
            print(f"\n{label} ({row['created_at']}):\n{row['body']}\n")
        seen = len(rows)
        # Quando a primeira resposta chega, aguarda mais um pouco por chunks extras
        if seen > 0 and time.time() - start > 5:
            time.sleep(3)
            final = fetch_new_out_messages(phone, since_iso)
            for row in final[seen:]:
                label = {"agent": "🤖 AGENTE", "human": "🧑 HUMANO"}.get(row["sender"], row["sender"])
                print(f"\n{label} ({row['created_at']}):\n{row['body']}\n")
            print(f"[fim] {len(final)} mensagem(ns) de resposta recebida(s).")
            return
        # Indicador de progresso (1 ponto por segundo)
        elapsed = int(time.time() - start)
        if elapsed > last_dot:
            print(".", end="", flush=True)
            last_dot = elapsed
        time.sleep(1)
    print(f"\n[timeout] Nenhuma resposta em {timeout:.0f}s.")
    print("Verifique: o servidor está rodando? GEMINI_API_KEY válida? "
          "O contato está em modo humano no painel?")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simula mensagens WhatsApp (formato UAZAPI) contra o Flask local.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("messages", nargs="+", help="Texto(s) da(s) mensagem(ns) a enviar")
    parser.add_argument("--phone", default="5511999999999",
                        help="Número do remetente fictício (padrão: 5511999999999)")
    parser.add_argument("--name", default="Cliente Teste",
                        help="pushName do remetente (padrão: 'Cliente Teste')")
    parser.add_argument("--url", default="http://localhost:5000",
                        help="URL base do servidor (padrão: http://localhost:5000)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Segundos entre mensagens de uma rajada (padrão: 1.0)")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="Timeout aguardando resposta do agente (padrão: 60s)")
    parser.add_argument("--no-wait", action="store_true",
                        help="Não aguarda resposta do agente (só envia)")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    print(f"[check] Servidor em {base_url} ...")
    if not check_server(base_url):
        print("[ERRO] Servidor não respondeu. Rode 'python app.py' em outro terminal primeiro.")
        sys.exit(1)
    print("[check] Servidor no ar \u2705")

    # Marca o início do teste (mesmo formato de created_at usado pelo database.py)
    since_iso = datetime.utcnow().isoformat()
    batch_id = int(time.time() * 1000)

    total = len(args.messages)
    for i, body in enumerate(args.messages, start=1):
        msg_id = f"TEST-{batch_id}-{i}"
        if not send_message(base_url, args.phone, args.name, body, msg_id):
            sys.exit(1)
        if i < total:
            time.sleep(args.delay)

    if total > 1:
        print(f"[info] {total} mensagens enviadas. Se o intervalo ({args.delay}s) for menor "
              f"que BUFFER_SECONDS, elas serão agrupadas em um único turno do agente.")

    if not args.no_wait:
        wait_for_replies(args.phone, since_iso, args.timeout)
    else:
        print("[info] --no-wait: respostas podem ser vistas no painel http://localhost:5000/panel")


if __name__ == "__main__":
    main()
