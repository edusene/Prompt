"""
test_agente.py — Simulador de conversa que exercita o caminho real do agente.

Roda handle_flush() de verdade (mesmo prompt, mesmas tools, mesmo LLM), mas com
WhatsApp e escrita no banco substituidos por stubs em memoria: nenhuma mensagem
sai e nenhum contato/pedido e criado no CRM. A base de conhecimento e lida de
verdade — e ela que esta sendo testada.

Uso:
    python test_agente.py                  # bateria padrao de perguntas
    python test_agente.py "sua pergunta"   # pergunta avulsa
    python test_agente.py --fluxo          # conversa completa de pedido
    python test_agente.py --chat           # cliente manda tudo na primeira mensagem
    python test_agente.py --extracao       # so a extracao: sem LLM, sem rede, sem banco
"""

import sys

from dotenv import load_dotenv

load_dotenv(override=True)

import app  # noqa: E402
import conversation_memory  # noqa: E402
import database  # noqa: E402
import facts  # noqa: E402
import memory  # noqa: E402

TELEFONE = "5569000000000"

_enviadas: list[str] = []
_ficha: dict = {}
_prompt_capturado = {"system": ""}


def _instalar_stubs():
    """Isola o teste: nada sai para o WhatsApp, nada e gravado no CRM."""
    app.send_text = lambda phone, text: (_enviadas.append(text), {"ok": True})[1]
    app.send_typing = lambda *a, **k: None
    app.send_msg_out_to_panel = lambda *a, **k: None
    app.perform_transfer = lambda phone, reason="", send_message=True: (
        print(f"  [transferencia solicitada: {reason}]"), True
    )[1]

    database.get_contact = lambda phone: {"phone": phone, "name": "", "auto_mode": 1}
    database.upsert_contact = lambda phone, name="", **k: {"phone": phone, "name": name}
    database.get_or_create_conversation = lambda phone: {"id": 0, "contact_id": 0}
    database.update_conversation = lambda *a, **k: None
    database.reset_unread = lambda *a, **k: None
    database.save_message = lambda *a, **k: None
    database.get_recent_messages = lambda phone, limit=20: []
    database.get_setting = lambda key, default="": {"agent_mode": "1"}.get(key, default)
    # Sem este stub o teste tenta gravar pedido em cardapio_orders no banco real
    database.criar_cardapio_order = lambda *a, **k: print("  [pedido registrado (simulado)]")

    # E sem este, com o modo producao ligado, a bateria de teste criaria pedido
    # DE VERDADE no cardapio digital — a fabrica veria a comanda na tela.
    # calculateOrder continua real: ele so calcula, e e justamente o caminho
    # que se quer testar.
    app.cardapio.criar_pedido = lambda **k: (
        print(f"  [TESTE — PEDIDO NAO FOI PARA A FABRICA] createOrderFromAgent "
              f"bloqueado: {k.get('customer_name')} R$ {k.get('total')}"),
        {"success": True, "order_id": "PEDIDO-DE-TESTE",
         "display_id": "TESTE-NAO-PREPARAR", "status": "simulado"},
    )[1]

    conversation_memory.load = lambda phone: dict(_ficha) or conversation_memory._empty()
    conversation_memory.save = lambda phone, data: _ficha.update(data)

    original = app.llm.generate_reply

    def _capturando(history, system, tools=None, tool_handlers=None):
        _prompt_capturado["system"] = system
        return original(history, system, tools=tools, tool_handlers=tool_handlers)

    app.llm.generate_reply = _capturando


def enviar(texto: str) -> str:
    _enviadas.clear()
    print(f"\nCLIENTE: {texto}")
    app.handle_flush(TELEFONE, [{"text": texto, "wa_message_id": ""}])
    resposta = "\n".join(_enviadas)
    print(f"SANDRINHA: {resposta or '(sem resposta)'}")
    return resposta


PERGUNTAS = [
    "qual o valor da porcao de coxinha?",
    "quanto fica a entrega no bairro Olaria?",
    "voces entregam no Marte City?",
    "quanto custa o cento de brigadeiro?",
    "qual o horario de funcionamento de voces?",
    "qual a chave pix de voces?",
    "voces aceitam vale refeicao?",
    "qual o endereco da fabrica pra retirada?",
]

FLUXO = [
    "oi, tudo bem?",
    "meu nome e Eduardo",
    "quero 50 coxinhas",
    "vai ser entrega",
    "bairro Aponia, rua das flores 120",
    "hoje as 18h",
    "vou pagar no pix",
]

# Conversa real (chat.txt, 18/09): o cliente manda tudo de uma vez. Antes o
# subtotal saia R$ 0,00 e o agente respondia "houve um erro na consulta dos precos".
FLUXO_TUDO_DE_UMA_VEZ = [
    "Oi Sandrinha, sou o Balbino. Quero pedir 50 coxinhas e 25 kibes pra entregar "
    "hoje as 18h na Av. Calama, 3234, bairro Olaria. Vou pagar no cartao.",
    "Credito",
    "Sim",
]


# Tabelas da bateria --extracao. Cada defeito que apareceu num teste do dono
# vira uma linha aqui, para nao voltar.
_CASOS_PAGAMENTO = [
    # A frase que a propria Sandrinha oferece. Virava "dinheiro" e mandava o
    # entregador levar troco para quem ia pagar no cartao (teste de 22/09).
    ("Vou pagar no Cartão na entrega", "cartao"),
    ("Cartao na entrega", "cartao"),
    ("Dinheiro na entrega", "dinheiro"),
    ("vou pagar na entrega", "dinheiro"),
    ("quero pagar no credito na entrega", "cartao_credito"),
    ("no débito na entrega", "cartao_debito"),
    ("vou mandar o pix", "pix"),
    ("Pix antecipado", "pix"),
    ("maquininha", "cartao"),
    ("CARTAO", "cartao"),
    ("cartao de credito", "cartao_credito"),
    ("aceita cartao? vou pagar em dinheiro", "dinheiro"),
    ("nao e dinheiro, e no cartao", "cartao"),
    ("quero 50 coxinhas", ""),
]

_CASOS_HORARIO = [
    # "20 horas" nao era entendido: o dono digitou 4x e a Sandrinha
    # reperguntou 3x seguidas (teste de 22/09).
    ("20 HORAS", "20h"), ("as 20 horas", "20h"), ("pode ser as 19 horas", "19h"),
    ("20h", "20h"), ("18h", "18h"), ("18:30", "18h30"), ("hoje as 18h", "18h"),
    ("8h15", "08h15"),
    # Numero de rua nao pode virar horario.
    ("Av. Calama, 3234", ""), ("Rua Salgado Filho, 2125", ""), ("2125", ""),
    ("50 coxinhas", ""), ("quero 100 salgados", ""),
    # Nem pergunta de prazo de entrega.
    ("demora 1 hora?", ""), ("chega em 2 horas", ""), ("dentro de 2 horas", ""),
    ("meia hora", ""),
]

_CASOS_REFERENCIA = [
    # A locucao faz parte do texto: "em frente ao mercado" virava "ao mercado",
    # e sem virgula no fim da frase nao extraia nada.
    ("em frente ao mercado", "em frente ao mercado"),
    ("perto do posto Ipiranga", "perto do posto Ipiranga"),
    ("ao lado da farmacia", "ao lado da farmacia"),
    ("fica atras do colegio Dom Bosco", "atras do colegio Dom Bosco"),
    ("referencia: portao azul", "portao azul"),
    ("ponto de referencia em frente a escola", "em frente a escola"),
    ("Av Calama 100, proximo a Havan", "proximo a Havan"),
    ("quero 50 coxinhas", ""),
]


def bateria_extracao() -> int:
    """Confere a extracao deterministica. Nao chama LLM, rede nem banco."""
    from agents.conversation import (detectar_pagamento, detectar_horario,
                                     detectar_referencia)
    falhas = []

    def rodar(titulo, casos, fn):
        print(f"\n=== {titulo} ===")
        for entrada, esperado in casos:
            obtido = fn(entrada)
            ok = obtido == esperado
            if not ok:
                falhas.append(f"{titulo}: {entrada!r} -> {obtido!r}, esperado {esperado!r}")
            print(f"  {'OK  ' if ok else 'FALHA'} {entrada!r:<42} -> {obtido!r}")

    rodar("forma de pagamento", _CASOS_PAGAMENTO, detectar_pagamento)
    rodar("horario", _CASOS_HORARIO, detectar_horario)
    rodar("ponto de referencia", _CASOS_REFERENCIA, detectar_referencia)

    print("\n" + "=" * 70)
    if falhas:
        for f in falhas:
            print(f"  FALHA {f}")
        print(f"\n{len(falhas)} falha(s)")
        return 1
    print("extracao: tudo certo")
    return 0


def main() -> int:
    if "--extracao" in sys.argv:
        return bateria_extracao()

    if not facts.is_loaded():
        print("ERRO: base de fatos vazia — rode 'python reindex_knowledge.py --embed' antes.")
        return 1

    _instalar_stubs()
    memory.reset(TELEFONE)
    _ficha.clear()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--chat" in sys.argv:
        roteiro = FLUXO_TUDO_DE_UMA_VEZ
    elif "--fluxo" in sys.argv:
        roteiro = FLUXO
    elif args:
        roteiro = args
    else:
        roteiro = PERGUNTAS

    for texto in roteiro:
        if "--fluxo" not in sys.argv and "--chat" not in sys.argv:
            memory.reset(TELEFONE)
            _ficha.clear()
        enviar(texto)

    print("\n" + "=" * 70)
    print(f"Tamanho do system prompt: {len(_prompt_capturado['system'])} chars")
    if _ficha:
        print("Ficha final:", {k: v for k, v in _ficha.items() if v})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
