"""
conversation_memory.py — Memoria estruturada do atendimento, por telefone.

O historico de mensagens sozinho nao e memoria confiavel: a janela e curta, o
modelo reinterpreta mensagens antigas e volta a perguntar o que ja foi dito.
Aqui cada dado coletado (nome, itens, modalidade, endereco, bairro, data,
horario, pagamento) fica guardado no banco e volta ao prompt como uma FICHA
curta e explicita, junto com a lista do que ainda falta.

Efeito pratico: o agente nao repergunta o que ja sabe nem inventa o que nao foi
dito — para cada campo ele ve "preenchido" ou "FALTA PERGUNTAR".
"""

import time

import database

# Campos que o cliente pode corrigir durante a conversa (a fala mais recente vence)
_CAMPOS_REVISAVEIS = (
    "name", "modality", "bairro", "endereco", "referencia",
    "horario", "data_agendada", "payment_method",
)

_CAMPOS = _CAMPOS_REVISAVEIS + ("items",)

_ROTULOS = {
    "name": "Nome do cliente",
    "items": "Itens do pedido",
    "modality": "Modalidade (entrega/retirada)",
    "bairro": "Bairro",
    "endereco": "Endereco (rua e numero)",
    "referencia": "Ponto de referencia",
    "data_agendada": "Data",
    "horario": "Horario",
    "payment_method": "Forma de pagamento",
}

# O que e obrigatorio antes do resumo, por modalidade
_OBRIGATORIOS_COMUNS = ("name", "items", "modality", "data_agendada", "horario", "payment_method")
_OBRIGATORIOS_ENTREGA = ("bairro", "endereco")


def _empty() -> dict:
    return {
        "name": "",
        "items": [],
        "modality": "",
        "bairro": "",
        "endereco": "",
        "referencia": "",
        "horario": "",
        "data_agendada": "",
        "payment_method": "",
        "frete": None,
        "pedido_enviado": False,
        "updated_at": "",
    }


def load(phone: str) -> dict:
    """Carrega a memoria estruturada do cliente (vazia se nao houver)."""
    data = _empty()
    try:
        stored = database.get_conversation_state(phone)
    except Exception as e:
        print(f"[memoria] WARN falha ao carregar {phone}: {e}")
        stored = None
    if stored:
        for key in data:
            if key in stored and stored[key] not in (None, ""):
                data[key] = stored[key]
    return data


def save(phone: str, data: dict) -> None:
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        database.save_conversation_state(phone, data)
    except Exception as e:
        print(f"[memoria] WARN falha ao salvar {phone}: {e}")


def reset(phone: str) -> None:
    try:
        database.delete_conversation_state(phone)
    except Exception as e:
        print(f"[memoria] WARN falha ao limpar {phone}: {e}")


def merge(memoria: dict, extraido: dict, novo_da_mensagem: dict | None = None) -> tuple[dict, list]:
    """Funde o que foi extraido da mensagem na memoria acumulada.

    `extraido` vem da extracao feita ja considerando o que a memoria tem (so
    preenche lacunas). `novo_da_mensagem` vem de uma extracao sem estado: serve
    para o cliente CORRIGIR um dado ("na verdade e no Olaria"), caso em que o
    valor novo substitui o antigo.
    """
    mudancas = []

    for campo, valor in (extraido or {}).items():
        if campo not in _CAMPOS or not valor:
            continue
        if not memoria.get(campo):
            memoria[campo] = valor
            mudancas.append(f"{campo}={valor}")

    for campo in _CAMPOS_REVISAVEIS:
        novo = (novo_da_mensagem or {}).get(campo)
        if novo and memoria.get(campo) and novo != memoria[campo]:
            print(f"[memoria] {campo}: '{memoria[campo]}' -> '{novo}' (cliente corrigiu)")
            memoria[campo] = novo
            mudancas.append(f"{campo}={novo}")

    itens_novos = (novo_da_mensagem or {}).get("items")
    if itens_novos and itens_novos != memoria.get("items"):
        memoria["items"] = itens_novos
        mudancas.append(f"items={itens_novos}")

    return memoria, mudancas


def faltando(memoria: dict) -> list[str]:
    """Campos obrigatorios ainda nao coletados, na ordem do funil."""
    obrigatorios = list(_OBRIGATORIOS_COMUNS)
    if memoria.get("modality") == "entrega":
        # endereco so faz sentido depois de saber que e entrega
        idx = obrigatorios.index("modality") + 1
        obrigatorios[idx:idx] = list(_OBRIGATORIOS_ENTREGA)
    return [c for c in obrigatorios if not memoria.get(c)]


def _formatar_itens(items) -> str:
    if not items:
        return ""
    partes = []
    for item in items:
        if isinstance(item, dict):
            qtd = item.get("quantity", "")
            nome = item.get("name", "")
            partes.append(f"{qtd} x {nome}".strip())
        else:
            partes.append(str(item))
    return "; ".join(p for p in partes if p)


def render_ficha(memoria: dict) -> str:
    """Bloco compacto para o system prompt: o que ja sei e o que falta."""
    linhas = []
    for campo in ("name", "items", "modality", "bairro", "endereco", "referencia",
                  "data_agendada", "horario", "payment_method"):
        valor = memoria.get(campo)
        if campo == "items":
            valor = _formatar_itens(valor)
        if valor:
            linhas.append(f"- {_ROTULOS[campo]}: {valor}")

    if memoria.get("frete") is not None:
        linhas.append(f"- Frete ja calculado: R$ {float(memoria['frete']):.2f}".replace(".", ","))

    pendentes = faltando(memoria)
    bloco = ["=== FICHA DESTE ATENDIMENTO (memoria do cliente) ==="]
    if linhas:
        bloco.append("JA CONFIRMADO PELO CLIENTE - nao pergunte de novo, nao altere:")
        bloco += linhas
    else:
        bloco.append("Nenhum dado coletado ainda.")

    if memoria.get("items"):
        bloco.append(
            "Os itens acima sao o que o cliente pediu. O preco deles vem de "
            "consultar_preco/calculate_order - nunca do seu calculo."
        )

    if pendentes:
        bloco.append("AINDA FALTA COLETAR, nesta ordem:")
        bloco += [f"- {_ROTULOS[c]}" for c in pendentes]
        bloco.append(
            f"PROXIMA PERGUNTA OBRIGATORIA: {_ROTULOS[pendentes[0]]}. "
            "Pergunte SO isso nesta mensagem."
        )
        bloco.append(
            "PROIBIDO nesta mensagem: apresentar resumo do pedido, informar total "
            "ou chamar send_order_to_cardapio - a ficha ainda tem pendencias."
        )
    else:
        bloco.append(
            "Ficha completa. Agora: chame calculate_order com os itens e o bairro, "
            "copie o 'resumo_formatado' do retorno e peca a confirmacao do cliente. "
            "So chame send_order_to_cardapio depois que ele confirmar."
        )
    return "\n".join(bloco)


def registrar_frete(phone: str, memoria: dict, valor: float) -> None:
    memoria["frete"] = valor
    save(phone, memoria)


def registrar_pedido_enviado(phone: str, memoria: dict) -> None:
    memoria["pedido_enviado"] = True
    save(phone, memoria)
