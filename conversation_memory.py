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
    "horario", "data_agendada", "data_iso", "payment_method",
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

# O que e obrigatorio antes do resumo, por modalidade.
# Pagamento NAO entra aqui: na retirada e no balcao, e o prompt operacional
# proibe perguntar. Ele e exigido so na entrega (ver faltando()).
_OBRIGATORIOS_COMUNS = ("name", "items", "modality", "data_agendada", "horario")
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
        # Dia resolvido (AAAA-MM-DD). data_agendada e o texto que vai para a
        # tela ("hoje, sexta-feira (19/09)"); data_iso e o dia de verdade, usado
        # para validar o expediente.
        "data_iso": "",
        "payment_method": "",
        "frete": None,
        "pedido_enviado": False,
        # A saudacao sai uma vez so, e na primeira resposta.
        "saudacao_feita": False,
        # Aviso de "estamos fechados" ja dado nesta conversa.
        "aviso_horario_dado": False,
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


# Como a pergunta do agente aparece, por campo que ela preenche. Serve para
# ler a resposta seca do cliente ("Eduardo", "Olaria"), que as regras de
# extracao nao pegam por nao ter nenhuma palavra-chave em volta. Sem isto o
# agente perguntava, o cliente respondia, e ele perguntava de novo.
_PERGUNTAS_POR_CAMPO = {
    "name": ("seu nome", "qual e o nome", "qual é o nome", "como posso te chamar",
             "com quem eu falo", "com quem falo"),
    "bairro": ("qual bairro", "qual o bairro", "qual é o bairro", "qual e o bairro",
               "nome do bairro", "em que bairro", "de que bairro"),
}

# Respostas curtas que nunca sao um nome proprio nem um bairro.
_NAO_E_RESPOSTA = {
    "sim", "nao", "não", "ok", "blz", "beleza", "pode", "pode ser", "isso",
    "certo", "claro", "obrigado", "obrigada", "entrega", "retirada", "hoje",
    "amanha", "amanhã", "pix", "dinheiro", "cartao", "cartão", "debito",
    "débito", "credito", "crédito",
}


def capturar_resposta_direta(memoria: dict, pergunta_anterior: str, texto: str) -> str:
    """Preenche o campo que a pergunta anterior pediu, a partir da resposta seca.

    Retorna o nome do campo preenchido, ou "" se a mensagem nao for uma
    resposta direta. So age em campo ainda vazio: nunca sobrescreve um dado
    que o cliente ja tinha dado.
    """
    pergunta = (pergunta_anterior or "").lower()
    candidato = (texto or "").strip().strip(".!,;:")
    if not pergunta or not candidato:
        return ""
    if candidato.lower() in _NAO_E_RESPOSTA or any(c.isdigit() for c in candidato):
        return ""
    if not (0 < len(candidato.split()) <= 3) or len(candidato) < 2:
        return ""

    for campo, marcas in _PERGUNTAS_POR_CAMPO.items():
        if memoria.get(campo):
            continue
        if any(m in pergunta for m in marcas):
            memoria[campo] = candidato.title()
            return campo
    return ""


def faltando(memoria: dict) -> list[str]:
    """Campos obrigatorios ainda nao coletados, na ordem do funil."""
    obrigatorios = list(_OBRIGATORIOS_COMUNS)
    if memoria.get("modality") == "entrega":
        # endereco so faz sentido depois de saber que e entrega
        idx = obrigatorios.index("modality") + 1
        obrigatorios[idx:idx] = list(_OBRIGATORIOS_ENTREGA)
        # Na retirada o pagamento e no balcao e perguntar e proibido; so a
        # entrega precisa da forma de pagamento antes do resumo.
        obrigatorios.append("payment_method")
    return [c for c in obrigatorios if not memoria.get(c)]


def _itens_ambiguos(memoria: dict) -> list[str]:
    """Itens cujo nome serve para o sabor de pacote E para o salgado grande.

    "Enroladinho de Salsicha" e "Rissole de Carne" existem nas duas familias do
    cardapio, e "coxinha" casa tanto com o sabor quanto com a Coxinha de Frango
    com Catupiry. A diferenca de preco e grande (R$ 0,80/un no pacote contra
    R$ 9,00/un avulso), entao o agente confirma em vez de escolher sozinho.

    So vale para quantidade ABAIXO de 25: de 25 para cima a leitura de pacote e
    segura (ninguem pede 50 coxinhas grandes a R$ 9,00 cada) e perguntar seria
    so atrito. A duvida real e o pedido pequeno, onde as duas leituras fazem
    sentido.
    """
    try:
        import facts
    except Exception:
        return []

    avisos = []
    for item in memoria.get("items") or []:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("name", ""))
        try:
            qtd = int(float(item.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            continue
        if qtd >= 25:
            continue
        try:
            oficial = facts.resolve_unit_price(nome, qtd)
        except Exception:
            continue
        if oficial and oficial.get("ambiguo"):
            avisos.append(
                f"{qtd} x '{nome}': pode ser {oficial['item_oficial']} "
                f"(R$ {oficial['preco_unitario']:.2f}/un) ou "
                f"{oficial['alternativa']}"
            )
    return avisos


def pendencia_de_item(memoria: dict) -> str:
    """Duvida sobre O QUE o cliente pediu. Tem prioridade sobre o funil.

    Confirmar o produto depois de ja ter coletado endereco e horario obriga a
    refazer a conta do pedido inteiro, entao esta pergunta fura a fila.
    """
    ambiguos = _itens_ambiguos(memoria)
    if not ambiguos:
        return ""
    return (
        "ITEM AMBIGUO - o mesmo nome existe como sabor de pacote e como salgado "
        "grande avulso, com precos bem diferentes: " + "; ".join(ambiguos)
        + ". Pergunte ao cliente qual das duas opcoes ele quer, citando as duas "
        "com o preco, ANTES de seguir para entrega/retirada."
    )


def pendencia_de_detalhe(memoria: dict) -> str:
    """Campo preenchido que ainda precisa de um detalhe, nao de nova coleta."""
    if memoria.get("payment_method") == "cartao_a_definir":
        return (
            "O cliente ja disse que paga com CARTAO. Falta so saber se e DEBITO "
            "ou CREDITO - pergunte exatamente isso, nunca 'qual a forma de pagamento'."
        )
    return ""


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
    detalhe = pendencia_de_detalhe(memoria)
    duvida_item = pendencia_de_item(memoria)
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

    if duvida_item:
        bloco.append(duvida_item)
    if detalhe:
        bloco.append(detalhe)

    # Duas coisas nunca podem ser preenchidas por deducao: a modalidade e o
    # dia. Sao as duas que o agente vinha assumindo sozinho.
    if not memoria.get("modality"):
        bloco.append(
            "MODALIDADE NAO DEFINIDA: o cliente ainda nao disse se e ENTREGA ou "
            "RETIRADA. Ate ele dizer, e PROIBIDO falar em endereco, bairro, "
            "frete ou taxa de entrega, e proibido inclui-los no resumo. "
            "Cliente PERGUNTAR sobre entrega nao e o cliente ESCOLHER entrega."
        )
    if not memoria.get("data_agendada"):
        bloco.append(
            "DIA NAO DEFINIDO: o cliente nao disse para qual DIA e o pedido. "
            'E PROIBIDO assumir "hoje". Pergunte o dia.'
        )
    else:
        bloco.append(
            f"DIA DO PEDIDO: {memoria['data_agendada']}. Escreva sempre assim, "
            'com dia da semana e data — nunca so "hoje". O cliente precisa VER '
            "o dia que esta confirmando, e a confirmacao final cita dia E horario."
        )

    if duvida_item:
        # Fura a fila do funil: o que o cliente pediu precisa estar definido
        # antes de coletar entrega, dia e horario.
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: esclarecer o ITEM AMBIGUO acima. "
            "Nao pergunte modalidade, endereco, dia, horario nem pagamento "
            "nesta mensagem, e nao apresente resumo."
        )
    elif pendentes:
        bloco.append("AINDA FALTA COLETAR, nesta ordem:")
        bloco += [f"- {_ROTULOS[c]}" for c in pendentes]
        bloco.append(
            f"PROXIMA PERGUNTA OBRIGATORIA: {_ROTULOS[pendentes[0]]}. "
            "Faca SO essa pergunta nesta mensagem - uma pergunta por mensagem. "
            "Saudacao e resposta a uma duvida do cliente nao contam como pergunta "
            "e podem vir na mesma mensagem."
        )
        bloco.append(
            "PROIBIDO nesta mensagem: apresentar resumo do pedido, informar total "
            "ou chamar send_order_to_cardapio - a ficha ainda tem pendencias."
        )
    elif detalhe:
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: o detalhe pendente acima. "
            "Faca SO essa pergunta nesta mensagem - uma pergunta por mensagem. "
            "Saudacao e resposta a uma duvida do cliente nao contam como pergunta "
            "e podem vir na mesma mensagem."
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
