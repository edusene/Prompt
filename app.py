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
import re as _re_mod
from datetime import datetime, timedelta, date
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
import horario
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
# Janela de debounce: tempo de silencio do cliente antes de o agente responder.
# Era 8s e sozinha ja comia quase metade do tempo de resposta — com o modelo e
# as tools, a Sandrinha levava ~21s para responder. O alvo do dono e a primeira
# mensagem chegar entre 5 e 10 segundos.
BUFFER_SECONDS = float(os.getenv("BUFFER_SECONDS", "3"))
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
    "\nVoce tem acesso a uma tool 'consultar_frete' que retorna a taxa OFICIAL de "
    "entrega de um bairro. Use SEMPRE que precisar informar frete: ela le a tabela "
    "oficial. Se ela responder que o bairro nao esta na tabela, NAO invente um valor "
    "- pergunte o bairro de novo ou transfira para um humano."
    "\nVoce tem acesso a uma tool 'consultar_preco' que retorna o preco OFICIAL de um "
    "produto do cardapio. Use SEMPRE antes de informar qualquer preco."
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

# O conteudo do DOCX pode mudar pelo painel. A base e reavaliada a cada
# atendimento, mas continua usando o cache de parse enquanto ele for valido.
_TOOLS_PROMPT = FULL_SYSTEM_PROMPT[len(_PROMPT_FROM_DOCX or _BASE_PROMPT):]


def _get_current_system_prompt() -> str:
    base = (parse_knowledge.get_prompt_cached() or _BASE_PROMPT) + _TOOLS_PROMPT
    return base + _bloco_precos_oficiais()


def _bloco_precos_oficiais() -> str:
    """Tabela de precos lida da planilha, colada no fim do system prompt.

    Ate 23/09 os precos viviam em dois lugares: a planilha (que a tool le) e o
    texto do cardapio.md (que o RAG devolve). Quando um mudava e o outro nao, o
    modelo tinha duas verdades e escolhia uma. O dono pediu uma fonte so, e a
    planilha e quem manda - e tambem quem sabe o que esta esgotado.
    """
    try:
        tabela = facts.render_tabela_precos()
    except Exception as e:
        print(f"[precos] WARN tabela oficial indisponivel: {e}")
        return ""
    return f"\n\n=== {tabela}" if tabela else ""

# ── Knowledge Tool (busca seletiva via function calling) ──────────────────────
import knowledge_rag
import facts
import conversation_memory

# ── Tools deterministicas (frete e preco vem da tabela, nao do texto) ─────────

CONSULTAR_FRETE_TOOL = {
    "type": "function",
    "function": {
        "name": "consultar_frete",
        "description": (
            "Retorna a taxa de entrega OFICIAL de um bairro de Porto Velho, lida "
            "diretamente da tabela da empresa. Use SEMPRE antes de informar frete ou "
            "montar o total de uma entrega. Se o bairro nao existir na tabela, a tool "
            "avisa - nesse caso NUNCA estime um valor."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "bairro": {
                    "type": "string",
                    "description": "Nome do bairro informado pelo cliente (aceita apelidos e erros de digitacao)",
                },
            },
            "required": ["bairro"],
        },
    },
}

CONSULTAR_PRECO_TOOL = {
    "type": "function",
    "function": {
        "name": "consultar_preco",
        "description": (
            "Retorna o preco OFICIAL de itens do cardapio (porcao, meio cento, cento, "
            "salgados grandes, combos, bolos, bebidas). Use SEMPRE antes de informar "
            "qualquer preco ao cliente. Se o item nao aparecer, ele nao esta no cardapio. "
            "O retorno traz 'forma_de_venda' de cada item: respeite-a - item vendido por "
            "UNIDADE nunca vira porcao de 25."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "produto": {
                    "type": "string",
                    "description": (
                        "Nome do produto exatamente como o cliente falou (ex: 'quiche de frango', "
                        "'coxinha'). NAO troque pelo nome da categoria que voce supoe - consulte "
                        "o produto citado e leia a categoria que a tool devolve."
                    ),
                },
                "formato": {
                    "type": "string",
                    "description": "Formato desejado, se houver (ex: 'porcao', 'meio cento', 'cento', 'unidade')",
                },
            },
            "required": ["produto"],
        },
    },
}


def _consultar_frete_handler(bairro: str = "", **kwargs) -> str:
    resultado = facts.lookup_frete(bairro)
    if not resultado:
        disponiveis = ", ".join(facts.bairro_names())
        print(f"[facts] consultar_frete bairro='{bairro}' -> NAO ENCONTRADO")
        return json.dumps({
            "encontrado": False,
            "bairro_consultado": bairro,
            "aviso": (
                "Bairro nao esta na tabela oficial de entrega. NAO invente uma taxa. "
                "Confirme o bairro com o cliente ou transfira para um atendente."
            ),
            "bairros_atendidos": disponiveis,
        }, ensure_ascii=False)

    print(f"[facts] consultar_frete bairro='{bairro}' -> {resultado['bairro']} R$ {resultado['taxa']:.2f} ({resultado['match']})")
    return json.dumps({
        "encontrado": True,
        "bairro_oficial": resultado["bairro"],
        "taxa_entrega": resultado["taxa"],
        "tipo_de_correspondencia": resultado["match"],
    }, ensure_ascii=False)


def _consultar_preco_handler(produto: str = "", formato: str = "", **kwargs) -> str:
    itens = facts.lookup_preco(produto, formato)
    if not itens:
        print(f"[facts] consultar_preco produto='{produto}' -> NAO ENCONTRADO")
        return json.dumps({
            "encontrado": False,
            "produto_consultado": produto,
            "aviso": (
                "Produto nao encontrado no cardapio oficial. NAO invente preco nem "
                "disponibilidade: ofereca outro item do cardapio."
            ),
        }, ensure_ascii=False)

    print(f"[facts] consultar_preco produto='{produto}' -> {len(itens)} itens")

    def _forma_de_venda(item: dict) -> str:
        formato = facts.normalize(item["formato"])
        if item["preco"] is None:
            return "sabor incluso no pacote da categoria (o preco esta na linha do formato)"
        if "unidade" in formato:
            return "vendido por UNIDADE - nao existe porcao de 25 deste item"
        return f"vendido no formato '{item['formato']}'"

    return json.dumps({
        "encontrado": True,
        "itens": [
            {
                "categoria": i["categoria"],
                "item": i["item"],
                "formato": i["formato"],
                "preco": i["preco"] if i["preco"] is not None else i["preco_texto"],
                "forma_de_venda": _forma_de_venda(i),
                "disponivel": i["disponivel"],
            }
            for i in itens
        ],
    }, ensure_ascii=False)

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
                    "description": (
                        "Forma de pagamento: 'pix', 'cartao', 'dinheiro'. "
                        "'cartao' basta - NAO pergunte ao cliente se e debito "
                        "ou credito. 'cartao_debito' e 'cartao_credito' so se o "
                        "proprio cliente tiver dito qual."
                    ),
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

# Prefixo do retorno de send_order_to_cardapio quando a API recusa o pedido.
# Existe porque o modelo nao e confiavel aqui: na conversa de 19/09 a API
# devolveu 401, o handler avisou do erro, e a Sandrinha respondeu "Pedido
# agendado! Muito obrigado por escolher a BALBS!" — o cliente foi embora
# achando que tinha pedido e a fabrica nunca viu a comanda. Hoje quem decide o
# que o cliente le nesse caso e o codigo, nao o modelo.
FALHA_NO_ENVIO = "FALHA_NO_ENVIO"

MENSAGEM_PEDIDO_NAO_ENTROU = (
    "Consegui montar seu pedido certinho, mas não consegui registrar aqui no "
    "sistema agora. Já estou chamando nossa equipe para confirmar com você e "
    "garantir que nada se perca. Um instante! 💛"
)

def _calculate_order_handler(items: list[dict], bairro: str = "", modalidade: str = "",
                             dia: str = "", horario: str = "", pagamento: str = "",
                             cliente: str = "", endereco: str = "", troco: str = "",
                             referencia: str = "", data_iso: str = "", **kwargs) -> str:
    """Handler para a tool calculate_order.

    Os campos precisam estar TODOS na assinatura: o que cai em **kwargs e
    descartado em silencio. Foi o que aconteceu com troco, referencia e
    data_iso — o resumo_formatado saia sem eles, o modelo escrevia o espelho a
    mao e a data voltava por extenso.
    """
    print(f"[cardápio] TOOL calculate_order items={items} bairro={bairro}")
    result = cardapio.calcular_pedido(
        items, bairro, modalidade=modalidade, dia=dia, horario=horario,
        pagamento=pagamento, cliente=cliente, endereco=endereco,
        troco=troco, referencia=referencia, data_iso=data_iso,
    )
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
    # So para o NOSSO registro: o contrato do cardapio nao tem campo de hora,
    # mas a aba Cardapio precisa dela para o dono planejar a producao.
    scheduled_time: str = "",
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
                # A taxa de retorno SOMA. Estava subtraindo: enquanto ela era
                # sempre zero nao aparecia, mas com a taxa real um pedido de
                # R$ 74,00 era registrado no cardapio como R$ 70,00.
                total = float(subtotal) + float(delivery_fee) + float(return_fee)
        except (ValueError, TypeError):
            pass
    print(f"[cardápio] TOOL send_order customer={customer_name} phone={customer_phone} total={total}")

    # Uma fonte so para o flag (ver cardapio.em_modo_teste).
    test_mode = cardapio.em_modo_teste()
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

    # Pix exige conferencia do comprovante por uma pessoa antes de entrar no
    # cardapio (regra do dono; sem human_confirmed a API devolve 400). O pedido
    # fica gravado aqui aguardando o botao de validar no painel.
    aguardando_pix = (payment_method or "").strip().lower().startswith("pix")

    if aguardando_pix and not test_mode:
        result = {
            "success": True,
            "order_id": "",
            "display_id": "",
            "status": "aguardando_pix",
        }
        print(f"[cardápio] PIX - pedido retido para conferencia do comprovante")
    elif test_mode:
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
        # O status daqui e o do NOSSO lado, nao o da fabrica. Nada le o estado
        # de volta do cardapio — so existem dois endpoints, createOrderFromAgent
        # e calculateOrder —, entao guardar o "pendente" que o cardapio devolveu
        # no instante da criacao congelava essa palavra para sempre: o pedido
        # podia estar aceito, impresso e entregue e o painel continuaria dizendo
        # "pendente". "enviado" e o que de fato sabemos. A unica excecao e
        # aguardando_pix, que e estado NOSSO: o pedido fica retido aqui ate
        # alguem conferir o comprovante.
        status=("aguardando_pix" if result.get("status") == "aguardando_pix"
                else "enviado") if result.get("success") else "erro",
        api_response=result,
        error_message=result.get("error", "") if not result.get("success") else "",
        scheduled_date=scheduled_date if is_scheduled else "",
        scheduled_time=scheduled_time if is_scheduled else "",
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
        if result.get("status") == "aguardando_pix":
            return (
                "Pedido registrado e aguardando o comprovante do Pix. Envie a chave "
                "Pix ao cliente e diga que a producao e liberada assim que a equipe "
                "conferir o pagamento. NAO diga que o pedido ja foi finalizado."
            )
        return f"Pedido enviado com sucesso para o cardapio digital. Numero do pedido: {display_id}. Status: {result.get('status', 'enviado')}."
    else:
        # O prefixo e contrato com _send_handler: e por ele que o codigo sabe
        # que o pedido NAO entrou, sem depender de o modelo admitir.
        return (
            f"{FALHA_NO_ENVIO}: {result.get('error', 'desconhecido')}. "
            "O pedido NAO entrou no cardapio e a cozinha NAO recebeu nada. "
            "E PROIBIDO dizer ao cliente que o pedido foi confirmado, agendado "
            "ou finalizado. Avise que a equipe vai confirmar e encerre."
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sem_repeticao_consecutiva(texto: str) -> str:
    """Remove um trecho que o modelo repetiu logo em seguida a si mesmo.

    O modelo as vezes emite a mesma frase duas vezes. Quando a copia vem depois
    de uma linha em branco, a deduplicacao por paragrafo pega; quando vem
    separada por UMA quebra de linha, por um espaco ou colada, passava direto e
    o cliente recebia a pergunta duplicada.

    So colapsa repeticao CONSECUTIVA e IDENTICA de um trecho longo: duas linhas
    diferentes de um resumo, ou dois itens parecidos, nao sao tocados.
    """
    import re as _re

    def _chave(s: str) -> str:
        return " ".join(s.lower().split())

    # 1) linhas iguais em sequencia
    linhas, anterior = [], ""
    for linha in texto.split("\n"):
        if linha.strip() and len(linha.strip()) >= 25 and _chave(linha) == anterior:
            continue
        linhas.append(linha)
        if linha.strip():
            anterior = _chave(linha)
    texto = "\n".join(linhas)

    # 2) frases iguais em sequencia, dentro da mesma linha.
    #    O separador vai num grupo de captura para ser recolocado como estava:
    #    juntar tudo com espaco apagava as linhas em branco e mudava a divisao
    #    das mensagens enviadas ao cliente.
    partes = _re.split(r"((?<=[.!?])\s+)", texto)
    frases, anterior = [], ""
    for i in range(0, len(partes), 2):
        frase = partes[i]
        separador = partes[i + 1] if i + 1 < len(partes) else ""
        if len(frase.strip()) >= 30 and _chave(frase) == anterior:
            continue
        frases.append(frase + separador)
        if frase.strip():
            anterior = _chave(frase)
    texto = "".join(frases)

    # 3 e 4) a mensagem INTEIRA e um bloco repetido, com ou sem separador
    #    ("A. B. A. B."). As frases se alternam, entao a checagem consecutiva
    #    acima nao pega. Ancorado nas duas pontas, e so para bloco que pareca
    #    texto de verdade: sem essa guarda, algo degenerado como "A A A A..."
    #    tambem casava e era truncado.
    def _colapsar(m):
        bloco = m.group(1)
        palavras = {p for p in _re.findall(r"\w+", bloco.lower()) if len(p) >= 2}
        return bloco if len(palavras) >= 5 else m.group(0)

    texto = _re.sub(r"^(.{40,}?)(?:\s*\1)+$", _colapsar, texto.strip(), flags=_re.DOTALL)
    return texto


def split_reply(text: str) -> list[str]:
    text = _sem_repeticao_consecutiva(text or "")
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

    # O modelo as vezes repete a mesma frase em dois paragrafos seguidos —
    # imitando o texto do prompt operacional, que vem do .docx com trechos
    # duplicados. Para o cliente isso chega como a mesma pergunta duas vezes.
    unicos: list[str] = []
    vistos: set[str] = set()
    for c in chunks:
        if not c:
            continue
        chave = " ".join(c.lower().split())
        if chave in vistos:
            print(f"[split] paragrafo repetido descartado: {c[:60]!r}")
            continue
        vistos.add(chave)
        unicos.append(c)
    return unicos


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


# ── Reset ─────────────────────────────────────────────────────────────────────

_PALAVRA_RESET_PADRAO = "#reset"

_MENSAGEM_RESET_PADRAO = (
    "Prontinho, recomecei o atendimento do zero! 💛 Eu sou a Sandrinha da "
    "BALBS. Qual delícia você quer pedir?"
)


def palavra_de_reset() -> str:
    return (database.get_setting("reset_keyword", "") or _PALAVRA_RESET_PADRAO).strip().lower()


def e_pedido_de_reset(texto: str) -> bool:
    """A mensagem e a palavra-chave de reset?

    Token inteiro, nao trecho: "#reset" e "#reset por favor" contam, mas uma
    frase que apenas cite a palavra no meio de outra ("resetar o pedido") nao.
    """
    chave = palavra_de_reset()
    if not chave:
        return False
    limpo = (texto or "").strip().lower()
    return limpo == chave or chave in limpo.split()


def perform_reset(phone: str, origem: str = "cliente", avisar: bool = True) -> None:
    """Zera o atendimento e devolve a conversa para a Sandrinha.

    O espelho de perform_transfer: la a conversa sai do agente e vai para uma
    pessoa; aqui ela volta. Existe porque o transbordo so tinha ida — depois de
    um transbordo o numero ficava preso em modo humano ate alguem abrir o
    painel, que e exatamente o que o dono nao quer ter de fazer.
    """
    agora_iso = datetime.utcnow().isoformat()

    contact = database.get_contact(phone) or database.upsert_contact(phone)
    database.update_contact(contact["id"], auto_mode=1)

    conv = database.get_or_create_conversation(phone)
    # reset_at corta o historico lido do banco; transferred_at volta a NULL
    # para a conversa sair da fila de atendimento humano do painel.
    database.update_conversation(conv["id"], reset_at=agora_iso, transferred_at=None)

    # Historico em memoria + ficha do atendimento.
    memory.reset(phone)

    # Um pedido reenviado depois do reset nao pode ser barrado como duplicata.
    for chave in [k for k in _orders_sent if k.startswith(f"{phone}:")]:
        _orders_sent.discard(chave)

    database.save_message(
        conversation_id=conv["id"],
        phone=phone,
        direction="internal",
        body=f"Atendimento reiniciado por {origem} ({palavra_de_reset()}) em "
             f"{datetime.utcnow().strftime('%d/%m %H:%M')} — agente reativado",
        sender="system",
        msg_type="system",
    )

    events.emit("conversation_updated", {
        "conversation_id": conv["id"],
        "auto_mode": 1,
        "transferred": False,
        "reset_at": agora_iso,
    })

    print(f"[reset] user={phone} atendimento zerado e agente reativado ({origem})")

    if not avisar:
        return

    resposta = database.get_setting("reset_message", "") or _MENSAGEM_RESET_PADRAO
    res = send_text(phone, resposta)
    if res.get("ok"):
        database.save_message(
            conversation_id=conv["id"],
            phone=phone,
            direction="out",
            body=resposta,
            sender="agent",
            msg_type="text",
        )
        database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        # A saudacao acabou de sair: a Sandrinha nao repete na proxima mensagem.
        try:
            ficha = conversation_memory.load(phone)
            ficha["saudacao_feita"] = True
            conversation_memory.save(phone, ficha)
        except Exception as e:
            print(f"[reset] WARN nao marcou saudacao de {phone}: {e}")
    else:
        print(f"[reset] WARN falha ao confirmar reset p/ {phone}: {res.get('error')}")


# ── Flush handler ─────────────────────────────────────────────────────────────

_VALOR_RE = __import__("re").compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:[.,]\d{1,2})?)")


def _valores_oficiais() -> set:
    """Todos os valores monetarios que existem na base oficial."""
    dados = facts.get()
    valores = {float(b["taxa"]) for b in dados["bairros"]}
    valores |= {float(p["preco"]) for p in dados["produtos"] if p["preco"] is not None}
    return valores


def _multiplo_de(valor: float, base: float, limite: int = 500) -> bool:
    if base <= 0:
        return False
    fator = valor / base
    return abs(fator - round(fator)) < 0.005 and 0 < round(fator) <= limite


def _valor_explicavel(valor: float, conhecidos: set, consultados: list) -> bool:
    """O valor sai da base, de uma quantidade x preco, ou de subtotal + frete?"""
    alvo = round(valor, 2)
    if alvo in conhecidos:
        return True
    if any(_multiplo_de(alvo, base) for base in consultados):
        return True
    # subtotal (quantidade x preco) somado a uma taxa tambem consultada
    for taxa in consultados:
        resto = round(alvo - taxa, 2)
        if resto > 0 and any(_multiplo_de(resto, base) for base in consultados):
            return True
    return False


# "Total: R$ 72,00", "o total fica R$ 84,00", "total e de R$ 58".
_RX_TOTAL_DITO = _re_mod.compile(
    r"total[^\d\n]{0,24}?R?\$?\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:[.,]\d{1,2})?)",
    _re_mod.IGNORECASE,
)

# O modelo concordando em tirar a taxa de retorno. Ela nao e negociavel: o
# motoboy volta com a maquina ou com o troco de qualquer jeito. No teste do
# dono de 22/09 17:09 bastou o cliente reclamar ("por que cobrou taxa se vou
# pagar no cartao?") para a Sandrinha responder "removendo a taxa de retorno".
_RX_TIRA_RETORNO = _re_mod.compile(
    r"(?:remov\w+|retir\w+|tir\w+|sem|isent\w+|nao (?:vou |irei )?cobr\w+|"
    r"nao (?:sera|e) cobrad\w+|desconsider\w+)[^.\n]{0,40}taxa de retorno"
    r"|taxa de retorno[^.\n]{0,40}(?:remov\w+|retirad\w+|isent\w+|"
    r"nao (?:sera|e) cobrad\w+|nao se aplica|zerad\w+)",
    _re_mod.IGNORECASE,
)


def _valor_em_float(bruto: str) -> float:
    """'1.234,56' -> 1234.56; '58' -> 58.0. Devolve 0.0 no que nao for numero."""
    texto = bruto.replace(".", "").replace(",", ".") if "," in bruto else bruto
    try:
        return float(texto)
    except ValueError:
        return 0.0


def _numeros_do_pedido(memoria: dict) -> dict:
    """Recalcula o pedido da ficha em CODIGO. {} quando ainda nao da para somar.

    Esta e a unica fonte de dinheiro que o cliente pode ver. O modelo continua
    escrevendo a conversa, mas nao a aritmetica: na bateria do dono ele errou
    o subtotal (disse R$ 70,00 onde eram R$ 58,00) e so nao foi para a comanda
    porque o proprio cliente conferiu a soma.
    """
    itens = memoria.get("items") or []
    if not itens:
        return {}
    try:
        resultado = cardapio.calcular_pedido(
            itens,
            memoria.get("bairro", ""),
            modalidade=memoria.get("modality", ""),
            dia=memoria.get("data_agendada", ""),
            horario=memoria.get("horario", ""),
            pagamento=memoria.get("payment_method", ""),
            cliente=memoria.get("name", ""),
            endereco=memoria.get("endereco", ""),
            troco=memoria.get("troco_para", ""),
            referencia=memoria.get("referencia", ""),
            data_iso=memoria.get("data_iso", ""),
        )
    except Exception as e:
        print(f"[dinheiro] WARN nao recalculou: {e}")
        return {}
    return resultado if resultado.get("success") else {}


def _conferir_dinheiro(user: str, reply: str, memoria: dict,
                       calculo: dict | None = None, pular: bool = False) -> str:
    """Troca o dinheiro inventado pelo resumo que o codigo calculou.

    Os dois pedidos do dono sobre valor — taxa de retorno inegociavel e
    "matematica por funcao" — sao o mesmo pedido: o numero que chega ao
    cliente tem de vir daqui, nao da cabeca do modelo. Em vez de tentar
    consertar a frase, devolve o resumo oficial inteiro, que ja sai formatado
    de cardapio.montar_resumo com subtotal, frete, taxa e troco.

    `calculo` e a resposta da calculate_order deste turno, quando houve uma.
    Ela tem prioridade sobre a ficha: e o numero que o modelo TINHA EM MAOS
    quando escreveu a resposta, entao divergir dela nao e ficha desatualizada,
    e o modelo ignorando a propria ferramenta. Conferir contra ela tambem nao
    custa rede nenhuma.

    `pular` vale quando a tool rodou e FALHOU (pediu confirmacao, item fora do
    cardapio): ai a resposta do modelo e uma pergunta, nao um total.
    """
    if not reply or pular:
        return reply

    ditos = [_valor_em_float(b) for b in _RX_TOTAL_DITO.findall(reply)]
    ditos = [v for v in ditos if v > 0]
    negou_retorno = bool(_RX_TIRA_RETORNO.search(facts.normalize(reply)))
    if not ditos and not negou_retorno:
        return reply

    # So corrige pedido que JA passou por calculate_order alguma vez nesta
    # conversa. A ficha e montada por extracao e pode estar incompleta — no
    # aceite ela ficou com 2 dos 3 itens, e sem esta guarda o codigo trocava o
    # total certo do modelo (R$ 58,00) por um errado calculado sobre meia
    # ficha (R$ 40,00). Numero errado com cara de oficial e pior do que o
    # problema que esta funcao veio resolver.
    #
    # Nao e perda de alcance: o defeito que o dono relatou e "a IA alucinou
    # valores AO EDITAR ITENS", ou seja, depois de ja ter havido um calculo.
    if not calculo:
        try:
            _ja_calculado = float(memoria.get("total") or 0)
        except (TypeError, ValueError):
            _ja_calculado = 0.0
        if _ja_calculado <= 0:
            print(f"[dinheiro] user={user} valores fora de calculate_order, mas a ficha "
                  f"nunca foi calculada; nao da para conferir sem arriscar piorar")
            return reply

    numeros = calculo or _numeros_do_pedido(memoria)
    if not numeros:
        if negou_retorno:
            print(f"[dinheiro] user={user} modelo mexeu na taxa de retorno mas a "
                  f"ficha ainda nao fecha conta; mantendo a resposta")
        return reply

    oficial = round(float(numeros.get("total") or 0), 2)
    if oficial <= 0:
        return reply

    errados = [v for v in ditos if abs(v - oficial) > 0.01]
    if not errados and not negou_retorno:
        return reply

    resumo = numeros.get("resumo_formatado") or ""
    if not resumo:
        return reply

    if negou_retorno and numeros.get("return_fee"):
        print(f"[dinheiro] user={user} modelo tentou tirar a taxa de retorno "
              f"(devida: R$ {numeros['return_fee']:.2f}); resumo do codigo no lugar")
    if errados:
        print(f"[dinheiro] user={user} total dito {errados} != calculado "
              f"R$ {oficial:.2f}; resumo do codigo no lugar")
    return resumo


# Como a pergunta de tamanho aparece na resposta. Vale sobre texto ja
# normalizado (sem acento), porque o modelo escreve "balcão" e "porção".
_RX_PERGUNTA_TAMANHO = _re_mod.compile(
    r"(balcao|avuls|grande ou|porcao ou|festa ou|ou a porcao|ou grande|"
    r"unidade avuls|salgado grande)",
    _re_mod.IGNORECASE,
)

# Onde uma frase termina. O modelo emenda a pergunta de tamanho no fim de uma
# pergunta legitima, entao cortar a mensagem inteira perderia a parte boa.
# A quebra de linha corta sempre, com ou sem espaco depois: sem isso o
# espelho do pedido inteiro virava uma "frase" so.
_RX_FIM_DE_FRASE = _re_mod.compile(r"\n+|(?<=[?!.])\s+")

# O espelho do pedido, que o filtro nao pode tocar.
_RX_LINHA_DE_RESUMO = _re_mod.compile(
    r"(💵\s*total|💰\s*subtotal|👤\s*cliente|🚚\s*frete)",
    _re_mod.IGNORECASE)


def _conferir_tamanho(user: str, reply: str, memoria: dict) -> str:
    """Tira a pergunta de tamanho quando a quantidade ja resolveu o tamanho.

    Irmao de _conferir_dinheiro, e pela mesma razao. A regra da fabrica e que
    de 21 unidades para cima o pedido e SEMPRE festa; o codigo parou de mandar
    perguntar, o prompt proibe a pergunta e a ficha repete a proibicao a cada
    volta — e o modelo perguntava assim mesmo em cerca de 1 de cada 3 rodadas,
    recitando a frase do BLOCO 2 com 50 unidades na ficha.

    Instrucao nao e garantia. O que garante e isto: a frase nao chega ao
    cliente. Com qualquer modelo, de qualquer provedor.

    So age quando NAO ha duvida a resolver (conversation_memory diz quando).
    Com um item abaixo do minimo no pedido a pergunta e legitima, e e ela que
    evitou os R$ 450,00 em coxinha de balcao.
    """
    if not reply or not conversation_memory.tamanho_ja_definido(memoria):
        return reply

    # Nunca mexer no espelho do pedido. Ele tem linha de item, subtotal,
    # total e a pergunta de confirmacao no fim, tudo na mesma mensagem; um
    # item de balcao no meio ("30 un - Quiche de Frango") faria o filtro
    # comer o resumo inteiro. Foi assim que _conferir_dinheiro trocou um
    # total certo por um errado em 23/09 — o mesmo erro nao se repete aqui.
    if _RX_LINHA_DE_RESUMO.search(reply):
        return reply

    def _e_pergunta_de_tamanho(frase: str) -> bool:
        # Tem de ser PERGUNTA. Sem isto, um resumo com salgado grande no meio
        # ("30 un - Quiche de Frango (Salgado Grande)") seria cortado junto:
        # a frase cita balcao, mas esta informando, nao perguntando.
        return ("?" in frase
                and bool(_RX_PERGUNTA_TAMANHO.search(facts.normalize(frase))))

    frases = _RX_FIM_DE_FRASE.split(reply)
    limpas = [f for f in frases if not _e_pergunta_de_tamanho(f)]
    if len(limpas) == len(frases):
        return reply

    cortadas = [f.strip() for f in frases if f not in limpas]
    print(f"[tamanho] user={user} quantidade ja define o tamanho; "
          f"pergunta removida: {' | '.join(cortadas)[:160]}")

    novo = " ".join(f.strip() for f in limpas if f.strip()).strip()
    if novo:
        return novo

    # A pergunta era a mensagem inteira. Nao da para devolver vazio: o codigo
    # escreve a proxima pergunta do funil, como ja faz em outras recusas.
    return _proxima_pergunta_do_funil(memoria)


def _proxima_pergunta_do_funil(memoria: dict) -> str:
    """Texto de codigo para seguir o atendimento, sem passar pelo modelo."""
    pendentes = conversation_memory.faltando(memoria)
    if not pendentes:
        return ("Confirmando o seu pedido! Posso seguir para o resumo com os "
                "valores? 💛")
    rotulos = [conversation_memory._ROTULOS.get(c, c) for c in pendentes]
    if len(rotulos) == 1:
        return f"Para seguir, me confirma: {rotulos[0].lower()}? 😊"
    lista = "\n".join(f"• {r}" for r in rotulos)
    return f"Para seguir, me confirma:\n{lista}"


def _auditar_valores(user: str, reply: str, consultados: list) -> None:
    """Registra no log todo valor em R$ da resposta que nao veio da base.

    Nao reescreve a mensagem: serve para enxergar, nos logs, se o agente ainda
    esta inventando numero — e para qual cliente. Somas (subtotal, total) sao
    aceitas quando batem com a combinacao dos valores consultados.
    """
    if not reply:
        return
    try:
        citados = []
        for bruto in _VALOR_RE.findall(reply):
            texto = bruto.replace(".", "").replace(",", ".") if "," in bruto else bruto
            try:
                citados.append(float(texto))
            except ValueError:
                continue
        if not citados:
            return

        conhecidos = _valores_oficiais() | {round(float(v), 2) for v in consultados}
        suspeitos = [
            v for v in citados
            if v > 0 and not _valor_explicavel(v, conhecidos, consultados)
        ]
        if suspeitos:
            print(
                f"[auditoria] user={user} valores SEM ORIGEM na base: {suspeitos} "
                f"| consultados nesta resposta: {consultados}"
            )
    except Exception as e:
        print(f"[auditoria] WARN falhou: {e}")


# Ritmo de digitacao entre as mensagens divididas por split_reply.
# Antes toda mensagem esperava 1 + len/200 segundos, inclusive a PRIMEIRA: o
# cliente ja tinha esperado o debounce e o modelo, e ainda levava mais ~2s para
# ver a primeira linha. Agora a primeira sai quase imediata e so as seguintes
# tem a pausa curta que imita digitacao.
PAUSA_PRIMEIRA = float(os.getenv("PAUSA_PRIMEIRA_SEG", "0.4"))
PAUSA_MIN = float(os.getenv("PAUSA_MIN_SEG", "0.8"))
PAUSA_MAX = float(os.getenv("PAUSA_MAX_SEG", "2.0"))

# Piso do tempo de resposta pedido pelo dono: a Sandrinha responde entre 5 e 10
# segundos. O teto sai do debounce curto e do ritmo acima; o piso existe porque
# resposta pronta e curta saia em ~4s, e resposta instantanea entrega que do
# outro lado nao tem ninguem digitando. Conta do ponto de vista do cliente,
# entao o debounce ja consumido entra na conta.
RESPOSTA_MIN_SEG = float(os.getenv("RESPOSTA_MIN_SEG", "5.0"))


# Concordancia curta com o que a Sandrinha acabou de oferecer. Deliberadamente
# estreito: so mensagem curta e so as formas que nao carregam informacao nova,
# para nao roubar a vez de uma resposta que traz dado ("pode ser dia 23").
_CONCORDANCIAS = {
    "sim", "s", "sim pode", "pode", "pode sim", "pode ser", "pode por favor",
    "por favor", "ok", "okay", "blz", "beleza", "isso", "isso mesmo", "claro",
    "certo", "ta bom", "tá bom", "tabom", "fechou", "confirma", "confirmo",
    "manda ver", "bora", "aceito", "perfeito", "otimo", "ótimo", "combinado",
    "sim por favor", "pode deixar", "tudo bem", "uhum", "aham",
}


# Horario dentro da concordancia ("pode sim, as 11h"). O horario ja e extraido
# para a ficha por outro caminho, entao aqui ele e apenas descontado: o que
# interessa e se o que sobra e um "sim".
_SO_HORARIO = _re_mod.compile(
    r'\b(?:as|às|ás|para|pra|pro|ao|meio\s*dia|manha|manhã|tarde|noite'
    r'|\d{1,2}(?::\d{2})?\s*(?:h|hs|hrs|horas?)?)\b',
    _re_mod.IGNORECASE | _re_mod.UNICODE,
)


def _concorda(frase: str) -> bool:
    """A frase, sozinha, e um 'sim'?"""
    limpo = _re_mod.sub(r"[^\w\s]", " ", (frase or "").lower(), flags=_re_mod.UNICODE)
    limpo = " ".join(limpo.split())
    if not limpo:
        return False
    if limpo in _CONCORDANCIAS:
        return True
    # "pode sim, as 11h": tira o horario e reavalia o que sobrou.
    sem_horario = " ".join(_SO_HORARIO.sub(" ", limpo).split())
    return bool(sem_horario) and sem_horario in _CONCORDANCIAS


def _e_concordancia(texto: str) -> bool:
    """O cliente aceitou o dia que a Sandrinha ofereceu?

    Basta a mensagem COMECAR com o sim; ela nao precisa ser so isso. Quem
    aceita costuma emendar o pedido na mesma frase, e exigir a mensagem
    inteira fazia a data se perder: no teste do dono de 23/09 13:32 ele
    respondeu "Pode sim, pode adiantar pras 14h. Passa os valores de tudo com
    a entrega e o cartao pra eu confirmar" e a Sandrinha perguntou o dia de
    novo, dois turnos depois.

    So a PRIMEIRA oracao conta, o que mantem a guarda contra o contrario:
    "nao, pode ser amanha" comeca com "nao" e continua nao sendo aceite.
    """
    if _concorda(texto):
        return True
    primeira = _re_mod.split(r"[,.;!]", (texto or "").strip(), maxsplit=1)[0]
    return _concorda(primeira)


# Marcas de que a mensagem anterior do agente FOI o espelho do pedido. Ambas
# saem de montar_resumo e do modelo do BLOCO 5.
_MARCAS_DE_RESUMO = ("posso confirmar o seu pedido", "resumo do pedido")

# Aceites listados no BLOCO 5 do prompt operacional, mais os que aparecem na
# pratica. Mais largo que _CONCORDANCIAS de proposito: aqui existe o contexto
# de que a pergunta anterior foi "Posso confirmar o seu pedido?", e e ele que
# segura o falso positivo.
_ACEITES_DE_PEDIDO = (
    "sim", "pode", "confirma", "confirmo", "confirmado", "fechou", "fechado",
    "beleza", "blz", "manda ver", "manda", "ta bom", "tudo certo", "certo",
    "isso", "isso mesmo", "ok", "okay", "bora", "aceito", "perfeito", "otimo",
    "combinado", "positivo", "exato", "correto", "pode mandar", "pode fechar",
)

# Se aparecer qualquer uma destas, NAO e aceite limpo: o cliente esta mexendo
# no pedido, e forcar o fechamento gravaria a comanda errada.
_RESSALVAS_NO_ACEITE = (
    "nao", "mas", "muda", "mudar", "troca", "trocar", "errou", "errado",
    "cancela", "cancelar", "espera", "aguarda", "so que", "porem", "antes",
    "acrescenta", "adiciona", "tira", "remove", "retira", "corrige", "ajusta",
)


# Frases de encerramento do BLOCO 5. Se alguma aparece, o modelo esta dizendo
# ao cliente que o pedido esta fechado.
_MARCAS_DE_FECHAMENTO = (
    "pedido agendado", "pedido finalizado", "pedido confirmado",
    "ja esta sendo preparado", "esta sendo preparado",
    "obrigado por escolher a balbs", "obrigada por escolher a balbs",
)


# Como a oferta de ajuste de quantidade aparece na resposta da Sandrinha.
_MARCAS_DE_AJUSTE_DE_LOTE = (
    "ajusto para", "ajustar para", "de 25 em 25", "porcoes fechadas",
    "porcao fechada", "multiplo de 25", "fechamos de 25",
)


# Como o cliente escolhe o lado da duvida "pacote ou salgado grande".
_MARCAS_ESCOLHEU_PACOTE = (
    "pacote", "porcao", "porcoes", "festa", "de 25", "25 de cada", "meio cento",
    "cento",
)
_MARCAS_ESCOLHEU_BALCAO = ("balcao", "avuls", "grande", "unidade", "catupiry")


def _escolheu_pacote(texto: str) -> bool:
    """O cliente optou pelo sabor de pacote, e nao pelo salgado grande?"""
    limpo = " ".join(facts.normalize(texto or "").split())
    if not limpo:
        return False
    if any(m in limpo for m in _MARCAS_ESCOLHEU_BALCAO):
        return False          # citou o outro lado: nao decida por ele
    return any(m in limpo for m in _MARCAS_ESCOLHEU_PACOTE)


def _ofereceu_ajuste_de_lote(texto: str) -> bool:
    """A ultima resposta ofereceu arredondar a quantidade para o lote?"""
    limpo = " ".join(facts.normalize(texto or "").split())
    return any(facts.normalize(m) in limpo for m in _MARCAS_DE_AJUSTE_DE_LOTE)


def _parece_fechamento(texto: str) -> bool:
    """A resposta anuncia ao cliente que o pedido foi fechado?"""
    limpo = " ".join(facts.normalize(texto or "").split())
    return any(facts.normalize(m) in limpo for m in _MARCAS_DE_FECHAMENTO)


def _confirmou_o_resumo(ultima_do_agente: str, texto_do_cliente: str) -> bool:
    """O cliente esta aceitando o espelho que acabou de receber?

    Existe porque o modelo, depois do "sim", chamava calculate_order de novo e
    reapresentava o MESMO resumo em vez de fechar - o cliente tinha de
    confirmar duas vezes. So vale com os dois lados: o agente pediu confirmacao
    E a resposta e um aceite sem ressalva.
    """
    # facts.normalize tira acento e pontuacao: sem isso "ta bom" nao casa com
    # o "tá bom" que o cliente escreve.
    anterior = " ".join(facts.normalize(ultima_do_agente or "").split())
    if not any(facts.normalize(m) in anterior for m in _MARCAS_DE_RESUMO):
        return False

    limpo = " ".join(facts.normalize(texto_do_cliente or "").split())
    if not limpo or len(limpo.split()) > 8:
        return False

    palavras = limpo.split()
    if any(r in palavras or r in limpo for r in _RESSALVAS_NO_ACEITE):
        return False

    return any(a == limpo or a in palavras or f" {a} " in f" {limpo} "
               for a in _ACEITES_DE_PEDIDO)


def _pausa_digitacao(indice: int, chunk: str) -> float:
    if indice == 0:
        return PAUSA_PRIMEIRA
    return min(PAUSA_MAX, max(PAUSA_MIN, len(chunk) / 200))


def _esperar_piso(chegada: float | None) -> None:
    """Segura a primeira mensagem ate fechar RESPOSTA_MIN_SEG desde o cliente.

    `chegada` e o instante em que a mensagem entrou no webhook — o relogio do
    cliente. Antes isso era o comeco do flush mais BUFFER_SECONDS, o que
    ignorava os segundos gastos em gravar a mensagem e fazia o piso somar
    espera a quem ja tinha esperado demais.
    """
    if chegada is None:
        return
    decorrido = time.monotonic() - chegada
    if decorrido < RESPOSTA_MIN_SEG:
        time.sleep(RESPOSTA_MIN_SEG - decorrido)


def enviar_em_blocos(user: str, texto: str, chegada: float | None = None) -> None:
    """Envia a resposta ja dividida, com o ritmo de digitacao, e espelha no painel.

    `chegada` e o time.monotonic() de quando a mensagem do cliente chegou; com
    ele a primeira mensagem respeita o piso de RESPOSTA_MIN_SEG.
    """
    chunks = split_reply(texto) or ([texto] if texto else [])
    for indice, chunk in enumerate(chunks):
        pausa = _pausa_digitacao(indice, chunk)
        if pausa > 0:
            time.sleep(pausa)
        if indice == 0:
            _esperar_piso(chegada)
        res = send_text(user, chunk)
        if not res.get("ok"):
            print(f"[flush] ERRO ao enviar p/ {user}: {res.get('error')}")
            continue
        send_msg_out_to_panel(user, chunk)


def _responder_direto(user: str, items: list[dict], texto: str,
                     chegada: float | None = None) -> None:
    """Envia uma resposta decidida em codigo, sem passar pelo LLM.

    Usado para as regras de negocio fechadas (fora do expediente, horario
    invalido): elas nao dependem de o modelo lembrar de aplica-las, mas ainda
    precisam do mesmo registro em memoria e no painel que qualquer resposta —
    inclusive a mensagem do cliente, que neste caminho ainda nao foi gravada.
    """
    memory.append_multimodal(user, "user", _build_user_parts(items))
    memory.append(user, "assistant", texto)
    enviar_em_blocos(user, texto, chegada)


def handle_flush(user: str, items: list[dict]) -> None:
    _inicio = time.monotonic()
    # Quando o cliente mandou a mensagem. E o unico relogio que interessa para
    # o piso e para a linha [tempo]; _inicio so serve para medir o flush.
    _chegada = min((it.get("recebido_em") or 0.0) for it in items) if items else 0.0
    if not _chegada:
        _chegada = _inicio - BUFFER_SECONDS
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
                    threading.Thread(target=_avisar_digitando_async,
                                     args=(last_wa_msg_id,), daemon=True).start()
                # Processa via sub-agentes
                reply = manager.handle_message(phone=user, message=message_text)
                if reply:
                    memory.append(user, "assistant", reply)
                    enviar_em_blocos(user, reply, _chegada)
                return

        # ── Modo classico (prompt unico) ─────────────────────────────────────
        # ── Detecção de palavras-chave de transferência (antes do LLM) ──────────
        matched_kw = _check_transfer_keywords(items)
        if matched_kw:
            print(f"[flush] user={user} keyword de transferência detectada: '{matched_kw}'")
            perform_transfer(user, reason=f"cliente mencionou '{matched_kw}'")
            return

        # Typing indicator em background: e enfeite, e a ida ate a Datafy
        # custava 0,8s do tempo do cliente. Em thread ele aparece no celular
        # mais cedo do que antes, e nao no lugar do preparo da resposta.
        last_wa_msg_id = items[-1].get("wa_message_id", "") if items else ""
        if last_wa_msg_id and last_wa_msg_id.startswith("wamid."):
            threading.Thread(target=_avisar_digitando_async,
                             args=(last_wa_msg_id,), daemon=True).start()

        # ── Memoria estruturada do atendimento ───────────────────────────────
        # A extracao roda duas vezes: uma sobre o que ja esta na memoria (so
        # preenche lacunas) e outra sem estado (detecta correcao do cliente).
        text_only = "\n".join(it.get("text", "") for it in items)

        _memoria = conversation_memory.load(user)
        if contact and contact.get("name") and not _memoria.get("name"):
            _memoria["name"] = contact["name"]

        _state_memoria = ConversationState(phone=user, **{
            campo: _memoria.get(campo) or ("" if campo != "items" else [])
            for campo in ("name", "items", "modality", "bairro", "endereco",
                          "referencia", "horario", "data_agendada", "payment_method")
        })
        _extracted = _extract_data_from_message(text_only, _state_memoria)
        _extracted_fresh = _extract_data_from_message(text_only, ConversationState(phone=user))

        _memoria, _mudancas = conversation_memory.merge(_memoria, _extracted, _extracted_fresh)
        conversation_memory.save(user, _memoria)
        if _mudancas:
            print(f"[memoria] {user}: {', '.join(_mudancas)}")

        # Nome novo tambem atualiza o cadastro do contato
        if _memoria.get("name") and (not contact or contact.get("name") != _memoria["name"]):
            try:
                database.upsert_contact(user, name=_memoria["name"])
            except Exception as e:
                print(f"[memoria] WARN nao atualizou contato: {e}")

        # Cliente aceitando o dia que a propria Sandrinha ofereceu ("Posso
        # deixar para segunda-feira (21/09)?" -> "Pode sim"). Sem isto a
        # concordancia nao virava data e o dia era perguntado de novo, duas e
        # tres vezes seguidas.
        if _memoria.get("data_proposta") and not _memoria.get("data_iso"):
            if _e_concordancia(text_only):
                _proposta = _memoria["data_proposta"]
                try:
                    _d = date.fromisoformat(_proposta)
                except (TypeError, ValueError):
                    _d = None
                if _d:
                    _memoria["data_iso"] = _proposta
                    _memoria["data_agendada"] = horario.formatar_data(_d, horario.agora())
                    _memoria["data_proposta"] = ""
                    conversation_memory.save(user, _memoria)
                    print(f"[memoria] {user}: cliente aceitou o dia oferecido "
                          f"-> {_memoria['data_agendada']}")

        # ── Expediente e agendamento (decididos em codigo) ───────────────────
        # Estas duas respostas saem sem passar pelo LLM porque sao regras de
        # negocio fechadas, e eram justamente as que o modelo pulava quando o
        # cliente mandava o pedido pronto na primeira mensagem.
        _agora = horario.agora()
        _expediente = horario.status(_agora)

        if not _expediente["aberto"] and not _memoria.get("aviso_horario_dado"):
            _memoria["aviso_horario_dado"] = True
            if not _expediente["aceita_hoje"] and _memoria.get("data_iso") == _agora.date().isoformat():
                # Cliente pediu para hoje, mas hoje ja nao cabe: o dia volta a
                # ser pergunta em aberto em vez de seguir errado.
                _memoria["data_iso"] = ""
                _memoria["data_agendada"] = ""
            _nome = f", {_memoria['name']}" if _memoria.get("name") else ""
            # Esta resposta sai SEM passar pelo modelo, e por isso o pedido que
            # o cliente acabou de escrever ficava sem resposta: ele mandou
            # "QUERO 50 SALSICHA E 100 RISSOLES" e ouviu so que a producao
            # tinha encerrado, como se ninguem tivesse lido os itens (teste
            # do dono, 23/09 22:43). Os itens ja estao na ficha desde a
            # extracao; o que faltava era dizer isso ao cliente.
            _itens_lidos = conversation_memory._formatar_itens(_memoria.get("items"))
            _eco = f"Anotei seu pedido: {_itens_lidos}.\n\n" if _itens_lidos else ""
            _resposta = (
                f"{horario.saudacao(_agora)}{_nome}! Aqui é a Sandrinha da BALBS.\n\n"
                f"{_eco}{_expediente['mensagem']}"
            )
            _memoria["saudacao_feita"] = True
            # Guarda o dia oferecido na mensagem para o "pode sim" seguinte.
            if _expediente.get("sugestao_data") and not _memoria.get("data_iso"):
                _memoria["data_proposta"] = _expediente["sugestao_data"].isoformat()
            conversation_memory.save(user, _memoria)
            _responder_direto(user, items, _resposta, _chegada)
            return

        if _memoria.get("data_iso") and _memoria.get("horario"):
            _ok, _motivo, _sugestao = horario.validar_agendamento(
                date.fromisoformat(_memoria["data_iso"]), _memoria["horario"], _agora
            )
            if not _ok and _motivo and not _motivo.startswith("Falta"):
                # Limpa so o campo que nao cabe, para o agente reperguntar esse
                # e nao a ficha inteira.
                if "horario" in _motivo.lower() or "intervalo" in _motivo.lower() or "20h00" in _motivo:
                    _memoria["horario"] = ""
                else:
                    _memoria["data_iso"] = ""
                    _memoria["data_agendada"] = ""
                    # A recusa ja ofereceu outro dia; guarda para o "pode sim".
                    if _sugestao:
                        _memoria["data_proposta"] = _sugestao.isoformat()
                _memoria["saudacao_feita"] = True
                conversation_memory.save(user, _memoria)
                _responder_direto(user, items, _motivo, _chegada)
                return

        user_parts = _build_user_parts(items)

        # --- INÍCIO DETECÇÃO DE CONFIRMAÇÃO ---
        history_before = memory.get(user)
        last_assistant_text = ""
        for m in reversed(history_before):
            if m.get("role") == "assistant":
                last_assistant_text = (m.get("parts", [{}])[0].get("text", "") or "")
                break

        # Resposta seca a pergunta anterior ("Eduardo", "Olaria"): a extracao
        # por palavra-chave nao pega, e sem isto o agente perguntava, o cliente
        # respondia, e ele perguntava de novo.
        _campo_direto = conversation_memory.capturar_resposta_direta(
            _memoria, last_assistant_text, text_only
        )
        # Troco tem captura propria: capturar_resposta_direta descarta qualquer
        # candidato com digito, guarda que e justamente o que impede "50" de
        # virar nome ou bairro.
        _troco_capturado = conversation_memory.capturar_troco(
            _memoria, last_assistant_text, text_only
        )
        if _troco_capturado:
            conversation_memory.save(user, _memoria)
            print(f"[memoria] {user}: troco_para={_troco_capturado}")

        # Cliente aceitando o ajuste de quantidade que a propria Sandrinha
        # ofereceu ("Ajusto para 25 quibes?" -> "pode ser"). O aceite precisa
        # entrar na FICHA: enquanto ele ficava so no texto, a trava de lote
        # continuava vendo 5 quibes, o funil nao andava e o modelo acabava
        # transferindo para humano um pedido ja ajustado (teste do dono,
        # 23/09 15:41).
        # Cliente escolhendo o lado da duvida de tamanho ("pode ser a do pacote
        # mesmo"). Tem de entrar na FICHA: enquanto a escolha ficava so no
        # texto, a duvida voltava a cada mensagem e o pedido ia para a fila do
        # humano ja resolvido (23/09 15:41).
        if (conversation_memory.pendencia_de_item(_memoria)
                and _escolheu_pacote(text_only)):
            _escolha = conversation_memory.aplicar_escolha_de_pacote(_memoria)
            if _escolha:
                conversation_memory.save(user, _memoria)
                print(f"[memoria] {user}: cliente escolheu o pacote -> "
                      f"{'; '.join(_escolha)}")

        if (_ofereceu_ajuste_de_lote(last_assistant_text)
                and _e_concordancia(text_only)):
            _ajustes = conversation_memory.aplicar_ajuste_de_lote(_memoria)
            if _ajustes:
                conversation_memory.save(user, _memoria)
                print(f"[memoria] {user}: ajuste de lote aceito -> "
                      f"{'; '.join(_ajustes)}")

        if _campo_direto:
            conversation_memory.save(user, _memoria)
            print(f"[memoria] {user}: {_campo_direto}={_memoria[_campo_direto]} (resposta direta)")
            if _campo_direto == "name":
                try:
                    database.upsert_contact(user, name=_memoria["name"])
                except Exception as e:
                    print(f"[memoria] WARN nao atualizou contato: {e}")

        is_short_reply = len(text_only.strip()) <= 60
        confirmation_keywords = ["sim", "isso", "isso mesmo", "esse", "essa", "esse mesmo", "1 mesmo", "uma mesmo", "1 de 25", "1 de 25 mesmo", "pode ser", "correto", "exatamente", "é isso", "quero esse", "ok", "manda", "confirmo"]

        # Padrões para detectar respostas sobre data/horário
        date_time_patterns = ["hoje", "amanha", "agora", "daqui a pouco", "já", "já já"]
        time_patterns = [r"\d{1,2}h", r"\d{1,2}:\d{2}", "manha", "tarde", "noite", "almoco"]

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

        dynamic_prompt = _get_current_system_prompt()

        # ── BASE OFICIAL (uma unica vez, sem duplicar o prompt) ──────────────
        full_knowledge = knowledge_rag.get_full_knowledge_content()
        if full_knowledge:
            dynamic_prompt += (
                "\n\n=== BASE DE CONHECIMENTO OFICIAL ===\n"
                "Esta e a UNICA fonte valida para preco, taxa de entrega, horario, "
                "forma de pagamento e politica da empresa.\n"
                "Cada linha e um registro completo no formato 'campo | campo | campo'.\n"
                "Se um dado NAO estiver aqui nem no retorno de uma tool, ele NAO existe: "
                "diga que vai confirmar com a equipe ou transfira - nunca estime.\n\n"
                + full_knowledge
            )

        # ── CONTEUDO DINAMICO (no final, para preservar o cache do prefixo) ──
        # Calendario e expediente ja resolvidos em codigo: saudacao, dia da
        # semana e "estamos abertos?" sao conta, nao interpretacao de texto.
        dynamic_prompt += (
            "\n\n=== CALENDARIO E EXPEDIENTE (FATOS, NAO INTERPRETE) ===\n"
            + horario.contexto_para_prompt(_agora)
            + f"\nO telefone (numero WhatsApp) do cliente e: {user} "
            f"(use em send_order_to_cardapio)."
        )

        # Saudacao: uma vez so, e na primeira resposta — inclusive quando o
        # cliente manda o pedido inteiro de uma vez, que era justamente o caso
        # em que ela era pulada. A diretiva e montada aqui mas so entra no
        # prompt DEPOIS da ficha: a ficha termina com "pergunte SO isso", e o
        # modelo lia isso como proibicao de cumprimentar.
        if not _memoria.get("saudacao_feita"):
            _nome_saud = f", {_memoria['name']}" if _memoria.get("name") else ""
            _diretiva_saudacao = (
                f"\n\n=== ABERTURA DESTA MENSAGEM (vale sobre tudo acima) ===\n"
                f'Esta e a PRIMEIRA resposta da conversa. A mensagem COMECA por '
                f'"{horario.saudacao(_agora)}{_nome_saud}!" seguido de uma '
                f"apresentacao curta como Sandrinha da BALBS, e so depois vem a "
                f"pergunta pendente. Cumprimentar nao conta como pergunta e nao "
                f'viola o "pergunte so isso": as duas coisas vao na MESMA '
                f"mensagem. Nao pule a saudacao mesmo que o cliente ja tenha "
                f"mandado o pedido inteiro de uma vez."
            )
            _memoria["saudacao_feita"] = True
            conversation_memory.save(user, _memoria)
        else:
            _diretiva_saudacao = (
                "\n\nA saudacao ja foi feita nesta conversa. NAO cumprimente de novo."
            )

        if is_confirmation and last_assistant_text:
            dynamic_prompt += (
                "\n\nCONTEXTO: a ultima mensagem do cliente e uma confirmacao/resposta "
                f"direta a sua pergunta anterior: '{last_assistant_text[-250:]}'\n"
                "Nao reinterprete como uma nova intencao isolada e preserve o que ja "
                "estava sendo tratado (produto, formato, quantidade, preco). "
                "'1 de 25 mesmo' confirma a porcao de 25 oferecida, nao 1 unidade."
            )

        if is_date_time_reply and last_assistant_text:
            perguntas_data_horario = ["data", "horario", "horário", "que dia", "que hora", "quando"]
            if any(p in last_assistant_text.lower() for p in perguntas_data_horario):
                dynamic_prompt += (
                    "\n\nCONTEXTO: o cliente esta respondendo a sua pergunta de data/horario. "
                    "Interprete assim: 'hoje, 18h' = data hoje + horario 18h; 'hoje' = so a data "
                    "(pergunte o horario); '18h' = so o horario (pergunte a data)."
                )

        # ── REGRAS DE EXECUCAO (ordem unica, sem repeticao) ──────────────────
        dynamic_prompt += (
            "\n\n=== REGRAS DE EXECUCAO (valem sobre qualquer instrucao anterior) ==="
            "\n1. FONTE DOS NUMEROS: preco vem de consultar_preco ou da BASE OFICIAL; "
            "taxa de entrega vem de consultar_frete. Nunca calcule de cabeca, nunca "
            "estime, nunca reaproveite valor de exemplo do prompt."
            "\n2. NAO SEI: se o dado nao esta na base nem no retorno de uma tool, responda "
            "que vai confirmar com a equipe (ou transfira). Inventar e o pior erro possivel."
            "\n3. MEMORIA: a FICHA acima e a verdade sobre o que o cliente ja informou. "
            "Nao pergunte o que ja esta preenchido; pergunte UM item pendente por mensagem, "
            "na ordem listada."
            "\n4. RESUMO: so apresente resumo quando a ficha nao tiver nenhuma pendencia. "
            "Para entrega, chame calculate_order(items, bairro) antes e copie o campo "
            "'resumo_formatado' do retorno, sem alterar nenhum numero."
            "\n5. ENVIO: so chame send_order_to_cardapio depois de apresentar o resumo E "
            "receber confirmacao explicita do cliente (sim/ok/pode ser/confirma). "
            "Nunca envie na mesma mensagem em que apresenta o resumo."
            "\n6. PRECO UNITARIO em items: e o valor por UNIDADE (Fritos 20,00/25 = 0,80; "
            "Assados 30,00/25 = 1,20), nunca o preco do pacote."
            "\n7. Se for entrega e o bairro nao estiver na tabela oficial, diga que precisa "
            "confirmar a taxa com a equipe - nao chute frete."
            "\n8. CALENDARIO: saudacao, dia da semana e situacao do expediente ja vem "
            "resolvidos no bloco CALENDARIO E EXPEDIENTE. Copie de la; nunca calcule de "
            "cabeca. Se o bloco disser para nao aceitar pedido para hoje, nao aceite."
            "\n8.1 DIA DO PEDIDO (corrige o BLOCO 5): onde o modelo de resumo diz "
            "'Data: [Data agendada ou \"Hoje\"]', leia 'Data: [dia que o CLIENTE disse]'. "
            "Nao existe 'ou Hoje': se o cliente nao disse o dia, voce PERGUNTA o dia. "
            "Isso vale tambem na ENTREGA, cuja lista de coleta do BLOCO 3 esquece a Data "
            "- colete Nome, Bairro, Rua com Numero, Ponto de Referencia, DATA, Horario e "
            "Forma de Pagamento. NA CONVERSA escreva o dia por extenso com a data "
            "(\"hoje, sexta-feira (19/09)\"), porque e mais claro para o cliente; NO "
            "ESPELHO do pedido a data e numerica (DD/MM/AAAA), como manda o BLOCO 5 - "
            "e ela ja vem pronta no resumo_formatado, nao reescreva. Cite dia E "
            "horario na confirmacao."
            "\n8.2 MODALIDADE: nunca assuma entrega nem retirada. Enquanto o cliente nao "
            "escolher, nao fale em endereco, bairro, frete ou taxa de entrega. Cliente "
            "PERGUNTAR sobre entrega nao e o cliente ESCOLHER entrega."
            "\n9. Quando o cliente define a quantidade (ex: '50 coxinhas'), aceite o lote "
            "correspondente e nao questione o formato."
            "\n10. SEMPRE responda primeiro a pergunta que o cliente fez (preco, frete, "
            "horario, endereco, forma de pagamento, se tem determinado produto) e so "
            "depois puxe o proximo dado do funil, na mesma mensagem. Isso vale tambem no "
            "primeiro contato: se a primeira mensagem ja traz uma pergunta, responda-a e "
            "peca o nome em seguida - nunca troque a resposta pela saudacao padrao."
        )

        # ── FICHA DO ATENDIMENTO (fica por ultimo de proposito: e o trecho que o
        # modelo le mais perto da resposta, onde pesa mais contra repergunta e
        # contra resumo apresentado antes da hora) ───────────────────────────
        dynamic_prompt += "\n\n" + conversation_memory.render_ficha(_memoria)

        # Depois da ficha de proposito: e a ultima linha que o modelo le.
        dynamic_prompt += _diretiva_saudacao

        # Cliente aceitou o espelho: a unica acao valida agora e fechar. Sem
        # isto o modelo chamava calculate_order de novo e reapresentava o MESMO
        # resumo, obrigando o cliente a confirmar duas vezes.
        _aceitou_o_resumo = _confirmou_o_resumo(last_assistant_text, text_only)
        if _aceitou_o_resumo:
            print(f"[flush] user={user} cliente confirmou o resumo - fechamento obrigatorio")
            dynamic_prompt += (
                "\n\n=== O CLIENTE ACABOU DE CONFIRMAR O RESUMO ==="
                "\nA mensagem dele e o aceite do espelho que voce mandou. "
                "NAO reapresente o resumo, NAO repita os valores e NAO chame "
                "calculate_order de novo. Chame send_order_to_cardapio AGORA, "
                "com os dados da ficha, e responda apenas a mensagem de "
                "encerramento do BLOCO 5 - sem nenhum ponto de interrogacao."
            )

        # Verifica se a IA transferiu durante a chamada (via tool)
        _transfer_done = False
        # Preenchido quando o cardapio recusa o pedido (ver FALHA_NO_ENVIO).
        _pedido_nao_entrou = ""
        # True quando send_order_to_cardapio chegou a ser chamada com sucesso
        # neste turno. Serve para pegar o caso em que o modelo ANUNCIA o
        # fechamento sem ter chamado a tool - foi o que aconteceu no segundo
        # teste do dono: ele leu "Pedido agendado!" e nada foi registrado.
        _pedido_saiu = False

        # True quando calculate_order rodou neste turno. Quando ela rodou, o
        # dinheiro da resposta ja veio de codigo e nao ha o que conferir: a
        # conferencia refaz a conta chamando calculateOrder, e repetir isso em
        # toda resposta com total custaria ~1s justamente na hora em que o
        # cliente esta esperando o resumo.
        _calculou_na_volta = False
        # O resultado da ultima calculate_order bem-sucedida deste turno.
        # Chamar a tool nao garante nada: no teste do dono de 22/09 21:27 ela
        # devolveu R$ 94,00 e o modelo escreveu R$ 54,00 na mesma volta, tendo
        # sumido com um item do pedido. Guardando a resposta da tool da para
        # conferir contra ela sem custo de rede nenhum.
        _calculo_da_volta = None

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

        # Valores realmente consultados nesta mensagem — usados para conferir
        # a resposta final contra os numeros oficiais.
        _valores_consultados: list[float] = []

        # O troco foi o CLIENTE que disse, entao repeti-lo nao e preco
        # inventado. Sem isto a auditoria acusaria "R$ 100,00 SEM ORIGEM na
        # base" toda vez que a Sandrinha confirmasse o troco, e o log que serve
        # para achar preco inventado viraria ruido.
        try:
            _valores_consultados.append(float(_memoria.get("troco_para") or 0))
        except (TypeError, ValueError):
            pass

        def _frete_handler(bairro: str = "", **kwargs) -> str:
            resultado = facts.lookup_frete(bairro)
            if resultado:
                _valores_consultados.append(float(resultado["taxa"]))
                conversation_memory.registrar_frete(user, _memoria, float(resultado["taxa"]))
            return _consultar_frete_handler(bairro=bairro, **kwargs)

        def _preco_handler(produto: str = "", formato: str = "", **kwargs) -> str:
            for item in facts.lookup_preco(produto, formato):
                if item.get("preco") is not None:
                    _valores_consultados.append(float(item["preco"]))
            return _consultar_preco_handler(produto=produto, formato=formato, **kwargs)

        def _calculate_handler(items: list = None, bairro: str = "", **kwargs) -> str:
            # Depois do aceite nao existe resumo para recalcular. Como o modelo
            # copia o resumo_formatado que esta no retorno, a unica forma de ele
            # parar de reapresenta-lo e a tool nao devolver resumo nenhum.
            if _aceitou_o_resumo:
                return json.dumps({
                    "success": False,
                    "erro": "RESUMO_JA_CONFIRMADO",
                    "instrucao": (
                        "O cliente ja confirmou este resumo. Nao ha nada a "
                        "recalcular. Chame send_order_to_cardapio agora."
                    ),
                }, ensure_ascii=False)

            # A tool se recusa a fechar conta com dado faltando. Como instrucao
            # de prompt isso era ignorado; como retorno de tool nao ha resumo
            # para o modelo copiar.
            pendentes = conversation_memory.faltando(_memoria)
            if pendentes:
                rotulos = [conversation_memory._ROTULOS[c] for c in pendentes]
                return json.dumps({
                    "success": False,
                    "erro": "DADOS_INCOMPLETOS",
                    "faltando": rotulos,
                    "instrucao": (
                        f"NAO apresente resumo nem total. Pergunte ao cliente "
                        f"apenas: {rotulos[0]}. Uma pergunta so."
                    ),
                }, ensure_ascii=False)

            # O modelo as vezes manda o ROTULO do formato no lugar do sabor
            # ("Meio cento (50 un)"), copiado da saida de consultar_preco. O que
            # sobra depois de tirar o rotulo e vazio, e ai o sabor de verdade
            # esta na ficha - que e a fonte dos outros campos deste mesmo
            # handler (modalidade, dia, pagamento, cliente, endereco).
            _itens = []
            for _item in (items or []):
                _bruto = str(_item.get("name", ""))
                _limpo = cardapio.nome_sem_rotulo_de_formato(_bruto)
                if not _limpo:
                    continue
                _novo = dict(_item, name=_limpo)
                # Com o rotulo no nome, a quantidade vem em PACOTES ("quantity":
                # 1 para um meio cento). Abaixo de 25 nao existe pedido de lote,
                # entao um numero pequeno so pode ser contagem de pacote.
                _unidades = cardapio.unidades_do_rotulo(_bruto)
                try:
                    _qtd = int(float(_novo.get("quantity", 0) or 0))
                except (ValueError, TypeError):
                    _qtd = 0
                if _unidades and 0 < _qtd < 25:
                    _novo["quantity"] = _qtd * _unidades
                    print(f"[cardápio] quantidade em pacotes: {_bruto!r} "
                          f"{_qtd} x {_unidades} un -> {_novo['quantity']} un")
                _itens.append(_novo)
            _uteis = _itens
            if (items or []) and not _uteis and _memoria.get("items"):
                print(f"[cardápio] itens do modelo eram so rotulo de formato "
                      f"({[i.get('name') for i in (items or [])]}); usando os da ficha")
                _itens = _memoria["items"]

            resultado = _calculate_order_handler(
                _itens, bairro,
                modalidade=_memoria.get("modality", ""),
                dia=_memoria.get("data_agendada", ""),
                horario=_memoria.get("horario", ""),
                pagamento=_memoria.get("payment_method", ""),
                cliente=_memoria.get("name", ""),
                endereco=_memoria.get("endereco", ""),
                troco=_memoria.get("troco_para", ""),
                referencia=_memoria.get("referencia", ""),
                data_iso=_memoria.get("data_iso", ""),
                **kwargs,
            )
            nonlocal _calculou_na_volta, _calculo_da_volta
            _calculou_na_volta = True
            try:
                dados = json.loads(resultado)
                if dados.get("success") and dados.get("total"):
                    _calculo_da_volta = dados
                for campo in ("subtotal", "delivery_fee", "return_fee", "total"):
                    if dados.get(campo) is not None:
                        _valores_consultados.append(float(dados[campo]))
                if dados.get("delivery_fee"):
                    conversation_memory.registrar_frete(user, _memoria, float(dados["delivery_fee"]))
                # O total fica na ficha porque a regra do troco ("maior que o
                # total") so pode ser conferida contra ele.
                if dados.get("total"):
                    _memoria["total"] = float(dados["total"])
                    conversation_memory.save(user, _memoria)
            except Exception:
                pass
            return resultado

        def _send_handler(**kwargs) -> str:
            # Campos que nao dependem do modelo lembrar de preencher.
            # whatsapp_message_id e a chave de idempotencia do cardapio: com ela,
            # reenviar o mesmo pedido nunca cria duplicata. Deixar isso a cargo
            # do LLM e justamente perder a garantia.
            if last_wa_msg_id:
                kwargs["whatsapp_message_id"] = last_wa_msg_id
            _retirada = _memoria.get("modality") == "retirada"
            # Na retirada o pagamento e no balcao e o prompt PROIBE perguntar
            # (BLOCO 3, TRAVA DE RETIRADA). O modelo as vezes chuta "dinheiro"
            # ao fechar; deixar esse chute passar fazia o portao de troco abaixo
            # disparar numa retirada e a Sandrinha perguntar troco quando nao
            # devia — foi o que quebrou o segundo teste do dono.
            if _retirada:
                kwargs["payment_method"] = ""
            else:
                kwargs["payment_method"] = kwargs.get("payment_method") or _memoria.get("payment_method", "")
                # 'cartao_a_definir' era o estado interno de "falta saber se e
                # debito ou credito". A pergunta acabou em 23/09; fichas antigas
                # ainda podem trazer o valor, e ele vira 'cartao'.
                if kwargs["payment_method"] == "cartao_a_definir":
                    kwargs["payment_method"] = "cartao"

            # Ponto de referencia entra no endereco: o prompt manda coleta-lo
            # (BLOCO 3) e o modelo do BLOCO 5 escreve "Rua, Numero e Ponto de
            # Referencia", mas ele ficava so na ficha e o entregador nunca via.
            _endereco_ficha = ", ".join(
                x for x in (_memoria.get("endereco", ""), _memoria.get("bairro", ""),
                            _memoria.get("referencia", "")) if x
            )
            kwargs["delivery_address"] = (
                "Retirada" if _retirada
                else (kwargs.get("delivery_address") or _endereco_ficha)
            )
            _ref = (_memoria.get("referencia") or "").strip()
            if not _retirada and _ref and _ref.lower() not in kwargs["delivery_address"].lower():
                kwargs["delivery_address"] = f"{kwargs['delivery_address']}, {_ref}"
            # Data do agendamento vem da ficha (ja resolvida em dia absoluto),
            # nao do texto livre do modelo.
            if _memoria.get("data_iso"):
                # DD/MM/AAAA: e o formato que a especificacao do desenvolvedor
                # do cardapio pede ("scheduled_date (DD/MM/AAAA)") e o mesmo
                # que o BLOCO 5 do prompt usa. Estavamos mandando ISO.
                kwargs["scheduled_date"] = (
                    cardapio._data_numerica(_memoria["data_iso"]) or _memoria["data_iso"]
                )
                kwargs["is_scheduled"] = True
                kwargs["scheduled_time"] = _memoria.get("horario", "")
            # Sabores do pacote saem dos itens da ficha.
            _itens_modelo = kwargs.get("items") or []
            _itens_ficha = _memoria.get("items") or []
            # O modelo mandou o FORMATO no lugar do sabor ("Meio cento (50 un)",
            # "Cento (100 un)"): sao linhas de preco da planilha, o de-para nao
            # tem equivalente e o pedido era recusado - o cliente ouvia que nao
            # deu para registrar e ia para a fila do humano com o pedido pronto
            # (teste do dono, 23/09 22:50). A ficha guarda o sabor que ele
            # confirmou, e e ela que vale aqui.
            if _itens_ficha and any(
                cardapio.nome_e_so_formato(i.get("name", "")) for i in _itens_modelo
            ):
                print(f"[cardápio] itens vieram como formato, nao como sabor: "
                      f"{[i.get('name') for i in _itens_modelo]} -> usando a ficha "
                      f"{[i.get('name') for i in _itens_ficha]}")
                _itens_modelo = _itens_ficha
            kwargs["items"] = _itens_modelo or _itens_ficha

            # Troco: a API nao tem campo change_for, entao ele vai na observacao
            # do pedido - unico lugar que chega a comanda do entregador.
            if (not _retirada
                    and (kwargs.get("payment_method") or "").strip().lower() == "dinheiro"
                    and not _memoria.get("troco_para")):
                if "troco" in (last_assistant_text or "").lower():
                    # Ja perguntamos e a resposta nao foi entendida. Registrar e
                    # seguir e melhor do que travar o pedido num laco.
                    _memoria["troco_para"] = "nao_informado"
                    conversation_memory.save(user, _memoria)
                else:
                    # NAO usar o prefixo FALHA_NO_ENVIO: ele e contrato com o
                    # codigo que descarta a resposta do modelo e transfere para
                    # humano. Aqui nao houve falha, falta um dado.
                    return json.dumps({
                        "success": False,
                        "erro": "FALTA_TROCO",
                        "instrucao": (
                            "Pagamento em dinheiro sem troco definido. Pergunte "
                            "'Precisa de troco para quanto? 💵' e so entao envie "
                            "o pedido."
                        ),
                    }, ensure_ascii=False)

            kwargs["notes"] = cardapio.montar_notes(
                _memoria.get("troco_para", ""),
                float(_memoria.get("total") or kwargs.get("total") or 0),
                kwargs.get("notes", ""),
                data_iso=_memoria.get("data_iso", ""),
                horario=_memoria.get("horario", ""),
            )

            resultado = _send_order_handler(**kwargs)
            if str(resultado).startswith(FALHA_NO_ENVIO):
                # Ficha NAO e fechada: o pedido nao saiu, e os dados precisam
                # continuar na mao do atendente que vai assumir.
                nonlocal _pedido_nao_entrou
                _pedido_nao_entrou = str(resultado)
                print(f"[cardápio] pedido NAO entrou para {user}: {resultado[:120]}")
                return resultado
            nonlocal _pedido_saiu
            _pedido_saiu = True
            conversation_memory.registrar_pedido_enviado(user, _memoria)
            return resultado

        _t_llm = time.monotonic()
        try:
            reply = llm.generate_reply(
                history=memory.get(user),
                system=dynamic_prompt,
                tools=[
                    CONSULTAR_FRETE_TOOL,
                    CONSULTAR_PRECO_TOOL,
                    KNOWLEDGE_TOOL,
                    TRANSFER_TOOL,
                    CALCULATE_ORDER_TOOL,
                    SEND_ORDER_TOOL,
                ],
                tool_handlers={
                    "consultar_frete": _frete_handler,
                    "consultar_preco": _preco_handler,
                    "search_knowledge": _contextual_knowledge_handler,
                    "transfer_to_human": _transfer_handler,
                    "calculate_order": _calculate_handler,
                    "send_order_to_cardapio": _send_handler,
                },
            )
        except Exception as e:
            print(f"[flush] ERRO llm p/ {user}: {e}")
            return
        _gasto_llm = time.monotonic() - _t_llm

        # O dono pediu resposta entre 5 e 10 segundos. Medido no teste dele,
        # em producao, deu 16s a 31s — e o log nao dizia onde o tempo ia. Esta
        # linha e o que permite cortar o que pesa em vez de adivinhar: o
        # debounce ja e conhecido (BUFFER_SECONDS), o preparo e tudo que vem
        # antes do modelo (ficha, RAG, calendario) e o llm inclui as tools.
        _gasto_ate_aqui = time.monotonic() - _inicio
        # webhook = o que rodou antes do debounce comecar (gravar a mensagem).
        # O log de 22/09 mostrou 3,8s escondidos aqui, maiores que o proprio
        # debounce, sem que a linha [tempo] os enxergasse.
        _gasto_webhook = max(0.0, _inicio - _chegada - BUFFER_SECONDS)
        # O total e o que o CLIENTE espera, nao o que o servidor gastou: quando
        # a resposta fica pronta antes do piso, enviar_em_blocos segura ate
        # RESPOSTA_MIN_SEG. Sem esse max o log dizia "total=3.8s" num caso em
        # que o cliente esperou 5s — numero impossivel e que tiraria a
        # confianca da linha justamente quando ela for usada para cortar tempo.
        _pronto_em = _gasto_webhook + BUFFER_SECONDS + _gasto_ate_aqui
        print(f"[tempo] user={user} total={max(_pronto_em, RESPOSTA_MIN_SEG):.1f}s "
              f"(webhook={_gasto_webhook:.1f} debounce={BUFFER_SECONDS:.1f} "
              f"preparo={_gasto_ate_aqui - _gasto_llm:.1f} "
              f"llm+tools={_gasto_llm:.1f} piso={RESPOSTA_MIN_SEG:.1f})")

        _auditar_valores(user, reply, _valores_consultados)

        # Dinheiro so sai daqui calculado em codigo. Fica DEPOIS da auditoria,
        # para o log continuar registrando o que o modelo tinha escrito, e so
        # vale enquanto o pedido nao foi enviado: depois do envio a resposta
        # fala de um pedido que ja existe, com os valores que foram gravados.
        if not _pedido_saiu and not _transfer_done:
            reply = _conferir_dinheiro(user, reply, _memoria, _calculo_da_volta,
                                       pular=_calculou_na_volta and not _calculo_da_volta)
            # Mesma ideia, para a pergunta de tamanho: com 21 unidades ou mais
            # nao ha duvida a resolver, e a frase nao pode chegar ao cliente
            # por mais que o modelo insista em escreve-la.
            reply = _conferir_tamanho(user, reply, _memoria)

        # O cliente confirmou o resumo, o modelo escreveu que fechou, mas a tool
        # nunca foi chamada — o pedido nao existe em lugar nenhum e o cliente
        # foi embora achando que pediu. Aconteceu no 2o teste do dono (22/09
        # 09:46): "Pedido agendado!" sem nenhum registro em cardapio_orders.
        if (_aceitou_o_resumo and not _pedido_saiu and not _pedido_nao_entrou
                and not _transfer_done and _parece_fechamento(reply)):
            # Antes de transferir, TENTA ENVIAR. A ficha tem tudo o que o
            # pedido precisa, e transferir obriga uma pessoa a refazer o pedido
            # inteiro — no Pix e na retirada isso e pior do que precisa ser.
            _itens_ficha = _memoria.get("items") or []
            try:
                _total_ficha = float(_memoria.get("total") or 0)
            except (TypeError, ValueError):
                _total_ficha = 0.0

            if _itens_ficha and _total_ficha > 0:
                # subtotal/frete/retorno nao sao guardados como tal: o frete vem
                # de registrar_frete e o retorno da regra do BLOCO 3.
                try:
                    _frete_ficha = float(_memoria.get("frete") or 0)
                except (TypeError, ValueError):
                    _frete_ficha = 0.0
                _retorno_ficha = cardapio.calcular_taxa_retorno(
                    _memoria.get("modality", ""), _memoria.get("payment_method", "")
                )
                print(f"[flush] user={user} modelo ANUNCIOU fechamento sem chamar "
                      f"a tool; enviando o pedido pela ficha")
                _resultado_auto = _send_handler(
                    customer_name=_memoria.get("name", ""),
                    customer_phone=user,
                    items=_itens_ficha,
                    subtotal=round(_total_ficha - _frete_ficha - _retorno_ficha, 2),
                    delivery_fee=_frete_ficha,
                    return_fee=_retorno_ficha,
                    total=_total_ficha,
                )
            else:
                # Sem total nao existe pedido: se o calculate_order nunca rodou,
                # qualquer valor aqui seria invencao. Melhor uma pessoa.
                print(f"[flush] user={user} modelo ANUNCIOU fechamento sem chamar "
                      f"a tool, e a ficha nao tem itens/total para enviar "
                      f"(itens={len(_itens_ficha)} total={_total_ficha})")
                _resultado_auto = ""

            if _pedido_saiu:
                # Deu certo: a mensagem que o modelo ja escreveu passa a ser
                # verdade. Vale inclusive no Pix — o texto do BLOCO 5 pedindo o
                # comprovante e exatamente o certo para um pedido retido em
                # aguardando_pix, que o painel mostra com o botao Validar Pix.
                print(f"[flush] user={user} pedido enviado pela ficha; "
                      f"resposta do modelo mantida")
            else:
                reply = MENSAGEM_PEDIDO_NAO_ENTROU
                perform_transfer(user, reason="modelo disse que fechou o pedido sem "
                                              "chamar send_order_to_cardapio, e o "
                                              "envio pela ficha nao deu: "
                                              f"{str(_resultado_auto)[:120]}",
                                 send_message=False)
                _transfer_done = True

        # Pedido recusado pelo cardapio: o que o modelo escreveu e descartado.
        # Ele ja respondeu "Pedido agendado!" por cima de um 401 — nao ha
        # instrucao de prompt que valha mais do que nao mentir para o cliente.
        if _pedido_nao_entrou:
            print(f"[flush] user={user} pedido recusado pelo cardapio; "
                  f"descartando resposta do modelo e transferindo")
            reply = MENSAGEM_PEDIDO_NAO_ENTROU
            if not _transfer_done:
                perform_transfer(user, reason=f"pedido nao entrou no cardapio: "
                                              f"{_pedido_nao_entrou[:160]}",
                                 send_message=False)
                _transfer_done = True

        # Se a IA transferiu, envia a resposta do LLM (knowledge/explicação) e depois a msg de transferência
        if _transfer_done:
            print(f"[flush] user={user} transferido pela IA, enviando resposta do LLM antes da transferência")
            # Envia a resposta do LLM (pode conter info do knowledge)
            if reply:
                memory.append(user, "assistant", reply)
                enviar_em_blocos(user, reply, _chegada)
            # Agora envia a mensagem de transferência
            transfer_msg = database.get_setting("transfer_message", "") or _DEFAULT_TRANSFER_MESSAGE
            time.sleep(PAUSA_MIN)
            send_text(user, transfer_msg)
            try:
                conv = database.get_or_create_conversation(user)
                database.update_conversation(conv["id"], last_message_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            except Exception:
                pass
            return

        memory.append(user, "assistant", reply)
        print(f"[flush] user={user} reply_len={len(reply)}")

        if not reply:
            return

        enviar_em_blocos(user, reply, _chegada)

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


def _marcar_lido_async(wa_message_id: str) -> None:
    """Tique azul no celular do cliente. Nada depende do resultado."""
    try:
        mark_read(wa_message_id)
    except Exception as e:
        print(f"[meta] WARN: falha ao marcar como lido: {e}")


def _avisar_digitando_async(wa_message_id: str) -> None:
    """'digitando...' no WhatsApp. Enfeite: nao pode atrasar a resposta."""
    try:
        send_typing(wa_message_id)
    except Exception as e:
        print(f"[meta] WARN: falha ao enviar typing: {e}")


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

            _chegou_em = time.monotonic()
            print(f"[webhook] from={number} ({push_name}) type={msg_type} len={len(body)} msg_id={message_id[:30]}")

            # Marca como lido em background. E uma chamada de rede a Datafy
            # (0,9s medido em producao) cujo resultado ninguem espera: o tique
            # azul no celular do cliente nao muda uma virgula da resposta.
            # Segurava o debounce sem motivo.
            threading.Thread(
                target=_marcar_lido_async, args=(str(message_id),), daemon=True,
            ).start()

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
            # A partir daqui nada mais precisa ser feito ANTES de comecar a
            # contar o debounce. O que vem abaixo — CRM, follow-ups, SSE do
            # painel — sao 1,4s de banco que o cliente estava esperando de pe,
            # embora nenhum deles mude a resposta. Disparando o buffer aqui,
            # eles passam a correr dentro dos 3s de silencio, de graca.
            #
            # save_message fica acima porque e ele quem faz a deduplicacao (a
            # Meta reentrega o mesmo wamid) e quem devolve o msg_id da midia.
            # Comecar o debounce antes dele processaria a mensagem duas vezes.
            if not e_pedido_de_reset(body):
                # Download de midia em background — antes do CRM, para ter os
                # 3s inteiros do debounce e chegar pronta em _build_user_parts.
                if saved and wa_media_id and msg_type != "text":
                    threading.Thread(
                        target=_download_media_async,
                        args=(saved["id"], wa_media_id),
                        daemon=True,
                    ).start()
                buffer.add(number, body,
                           msg_id=saved["id"] if saved else None,
                           wa_message_id=str(message_id) if message_id else "",
                           wa_media_id=wa_media_id,
                           recebido_em=_chegou_em)

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
            else:
                print(f"[webhook] MSG DUPLICADA msg_id={message_id}")

            # #reset nao entra no debounce — a palavra nao e conversa, e
            # comando — e roda DEPOIS do CRM justamente por nao ter pressa:
            # ele precisa funcionar quando a conversa esta em modo humano, que
            # e onde handle_flush desiste logo no inicio.
            if e_pedido_de_reset(body):
                try:
                    perform_reset(number, origem="cliente")
                except Exception as e:
                    print(f"[reset] ERRO ao reiniciar {number}: {e}")
                continue

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


@app.get("/api/settings/reset")
@login_required
def api_get_reset_settings():
    return jsonify({
        "reset_keyword": palavra_de_reset(),
        "reset_message": database.get_setting("reset_message", "") or _MENSAGEM_RESET_PADRAO,
    })


@app.put("/api/settings/reset")
@login_required
def api_set_reset_settings():
    data = request.get_json(silent=True) or {}
    if "reset_keyword" in data:
        chave = str(data["reset_keyword"]).strip().lower()
        # Palavra de uma peca so: o reconhecimento e por token, entao uma chave
        # com espaco nunca seria reconhecida.
        if not chave or len(chave.split()) != 1:
            return jsonify({"error": "reset_keyword deve ser uma palavra unica, sem espacos"}), 400
        database.set_setting("reset_keyword", chave)
    if "reset_message" in data:
        msg = str(data["reset_message"]).strip()
        if not msg:
            return jsonify({"error": "reset_message não pode ser vazio"}), 400
        database.set_setting("reset_message", msg)
    return jsonify({
        "ok": True,
        "reset_keyword": palavra_de_reset(),
        "reset_message": database.get_setting("reset_message", "") or _MENSAGEM_RESET_PADRAO,
    })


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
    """Mesmo reset da palavra-chave, disparado pelo painel.

    Antes isto so apagava a ficha (conversation_state), o que deixava a
    conversa em modo humano e com o historico antigo: quem clicava aqui depois
    de um transbordo continuava sem a Sandrinha. Agora faz o reset inteiro.
    Para o comportamento antigo, silencioso, passe {"avisar": false}.
    """
    data = request.get_json(silent=True) or {}
    try:
        perform_reset(phone, origem="painel", avisar=bool(data.get("avisar", True)))
        return jsonify({"ok": True, "message": f"Atendimento de {phone} reiniciado"})
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
        
        # Atualiza o texto no knowledge no MESMO formato do carregamento
        # oficial: uma linha por registro, colunas separadas por ' | '.
        content_parts = []
        for sheet_data in sheets:
            texto = parse_knowledge.sheet_rows_to_text(
                sheet_data.get("name", "Dados"),
                sheet_data.get("headers", []),
                sheet_data.get("rows", []),
            )
            if texto:
                content_parts.append(texto)

        full_content = "\n\n".join(content_parts)
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
    try:
        knowledge_rag.save_knowledge_raw(filename, content, description=description)
    except knowledge_rag.ConteudoCorrompido as e:
        print(f"[knowledge] RECUSADO {filename}: {e}")
        return jsonify({"error": f"Conteudo recusado - {e}"}), 400
    print(f"[knowledge] criado: {filename}")
    return jsonify({"ok": True, "filename": filename})


@app.put("/api/knowledge/<filename>")
@login_required
def api_knowledge_update(filename):
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    description = data.get("description", "").strip()
    try:
        knowledge_rag.save_knowledge_raw(filename, content, description=description)
    except knowledge_rag.ConteudoCorrompido as e:
        # Barra aqui: salvar o texto remontado por cima do original foi o que
        # destruiu o prompt operacional uma vez. O arquivo local tambem nao e
        # tocado, entao nada se perde.
        print(f"[knowledge] RECUSADO {filename}: {e}")
        return jsonify({"error": f"Conteudo recusado - {e}"}), 400

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
    """Extrai texto de um arquivo .xlsx enviado via upload (todas as abas).

    Usa o mesmo conversor do carregamento oficial: uma linha por registro. O
    formato antigo (df.to_string) alinhava colunas com dezenas de espacos e as
    linhas eram cortadas na indexacao, quebrando precos e taxas.
    """
    try:
        import io
        return parse_knowledge.xlsx_to_text(io.BytesIO(file_storage.read()))
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
    """Recarrega tudo que depende do knowledge (prompt, base, tabelas, RAG)."""
    global FULL_SYSTEM_PROMPT
    parse_knowledge.reload_cache()
    knowledge_rag.invalidate_knowledge_caches()
    facts.invalidate()

    FULL_SYSTEM_PROMPT = _get_current_system_prompt()
    dados = facts.get()
    print(
        f"[knowledge] recarregado: prompt={len(FULL_SYSTEM_PROMPT)} chars, "
        f"{len(dados['bairros'])} bairros, {len(dados['produtos'])} produtos"
    )
    return jsonify({
        "ok": True,
        "prompt_chars": len(FULL_SYSTEM_PROMPT),
        "bairros": len(dados["bairros"]),
        "produtos": len(dados["produtos"]),
    })


@app.get("/api/knowledge/health")
@login_required
def api_knowledge_health():
    """Diagnostico da base: o que o agente realmente le antes de responder.

    Aponta arquivo sem documento original, texto com trechos duplicados e
    tabelas que nao foram reconhecidas — as tres coisas que fazem o agente
    responder errado sem dar nenhum erro visivel.
    """
    import knowledge_repair

    arquivos = []
    for item in knowledge_rag.list_files_with_status():
        filename = item["filename"]
        documento = database.get_knowledge_document(filename)
        conteudo = documento or knowledge_rag.get_file_content(filename)
        arquivos.append({
            "filename": filename,
            "eh_prompt": knowledge_rag.is_prompt_file(filename),
            "chunks": item.get("chunks", 0),
            "status": item.get("status", ""),
            "chars": len(conteudo),
            "tem_documento_original": bool(documento),
            "texto_duplicado": knowledge_repair.looks_corrupted(conteudo),
        })

    dados = facts.get()
    problemas = []
    for a in arquivos:
        if not a["tem_documento_original"]:
            problemas.append(f"{a['filename']}: sem documento original — rode reindex_knowledge.py")
        if a["texto_duplicado"]:
            problemas.append(f"{a['filename']}: texto com trechos duplicados — rode reindex_knowledge.py")
        if a["status"] != "indexed":
            problemas.append(f"{a['filename']}: status '{a['status']}' (busca semantica incompleta)")
    if not dados["bairros"]:
        problemas.append("tabela de bairros vazia — consultar_frete nao vai funcionar")
    if not dados["produtos"]:
        problemas.append("tabela de produtos vazia — consultar_preco nao vai funcionar")

    return jsonify({
        "ok": not problemas,
        "arquivos": arquivos,
        "bairros_carregados": len(dados["bairros"]),
        "produtos_carregados": len(dados["produtos"]),
        "contexto_injetado_chars": len(knowledge_rag.get_full_knowledge_content()),
        "problemas": problemas,
    })


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
    try:
        knowledge_rag.save_knowledge_raw(
            filename, content, description=f"Prompt do agente {filename.replace('.md','')}"
        )
    except knowledge_rag.ConteudoCorrompido as e:
        print(f"[knowledge] RECUSADO {filename}: {e}")
        return jsonify({"error": f"Conteudo recusado - {e}"}), 400
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
    ("OPENAI_TEMPERATURE", "Temperatura (0.0 a 2.0)", False),
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


def _exige_template_meta(days, hours, use_meta_template, meta_template_name) -> str:
    """Erro quando o prazo passa das 24h e nao ha template aprovado.

    Fora da janela de 24 horas o WhatsApp so entrega template aprovado: uma
    automacao de dias com mensagem de texto falha em TODO envio e o usuario so
    descobre pelo log, dias depois. Melhor recusar na hora de salvar.
    """
    try:
        total_horas = int(days or 0) * 24 + int(hours or 0)
    except (TypeError, ValueError):
        return ""
    if total_horas < 24:
        return ""
    if int(use_meta_template or 0) and str(meta_template_name or "").strip():
        return ""
    return (
        f"Esta automacao dispara {total_horas}h depois da ultima interacao, "
        "fora da janela de 24 horas do WhatsApp. Nesse prazo a Meta so entrega "
        "template aprovado: marque 'Usar template aprovado pela Meta' e escolha "
        "um template, ou reduza o prazo para menos de 24 horas."
    )


@app.post("/api/crm/automations")
@login_required
def api_crm_automations_create():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    problema = _exige_template_meta(
        data.get("days", 0), data.get("hours", 0),
        data.get("use_meta_template", 0), data.get("meta_template_name", ""),
    )
    if problema:
        return jsonify({"error": problema}), 400
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
    # Valida o estado FINAL: o PUT pode mandar so alguns campos, e o resto
    # continua valendo do que ja esta salvo.
    atual = database.get_automation(auto_id) or {}
    final = {**atual, **data}
    problema = _exige_template_meta(
        final.get("days", 0), final.get("hours", 0),
        final.get("use_meta_template", 0), final.get("meta_template_name", ""),
    )
    if problema:
        return jsonify({"error": problema}), 400
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


@app.post("/api/cardapio/orders/<int:order_id>/validar-pix")
@login_required
def api_cardapio_order_validar_pix(order_id):
    """Confere o comprovante do Pix e so entao manda o pedido ao cardapio.

    Pela regra do dono, pedido no Pix so grava com human_confirmed=true. O
    agente deixa o pedido em 'aguardando_pix' e quem valida e uma pessoa, aqui.
    """
    order = database.obter_cardapio_order(order_id)
    if not order:
        return jsonify({"error": "Pedido nao encontrado"}), 404
    if order.get("status") != "aguardando_pix":
        return jsonify({"error": f"Pedido nao esta aguardando Pix (status: {order.get('status')})"}), 400

    items = order.get("items")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []

    resultado = cardapio.criar_pedido(
        customer_name=order.get("customer_name", ""),
        customer_phone=order.get("customer_phone", ""),
        items=items or [],
        total=float(order.get("total") or 0),
        customer_confirmed=True,
        human_confirmed=True,
        delivery_address=order.get("delivery_address", ""),
        payment_method=order.get("payment_method", ""),
        subtotal=float(order.get("subtotal") or 0),
        delivery_fee=float(order.get("delivery_fee") or 0),
        return_fee=float(order.get("return_fee") or 0),
        notes=order.get("notes", ""),
        whatsapp_message_id=order.get("whatsapp_message_id", ""),
    )

    if not resultado.get("success"):
        database.atualizar_cardapio_order(
            order_id, error_message=resultado.get("error", ""), api_response=resultado
        )
        return jsonify({"error": resultado.get("error", "Falha ao enviar")}), 502

    database.atualizar_cardapio_order(
        order_id,
        # Mesma razao do status na criacao: "enviado" e o que sabemos: o
        # pedido saiu daqui e o cardapio aceitou. O que acontece depois so o
        # painel do cardapio mostra.
        status="enviado",
        order_id_externo=resultado.get("order_id", ""),
        display_id=resultado.get("display_id", ""),
        api_response=resultado,
        error_message="",
    )
    print(f"[cardápio] Pix validado por humano, pedido {resultado.get('display_id')} enviado")
    return jsonify({"ok": True, "display_id": resultado.get("display_id", ""),
                    "status": resultado.get("status", "pendente")})


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


@app.get("/api/cardapio/health")
@login_required
def api_cardapio_health():
    """Diz se o cardapio digital aceita a nossa chave, sem criar pedido.

    Mesmo contrato de /api/knowledge/health: sempre HTTP 200, e a falha aparece
    em 'ok' e 'problemas'. Antes disto, a unica forma de saber que a chave
    estava errada era o pedido de um cliente real falhar no WhatsApp.
    """
    return jsonify(cardapio.verificar_credencial())


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
