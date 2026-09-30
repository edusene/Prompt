"""
conversation.py — ConversationState e ConversationManager.
Gerencia estado compartilhado e orquestra sub-agentes.
"""

from dataclasses import dataclass, field
import time
import json
import re

import facts


@dataclass
class ConversationState:
    """Estado compartilhado entre todos os agentes."""

    # Identificacao
    phone: str = ""
    name: str = ""

    # Fluxo
    phase: str = "triagem"
    active_agent: str = "triagem"
    intent: str = ""

    # Dados do pedido
    items: list = field(default_factory=list)

    # Dados de logistica
    modality: str = ""
    bairro: str = ""
    endereco: str = ""
    referencia: str = ""
    horario: str = ""
    data_agendada: str = ""
    # Data resolvida em ISO (AAAA-MM-DD). data_agendada guarda o que o cliente
    # escreveu ("hoje"); data_iso guarda o dia de verdade. Sem essa separacao,
    # "hoje" seguia para o resumo sem nunca virar um dia concreto na tela.
    data_iso: str = ""

    # Dados de pagamento
    payment_method: str = ""
    troco_para: float = 0

    # Calculos
    subtotal: float = 0
    frete: float = 0
    taxa_retorno: float = 0
    total: float = 0

    # Controle
    upsell_oferecido: bool = False
    resumo_apresentado: bool = False
    pedido_confirmado: bool = False
    pedido_enviado: bool = False

    # A saudacao so pode sair uma vez, e tem de sair na PRIMEIRA resposta —
    # inclusive quando o cliente ja manda o pedido inteiro de uma vez, que era
    # justamente o caso em que ela era pulada.
    saudacao_feita: bool = False
    # Aviso de "estamos fechados" ja dado nesta conversa.
    aviso_horario_dado: bool = False
    # Modalidade dita pelo cliente, nao inferida por uma palavra solta.
    modalidade_confirmada: bool = False
    # Data dita pelo cliente. Falso = ainda precisa perguntar o DIA.
    data_confirmada: bool = False

    # Tempo atual (injetado pelo ConversationManager)
    current_time: str = ""

    # Carregado em cada mensagem; nao e persistido no estado da conversa.
    knowledge_context: str = ""
    calendario: str = ""
    diretivas_runtime: str = ""

    # Historico
    history: list = field(default_factory=list)
    collected_data: dict = field(default_factory=dict)
    last_bot_message: str = ""

    def to_dict(self) -> dict:
        """Converte para dicionario."""
        return {
            "phone": self.phone,
            "name": self.name,
            "phase": self.phase,
            "active_agent": self.active_agent,
            "intent": self.intent,
            "items": self.items,
            "modality": self.modality,
            "bairro": self.bairro,
            "endereco": self.endereco,
            "referencia": self.referencia,
            "horario": self.horario,
            "data_agendada": self.data_agendada,
            "data_iso": self.data_iso,
            "payment_method": self.payment_method,
            "troco_para": self.troco_para,
            "subtotal": self.subtotal,
            "frete": self.frete,
            "taxa_retorno": self.taxa_retorno,
            "total": self.total,
            "upsell_oferecido": self.upsell_oferecido,
            "resumo_apresentado": self.resumo_apresentado,
            "pedido_confirmado": self.pedido_confirmado,
            "pedido_enviado": self.pedido_enviado,
            "saudacao_feita": self.saudacao_feita,
            "aviso_horario_dado": self.aviso_horario_dado,
            "modalidade_confirmada": self.modalidade_confirmada,
            "data_confirmada": self.data_confirmada,
            "history": self.history[-20:],
            "collected_data": self.collected_data,
            "last_bot_message": self.last_bot_message,
        }

    # ── Contexto de execucao injetado no system prompt de todos os agentes ──

    def dados_faltando(self) -> list:
        """Dados obrigatorios ainda nao coletados, na ordem em que devem ser
        perguntados.

        A ordem importa: modalidade vem ANTES de bairro e endereco, senao o
        agente comeca a falar de frete de entrega sem o cliente ter dito que
        queria entrega. Data vem ANTES de horario.
        """
        faltando = []
        if not self.name:
            faltando.append("nome do cliente")
        if not self.items:
            faltando.append("itens do pedido")
        if not self.modality:
            faltando.append("modalidade (entrega ou retirada)")
        elif self.modality == "entrega":
            if not self.bairro:
                faltando.append("bairro")
            if not self.endereco:
                faltando.append("rua e numero")
        if not self.data_iso or not self.data_confirmada:
            faltando.append("data (o DIA do pedido)")
        if not self.horario:
            faltando.append("horario")
        # Na retirada o pagamento e no balcao e o prompt proibe perguntar; so a
        # entrega precisa da forma antes do resumo. Entra aqui para caber na
        # mesma mensagem de coleta, em vez de virar mais uma rodada sozinha.
        if self.modality == "entrega" and not self.payment_method:
            faltando.append("forma de pagamento")
        return faltando

    def bloco_runtime(self) -> str:
        """Calendario + diretivas da vez, para o fim do system prompt.

        Fica no fim de proposito: e a ultima coisa que o modelo le antes de
        responder, e vence o que o prompt em markdown disser em contrario.
        """
        partes = []
        if self.calendario:
            partes.append("=== CALENDARIO E EXPEDIENTE (FATOS, NAO INTERPRETE) ===\n" + self.calendario)
        elif self.current_time:
            partes.append(f"Data e hora atual (Porto Velho): {self.current_time}")
        if self.diretivas_runtime:
            partes.append("=== DIRETIVAS OBRIGATORIAS DESTA RESPOSTA ===\n" + self.diretivas_runtime)
        return ("\n\n" + "\n\n".join(partes)) if partes else ""

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationState":
        """Cria instancia a partir de dicionario."""
        fields = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in fields})

    def save_to_db(self):
        """Salva estado no banco de dados."""
        try:
            import database
            estado = self.to_dict()
            # Carimbo em hora LOCAL, igual ao conversation_memory.save: e o que
            # _sessao_expirou compara. A coluna updated_at da tabela guarda UTC
            # e nao volta na leitura (get_conversation_state devolve so o JSON).
            estado["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            database.save_conversation_state(self.phone, estado)
        except Exception as e:
            print(f"[state] Erro ao salvar: {e}")

    @classmethod
    def load_from_db(cls, phone: str) -> "ConversationState":
        """Carrega estado do banco de dados.

        A saudacao e o aviso de expediente valem por CONVERSA: passado o tempo
        de sessao, voltam a valer. Sem isso o cliente era cumprimentado uma
        unica vez na vida. Mesma regra do caminho classico
        (conversation_memory._sessao_expirou).
        """
        try:
            import database
            data = database.get_conversation_state(phone)
            if data:
                estado = cls.from_dict(data)
                try:
                    import conversation_memory
                    if conversation_memory._sessao_expirou(data.get("updated_at", "")):
                        estado.saudacao_feita = False
                        estado.aviso_horario_dado = False
                        print(f"[state] {phone}: conversa nova, saudacao volta a valer")
                except Exception:
                    pass
                return estado
        except Exception as e:
            print(f"[state] Erro ao carregar: {e}")
        return cls(phone=phone)

    def fechar_pedido(self) -> None:
        """Limpa os dados do pedido, mantendo os do cliente.

        Sem isto o pedido seguinte sai com o endereco e o pagamento do anterior
        e com a data antiga, que ja passou.
        """
        self.items = []
        self.modality = ""
        self.data_agendada = ""
        self.data_iso = ""
        self.data_confirmada = False
        self.horario = ""
        self.payment_method = ""
        self.troco_para = 0
        self.subtotal = self.frete = self.taxa_retorno = self.total = 0
        self.modalidade_confirmada = False
        self.upsell_oferecido = False
        self.resumo_apresentado = False
        self.pedido_confirmado = False
        self.pedido_enviado = False
        self.phase = "triagem"
        self.active_agent = "triagem"
        # name, bairro, endereco e referencia ficam: sao dados do cliente.


# ── Mapeamento de produtos para categorias ──────────────────────────────────

_PRODUTOS_FRI_COCOQ = {
    "coxinha": "Coxinha",
    "coxinha de frango": "Coxinha",
    "bolinha de queijo": "Bolinha de Queijo",
    "enroladinho": "Enroladinho de Salsicha",
    "enroladinho de salsicha": "Enroladinho de Salsicha",
    "pastel": "Pastel",
    "pastel carne": "Pastel Carne",
    # Como o cliente escreve: "pastel DE carne". Sem o alias o casamento caia
    # na chave curta "pastel" e o sabor se perdia.
    "pastel de carne": "Pastel Carne",
    "pastel queijo": "Pastel Queijo",
    "pastel de queijo": "Pastel Queijo",
    "quibe": "Quibe",
    # Como o cliente escreve de verdade. Sem estes apelidos, "25 kibes" nao era
    # reconhecido e o item sumia da ficha do atendimento.
    "kibe": "Quibe",
    "quibes": "Quibe",
    "kibes": "Quibe",
    "rissole": "Rissole de Carne",
    "rissoles": "Rissole de Carne",
    "risole": "Rissole de Carne",
    "rissole de carne": "Rissole de Carne",
    "churros": "Churros",
    "croquete": "Croquete de Carne",
    "croquete de carne": "Croquete de Carne",
    "misto": "Misto",
    "esfiha": "Esfirra de Carne",
    "esfilha": "Esfirra de Carne",
}

_PRODUTOS_ASSADOS = {
    "esfirra": "Esfirra de Carne",
    "esfirra de carne": "Esfirra de Carne",
    "empadinha": "Empadinha de Frango",
    "empadinha de frango": "Empadinha de Frango",
    "pao pizza": "Pao Pizza",
    "pão pizza": "Pao Pizza",
    "canudinho": "Canudinho de Frango",
    "canudinho de frango": "Canudinho de Frango",
    "canudinho de camarao": "Canudinho de Camarao",
    "canudinho de camarão": "Canudinho de Camarao",
}

_PRODUTOS_GRANDES = {
    "quiche": "Quiche de Frango",
    "quiche de frango": "Quiche de Frango",
    "quiche de carne de sol": "Quiche de Carne de Sol",
    "saltenha": "Saltenha Assada",
    "saltenha assada": "Saltenha Assada",
    "saltenha frita": "Saltenha Frita",
    "panqueca": "Panqueca de Carne",
    "panqueca de carne": "Panqueca de Carne",
    "panqueca de frango": "Panqueca de Frango",
    "bomba": "Bomba de Carne de Sol",
    "bomba de carne de sol": "Bomba de Carne de Sol",
    "empada": "Empada de Frango",
    "empada de frango": "Empada de Frango",
    "empada de camarao": "Empada de Camarao",
    "empada de camarão": "Empada de Camarao",
    "croquete de macaxeira": "Croquete de Macaxeira",
}

_TODOS_PRODUTOS = {**_PRODUTOS_FRI_COCOQ, **_PRODUTOS_ASSADOS, **_PRODUTOS_GRANDES}

_PRECO_UNITARIO = {
    # Produtos Fritos: R$ 20,00 / 25 un = R$ 0,80/un
    "Coxinha": 0.80,
    "Bolinha de Queijo": 0.80,
    "Enroladinho de Salsicha": 0.80,
    "Pastel": 0.80,
    "Pastel Carne": 0.80,
    "Pastel Queijo": 0.80,
    "Quibe": 0.80,
    "Rissole de Carne": 0.80,
    "Churros": 0.80,
    "Croquete de Carne": 0.80,
    "Misto": 0.80,
    # Produtos Assados: R$ 30,00 / 25 un = R$ 1,20/un
    "Esfirra de Carne": 1.20,
    "Empadinha de Frango": 1.20,
    "Pao Pizza": 1.20,
    "Canudinho de Frango": 1.20,
    "Canudinho de Camarao": 1.20,
    # Produtos Grandes: preços individuais
    "Quiche de Frango": 12.00,
    "Quiche de Carne de Sol": 12.00,
    "Saltenha Assada": 9.00,
    "Saltenha Frita": 10.00,
    "Panqueca de Carne": 15.00,
    "Panqueca de Frango": 15.00,
    "Bomba de Carne de Sol": 10.00,
    "Empada de Frango": 9.00,
    "Empada de Camarao": 9.00,
    "Croquete de Macaxeira": 9.00,
}


# ── Modalidade ──────────────────────────────────────────────────────────────

# Pergunta SOBRE entrega ("quanto fica a taxa de entrega?"), que nao escolhe
# modalidade nenhuma. A palavra de pergunta precisa estar PERTO da palavra
# entrega/retirada, e na mesma frase: antes bastava um "quanto" em qualquer
# lugar da mensagem, e "Entrega hoje as 19h na Rua Guapore (...) me passa o
# total e quanto sobra" perdia a modalidade por causa do "quanto sobra" que
# vinha 130 caracteres depois, sobre outro assunto.
_PALAVRA_DE_PERGUNTA = (r"quanto|qual|quais|tem|voces|vcs|fazem|faz|taxa|valor|"
                        r"preco|custa|cobram|funciona|como")
_MODALIDADE_PERGUNTA = re.compile(
    rf"\b(?:{_PALAVRA_DE_PERGUNTA})\b[^.?!]{{0,30}}?\b(?:entrega\w*|retirad\w*|delivery)\b"
    rf"|\b(?:entrega\w*|retirad\w*|delivery)\b[^.?!]{{0,20}}?\b(?:{_PALAVRA_DE_PERGUNTA})\b"
)

_ENTREGA_EXPLICITA = re.compile(
    r"(?:\b(?:pra|para|pode|podem|quero|queria|gostaria|vai ser|vou querer|e|eh|"
    r"seria|prefiro)\s+(?:de\s+)?entreg\w*)"
    r"|\bme\s+entreg\w*"
    r"|\bentregar\s+(?:em|na|no|hoje|amanha)"
    r"|\bdelivery\b"
    r"|\breceber\s+em\s+casa\b"
    r"|\blevar?\s+(?:na|em)\s+(?:minha\s+)?casa\b"
    r"|\btrazer\s+(?:em|na|no|aqui)\b"
)

# "entrega" como substantivo, seguido de dia, hora ou endereco — nunca de
# preco ("taxa de entrega", "valor da entrega"), que e pergunta, nao escolha.
_ENTREGA_SUBSTANTIVO = re.compile(
    r"(?<!taxa de )(?<!valor da )(?<!preco da )(?<!custo da )"
    r"\bentrega\s+(?:hoje|amanha|depois|pra|para|em|na|no|as|ate|neste|nesse)\b"
)

# Intencao de retirar, dita com verbo: vale mesmo numa frase com pergunta.
_RETIRADA_EXPLICITA = re.compile(
    r"\bretirar\b"
    r"|\b(?:vou|posso|prefiro|quero|queria)\s+(?:retirar|buscar|pegar)\b"
    r"|\beu\s+busco\b"
    r"|\beu\s+pego\b"
    r"|\bna\s+fabrica\b"
    r"|\bno\s+balcao\b"
)

# "retirada" como substantivo. Fica abaixo da guarda de pergunta, igual a
# entrega: "voces tem retirada?" e pergunta, nao escolha — antes ela marcava
# o pedido como retirada e a Sandrinha nunca mais perguntava.
_RETIRADA_SUBSTANTIVO = re.compile(r"\bretirad[ao]\b")


# Palavras que aparecem no lugar de um nome sem ser nome de ninguem. A lista
# existe porque a captura e por palavra-chave, e qualquer palavra-chave um dia
# encosta em texto corrido: no teste do dono de 22/09 17:07, "entregar daqui a
# pouco" gravou o cliente como "pouco" e o resumo saiu com "Cliente: pouco".
_NAO_E_NOME = {
    # tempo — o vizinho mais comum de "daqui a"
    "pouco", "pouca", "poucos", "poucas", "pouquinho", "instante", "instantes",
    "momento", "minuto", "minutos", "hora", "horas", "dia", "dias", "semana",
    "hoje", "amanha", "ontem", "agora", "cedo", "tarde", "noite", "manha",
    "meia", "meio", "cento", "porcao", "unidade", "unidades",
    # quantificadores e ligacoes
    "um", "uma", "uns", "umas", "mais", "menos", "muito", "muita", "tudo",
    "nada", "todo", "toda", "outro", "outra", "mesmo", "mesma",
    "para", "pra", "por", "com", "sem", "que", "qual", "quanto", "quando",
    "onde", "como", "ali", "aqui", "la", "isso", "isto", "esse", "essa",
    # respostas e vocabulario do pedido
    "sim", "nao", "ok", "certo", "claro", "bom", "boa", "obrigado", "obrigada",
    "entrega", "retirada", "pedido", "salgado", "salgados", "coxinha", "quibe",
    "pix", "dinheiro", "cartao", "credito", "debito", "troco", "frete", "taxa",
    "cliente", "gente", "voce", "eu", "ele", "ela", "nos",
}

# Cada frase abaixo e uma apresentacao EXPLICITA. A versao anterior aceitava
# "aqui (?:o|a)" sem limite de palavra, entao "d-aqui a pouco" casava: o \b
# aqui nao e detalhe, e o que separa o cliente se apresentando de uma frase
# qualquer que por acaso contem "aqui".
_RX_NOME = [
    # "meu nome nao e 'pouco', e Balbino" — o cliente corrigindo a Sandrinha.
    # Vem primeiro porque tambem casaria com os padroes de baixo, e neles o
    # nome capturado seria o errado, o que o cliente acabou de negar.
    re.compile(
        r"\bnome\s+(?:nao|não)\s+(?:e|é|eh)\s+['\"]?[A-Za-zÀ-ÿ]+['\"]?\s*[,.!]*\s*"
        r"(?:e|é|eh)\s+(?:o\s+|a\s+)?([A-Za-zÀ-ÿ]{3,})",
        re.IGNORECASE),
    re.compile(r"\bmeu\s+nome\s+(?:e|é|eh)\s+(?:o\s+|a\s+)?([A-Za-zÀ-ÿ]{3,})", re.IGNORECASE),
    re.compile(r"\bme\s+chamo\s+(?:de\s+)?([A-Za-zÀ-ÿ]{3,})", re.IGNORECASE),
    re.compile(r"\b(?:aqui|quem\s+fala)\s+(?:e|é|eh)\s+(?:o|a)\s+([A-Za-zÀ-ÿ]{3,})", re.IGNORECASE),
    re.compile(r"\b(?:sou|aqui|fala)\s+(?:o|a)\s+([A-Za-zÀ-ÿ]{3,})", re.IGNORECASE),
    re.compile(r"\bsou\s+(?:e|é|eh)?\s*([A-ZÀ-Ú][a-zà-ú]{2,})"),   # "sou Balbino": exige maiuscula
    re.compile(r"\b(?:nome|falar)\s+(?:com\s+)?(?:o|a)\s+([A-Za-zÀ-ÿ]{3,})", re.IGNORECASE),
]


def detectar_nome(message: str) -> str:
    """O nome que o cliente deu, ou '' quando ele nao se apresentou.

    So aceita apresentacao explicita ("meu nome e X", "aqui e o X"). Um nome
    errado e pior do que nenhum: ele vai para o resumo, para a comanda e para
    o cadastro do contato, e o cliente precisa corrigir a Sandrinha no meio do
    pedido — foi o que aconteceu no teste do dono com "Cliente: pouco".
    """
    for rx in _RX_NOME:
        achado = rx.search(message or "")
        if not achado:
            continue
        nome = achado.group(1).strip(" .,!?;:")
        if facts.normalize(nome) in _NAO_E_NOME:
            continue
        return nome[:1].upper() + nome[1:] if nome.islower() else nome
    return ""


def detectar_bairro(message: str) -> str:
    """O bairro OFICIAL citado na mensagem, ou '' quando nenhum aparece.

    A tabela de bairros e a mesma que da o frete, entao casar contra ela
    resolve dois defeitos de uma vez:

      - o nome nao trunca. O padrao anterior pegava uma palavra so, e
        "bairro Agenor de Carvalho" virava "Agenor" na ficha e na comanda.
      - a palavra "bairro" deixa de ser obrigatoria. Em "Rua Guapore, 4120 -
        Agenor de Carvalho" e em "Sim, e Agenor de Carvalho mesmo" nao havia
        captura nenhuma: a Sandrinha perguntava o endereco de novo, recebia a
        mesma resposta e perguntava outra vez. Foi esse laco que travou o
        pedido no aceite.

    Prefere o nome mais longo: "Agenor de Carvalho" ganha de "Agenor" quando
    os dois existirem na tabela.
    """
    alvo = facts.normalize(message or "")
    if not alvo:
        return ""
    try:
        oficiais = facts.get()["bairros"]
    except Exception:
        return ""

    achado = ""
    for bairro in oficiais:
        for nome in [bairro["nome"], *(bairro.get("aliases") or [])]:
            chave = facts.normalize(nome)
            if not chave or len(chave) < 4:
                continue
            if re.search(rf"\b{re.escape(chave)}\b", alvo) and len(chave) > len(facts.normalize(achado)):
                achado = bairro["nome"]
    return achado


def detectar_modalidade(message: str) -> str:
    """'entrega', 'retirada' ou '' quando o cliente ainda nao escolheu.

    O teste anterior era um `"entrega" in mensagem` cru: "quanto fica a taxa de
    entrega?" ja marcava o pedido como entrega, e o agente seguia cobrando frete
    sem nunca ter perguntado entrega ou retirada. Aqui so conta intencao
    explicita — pergunta sobre entrega nao escolhe nada.
    """
    import horario as _horario

    t = _horario._norm(message)
    if not t:
        return ""

    if _RETIRADA_EXPLICITA.search(t):
        return "retirada"
    if _ENTREGA_EXPLICITA.search(t):
        return "entrega"

    # Pergunta SOBRE entrega nao e escolha de entrega. Repare que nao basta a
    # mensagem TER uma interrogacao: quem manda o pedido pronto quase sempre
    # termina perguntando o preco. No teste do dono de 23/09 13:31 a frase era
    # "Entrega na Rua Duque de Caxias, 500, bairro Agenor de Carvalho. Vou
    # pagar no cartao. Quanto fica tudo?" — o "?" do fim apagava a modalidade,
    # e a Sandrinha perguntava entrega ou retirada logo depois de ele ter dito
    # o endereco inteiro.
    #
    # Quem decide e _MODALIDADE_PERGUNTA, que exige a palavra de pergunta
    # PERTO de entrega/retirada e na mesma frase.
    if _MODALIDADE_PERGUNTA.search(t):
        return ""

    # Resposta curta e seca a "entrega ou retirada?".
    curto = t.rstrip(" .!,")
    if curto in ("entrega", "e entrega", "eh entrega", "entrega mesmo", "entregar"):
        return "entrega"
    if curto in ("retirada", "e retirada", "eh retirada", "retirar", "vou buscar", "busco"):
        return "retirada"

    # "Entrega hoje as 19h na Rua Guapore" — o substantivo seguido de dia,
    # hora ou endereco. A retirada ja contava assim ("\bretirad\w*\b"); a
    # entrega nao, e a assimetria custou uma modalidade perdida no teste do
    # dono: o pedido tinha endereco, hora e pagamento, e a Sandrinha ainda
    # perguntava se era entrega ou retirada.
    #
    # So chega aqui o que NAO e pergunta: "quanto fica a entrega no Olaria?"
    # ja voltou vazio na guarda acima, que e o que essa funcao existia para
    # proteger.
    if _ENTREGA_SUBSTANTIVO.search(t):
        return "entrega"
    if _RETIRADA_SUBSTANTIVO.search(t):
        return "retirada"

    return ""


# ── Data ────────────────────────────────────────────────────────────────────

# "hoje eu queria saber..." fala de hoje sem agendar nada para hoje.
_HOJE_NAO_AGENDAMENTO = re.compile(
    r"\bhoje\s+(?:eu\s+)?(?:queria|quero|gostaria|to|estou|tava|estava)\s+"
    r"(?:saber|perguntar|ver|conversar)"
)


def detectar_data(message: str, now=None):
    """Data absoluta que o cliente pediu, ou None."""
    import horario as _horario

    if _HOJE_NAO_AGENDAMENTO.search(_horario._norm(message)):
        return None
    return _horario.resolver_data(message, now)


_PGTO_PIX = re.compile(r"\bpix\b")
_PGTO_CREDITO = re.compile(r"\bcredito\b")
_PGTO_DEBITO = re.compile(r"\bdebito\b")
_PGTO_CARTAO = re.compile(r"\b(cartao|cartoes|maquininha|maquina de cartao|maestro|visa|elo|mastercard)\b")
_PGTO_DINHEIRO = re.compile(r"\b(dinheiro|especie)\b")
# "na entrega" sozinho: e o que sobra do menu da Sandrinha ("Dinheiro na
# entrega"), mas so vale quando NENHUMA forma foi citada.
_PGTO_NA_ENTREGA = re.compile(r"\b(na entrega|no ato da entrega)\b")


def detectar_pagamento(message: str) -> str:
    """Forma de pagamento citada na mensagem, ou "" se nenhuma.

    A cadeia if/elif antiga decidia pela PRIMEIRA regra que batia, e o ramo do
    dinheiro testava "na entrega" antes de alguem olhar a palavra "cartao".
    Resultado: "Vou pagar no Cartao na entrega" — que e literalmente a frase
    que a propria Sandrinha oferece — era gravado como dinheiro, e a comanda ia
    para a fabrica mandando o entregador levar troco.

    Aqui a decisao e por ULTIMA MENCAO: quem corrige no meio da frase ("nao e
    dinheiro, e no cartao") tem a correcao respeitada. "na entrega" deixa de
    ser sinal de dinheiro e vira so o ultimo recurso.
    """
    import horario as _horario
    t = _horario._norm(message)        # tira acento: cartão/débito caem aqui

    cred, deb = _PGTO_CREDITO.search(t), _PGTO_DEBITO.search(t)
    cartao = _PGTO_CARTAO.search(t)
    pix, dinheiro = _PGTO_PIX.search(t), _PGTO_DINHEIRO.search(t)

    candidatos = []
    marcas_cartao = [m for m in (cred, deb, cartao) if m]
    if marcas_cartao:
        if cred and not deb:
            valor = "cartao_credito"
        elif deb and not cred:
            valor = "cartao_debito"
        else:
            # "cartao" solto ja basta. Debito e credito cobram a MESMA taxa de
            # retorno de R$ 2,00 da maquininha e chegam iguais na comanda, entao
            # perguntar qual era so uma pergunta a mais no funil - o dono cortou
            # em 23/09. "cartao_a_definir" some daqui, mas segue aceito nas
            # fichas antigas que ainda o tenham gravado.
            valor = "cartao"
        candidatos.append((min(m.start() for m in marcas_cartao), valor))
    if pix:
        candidatos.append((pix.start(), "pix"))
    if dinheiro:
        candidatos.append((dinheiro.start(), "dinheiro"))

    if candidatos:
        return max(candidatos)[1]
    if _PGTO_NA_ENTREGA.search(t):
        return "dinheiro"
    return ""


# Frases de DURACAO, que nao sao horario de agendamento. Sem esta guarda,
# "demora 1 hora?" viraria pedido marcado para 1h da manhã e a Sandrinha
# responderia "A gente so comeca as 07h40!".
_DURACAO = re.compile(
    r"\b(?:em|daqui a|dentro de|demora|demoram|leva|levam|dura|duram|durante|"
    r"por|apos|depois de|antecedencia de|meia)\s+(?:umas?\s+)?\d{0,2}\s*horas?\b"
)


def detectar_horario(message: str) -> str:
    """Horario do pedido no formato da ficha ("18h", "18h30"), ou "".

    Usa horario.parse_horario em vez de uma regex propria: a daqui exigia 'h'
    ou ':' colado no numero, entao "20 horas" nao era entendido — o cliente
    respondia quatro vezes e a Sandrinha reperguntava o horario tres vezes
    seguidas. parse_horario ja entende "20 horas" e continua devolvendo None
    para numero de rua ("Av. Calama, 3234"), que era a razao da regex
    restritiva.
    """
    import horario as _horario
    t = _horario._norm(message)
    if _DURACAO.search(t):
        return ""
    hm = _horario.parse_horario(t)
    return _horario.formatar_horario(*hm) if hm else ""


# Referencia anunciada por ROTULO: o rotulo some, fica so o valor.
_REF_ROTULO = re.compile(
    r"(?:ponto\s+de\s+refer[eê]ncia|refer[eê]ncia|proximidade)\s*(?:[:\-]\s*|\s+)(?P<v>.{2,80})",
    re.IGNORECASE,
)
# Referencia dita por LOCUCAO: a locucao faz parte do texto e FICA.
# "em frente ao mercado" tem de chegar inteiro ao entregador; a versao antiga
# capturava so o que vinha depois do gatilho e entregava "ao mercado".
_REF_LOCAL = re.compile(
    r"(?:(?:em|de)\s+frente|perto|pr[oó]xim[oa]|ao\s+lado|do\s+lado|atr[aá]s|"
    r"em\s+cima|embaixo)\s+(?:d[eoa]s?|ao?s?|à|a)?\s*.{2,80}",
    re.IGNORECASE,
)
# Onde a referencia acaba. Era obrigatorio, entao referencia no FIM da frase
# (sem virgula depois) nao era extraida de jeito nenhum.
_REF_CORTE = re.compile(
    r"\s*(?:[,;.]|\bhoje\b|\bamanh[aã]\b|\b[aà]s?\s+\d|\bpara\s+|\bpra\s+).*",
    re.IGNORECASE | re.DOTALL,
)
_REF_COPULA = re.compile(r"^(?:e|eh|é|que)\s+", re.IGNORECASE)


def detectar_referencia(message: str) -> str:
    """Ponto de referencia do endereco, ou ""."""
    m = _REF_ROTULO.search(message or "")
    bruto = m.group("v") if m else None
    if bruto is None:
        m = _REF_LOCAL.search(message or "")
        bruto = m.group(0) if m else None
    if not bruto:
        return ""
    texto = _REF_CORTE.sub("", bruto).strip()
    texto = _REF_COPULA.sub("", texto).strip()
    return texto if len(texto) >= 2 else ""


# Onde o nome da rua termina: o proximo campo do pedido. Sem isto o endereco
# engolia pagamento, horario e dia, e ia inteiro para a comanda.
_FIM_DA_RUA = re.compile(
    r"\s+(?:bairro|pagamento|pago|vou\s+pagar|pix|cart[aã]o|dinheiro|troco|"
    r"[aà]s\s|\d{1,2}\s*h(?:oras)?\b|hoje|amanh[aã]|depois\s+de\s+amanh[aã]|"
    r"data|hor[aá]rio|ponto\s+de\s+refer|refer[eê]ncia|entrega|retirada|"
    r"para\s+(?:hoje|amanh|as|às)|segunda|ter[cç]a|quarta|quinta|sexta|"
    r"s[aá]bado|domingo)",
    re.IGNORECASE)


# Rua respondida sem rotulo: "SALGADO FILHO,2233", "Duque de Caxias 500".
# Duas palavras ou mais e o numero no fim, e a mensagem inteira, para nao
# confundir com um trecho de pedido.
_RUA_SEM_ROTULO = re.compile(
    r"^[A-Za-zÀ-ÿ]{3,}(?:[\s.]+[A-Za-zÀ-ÿ]{2,})+\s*,?\s*n?[ºo°]?\s*\d{1,6}$",
    re.IGNORECASE)
# O que nunca e endereco, por mais que caiba no molde acima.
_NAO_E_RUA = re.compile(
    r"\b(?:hoje|amanh[aã]|depois|hora|horas|hrs|manh[aã]|tarde|noite|"
    r"segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo|"
    r"pix|cart[aã]o|credito|cr[eé]dito|debito|d[eé]bito|dinheiro|troco|"
    r"reais|total|unidades?|coxinhas?|churros|"
    r"pode|posso|quero|vou|ser|seria|fica|manda|mande|mais|menos|"
    r"sim|n[aã]o|obrigad[oa])\b")


# Rua dita sem "Rua" logo depois do pedido de entrega: "entrega na Calama
# 2025". Exige o numero colado ao nome - "entrega no Olaria" e bairro, e
# "entrega no dia 29" e data.
_RUA_DEPOIS_DE_ENTREGA = re.compile(
    r"\bentreg\w*\s+(?:na|no|em)\s+"
    r"(?P<nome>(?!(?:bairro|dia|data|hora|rua|avenida|av)\b)"
    r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ]+(?:\s+[A-Za-zÀ-ÿ]+){0,3}?)"
    r"\s*,?\s*(?:n[º°o]?\s*)?(?P<numero>\d{1,6})\b",
    re.IGNORECASE)


# Como o cliente pede sem dizer numero: "meio cento de coxinha", "um cento de
# pastel", "uma porcao de quibe". E a forma mais natural de pedir salgado no
# balcao, e a extracao ignorava as tres — a ficha ficava sem item e a Sandrinha
# pedia os itens de novo depois de o cliente ja ter dito (teste do dono, 25/09).
# Os termos de volume do dono (25/09): meio cento = 50, cento = 100,
# cento e meio = 150, dois centos = 200. "cento e meio" vem ANTES de "cento"
# na alternancia, senao casaria so o "cento" e o pedido sairia pela metade.
_PACOTE_POR_EXTENSO = re.compile(
    r"\b(?:(uma?|1)\s+)?("
    r"por[cç][aã]o|meio\s+cento|cento\s+e\s+meio|"
    r"(?:dois|duas|tr[eê]s|quatro|\d{1,2})\s+centos?|cento"
    r")\s+(?:de\s+|d[eo]s\s+)?([a-zà-ÿ][a-zà-ÿ\s]{2,40}?)"
    r"(?=\s*(?:[^a-z\u00e0-\u00ff\s]|\be\b|\bpara\b|\bpra\b|\bcom\b|$))",
    re.IGNORECASE)

_UNIDADES_DO_PACOTE = {
    "porcao": 25, "porção": 25,
    "meio cento": 50,
    "cento": 100,
    "cento e meio": 150,
}

_CENTOS_POR_EXTENSO = {"dois": 2, "duas": 2, "tres": 3, "três": 3, "quatro": 4}


def _unidades_do_formato(formato: str) -> int:
    """Quantas unidades o termo de volume vale. 0 quando nao for um deles."""
    chave = " ".join((formato or "").lower().split())
    if chave in _UNIDADES_DO_PACOTE:
        return _UNIDADES_DO_PACOTE[chave]
    # "dois centos", "3 centos"
    partes = chave.split()
    if len(partes) == 2 and partes[1].startswith("cento"):
        quantos = _CENTOS_POR_EXTENSO.get(partes[0])
        if quantos is None and partes[0].isdigit():
            quantos = int(partes[0])
        if quantos and 0 < quantos <= 20:
            return quantos * 100
    return 0


def _forma_de_busca(nome: str) -> str:
    """minusculo, sem acento e sem plural — 'Canudinhos' casa com 'canudinho'."""
    try:
        import facts
        limpo = facts.normalize(nome)
    except Exception:
        limpo = " ".join((nome or "").lower().split())
    palavras = []
    for p in limpo.split():
        if len(p) > 3 and p.endswith("s"):
            p = p[:-1]
        palavras.append(p)
    return " ".join(palavras)


_INDICE_PRODUTOS: dict[str, str] | None = None
_INDICE_VERSAO = -1


def _extras_do_dono() -> list[dict]:
    """Produtos que o dono ligou pelo painel (config_dono). [] se nenhum."""
    try:
        import config_dono
        return config_dono.catalogo_extra()
    except Exception as e:
        print(f"[produtos] camada do dono ignorada: {e}")
        return []


def _versao_do_dono() -> int:
    try:
        import config_dono
        return config_dono.versao()
    except Exception:
        return 0


def _indice_de_produtos() -> dict:
    """{forma de busca: nome canonico}, montado uma vez (e de novo quando o dono grava)."""
    global _INDICE_PRODUTOS, _INDICE_VERSAO
    if _INDICE_PRODUTOS is not None and _INDICE_VERSAO != _versao_do_dono():
        _INDICE_PRODUTOS = None
    if _INDICE_PRODUTOS is None:
        _INDICE_PRODUTOS = {}
        _INDICE_VERSAO = _versao_do_dono()
        for chave, canonico in _TODOS_PRODUTOS.items():
            forma = _forma_de_busca(chave)
            atual = _INDICE_PRODUTOS.get(forma)
            if atual is None or (atual == _forma_de_busca(atual).title()
                                 and canonico != _forma_de_busca(canonico).title()):
                _INDICE_PRODUTOS[forma] = canonico
        # Nomes do dono so preenchem o que o codigo ainda nao conhece: um
        # apelido "coxinha" apontando para outro produto nao pode tirar a
        # coxinha de quem ja pede coxinha.
        for e in _extras_do_dono():
            for chave in [e["nome_planilha"]] + list(e.get("apelidos") or []):
                forma = _forma_de_busca(chave)
                if forma and forma not in _INDICE_PRODUTOS:
                    _INDICE_PRODUTOS[forma] = e["nome_planilha"]
    return _INDICE_PRODUTOS


def _produto_conhecido(bruto: str) -> str | None:
    """Nome canonico do produto citado, ou None.

    Casa sem acento e sem plural, e prefere sempre a chave MAIS LONGA. Era
    aqui que "25 canudinhos de camarao" virava Canudinho de FRANGO: o plural
    quebrava o casamento exato e sobrava um atalho de 4 letras, que pegava a
    primeira chave comecando em "canu" — o frango. Sabor errado na comanda,
    com camarao no meio (teste do Eduardo, 25/09).
    """
    alvo = _forma_de_busca(bruto)
    if len(alvo) < 3:
        return None

    indice = _indice_de_produtos()
    if alvo in indice:
        return indice[alvo]

    # Duas direcoes, e elas nao valem o mesmo.
    #
    # O cliente disse MAIS do que a chave ("canudinho de camarao" contem
    # "canudinho"): a chave mais longa e a mais especifica, e ganha.
    #
    # O cliente disse MENOS do que a chave ("carne" cabe dentro de "quiche de
    # carne de sol", "pastel carne", "rissole de carne", "croquete de carne"...):
    # a chave mais longa nao e a mais certa, e so a mais comprida. Era assim
    # que "30 de carne", num pedido de cento de FRITOS, virava 30 Quiches de
    # Carne de Sol a R$ 12,00 - e como quiche e item de balcao, a trava de lote
    # nem via o item: o ajuste de 25 em 25 saia sem ele e o sabor desaparecia
    # do pedido (teste do dono, 26/09 18h18). Palavra que serve para varios
    # produtos nao identifica nenhum: devolve None e o sabor vira pergunta.
    melhor, tamanho = None, 0
    candidatos = set()
    for chave, canonico in indice.items():
        if len(chave) < 4:
            continue
        if chave in alvo:
            if len(chave) > tamanho:
                melhor, tamanho = canonico, len(chave)
        elif alvo in chave:
            candidatos.add(canonico)
    if melhor:
        return melhor
    if len(candidatos) == 1:
        return candidatos.pop()
    if candidatos:
        return None

    # Prefixo so quando o cliente NAO foi mais especifico do que a chave.
    # Sem esta condicao, "canudinho de camarao" caia de novo no frango.
    if len(alvo.split()) <= 1:
        for chave, canonico in indice.items():
            if len(chave) >= 4 and (chave.startswith(alvo[:4])
                                    or alvo.startswith(chave[:4])):
                return canonico
    return None


# Sabores que existem como SABOR DE LOTE (pacote de 25). Servem para filtrar a
# lista de opcoes quando o cliente pede um cento e diz so "carne": oferecer
# Quiche de Carne de Sol, que e item de balcao vendido por unidade, seria
# responder outra coisa.
_SABORES_DE_LOTE = frozenset(_PRODUTOS_FRI_COCOQ.values()) | frozenset(_PRODUTOS_ASSADOS.values())


def sabores_possiveis(bruto: str, so_lote: bool = True) -> list[str]:
    """Produtos que a palavra do cliente pode significar, quando serve a varios.

    "30 de carne" nao e pedido de nada em especial: cabe em pastel de carne,
    rissole de carne, esfirra de carne, croquete de carne. Antes o casamento
    por substring escolhia a chave mais LONGA e o sabor virava Quiche de Carne
    de Sol a R$ 12,00 (teste do dono, 26/09 18h18). Devolver a lista e o que
    permite PERGUNTAR em vez de chutar.

    [] quando a palavra identifica um produto so - ai nao ha o que perguntar.
    """
    alvo = _forma_de_busca(bruto)
    if len(alvo) < 3:
        return []
    indice = _indice_de_produtos()
    if alvo in indice:
        return []
    achados = []
    for chave, canonico in indice.items():
        if len(chave) < 4:
            continue
        if chave in alvo:
            return []          # o cliente foi MAIS especifico: nao ha duvida
        if alvo in chave and canonico not in achados:
            achados.append(canonico)
    if len(achados) < 2:
        return []
    if so_lote:
        de_lote_do_dono = {e["nome_planilha"] for e in _extras_do_dono() if e["tipo"] == "sabor"}
        de_lote = [a for a in achados if a in _SABORES_DE_LOTE or a in de_lote_do_dono]
        if de_lote:
            achados = de_lote
    # Esgotado nao entra na lista de opcoes: oferecer o que a fabrica nao tem e
    # so adiar a pergunta.
    disponiveis = []
    for nome in achados:
        try:
            import facts
            if facts.resolve_unit_price(nome, 25):
                disponiveis.append(nome)
        except Exception:
            disponiveis.append(nome)
    return disponiveis or achados


def volume_sem_sabor(msg_lower: str) -> int:
    """Unidades pedidas por volume SEM dizer o sabor. 0 quando nao ha.

    "Quero um cento de salgados fritos" nao tem sabor nenhum para virar item, e
    a ficha ficava vazia. A regra do dono (25/09) pede resposta especifica:
    oferecer ate Total/25 sabores e perguntar o preparo.
    """
    for _um, formato, produto in _PACOTE_POR_EXTENSO.findall(msg_lower):
        unidades = _unidades_do_formato(formato)
        if unidades and not _produto_conhecido(produto):
            return unidades
    return 0


_RX_FRITO = re.compile(r"\bfrit[oa]s?\b")
_RX_ASSADO = re.compile(r"\bassad[oa]s?\b")


def preparo_dito(msg_lower: str) -> str:
    """'frito' ou 'assado' quando o cliente disse um so. '' se nenhum ou os dois.

    "Faz 1 cento frito" e a ficha nao guardava o preparo: a instrucao de volume
    sem sabor mandava perguntar "frito ou assado" do mesmo jeito, e o cliente
    ouvia de novo o que tinha acabado de dizer (teste do dono, 27/09 20h40).
    """
    frito = bool(_RX_FRITO.search(msg_lower))
    assado = bool(_RX_ASSADO.search(msg_lower))
    if frito == assado:
        return ""
    return "frito" if frito else "assado"


# Volume que sera DIVIDIDO entre os sabores ditos logo depois: "1 cento pastel
# e bolinha", "meio cento de coxinha e quibe", "100 salgados metade X metade Y".
_RX_VOLUME_DIVIDIDO = re.compile(
    r"\b(?:(?:uma?|1)\s+)?(?P<formato>por[cç][aã]o|meio\s+cento|cento\s+e\s+meio|"
    r"(?:dois|duas|tr[eê]s|quatro|\d{1,2})\s+centos?|cento)\b"
    r"|\b(?P<numero>\d{2,3})\s+(?:salgad\w*|unidades?)\b",
    re.IGNORECASE)

# Onde a lista de sabores acaba: logistica, outro numero ou fim de frase.
_RX_FIM_DOS_SABORES = re.compile(
    r"\bpra\b|\bpara\b|\bhoje\b|\bamanh[aã]\b|\bretir\w*|\bentreg\w*|\bbusc\w*"
    r"|\b[aà]s\b|\d|[.;!?:\n]")

# O que vem antes do sabor e nao e sabor: "sendo metade", "a outra metade de".
_RX_ANTES_DO_SABOR = re.compile(
    r"^(?:(?:sendo|de|dos|das|com|a|o|outra|outro|metade|meio)(?:\s+|$))+",
    re.IGNORECASE)

# Palavra de volume, nao de sabor: "cento de salgados fritos" nao diz o sabor.
_RX_SO_GENERICO = re.compile(
    r"^(?:salgad\w*|salgadinh\w*|frit\w*|assad\w*|sortid\w*|variad\w*|misturad\w*|\s)+$")


def itens_divididos(msg_lower: str) -> list[dict]:
    """Volume dividido por igual entre os sabores ditos. [] quando nao ha.

    O balcao pede assim: "1 cento pastel e bolinha" e 50 de cada, "metade
    croquete e metade pastel de carne" e um cento meio a meio. A extracao so
    conhecia "cento de X": a ficha ficava com 100 pasteis e a bolinha sumia
    (amostra do balcao do dono, 28/09). Dois centos iguais na mesma frase somam.

    So divide quando TODOS os sabores do trecho sao conhecidos e a divisao
    fecha em lotes de 25 - senao quem decide e o fluxo normal, que pergunta.
    """
    alvo = " ".join((msg_lower or "").lower().split())
    trechos = []
    marcas = list(_RX_VOLUME_DIVIDIDO.finditer(alvo))
    for i, m in enumerate(marcas):
        if m.group("formato"):
            unidades = _unidades_do_formato(m.group("formato"))
        else:
            unidades = int(m.group("numero"))
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(alvo)
        trechos.append((unidades, alvo[m.end():fim]))
    # "Metade croquete e metade pastel de carne", sem volume nenhum: o balcao
    # entende um cento.
    if not marcas:
        m = re.search(r"\bmetade\b.*\bmetade\b", alvo)
        if m:
            trechos.append((100, alvo[m.start():]))

    somados: dict[str, int] = {}
    for unidades, trecho in trechos:
        corte = _RX_FIM_DOS_SABORES.search(trecho)
        if corte:
            trecho = trecho[:corte.start()]
        partes = []
        for parte in re.split(r"\s*(?:,|\be\b|\+|/|\bmetade\b)\s*", trecho):
            parte = _RX_ANTES_DO_SABOR.sub("", parte.strip()).strip()
            if parte and not _RX_SO_GENERICO.match(parte):
                partes.append(parte)
        if len(partes) < 2 or not unidades:
            continue
        sabores = [_produto_conhecido(p) for p in partes]
        if not all(sabores) or len(set(sabores)) != len(sabores):
            continue
        cada = unidades // len(sabores)
        if cada * len(sabores) != unidades or cada % 25:
            continue
        for nome in sabores:
            somados[nome] = somados.get(nome, 0) + cada

    achados = []
    for nome, qtd in somados.items():
        preco = 0.0
        try:
            import facts
            oficial = facts.resolve_unit_price(nome, qtd)
            if oficial:
                preco = oficial["preco_unitario"]
        except Exception:
            pass
        achados.append({"name": nome, "quantity": qtd, "price": preco})
    return achados


# Bebida pedida pelo nome de balcao. O indice de produtos nao tem bebida
# nenhuma, entao "pode mandar uma coca lata" e "1 Coca-Cola de 2 litros"
# sumiam do pedido e o espelho saia sem elas (teste do dono, 28/09 20h08).
_RX_BEBIDA = re.compile(
    r"(?:\b(?P<qtd>\d{1,2}|uma?|duas|dois|tr[eê]s)\s+)?"
    r"\b(?P<marca>coca(?:[\s-]*cola)?|refri(?:gerante)?s?|guaran[aá]s?|tuchaua"
    r"|fanta|sprite|pepsi|kuat|soda)\b"
    r"(?P<resto>[^,.;!?\n]{0,30})",
    re.IGNORECASE)

_QTD_POR_EXTENSO = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3}

# Nome EXATO da planilha para cada tamanho.
BEBIDA_LATA = "Refrigerante Lata"
BEBIDA_600 = "Refrigerante 600ml"
BEBIDA_2L = "Refrigerante 2 Litros"
BEBIDA_TUCHAUA = "Guaraná Tuchaua 2 Litros"

BOLO = "Bolo de Milho"
_RX_BOLO = re.compile(
    r"(?:\b(?P<qtd>\d{1,2}|uma?|dois|duas|tr[eê]s)\s+)?\bbolos?\b(?:\s+de\s+milho)?",
    re.IGNORECASE)

# Palavra que basta para escolher o tamanho quando ela pergunta ("lata").
PALAVRAS_DA_BEBIDA = {
    BEBIDA_LATA: ("lata", "latinha"),
    BEBIDA_600: ("600",),
    BEBIDA_TUCHAUA: ("tuchaua", "guarana"),
}


def bebidas_citadas(msg_lower: str) -> tuple[list[dict], list[dict]]:
    """(itens, pendentes) de bebida na mensagem.

    O tamanho decide o produto: lata, 600ml, 2 litros. Guarana e sempre o
    Tuchaua 2L, o unico que a fabrica vende. Refrigerante sem tamanho nao e
    chutado: volta como pendencia, para ela perguntar qual.
    """
    itens, pendentes = [], []
    for m in _RX_BEBIDA.finditer(msg_lower or ""):
        # "sem refri", "nao quero refrigerante": e assim que se RECUSA a oferta.
        if re.search(r"\b(?:sem|n[aã]o|nada\s+de|nem)\b[^,.;!?\n]{0,18}$",
                     (msg_lower or "")[:m.start()]):
            continue
        bruto_qtd = (m.group("qtd") or "").lower()
        qtd = int(bruto_qtd) if bruto_qtd.isdigit() else _QTD_POR_EXTENSO.get(bruto_qtd, 1)
        if not 0 < qtd <= 20:
            continue
        marca = m.group("marca").lower()
        resto = (m.group("resto") or "").lower()
        if re.search(r"\blat(?:a|inha)s?\b", resto):
            nome = BEBIDA_LATA
        elif re.search(r"\b600", resto):
            nome = BEBIDA_600
        elif marca.startswith(("guaran", "tuchaua")):
            nome = BEBIDA_TUCHAUA
        elif re.search(r"\b2\s*l\b|\b2\s*lts?\b|\b(?:2|dois)\s*litros?\b", resto):
            nome = BEBIDA_2L
        else:
            pendentes.append({"qtd": qtd, "termo": "refrigerante",
                              "opcoes": _opcoes_de_refrigerante()})
            continue
        if any(i["name"] == nome for i in itens):
            continue
        try:
            import facts
            oficial = facts.resolve_unit_price(nome, qtd)
        except Exception:
            oficial = {"preco_unitario": 0.0}
        if not oficial:
            # Esgotado (o 2L comum): vira a pergunta do tamanho, com o aviso na
            # frente. Como item, a troca "lata" nao teria por onde entrar.
            pendentes.append({"qtd": qtd, "termo": "refrigerante",
                              "opcoes": _opcoes_de_refrigerante(), "em_falta": nome})
            continue
        itens.append({"name": nome, "quantity": qtd, "price": oficial["preco_unitario"]})

    # O bolo da oferta ("vai um Bolo de Milho?" -> "Um bolo") tambem sumia do
    # espelho: nao estava no indice de produtos (teste do dono, 29/09 15h24).
    # Entra pelo mesmo caminho da bebida, que SOMA ao pedido.
    for m in _RX_BOLO.finditer(msg_lower or ""):
        if re.search(r"\b(?:sem|n[aã]o|nada\s+de|nem)\b[^,.;!?\n]{0,18}$",
                     (msg_lower or "")[:m.start()]):
            continue
        bruto_qtd = (m.group("qtd") or "").lower()
        qtd = int(bruto_qtd) if bruto_qtd.isdigit() else _QTD_POR_EXTENSO.get(bruto_qtd, 1)
        if not 0 < qtd <= 10 or any(i["name"] == BOLO for i in itens):
            continue
        try:
            import facts
            oficial = facts.resolve_unit_price(BOLO, qtd)
        except Exception:
            oficial = None
        if oficial:
            itens.append({"name": BOLO, "quantity": qtd, "price": oficial["preco_unitario"]})
    return itens, pendentes[:1]


def _opcoes_de_refrigerante() -> list[str]:
    """Tamanhos que a fabrica tem HOJE (esgotado nao entra na pergunta)."""
    opcoes = []
    for nome in (BEBIDA_LATA, BEBIDA_600, BEBIDA_TUCHAUA):
        try:
            import facts
            if not facts.resolve_unit_price(nome, 1):
                continue
        except Exception:
            pass
        opcoes.append(nome)
    return opcoes or [BEBIDA_LATA]


def _itens_por_extenso(msg_lower: str) -> list[dict]:
    """Itens pedidos por formato em vez de numero. [] quando nao ha nenhum."""
    achados = []
    for _um, formato, produto in _PACOTE_POR_EXTENSO.findall(msg_lower):
        unidades = _unidades_do_formato(formato)
        if not unidades:
            continue
        canonico = _produto_conhecido(produto)
        if not canonico:
            continue          # "meio cento de salgado assado": falta o sabor
        preco = 0.0
        try:
            import facts
            oficial = facts.resolve_unit_price(canonico, unidades)
            if oficial:
                preco = oficial["preco_unitario"]
        except Exception:
            pass
        achados.append({"name": canonico, "quantity": unidades, "price": preco})
    return achados


# Numero dito por extenso. Chega assim pelo audio - o Whisper escreveu
# "Quero cinquenta coxinha, cinquenta bolinha, cinquenta churros" e a extracao,
# que so le digitos, nao achou item nenhum (teste do dono por voz, 29/09 20h04).
_UNIDADES_EXTENSO = {
    "dois": 2, "duas": 2, "tres": 3, "três": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11,
    "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14, "quinze": 15,
    "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19,
}
_DEZENAS_EXTENSO = {
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "cinqüenta": 50,
    "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90,
    "cem": 100, "duzentos": 200, "duzentas": 200, "trezentos": 300,
    "trezentas": 300, "quatrocentos": 400, "quatrocentas": 400,
    "quinhentos": 500, "quinhentas": 500,
}
_RX_NUMERO_EXTENSO = re.compile(
    r"\b(?P<dezena>" + "|".join(sorted(_DEZENAS_EXTENSO, key=len, reverse=True)) + r")"
    r"(?:\s+e\s+(?P<unidade>" + "|".join(sorted(_UNIDADES_EXTENSO, key=len, reverse=True)) + r"))?\b"
    r"|\b(?P<sozinha>" + "|".join(sorted(_UNIDADES_EXTENSO, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)

# O numero so vira digito antes de produto, volume ou hora: "Rua Sete de
# Setembro" e "Avenida Sete" continuam como estao.
_RX_DEPOIS_DO_NUMERO = re.compile(
    r"^\s+(?:(?:de|da|do)\s+)?(?:horas?|h\b|da\s+manh|da\s+tarde|da\s+noite"
    r"|salgad|unidades?|un\b|porc|centos?|[a-zà-ÿ]+)", re.IGNORECASE)


def numeros_por_extenso(texto: str) -> str:
    """'cinquenta coxinhas as oito horas' -> '50 coxinhas as 8 horas'."""
    def _troca(m):
        resto = texto[m.end():]
        seguinte = _RX_DEPOIS_DO_NUMERO.match(resto)
        if not seguinte:
            return m.group(0)
        palavra = seguinte.group(0).strip().lower()
        if not re.match(r"(?:(?:de|da|do)\s+)?(?:horas?|h\b|da\s+|salgad|unidade|un\b|porc|cento)", palavra):
            # Palavra comum: so troca se for um produto conhecido.
            candidato = re.sub(r"^(?:de|da|do)\s+", "", palavra)
            if not _produto_conhecido(candidato):
                return m.group(0)
        if m.group("sozinha"):
            return str(_UNIDADES_EXTENSO[m.group("sozinha").lower()])
        valor = _DEZENAS_EXTENSO[m.group("dezena").lower()]
        if m.group("unidade"):
            valor += _UNIDADES_EXTENSO[m.group("unidade").lower()]
        return str(valor)
    return _RX_NUMERO_EXTENSO.sub(_troca, texto or "")


def _extract_data_from_message(message: str, state: "ConversationState") -> dict:
    """Extrai dados estruturados da mensagem do cliente (nome, itens, modalidade, endereco, horario, pagamento).

    Retorna dict com os dados extraidos que devem ser aplicados ao state.
    """
    message = numeros_por_extenso(message or "")
    msg_lower = message.lower().strip()
    extracted = {}

    # ── Nome ────────────────────────────────────────────────────────────────
    # "sou a Ana" nao era reconhecido — so "sou o". O resultado era perguntar o
    # nome de novo para metade dos clientes, logo depois de eles o dizerem.
    if not state.name:
        nome = detectar_nome(message)
        if nome:
            extracted["name"] = nome

    # ── Modalidade ──────────────────────────────────────────────────────────
    if not state.modality:
        modalidade = detectar_modalidade(message)
        if modalidade:
            extracted["modality"] = modalidade

    # ── Horario ─────────────────────────────────────────────────────────────
    if not state.horario:
        hora = detectar_horario(message)
        if hora:
            extracted["horario"] = hora

    # ── Data ────────────────────────────────────────────────────────────────
    # A data so entra no pedido se o cliente disser o dia. Nunca ha default
    # para "hoje": um pedido cair no dia errado e pior do que uma pergunta a
    # mais.
    if not state.data_iso:
        import horario as _horario
        _now = None
        if state.current_time:
            try:
                _now = _horario.datetime.strptime(state.current_time, "%d/%m/%Y %H:%M")
            except Exception:
                _now = None
        data = detectar_data(message, _now)
        if data:
            extracted["data_iso"] = data.isoformat()
            extracted["data_agendada"] = _horario.formatar_data(data, _now)

    # ── Endereco ────────────────────────────────────────────────────────────
    if not state.endereco:
        # Padrão: "Av. Calama, 3234" ou "Av. Calama 3234" ou "Rua X, 123"
        # Para antes de "bairro" ou no final da string
        addr_match = re.search(
            # O ponto da abreviacao era obrigatorio, entao "Av Calama 100" —
            # como o cliente escreve de verdade — nao era reconhecido e o
            # agente ficava pedindo o endereco que ja tinha recebido.
            r'(\b(?:Av\.?|Avenida|R\.|Rua|Al\.?|Alameda|Travessa|Tv\.?|'
            r'Estrada|Rod\.?|Rodovia|Pra[cç]a)\s+[^\.,]+(?:[,\s]+\d+)?)',
            message, re.IGNORECASE
        )
        if addr_match:
            addr = addr_match.group(1).strip()
            # O nome da rua so parava em virgula ou ponto, e o cliente escreve
            # tudo emendado e sem pontuacao. "OLARIA RUA GUANABARA PAGAMENTO NO
            # PIX" virava endereco "RUA GUANABARA PAGAMENTO NO PIX", e
            # "RUA FLORES AS 18 HORAS" levou o horario junto — os dois foram
            # impressos na comanda assim (teste do dono, 25/09). O nome da rua
            # termina onde comeca outro campo do pedido.
            addr = _FIM_DA_RUA.split(addr, maxsplit=1)[0].strip(" ,-")
            extracted["endereco"] = addr
        elif _RUA_DEPOIS_DE_ENTREGA.search(message):
            # "PARA ENTREGA NA CALAMA 2025 APONIA": sem "Rua" nada era
            # guardado e ela pedia "endereco (rua e numero)" que o cliente ja
            # tinha mandado na primeira mensagem (teste do dono, 28/09 20h03).
            m = _RUA_DEPOIS_DE_ENTREGA.search(message)
            nome = " ".join(m.group("nome").split())
            if not _NAO_E_RUA.search(nome.lower()):
                extracted["endereco"] = f"{nome} {m.group('numero')}"
        elif state.bairro and "entrega" in (state.modality,
                                            extracted.get("modality")):
            # Quem responde "qual e a rua e o numero?" responde so a rua:
            # "SALGADO FILHO,2233". Sem "Rua" na frente nada era guardado, e a
            # Sandrinha pedia o endereco de novo — o dono teve de repetir o
            # endereco inteiro em caixa alta para seguir. So vale quando a
            # entrega e o bairro ja estao na ficha, isto e, quando o endereco
            # e de fato a proxima pergunta: duas palavras ou mais e um numero
            # no fim, sem nenhuma palavra de dia, hora ou pagamento no meio.
            solto = _RUA_SEM_ROTULO.match(message.strip())
            if solto and not _NAO_E_RUA.search(message.lower()):
                extracted["endereco"] = " ".join(message.strip().split())

    # ── Bairro ──────────────────────────────────────────────────────────────
    if not state.bairro:
        bairro = detectar_bairro(message)
        if bairro:
            extracted["bairro"] = bairro

    # ── Pagamento ───────────────────────────────────────────────────────────
    if not state.payment_method:
        forma = detectar_pagamento(message)
        if forma:
            extracted["payment_method"] = forma

    # ── Itens ───────────────────────────────────────────────────────────────
    if not state.items:
        # Padrão: "50 coxinhas", "25 kibes", "100 esfirras"
        # O fim do nome do produto so era reconhecido em virgula, " e " ou fim
        # da mensagem. Com isso "quero 25 coxinhas pra retirar as 16h" nao
        # extraia item nenhum — o pedido sumia e a Sandrinha perguntava de novo
        # o que o cliente ja tinha dito. Aqui o nome termina tambem em ponto e
        # nas preposicoes que costumam vir depois do produto.
        # O preco dito pelo cliente tambem encerra o nome do produto. Sem
        # "r$" na lista, "2 coxinhas grandes de balcao de R$ 9,00" nao casava
        # com nada — o nome seguia engolindo texto ate esbarrar no cifrao, que
        # a classe de caracteres nao aceita, e o item inteiro sumia da ficha.
        # No aceite isso deixou a ficha com 2 dos 3 itens do pedido.
        # Qualquer outro sinal tambem encerra o nome. Valiam so os
        # terminadores listados acima, entao '50 coxinha e 50 churros"'
        # guardava a coxinha e perdia o churros: a aspa nao terminava o
        # nome nem cabia na classe de caracteres. O dono fechou assim um
        # pedido de R$ 94,00 cobrando R$ 54,00. E lookahead, para nao
        # comer o '50' do item seguinte quando o sinal e um digito.
        item_pattern = re.findall(
            r'(\d+)\s*(?:unidades?|un\.?|pcs\.?)?\s*(de\s+)?([a-záàãâéêíóôõúç\s]+?)'
            r'(?:\s*(?:[,;.!?]|\be\b|\bpra\b|\bpara\b|\bcom\b|\bno\b|\bna\b|\bem\b'
            r'|\bhoje\b|\bamanh[aã]\b|\bàs\b|\bas\b|\bde\s+r\$|r\$'
            r"|(?![a-z\u00e0-\u00ff\s])|$))",
            msg_lower
        )
        items_found = []
        sabores_pendentes = []
        for qty_str, _, product_name in item_pattern:
            qty = int(qty_str)
            product_name = product_name.strip()

            # Nome curto demais nao identifica produto. Sem esta guarda, "hoje as
            # 18h" virava "18 x Coxinha": o resto "h" casava por substring com
            # "coxinha" e o pedido inteiro era trocado por um horario.
            if len(product_name) < 3:
                continue

            # "2 centos de coxinha" e VOLUME, nao quantidade: o numero conta
            # centos, nao unidades. Quem trata e _itens_por_extenso, logo
            # abaixo — aqui sairia um pedido de 2 coxinhas.
            if product_name.startswith("cento"):
                continue

            # Remove plural
            if product_name.endswith('s') and not product_name.endswith('es'):
                product_name_singular = product_name[:-1]
            elif product_name.endswith('es'):
                product_name_singular = product_name[:-2] if len(product_name) > 3 else product_name
            else:
                product_name_singular = product_name

            # Busca produto conhecido
            matched_name = _produto_conhecido(product_name)

            if not matched_name and qty > 0:
                # Palavra que serve a varios sabores. Nao vira item (chutar foi
                # o defeito), mas tambem nao pode sumir: o cliente pediu "30 de
                # carne" e o ajuste de lote saiu sem esse sabor, como se ele
                # nunca tivesse falado (teste do dono, 26/09 18h20).
                opcoes = sabores_possiveis(product_name)
                if len(opcoes) == 1:
                    # Depois de descartar os itens de balcao e os esgotados
                    # sobrou um so: "30 de queijo" num pedido de fritos e
                    # bolinha de queijo, e nao ha o que perguntar.
                    matched_name = opcoes[0]
                elif opcoes:
                    sabores_pendentes.append({
                        "qtd": qty,
                        "termo": product_name.strip(),
                        "opcoes": opcoes,
                    })
                    continue
                else:
                    continue

            if matched_name and qty > 0:
                # O preco oficial vem da planilha; o mapa local so entra se a
                # tabela estiver indisponivel (evita tabela de preco duplicada
                # no codigo, que envelhece sem ninguem perceber).
                unit_price = 0.0
                try:
                    import facts
                    oficial = facts.resolve_unit_price(matched_name, qty)
                    if oficial:
                        unit_price = oficial["preco_unitario"]
                except Exception:
                    pass
                if not unit_price:
                    unit_price = _PRECO_UNITARIO.get(matched_name, 0.0)
                items_found.append({
                    "name": matched_name,
                    "quantity": qty,
                    "price": unit_price,
                })

        # "1 cento pastel e bolinha", "metade X e metade Y": o volume dividido
        # vale mais do que qualquer leitura parcial do mesmo sabor acima.
        divididos = itens_divididos(msg_lower)
        if divididos:
            nomes = {i["name"] for i in divididos}
            items_found = [i for i in items_found if i["name"] not in nomes] + divididos
            extracted["pedido_por_volume"] = True

        # "meio cento de coxinha" nao tem numero e escapava do padrao acima.
        # So entra o produto que o padrao numerado nao pegou.
        ja_vistos = {i["name"] for i in items_found}
        for extra in _itens_por_extenso(msg_lower):
            if extra["name"] not in ja_vistos:
                items_found.append(extra)
                ja_vistos.add(extra["name"])

        if sabores_pendentes:
            extracted["sabores_pendentes"] = sabores_pendentes

        if items_found:
            extracted["items"] = items_found
        else:
            # Volume dito sem sabor: guarda para a ficha pedir os sabores.
            unidades = volume_sem_sabor(msg_lower)
            if unidades:
                extracted["volume_pendente"] = unidades

    # Bebida em qualquer mensagem, inclusive com o pedido ja na ficha: ela
    # costuma vir como resposta a oferta ("pode mandar uma coca lata"). Vai
    # em campo proprio porque SOMA aos itens - como "items" ela trocaria o
    # pedido inteiro pela bebida.
    bebidas, bebida_sem_tamanho = bebidas_citadas(msg_lower)
    if bebidas:
        extracted["bebidas"] = bebidas
    if bebida_sem_tamanho:
        extracted["bebidas_pendentes"] = bebida_sem_tamanho

    # Preparo dito em qualquer mensagem: o "frito" pode vir junto com o cento
    # ou na resposta a pergunta dos sabores.
    preparo = preparo_dito(msg_lower)
    if preparo:
        extracted["preparo_pendente"] = preparo

    # ── Referencia ──────────────────────────────────────────────────────────
    if not state.referencia:
        ref = detectar_referencia(message)
        if ref:
            extracted["referencia"] = ref

    return extracted


class ConversationManager:
    """Gerencia conversa completa via sub-agentes."""

    def __init__(self):
        self.agents = {}
        self._load_agents()

    def _load_agents(self):
        """Carrega todos os agentes."""
        from agents.triagem import TriagemAgent
        from agents.cardapio import CardapioAgent
        from agents.pedido import PedidoAgent
        from agents.logistica import LogisticaAgent
        from agents.pagamento import PagamentoAgent
        from agents.crise import CriseAgent

        self.agents = {
            "triagem": TriagemAgent(),
            "cardapio": CardapioAgent(),
            "pedido": PedidoAgent(),
            "logistica": LogisticaAgent(),
            "pagamento": PagamentoAgent(),
            "crise": CriseAgent(),
        }

    def handle_message(self, phone: str, message: str, media=None) -> str:
        """Processa mensagem do WhatsApp."""

        # 1. Carrega ou cria estado
        state = ConversationState.load_from_db(phone)
        state.phone = phone

        # 1.1 Injeta calendario e expediente ja resolvidos (fuso de Porto Velho)
        import horario
        now = horario.agora()
        state.current_time = now.strftime("%d/%m/%Y %H:%M")
        state.calendario = horario.contexto_para_prompt(now)
        expediente = horario.status(now)

        # Conhecimento obrigatorio no contexto: os agentes nao dependem de uma
        # decisao do LLM de chamar a tool antes de responder.
        import knowledge_rag
        state.knowledge_context = knowledge_rag.get_full_knowledge_content()

        # 2. Extrai dados estruturados da mensagem ANTES de rotear
        extracted = _extract_data_from_message(message, state)
        if extracted:
            if "name" in extracted and not state.name:
                state.name = extracted["name"]
                print(f"[extract] nome: {state.name}")
            if "items" in extracted and not state.items:
                state.items = extracted["items"]
                print(f"[extract] itens: {state.items}")
            if "modality" in extracted and not state.modality:
                state.modality = extracted["modality"]
                state.modalidade_confirmada = True
                print(f"[extract] modalidade: {state.modality}")
            if "endereco" in extracted and not state.endereco:
                state.endereco = extracted["endereco"]
                print(f"[extract] endereco: {state.endereco}")
            if "bairro" in extracted and not state.bairro:
                state.bairro = extracted["bairro"]
                print(f"[extract] bairro: {state.bairro}")
            if "horario" in extracted and not state.horario:
                state.horario = extracted["horario"]
                print(f"[extract] horario: {state.horario}")
            if "data_iso" in extracted and not state.data_iso:
                state.data_iso = extracted["data_iso"]
                state.data_agendada = extracted.get("data_agendada", state.data_agendada)
                state.data_confirmada = True
                print(f"[extract] data: {state.data_agendada} ({state.data_iso})")
            if "payment_method" in extracted and not state.payment_method:
                state.payment_method = extracted["payment_method"]
                print(f"[extract] pagamento: {state.payment_method}")
            if "referencia" in extracted and not state.referencia:
                state.referencia = extracted["referencia"]
                print(f"[extract] referencia: {state.referencia}")

            # Se tem itens + modalidade, ja pula direto para pedido/logistica
            if state.items and state.modality:
                if state.phase == "triagem":
                    state.phase = "pedido"
                    state.active_agent = "pedido"
                    print(f"[extract] DADOS COMPLETOS - pulando para pedido")

        # 3. Adiciona mensagem ao historico
        state.history.append({
            "role": "user",
            "text": message,
            "timestamp": time.time(),
        })

        # 3.1 Fora do expediente: avisa ANTES de qualquer coisa.
        # Isso sai em codigo, nao em instrucao de prompt, porque era exatamente
        # a regra que o modelo pulava quando o cliente mandava o pedido pronto
        # na primeira mensagem.
        if not expediente["aberto"] and not state.aviso_horario_dado:
            state.aviso_horario_dado = True
            saudacao = horario.saudacao(now)
            nome = f", {state.name}" if state.name else ""
            resposta = f"{saudacao}{nome}! Aqui é a Sandrinha da BALBS.\n\n{expediente['mensagem']}"
            if not expediente["aceita_hoje"] and state.data_iso == now.date().isoformat():
                # O cliente pediu para hoje, mas hoje ja nao cabe: a data volta
                # a ser uma pergunta em aberto em vez de seguir errada.
                state.data_iso = ""
                state.data_agendada = ""
                state.data_confirmada = False
            return self._finalizar(state, resposta)

        # 3.2 Data + horario incompativeis com o expediente: corrige na hora.
        if state.data_iso and state.horario:
            from datetime import date as _date
            ok, motivo, _sugestao, campo = horario.validar_agendamento(
                _date.fromisoformat(state.data_iso), state.horario, now
            )
            if not ok and motivo and not motivo.startswith("Falta"):
                # Quem diz qual campo nao cabe e a propria validacao. Ler isso
                # do texto da mensagem falhava no acento de "horário".
                if campo == "horario":
                    state.horario = ""
                else:
                    state.data_iso = ""
                    state.data_agendada = ""
                    state.data_confirmada = False
                return self._finalizar(state, motivo)

        # 3.3 Diretivas desta resposta (vao para o fim do system prompt)
        state.diretivas_runtime = self._montar_diretivas(state, expediente, now)

        # 4. Router classifica
        from agents.router import classify_intent
        router_result = classify_intent(message, state.to_dict())

        print(
            f"[router] phone={phone} intent={router_result.get('intencao')} "
            f"phase={router_result.get('fase')} "
            f"agent={router_result.get('agente')} "
            f"confidence={router_result.get('confianca', 0)}"
        )

        # 5. Atualiza fase se confianca alta (mas NAO reverte fase ja avancada)
        if router_result.get("confianca", 0) > 0.7:
            new_phase = router_result.get("fase", state.phase)
            # So avanca fase, nunca volta para triagem se ja tem dados
            phase_order = ["triagem", "cardapio", "pedido", "logistica", "pagamento"]
            current_idx = phase_order.index(state.phase) if state.phase in phase_order else 0
            new_idx = phase_order.index(new_phase) if new_phase in phase_order else 0
            # SE ja tem itens extraidos, NAO deixa o router reverter para triagem
            if state.items:
                # Mantem o agente ja definido pela extracao - so avanca, nunca volta
                if new_idx > current_idx:
                    state.phase = new_phase
                    state.active_agent = router_result.get("agente", state.active_agent)
                    state.intent = router_result.get("intencao", state.intent)
            elif new_idx >= current_idx:
                state.phase = new_phase
                state.active_agent = router_result.get("agente", state.active_agent)
                state.intent = router_result.get("intencao", state.intent)

        # 6. Executa agente ativo
        agent = self.agents.get(state.active_agent)
        if not agent:
            agent = self.agents["triagem"]
            state.active_agent = "triagem"

        response = agent.process(message, state, media)

        return self._finalizar(state, response)

    def _finalizar(self, state: ConversationState, response: str) -> str:
        """Grava a resposta no historico e persiste o estado.

        Caminho unico de saida: as respostas dadas em codigo (expediente,
        agendamento invalido) passam pelos mesmos registros das respostas do
        modelo, inclusive a marca de que a saudacao ja saiu.
        """
        state.history.append({
            "role": "assistant",
            "text": response,
            "timestamp": time.time(),
        })
        state.last_bot_message = response
        state.saudacao_feita = True
        state.save_to_db()
        return response

    def _montar_diretivas(self, state: ConversationState, expediente: dict, now) -> str:
        """Regras que valem so para esta resposta, ja resolvidas em codigo.

        Sao poucas e concretas de proposito: uma saudacao, uma proxima
        pergunta, uma lista de coisas que nao podem ser assumidas. O prompt em
        markdown descreve o fluxo; isto aqui decide o passo.
        """
        import horario

        linhas = []

        if not state.saudacao_feita:
            nome = f", {state.name}" if state.name else ""
            linhas.append(
                f'1. Esta e a PRIMEIRA resposta desta conversa. Comece por '
                f'"{horario.saudacao(now)}{nome}!" e apresente-se como Sandrinha da '
                f'BALBS. Nao pule a saudacao mesmo que o cliente ja tenha mandado '
                f'o pedido inteiro.'
            )
        else:
            linhas.append("1. A saudacao ja foi feita. NAO cumprimente de novo.")

        faltando = state.dados_faltando()
        if faltando and not state.modality:
            # Antes da modalidade a coleta segue de uma em uma: perguntar
            # endereco e frete sem o cliente ter dito que quer entrega e
            # justamente o que o funil evita.
            linhas.append(
                f"2. Ainda faltam: {', '.join(faltando)}. Pergunte APENAS "
                f'"{faltando[0]}" nesta mensagem, uma pergunta so. Nao pergunte o '
                f"resto agora e nao apresente resumo nem total."
            )
        elif faltando:
            # Definida a modalidade, o resto vai numa mensagem so. O dono
            # contou dez perguntas pingadas para fechar um pedido (23/09) e
            # pediu bairro, rua com numero, dia, horario e pagamento juntos.
            itens = "; ".join(faltando)
            linhas.append(
                f"2. Ainda faltam: {itens}. Peca TODOS eles NESTA MESMA "
                f"mensagem, em lista vertical curta (um item por linha), e so "
                f"eles. E PROIBIDO perguntar um de cada vez, PROIBIDO repetir o "
                f"que ja esta na ficha e PROIBIDO pedir ponto de referencia. "
                f"Nao apresente resumo nem total enquanto faltar algum."
            )
        else:
            linhas.append(
                "2. Todos os dados obrigatorios estao coletados. Chame "
                "calculate_order e apresente o resumo."
            )

        if not state.modality:
            linhas.append(
                "3. A modalidade NAO foi definida. E proibido falar em entrega, "
                "endereco, bairro, frete ou taxa de entrega antes do cliente "
                "responder se e entrega ou retirada."
            )
        else:
            linhas.append(f"3. Modalidade ja definida pelo cliente: {state.modality}.")

        if not state.data_iso:
            linhas.append(
                "4. O cliente NAO informou o DIA do pedido. E proibido assumir "
                '"hoje". Pergunte para qual dia e o pedido.'
            )
        else:
            linhas.append(
                f"4. Dia do pedido: {state.data_agendada}. Escreva o dia assim, por "
                f"extenso com a data, em toda confirmacao e no resumo — nunca so "
                f'"hoje".'
            )

        if not expediente["aceita_hoje"]:
            linhas.append(
                "5. NAO aceite pedido para hoje. Ofereca "
                f"{horario.formatar_data(expediente['sugestao_data'], now)}."
            )

        return "\n".join(linhas)

    def reset_conversation(self, phone: str):
        """Reseta conversa."""
        state = ConversationState(phone=phone)
        state.save_to_db()


# Instancia global
_manager = None


def get_manager() -> ConversationManager:
    """Retorna instancia singleton do manager."""
    global _manager
    if _manager is None:
        _manager = ConversationManager()
    return _manager
