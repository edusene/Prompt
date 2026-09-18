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
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# Windows: stdout pode estar em cp1252
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

from flask import Flask, request, jsonify, Response, redirect, url_for, render_template, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import memory
import llm
import database
import events
import meta
import cardapio
import parse_knowledge
import scheduler as crm_scheduler
from agents.conversation import _extract_data_from_message, ConversationState

from meta import send_text, mark_read, send_typing, get_media_url, download_media_bytes
from meta import get_phone_info, list_templates, get_template, delete_template, create_template
from meta import get_business_profile, update_business_profile, get_phone_id, get_waba_id
from meta import get_webhook_media_url
from buffer import MessageBuffer
from auth import login_required, do_login, do_logout

# ── Sub-agentes (opcional) ───────────────────────────────────────────────────
USE_SUB_AGENTS = os.getenv("USE_SUB_AGENTS", "false").lower() == "true"
_conversation_manager = None

def _get_conversation_manager():
    """Lazy load do ConversationManager (so carrega se habilitado)."""
    global _conversation_manager
    if _conversation_manager is None and USE_SUB_AGENTS:
        from agents.conversation import get_manager
        _conversation_manager = get_manager()
    return _conversation_manager

# ── Config ──────────────────────────────────────────────────────────────────
BUFFER_SECONDS = float(os.getenv("BUFFER_SECONDS", "8"))
PORT = int(os.getenv("PORT", "5000"))
SECRET_KEY = os.getenv("SECRET_KEY", "troque_esta_chave_secreta_aleatoria")
DATAFY_WEBHOOK_SECRET = os.getenv("DATAFY_WEBHOOK_SECRET", "")

_BASE_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "Voce e a Sandrinha da BALBS SALGADOS, em Porto Velho - RO. "
    "Responda usando a estrutura de blocos definida no prompt operacional.",
)

# Carrega o system prompt do arquivo .docx (com adapted rules)
_PROMPT_FROM_DOCX = parse_knowledge.get_prompt_cached()

# Se o docx foi carregado, usa ele; senao, usa o fallback
if _PROMPT_FROM_DOCX:
    FULL_SYSTEM_PROMPT = _PROMPT_FROM_DOCX
else:
    FULL_SYSTEM_PROMPT = _BASE_PROMPT

# Tools description (sempre injetada)
FULL_SYSTEM_PROMPT += (
    "\n\n=== TOOLS DISPONIVEIS ==="
    "\nVoce tem acesso a uma tool 'search_knowledge' para buscar informacoes "
    "na base de conhecimento. Chame essa tool sempre que precisar consultar "
    "produtos, precos, politicas ou qualquer informacao da empresa."
    "\nVoce tem acesso a uma tool 'transfer_to_human' para transferir o atendimento a um humano. "
    "USE essa tool quando: o cliente pedir explicitamente para falar com um atendente ou humano; "
    "a situacao envolver reclamacao grave, cancelamento, reembolso ou devolucao; "
    "voce nao souber responder adequadamente; "
    "a solicitacao estiver fora do seu escopo; "
    "ou quando perceber que o cliente esta frustrado ou insatisfeito. "
    "Ao transferir, informe brevemente o motivo no parametro 'reason'."
    "\nVoce tem acesso a uma tool 'calculate_order' para validar pedidos no cardapio. "
    "Chame essa tool ANTES de confirmar o pedido com o cliente, informando os itens e o bairro de entrega. "
    "Ela valida produtos, quantidades, calcula subtotal, frete e total."
    "\nVoce tem acesso a uma tool 'send_order_to_cardapio' para enviar pedidos confirmados para o cardapio digital. "
    "USE essa tool SOMENTE apos o cliente ter confirmado o pedido completo e voce ter validado com calculate_order. "
    "Nunca envie sem confirmacao explicita do cliente."
)

# ── Knowledge Tool (busca seletiva via function calling) ──────────────────────
import knowledge_rag

KNOWLEDGE_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": (
            "Busca informacoes na base de conhecimento da empresa. "
            "USE ESTA TOOL para qualquer pergunta sobre produtos, precos, servicos, "
            "horarios, politicas, regras, normas, procedimentos, devolucoes, trocas, "
            "cancelamentos, garantias. "
            "Pode buscar por nome de arquivo OU por palavra-chave do tema."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Busca descritiva sobre o tema desejado (ex: 'preco produto X', 'politica devolucao')",
                },
                "filename": {
                    "type": "string",
                    "description": "Nome exato do arquivo knowledge (ex: '02_cardapio_produtos_precos.md'). Use quando souber qual arquivo consultar.",
                },
            },
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


def _knowledge_tool_handler(query: str = "", filename: str = "", **kwargs) -> str:
    print(f"[knowledge] TOOL CHAMADA query='{query}' filename='{filename}'")
    # Se filename foi informado, retorna o conteúdo completo do arquivo
    if filename:
        content = knowledge_rag.get_file_content(filename.strip())
        if content:
            print(f"[knowledge] RESULTADO (filename): {len(content)} chars")
            return content
        return f"Arquivo '{filename}' nao encontrado."
    # Senão, busca por query (busca vetorial)
    if not query or not query.strip():
        return "Forneça uma busca descritiva sobre o tema desejado."
    content = knowledge_rag.search_context(query.strip(), top_k=8)
    if not content:
        return "Nenhuma informacao encontrada para essa busca."
    print(f"[knowledge] RESULTADO (query): {len(content)} chars")
    return content


# ── Transfer Tool (transferência para humano via function calling) ─────────────

# Palavras-chave fixas que disparam transferência automática ANTES de chamar o LLM
TRANSFER_KEYWORDS = [
    "falar com atendente",
    "falar com humano",
    "falar com pessoa",
    "quero atendente",
    "quero humano",
    "preciso de atendente",
    "atendimento humano",
    "atendente humano",
    "falar com alguem",
    "falar com alguém",
    "reclamacao",
    "reclamação",
    "cancelar",
    "cancelamento",
    "reembolso",
    "devolver",
    "devolucao",
    "devolução",
    "gerente",
    "supervisor",
    "ouvidoria",
]

TRANSFER_TOOL = {
    "type": "function",
    "function": {
        "name": "transfer_to_human",
        "description": (
            "Transfere a conversa para um atendente humano. "
            "Use quando: o cliente pedir para falar com um humano ou atendente; "
            "a situação envolver reclamação grave, cancelamento ou reembolso; "
            "você não souber responder adequadamente; "
            "ou quando a solicitação estiver fora do seu escopo de atuação."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Breve motivo da transferência (ex: 'cliente solicitou atendente', 'reclamação sobre pedido')",
                },
            },
            "required": ["reason"],
        },
    },
}


# ── Cardápio Tools (validação e envio de pedidos via function calling) ───────

CALCULATE_ORDER_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_order",
        "description": (
            "Valida um pedido no cardapio: verifica se os produtos existem, "
            "calcula subtotal, taxa de entrega e total. Chame ANTES de apresentar "
            "o resumo do pedido ao cliente. Inclua o price (preco unitario) de cada "
            "item obtido do knowledge para calcular o subtotal corretamente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Lista de itens do pedido com precos",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Nome exato do produto cadastrado"},
                            "quantity": {"type": "integer", "description": "Quantidade do item"},
                            "price": {"type": "number", "description": "Preco unitario do item (obtido do knowledge)"},
                        },
                        "required": ["name", "quantity"],
                    },
                },
                "bairro": {
                    "type": "string",
                    "description": "Bairro de entrega (necessario para calcular frete)",
                },
            },
            "required": ["items", "bairro"],
        },
    },
}

SEND_ORDER_TOOL = {
    "type": "function",
    "function": {
        "name": "send_order_to_cardapio",
        "description": (
            "Envia um pedido confirmado para o cardapio digital da BALBS. "
            "Use SOMENTE apos o cliente ter confirmado o pedido completo (itens, "
            "quantidades, forma de pagamento, endereco se entrega) e apos validacao "
            "via calculate_order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Nome completo do cliente",
                },
                "customer_phone": {
                    "type": "string",
                    "description": "Telefone do cliente com DDD e codigo do pais (ex: 5569999999999)",
                },
                "delivery_address": {
                    "type": "string",
                    "description": "Endereco completo de entrega (rua, numero, bairro, cidade). Opcional se for retirada.",
                },
                "payment_method": {
                    "type": "string",
                    "description": "Forma de pagamento: 'pix', 'cartao_debito', 'cartao_credito', 'dinheiro'",
                },
                "items": {
                    "type": "array",
                    "description": "Lista de itens do pedido",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Nome exato do produto cadastrado no cardapio"},
                            "quantity": {"type": "integer", "description": "Quantidade do item"},
                            "price": {"type": "number", "description": "Preco UNITARIO por UNIDADE (ex: Coxinha = 0.80, NAO 20.00 que e o preco da porcao de 25)"},
                            "notes": {"type": "string", "description": "Observacao especifica deste item"},
                            "selected_flavors": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Sabores selecionados (se aplicavel)",
                            },
                        },
                        "required": ["name", "quantity", "price"],
                    },
                },
                "subtotal": {"type": "number", "description": "Subtotal dos itens"},
                "delivery_fee": {"type": "number", "description": "Taxa de entrega"},
                "return_fee": {"type": "number", "description": "Taxa de retorno"},
                "total": {"type": "number", "description": "Total do pedido (itens + frete)"},
                "notes": {"type": "string", "description": "Observacoes gerais do pedido"},
                "whatsapp_message_id": {"type": "string", "description": "ID da mensagem WhatsApp que confirmou o pedido (para idempotencia)"},
                "scheduled_date": {"type": "string", "description": "Data agendada para entrega/retirada (formato DD/MM/YYYY). Use apenas quando o pedido for agendado."},
                "is_scheduled": {"type": "boolean", "description": "Se true, o pedido e agendado para a data em scheduled_date. Use quando o cliente pedir agendamento."},
            },
            "required": ["customer_name", "customer_phone", "items", "total"],
        },
    },
}


def _transfer_tool_handler(phone: str):
    """Retorna uma closure que chama perform_transfer com o phone correto."""
    def _handler(reason: str = "") -> str:
        performed = perform_transfer(phone, reason)
        if performed:
            return "Transferência realizada com sucesso. O atendente humano continuará o atendimento."
        return "O cliente já está sendo atendido por um humano."
    return _handler


def _check_transfer_keywords(items: list[dict]) -> str | None:
    """Verifica se alguma mensagem contém palavras-chave de transferência.
    Retorna a keyword encontrada ou None."""
    full_text = " ".join(
        item.get("text", "").lower()
        for item in items
        if item.get("text")
    )
    for kw in TRANSFER_KEYWORDS:
        if kw in full_text:
            return kw
    return None


# ── Cardápio Tool Handlers ──────────────────────────────────────────────────

# Flag para controlar pedidos já enviados (evita duplicação)
_orders_sent: set[str] = set()

def _calculate_order_handler(items: list[dict], bairro: str = "", **kwargs) -> str:
    """Handler para a tool calculate_order."""
    print(f"[cardápio] TOOL calculate_order items={items} bairro={bairro}")
    result = cardapio.calcular_pedido(items, bairro)
    if result.get("success"):
        return json.dumps(result, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


def _send_order_handler(
    customer_name: str,
    customer_phone: str,
    items: list[dict],
    total: float,
    delivery_address: str = "",
    payment_method: str = "",
    subtotal: float = 0,
    delivery_fee: float = 0,
    return_fee: float = 0,
    notes: str = "",
    whatsapp_message_id: str = "",
    scheduled_date: str = "",
    is_scheduled: bool = False,
    **kwargs,
) -> str:
    """Handler para a tool send_order_to_cardapio."""
    # Verifica se o pedido já foi enviado (evita duplicação)
    order_key = f"{customer_phone}:{total}:{json.dumps(items, sort_keys=True)}"
    if order_key in _orders_sent:
        print(f"[cardápio] PEDIDO JÁ ENVIADO - ignorando chamada duplicada: {order_key[:50]}")
        return "Este pedido ja foi enviado anteriormente. Nao e necessario enviar novamente."
    
    if items:
        try:
            if not subtotal or float(subtotal) == 0:
                subtotal = sum(float(item.get("quantity", 0)) * float(item.get("price", 0)) for item in items)
            if not total or float(total) == 0:
                total = float(subtotal) + float(delivery_fee) - float(return_fee)
        except (ValueError, TypeError):
            pass
    print(f"[cardápio] TOOL send_order customer={customer_name} phone={customer_phone} total={total}")

    test_mode = (database.get_setting("CARDÁPIO_TEST_MODE", "true") or os.getenv("CARDÁPIO_TEST_MODE", "true") or os.getenv("CARD_TEST_MODE", "true")).lower() == "true"
    is_test = 1 if test_mode else 0

    # Detecta phone do contexto da conversa (user variable do escopo pai)
    conv_phone = ""
    try:
        conv = database.get_or_create_conversation(customer_phone)
        if conv:
            contact = database.get_contact_by_id(conv.get("contact_id", 0))
            if contact:
                conv_phone = contact.get("phone", "")
    except Exception:
        pass

    if test_mode:
        # Modo teste: simula resposta sem enviar para o cardápio real
        import random as _rnd
        _test_id = f"TEST-{__import__('uuid').uuid4().hex[:8]}"
        _test_display = f"T{_rnd.randint(100, 999)}"
        result = {
            "success": True,
            "order_id": _test_id,
            "display_id": _test_display,
            "status": "enviado",
        }
        print(f"[cardápio] MODO TESTE - pedido simulado: {_test_display} (nenhum envio à API real)")
    else:
        # Modo produção: envia para o cardápio real
        result = cardapio.criar_pedido(
            customer_name=customer_name,
            customer_phone=customer_phone,
            items=items,
            total=total,
            customer_confirmed=True,
            delivery_address=delivery_address,
            payment_method=payment_method,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            return_fee=return_fee,
            notes=notes,
            whatsapp_message_id=whatsapp_message_id,
            scheduled_date=scheduled_date,
            is_scheduled=is_scheduled,
        )

    # Salva log no banco
    try:
        conv = database.get_or_create_conversation(customer_phone)
        conv_id = conv["id"] if conv else 0
        contact = database.get_contact(customer_phone)
        contact_id = contact["id"] if contact else 0
    except Exception:
        conv_id = 0
        contact_id = 0

    database.criar_cardapio_order(
        conversation_id=conv_id,
        contact_id=contact_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        delivery_address=delivery_address,
        payment_method=payment_method,
        items=items,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        return_fee=return_fee,
        total=total,
        notes=notes,
        whatsapp_message_id=whatsapp_message_id,
        is_test=is_test,
        order_id_externo=result.get("order_id", ""),
        display_id=result.get("display_id", ""),
        status=result.get("status", "enviado") if result.get("success") else "erro",
        api_response=result,
        error_message=result.get("error", "") if not result.get("success") else "",
    )

    # Emite evento SSE para atualizar o painel em tempo real
    try:
        events.emit("cardapio_order", {
            "customer_name": customer_name,
            "total": total,
            "is_test": is_test,
            "status": result.get("status", "erro"),
        })
    except Exception:
        pass

    if result.get("success"):
        display_id = result.get("display_id", "")
        # Marca o pedido como enviado para evitar duplicação
        _orders_sent.add(order_key)
        # Limita o tamanho do set para evitar memory leak
        if len(_orders_sent) > 1000:
            _orders_sent.clear()
        return f"Pedido enviado com sucesso para o cardapio digital. Numero do pedido: {display_id}. Status: {result.get('status', 'enviado')}."
    else:
        return f"Erro ao enviar pedido para o cardapio: {result.get('error', 'desconhecido')}"


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

_OPENAI_SUPPORTED_AUDIO = {
    "audio/ogg", "audio/ogg; codecs=opus", "audio/mpeg", "audio/mp3",
    "audio/mp4", "audio/aac", "audio/wav", "audio/x-wav",
    "audio/webm", "audio/flac", "audio/x-m4a",
}

_OPENAI_SUPPORTED_DOCS = {
    "application/pdf",
}


def _normalize_mime(mime: str) -> str:
    """Normaliza MIME type removendo codecs (ex: 'audio/ogg; codecs=opus' -> 'audio/ogg')."""
    return mime.split(";")[0].strip().lower()


def _parse_data_uri(data_uri: str) -> tuple[str, str]:
    m = __import__("re").match(r"^data:([^;,]+);base64,(.+)$", data_uri, __import__("re").DOTALL)
    if m:
        return m.group(1), m.group(2)
    return "", data_uri


def transcribe_audio(b64_data: str, mime: str) -> str:
    """Transcreve áudio via OpenAI Whisper. Retorna texto transcrito ou vazio."""
    try:
        import base64 as _b64
        raw = _b64.b64decode(b64_data)
        if len(raw) > 25 * 1024 * 1024:
            print("[transcribe] áudio > 25MB, ignorando")
            return ""
        ext_map = {
            "audio/ogg": "ogg", "audio/ogg; codecs=opus": "ogg",
            "audio/mpeg": "mp3", "audio/mp3": "mp3",
            "audio/mp4": "m4a", "audio/aac": "m4a",
            "audio/wav": "wav", "audio/x-wav": "wav",
            "audio/webm": "webm", "audio/flac": "flac",
            "audio/x-m4a": "m4a",
        }
        ext = ext_map.get(mime, "ogg")
        from llm import _client as _oai
        resp = _oai.audio.transcriptions.create(
            model="whisper-1",
            file=(f"audio.{ext}", raw, mime.split(";")[0]),
            language="pt",
        )
        text = (resp.text or "").strip()
        print(f"[transcribe] len={len(text)} preview={text[:80]}")
        return text
    except Exception as e:
        print(f"[transcribe] ERRO: {e}")
        return ""


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

            if not media_url and msg_type != "text":
                wa_media_id_db = str((msg or {}).get("wa_media_id") or "").strip()
                if wa_media_id_db:
                    try:
                        info = get_webhook_media_url(wa_media_id_db)
                        if info.get("ok") and isinstance(info.get("data"), dict):
                            url = info["data"].get("url", "")
                            mime_from_api = info["data"].get("mime_type", "application/octet-stream")
                            if url:
                                import requests as _req
                                resp = _req.get(url, timeout=30)
                                if resp.status_code == 200:
                                    import base64 as _b64
                                    b64_data = _b64.b64encode(resp.content).decode()
                                    media_url = f"data:{mime_from_api};base64,{b64_data}"
                                    database.update_message_media(msg["id"], media_url)
                                    print(f"[flush] on-demand download ok msg={msg['id']} mime={mime_from_api}")
                    except Exception as e:
                        print(f"[flush] WARN on-demand download falhou msg={msg_id}: {e}")

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

                if mime and b64 and msg_type in ("audio", "ptt") and _normalize_mime(mime) in _OPENAI_SUPPORTED_AUDIO:
                    transcription = transcribe_audio(b64, mime)
                    if transcription:
                        parts.append({"text": f"O cliente enviou uma mensagem de voz. Transcrição: {transcription}"})
                    else:
                        parts.append({"text": media_desc})
                elif mime and b64 and _normalize_mime(mime) in _OPENAI_SUPPORTED_IMAGES:
                    parts.append({"text": media_desc})
                    parts.append({
                        "inline_data": {
                            "mime_type": mime,
                            "data": b64,
                        }
                    })
                elif mime and b64 and _normalize_mime(mime) in _OPENAI_SUPPORTED_DOCS:
                    decoded_size = len(b64) * 3 // 4
                    if decoded_size > 2 * 1024 * 1024:
                        parts.append({"text": media_desc + " (arquivo muito grande para analisar automaticamente)"})
                    else:
                        parts.append({"text": media_desc})
                        parts.append({
                            "inline_data": {
                                "mime_type": _normalize_mime(mime),
                                "data": b64,
                            }
                        })
                else:
                    parts.append({"text": media_desc})

                continue

        if text:
            parts.append({"text": text})

    if not parts:
        parts.append({"text": "[Mensagem sem conteudo de texto]"})

    return parts


# ── Transfer ──────────────────────────────────────────────────────────────────

def perform_transfer(phone: str, reason: str = "", send_message: bool = True) -> bool:
    """Transfere a conversa de um usuário para atendimento humano.

    - Muda auto_mode do contato para 0
    - Registra transferred_at na conversa
    - Salva mensagem interna de sistema no chat
    - Envia mensagem configurável ao cliente (se send_message=True)
    - Emite evento SSE para atualizar o painel em tempo real

    Retorna True se a transferência foi feita, False se já estava em modo humano.
    """
    contact = database.get_contact(phone)
    if contact and not contact.get("auto_mode", 1):
        print(f"[transfer] user={phone} já está em modo humano, ignorando")
        return False

    now = datetime.utcnow().isoformat()
    now_fmt = datetime.utcnow().strftime("%d/%m %H:%M")

    # Muda para modo humano
    if contact:
        database.update_contact(contact["id"], auto_mode=0)
    else:
        # Cria contato se não existir
        contact = database.upsert_contact(phone)
        database.update_contact(contact["id"], auto_mode=0)

    # Busca ou cria conversa
    conv = database.get_or_create_conversation(phone)
    database.update_conversation(conv["id"], transferred_at=now)

    reason_log = f" Motivo: {reason}" if reason else ""
    print(f"[transfer] user={phone} transferido para humano.{reason_log}")

    # Mensagem interna no chat (não visível ao cliente)
    internal_body = f"Transferido pelo agente em {now_fmt}"
    if reason:
        internal_body += f" — {reason}"
    database.save_message(
        conversation_id=conv["id"],
        phone=phone,
        direction="internal",
        body=internal_body,
        sender="system",
        msg_type="system",
    )

    # Busca mensagem de transferência configurável
    transfer_msg = database.get_setting("transfer_message", "") or _DEFAULT_TRANSFER_MESSAGE

    # Envia mensagem ao cliente (somente se send_message=True)
    if send_message:
        res = send_text(phone, transfer_msg)
        if res.get("ok"):
            database.save_message(
                conversation_id=conv["id"],
                phone=phone,
                direction="out",
                body=transfer_msg,
                sender="agent",
                msg_type="text",
            )
            database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        else:
            print(f"[transfer] WARN: falha ao enviar msg de transferência p/ {phone}: {res.get('error')}")
    else:
        # Salva a mensagem de transferência no banco para envio posterior
        database.save_message(
            conversation_id=conv["id"],
            phone=phone,
            direction="out",
            body=transfer_msg,
            sender="agent",
            msg_type="text",
        )

    # Emite evento SSE para o painel atualizar em tempo real
    events.emit("conversation_updated", {
        "conversation_id": conv["id"],
        "auto_mode": 0,
        "transferred": True,
        "transferred_at": now,
    })

    return True


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
        else:
            print(f"[flush] user={user} contact_name='{contact.get('name', '')}'")

        # ── Sub-agentes (se habilitado) ──────────────────────────────────────
        if USE_SUB_AGENTS:
            manager = _get_conversation_manager()
            if manager:
                print(f"[flush] user={user} usando SUB-AGENTES")
                # Extrai texto da mensagem
                message_text = "\n".join(it.get("text", "") for it in items)
                # Typing indicator
                last_wa_msg_id = items[-1].get("wa_message_id", "") if items else ""
                if last_wa_msg_id and last_wa_msg_id.startswith("wamid."):
                    send_typing(last_wa_msg_id)
                # Processa via sub-agentes
                reply = manager.handle_message(phone=user, message=message_text)
                if reply:
                    memory.append(user, "assistant", reply)
                    chunks = split_reply(reply)
                    for chunk in chunks:
                        time.sleep(1 + len(chunk) / 200)
                        send_text(user, chunk)
                        send_msg_out_to_panel(user, chunk)
                return

        # ── Modo classico (prompt unico) ─────────────────────────────────────
        # ── Detecção de palavras-chave de transferência (antes do LLM) ──────────
        matched_kw = _check_transfer_keywords(items)
        if matched_kw:
            print(f"[flush] user={user} keyword de transferência detectada: '{matched_kw}'")
            perform_transfer(user, reason=f"cliente mencionou '{matched_kw}'")
            return

        # Typing indicator (Meta: combinado com mark_read via typing_indicator)
        last_wa_msg_id = items[-1].get("wa_message_id", "") if items else ""
        if last_wa_msg_id and last_wa_msg_id.startswith("wamid."):
            send_typing(last_wa_msg_id)

        # ── Extracao de dados da mensagem (modo classico) ────────────────────
        # Extraindo dados estruturados para injetar no prompt dinamico
        text_only = "\n".join(it.get("text", "") for it in items)
        _ext_state = ConversationState(
            phone=user,
            name=contact.get("name", "") if contact else "",
            items=[],
            modality="",
            bairro="",
            endereco="",
            horario="",
            data_agendada="",
            payment_method="",
        )
        _extracted = _extract_data_from_message(text_only, _ext_state)
        _extracted_summary = ""
        if _extracted:
            _parts = []
            if "name" in _extracted:
                _parts.append(f"nome={_extracted['name']}")
            if "items" in _extracted:
                _parts.append(f"itens={_extracted['items']}")
            if "modality" in _extracted:
                _parts.append(f"modalidade={_extracted['modality']}")
            if "endereco" in _extracted:
                _parts.append(f"endereco={_extracted['endereco']}")
            if "bairro" in _extracted:
                _parts.append(f"bairro={_extracted['bairro']}")
            if "horario" in _extracted:
                _parts.append(f"horario={_extracted['horario']}")
            if "data_agendada" in _extracted:
                _parts.append(f"data={_extracted['data_agendada']}")
            if "payment_method" in _extracted:
                _parts.append(f"pagamento={_extracted['payment_method']}")
            if "referencia" in _extracted:
                _parts.append(f"referencia={_extracted['referencia']}")
            _extracted_summary = ", ".join(_parts)
            print(f"[extract] DADOS EXTRAIDOS: {_extracted_summary}")

        user_parts = _build_user_parts(items)
        text_only = "\n".join(it["text"] for it in items)
        
        # --- INÍCIO DETECÇÃO DE CONFIRMAÇÃO ---
        history_before = memory.get(user)
        last_assistant_text = ""
        for m in reversed(history_before):
            if m.get("role") == "assistant":
                last_assistant_text = (m.get("parts", [{}])[0].get("text", "") or "")
                break
                
        is_short_reply = len(text_only.strip()) <= 60
        confirmation_keywords = ["sim", "isso", "isso mesmo", "esse", "essa", "esse mesmo", "1 mesmo", "uma mesmo", "1 de 25", "1 de 25 mesmo", "pode ser", "correto", "exatamente", "é isso", "quero esse", "ok", "manda", "confirmo"]
        
        # Padrões para detectar respostas sobre data/horário
        date_time_patterns = ["hoje", "amanha", "agora", "daqui a pouco", "já", "já já"]
        time_patterns = ["\d{1,2}h", "\d{1,2}:\d{2}", "manha", "tarde", "noite", "almoco"]
        
        is_confirmation = False
        is_date_time_reply = False
        text_lower = text_only.strip().lower()
        
        # Verifica se é uma resposta sobre data/horário
        if is_short_reply:
            for pattern in date_time_patterns:
                if pattern in text_lower:
                    is_date_time_reply = True
                    break
            if not is_date_time_reply:
                import re
                for pattern in time_patterns:
                    if re.search(pattern, text_lower):
                        is_date_time_reply = True
                        break
        
        # Detecta confirmação
        if is_short_reply and (text_lower in confirmation_keywords or text_lower in last_assistant_text.lower() or "confirma" in last_assistant_text.lower() or "?" in last_assistant_text[-15:]):
            is_confirmation = True
            
        memory.append_multimodal(user, "user", user_parts)

        dynamic_prompt = FULL_SYSTEM_PROMPT
        
        if is_confirmation and last_assistant_text:
            dynamic_prompt += (
                "\n\n⚠️ ATENÇÃO: CONTEXTO DE CONFIRMAÇÃO DETECTADO\n"
                "A última mensagem do cliente parece ser uma confirmação ou resposta direta à sua última pergunta.\n"
                f"Sua última pergunta/mensagem foi: '{last_assistant_text[-250:]}'\n"
                "NÃO reinterprete a mensagem do cliente como uma nova intenção isolada.\n"
                "PRESERVE TODO o estado do pedido (produto, formato, quantidade em porções, preço da porção, etc) que já estava sendo discutido.\n"
                "Se o cliente disse algo como '1 de 25 mesmo', ele está confirmando a porção de 25 que você ofereceu, NÃO pedindo 1 unidade individual.\n"
            )
        
        # Detecta se é uma resposta sobre data/horário
        if is_date_time_reply and last_assistant_text:
            # Verifica se a última mensagem do bot era sobre data/horário
            perguntas_data_horario = ["data", "horario", "horário", "que dia", "que hora", "quando", "que dia"]
            is_response_to_datetime = any(p in last_assistant_text.lower() for p in perguntas_data_horario)
            
            if is_response_to_datetime:
                dynamic_prompt += (
                    "\n\n⚠️ ATENÇÃO: RESPOSTA SOBRE DATA/HORÁRIO DETECTADA\n"
                    "O cliente está respondendo à sua pergunta sobre data e/ou horário.\n"
                    f"Sua última pergunta/mensagem foi: '{last_assistant_text[-250:]}'\n"
                    "NÃO pergunte o nome do cliente novamente - você já tem essa informação.\n"
                    "Interprete a resposta como data e/ou horário conforme o contexto.\n"
                    "Exemplos:\n"
                    "- 'hoje, 18h' = data é hoje, horário é 18h\n"
                    "- 'hoje' = apenas data, pergunte o horário\n"
                    "- '18h' = apenas horário, pergunte a data\n"
                )
        # --- FIM DETECÇÃO DE CONFIRMAÇÃO ---

        # Injeta dados do xlsx (cardapio, bairros, horarios, pagamentos)
        knowledge_dados = parse_knowledge.get_knowledge_dados()
        if knowledge_dados:
            dynamic_prompt += (
                "\n\n=== BASE DE DADOS OFICIAL ===\n"
                "Use estes dados como FONTE VERDADEIRA para precos, taxas de frete, "
                "horarios de funcionamento e formas de pagamento.\n\n"
                + knowledge_dados
            )

        # ── CONTEUDO DINAMICO (no final para preservar cache do prefixo estatico) ──
        # Injeta data e hora atual para o agente saber o horario (fuso de Porto Velho = UTC-4)
        _now = (datetime.utcnow() - timedelta(hours=4)).strftime("%d/%m/%Y %H:%M")
        _tomorrow = (datetime.utcnow() - timedelta(hours=4) + timedelta(days=1)).strftime("%d/%m/%Y")
        dynamic_prompt += (
            f"\n\nData e hora atual (Porto Velho): {_now}. "
            f"Data de amanha (se agendar): {_tomorrow}."
        )

        # Injeta nome do cliente no prompt
        if contact and contact.get("name"):
            dynamic_prompt += (
                f"\n\nO nome do cliente e: {contact['name']}. "
                f"Use o nome dele ao conversar, mas de forma natural. "
                f"NUNCA pergunte o nome novamente - ele ja foi coletado."
            )
            print(f"[flush] injetando nome: {contact['name']}")

        # Injeta telefone do cliente no prompt (necessario para send_order_to_cardapio)
        dynamic_prompt += (
            f"\n\nO telefone (numero WhatsApp) do cliente e: {user}. "
            f"Use este numero ao enviar pedidos com send_order_to_cardapio."
        )

        # Injeta dados extraidos da mensagem no prompt (REMOVIDO - injetado apos OVERRIDE FINAL)

        # REGRAS CRITICAS DE CONTROLE DE FLUXO
        dynamic_prompt += (
            "\n\n⚠️ REGRAS CRITICAS DE CONTROLE DE FLUXO ⚠️"
            "\n1. NUNCA chame send_order_to_cardapio SEM ter apresentado o resumo do pedido ANTES"
            "\n2. NUNCA chame send_order_to_cardapio SEM o cliente ter confirmado EXPLICITAMENTE (sim, ok, pode ser, confirma)"
            "\n3. Se o cliente disse 'sim' mas voce ainda NAO apresentou o resumo, APRESENTE o resumo PRIMEIRO"
            "\n4. Apos apresentar resumo, AGUARDE confirmacao do cliente"
            "\n5. So CHAME send_order_to_cardapio apos o cliente ter confirmado o resumo"
            "\n6. NUNCA assume que o cliente quer enviar o pedido sem confirmacao"
            "\n7. NUNCA assuma entrega ou retirada SEM PERGUNTA - EXCETO se modalidade ja extraida"
            "\n8. PRIMEIRA PERGUNTA sobre logistica: 'E para entrega ou retirada na fabrica?' - EXCETO se modalidade ja extraida"
            "\n9. NAO pule esta pergunta - ela e OBRIGATORIA (exceto se modalidade ja extraida)"
            "\n10. ANTES de apresentar resumo, VERIFIQUE: tem modalidade? tem data? tem horario? Se falta algum, PERGUNTE"
            "\n11. NAO apresente resumo SEM data + horario + modalidade confirmados"
            "\n12. NAO PERGUNTE forma de pagamento se o cliente JA INFORMOU (pix/dinheiro/cartao)"
            "\n13. NAO PERGUNTE ponto de referencia se o cliente NAO INFORMOU - se nao mencionou, nao tem. ACEITE."
            "\n14. QUANDO CLIENTE DEFINE QUANTIDADE (ex: '50 coxinhas'), ACEITE como meio cento. NAO questione formato."
        )

        # OVERRIDE FINAL - Regras que SOBRESCREVEM qualquer instrucao anterior
        dynamic_prompt += (
            "\n\n🚨 OVERRIDE FINAL - INSTRUCOES QUE SOBRESCREVEM TUDO ANTERIOR 🚨"
            "\nSE o pedido ja tem itens mas NAO tem modalidade (entrega/retirada):"
            "\n  -> PERGUNTE: 'Será para entrega ou retirada na fabrica?' - EXCETO se modalidade ja foi extraida da mensagem"
            "\nSE tem modalidade mas NAO tem data:"
            "\n  -> PERGUNTE: 'Qual data você prefere?'"
            "\nSE tem data mas NAO tem horario:"
            "\n  -> PERGUNTE: 'E qual horário?'"
            "\nSE tem tudo mas e entrega e NAO tem endereco:"
            "\n  -> PERGUNTE bairro, rua, numero. SE cliente nao informou referencia, ACEITE e prossiga."
            "\nSE e entrega e tem bairro mas NAO calculou frete:"
            "\n  -> CALCULE o frete com search_knowledge ANTES do resumo"
            "\nSE cliente JA INFORMOU pagamento (pix/dinheiro/cartao): NAO PERGUNTE NOVAMENTE"
            "\nSE cliente DEFINIU quantidade (ex: '50 coxinhas'): ACEITE como meio cento, NAO questione formato"
            "\nNAO assuma 'hoje' para data - SEMPRE confirme com o cliente"
            "\nNAO assuma horario - SEMPRE pergunte ao cliente"
            "\nAPENAS depois de ter TODOS os dados acima, apresente o resumo"
            "\nNUNCA pule etapas mesmo que o cliente responda de forma incompleta"
            "\n\n🚫 NÃO CHAME send_order_to_cardapio AGORA - apenas apresente o resumo e aguarde 'sim' do cliente 🚫"
            "\n\nAo CHAMAR send_order_to_cardapio (depois da confirmacao), envie:"
            "\n- customer_name, customer_phone, items [{name, quantity, price}], total"
            "\n- subtotal, payment_method ('pix'/'cartao_debito'/'cartao_credito'/'dinheiro')"
            "\n- SE ENTREGA: delivery_address, delivery_fee, return_fee (2.00 dinheiro/cartao ou 0.00 pix)"
            "\n- SE DATA != HOJE: scheduled_date ('DD/MM/YYYY') E is_scheduled=true"
            "\n- TODOS valores numericos devem ser numeros, nao textos"
            "\n- ⚠️ price no items = PRECO UNITARIO por UNIDADE (ex: Coxinha=0.80, NAO 20.00)"
            "\n  Produtos Fritos: 20,00/25 = R$ 0,80/un | Produtos Assados: 30,00/25 = R$ 1,20/un"
            "\n\nREGRAS DE HORARIO:"
            "\n- Use saudacao baseada no horario: 06-12=Bom dia, 12-18=Boa tarde, 18-06=Boa noite"
            "\n- Se hora atual > 20:00 ou < 07:40: avise que estamos fora do horario ANTES de qualquer coisa"
            "\n- Se domingo: avise que nao abrimos e ofereca agendamento para segunda"
            "\n- NAO inicie fluxo de pedidos se estiver fora de horario sem avisar primeiro"
            "\n- Se cliente pedir 'hoje' e for fora de horario: 'Nossa producao ja encerrou! Quer agendar para amanha?'"
        )

        # ═══ DADOS EXTRAIDOS - PRIORIDADE MAXIMA (DEPOIS de todas as regras) ═══
        if _extracted:
            _ext_lines = []
            if "name" in _extracted and not (contact and contact.get("name")):
                _ext_lines.append(f"Nome do cliente: {_extracted['name']}")
            if "items" in _extracted:
                _ext_lines.append("Itens ja definidos pelo cliente:")
                for _it in _extracted["items"]:
                    _ext_lines.append(f"  - {_it['quantity']} un - {_it['name']}")
            if "modality" in _extracted:
                _ext_lines.append(f"Modalidade: {_extracted['modality']} (JA CONFIRMADA pelo cliente)")
            if "endereco" in _extracted:
                _ext_lines.append(f"Endereco: {_extracted['endereco']}")
            if "bairro" in _extracted:
                _ext_lines.append(f"Bairro: {_extracted['bairro']}")
            if "horario" in _extracted:
                _ext_lines.append(f"Horario: {_extracted['horario']}")
            if "data_agendada" in _extracted:
                _ext_lines.append(f"Data: {_extracted['data_agendada']}")
            if "payment_method" in _extracted:
                _ext_lines.append(f"Pagamento: {_extracted['payment_method']}")
            if "referencia" in _extracted:
                _ext_lines.append(f"Referencia: {_extracted['referencia']}")
            if _ext_lines:
                dynamic_prompt += (
                    "\n\n🚨🚨🚨 PRIORIDADE MAXIMA - DADOS JA CONFIRMADOS PELO CLIENTE 🚨🚨🚨"
                    + "\n\n" + "\n".join(_ext_lines)
                    + "\n\nACIMA ESTAO DADOS QUE O CLIENTE JA INFORMOU NA MENSAGEM."
                    + "\nNAO PERGUNTE NENHUM DOS DADOS LISTADOS ACIMA."
                    + "\nNAO DIGA 'sera para entrega ou retirada?' SE A MODALIDADE JA FOI LISTADA."
                    + "\nNAO DIGA 'qual o endereco?' SE O ENDERECO JA FOI LISTADO."
                    + "\nNAO DIGA 'qual horario?' SE O HORARIO JA FOI LISTADO."
                    + "\nNAO DIGA 'qual forma de pagamento?' SE O PAGAMENTO JA FOI LISTADO."
                    + "\nVA DIRETO PARA O PROXIMO PASSO COM OS DADOS ACIMA."
                )
                print(f"[flush] DADOS PRIORITARIOS injetados: {_extracted_summary}")

        # Verifica se a IA transferiu durante a chamada (via tool)
        _transfer_done = False

        def _transfer_handler(reason: str = "", **kwargs) -> str:
            nonlocal _transfer_done
            performed = perform_transfer(user, reason, send_message=False)
            _transfer_done = True
            if performed:
                return "Transferência realizada com sucesso. O atendente humano continuará o atendimento."
            return "O cliente já está sendo atendido por um humano."

        def _contextual_knowledge_handler(query: str = "", filename: str = "", **kwargs) -> str:
            if not filename and query and len(query.split()) <= 3 and last_assistant_text:
                formats = ["porção", "porcao", "25", "festa", "individual", "unidade", "combo"]
                found_formats = [f for f in formats if f in last_assistant_text.lower()]
                if found_formats:
                    adds = [f for f in found_formats if f not in query.lower()]
                    if adds:
                        expanded_query = f"{query} {' '.join(adds)}"
                        print(f"[rag] Query expandida por contexto: de '{query}' para '{expanded_query}'")
                        return _knowledge_tool_handler(query=expanded_query, filename=filename)
            return _knowledge_tool_handler(query=query, filename=filename)

        try:
            reply = llm.generate_reply(
                history=memory.get(user),
                system=dynamic_prompt,
                tools=[KNOWLEDGE_TOOL, TRANSFER_TOOL, CALCULATE_ORDER_TOOL, SEND_ORDER_TOOL],
                tool_handlers={
                    "search_knowledge": _contextual_knowledge_handler,
                    "transfer_to_human": _transfer_handler,
                    "calculate_order": _calculate_order_handler,
                    "send_order_to_cardapio": _send_order_handler,
                },
            )
        except Exception as e:
            print(f"[flush] ERRO llm p/ {user}: {e}")
            return

        # Se a IA transferiu, envia a resposta do LLM (knowledge/explicação) e depois a msg de transferência
        if _transfer_done:
            print(f"[flush] user={user} transferido pela IA, enviando resposta do LLM antes da transferência")
            # Envia a resposta do LLM (pode conter info do knowledge)
            if reply:
                memory.append(user, "assistant", reply)
                chunks = split_reply(reply)
                for chunk in chunks:
                    time.sleep(1 + len(chunk) / 200)
                    send_text(user, chunk)
                    send_msg_out_to_panel(user, chunk)
            # Agora envia a mensagem de transferência
            transfer_msg = database.get_setting("transfer_message", "") or _DEFAULT_TRANSFER_MESSAGE
            time.sleep(1)
            send_text(user, transfer_msg)
            try:
                conv = database.get_or_create_conversation(user)
                database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            except Exception:
                pass
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
            info = get_webhook_media_url(wa_media_id)
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
            url = fresh["media_url"]
            if url.startswith("data:") and len(url) > 500:
                return url
        wa_media_id = str((fresh or {}).get("wa_media_id") or "")
        if not wa_media_id:
            return ""
        info = get_webhook_media_url(wa_media_id)
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

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per minute"])

buffer = MessageBuffer(wait_seconds=BUFFER_SECONDS, on_flush=handle_flush)

database.init_db()

# Carrega configurações do cardápio salvas no banco para os.environ
for _key in ("CARDÁPIO_TEST_MODE", "CARDÁPIO_API_URL", "CARDÁPIO_API_URL_TEST", "BALBS_AGENTE_API_KEY"):
    _val = database.get_setting(_key, "")
    if _val:
        os.environ[_key] = _val

# Carrega config de sub-agentes do banco (se disponivel)
if os.getenv("USE_SUB_AGENTS", "").lower() not in ("true", "false"):
    _sub_agents_db = database.get_setting("use_sub_agents", "false")
    USE_SUB_AGENTS = _sub_agents_db.lower() == "true"

print(f"[config] Sub-agentes: {'ATIVADOS' if USE_SUB_AGENTS else 'DESATIVADOS'}")

# Inicia o scheduler de follow-ups CRM
try:
    crm_scheduler.start_scheduler()
except Exception as e:
    print(f"[crm] WARN: scheduler nao iniciado: {e}")

print(f"[config] Datafy API: PHONE_ID={os.getenv('DATAFY_PHONE_NUMBER_ID', '') or 'N/A'}")
print(f"[config] Datafy API: WABA_ID={os.getenv('DATAFY_WABA_ID', '') or 'N/A'}")

# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/login")
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("panel_page"))
    return render_template("login.html")


@app.post("/login")
@limiter.limit("5 per minute")
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


# ── Security Headers ─────────────────────────────────────────────────────────

@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


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
                wa_media_id=wa_media_id,
            )
            if saved:
                database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
                database.increment_unread(conv["id"])
                # CRM: atualizar última interação e cancelar/recriar follow-ups
                database.update_contact(contact["id"], last_interaction_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
                database.cancel_follow_ups_for_contact(contact["id"])
                try:
                    database.create_follow_ups_for_contact(contact["id"])
                except Exception as e:
                    print(f"[crm] WARN: falha ao recriar follow-ups: {e}")
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
    limit = min(int(request.args.get("limit", "100")), 500)
    offset = max(int(request.args.get("offset", "0")), 0)
    arch = request.args.get("archived", "")
    archived = None if arch == "" else (arch == "1" or arch.lower() == "true")
    if status == "archived":
        status = ""
        archived = True
    # Filtro por modo: auto_mode=1 (bot) ou auto_mode=0 (fila humana)
    auto_mode_param = request.args.get("auto_mode", "")
    auto_mode = int(auto_mode_param) if auto_mode_param in ("0", "1") else None
    convs = database.list_conversations(status=status, archived=archived, auto_mode=auto_mode, limit=limit, offset=offset)
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
            # Busca a ultima mensagem RECEBIDA para mark_read na Meta
            # (nao usa a ultima mensagem da conversa, que pode ser enviada pelo agente)
            import db as _db
            msgs = _db.fetch_all(
                "SELECT * FROM messages WHERE conversation_id=? AND direction='in' ORDER BY created_at DESC LIMIT 1",
                (conv_id,),
            )
            if msgs:
                wa_mid = msgs[0].get("wa_message_id", "")
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

    # Otimizacao: queries bulk sem validacao individual
    if action in ("read", "unread") and ids:
        if action == "read":
            database.reset_unread_bulk(ids)
        else:
            database.mark_unread_bulk(ids)
        events.emit("conversation_updated", {"conversation_ids": ids, "unread_count": 0 if action == "read" else 1})
        return jsonify({"ok": True, "processed": len(ids), "errors": []})

    if action in ("archive", "mute", "pin", "favorite") and ids:
        field_map = {"archive": "archived", "mute": "muted", "pin": "pinned", "favorite": "favorite"}
        field = field_map[action]
        value = 1 if params.get(field, True) else 0
        now = datetime.utcnow().isoformat()
        placeholders = ",".join("?" * len(ids))
        import db
        db.execute(
            f"UPDATE conversations SET {field}=?, updated_at=? WHERE id IN ({placeholders})",
            (value, now, *ids),
        )
        events.emit("conversation_updated", {"conversation_ids": ids, field: value})
        return jsonify({"ok": True, "processed": len(ids), "errors": []})

    if action == "status" and ids:
        status = params.get("status", "")
        if status not in ("new", "open", "waiting", "resolved", "closed"):
            return jsonify({"error": "invalid status"}), 400
        now = datetime.utcnow().isoformat()
        placeholders = ",".join("?" * len(ids))
        import db
        db.execute(
            f"UPDATE conversations SET status=?, updated_at=? WHERE id IN ({placeholders})",
            (status, now, *ids),
        )
        events.emit("conversation_updated", {"conversation_ids": ids, "status": status})
        return jsonify({"ok": True, "processed": len(ids), "errors": []})

    if action == "clear" and ids:
        for cid in ids:
            try:
                database.clear_conversation(cid)
            except Exception:
                pass
        events.emit("conversation_updated", {"conversation_ids": ids, "cleared": True})
        return jsonify({"ok": True, "processed": len(ids), "errors": []})

    if action == "delete" and ids:
        for cid in ids:
            try:
                database.delete_conversation(cid)
            except Exception:
                pass
        events.emit("conversation_updated", {"conversation_ids": ids, "deleted": True})
        return jsonify({"ok": True, "processed": len(ids), "errors": []})

    if action == "add_tag" and ids:
        tag_id = params.get("tag_id")
        if not tag_id:
            return jsonify({"error": "tag_id required"}), 400
        for cid in ids:
            try:
                database.add_tag_to_conversation(cid, tag_id)
            except Exception:
                pass
        events.emit("tags_updated", {"conversation_ids": ids, "tag_id": tag_id, "action": "add"})
        return jsonify({"ok": True, "processed": len(ids), "errors": []})

    if action == "remove_tag" and ids:
        tag_id = params.get("tag_id")
        if not tag_id:
            return jsonify({"error": "tag_id required"}), 400
        for cid in ids:
            try:
                import db
                db.execute("DELETE FROM conversation_tags WHERE conversation_id=? AND tag_id=?", (cid, tag_id))
            except Exception:
                pass
        events.emit("tags_updated", {"conversation_ids": ids, "tag_id": tag_id, "action": "remove"})
        return jsonify({"ok": True, "processed": len(ids), "errors": []})

    # Fallback: mode (precisa de loop por contact individual)
    if action == "mode" and ids:
        now = datetime.utcnow().isoformat()
        placeholders = ",".join("?" * len(ids))
        import db
        rows = db.fetch_all(
            f"SELECT c.id as conv_id, ct.id as contact_id, ct.auto_mode FROM conversations c "
            f"JOIN contacts ct ON c.contact_id=ct.id WHERE c.id IN ({placeholders})",
            tuple(ids),
        )
        for row in rows:
            new_mode = 0 if row["auto_mode"] else 1
            database.update_contact(row["contact_id"], auto_mode=new_mode)
            events.emit("conversation_updated", {"conversation_id": row["conv_id"], "auto_mode": new_mode})
            processed += 1
        return jsonify({"ok": True, "processed": processed, "errors": errors})

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


_DEFAULT_TRANSFER_MESSAGE = os.getenv(
    "TRANSFER_MESSAGE",
    "Estou transferindo você para um atendente humano. Aguarde um momento, logo alguém irá te atender. 😊"
)


@app.get("/api/settings/transfer-message")
@login_required
def api_get_transfer_message():
    msg = database.get_setting("transfer_message", "") or _DEFAULT_TRANSFER_MESSAGE
    return jsonify({"transfer_message": msg})


@app.put("/api/settings/transfer-message")
@login_required
def api_set_transfer_message():
    data = request.get_json(silent=True) or {}
    msg = data.get("transfer_message", "").strip()
    if not msg:
        return jsonify({"error": "transfer_message não pode ser vazio"}), 400
    database.set_setting("transfer_message", msg)
    return jsonify({"ok": True, "transfer_message": msg})


# ── Sub-agentes ──────────────────────────────────────────────────────────────

@app.get("/api/settings/sub-agents")
@login_required
def api_get_sub_agents():
    """Retorna status dos sub-agentes."""
    return jsonify({
        "enabled": USE_SUB_AGENTS,
        "database_setting": database.get_setting("use_sub_agents", "false"),
    })


@app.put("/api/settings/sub-agents")
@login_required
def api_set_sub_agents():
    """Ativa/desativa sub-agentes."""
    global USE_SUB_AGENTS, _conversation_manager
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled", False)
    USE_SUB_AGENTS = enabled
    database.set_setting("use_sub_agents", str(enabled).lower())
    if not enabled:
        _conversation_manager = None
    return jsonify({"ok": True, "enabled": enabled})


@app.post("/api/conversations/<phone>/reset")
@login_required
def api_reset_conversation(phone: str):
    """Reseta estado da conversa de um usuario (sub-agentes)."""
    try:
        database.delete_conversation_state(phone)
        return jsonify({"ok": True, "message": f"Estado da conversa de {phone} resetado"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/conversations/<phone>/state")
@login_required
def api_get_conversation_state(phone: str):
    """Retorna estado da conversa de um usuario (sub-agentes)."""
    state = database.get_conversation_state(phone)
    if state:
        return jsonify({"phone": phone, "state": state})
    return jsonify({"phone": phone, "state": None})


@app.get("/api/conversations/human-queue/count")
@login_required
def api_human_queue_count():
    """Retorna a contagem de conversas não lidas em modo humano (auto_mode=0 e unread_count > 0)."""
    count = database.count_unread_human_conversations()
    return jsonify({"count": count})


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


@app.post("/api/messages/send-media")
@login_required
def api_messages_send_media():
    """Envia midia (imagem, video, audio, documento) via WhatsApp."""
    if "media" not in request.files:
        return jsonify({"error": "Arquivo de midia requerido"}), 400
    
    phone = request.form.get("phone", "").strip()
    media_type = request.form.get("type", "").strip()
    caption = request.form.get("caption", "").strip()
    
    if not phone:
        return jsonify({"error": "Phone requerido"}), 400
    
    if not media_type or media_type not in ["image", "video", "audio", "document"]:
        return jsonify({"error": "Tipo de midia invalido"}), 400
    
    f = request.files["media"]
    if not f.filename:
        return jsonify({"error": "Filename requerido"}), 400
    
    import mimetypes
    mime = mimetypes.guess_type(f.filename)[0] or "application/octet-stream"
    file_bytes = f.read()
    
    # Upload da midia para o servidor Meta
    from meta import upload_media_bytes
    upload_result = upload_media_bytes(file_bytes, mime, f.filename)
    
    if not upload_result.get("ok"):
        err = upload_result.get("error", "Erro ao fazer upload da midia")
        print(f"[api] ERRO upload media p/ {phone}: {err}")
        return jsonify({"error": err}), (upload_result.get("status") or 500)
    
    media_id = upload_result.get("data", {}).get("id", "")
    if not media_id:
        return jsonify({"error": "media_id nao retornado"}), 500
    
    # Envia a midia conforme o tipo
    from meta import send_image, send_video, send_audio, send_document
    
    if media_type == "image":
        res = send_image(phone, media_id, caption)
    elif media_type == "video":
        res = send_video(phone, media_id, caption)
    elif media_type == "audio":
        res = send_audio(phone, media_id)
    elif media_type == "document":
        res = send_document(phone, media_id, f.filename, caption)
    else:
        return jsonify({"error": "Tipo de midia nao suportado"}), 400
    
    if not res.get("ok"):
        err = res.get("error") or "Falha ao enviar midia."
        print(f"[api] ERRO ao enviar media p/ {phone}: {err}")
        return jsonify({"error": err}), (res.get("status") or 500)
    
    # Extrai wa_id da resposta Meta
    data_res = res.get("data") or {}
    contacts = data_res.get("contacts", [])
    wa_id = contacts[0].get("wa_id", phone) if contacts else phone
    messages_res = data_res.get("messages", [])
    meta_msg_id = messages_res[0].get("id", "") if messages_res else ""
    
    # Salva no banco
    contact = database.upsert_contact(wa_id)
    conv = database.get_or_create_conversation(wa_id)
    
    # Corpo da mensagem
    body = caption if caption else f"[{media_type.upper()}]"
    
    saved_msg = database.save_message(
        conversation_id=conv["id"],
        phone=wa_id,
        direction="out",
        body=body,
        sender="human",
        msg_type=media_type,
        wa_message_id=meta_msg_id,
        wa_media_id=media_id,
    )
    
    db_msg_id = saved_msg["id"] if saved_msg else ""
    
    database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    events.emit("message_sent", {"conversation_id": conv["id"], "phone": wa_id, "body": body})
    
    return jsonify({"ok": True, "phone": wa_id, "message_id": meta_msg_id, "media_type": media_type, "db_msg_id": db_msg_id})


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
    limit = min(int(request.args.get("limit", "100")), 500)
    offset = max(int(request.args.get("offset", "0")), 0)
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
    return jsonify(knowledge_rag.list_files_with_status())


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


@app.get("/api/knowledge/<filename>/xlsx-data")
@login_required
def api_knowledge_xlsx_data(filename):
    """Retorna dados estruturados de um arquivo .xlsx para visualizacao em tabela."""
    if not filename.endswith('.xlsx'):
        return jsonify({"error": "Arquivo nao e xlsx"}), 400
    
    file_data = knowledge_rag.get_file(filename)
    if not file_data:
        return jsonify({"error": "Arquivo nao encontrado"}), 404
    
    try:
        import openpyxl
        from pathlib import Path
        
        # Busca o arquivo original do storage
        uploads_dir = Path(__file__).parent / "uploads"
        xlsx_path = uploads_dir / filename
        
        if not xlsx_path.exists():
            xlsx_path = Path(__file__).parent / "static" / filename
        
        if not xlsx_path.exists():
            xlsx_path = Path(__file__).parent / filename
        
        if not xlsx_path.exists():
            content = file_data.get("content", "")
            return jsonify({
                "sheets": [{"name": "Dados", "data": content}],
                "filename": filename
            })
        
        wb = openpyxl.load_workbook(str(xlsx_path), read_only=True, data_only=True)
        sheets = []
        
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            
            # Le todas as linhas como listas
            all_rows = []
            for row in ws.iter_rows(values_only=True):
                vals = [str(cell) if cell is not None else "" for cell in row]
                all_rows.append(vals)
            
            if not all_rows:
                continue
            
            # Remove colunas totalmente vazias (conta colunas com pelo menos 1 valor)
            non_empty_cols = set()
            for row in all_rows:
                for ci, cell in enumerate(row):
                    if cell.strip():
                        non_empty_cols.add(ci)
            
            if not non_empty_cols:
                continue
            
            non_empty_cols = sorted(non_empty_cols)
            all_rows = [[row[ci] if ci < len(row) else "" for ci in non_empty_cols] for row in all_rows]
            
            # Remove linhas totalmente vazias
            all_rows = [r for r in all_rows if any(cell.strip() for cell in r)]
            
            if not all_rows:
                continue
            
            # Detecta linha de headers: procura a primeira linha com 2+ valores nao-vazios
            # que parecem ser nomes de coluna (nao numericos)
            header_idx = 0
            for idx, row in enumerate(all_rows):
                non_empty = [c.strip() for c in row if c.strip()]
                if len(non_empty) >= 2:
                    # Verifica se parece header (valores sao texto, nao numeros puros)
                    has_text = any(
                        not c.replace(".", "").replace(",", "").isdigit()
                        for c in non_empty
                    )
                    if has_text:
                        header_idx = idx
                        break
            
            headers = [c.strip() or f"Col_{i+1}" for i, c in enumerate(all_rows[header_idx])]
            data_rows = all_rows[header_idx + 1:]
            
            # Remove linhas totalmente vazias apos headers
            data_rows = [r for r in data_rows if any(cell.strip() for cell in r)]
            
            sheets.append({
                "name": sheet_name,
                "headers": headers,
                "rows": data_rows,
                "rowCount": len(data_rows),
                "colCount": len(headers)
            })
        
        wb.close()
        
        return jsonify({
            "sheets": sheets,
            "filename": filename,
            "totalSheets": len(sheets)
        })
        
    except Exception as e:
        print(f"[knowledge] ERRO ao processar xlsx: {e}")
        # Fallback: retorna o conteudo como texto
        content = file_data.get("content", "")
        return jsonify({
            "sheets": [{"name": "Dados", "data": content}],
            "filename": filename,
            "error": str(e)
        })


@app.put("/api/knowledge/<filename>/xlsx-data")
@login_required
def api_knowledge_xlsx_data_update(filename):
    """Salva dados editados de uma planilha .xlsx de volta ao arquivo."""
    if not filename.endswith('.xlsx'):
        return jsonify({"error": "Arquivo nao e xlsx"}), 400
    
    data = request.get_json(silent=True) or {}
    sheets = data.get("sheets", [])
    description = data.get("description", "").strip()
    
    if not sheets:
        return jsonify({"error": "Nenhum dado fornecido"}), 400
    
    try:
        import openpyxl
        from pathlib import Path
        
        # Busca o arquivo
        uploads_dir = Path(__file__).parent / "uploads"
        xlsx_path = uploads_dir / filename
        if not xlsx_path.exists():
            xlsx_path = Path(__file__).parent / "static" / filename
        if not xlsx_path.exists():
            xlsx_path = Path(__file__).parent / filename
        
        if not xlsx_path.exists():
            return jsonify({"error": "Arquivo xlsx nao encontrado no disco"}), 404
        
        # Abre o workbook existente
        wb = openpyxl.load_workbook(str(xlsx_path))
        
        for sheet_data in sheets:
            sheet_name = sheet_data.get("name", "")
            headers = sheet_data.get("headers", [])
            rows = sheet_data.get("rows", [])
            
            if sheet_name not in wb.sheetnames:
                continue
            
            ws = wb[sheet_name]
            
            # Encontra onde comecam os headers no worksheet original
            header_row_idx = None
            for row_idx in range(1, min(ws.max_row + 1, 20)):
                row_vals = [str(ws.cell(row=row_idx, column=ci + 1).value or "").strip() for ci in range(len(headers))]
                # Compara com os headers atuais
                if row_vals == headers or row_vals[:len(headers)] == headers:
                    header_row_idx = row_idx
                    break
            
            if header_row_idx is None:
                # Headers nao encontrados - escreve a partir da linha 1
                header_row_idx = 1
                # Escreve headers
                for ci, h in enumerate(headers):
                    ws.cell(row=1, column=ci + 1, value=h)
                header_row_idx = 2
            
            # Limpa dados antigos (mantem headers)
            max_data_row = ws.max_row
            for row_idx in range(header_row_idx, max_data_row + 1):
                for ci in range(len(headers)):
                    ws.cell(row=row_idx, column=ci + 1, value=None)
            
            # Escreve novos dados
            for ri, row in enumerate(rows):
                for ci, val in enumerate(row):
                    # Tenta manter tipo numerico quando possivel
                    cell_val = val
                    if val and isinstance(val, str):
                        try:
                            cell_val = float(val.replace(",", "."))
                        except (ValueError, AttributeError):
                            cell_val = val
                    ws.cell(row=header_row_idx + ri, column=ci + 1, value=cell_val)
        
        # Salva o arquivo
        wb.save(str(xlsx_path))
        wb.close()
        
        # Atualiza o conteudo de texto no knowledge base
        content_parts = []
        for sheet_data in sheets:
            sheet_name = sheet_data.get("name", "Dados")
            headers = sheet_data.get("headers", [])
            rows = sheet_data.get("rows", [])
            content_parts.append(f"=== {sheet_name} ===")
            content_parts.append("\t".join(headers))
            for row in rows:
                content_parts.append("\t".join(str(c) for c in row))
            content_parts.append("")
        
        full_content = "\n".join(content_parts)
        knowledge_rag.save_knowledge_raw(filename, full_content, description=description)
        
        print(f"[knowledge] planilha salva: {filename}")
        return jsonify({"ok": True})
        
    except Exception as e:
        print(f"[knowledge] ERRO ao salvar xlsx: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Erro ao salvar planilha: {str(e)}"}), 500


@app.post("/api/knowledge")
@login_required
def api_knowledge_create():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "").strip()
    content = data.get("content", "")
    description = data.get("description", "").strip()
    skip_embed = data.get("skip_embed", True)
    if not filename:
        return jsonify({"error": "filename obrigatorio"}), 400
    if not filename.endswith(('.md', '.txt', '.docx', '.xlsx')):
        return jsonify({"error": "Extensao invalida (.md, .txt, .docx ou .xlsx)"}), 400
    existing = knowledge_rag.get_file(filename)
    if existing:
        return jsonify({"error": "Arquivo ja existe"}), 409
    knowledge_rag.save_knowledge_raw(filename, content, description=description)
    print(f"[knowledge] criado: {filename}")
    return jsonify({"ok": True, "filename": filename})


@app.put("/api/knowledge/<filename>")
@login_required
def api_knowledge_update(filename):
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    description = data.get("description", "").strip()
    knowledge_rag.save_knowledge_raw(filename, content, description=description)

    # Salva de volta no arquivo local (.docx ou .txt/.md)
    if filename.endswith('.docx'):
        try:
            from docx import Document
            from pathlib import Path
            uploads_dir = Path(__file__).parent / "uploads"
            docx_path = uploads_dir / filename
            if not docx_path.exists():
                docx_path = Path(__file__).parent / "static" / filename
            if not docx_path.exists():
                docx_path = Path(__file__).parent / filename
            if docx_path.exists():
                doc = Document()
                for line in content.split('\n'):
                    doc.add_paragraph(line)
                doc.save(str(docx_path))
                print(f"[knowledge] arquivo local salvo: {docx_path}")
        except Exception as e:
            print(f"[knowledge] aviso: nao foi possivel salvar arquivo local: {e}")
    elif filename.endswith(('.txt', '.md')):
        try:
            from pathlib import Path
            # Para .md de prompts dos sub-agentes, salva na pasta prompts/
            prompts_dir = Path(__file__).parent / "prompts"
            agent_prompts = {"triagem.md", "cardapio.md", "pedido.md", "logistica.md", "pagamento.md", "crise.md"}
            if filename in agent_prompts:
                txt_path = prompts_dir / filename
                if not txt_path.exists():
                    txt_path = Path(__file__).parent / "uploads" / filename
            else:
                uploads_dir = Path(__file__).parent / "uploads"
                txt_path = uploads_dir / filename
                if not txt_path.exists():
                    txt_path = Path(__file__).parent / "static" / filename
                if not txt_path.exists():
                    txt_path = Path(__file__).parent / filename
            if txt_path.parent.exists():
                txt_path.write_text(content, encoding='utf-8')
                print(f"[knowledge] arquivo local salvo: {txt_path}")
                # Limpa cache do agente correspondente
                knowledge_rag.reload_agent_prompts()
        except Exception as e:
            print(f"[knowledge] aviso: nao foi possivel salvar arquivo local: {e}")

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


def _parse_docx_upload(file_storage) -> str:
    """Extrai texto de um arquivo .docx enviado via upload."""
    try:
        from docx import Document
        import io
        doc = Document(io.BytesIO(file_storage.read()))
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(lines)
    except Exception as e:
        print(f"[knowledge] ERRO parse docx: {e}")
        return ""


def _parse_xlsx_upload(file_storage) -> str:
    """Extrai texto de um arquivo .xlsx enviado via upload (todas as abas)."""
    try:
        import pandas as pd
        import io
        xls = pd.ExcelFile(io.BytesIO(file_storage.read()))
        partes = []
        for sheet in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet)
            df = df.dropna(how="all")
            df = df.dropna(axis=1, how="all")
            if df.empty:
                continue
            df.columns = [
                f"col{i}" if "Unnamed" in str(c) else str(c)
                for i, c in enumerate(df.columns)
            ]
            texto = df.to_string(index=False, max_colwidth=120)
            partes.append(f"=== {sheet} ===\n{texto}")
        return "\n\n".join(partes)
    except Exception as e:
        print(f"[knowledge] ERRO parse xlsx: {e}")
        return ""


@app.post("/api/knowledge/upload")
@login_required
def api_knowledge_upload():
    """Upload de múltiplos arquivos (multipart). Aceita .md, .txt, .docx, .xlsx."""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    saved = []
    errors = []
    for f in files:
        filename = f.filename or ""
        if not filename:
            continue
        lower = filename.lower()
        if not lower.endswith(('.md', '.txt', '.docx', '.xlsx')):
            errors.append({"filename": filename, "error": "Extensao invalida (.md, .txt, .docx ou .xlsx)"})
            continue
        existing = knowledge_rag.get_file(filename)
        if existing:
            errors.append({"filename": filename, "error": "Arquivo ja existe"})
            continue
        try:
            if lower.endswith('.docx'):
                content = _parse_docx_upload(f)
            elif lower.endswith('.xlsx'):
                content = _parse_xlsx_upload(f)
            else:
                content = f.read().decode("utf-8", errors="replace")
            if not content or not content.strip():
                errors.append({"filename": filename, "error": "Arquivo vazio ou nao parseado"})
                continue
            knowledge_rag.save_knowledge_raw(filename, content)
            saved.append(filename)
        except Exception as e:
            errors.append({"filename": filename, "error": str(e)})

    print(f"[knowledge] upload: {len(saved)} salvos, {len(errors)} erros")
    return jsonify({"ok": True, "saved": saved, "errors": errors})


@app.post("/api/knowledge/embed-all")
@login_required
def api_knowledge_embed_all():
    """Gera embeddings para todos os arquivos pendentes."""
    result = knowledge_rag.embed_pending_files()
    print(f"[knowledge] embed-all: {result}")
    return jsonify(result)


@app.post("/api/knowledge/clear-embeddings")
@login_required
def api_knowledge_clear_embeddings():
    """Remove todos os embeddings, mantém conteúdo, marca como pending."""
    count = knowledge_rag.clear_all_embeddings()
    return jsonify({"ok": True, "cleared": count})


@app.delete("/api/knowledge/all")
@login_required
def api_knowledge_delete_all():
    """Deleta todo o knowledge (chunks + metadados)."""
    count = knowledge_rag.delete_all()
    return jsonify({"ok": True, "deleted": count})


@app.post("/api/knowledge/reload")
@login_required
def api_knowledge_reload():
    """Recarrega o cache do system prompt (util apos upload de .docx/.xlsx)."""
    global FULL_SYSTEM_PROMPT
    parse_knowledge.reload_cache()
    _prompt_from_docx = parse_knowledge.get_prompt_cached()
    if _prompt_from_docx:
        FULL_SYSTEM_PROMPT = _prompt_from_docx + (
            "\n\n=== TOOLS DISPONIVEIS ==="
            "\nVoce tem acesso a uma tool 'search_knowledge' para buscar informacoes "
            "na base de conhecimento. Chame essa tool sempre que precisar consultar "
            "produtos, precos, politicas ou qualquer informacao da empresa."
            "\nVoce tem acesso a uma tool 'transfer_to_human' para transferir o atendimento a um humano. "
            "USE essa tool quando: o cliente pedir explicitamente para falar com um atendente ou humano; "
            "a situacao envolver reclamacao grave, cancelamento, reembolso ou devolucao; "
            "voce nao souber responder adequadamente; "
            "a solicitacao estiver fora do seu escopo; "
            "ou quando perceber que o cliente esta frustrado ou insatisfeito. "
            "Ao transferir, informe brevemente o motivo no parametro 'reason'."
            "\nVoce tem acesso a uma tool 'calculate_order' para validar pedidos no cardapio. "
            "Chame essa tool ANTES de confirmar o pedido com o cliente, informando os itens e o bairro de entrega. "
            "Ela valida produtos, quantidades, calcula subtotal, frete e total."
            "\nVoce tem acesso a uma tool 'send_order_to_cardapio' para enviar pedidos confirmados para o cardapio digital. "
            "USE essa tool SOMENTE apos o cliente ter confirmado o pedido completo e voce ter validado com calculate_order. "
            "Nunca envie sem confirmacao explicita do cliente."
        )
    print(f"[knowledge] system prompt recarregado: {len(FULL_SYSTEM_PROMPT)} chars")
    return jsonify({"ok": True, "prompt_chars": len(FULL_SYSTEM_PROMPT)})


@app.get("/api/knowledge/prompts")
@login_required
def api_knowledge_prompts():
    """Lista os arquivos .md dos sub-agentes (prompts)."""
    prompts_dir = Path(__file__).parent / "prompts"
    prompts = []
    agent_map = {
        "triagem.md": "triagem",
        "cardapio.md": "cardapio",
        "pedido.md": "pedido",
        "logistica.md": "logistica",
        "pagamento.md": "pagamento",
        "crise.md": "crise",
    }
    for filename, agent in agent_map.items():
        fpath = prompts_dir / filename
        # Tenta ler do banco primeiro
        content = knowledge_rag.get_file_content(filename)
        source = "db" if content else "disk"
        if not content and fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            source = "disk"
        prompts.append({
            "filename": filename,
            "agent": agent,
            "source": source,
            "chars": len(content) if content else 0,
            "exists": bool(content),
        })
    return jsonify(prompts)


@app.post("/api/knowledge/reload-prompts")
@login_required
def api_knowledge_reload_prompts():
    """Recarrega cache de prompts dos sub-agentes."""
    knowledge_rag.reload_agent_prompts()
    return jsonify({"ok": True})


@app.get("/api/knowledge/prompt/<filename>")
@login_required
def api_knowledge_prompt_get(filename):
    """Le conteudo de um .md de prompt da pasta prompts/."""
    agent_prompts = {"triagem.md", "cardapio.md", "pedido.md", "logistica.md", "pagamento.md", "crise.md"}
    if filename not in agent_prompts:
        return jsonify({"error": "Prompt invalido"}), 400
    # Tenta do banco primeiro
    content = knowledge_rag.get_file_content(filename)
    source = "db"
    if not content:
        # Fallback: disco
        prompts_dir = Path(__file__).parent / "prompts"
        fpath = prompts_dir / filename
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            source = "disk"
        else:
            return jsonify({"error": "Prompt nao encontrado"}), 404
    return jsonify({"content": content, "source": source, "filename": filename})


@app.put("/api/knowledge/prompt/<filename>")
@login_required
def api_knowledge_prompt_put(filename):
    """Salva conteudo de um .md de prompt no banco + disco."""
    agent_prompts = {"triagem.md", "cardapio.md", "pedido.md", "logistica.md", "pagamento.md", "crise.md"}
    if filename not in agent_prompts:
        return jsonify({"error": "Prompt invalido"}), 400
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    # Salva no banco
    knowledge_rag.save_knowledge_raw(filename, content, description=f"Prompt do agente {filename.replace('.md','')}")
    # Salva no disco (pasta prompts/)
    prompts_dir = Path(__file__).parent / "prompts"
    fpath = prompts_dir / filename
    try:
        fpath.write_text(content, encoding="utf-8")
        print(f"[knowledge] prompt salvo em disco: {fpath}")
    except Exception as e:
        print(f"[knowledge] aviso: nao foi possivel salvar prompt em disco: {e}")
    # Limpa cache dos agentes
    knowledge_rag.reload_agent_prompts()
    return jsonify({"ok": True, "filename": filename})


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


@app.post("/api/whatsapp/templates")
@login_required
def api_whatsapp_template_create():
    """Cria um template na WABA."""
    problem = meta.config_problem()
    if problem:
        return jsonify({"error": problem}), 400
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    category = data.get("category", "UTILITY").strip()
    language = data.get("language", "pt_BR").strip()
    body = data.get("body", "").strip()
    header_text = data.get("header_text", "").strip()
    if not name or not body:
        return jsonify({"error": "name e body sao obrigatorios"}), 400
    r = create_template(name, category, language, body, header_text=header_text)
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Erro ao criar template")}), 500
    return jsonify({"ok": True, "data": r.get("data", {})})


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
    problem = meta.config_problem()
    if problem:
        return jsonify({"ok": False, "error": problem})

    r = meta.get_phone_info()
    if not r.get("ok"):
        return jsonify({
            "ok": False,
            "error": r.get("error", "Erro ao conectar com Datafy"),
            "phone_number_id": get_phone_id(),
            "waba_id": get_waba_id(),
        })

    d = r.get("data", {})
    return jsonify({
        "ok": True,
        "phone_number_id": get_phone_id(),
        "waba_id": get_waba_id(),
        "display_name": d.get("verified_name", ""),
        "display_phone": d.get("display_phone_number", ""),
        "quality_rating": d.get("quality_rating", ""),
        "name_status": d.get("name_status", ""),
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


# ── CRM API ─────────────────────────────────────────────────────────────────

@app.get("/api/crm/dashboard")
@login_required
def api_crm_dashboard():
    return jsonify(database.get_crm_dashboard_stats())


@app.get("/api/crm/contacts")
@login_required
def api_crm_contacts_list():
    status = request.args.get("status", "")
    tag_id = int(request.args.get("tag_id", "0"))
    days_without = int(request.args.get("days_without", "0"))
    has_follow_up = request.args.get("has_follow_up", "")
    search = request.args.get("search", "")
    limit = min(int(request.args.get("limit", "100")), 500)
    offset = max(int(request.args.get("offset", "0")), 0)
    contacts = database.list_contacts_crm(
        status=status, tag_id=tag_id, days_without=days_without,
        has_follow_up=has_follow_up, search=search, limit=limit, offset=offset,
    )
    return jsonify(contacts)


@app.get("/api/crm/contacts/<int:contact_id>")
@login_required
def api_crm_contact_detail(contact_id):
    detail = database.get_crm_contact_detail(contact_id)
    if not detail:
        return jsonify({"error": "not found"}), 404
    return jsonify(detail)


@app.put("/api/crm/contacts/<int:contact_id>/status")
@login_required
def api_crm_contact_update_status(contact_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status", "")
    if status not in ("novo", "em_atendimento", "cliente", "inativo", "nao_interessado", ""):
        return jsonify({"error": "invalid status"}), 400
    database.update_contact(contact_id, crm_status=status)
    return jsonify({"ok": True})


@app.put("/api/crm/contacts/<int:contact_id>/notes")
@login_required
def api_crm_contact_update_notes(contact_id):
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "")
    database.update_contact(contact_id, notes=notes)
    return jsonify({"ok": True})


@app.get("/api/crm/automations")
@login_required
def api_crm_automations_list():
    return jsonify(database.list_automations())


@app.post("/api/crm/automations")
@login_required
def api_crm_automations_create():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    auto = database.create_automation(
        name=name,
        days=int(data.get("days", 0)),
        hours=int(data.get("hours", 0)),
        message_template=data.get("message_template", ""),
        use_meta_template=int(data.get("use_meta_template", 0)),
        meta_template_name=data.get("meta_template_name", ""),
        meta_template_language=data.get("meta_template_language", "pt_BR"),
        action_tag_add=data.get("action_tag_add", ""),
        action_tag_remove=data.get("action_tag_remove", ""),
        action_status_change=data.get("action_status_change", ""),
        is_active=int(data.get("is_active", 1)),
        send_window_start=data.get("send_window_start", "08:00"),
        send_window_end=data.get("send_window_end", "18:00"),
    )
    return jsonify(auto)


@app.put("/api/crm/automations/<int:auto_id>")
@login_required
def api_crm_automations_update(auto_id):
    data = request.get_json(silent=True) or {}
    database.update_automation(auto_id, **data)
    return jsonify({"ok": True})


@app.delete("/api/crm/automations/<int:auto_id>")
@login_required
def api_crm_automations_delete(auto_id):
    database.delete_automation(auto_id)
    return jsonify({"ok": True})


@app.put("/api/crm/automations/<int:auto_id>/toggle")
@login_required
def api_crm_automations_toggle(auto_id):
    auto = database.get_automation(auto_id)
    if not auto:
        return jsonify({"error": "not found"}), 404
    new_val = 0 if auto["is_active"] else 1
    database.update_automation(auto_id, is_active=new_val)
    return jsonify({"ok": True, "is_active": new_val})


@app.get("/api/crm/follow-ups")
@login_required
def api_crm_follow_ups_list():
    status = request.args.get("status", "")
    limit = min(int(request.args.get("limit", "100")), 500)
    offset = max(int(request.args.get("offset", "0")), 0)
    return jsonify(database.list_follow_ups(status=status, limit=limit, offset=offset))


@app.get("/api/crm/follow-ups/pending")
@login_required
def api_crm_follow_ups_pending():
    return jsonify(database.get_pending_follow_ups())


@app.get("/api/crm/logs")
@login_required
def api_crm_logs_list():
    limit = min(int(request.args.get("limit", "100")), 500)
    offset = max(int(request.args.get("offset", "0")), 0)
    return jsonify(database.list_crm_logs(limit=limit, offset=offset))


# ── Cardápio: API Routes ───────────────────────────────────────────────────

@app.get("/api/cardapio/orders")
@login_required
def api_cardapio_orders_list():
    limit = min(int(request.args.get("limit", "50")), 500)
    offset = max(int(request.args.get("offset", "0")), 0)
    test = request.args.get("test", "")
    test_only = int(test) if test != "" else None
    orders = database.listar_cardapio_orders(limit=limit, offset=offset, test_only=test_only)
    # Desserializa items JSON para objetos Python
    for o in orders:
        if isinstance(o.get("items"), str):
            try:
                o["items"] = json.loads(o["items"])
            except Exception:
                o["items"] = []
        if isinstance(o.get("api_response"), str):
            try:
                o["api_response"] = json.loads(o["api_response"])
            except Exception:
                o["api_response"] = {}
    return jsonify(orders)


@app.get("/api/cardapio/orders/<int:order_id>")
@login_required
def api_cardapio_order_detail(order_id):
    order = database.obter_cardapio_order(order_id)
    if not order:
        return jsonify({"error": "Pedido nao encontrado"}), 404
    if isinstance(order.get("items"), str):
        try:
            order["items"] = json.loads(order["items"])
        except Exception:
            order["items"] = []
    if isinstance(order.get("api_response"), str):
        try:
            order["api_response"] = json.loads(order["api_response"])
        except Exception:
            order["api_response"] = {}
    return jsonify(order)


@app.delete("/api/cardapio/orders/<int:order_id>")
@login_required
def api_cardapio_order_delete(order_id):
    ok = database.deletar_cardapio_order(order_id)
    if not ok:
        return jsonify({"error": "Pedido nao encontrado"}), 404
    return jsonify({"ok": True})


@app.get("/api/cardapio/stats")
@login_required
def api_cardapio_stats():
    return jsonify(database.cardapio_orders_stats())


@app.get("/api/cardapio/settings")
@login_required
def api_cardapio_settings_get():
    api_key = database.get_setting("BALBS_AGENTE_API_KEY", os.getenv("BALBS_AGENTE_API_KEY", ""))
    masked_key = (api_key[:4] + "****" + api_key[-4:]) if len(api_key) > 8 else "****"
    return jsonify({
        "cardapio_test_mode": database.get_setting("CARDÁPIO_TEST_MODE", os.getenv("CARDÁPIO_TEST_MODE", "true")),
        "cardapio_api_url": database.get_setting("CARDÁPIO_API_URL", os.getenv("CARDÁPIO_API_URL", "")),
        "cardapio_api_url_test": database.get_setting("CARDÁPIO_API_URL_TEST", os.getenv("CARDÁPIO_API_URL_TEST", "")),
        "balbs_agente_api_key_masked": masked_key,
    })


@app.put("/api/cardapio/settings")
@login_required
def api_cardapio_settings_save():
    data = request.get_json(silent=True) or {}
    if "cardapio_test_mode" in data:
        val = str(data["cardapio_test_mode"]).lower()
        database.set_setting("CARDÁPIO_TEST_MODE", val)
        os.environ["CARDÁPIO_TEST_MODE"] = val
    if "cardapio_api_url" in data:
        database.set_setting("CARDÁPIO_API_URL", data["cardapio_api_url"])
        os.environ["CARDÁPIO_API_URL"] = data["cardapio_api_url"]
    if "cardapio_api_url_test" in data:
        database.set_setting("CARDÁPIO_API_URL_TEST", data["cardapio_api_url_test"])
        os.environ["CARDÁPIO_API_URL_TEST"] = data["cardapio_api_url_test"]
    if "balbs_agente_api_key" in data and data["balbs_agente_api_key"]:
        database.set_setting("BALBS_AGENTE_API_KEY", data["balbs_agente_api_key"])
        os.environ["BALBS_AGENTE_API_KEY"] = data["balbs_agente_api_key"]
    return jsonify({"ok": True})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
