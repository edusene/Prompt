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

import datetime as _dt
import re as _re
import time

import database

# Campos que o cliente pode corrigir durante a conversa (a fala mais recente vence)
_CAMPOS_REVISAVEIS = (
    "name", "modality", "bairro", "endereco", "referencia",
    "horario", "data_agendada", "data_iso", "payment_method",
)

_CAMPOS = _CAMPOS_REVISAVEIS + ("items", "volume_pendente", "sem_numero",
                                "sabores_pendentes", "preparo_pendente",
                                "pedido_por_volume")

_ROTULOS = {
    "name": "Nome do cliente",
    "items": "Itens do pedido",
    "modality": "Modalidade (entrega/retirada)",
    "bairro": "Bairro",
    "endereco": "Endereco (rua e numero)",
    "endereco_numero": "Numero da casa",
    "endereco_referencia": "Ponto de referencia (a casa nao tem numero)",
    "referencia": "Ponto de referencia",
    "data_agendada": "Data",
    "horario": "Horario",
    "payment_method": "Forma de pagamento",
    "troco_para": "Troco",
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
        "volume_pendente": 0,
        # "frito"/"assado" dito junto com um volume sem sabor. Sem ele a
        # pergunta do preparo voltava depois de o cliente ja ter respondido.
        "preparo_pendente": "",
        # Quantidades ditas com uma palavra que serve a varios sabores
        # ("30 de carne"). [{"qtd", "termo", "opcoes"}]. Nao viram item: viram
        # pergunta. Enquanto ficarem aqui, o funil nao anda.
        "sabores_pendentes": [],
        "modality": "",
        "bairro": "",
        "endereco": "",
        "referencia": "",
        # O cliente afirmou que a casa nao tem numero. Nao completa o
        # endereco - troca a pendencia do numero pela do ponto de referencia,
        # que e o que o motoboy usa para achar a casa.
        "sem_numero": False,
        "horario": "",
        "data_agendada": "",
        # Dia resolvido (AAAA-MM-DD). data_agendada e o texto que vai para a
        # tela ("hoje, sexta-feira (19/09)"); data_iso e o dia de verdade, usado
        # para validar o expediente.
        "data_iso": "",
        "payment_method": "",
        # Troco, so para pagamento em dinheiro. E STRING de proposito, com
        # quatro estados: "" nao perguntado, "nao" cliente tem trocado,
        # "100.00" precisa de troco para esse valor, "nao_informado"
        # perguntamos e nao deu para entender a resposta.
        # Float nao serve: 0.0 e falsy e sumiria no merge, no load e em toda
        # checagem por truthiness; "nao" atravessa os tres intacto.
        "troco_para": "",
        "frete": None,
        # Total da ultima calculate_order. Guardado porque a regra do troco
        # ("precisa ser maior que o total") so pode ser conferida contra ele.
        "total": 0,
        "pedido_enviado": False,
        # A saudacao sai uma vez so, e na primeira resposta.
        "saudacao_feita": False,
        # A OFERTA ADICIONAL do BLOCO 2 (bebida/doce/bolo) ja foi feita nesta
        # conversa. O funil do dono e Produto/Tamanho -> Upsell -> Entrega, e
        # era sempre o upsell que sumia: em 4 dos testes de 26/09 ela foi
        # direto do pedido para o endereco, e o dono cobrou isso quatro vezes.
        "upsell_feito": False,
        # Aviso de "estamos fechados" ja dado nesta conversa.
        "aviso_horario_dado": False,
        # Quando o ultimo pedido foi fechado. Sobrevive a limpeza da ficha: e o
        # que distingue "quero mais 30 coxinhas" logo apos um pedido de um
        # pedido novo dias depois.
        "ultimo_pedido_em": "",
        # Entrega ou retirada do ultimo pedido. Sobrevive a limpeza como o
        # ultimo_pedido_em: "meu marido ja ta indo buscar" pede a resposta de
        # retirada, "saiu pra entrega?" a de entrega.
        "ultimo_pedido_modalidade": "",
        # Pedido dito por volume dividido ("1 cento pastel e bolinha",
        # "metade/metade"). O balcao responde esses com o valor na hora.
        "pedido_por_volume": False,
        # Data (ISO) que o AGENTE ofereceu e o cliente ainda nao aceitou. Sem
        # isto, "Pode sim" depois de "Posso deixar para segunda-feira (21/09)?"
        # nao preenchia data nenhuma e a Sandrinha perguntava o dia de novo.
        "data_proposta": "",
        # Horario que o AGENTE ofereceu ("adiantar para as 14h?"), para o
        # "sim" seguinte gravar a hora junto com o dia.
        "horario_proposto": "",
        # Volume com sabores que nao dividem em lotes de 25 ("1 cento" +
        # "coxinha, pastel e churros"): {"volume": 100, "sabores": [...]}.
        # Enquanto existir, a pergunta e "a porcao a mais fica com qual sabor?".
        "divisao_pendente": {},
        "updated_at": "",
    }


# Depois deste tempo parado — ou virando o dia — a conversa recomeça: o cliente
# e cumprimentado de novo e volta a ser avisado se a fabrica estiver fechada.
HORAS_ATE_NOVA_SESSAO = 6

# Marcas que valem por CONVERSA, nao pela vida do cliente.
_CAMPOS_DE_SESSAO = ("saudacao_feita", "aviso_horario_dado", "upsell_feito")


def _sessao_expirou(updated_at: str, agora=None) -> bool:
    """A ultima mensagem foi em outro dia ou ha muitas horas?

    Sem isto, `saudacao_feita` ficava True para sempre: o cliente era
    cumprimentado uma unica vez na vida e nunca mais, e quem ja tinha ouvido
    "estamos fechados" nunca mais era avisado.
    """
    if not updated_at:
        return False
    agora = agora or _dt.datetime.now()
    try:
        ultima = _dt.datetime.fromisoformat(str(updated_at))
    except (TypeError, ValueError):
        return False
    if ultima.date() != agora.date():
        return True
    return (agora - ultima) >= _dt.timedelta(hours=HORAS_ATE_NOVA_SESSAO)


def _pedido_abandonado(updated_at: str, agora=None) -> bool:
    """O pedido pela metade ja pode ser descartado?

    Criterio diferente do da saudacao de proposito. A saudacao volta a valer
    tambem na virada do dia; o PEDIDO nao pode sumir so porque o relogio passou
    da meia-noite — quem esta montando um pedido as 23h50 e continua as 00h05
    perderia tudo no meio da conversa. Aqui vale so o silencio longo.
    """
    if not updated_at:
        return False
    agora = agora or _dt.datetime.now()
    try:
        ultima = _dt.datetime.fromisoformat(str(updated_at))
    except (TypeError, ValueError):
        return False
    return (agora - ultima) >= _dt.timedelta(hours=HORAS_ATE_NOVA_SESSAO)


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

        marca = stored.get("updated_at", "")
        if _pedido_abandonado(marca):
            # Pedido montado pela metade e abandonado ontem NAO volta hoje. So
            # os campos de sessao eram zerados aqui, entao itens, data, horario
            # e pagamento de uma conversa antiga ressuscitavam: o cliente dizia
            # "ola boa noite" e a Sandrinha ja respondia com um resumo cheio de
            # dados que ele nunca informou nesta conversa.
            tinha = [c for c in ("items", "data_iso", "horario", "payment_method",
                                 "modality") if data.get(c)]
            if tinha:
                print(f"[memoria] {phone}: pedido abandonado descartado "
                      f"({', '.join(tinha)}) - parado desde {marca}")
            data = _so_dados_do_cliente(data)

        if _sessao_expirou(marca):
            for campo in _CAMPOS_DE_SESSAO:
                data[campo] = False
            print(f"[memoria] {phone}: conversa nova (ultima em "
                  f"{marca}) - saudacao volta a valer")
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


def _e_bebida(nome) -> bool:
    """O item e bebida (categoria Bebidas da planilha)?"""
    return _categoria_do_item(str(nome or "")) == "bebidas"


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
        # A correcao troca os salgados; a bebida ja pedida fica. "Troca para
        # 50 coxinhas" nao e "tira o refrigerante".
        bebidas_antigas = [i for i in (memoria.get("items") or [])
                           if isinstance(i, dict) and _e_bebida(i.get("name"))]
        if not any(_e_bebida(i.get("name")) for i in itens_novos if isinstance(i, dict)):
            itens_novos = list(itens_novos) + bebidas_antigas
        memoria["items"] = itens_novos
        mudancas.append(f"items={itens_novos}")

    fonte = novo_da_mensagem or extraido or {}
    bebidas = fonte.get("bebidas") or (extraido or {}).get("bebidas") or []
    if bebidas:
        itens = list(memoria.get("items") or [])
        # "pode ser o guarana" RESPONDE a pergunta do refrigerante: a bebida
        # fecha a pendencia com a quantidade pedida. Somada como item novo e
        # depois de novo pela escolha do sabor, saiam 2 Tuchauas no lugar de
        # 1 e o cliente pagava R$ 12,00 a mais (teste do Eduardo, 28/09 21h39).
        pendentes = list(memoria.get("sabores_pendentes") or [])
        aberta = next((p for p in pendentes if p.get("termo") == "refrigerante"), None)
        if aberta:
            bebidas = [dict(bebidas[0], quantity=int(aberta.get("qtd") or 1))] + list(bebidas[1:])
            memoria["sabores_pendentes"] = [p for p in pendentes if p is not aberta]
            mudancas.append(f"refrigerante -> {bebidas[0]['name']}")
        for b in bebidas:
            if not any(isinstance(i, dict) and i.get("name") == b["name"] for i in itens):
                itens.append(dict(b))
                mudancas.append(f"bebida={b['quantity']} x {b['name']}")
        memoria["items"] = itens
    sem_tamanho = fonte.get("bebidas_pendentes") or (extraido or {}).get("bebidas_pendentes") or []
    if sem_tamanho and not any(_e_bebida(i.get("name")) for i in memoria.get("items") or []
                               if isinstance(i, dict)):
        pendentes = list(memoria.get("sabores_pendentes") or [])
        aberta = next((p for p in pendentes if p.get("termo") == "refrigerante"), None)
        if not aberta:
            pendentes += sem_tamanho
            memoria["sabores_pendentes"] = pendentes
            mudancas.append("bebida sem tamanho -> pergunta")
        elif sem_tamanho[0].get("em_falta") and not aberta.get("em_falta"):
            # Pergunta ja aberta ("qual refrigerante?") e o cliente responde com
            # o esgotado ("Coca 2 litros"): a pergunta volta, agora dizendo que
            # esse acabou - sem isso ela so repetia as opcoes (29/09 15h14).
            aberta["em_falta"] = sem_tamanho[0]["em_falta"]
            memoria["sabores_pendentes"] = pendentes
            mudancas.append(f"refrigerante em falta: {aberta['em_falta']}")

    sabores_novos = (novo_da_mensagem or {}).get("sabores_pendentes")
    if sabores_novos and sabores_novos != memoria.get("sabores_pendentes"):
        memoria["sabores_pendentes"] = sabores_novos
        mudancas.append(f"sabores_pendentes={[s['termo'] for s in sabores_novos]}")

    # Troco so existe para dinheiro. Quem pediu troco para 100 e depois trocou
    # para Pix nao pode levar essa observacao para a comanda.
    if memoria.get("troco_para") and memoria.get("payment_method") != "dinheiro":
        memoria["troco_para"] = ""
        mudancas.append("troco_para=(limpo, pagamento nao e mais dinheiro)")

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


# A diretiva de coleta pede data, horario e numero NUMA MENSAGEM SO, entao a
# resposta vem junta: "45, amanha as 16h". Para achar o numero da casa ali eu
# apago os numeros que sao de outra coisa; se sobrar exatamente um, e ele.
# Sem isto o numero ficava solto na conversa e o modelo inventava sentido para
# ele - leu "45" como quantidade e respondeu com a regra do minimo de 25.
_NUMERO_DE_OUTRA_COISA = _re.compile(
    r"\d{1,2}\s*[:h]\s*\d{0,2}\b"                    # 16h, 16:30
    r"|\b(?:as|ate|apos|depois\s+das)\s+\d{1,2}\b"      # as 16
    r"|\d{1,2}\s*(?:horas?|hrs?|hs)\b"                    # 16 horas
    r"|\d{1,2}\s*/\s*\d{1,2}(?:\s*/\s*\d{2,4})?"   # 26/09/2026
    r"|\bdia\s+\d{1,2}\b"                                # dia 26
    r"|\d{1,2}\s+de\s+\w+"                              # 26 de setembro
    r"|\d+\s*(?:un\b|unidades?|centos?|por[cs][oa]{1,2}es?|por[cs]ao|salgad\w*)"
    r"|\d+\s*reais|r$\s*\d[\d.,]*",                      # dinheiro
    _re.IGNORECASE)

# "numero 45", "n 45", "casa 45": rotulo explicito dispensa adivinhacao.
_NUMERO_COM_ROTULO = _re.compile(
    r"\b(?:numero|num|n|no|casa|lote)\s*[.:]?\s*(?:o\s*)?(\d{1,6})\b",
    _re.IGNORECASE)

_QUALQUER_NUMERO = _re.compile(r"\b(\d{1,6})\b")

# Quem esta mexendo no pedido nao esta respondendo o endereco.
_MEXENDO_NO_PEDIDO = _re.compile(
    r"\b(quero|queria|gostaria|manda|coloca|poe|adiciona|acrescenta|"
    r"tira|troca|mais)\b", _re.IGNORECASE)


def _numero_da_casa_no_texto(norm: str) -> str:
    """O numero de casa dentro de uma frase, ou "" quando nao da para afirmar."""
    rotulado = _NUMERO_COM_ROTULO.search(norm)
    if rotulado:
        return rotulado.group(1)

    # Um produto citado significa pedido, nao endereco: "quero 50 pao pizza"
    # nao pode virar casa 50.
    try:
        from agents.conversation import _produto_conhecido
        for pedaco in _re.split(r"[^a-z]+", norm):
            if len(pedaco) > 3 and _produto_conhecido(pedaco):
                return ""
    except Exception:
        pass
    if _MEXENDO_NO_PEDIDO.search(norm):
        return ""

    sobrou = _QUALQUER_NUMERO.findall(_NUMERO_DE_OUTRA_COISA.sub(" ", norm))
    return sobrou[0] if len(sobrou) == 1 else ""


def capturar_numero_da_casa(memoria: dict, texto: str) -> str:
    """Junta ao endereco o numero que o cliente acabou de responder.

    Sem isto a pergunta nao tinha resposta possivel: o cliente dizia "45" ou
    "e S/N, casa sem numero", nada entrava na ficha, e a pendencia do numero
    ficava de pe para sempre. Exigir sem saber receber e travar o pedido.

    So age quando o numero esta REALMENTE pendente, e devolve o endereco novo
    (ou "" quando nao capturou).
    """
    if not endereco_sem_numero(memoria):
        return ""
    bruto = " ".join((texto or "").split())
    if not bruto or len(bruto) > 120:
        return ""

    norm = _normalizar_sem_acento(bruto)
    endereco = str(memoria.get("endereco") or "").strip()
    if _SEM_NUMERO_ACEITO.search(norm):
        # Nao completa o endereco: marca que nao ha numero, e a pendencia
        # passa a ser o ponto de referencia.
        memoria["sem_numero"] = True
        return "casa sem numero - vai pedir ponto de referencia"

    numero = _numero_da_casa_no_texto(norm)
    if not numero:
        return ""
    memoria["endereco"] = f"{endereco}, {numero}"
    memoria["sem_numero"] = False
    return memoria["endereco"]


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


_NEGATIVAS_DE_TROCO = (
    "nao precisa", "nao vou precisar", "nao preciso", "sem troco", "tenho trocado",
    "ta trocado", "esta trocado", "valor certo", "valor exato", "certinho",
    "tenho o valor", "levo certo", "pago certinho", "trocado", "nao", "certo",
)

_EXTENSO_DE_TROCO = {
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60,
    "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100, "cento": 100,
    "duzentos": 200, "trezentos": 300, "quatrocentos": 400, "quinhentos": 500,
    "mil": 1000,
}

_NUMERO_RE = _re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?|\d+")


def _data_numerica(data_iso: str) -> str:
    """'2026-09-22' -> '22/09/2026'. Vazio se nao der para converter."""
    try:
        return _dt.date.fromisoformat(str(data_iso)).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return ""


def _brl(valor: float) -> str:
    return ("R$ %.2f" % float(valor or 0)).replace(".", ",")


def _sem_acento(texto: str) -> str:
    try:
        import facts
        return facts.normalize(texto or "")
    except Exception:
        return (texto or "").strip().lower()


# O valor do troco grudado na palavra que o anuncia. Sem esta ancora o troco
# era o PRIMEIRO numero da mensagem: em "Quero 1 porcao de 25 coxinhas ... nota
# de 100, traz troco" a ficha guardava troco de R$ 1,00, e a comanda saia
# mandando o motoboy levar um real de troco para uma nota de cem.
_NUM = r"(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_RX_TROCO_ANCORADO = [
    _re.compile(r"troco\s+(?:para|pra|p/|de)?\s*(?:r\$)?\s*" + _NUM, _re.IGNORECASE),
    _re.compile(r"(?:nota|cedula|cédula)\s+de\s+(?:r\$)?\s*" + _NUM, _re.IGNORECASE),
    _re.compile(r"(?:pagar?|pago)\s+com\s+(?:uma\s+|um\s+)?(?:nota\s+de\s+)?"
               r"(?:r\$)?\s*" + _NUM, _re.IGNORECASE),
    _re.compile(_NUM + r"\s*reais", _re.IGNORECASE),
    _re.compile(r"(?:r\$)\s*" + _NUM, _re.IGNORECASE),
]


def _valor_do_troco(texto: str) -> float | None:
    """O valor que o cliente deu como troco, ou None quando nao da para saber.

    Procura o numero ancorado em "troco"/"nota de"/"reais". So cai no primeiro
    numero solto quando a mensagem e curta — ai ela e resposta direta a
    pergunta do troco ("100") e nao ha o que confundir. Em mensagem longa, sem
    ancora, prefere nao saber: um troco errado vai para a comanda e o motoboy
    sai com o dinheiro errado no bolso.
    """
    cru = texto or ""
    for rx in _RX_TROCO_ANCORADO:
        achado = rx.search(cru)
        if achado:
            valor = _numero_brasileiro(achado.group(1))
            if valor:
                return valor

    if len(cru.split()) > 8:
        return None
    # Mensagem curta: "100", "R$ 100". Hora nao e dinheiro ("19h" nunca e troco).
    sem_hora = _re.sub(r"\b\d{1,2}\s*[h:]\S*", " ", cru, flags=_re.IGNORECASE)
    return _valor_em_reais(sem_hora)


def _numero_brasileiro(bruto: str) -> float | None:
    """'1.000,50' -> 1000.5; '100' -> 100.0. None no que nao for numero > 0."""
    if "." in bruto and "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    elif "," in bruto:
        bruto = bruto.replace(",", ".")
    elif bruto.count(".") == 1 and len(bruto.split(".")[1]) == 3:
        bruto = bruto.replace(".", "")
    try:
        valor = float(bruto)
    except ValueError:
        return None
    return valor if valor > 0 else None


def _valor_em_reais(texto: str) -> float | None:
    """Le o primeiro valor monetario do texto CRU, no formato brasileiro.

    Cru de proposito: _sem_acento apaga pontuacao ("1.000" vira "1 000",
    "R$ 100,00" vira "r 100 00"), e sem o ponto e a virgula nao da para
    distinguir milhar de centavo.
    """
    achado = _NUMERO_RE.search(texto or "")
    if not achado:
        return None
    bruto = achado.group(0)
    if "." in bruto and "," in bruto:        # 1.000,50
        bruto = bruto.replace(".", "").replace(",", ".")
    elif "," in bruto:                        # 100,50
        bruto = bruto.replace(",", ".")
    elif bruto.count(".") == 1 and len(bruto.split(".")[1]) == 3:
        bruto = bruto.replace(".", "")        # 1.000
    try:
        valor = float(bruto)
    except ValueError:
        return None
    return valor if valor > 0 else None


def capturar_troco(memoria: dict, pergunta_anterior: str, texto: str) -> str:
    """Grava o troco na ficha a partir da resposta do cliente.

    Devolve o valor gravado ("nao", "100.00") ou "" quando a mensagem nao e
    sobre troco.

    `capturar_resposta_direta` nao serve aqui: ela descarta qualquer candidato
    que tenha digito — guarda deliberada, que e o que impede "50" de virar nome
    ou bairro. Afrouxar aquela funcao estragaria as duas coisas.

    Dois gatilhos, e e a combinacao deles que evita falso positivo:
      - o CLIENTE falou "troco" -> vale sempre;
      - o AGENTE perguntou do troco na mensagem anterior -> ai uma resposta
        seca ("100", "nao precisa") conta.
    Sem a segunda amarra, "quero 100 coxinhas" viraria troco de R$ 100.

    Nao valida contra o total: isso e trabalho de pendencia_de_detalhe, que
    roda quando o total ja existe. Guardar um valor invalido e melhor do que
    perder a resposta do cliente em silencio.
    """
    if memoria.get("payment_method") != "dinheiro":
        return ""
    if memoria.get("troco_para"):
        return ""

    limpo = _sem_acento(texto)
    if not limpo:
        return ""

    cliente_falou_troco = "troco" in limpo
    agente_perguntou = "troco" in _sem_acento(pergunta_anterior)
    if not (cliente_falou_troco or agente_perguntou):
        return ""

    # "tenho troco" / "vou precisar de troco" nao sao negativa; "nao preciso de
    # troco" e. Por isso a negativa e testada antes do numero, mas so casa se a
    # frase inteira for curta o bastante para nao carregar outra informacao.
    palavras = limpo.split()
    if len(palavras) <= 6:
        for marca in _NEGATIVAS_DE_TROCO:
            if limpo == marca or limpo.startswith(marca + " ") or f" {marca}" in f" {limpo}":
                if not any(p.isdigit() for p in limpo):
                    memoria["troco_para"] = "nao"
                    return "nao"

    valor = _valor_do_troco(texto)
    if valor is None:
        for palavra, numero in _EXTENSO_DE_TROCO.items():
            if palavra in palavras:
                valor = float(numero)
                break

    if valor is not None:
        memoria["troco_para"] = f"{valor:.2f}"
        return memoria["troco_para"]

    # Perguntamos e nao deu para entender. Fica registrado para nao reperguntar
    # em laco; o pedido sai com "cliente nao informou" nas observacoes.
    if agente_perguntou:
        print(f"[memoria] troco nao entendido em {texto!r}")
        memoria["troco_para"] = "nao_informado"
        return "nao_informado"
    return ""


# "S/N", "SN", "sem numero": o proprio prompt oferece essa saida a quem mora
# em casa sem numeracao. Vale como numero respondido.
_SEM_NUMERO_ACEITO = _re.compile(r"\bs\s*/?\s*n\b|sem\s+numero", _re.IGNORECASE)


def endereco_sem_numero(memoria: dict) -> bool:
    """Entrega com rua, mas sem numero e sem nada que o substitua.

    O funil exigia so que o campo endereco nao estivesse vazio, e "RUA
    GUANABARA" contava como completo: o pedido do dono fechou assim em 25/09,
    R$ 92,00 para uma rua sem numero de casa. O prompt pedia o numero; pedir
    nao e garantir.

    NAO cobra quando o cliente deu ponto de referencia: quem disse como achar
    a casa ja resolveu o problema que o numero resolve, e o dono cortou a
    cobranca de referencia justamente para nao empilhar pergunta.
    """
    if (memoria.get("modality") or "") != "entrega":
        return False
    endereco = str(memoria.get("endereco") or "").strip()
    if not endereco:
        return False          # falta o endereco inteiro: quem cobra e o campo
    if any(c.isdigit() for c in endereco):
        return False
    if str(memoria.get("referencia") or "").strip():
        return False
    return True


def _normalizar_sem_acento(texto: str) -> str:
    try:
        import facts
        return facts.normalize(texto)
    except Exception:
        return (texto or "").lower()


def inferir_modalidade(memoria: dict) -> str:
    """Bairro ou rua na ficha, com itens pedidos, significa ENTREGA.

    Ninguem informa o proprio bairro para buscar no balcao. O cliente disse
    "1 cento de coxinha ... aqui no Agenor de Carvalho", e ela perguntou
    "entrega ou retirada?" - duas vezes, mesmo depois de ele dar a rua (teste
    do dono, 25/09 20h45). E a conversa travava ali: sem modalidade nao ha
    total, e sem total a trava do troco nunca dispara. As duas queixas dele
    eram este mesmo defeito.

    So preenche quando esta VAZIO: quem disse "retirada" tem a palavra, mesmo
    tendo dado o bairro. E so com itens no pedido, para uma pergunta de frete
    solta ("quanto e a entrega no Pedrinhas?") nao virar pedido de entrega.

    Devolve "entrega" quando inferiu, ou "" quando nao havia o que inferir.
    """
    if memoria.get("modality"):
        return ""
    if not (memoria.get("items") or memoria.get("volume_pendente")):
        return ""
    if not (str(memoria.get("bairro") or "").strip()
            or str(memoria.get("endereco") or "").strip()):
        return ""
    memoria["modality"] = "entrega"
    return "entrega"


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
    pendentes = [c for c in obrigatorios if not memoria.get(c)]

    # Rua sem numero e endereco incompleto. Entra aqui, e nao numa pendencia
    # propria, porque faltando() ja e o que governa as tres portas: a diretiva
    # de coleta, a troca por resumo e o fechamento do pedido.
    if "endereco" not in pendentes and endereco_sem_numero(memoria):
        # O dono cortou o "pode ser S/N" em 25/09: sem numero o motoboy nao
        # acha a casa. Quem afirma que a casa nao tem numero cai no unico
        # substituto que funciona - o ponto de referencia.
        pendentes.append("endereco_referencia" if memoria.get("sem_numero")
                         else "endereco_numero")
    return pendentes


def _itens_ambiguos(memoria: dict) -> list[str]:
    """Itens cujo nome serve para o sabor de pacote E para o salgado grande.

    "Enroladinho de Salsicha" e "Rissole de Carne" existem nas duas familias do
    cardapio, e "coxinha" casa tanto com o sabor quanto com a Coxinha de Frango
    com Catupiry. A diferenca de preco e grande (R$ 0,80/un no pacote contra
    R$ 9,00/un avulso), entao o agente confirma em vez de escolher sozinho.

    So vale abaixo de facts.QTD_MINIMA_FESTA: dali para cima a leitura de
    pacote e a unica que faz sentido (ninguem pede 50 coxinhas grandes a
    R$ 9,00 cada) e perguntar seria so atrito. A duvida real e o pedido
    pequeno, onde as duas leituras fazem sentido.
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
        if qtd >= facts.QTD_MINIMA_FESTA:
            continue
        if item.get("tamanho"):
            continue          # o cliente ja respondeu: nao pergunte de novo
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


def _itens_ambiguos_detalhe(memoria: dict) -> list[dict]:
    """[{qtd, sabor}] dos itens sem tamanho definido, para montar a pergunta."""
    saida = []
    for item in memoria.get("items") or []:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("name", ""))
        try:
            qtd = int(float(item.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            continue
        if qtd <= 0 or qtd >= _minimo_de_festa():
            continue
        if item.get("tamanho"):
            continue          # respondido: fora da pergunta
        try:
            import facts
            oficial = facts.resolve_unit_price(nome, qtd)
        except Exception:
            continue
        if oficial and oficial.get("ambiguo"):
            saida.append({"qtd": qtd, "sabor": nome})
    return saida


def pendencia_de_item(memoria: dict) -> str:
    """Duvida sobre O QUE o cliente pediu. Tem prioridade sobre o funil.

    Confirmar o produto depois de ja ter coletado endereco e horario obriga a
    refazer a conta do pedido inteiro, entao esta pergunta fura a fila.
    """
    ambiguos = _itens_ambiguos(memoria)
    if not ambiguos:
        return ""

    # A frase e do dono (25/09): "1 a 24 un sem tamanho especificado: isole
    # apenas esse item e pergunte 'Essas [Qtd] [Sabor] seriam tamanho grande
    # (lanche) ou mini (festa)?'". Antes a Sandrinha citava os nomes de
    # catalogo ("Coxinha de Frango com Catupiry ou Coxinha frango"), que nao
    # dizem nada para quem esta comprando.
    detalhe = _itens_ambiguos_detalhe(memoria)
    if len(detalhe) > 1:
        # Dois itens sem tamanho viravam duas mensagens seguidas, cada uma com
        # a mesma pergunta: "Essas 10 Coxinha...?" e depois "Esses 10 Rissole
        # de Carne...?". O dono chamou isso de triagem picada e pediu uma fala
        # so (26/09 19h24). A pergunta e uma, e responde pelos dois.
        lista = " e ".join(f"{a['qtd']} {a['sabor']}" for a in detalhe)
        exemplos = (f"'Esses {lista} seriam tamanho grande (lanche) ou mini "
                    f"(festa)? 😊'")
        consolida = (
            f" SAO {len(detalhe)} ITENS SEM TAMANHO: pergunte pelos "
            f"{len(detalhe)} NA MESMA MENSAGEM, numa frase so. E PROIBIDO "
            f"mandar uma pergunta de tamanho por item."
        )
    else:
        exemplos = "; ".join(
            f"Essas {a['qtd']} {a['sabor']} seriam tamanho grande (lanche) "
            f"ou mini (festa)? 😊"
            for a in detalhe
        )
        consolida = " ISOLE SO ESSE ITEM."
    return (
        "TAMANHO NAO INFORMADO - " + "; ".join(ambiguos)
        + ". Pergunte AGORA, nesta forma: "
        + (exemplos or "'seriam tamanho grande (lanche) ou mini (festa)? 😊'")
        + consolida
        + " NAO explique a regra dos 25 nem cite nome de catalogo: a pergunta e "
        "so grande (lanche) ou mini (festa). Se ele responder LANCHE ou GRANDE, "
        "aceite a quantidade que ele pediu. Se responder MINI ou FESTA, avise "
        "com gentileza que o mini fecha em 25 por sabor. E PROIBIDO transferir "
        "para humano por causa disto."
    )


def pendencia_de_sabor(memoria: dict) -> str:
    """Quantidade pedida com uma palavra que serve a varios sabores.

    Fura a fila: nao da para ajustar lote, calcular total nem coletar endereco
    de um sabor que ninguem sabe qual e. Antes a palavra virava o produto de
    nome mais comprido e o cliente recebia o pedido errado, ou - depois que
    isso foi barrado - o sabor simplesmente sumia do pedido.
    """
    pendentes = memoria.get("sabores_pendentes") or []
    if not pendentes:
        return ""
    perguntas = []
    for p in pendentes:
        opcoes = " ou ".join(p.get("opcoes") or [])
        perguntas.append(f"os {p.get('qtd')} de {p.get('termo')} ({opcoes})")
    return (
        "SABOR NAO IDENTIFICADO - o cliente disse a quantidade mas nao qual "
        "sabor em: " + "; ".join(perguntas) + ". Pergunte AGORA, numa frase so, "
        "qual sabor ele quer em cada um, OFERECENDO as opcoes entre parenteses. "
        "E PROIBIDO escolher por ele, PROIBIDO deixar esse item de fora do "
        "pedido e PROIBIDO apresentar resumo ou total antes de saber o sabor."
    )


def _itens_da_divisao(volume: int, sabores: list[str], extra_para: str = "",
                      so_o_que_fecha: bool = False) -> list[dict]:
    """Os itens do volume dividido em lotes de 25 pelos sabores."""
    lotes = volume // 25
    base, sobra = divmod(lotes, len(sabores))
    itens = []
    for nome in sabores:
        blocos = base + (sobra if (nome == extra_para and not so_o_que_fecha) else 0)
        if blocos <= 0:
            continue
        qtd = blocos * 25
        preco = 0.0
        try:
            import facts
            oficial = facts.resolve_unit_price(nome, qtd)
            if oficial:
                preco = oficial["preco_unitario"]
        except Exception:
            pass
        itens.append({"name": nome, "quantity": qtd, "price": preco})
    return itens


def aplicar_divisao_do_volume(memoria: dict, texto: str) -> list[str]:
    """Volume dito antes ("1 cento") + sabores ditos depois -> itens na ficha.

    Divide por igual quando fecha em lotes de 25. Quando nao fecha (100 em 3
    sabores), guarda a divisao e pergunta para qual sabor vai a porcao a
    mais; a resposta com um sabor ("pode colocar mais pasteis") fecha a
    conta. Era o modelo quem fazia isso, e ele repetia a mesma pergunta depois
    de o cliente responder (teste do dono, 30/09 15h50).
    """
    from agents.conversation import sabores_citados
    pendente = memoria.get("divisao_pendente") or {}
    citados = sabores_citados(texto)
    if pendente:
        sabores = pendente.get("sabores") or []
        volume = int(pendente.get("volume") or 0)
        escolhido = next((s for s in citados if s in sabores), "")
        so_esses = bool(_re.search(r"\b" + str((volume // 25 // len(sabores)) * 25 * len(sabores)) + r"\b",
                                   texto or "")) if sabores else False
        if not (escolhido or so_esses) or not sabores:
            return []
        memoria["items"] = _itens_da_divisao(volume, sabores, escolhido, so_o_que_fecha=not escolhido)
        memoria["divisao_pendente"] = {}
        return [f"divisao: {', '.join(f'{i['quantity']} {i['name']}' for i in memoria['items'])}"]

    try:
        volume = int(memoria.get("volume_pendente") or 0)
    except (TypeError, ValueError):
        volume = 0
    if not volume or memoria.get("items") or len(citados) < 2 or volume % 25:
        return []
    memoria["volume_pendente"] = 0
    if (volume // 25) % len(citados) == 0:
        memoria["items"] = _itens_da_divisao(volume, citados)
        return [f"volume dividido: {', '.join(f'{i['quantity']} {i['name']}' for i in memoria['items'])}"]
    memoria["divisao_pendente"] = {"volume": volume, "sabores": citados}
    return [f"divisao pendente: {volume} em {citados}"]


def pendencia_de_divisao(memoria: dict) -> str:
    """A pergunta da porcao a mais, quando o volume nao divide pelos sabores."""
    pendente = memoria.get("divisao_pendente") or {}
    sabores = pendente.get("sabores") or []
    if not sabores:
        return ""
    volume = int(pendente.get("volume") or 0)
    lotes = volume // 25
    base, sobra = divmod(lotes, len(sabores))
    fecha = base * 25 * len(sabores)
    nomes = [s.lower() for s in sabores]
    lista = ", ".join(nomes[:-1]) + " ou " + nomes[-1]
    porcoes = "uma porção" if sobra == 1 else f"{sobra} porções"
    return (f"Com {len(sabores)} sabores, {base * 25} de cada dá {fecha}. "
            f"Para completar {'o cento' if volume == 100 else f'as {volume}'}, "
            f"{porcoes} de 25 a mais fica com qual sabor: {lista}? 😊")


def aplicar_escolha_de_sabor(memoria: dict, texto: str) -> list[str]:
    """O cliente respondeu qual sabor era. Vira item na ficha.

    Sem isto a resposta morria no texto: a pendencia continuava, a pergunta
    voltava na mensagem seguinte e o pedido nao andava - foi o que ja
    aconteceu com o aceite de lote (23/09) e com a escolha de tamanho.
    """
    pendentes = memoria.get("sabores_pendentes") or []
    if not pendentes or not texto:
        return []
    limpo = _sem_acento(texto)
    itens = list(memoria.get("items") or [])
    sobraram, mudancas = [], []
    for p in pendentes:
        escolhido = ""
        for opcao in p.get("opcoes") or []:
            if _sem_acento(opcao) in limpo:
                escolhido = opcao
                break
        if not escolhido and p.get("termo") == "refrigerante":
            # "lata", "a de 600": ninguem repete "Refrigerante Lata" inteiro.
            from agents.conversation import PALAVRAS_DA_BEBIDA
            for opcao in p.get("opcoes") or []:
                if any(_re.search(r"\b" + palavra, limpo)
                       for palavra in PALAVRAS_DA_BEBIDA.get(opcao, ())):
                    escolhido = opcao
                    break
        if not escolhido:
            sobraram.append(p)
            continue
        try:
            qtd = int(p.get("qtd") or 0)
        except (TypeError, ValueError):
            qtd = 0
        if qtd <= 0:
            continue
        preco = 0.0
        try:
            import facts
            oficial = facts.resolve_unit_price(escolhido, qtd)
            if oficial:
                preco = oficial["preco_unitario"]
        except Exception:
            pass
        ja = next((i for i in itens if isinstance(i, dict)
                   and _sem_acento(str(i.get("name"))) == _sem_acento(escolhido)), None)
        if ja:
            ja["quantity"] = int(ja.get("quantity") or 0) + qtd
        else:
            itens.append({"name": escolhido, "quantity": qtd, "price": preco})
        mudancas.append(f"{p.get('termo')} -> {qtd} x {escolhido}")
    if mudancas:
        memoria["items"] = itens
        memoria["sabores_pendentes"] = sobraram
    return mudancas


# ── Sabor esgotado ──────────────────────────────────────────────────────────
# Categorias que JA SAO o acompanhamento da oferta adicional. Quem pediu
# refrigerante nao precisa ouvir a oferta de refrigerante.
_CATEGORIAS_DE_ACOMPANHAMENTO = ("bebidas", "bolos e doces")


def _categoria_do_item(nome: str) -> str:
    """Categoria oficial do produto na planilha, em minusculo. '' se nao achar."""
    try:
        import facts
        linhas = facts.lookup_preco(nome) or []
    except Exception:
        return ""
    alvo = _sem_acento(nome)
    for linha in linhas:
        if _sem_acento(linha.get("item", "")) == alvo:
            return _sem_acento(linha.get("categoria", ""))
    return _sem_acento(linhas[0].get("categoria", "")) if linhas else ""


def itens_indisponiveis(memoria: dict) -> list[str]:
    """Nomes OFICIAIS dos itens do pedido que a planilha marca como esgotados.

    Nome oficial, e nao o que o cliente escreveu, porque e justamente a troca
    de nome que causou o pior erro do dia: pedido de "croquete" num cento de
    fritos, ela respondeu que "o nome correto do sabor e Croquete de
    Macaxeira, mas ele esta esgotado" - juntou o sabor de lote (que esta
    esgotado) com o salgado grande de balcao (que nao esta) e inventou uma
    falta de estoque que nao existia naquele produto (teste do dono,
    26/09 19h12).
    """
    fora = []
    for item in memoria.get("items") or []:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("name", "")).strip()
        if not nome:
            continue
        try:
            import facts
            linhas = facts.lookup_preco(nome) or []
            if not linhas:
                continue
            alvo = _sem_acento(nome)
            exatos = [l for l in linhas if _sem_acento(l.get("item", "")) == alvo]
            if not exatos:
                continue
            # Item que o cardapio digital nao tem (bloqueado e nao liberado
            # pelo dono) tambem nao pode ser vendido: avisado AGORA, como um
            # esgotado. Descoberto so no envio, ele fazia o cliente passar
            # endereco e pagamento para no fim ir para a fila do humano - e
            # no caminho travava o ajuste de lote num laco (teste do dono,
            # 29/09 15h14, croquete de carne).
            import cardapio_catalogo
            bloqueado = any(_sem_acento(k) == alvo
                            for k in cardapio_catalogo.bloqueados_efetivos())
            if not bloqueado and any(l.get("disponivel", True) for l in exatos):
                continue
            oficial = exatos[0].get("item") or nome
        except Exception:
            continue
        if oficial not in fora:
            fora.append(oficial)
    return fora


def pendencia_de_indisponivel(memoria: dict) -> str:
    """Sabor pedido que esta ESGOTADO na planilha. Fura a fila do funil."""
    fora = itens_indisponiveis(memoria)
    if not fora:
        return ""
    lista = ", ".join(fora)
    return (
        f"SABOR ESGOTADO - {lista}. Diga AGORA, com o nome EXATO que esta "
        f"escrito aqui, que esse sabor esta em falta hoje, e pergunte por qual "
        f"sabor o cliente quer trocar. E PROIBIDO renomear o item ('o nome "
        f"correto seria...'), PROIBIDO oferecer outro produto como se fosse o "
        f"mesmo, e PROIBIDO dizer que esta esgotado um item que nao esta nesta "
        f"lista. Nao apresente resumo nem total enquanto a troca nao for feita."
    )


# ── Oferta adicional (BLOCO 2) ──────────────────────────────────────────────
# Faixa de horario -> frase exata do dono. Fora das faixas vale a ultima.
_OFERTAS_POR_HORARIO = (
    ((7, 40), (10, 0), "Pra acompanhar, vai um Bolo de Milho quentinho? 🌽"),
    ((10, 1), (14, 0), "Pra acompanhar, que tal um Refrigerante 2L bem gelado? 🥤"),
    ((14, 1), (17, 0), "Pro lanche da tarde, vai um Bolo de Milho ou um Guaraná "
                       "Tuchaua 2L geladinho? 🥤"),
    ((17, 1), (20, 0), "Pra acompanhar, vai um Refrigerante 2L ou docinhos "
                       "(Brigadeiro/Beijinho)? 🥤🍬"),
)


def _frases_de_oferta() -> list[str]:
    """Uma frase por faixa: a do dono (painel) onde houver, senao a padrao."""
    try:
        import config_dono
        do_dono = config_dono.ofertas()
    except Exception as e:
        print(f"[oferta] frases do painel ignoradas: {e}")
        do_dono = []
    padrao = [frase for _ini, _fim, frase in _OFERTAS_POR_HORARIO]
    return [(do_dono[i] if i < len(do_dono) and do_dono[i] else frase)
            for i, frase in enumerate(padrao)]


def oferta_adicional(agora=None) -> str:
    """A frase de upsell do BLOCO 2 para a hora em que estamos."""
    frases = _frases_de_oferta()
    try:
        import horario
        agora = agora or horario.agora()
        minuto = agora.hour * 60 + agora.minute
    except Exception:
        return frases[-1]
    for i, ((h1, m1), (h2, m2), _padrao) in enumerate(_OFERTAS_POR_HORARIO):
        if h1 * 60 + m1 <= minuto <= h2 * 60 + m2:
            return frases[i]
    return frases[-1]


def e_a_oferta_configurada(reply: str, agora=None) -> bool:
    """A resposta traz a frase de oferta que vale agora (inclusive a do dono)?

    As checagens de "ja ofereceu" procuram palavras fixas (refrigerante, bolo de
    milho...). Uma frase nova do dono sem nenhuma delas nunca seria reconhecida
    e a oferta se repetiria em toda mensagem. O inicio da frase basta: o modelo
    as vezes troca o emoji ou a pontuacao do fim.

    Vale a frase de QUALQUER faixa, nao so a de agora: a oferta feita as 13h58
    tem de continuar reconhecida as 14h01, quando a faixa ja virou. (`agora`
    fica na assinatura por compatibilidade.)
    """
    texto = _sem_acento(reply or "")
    for frase in _frases_de_oferta():
        inicio = " ".join(_sem_acento(frase).split()[:5])
        if inicio and inicio in texto:
            return True
    return False


def upsell_pendente(memoria: dict, agora=None) -> str:
    """A oferta adicional ainda tem de sair antes de o funil andar?

    O BLOCO 1 fixa o funil em Produto/Tamanho -> Upsell -> Endereco/Retirada ->
    Resumo, e o upsell era a etapa que sumia. Aqui ele deixa de depender da boa
    vontade do modelo: enquanto a frase nao sair, a ficha nao libera a coleta
    de logistica nem o espelho.

    Nao sai quando: nao ha item nenhum ainda, o pedido ja ESTA no acompanhamento
    (refrigerante, bolo, docinho), ou ainda ha duvida sobre o proprio pedido -
    tamanho, lote quebrado ou sabor esgotado vem antes.
    """
    if memoria.get("upsell_feito"):
        return ""
    itens = memoria.get("items") or []
    if not itens:
        return ""
    if pendencia_de_item(memoria) or pendencia_de_lote(memoria):
        return ""
    if pendencia_de_indisponivel(memoria) or pendencia_de_sabor(memoria):
        return ""
    for item in itens:
        if not isinstance(item, dict):
            continue
        if _categoria_do_item(str(item.get("name", ""))) in _CATEGORIAS_DE_ACOMPANHAMENTO:
            return ""          # ja tem bebida/doce no pedido
    return oferta_adicional(agora)


def subtotal_dos_salgados(memoria: dict) -> float:
    """Soma dos itens da ficha pelo preco da planilha. 0 se algum nao tem preco."""
    total = 0.0
    for item in memoria.get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            import facts
            qtd = int(item.get("quantity") or 0)
            oficial = facts.resolve_unit_price(str(item.get("name", "")), qtd)
        except Exception:
            return 0.0
        if not oficial or not qtd:
            return 0.0
        total += qtd * float(oficial["preco_unitario"])
    return round(total, 2)


def oferta_com_valor(memoria: dict, agora=None) -> str:
    """A oferta adicional, com o valor na frente quando o pedido veio por volume.

    O balcao responde "metade croquete e metade pastel" com "Ok. 80,00" - o
    dono pediu calculo imediato para esses pedidos, sem textao (28/09). Nos
    demais a oferta sai como sempre.
    """
    oferta = upsell_pendente(memoria, agora)
    if not oferta or not memoria.get("pedido_por_volume"):
        return oferta
    subtotal = subtotal_dos_salgados(memoria)
    if not subtotal:
        return oferta
    return f"Fica {_brl(subtotal)} nos salgados! {oferta}"


def pendencia_de_lote(memoria: dict) -> str:
    """Sabor de lote com quantidade que nao fecha em multiplo de 25.

    A trava ja existia, mas so no calculate_order — la no fim. O cliente pedia
    30 coxinhas, a Sandrinha seguia coletando endereco, horario e pagamento, e
    so entao descobria que 30 nao fecha lote (teste do dono, 22/09). Aqui a
    conta e a mesma (cardapio_catalogo.traduzir), so que lida a cada mensagem,
    antes de o funil andar.
    """
    items = memoria.get("items") or []
    if not items:
        return ""
    try:
        import cardapio
        import cardapio_catalogo
        norm, _avisos, nao_encontrados = cardapio._normalizar_itens(
            [dict(i) for i in items]
        )
        if nao_encontrados:
            return ""      # item fora do cardapio tem tratamento proprio
        _saida, bloqueados = cardapio_catalogo.traduzir([dict(i) for i in norm])
    except Exception as e:
        print(f"[memoria] WARN pendencia_de_lote: {e}")
        return ""

    quebrados = [b for b in bloqueados
                 if "multiplos de" in b or "exige no minimo" in b]
    if not quebrados:
        return ""

    # A pergunta precisa dizer DE QUAL SABOR se trata. O cliente pediu 30
    # coxinhas e 20 quibes e ouviu "quer 25 ou 50?", sem saber a qual dos dois
    # a Sandrinha se referia (teste do dono, 23/09).
    try:
        quebras = cardapio_catalogo.quebras_de_lote([dict(i) for i in norm])
    except Exception as e:
        print(f"[memoria] WARN quebras_de_lote: {e}")
        quebras = []

    if quebras:
        detalhe = "; ".join(
            f"{q['sabor']}: {q['qtd']} un -> ofereca {q['sugerida']} un"
            for q in quebras
        )
        exemplo = " e ".join(f"{q['sugerida']} {q['sabor']}" for q in quebras)
        return (
            f"QUANTIDADE QUEBRADA - {detalhe}. Pergunte AGORA, antes de seguir o "
            f"funil, CITANDO CADA SABOR pelo nome: 'Fazemos de 25 em 25 por "
            f"sabor! Ajusto para {exemplo}?'. E PROIBIDO perguntar 'quer 25 ou "
            f"50?' sem dizer de qual sabor voce esta falando. NAO apresente "
            f"resumo nem total enquanto a quantidade nao fechar em multiplo de 25."
        )

    return (
        "QUANTIDADE QUEBRADA - " + "; ".join(quebrados) + ". Pergunte AGORA, "
        "antes de seguir o funil, CITANDO O SABOR de que voce esta falando. "
        "NAO apresente resumo nem total enquanto a quantidade nao fechar em "
        "multiplo de 25."
    )


def aplicar_ajuste_de_lote(memoria: dict) -> list[str]:
    """Grava na ficha o ajuste de quantidade que o cliente acabou de aceitar.

    Sem isto o aceite morria na conversa. A Sandrinha oferecia "Ajusto para 25
    quibes?", o cliente dizia "pode ser", e a ficha continuava com 5: a trava
    de lote apontava a mesma quebra na mensagem seguinte, o funil nao andava e
    o modelo acabava transferindo para um humano um pedido que o cliente ja
    tinha ajustado (teste do dono, 23/09 15:41). A quantidade e do codigo, nao
    do texto do modelo.

    Devolve a lista de mudancas, para o log.
    """
    items = memoria.get("items") or []
    if not items:
        return []
    try:
        import cardapio
        import cardapio_catalogo
        # O nome tem de passar pela tabela antes: a ficha guarda "Coxinha" e a
        # planilha diz "Coxinha frango" - sem normalizar, nenhum item casa.
        norm, _avisos, nao_encontrados = cardapio._normalizar_itens(
            [dict(i) for i in items]
        )
        if nao_encontrados or len(norm) != len(items):
            return []          # sem correspondencia 1 para 1 nao da para aplicar
        quebras = cardapio_catalogo.quebras_de_lote([dict(i) for i in norm])
    except Exception as e:
        print(f"[memoria] WARN aplicar_ajuste_de_lote: {e}")
        return []
    if not quebras:
        return []

    por_indice = {q["indice"]: q for q in quebras}
    novos, mudancas = [], []
    for i, bruto in enumerate(items):
        it = dict(bruto)
        q = por_indice.get(i)
        if q and int(it.get("quantity") or 0) != q["sugerida"]:
            mudancas.append(f"{it.get('name')} {it.get('quantity')} -> {q['sugerida']} un")
            it["quantity"] = q["sugerida"]
            # O preco tem de ser relido: abaixo de 25 o mesmo nome resolvia
            # para o salgado grande avulso (R$ 9,00), e na quantidade de
            # pacote ele vale R$ 0,80.
            try:
                import facts
                oficial = facts.resolve_unit_price(it.get("name", ""), q["sugerida"])
                it["price"] = oficial["preco_unitario"] if oficial else 0.0
            except Exception:
                it["price"] = 0.0
        novos.append(it)

    if mudancas:
        memoria["items"] = novos
    return mudancas


# Tamanho minimo de um lote de festa. Abaixo disso o cardapio digital nao
# aceita, entao quem escolhe o pacote esta escolhendo, no minimo, uma porcao.
_PORCAO = 25


def aplicar_escolha_de_lanche(memoria: dict) -> list[str]:
    """Grava que o cliente quer o salgado GRANDE, e nao o sabor de pacote.

    Irma de aplicar_escolha_de_pacote, e existia so metade: havia tratamento
    para quem escolhia o pacote, e nenhum para quem escolhia o lanche. O cliente
    respondia "Grande, lanche", nada entrava na ficha, a pendencia de tamanho
    continuava de pe e o espelho nao saia - ela encerrava com "posso ajudar com
    mais alguma coisa?" e o pedido de R$ 45,00 morria ali (medido em 1 de 3
    rodadas, 26/09).

    A regra e do dono: LANCHE aceita a quantidade pedida. Entao a quantidade NAO
    muda - so se registra o tamanho, e o preco volta para a tabela resolver (a
    mesma quantidade abaixo de 25 resolve como salgado grande).

    Devolve a lista de mudancas, para o log.
    """
    itens = memoria.get("items") or []
    if not itens:
        return []
    try:
        import facts
    except Exception:
        return []

    minimo = _minimo_de_festa()
    novos, mudancas = [], []
    for bruto in itens:
        it = dict(bruto)
        try:
            qtd = int(float(it.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            novos.append(it)
            continue
        if qtd >= minimo or it.get("tamanho"):
            novos.append(it)
            continue
        try:
            oficial = facts.resolve_unit_price(it.get("name", ""), qtd)
        except Exception:
            oficial = None
        if oficial and oficial.get("ambiguo"):
            it["tamanho"] = "lanche"
            it["price"] = 0.0     # quem precifica e a tabela, nunca este codigo
            mudancas.append(f"{it.get('name')} {qtd} un -> lanche (grande)")
        novos.append(it)

    if mudancas:
        memoria["items"] = novos
    return mudancas


def aplicar_escolha_de_pacote(memoria: dict) -> list[str]:
    """Grava a escolha entre sabor de pacote e salgado grande de balcao.

    A duvida era levantada (pendencia_de_item), o cliente respondia "pode ser a
    do pacote mesmo" — e a resposta nao ia para lugar nenhum. A ficha seguia
    com 10 unidades a R$ 9,00, a trava de lote apontava a mesma quebra na
    mensagem seguinte e o modelo acabava transferindo para um humano um pedido
    ja resolvido. A trava nao tinha saida, igual ao caso do ajuste de
    quantidade (ver aplicar_ajuste_de_lote).

    Escolher o pacote sobe a quantidade para uma porcao fechada e devolve o
    preco para a tabela decidir: abaixo de 25 o mesmo nome resolve como salgado
    grande (R$ 9,00/un); na quantidade de pacote vale R$ 0,80.

    Devolve a lista de mudancas, para o log.
    """
    itens = memoria.get("items") or []
    if not itens:
        return []
    try:
        import facts
    except Exception:
        return []

    minimo = _minimo_de_festa()
    novos, mudancas = [], []
    for bruto in itens:
        it = dict(bruto)
        try:
            qtd = int(float(it.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            novos.append(it)
            continue
        if qtd >= minimo:
            novos.append(it)          # ja e quantidade de festa: nada a decidir
            continue
        try:
            oficial = facts.resolve_unit_price(it.get("name", ""), qtd)
        except Exception:
            oficial = None
        if oficial and oficial.get("ambiguo"):
            mudancas.append(f"{it.get('name')} {qtd} -> {_PORCAO} un (pacote)")
            it["quantity"] = _PORCAO
            it["price"] = 0.0
        novos.append(it)

    if mudancas:
        memoria["items"] = novos
    return mudancas


def pendencia_de_detalhe(memoria: dict) -> str:
    """Campo preenchido que ainda precisa de um detalhe, nao de nova coleta."""
    # Nao ha mais pendencia de debito/credito: "cartao" e forma de pagamento
    # completa desde 23/09 (os dois pagam a mesma taxa de retorno).

    # O troco entra aqui, e nao em faltando(), porque depende do total: exigi-lo
    # antes travaria o calculate_order, que e justamente quem calcula o total.
    # Na retirada o pagamento e no balcao e o prompt PROIBE perguntar, entao
    # nao existe troco a cobrar.
    if memoria.get("payment_method") == "dinheiro" and memoria.get("modality") != "retirada":
        total = float(memoria.get("total") or 0)
        troco = memoria.get("troco_para", "")
        if total > 0 and not troco:
            return (
                f"Pagamento em dinheiro e o total ja esta calculado "
                f"({_brl(total)}). Pergunte: 'Precisa de troco para quanto? 💵' "
                "antes de fechar o pedido."
            )
        try:
            valor = float(troco)
        except (TypeError, ValueError):
            valor = None
        if valor is not None and total > 0 and valor <= total:
            return (
                f"O cliente pediu troco para {_brl(valor)}, mas o total e "
                f"{_brl(total)}. Diga exatamente: 'O total do seu pedido deu "
                f"{_brl(total)}. O valor para troco precisa ser maior que o "
                "total da compra. Dinheiro para quanto? 💵'. NAO envie o pedido "
                "antes de o cliente corrigir."
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


def _minimo_de_festa() -> int:
    """A partir de quantas unidades o pedido e sempre festa (facts manda)."""
    try:
        import facts
        return facts.QTD_MINIMA_FESTA
    except Exception:
        return 21


def tamanho_ja_definido(memoria: dict) -> bool:
    """Nao ha duvida de tamanho a resolver: todo item e quantidade de festa.

    Um item abaixo do minimo derruba a resposta inteira de proposito. Ali a
    pergunta "festa ou grande de balcao?" e legitima - e a trava que evitou os
    R$ 450,00 em coxinha de balcao (22/09).

    Quem chama: a diretiva da ficha, que PEDE ao modelo para nao perguntar, e
    app._conferir_tamanho, que TIRA a pergunta quando ele pergunta assim mesmo.
    A regra e uma so, escrita num lugar so.
    """
    itens = memoria.get("items") or []
    minimo = _minimo_de_festa()
    qtds = []
    for it in itens:
        if not isinstance(it, dict):
            continue
        try:
            qtds.append(int(float(it.get("quantity", 0) or 0)))
        except (ValueError, TypeError):
            return False       # quantidade ilegivel: nao afirme que esta definido
    return bool(qtds) and all(q >= minimo for q in qtds)


def _frase_de_lote(memoria: dict) -> str:
    """'Fazemos de 25 em 25 por sabor! Ajusto para X e Y?' — em codigo."""
    try:
        import cardapio
        import cardapio_catalogo
        norm, _avisos, fora = cardapio._normalizar_itens(
            [dict(i) for i in (memoria.get("items") or [])]
        )
        base = norm if (norm and not fora) else (memoria.get("items") or [])
        quebras = cardapio_catalogo.quebras_de_lote([dict(i) for i in base])
    except Exception:
        quebras = []
    if not quebras:
        return ""
    partes = [f"{q['sugerida']} {q['sabor']}" for q in quebras]
    if len(partes) > 1:
        lista = ", ".join(partes[:-1]) + " e " + partes[-1]
    else:
        lista = partes[0]
    return f"Fazemos de 25 em 25 por sabor! Ajusto para {lista}? 😊"


def etapa_coberta(memoria: dict, reply: str, agora=None) -> bool:
    """A resposta JA cumpriu a etapa travada do funil?

    Nao da para decidir isso comparando o texto com a frase que o codigo
    escreveria: o modelo diz a mesma coisa com outras palavras, e ele tem o
    direito de dizer. O que conta e a SUBSTANCIA de cada etapa - o tamanho
    oferecido nas duas opcoes, o sabor perguntado, o esgotado nomeado, cada
    quantidade quebrada com o multiplo sugerido, a oferta adicional feita.

    Sem esta funcao a checagem era "os 28 primeiros caracteres da minha frase
    aparecem na resposta?", e a frase de lote tem DUAS sentencas: a resposta
    certa ("Fazemos de 25 em 25 por sabor! Ajusto para 25 coxinha, 25 quibe e
    50 bolinha de queijo?") batia so na primeira e perdia a segunda - os
    sabores eram cortados da mensagem que o cliente recebia.
    """
    if not reply:
        return False
    texto = _sem_acento(reply)

    detalhe = _itens_ambiguos_detalhe(memoria)
    if detalhe:
        if not ("lanche" in texto and ("mini" in texto or "festa" in texto)):
            return False
        return all(_sem_acento(a["sabor"]).split()[0] in texto for a in detalhe)

    # Mesma ordem de pergunta_bloqueante: o sabor em falta antes do resto.
    # "Disponivel" junto do nome do item e o contrario do aviso: nao cobre.
    fora = itens_indisponiveis(memoria)
    if fora:
        if not ("esgotad" in texto or "em falta" in texto or "indisponivel" in texto):
            return False
        if "macaxeira" in texto and not any("macaxeira" in _sem_acento(n) for n in fora):
            return False
        return all(_sem_acento(n).split()[0] in texto for n in fora)

    # Divisao do volume: a pergunta tem de citar cada sabor e ser pergunta.
    divisao = (memoria.get("divisao_pendente") or {}).get("sabores") or []
    if divisao:
        return "?" in reply and all(_sem_acento(s).split()[0] in texto for s in divisao)

    pendentes = memoria.get("sabores_pendentes") or []
    if pendentes:
        # Bebida esgotada: perguntar "qual sabor na Coca 2L?" sem dizer que o
        # 2L acabou deixa o cliente achando que ainda da (teste de 28/09).
        if any(p.get("em_falta") for p in pendentes) and not (
                "falta" in texto or "esgotad" in texto):
            return False
        # E as opcoes oferecidas tem de ser as que existem: "Tuchaua ou
        # Refrigerante 2 Litros (esgotado)" oferecia o esgotado e escondia a
        # lata e o 600ml (teste do Eduardo, 28/09 21h38).
        for p in pendentes:
            if p.get("termo") != "refrigerante":
                continue
            from agents.conversation import PALAVRAS_DA_BEBIDA
            for opcao in p.get("opcoes") or []:
                palavras = PALAVRAS_DA_BEBIDA.get(opcao, ())
                if palavras and not any(w in texto for w in palavras):
                    return False
        return all(_sem_acento(str(p.get("termo"))) in texto for p in pendentes)

    try:
        import cardapio
        import cardapio_catalogo
        norm, _avisos, nao_achados = cardapio._normalizar_itens(
            [dict(i) for i in (memoria.get("items") or [])]
        )
        base = norm if (norm and not nao_achados) else (memoria.get("items") or [])
        quebras = cardapio_catalogo.quebras_de_lote([dict(i) for i in base])
    except Exception:
        quebras = []
    if quebras:
        if "25" not in texto:
            return False
        for q in quebras:
            palavra = _sem_acento(str(q["sabor"])).split()[0]
            if palavra not in texto:
                return False
            if str(q["sugerida"]) not in texto:
                return False
        return True

    if upsell_pendente(memoria, agora):
        ofereceu = any(m in texto for m in (
            "refrigerante", "refri", "bolo de milho", "tuchaua", "docinho",
            "brigadeiro", "beijinho", "pra acompanhar")) or e_a_oferta_configurada(reply, agora)
        # Pedido por volume: o valor tem de estar na mesma mensagem.
        if ofereceu and memoria.get("pedido_por_volume"):
            subtotal = subtotal_dos_salgados(memoria)
            if subtotal and _brl(subtotal).split()[-1] not in reply:
                return False
        return ofereceu

    return True


def pergunta_bloqueante(memoria: dict, agora=None) -> str:
    """A pergunta que TEM de sair antes de o funil andar. '' quando nao ha.

    Mesma ordem da ficha, e o texto e o que vai para o cliente. Serve para o
    codigo reescrever a resposta quando o modelo pula a etapa — foi assim que
    as travas de tamanho, de troco e de casa sem numero pararam de depender da
    boa vontade do modelo.
    """
    detalhe = _itens_ambiguos_detalhe(memoria)
    if detalhe:
        lista = " e ".join(f"{a['qtd']} {a['sabor']}" for a in detalhe)
        return (f"Esses {lista} seriam tamanho grande (lanche) ou "
                f"mini (festa)? 😊")

    # O sabor em falta vem ANTES da pergunta do refrigerante: com as duas
    # abertas, a do refrigerante cobria a etapa e o que o modelo dissesse do
    # salgado passava - numa rodada ele inventou "o Croquete de Carne esta
    # disponivel, mas o de Macaxeira que voce pediu nao" (medido em 30/09).
    fora = itens_indisponiveis(memoria)
    if fora:
        return (f"O sabor {', '.join(fora)} está em falta hoje! "
                f"Por qual sabor você quer trocar? 😊")

    divisao = pendencia_de_divisao(memoria)
    if divisao:
        return divisao

    pendentes = memoria.get("sabores_pendentes") or []
    refri = next((p for p in pendentes if p.get("termo") == "refrigerante"), None)
    if refri and len(pendentes) == 1:
        opcoes = refri.get("opcoes") or []
        texto = (", ".join(opcoes[:-1]) + " ou " + opcoes[-1]) if len(opcoes) > 1 \
            else (opcoes[0] if opcoes else "")
        aviso = (f"O {refri['em_falta']} está em falta hoje! "
                 if refri.get("em_falta") else "")
        return f"{aviso}Qual refrigerante você prefere: {texto}? 🥤"
    if pendentes:
        partes = []
        for p in pendentes:
            opcoes = (p.get("opcoes") or [])
            if len(opcoes) > 1:
                texto = ", ".join(opcoes[:-1]) + " ou " + opcoes[-1]
            else:
                texto = opcoes[0] if opcoes else ""
            partes.append(f"{p.get('qtd')} de {p.get('termo')} ({texto})")
        # "dos", nao "de os": a frase sai para o cliente.
        return f"Me confirma o sabor dos {'; '.join(partes)}? 😊"

    frase = _frase_de_lote(memoria)
    if frase:
        return frase

    return oferta_com_valor(memoria, agora)


def render_ficha(memoria: dict) -> str:
    """Bloco compacto para o system prompt: o que ja sei e o que falta."""
    linhas = []
    for campo in ("name", "items", "modality", "bairro", "endereco", "referencia",
                  "data_agendada", "horario", "payment_method"):
        valor = memoria.get(campo)
        if campo == "items":
            valor = _formatar_itens(valor)
        if campo == "data_agendada":
            # A data numerica vem primeiro porque e ela que vai no espelho
            # (BLOCO 5: "Data: [DD/MM/AAAA]"). O modelo escreve o proprio
            # resumo em vez de copiar o da tool, entao ele precisa ver o
            # formato certo AQUI - no teste de 22/09 saiu "hoje, terca-feira
            # (22/09)" porque era so isso que a ficha mostrava.
            numerica = _data_numerica(memoria.get("data_iso", ""))
            if numerica:
                valor = f"{numerica}" + (f" ({valor})" if valor else "")
        if campo == "endereco" and valor and endereco_sem_numero(memoria):
            # Sem esta marca a ficha se contradizia: a rua entrava na lista do
            # que ja esta confirmado ("nao pergunte de novo") e o numero na
            # lista do que falta coletar. O modelo desempatava descartando o
            # numero - pedia so data e horario em 4 de 5 rodadas.
            valor = f"{valor} <-- SEM O NUMERO DA CASA, o numero ainda FALTA"
        if valor:
            linhas.append(f"- {_ROTULOS[campo]}: {valor}")

    if memoria.get("frete") is not None:
        linhas.append(f"- Frete ja calculado: R$ {float(memoria['frete']):.2f}".replace(".", ","))

    troco = memoria.get("troco_para", "")
    if troco == "nao":
        linhas.append("- Troco: cliente tem o valor trocado (nao precisa)")
    elif troco == "nao_informado":
        linhas.append("- Troco: perguntado, cliente nao informou - nao pergunte de novo")
    elif troco:
        linhas.append(f"- Troco para: {_brl(float(troco))}")

    pendentes = faltando(memoria)
    detalhe = pendencia_de_detalhe(memoria)
    duvida_item = pendencia_de_item(memoria)
    lote_quebrado = pendencia_de_lote(memoria)
    esgotado = pendencia_de_indisponivel(memoria)
    sabor_aberto = pendencia_de_sabor(memoria)
    upsell = oferta_com_valor(memoria)
    bloco = ["=== FICHA DESTE ATENDIMENTO (memoria do cliente) ==="]
    if linhas:
        bloco.append("JA CONFIRMADO PELO CLIENTE - nao pergunte de novo, nao altere:")
        bloco += linhas
    else:
        bloco.append("Nenhum dado coletado ainda.")

    # Janela em que "quero mais 30 coxinhas" e ambiguo: pode ser acrescimo ao
    # pedido que acabou de ir para a producao, ou pedido novo. Sem este aviso o
    # modelo aplicava a regra de alteracao do BLOCO 0 e transferia para humano.
    minutos = pedido_recente(memoria)
    if minutos is None and not memoria.get("pedido_enviado"):
        # Sem esta linha o modelo aplicava a regra de "pedido em linha de
        # producao" do BLOCO 0 a um pedido que ainda estava sendo montado: o
        # cliente respondeu "PODE SER" a uma pergunta de quantidade e foi
        # mandado para a fila do humano, sem nunca ter visto um resumo (teste
        # do dono, 23/09 15:32).
        bloco.append(
            "NENHUM PEDIDO FOI ENVIADO AINDA nesta conversa. A regra de "
            "'pedido ja entrou em linha de producao' do BLOCO 0 NAO se aplica: "
            "e PROIBIDO dizer que o pedido esta em producao e PROIBIDO "
            "transferir por causa dela. Mudar item, quantidade, endereco, dia "
            "ou pagamento agora e atendimento normal - apenas atualize e siga."
        )
    if minutos is not None:
        bloco.append(
            f"ATENCAO - o cliente fechou um pedido ha {minutos} minuto(s). Se ele "
            "pedir mais itens agora, NAO presuma nem transfira: PERGUNTE se e "
            "para somar ao pedido que acabou de sair ou se e um pedido novo. "
            "Somar ao anterior e alteracao (BLOCO 0: transfira). Pedido novo e "
            "atendimento normal: siga o funil desde a modalidade."
        )

    # Volume dito sem sabor ("quero um cento"): a regra do dono manda oferecer
    # ate Total/25 sabores e perguntar o preparo, em vez de so perguntar "qual
    # sabor?" — o cliente nao sabe que pode dividir.
    _volume = 0
    try:
        _volume = int(memoria.get("volume_pendente") or 0)
    except (TypeError, ValueError):
        _volume = 0
    _divisao = pendencia_de_divisao(memoria)
    if _divisao:
        bloco.append(
            f"DIVISAO DO VOLUME PENDENTE: os sabores ja foram ditos e nao fecham "
            f"em lotes de 25. Pergunte SO isso, exatamente assim: \"{_divisao}\" "
            f"E PROIBIDO pedir os sabores de novo ou oferecer outro sabor.")
    if _volume and not memoria.get("items"):
        _quantos = max(1, _volume // 25)
        _preparo = (memoria.get("preparo_pendente") or "").strip()
        if _preparo:
            _pergunta = (
                f"pergunte QUAIS sabores {_preparo}s ele quer. O cliente JA "
                f"disse que e {_preparo}: e PROIBIDO perguntar o preparo "
                f"('frito ou assado') de novo."
            )
        else:
            _pergunta = "pergunte QUAIS sabores e o PREPARO (frito ou assado)."
        bloco.append(
            f"VOLUME SEM SABOR: o cliente pediu {_volume} unidades e nao disse "
            f"os sabores. Diga que ele pode escolher ate {_quantos} "
            f"{'sabor' if _quantos == 1 else 'sabores'} (25 por opcao) e "
            f"{_pergunta} Nao "
            f"pergunte so 'qual sabor?': ele nao sabe que pode dividir."
        )

    if memoria.get("items"):
        bloco.append(
            "Os itens acima sao o que o cliente pediu. O preco deles vem de "
            "consultar_preco/calculate_order - nunca do seu calculo."
        )
        # O codigo ja parou de mandar perguntar o tamanho acima de 20 unidades,
        # mas o modelo continuava perguntando por conta propria - consultar_preco
        # devolve o sabor de pacote E o salgado grande com o mesmo nome, e ele
        # resolvia a duvida perguntando. Aqui a regra vira instrucao positiva:
        # nessa quantidade nao ha duvida nenhuma a resolver.
        if tamanho_ja_definido(memoria):
            _minimo = _minimo_de_festa()
            bloco.append(
                f"TAMANHO JA DEFINIDO PELA QUANTIDADE: todo item deste pedido "
                f"tem {_minimo} unidades ou mais, entao e FESTA (lote de 25 por "
                f"sabor), no preco de pacote. E PROIBIDO perguntar 'festa ou "
                f"salgado grande de balcao?', 'porcao ou unidade avulsa?' ou "
                f"qualquer variante disso - nao ha duvida a resolver. Essa "
                f"pergunta so existe de 1 a {_minimo - 1} unidades."
            )

    if lote_quebrado:
        bloco.append(lote_quebrado)
    elif memoria.get("items") and not duvida_item:
        # O dono foi explicito em 25/09: pedido certo nao leva aula. Com 50
        # coxinhas e 150 churros na ficha - os dois multiplos de 25 - ela
        # respondeu "a porcao minima e de 25 por sabor, o pedido deve ser em
        # multiplos de 25" e ainda pediu confirmacao.
        bloco.append(
            "QUANTIDADES JA NA REGRA DE LOTE: fecham nos multiplos exigidos, "
            "nao ha nada a ajustar. E PROIBIDO explicar 'multiplos de 25', "
            "'minimo por sabor', 'porcoes de 25' ou 'de 25 em 25', e PROIBIDO "
            "pedir confirmacao do que ja esta certo - confirme o pedido e siga "
            "o funil."
        )

    if duvida_item:
        bloco.append(duvida_item)
    if sabor_aberto:
        bloco.append(sabor_aberto)
    if esgotado:
        bloco.append(esgotado)
    if detalhe:
        bloco.append(detalhe)
    if upsell:
        bloco.append(
            f"OFERTA ADICIONAL PENDENTE (BLOCO 2): o pedido de salgados ja esta "
            f"definido e voce AINDA NAO ofereceu o acompanhamento. Mande esta "
            f"frase, exatamente assim: \"{upsell}\""
        )

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

    # Etapa travada nao apaga a lista do que falta: ela so a adia. Sem esta
    # lista a ficha deixava de dizer, por exemplo, que a rua veio SEM NUMERO -
    # e o dado voltava a se perder assim que a pergunta obrigatoria fosse
    # respondida.
    if (duvida_item or sabor_aberto or esgotado or lote_quebrado or upsell) and pendentes:
        bloco.append("AINDA FALTA COLETAR, mas NAO nesta mensagem (primeiro a "
                     "PROXIMA PERGUNTA OBRIGATORIA abaixo):")
        bloco += [f"- {_ROTULOS[c]}" for c in pendentes]

    if duvida_item:
        # Fura a fila do funil: o que o cliente pediu precisa estar definido
        # antes de coletar entrega, dia e horario.
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: esclarecer o ITEM AMBIGUO acima. "
            "Nao pergunte modalidade, endereco, dia, horario nem pagamento "
            "nesta mensagem, e nao apresente resumo."
        )
    elif sabor_aberto:
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: qual e o SABOR NAO IDENTIFICADO "
            "acima. Nao pergunte modalidade, endereco, dia, horario nem "
            "pagamento nesta mensagem, e nao apresente resumo."
        )
    elif esgotado:
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: a troca do SABOR ESGOTADO acima. "
            "Nao pergunte modalidade, endereco, dia, horario nem pagamento "
            "nesta mensagem, e nao apresente resumo."
        )
    elif lote_quebrado:
        # Mesma fila furada, e pelo mesmo motivo: a quantidade e parte do que o
        # cliente pediu. A trava ja estava escrita na ficha, mas a lista de
        # coleta saia JUNTO com ela na mesma volta - o modelo lia as duas
        # ordens, escolhia a de baixo e ia cobrar endereco e pagamento com o
        # lote ainda quebrado. Foi a primeira queixa do dono em 26/09 ("o
        # fluxo correto e travar a regra dos sabores em lotes de 25 antes de
        # puxar os dados de entrega") e voltou em outros dois testes.
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: o ajuste da QUANTIDADE QUEBRADA "
            "acima, com TODOS os sabores fora da regra numa frase so. "
            "Nao pergunte modalidade, endereco, bairro, dia, horario nem "
            "pagamento nesta mensagem, e nao apresente resumo."
        )
    elif upsell:
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: a OFERTA ADICIONAL acima, sozinha. "
            "Nao pergunte endereco, bairro, dia, horario nem pagamento nesta "
            "mensagem, e nao apresente resumo - o upsell vem ANTES da "
            "logistica, e e a etapa que voce mais pula."
        )
    elif pendentes and memoria.get("modality") == "entrega":
        # Escolhida a entrega, o resto vai numa mensagem so. O dono contou dez
        # perguntas pingadas para fechar um pedido (23/09) e pediu bairro, rua
        # com numero, dia, horario e pagamento juntos. Antes da modalidade a
        # coleta continua de uma em uma: perguntar endereco sem o cliente ter
        # escolhido entrega e exatamente o que o funil evita.
        bloco.append("AINDA FALTA COLETAR:")
        bloco += [f"- {_ROTULOS[c]}" for c in pendentes]
        bloco.append(
            "PECA TODOS ESSES DADOS NESTA MESMA MENSAGEM, em lista vertical "
            "curta (um por linha), e SO eles. E PROIBIDO perguntar um de cada "
            "vez, PROIBIDO repetir pergunta de dado que ja esta na ficha acima "
            "e PROIBIDO pedir ponto de referencia."
        )
        bloco.append(
            "PROIBIDO nesta mensagem: apresentar resumo do pedido, informar total "
            "ou chamar send_order_to_cardapio - a ficha ainda tem pendencias."
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


# O que sobrevive ao fim de um pedido: sao dados do CLIENTE, nao do pedido.
# O prompt manda nao repetir pergunta ja respondida, e endereco raramente muda.
_CAMPOS_DO_CLIENTE = ("name", "bairro", "endereco", "referencia")

# Por quanto tempo depois de um pedido sair ainda faz sentido perguntar se o
# cliente quer SOMAR ao que acabou de ir para a producao. Passado isso, pedir
# mais coisa so pode ser pedido novo. O proprio prompt estima 30 a 40 minutos
# de entrega, entao meia hora cobre a janela em que da para ajustar.
MINUTOS_PEDIDO_RECENTE = 30


def pedido_recente(memoria: dict, agora=None) -> int | None:
    """Minutos desde o ultimo pedido fechado, se ainda estiver na janela.

    Fora da janela devolve None: nao ha pedido em producao para alterar, e
    perguntar "e novo ou e alteracao?" so atrapalharia quem volta dias depois.
    """
    quando = memoria.get("ultimo_pedido_em")
    if not quando:
        return None
    agora = agora or _dt.datetime.now()
    try:
        saiu = _dt.datetime.fromisoformat(str(quando))
    except (TypeError, ValueError):
        return None
    minutos = (agora - saiu).total_seconds() / 60
    if 0 <= minutos <= MINUTOS_PEDIDO_RECENTE:
        return int(minutos)
    return None


# Janela do "pos-pedido": aviso de status ("ja saiu?", "vou autorizar a
# portaria") e acrescimo ("inclui 2 cocas") so fazem sentido enquanto o pedido
# ainda esta sendo feito ou entregue. Maior que MINUTOS_PEDIDO_RECENTE porque
# pedido agendado para a noite continua "em andamento" desde a manha.
HORAS_POS_PEDIDO = 12


def em_pos_pedido(memoria: dict, agora=None, aceita_itens: bool = False) -> bool:
    """Ha um pedido fechado ha menos de HORAS_POS_PEDIDO e nenhum em montagem?

    `aceita_itens` e para o acrescimo: "inclui 10 coxinhas" poe item na ficha
    na mesma mensagem, e mesmo assim e acrescimo, nao pedido novo.
    """
    if memoria.get("items") and not aceita_itens:
        return False
    quando = memoria.get("ultimo_pedido_em")
    if not quando:
        return False
    agora = agora or _dt.datetime.now()
    try:
        saiu = _dt.datetime.fromisoformat(str(quando))
    except (TypeError, ValueError):
        return False
    return _dt.timedelta(0) <= (agora - saiu) <= _dt.timedelta(hours=HORAS_POS_PEDIDO)


def _so_dados_do_cliente(memoria: dict) -> dict:
    """Ficha zerada mantendo so o que e do CLIENTE, nao do pedido."""
    limpa = _empty()
    for campo in _CAMPOS_DO_CLIENTE:
        if memoria.get(campo):
            limpa[campo] = memoria[campo]
    for campo in ("ultimo_pedido_em", "ultimo_pedido_modalidade"):
        if memoria.get(campo):
            limpa[campo] = memoria[campo]
    return limpa


def registrar_pedido_enviado(phone: str, memoria: dict) -> None:
    """Fecha o pedido e deixa a ficha pronta para o proximo.

    Antes isto so marcava `pedido_enviado = True` e guardava o resto. O segundo
    pedido do mesmo cliente saia com o endereco e o pagamento do anterior e com
    a data do pedido antigo - que ja tinha passado, e ainda travava o
    atendimento no portao de agendamento.

    Guarda a hora do fechamento: e ela que diz, logo depois, se "agora quero 30
    coxinhas" pode ser um acrescimo ao pedido que acabou de sair ou ja e outro
    pedido.
    """
    limpa = _so_dados_do_cliente(memoria)
    # A sessao continua a mesma: o cliente acabou de falar com a gente.
    for campo in _CAMPOS_DE_SESSAO:
        limpa[campo] = memoria.get(campo, False)
    limpa["ultimo_pedido_em"] = _dt.datetime.now().isoformat(timespec="seconds")
    limpa["ultimo_pedido_modalidade"] = memoria.get("modality") or ""

    memoria.clear()
    memoria.update(limpa)
    save(phone, memoria)
    print(f"[memoria] {phone}: pedido fechado, ficha limpa "
          f"(mantidos: {', '.join(c for c in _CAMPOS_DO_CLIENTE if limpa.get(c))})")
