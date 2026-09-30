"""
horario.py — Calendario e expediente da BALBS, resolvidos em codigo.

Motivacao: saudacao, "estamos abertos?" e "que dia e hoje?" sao contas de
calendario, nao interpretacao de texto. Enquanto isso ficou como regra escrita
no prompt, o modelo pulava a saudacao quando o cliente ja mandava o pedido
pronto, e assumia "hoje" sem nunca confirmar o dia com o cliente. Aqui vira
funcao pura: o agente recebe o resultado, nao a regra.

Expediente (base oficial): segunda a sabado, 07h40 as 20h00, com intervalo de
almoco das 12h01 as 13h59. Domingo fechado.
"""

import re
import unicodedata
from datetime import datetime, timedelta, date

# Porto Velho - RO = UTC-4, sem horario de verao.
_TZ_OFFSET = timedelta(hours=4)

ABERTURA = (7, 40)
FECHAMENTO = (20, 0)
INTERVALO_INICIO = (12, 1)
INTERVALO_FIM = (13, 59)

# Acentuados: estes nomes vao direto para a tela do cliente ("Data: hoje,
# sabado (19/09)" destoava do resto do texto). O dicionario de ENTRADA abaixo
# continua sem acento de proposito: ele compara texto ja normalizado.
_DIAS = [
    "segunda-feira", "terça-feira", "quarta-feira",
    "quinta-feira", "sexta-feira", "sábado", "domingo",
]

_DIAS_ENTRADA = {
    "segunda": 0, "segunda-feira": 0, "seg": 0,
    "terca": 1, "terca-feira": 1, "ter": 1,
    "quarta": 2, "quarta-feira": 2, "qua": 2,
    "quinta": 3, "quinta-feira": 3, "qui": 3,
    "sexta": 4, "sexta-feira": 4, "sex": 4,
    "sabado": 5, "sab": 5,
    "domingo": 6, "dom": 6,
}


def _norm(texto: str) -> str:
    texto = (texto or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def agora() -> datetime:
    """Horario local de Porto Velho."""
    return datetime.utcnow() - _TZ_OFFSET


def para_utc(local: datetime) -> datetime:
    """Hora local de Porto Velho -> UTC.

    O banco fala UTC (ver database.agora_utc). Quando uma conta e feita no
    relogio do cliente - a janela de envio do follow-up e 08h-18h DELE, nao
    08h-18h UTC - o resultado precisa voltar para UTC antes de ser gravado.
    """
    return local + _TZ_OFFSET


def _minutos(hora: int, minuto: int) -> int:
    return hora * 60 + minuto


def saudacao(now: datetime | None = None) -> str:
    """Bom dia / Boa tarde / Boa noite pela hora local."""
    now = now or agora()
    if 6 <= now.hour < 12:
        return "Bom dia"
    if 12 <= now.hour < 18:
        return "Boa tarde"
    return "Boa noite"


def nome_dia(d: date) -> str:
    return _DIAS[d.weekday()]


def formatar_data(d: date, now: datetime | None = None) -> str:
    """'hoje, sexta-feira (19/09)' — sempre com o dia explicito.

    O rotulo relativo sozinho ("hoje") foi o que permitiu fechar pedido sem o
    cliente nunca ver a data; aqui ele vem sempre acompanhado do dia da semana
    e da data numerica.
    """
    now = now or agora()
    hoje = now.date()
    delta = (d - hoje).days
    if delta == 0:
        prefixo = "hoje, "
    elif delta == 1:
        prefixo = "amanhã, "
    else:
        prefixo = ""
    return f"{prefixo}{nome_dia(d)} ({d.strftime('%d/%m')})"


def eh_dia_util(d: date) -> bool:
    """Segunda a sabado."""
    return d.weekday() != 6


def proximo_dia_util(a_partir_de: date) -> date:
    d = a_partir_de
    while not eh_dia_util(d):
        d += timedelta(days=1)
    return d


def status(now: datetime | None = None) -> dict:
    """Situacao do expediente agora.

    Retorna: aberto (bool), motivo, mensagem pronta para o cliente e a data
    sugerida para agendar quando estamos fechados.
    """
    now = now or agora()
    minuto_atual = _minutos(now.hour, now.minute)

    if not eh_dia_util(now.date()):
        proximo = proximo_dia_util(now.date() + timedelta(days=1))
        return {
            "aberto": False,
            "motivo": "domingo",
            "aceita_hoje": False,
            "sugestao_data": proximo,
            "mensagem": (
                "Aos domingos a gente não abre! Posso deixar seu pedido agendado "
                f"para {formatar_data(proximo, now)}? 😊"
            ),
        }

    if minuto_atual < _minutos(*ABERTURA):
        return {
            "aberto": False,
            "motivo": "antes_da_abertura",
            "aceita_hoje": True,
            "sugestao_data": now.date(),
            "mensagem": (
                "A gente começa a atender às 07h40! Já posso deixar seu pedido "
                "anotado para hoje a partir desse horário? 😊"
            ),
        }

    if minuto_atual >= _minutos(*FECHAMENTO):
        proximo = proximo_dia_util(now.date() + timedelta(days=1))
        return {
            "aberto": False,
            "motivo": "apos_fechamento",
            "aceita_hoje": False,
            "sugestao_data": proximo,
            "mensagem": (
                "Nossa produção já encerrou por hoje! Posso deixar seu pedido "
                f"agendado para {formatar_data(proximo, now)}, a partir das 07h40? 💛"
            ),
        }

    if _minutos(*INTERVALO_INICIO) <= minuto_atual <= _minutos(*INTERVALO_FIM):
        return {
            "aberto": False,
            "motivo": "intervalo",
            "aceita_hoje": True,
            "sugestao_data": now.date(),
            "mensagem": (
                "Estamos no intervalo de almoço até às 14h00! Posso já adiantar "
                "o seu pedido para as 14h? 😊"
            ),
        }

    return {
        "aberto": True,
        "motivo": "aberto",
        "aceita_hoje": True,
        "sugestao_data": now.date(),
        "mensagem": "",
    }


# Ate quantos dias atras uma data sem ano e lida como engano (data passada), e
# nao como o mesmo dia do ano que vem.
DIAS_DE_DATA_PASSADA = 60


def resolver_data(texto: str, now: datetime | None = None) -> date | None:
    """Converte o que o cliente escreveu em uma data absoluta.

    'hoje', 'amanha', 'sexta', '20/09' e '20/09/2026' viram um date. Qualquer
    outra coisa retorna None — e a falta de data e o que obriga a pergunta.
    """
    now = now or agora()
    hoje = now.date()
    t = _norm(texto)
    # "HOJE NAO" e a recusa da oferta de acompanhamento, nao uma data. Lido
    # como "hoje", trocava o "amanha as 20h" que o cliente ja tinha dado e ele
    # ouvia que o horario de hoje ja tinha passado (teste do dono, 28/09 20h10).
    t = re.sub(r"\bhoje\s+n[aã]o\b|\bn[aã]o\s+(?:e\s+|eh\s+|vai\s+ser\s+|sera\s+|pra\s+|para\s+)?hoje\b",
               " ", t).strip()
    if not t:
        return None

    if re.search(r"\bhoje\b", t):
        return hoje
    if re.search(r"\bdepois de amanha\b", t):
        return hoje + timedelta(days=2)
    if re.search(r"\bamanha\b", t):
        return hoje + timedelta(days=1)

    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", t)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        ano = int(m.group(3)) if m.group(3) else hoje.year
        if ano < 100:
            ano += 2000
        try:
            resolvida = date(ano, mes, dia)
        except ValueError:
            return None
        # Data sem ano que ja passou e o ano que vem so quando passou HA MUITO:
        # "10/01" dito em dezembro. Passada ha poucos dias e engano do cliente -
        # "25/09" dito em 29/09 virou 25/09/2027 e o espelho saiu para daqui a um
        # ano (teste do dono, 29/09 15h25). Devolvida como esta, a validacao
        # responde "essa data ja passou" e pergunta o dia de novo.
        if (not m.group(3) and resolvida < hoje
                and (hoje - resolvida).days > DIAS_DE_DATA_PASSADA):
            try:
                resolvida = date(ano + 1, mes, dia)
            except ValueError:
                return None
        return resolvida

    # "dia 16", "para o dia 2": so o numero do dia, sem mes. E como o cliente
    # marca quando o mes e obvio. Sem isto a extracao devolvia None, a ficha
    # ficava sem data e o modelo preenchia sozinho — no teste do dono ele
    # escreveu "16/09/2026", que ja tinha passado nove dias antes (25/09).
    m = re.search(r"\bdia\s+(\d{1,2})\b", t)
    if m:
        dia = int(m.group(1))
        for ano, mes in ((hoje.year, hoje.month),
                         (hoje.year + (hoje.month == 12), hoje.month % 12 + 1)):
            try:
                candidata = date(ano, mes, dia)
            except ValueError:
                continue
            # Dia que ja passou neste mes e o mesmo dia do mes que vem.
            if candidata >= hoje:
                return candidata
        return None

    for nome, indice in _DIAS_ENTRADA.items():
        if re.search(r"\b" + re.escape(nome) + r"\b", t):
            delta = (indice - hoje.weekday()) % 7
            if delta == 0:
                delta = 7  # "sexta" dita numa sexta e a proxima, nao hoje
            return hoje + timedelta(days=delta)

    return None


# Onde ha dinheiro na frase, virgula e centavo - nao minuto.
_RX_DINHEIRO = re.compile(r"r\$|reais|troco|pagar")

# Onde ha endereco na frase, virgula e parte do numero da casa. O Whisper
# transcreveu "rua Salgado Filho, 2152" como "rua Salgado Filho 21, 52", e o
# "21,52" virou 21h52 por cima das 8h que o cliente ja tinha dado (teste do
# dono por voz, 29/09 20h06).
_RX_ENDERECO_NA_FRASE = re.compile(
    r"\b(?:rua|avenida|av|travessa|alameda|estrada|rodovia|bairro|numero|n[oº°])\b")

# "(?<!\d)" em vez de "\b": o cliente cola a hora na palavra anterior
# ("PODE SER PARA AS19:50") e "\b" nao ve fronteira entre "s" e "1", entao o
# horario nao existia para o parser e a pergunta era repetida (teste do dono,
# 25/09). As duas metades da concatenacao sao raw de proposito: "\\b" num
# literal sem o r vira byte de backspace e o regex nunca casa.
_RX_HORA = re.compile(r"(?<!\d)(\d{1,2})\s*(?:h|:|horas?)\s*(\d{2})?\b")

# A virgula e como se escreve hora aqui ("19,50"), mas EXIGE os dois digitos de
# minuto: sem isso "45, amanha as 16h" casava "45," como hora, 45 nao e hora
# valida e o horario da mensagem inteira se perdia.
_RX_HORA_VIRGULA = re.compile(r"(?<!\d)(\d{1,2})\s*,\s*(\d{2})\b")

# "amanha as 9", "pras 10", "as 9 e meia": sem "h" nem "horas", mas a
# preposicao diz que e hora. Exige que depois do numero NAO venha palavra
# qualquer - "as 50 coxinhas" nao e horario (teste de 30/09: "amanha as nove"
# chegava sem horario e o funil pedia a hora de novo).
_RX_HORA_POR_PREPOSICAO = re.compile(
    r"(?:\bas|\bpras|\bpara as|\bate as)\s+(\d{1,2})(?:\s+e\s+(meia))?"
    r"(?=\s*$|\s*[^\w\s]|\s+(?:da\b|de\s+manha|hoje|amanha|pra\b|para\b|no\b|na\b"
    r"|pode\b|ta\b|esta\b|entao\b|por\s+favor|ok\b|beleza|obrigad))")


def parse_horario(texto: str) -> tuple[int, int] | None:
    """'18h', '18:30', 'as 8h15', '18 horas', '19,50' -> (hora, minuto)."""
    t = _norm(texto)
    moldes = [_RX_HORA]
    if not _RX_DINHEIRO.search(t) and not _RX_ENDERECO_NA_FRASE.search(t):
        moldes.append(_RX_HORA_VIRGULA)

    # Percorre TODOS os candidatos em vez de desistir no primeiro: numa frase
    # como "45, amanha as 16h" o primeiro numero nao e hora, e o horario esta
    # mais adiante.
    def _periodo(hora: int, fim: int) -> int:
        # "as 2 da tarde" e 14h, "8 da noite" e 20h. Sem isto voltava 2h.
        depois = t[fim:fim + 14]
        if hora < 12 and re.match(r"\s*(?:horas?\s+)?da\s+(?:tarde|noite)", depois):
            return hora + 12
        return hora

    for molde in moldes:
        for m in molde.finditer(t):
            hora = int(m.group(1))
            minuto = int(m.group(2) or 0)
            if hora <= 23 and minuto <= 59:
                return _periodo(hora, m.end()), minuto
    # Por ultimo, e so se nada acima achou: a hora dita so pela preposicao.
    for m in _RX_HORA_POR_PREPOSICAO.finditer(t):
        hora = int(m.group(1))
        if 1 <= hora <= 23:
            return _periodo(hora, m.end()), 30 if m.group(2) else 0
    return None


def formatar_horario(hora: int, minuto: int) -> str:
    return f"{hora:02d}h{minuto:02d}" if minuto else f"{hora}h"


def validar_agendamento(
    data_pedido: date | None,
    horario_texto: str,
    now: datetime | None = None,
) -> tuple[bool, str, date | None, str]:
    """Checa se data + horario cabem no expediente.

    Retorna (ok, mensagem, data_sugerida, campo). Quando nao cabe, a mensagem
    ja traz a alternativa para o cliente — a recusa nunca sai sem uma saida — e,
    se a alternativa for um DIA (e nao um horario no mesmo dia), ele vem no
    terceiro campo. Quem chama precisa dele: sem guardar o dia oferecido, o
    "pode sim" do cliente logo depois nao tinha data nenhuma para preencher e a
    pergunta era repetida.

    `campo` diz QUAL dado nao cabe — "data" ou "horario" — para quem chama
    limpar so esse. Antes quem chamava adivinhava lendo a mensagem
    (`"horario" in motivo.lower()`), e a mensagem escreve "horário", com
    acento: a comparacao dava False, o codigo limpava a DATA no lugar da hora e
    o cliente que ja tinha dito "hoje" era perguntado do dia de novo (teste do
    dono, 26/09 19h21). Campo em vez de texto nao tem como errar acento.
    """
    now = now or agora()

    if data_pedido is None:
        return False, "Falta a data do pedido.", None, "data"

    if data_pedido < now.date():
        return False, "Essa data já passou. Para qual dia você quer o pedido? 😊", None, "data"

    if not eh_dia_util(data_pedido):
        proximo = proximo_dia_util(data_pedido + timedelta(days=1))
        return False, (
            "Aos domingos a gente não abre! Posso deixar para "
            f"{formatar_data(proximo, now)}? 😊"
        ), proximo, "data"

    hm = parse_horario(horario_texto)
    if hm is None:
        return False, "Falta o horario do pedido.", None, "horario"

    minuto_pedido = _minutos(*hm)

    if minuto_pedido < _minutos(*ABERTURA):
        return False, (
            "A gente só começa às 07h40! Pode ser a partir desse horário? 😊"
        ), None, "horario"

    # ">" e nao ">=": a loja atende ATE as 20h, entao 20h00 e horario valido
    # - o ultimo. Com ">=" ela recusava as 20h em ponto e mandava o cliente
    # escolher "antes disso", e o dono bateu nisso tres vezes seguidas no
    # teste de 25/09 as 18h.
    if minuto_pedido > _minutos(*FECHAMENTO):
        return False, (
            "Nosso atendimento vai até às 20h00! Qual horário antes disso "
            "funciona melhor pra você? 😊"
        ), None, "horario"

    if _minutos(*INTERVALO_INICIO) <= minuto_pedido <= _minutos(*INTERVALO_FIM):
        return False, (
            "Das 12h01 às 13h59 estamos no intervalo de almoço! Pode ser às 14h "
            "ou prefere antes das 12h? 😊"
        ), None, "horario"

    # Horario de hoje que ja passou nao e agendamento, e pedido perdido.
    if data_pedido == now.date() and minuto_pedido <= _minutos(now.hour, now.minute):
        return False, (
            f"Esse horário de hoje já passou (agora são {now.strftime('%Hh%M')})! "
            "Qual outro horário fica bom? 😊"
        ), None, "horario"

    return True, "", None, ""


def contexto_para_prompt(now: datetime | None = None, aviso_ja_dado: bool = False) -> str:
    """Bloco de fatos de calendario injetado no system prompt dos agentes.

    `aviso_ja_dado`: o aviso de fechado/intervalo ja saiu nesta conversa (ou o
    pedido ja esta agendado num horario valido). Com "Aviso obrigatorio" no
    prompt a cada mensagem, o modelo o repetia - em 3 de 6 rodadas ele
    respondeu o "Hj nao" da oferta com "Estamos no intervalo... adiantar para
    as 14h?" num pedido para amanha as 9h (medido em 30/09).
    """
    now = now or agora()
    st = status(now)
    linhas = [
        f"Agora em Porto Velho: {nome_dia(now.date())}, {now.strftime('%d/%m/%Y as %Hh%M')}",
        f'Saudacao correta neste momento: "{saudacao(now)}" (nao use outra)',
        f"Hoje e: {formatar_data(now.date(), now)}",
        f"Amanha e: {formatar_data(now.date() + timedelta(days=1), now)}",
        "Expediente: segunda a sabado, 07h40 as 20h00 (intervalo 12h01-13h59)",
        f"Situacao agora: {'ABERTO' if st['aberto'] else 'FECHADO (' + st['motivo'] + ')'}",
    ]
    if not st["aberto"] and aviso_ja_dado:
        linhas.append(
            "O aviso de horario JA FOI DADO nesta conversa (ou o pedido ja esta "
            "agendado para um horario valido). E PROIBIDO repeti-lo: siga o pedido.")
    elif not st["aberto"]:
        linhas.append(f"Aviso obrigatorio ao cliente: {st['mensagem']}")
    if not st["aceita_hoje"]:
        linhas.append(
            "NAO aceite pedido para hoje. Ofereca "
            f"{formatar_data(st['sugestao_data'], now)}."
        )
    return "\n".join(linhas)
