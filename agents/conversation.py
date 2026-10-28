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
    # "croquete" sozinho e como "coxinha": serve para o grande (Croquete de
    # Macaxeira, R$ 9,00) e para o sabor de lote. Abaixo de 25 a triagem
    # pergunta o tamanho; de 25 para cima a tabela resolve o lote. Ia direto
    # para Croquete de Carne e "10 croquetes" virava "fazemos de 25 em 25".
    "croquete": "Croquete",
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
    "canudinho de camarao": "Canudinho de Camarão",
    "canudinho de camarão": "Canudinho de Camarão",
    # Sabores da porcao de 25 assados cadastrada em 07/10.
    "calzone": "Calzone de frango",
    "calzone de frango": "Calzone de frango",
    # Sem "salsicha" na chave: "50 de salsicha" continua sendo o frito, e nao
    # uma duvida entre os dois.
    "enroladinho assado": "Enroladinho de salsicha com Catupiry",
    "enroladinho com catupiry": "Enroladinho de salsicha com Catupiry",
    # "1 cento de canudos de frango" (teste do dono, 05/10).
    "canudo de frango": "Canudinho de Frango",
    "canudo de camarao": "Canudinho de Camarão",
    "canudo de camarão": "Canudinho de Camarão",
}

_PRODUTOS_GRANDES = {
    "quiche": "Quiche de Frango",
    "quiche de frango": "Quiche de Frango",
    "quiche de carne de sol": "Quiche de Carne de Sol",
    # "1 cento de mini quiche" virava 100 Quiches de Frango a R$ 12,00 e a falta
    # da mini so aparecia no fim (Thayline, 23/10).
    # Preco e unidade seguem o cardapio digital (decisao do dono, 23/10): hoje
    # "Mini quiches (Unidade) = R$ 150,00". Se a unidade for a caixa com um
    # cento, o acerto e no cardapio (o nome do item), nao aqui.
    "mini quiche": "Mini quiches",
    "mini quiches": "Mini quiches",
    # "mini quiche de frango": a chave mais longa era "quiche de frango" e virava
    # a quiche grande de R$ 12 (26/10).
    "mini quiche de frango": "Mini quiches",
    "mini quiches de frango": "Mini quiches",
    "mini quiche de carne de sol": "Mini quiches",
    "mini quiches de carne de sol": "Mini quiches",
    "saltenha": "Saltenha Assada",
    "saltenha assada": "Saltenha Assada",
    # A unica saltenha frita do cardapio e o kit de mini: "saltenha frita"
    # perdia o "frita" na limpeza do nome e virava Saltenha Assada, 20 a
    # R$ 180 (teste de 27/10 16h24).
    "saltenha frita": "Kit Mini Saltenha Frita (10und)",
    "mini saltenha": "Kit Mini Saltenha Frita (10und)",
    "mini saltenha frita": "Kit Mini Saltenha Frita (10und)",
    "kit mini saltenha": "Kit Mini Saltenha Frita (10und)",
    "kit de mini saltenha": "Kit Mini Saltenha Frita (10und)",
    "kit mini saltenha frita": "Kit Mini Saltenha Frita (10und)",
    # "1 kit mini pizza" (esgotado) virava 1 Mini Pizza avulsa de R$ 12 sem
    # aviso (varredura de 27/10).
    "kit mini pizza": "Kit mini pizza (8und)",
    "kit de mini pizza": "Kit mini pizza (8und)",
    "kit mini pizzas": "Kit mini pizza (8und)",
    # "10 mini lanches" (esgotado) nao virava item, e o modelo oferecia "25 mini
    # lanches" pela regra da festa (varredura de 27/10).
    "mini lanche": "Mini Lanches",
    "mini lanches": "Mini Lanches",
    "panqueca": "Panqueca de Carne",
    "panqueca de carne": "Panqueca de Carne",
    "panqueca de frango": "Panqueca de Frango",
    "bomba": "Bomba de Carne de Sol",
    "bomba de carne de sol": "Bomba de Carne de Sol",
    "empada": "Empada de Frango",
    "empada de frango": "Empada de Frango",
    "empada de camarao": "Empada de Camarão",
    "empada de camarão": "Empada de Camarão",
    "croquete de macaxeira": "Croquete de Macaxeira",
    # "3 unidades de mini pizza" e "3 fatias de pudim" nao viravam item: o
    # resumo saiu so com a coca e o modelo inventou porcao de 25 (24/10).
    "mini pizza": "Mini Pizza (Brotinho)",
    "pizza brotinho": "Mini Pizza (Brotinho)",
    "brotinho": "Mini Pizza (Brotinho)",
    "pudim": "Pudim (Fatia)",
    # Varredura do cardapio (26/10): estes caiam no produto errado ("empada
    # doce" virava Empada de Frango, "rissole de queijo" virava o de carne,
    # "kit empadinhas" virava 1 empadinha) ou nao viravam nada.
    "rissole de queijo e presunto": "Rissole de Queijo e Presunto",
    "rissole de queijo": "Rissole de Queijo e Presunto",
    # Como a cliente escreve, sem o "de" ou com a ordem trocada (27/10).
    "rissole queijo e presunto": "Rissole de Queijo e Presunto",
    "rissole queijo": "Rissole de Queijo e Presunto",
    "rissole de presunto e queijo": "Rissole de Queijo e Presunto",
    "rissole presunto e queijo": "Rissole de Queijo e Presunto",
    "empada doce": "Empada Doce",
    "empada de leite condensado": "Empada Doce",
    "kit empadinhas": "Kit Empadinhas (15 unid)",
    "kit de empadinhas": "Kit Empadinhas (15 unid)",
    "kit mini empadinhas": "Kit Mini Empadinhas",
    "kit de mini empadinhas": "Kit Mini Empadinhas",
    "centenario": "Centenário (fatia)",
    "parecis": "Refrigerante Parecis",
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
    "Croquete": 0.80,
    "Misto": 0.80,
    # Produtos Assados: R$ 30,00 / 25 un = R$ 1,20/un
    "Esfirra de Carne": 1.20,
    "Empadinha de Frango": 1.20,
    "Pao Pizza": 1.20,
    "Canudinho de Frango": 1.20,
    "Canudinho de Camarão": 1.20,
    # Produtos Grandes: preços individuais
    "Quiche de Frango": 12.00,
    "Quiche de Carne de Sol": 12.00,
    "Saltenha Assada": 9.00,
    "Saltenha Frita": 10.00,
    "Panqueca de Carne": 15.00,
    "Panqueca de Frango": 15.00,
    "Bomba de Carne de Sol": 10.00,
    "Empada de Frango": 9.00,
    "Empada de Camarão": 9.00,
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
    r"\bentrega\s+(?:hoje|amanha|depois|pra|para|em|na|no|as|ate|neste|nesse"
    r"|rua|r|av|avenida|travessa|bairro|agora|aqui)\b"
    # "pra amanha as 15h, entrega, pago no pix": a palavra solta entre
    # virgulas, ou no fim (simulador do dono, 14/10).
    r"|(?:^|[,;.]|\d\s*h(?:oras?)?|\d)\s*entrega\s*(?:[,;.!]|$|(?=pag|pix|cartao|dinheiro))"
)

# Intencao de retirar, dita com verbo: vale mesmo numa frase com pergunta.
_RETIRADA_EXPLICITA = re.compile(
    r"\bretirar\b"
    # "vou busca", sem o r (Thayline, 24/10).
    r"|\b(?:vou|posso|prefiro|quero|queria|resolvi|melhor|vamos)\s+(?:retirar?|buscar?|pegar?)\b"
    # "OPA RESOLVI PEGAR NA LOJA" (teste do dono, 17/10): o espelho voltava
    # com a entrega e o frete.
    r"|\b(?:peg|busc|retir)\w*\s+(?:ai\s+|la\s+)?na\s+loja\b"
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
    # tratamento, nao nome: o audio virou "Boa noite, Dona!" (teste do dono, 24/10)
    "dona", "dono", "moca", "moco", "senhora", "senhor", "menina", "atendente", "sandrinha",
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
    # "um centro de canudinho" e um cento, nao o bairro Centro (10/10 08h39).
    alvo = re.sub(r"\b(?:um|uma|meio|dois|duas|tres|\d+)\s+centros?\s+de\b", " ", alvo)
    if not alvo.strip():
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
    if not achado:
        # "BAIRRO SAO BOSCO": o nome dito depois da palavra "bairro", mesmo
        # encurtado, passa pela busca da tabela de frete (Sao Joao Bosco).
        m = re.search(r"\bbairro\s+([a-z][a-z ]{2,40}?)(?=\s+(?:as|hoje|amanha|rua|av|avenida"
                      r"|numero|n|pagamento|pago|pix|cartao|dinheiro|para|pra|dia|data)\b|\s*\d|$)",
                      alvo)
        if m:
            achado_tabela = facts.lookup_frete(m.group(1))
            if achado_tabela:
                achado = achado_tabela["bairro"]
    if not achado and re.fullmatch(r"[a-z ]{4,30}", alvo.strip()) and len(alvo.split()) <= 3:
        # Resposta seca so com o bairro, digitado errado ("IGAPARE"): a
        # tabela de frete tolera erro de digitacao. Virava a RUA, e o bairro
        # era perguntado de novo (simulador do dono, 14/10).
        achado_tabela = facts.lookup_frete(alvo.strip())
        # Uma palavra so, que e o comeco de um bairro de varias ("RONALDO" ->
        # Ronaldo Aragao), e nome de gente: respondia "informe seu nome" e
        # trocou o bairro e o frete do pedido (teste do dono, 17/10).
        if (achado_tabela and len(alvo.split()) == 1
                and len(facts.normalize(achado_tabela["bairro"]).split()) > 1):
            achado_tabela = None
        if achado_tabela:
            achado = achado_tabela["bairro"]
    return achado


# Erro de digitacao das duas palavras que decidem o funil. No teste de 01/10 a
# cliente escreveu "entrga" e "enytrega": a modalidade nao entrou na ficha e a
# Sandrinha respondeu com a lista "Informe: - Entrega ou retirada", pedindo
# justamente o que acabara de ser dito. Uma letra a mais, a menos ou trocada
# (distancia 1) em palavra de 6+ letras; "entrada" (distancia 2) nao conta.
_PALAVRAS_DO_FUNIL = ("entrega", "entregar", "retirada", "retirar")


def _distancia_ate_1(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        dif = [i for i in range(len(a)) if a[i] != b[i]]
        return len(dif) == 1 or (
            len(dif) == 2 and dif[1] == dif[0] + 1
            and a[dif[0]] == b[dif[1]] and a[dif[1]] == b[dif[0]])
    curta, longa = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(longa)):
        if longa[:i] + longa[i + 1:] == curta:
            return True
    return False


def corrigir_digitacao_do_funil(texto_normalizado: str) -> str:
    def _troca(m):
        palavra = m.group(0)
        if len(palavra) < 6 or palavra in _PALAVRAS_DO_FUNIL:
            return palavra
        for certa in _PALAVRAS_DO_FUNIL:
            if palavra[0] == certa[0] and _distancia_ate_1(palavra, certa):
                return certa
        return palavra
    return re.sub(r"[a-z]+", _troca, texto_normalizado or "")


_RX_PERGUNTA_DE_MODALIDADE = re.compile(
    r"\b(?:consigo|posso|da\s+pra|da\s+para|tem\s+como|pode|voces?\s+(?:entregam|fazem))\b"
    r".{0,30}\b(?:retir\w*|busc\w*|peg\w*|entreg\w*|receb\w*)"
    r"|\b(?:retir\w*|busc\w*|entreg\w*)\b.{0,20}\b(?:consigo|posso|da\s+pra|tem\s+como)\b")
_RX_TEM_PEDIDO = re.compile(r"\b(?:quero|queria|vou\s+querer|manda|me\s+ve)\b|\d+\s*(?:un\b|unidades|cox|salg|past)")


def detectar_modalidade(message: str) -> str:
    """'entrega', 'retirada' ou '' quando o cliente ainda nao escolheu.

    O teste anterior era um `"entrega" in mensagem` cru: "quanto fica a taxa de
    entrega?" ja marcava o pedido como entrega, e o agente seguia cobrando frete
    sem nunca ter perguntado entrega ou retirada. Aqui so conta intencao
    explicita — pergunta sobre entrega nao escolhe nada.
    """
    import horario as _horario

    t = corrigir_digitacao_do_funil(_horario._norm(message))
    if not t:
        return ""

    # "consigo retirar um pedido as 7:40?" e PERGUNTA, nao escolha: virava
    # retirada e ela respondia o frete com "Voce escolheu retirada, entao nao
    # ha taxa de entrega" (teste do dono, 06/10).
    if "?" in t and _RX_PERGUNTA_DE_MODALIDADE.search(t) and not _RX_TEM_PEDIDO.search(t):
        return ""
    ret = [m.start() for m in _RETIRADA_EXPLICITA.finditer(t)]
    ent = [m.start() for m in _ENTREGA_EXPLICITA.finditer(t)]
    if ret:
        ent += [m.start() for m in _ENTREGA_SUBSTANTIVO.finditer(t)]
    if ret and ent:
        # "pra retirar agora. Nao, mentira, entrega aqui no Bairro..." - vale
        # a ultima: o cliente se corrigiu (teste do dono, 05/10).
        return "entrega" if max(ent) > max(ret) else "retirada"
    if ret:
        return "retirada"
    if ent:
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


_RX_HORA_ATUAL = re.compile(
    r"\b(?:agora\s+)?(?:sao|ja\s+sao)\s+(?:quase\s+)?\d{1,2}\s*(?:h|:|horas?)\s*\d{0,2}"
    r"(?=\s*(?:e\b|,|\.|$|da\s+(?:manha|tarde|noite)|aqui))")


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
    # "pra entregar agora, sao 12h30 e o pessoal ta com fome": a hora de
    # AGORA, nao a do pedido (teste do dono, 06/10 - virou pedido para 12h30
    # e ela avisou do intervalo de almoco).
    t = _RX_HORA_ATUAL.sub(" ", t)
    hm = _horario.parse_horario(t)
    if hm:
        return _horario.formatar_horario(*hm)
    # "pra agora", "agora mesmo", ou "agora" sozinho como resposta a "qual
    # horario?": o prompt manda registrar a hora atual. No teste de 01/10 a
    # cliente respondeu "agora", "agora mesmo" e "pra agora" e a Sandrinha
    # perguntou "Qual horário?" tres vezes seguidas.
    if detectar_imediato(t):
        return _horario.HORARIO_IMEDIATO
    return ""


_IMEDIATO = re.compile(
    r"\b(?:pra|para)\s+agora\b|\bagora\s+mesmo\b|\bimediat[oa]\b"
    # "e agora" so quando fecha a frase ("e agora!" = e para agora); "e agora,
    # o que eu faco?" e duvida, nao horario.
    r"|\be\s+agora\s*[.!]*\s*$"
    # "pra retirar agora", "entrega agora", "pode mandar agora" (teste de
    # 02/10: so "pra agora" era entendido, e ela pedia data e horario).
    r"|\b(?:retir\w*|entreg\w*|buscar|busco|pegar|pego|mandar|manda|levar|enviar|sair|"
    r"saia|fazer|preparar)\s+(?:(?:ele|ela|eles|elas|isso|tudo)\s+)?agora\b"
    # "quero 25 coxinhas agora": o "agora" fechando o pedido.
    r"|\b(?:quero|queria|preciso|manda|mande)\b.*\bagora\s*[.!]*\s*$"
    r"|\bo\s+(?:quanto\s+antes|mais\s+rapido(?:\s+possivel)?)\b"
    r"|^\s*(?:agora|ja|ja\s+ja|agorinha)\s*(?:pf|por\s+favor|pfv)?\s*[.!]*\s*$"
)


def detectar_imediato(message: str) -> bool:
    import horario as _horario
    return bool(_IMEDIATO.search(_horario._norm(message)))


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
    # "rua guapore 120 no pix": o "no" sobrava no endereco (02/10).
    r"\s+(?:(?:no|na|em|pelo|pela)\s+(?:pix|cart[aã]o|dinheiro|debito|credito)|"
    r"bairro|pagamento|pago|vou\s+pagar|pix|cart[aã]o|dinheiro|troco|"
    r"[aà]s\s|\d{1,2}\s*h(?:oras)?\b|hoje|amanh[aã]|depois\s+de\s+amanh[aã]|"
    r"data|hor[aá]rio|ponto\s+de\s+refer|refer[eê]ncia|entrega|retirada|"
    # "rua julia 6559 igarape pra agora" foi impresso assim no espelho (teste
    # de 01/10): o "pra agora" e horario, e o "pra" de "pra hoje" sobrava.
    r"p(?:a)?ra\s+(?:hoje|amanh|agora|as|às)|agora\b|"
    # Dia da semana so com cara de data: "Rua Quinta do Sol" e "Avenida
    # Domingos" sao nome de rua (o corte depois do numero pega o resto).
    r"(?:segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo)"
    r"(?:[\s-]*feira\b|\s+(?:[aà]s?|de\s+manh|[aà]\s+tarde|[aà]\s+noite)\b|\s*$))",
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


# Rua dita no COMECO da resposta, sem "Rua": "NA CALAMA 2025 APONIA ...". So
# vale com a entrega definida (e ai o endereco e a proxima pergunta).
_RUA_NO_INICIO = re.compile(
    r"^\s*(?:(?:na|no|em)\s+)?"
    r"(?P<nome>(?!(?:bairro|dia|data|hora|rua|avenida|av)\b)"
    r"[A-Za-zÀ-ÿ]{3,}(?:\s+[A-Za-zÀ-ÿ]+){0,3}?)"
    r"\s*,?\s*(?:n[º°o]?\s*)?(?P<numero>\d{1,6})\b",
    re.IGNORECASE)

# Palavra que nunca faz parte do nome de uma rua dita solta: "entregar no dia
# 29" e data, "pra retirar as 10" e hora.
_NAO_E_NOME_DE_RUA = re.compile(
    r"\b(?:dia|data|hora|horas|entreg\w*|retir\w*|busc\w*|as|às|pra|para)\b")


# Como o cliente pede sem dizer numero: "meio cento de coxinha", "um cento de
# pastel", "uma porcao de quibe". E a forma mais natural de pedir salgado no
# balcao, e a extracao ignorava as tres — a ficha ficava sem item e a Sandrinha
# pedia os itens de novo depois de o cliente ja ter dito (teste do dono, 25/09).
# Os termos de volume do dono (25/09): meio cento = 50, cento = 100,
# cento e meio = 150, dois centos = 200. "cento e meio" vem ANTES de "cento"
# na alternancia, senao casaria so o "cento" e o pedido sairia pela metade.
_PACOTE_POR_EXTENSO = re.compile(
    # "um cento e meio" e "cento e cinquenta" nao tem produto "e meio" (23/10).
    r"\b(?:(uma?|1)\s+)?("
    r"por[cç][aã]o|meio\s+cento|cento\s+e\s+meio|"
    r"(?:dois|duas|tr[eê]s|quatro|\d{1,2})\s+centos?|cento"
    r")\s+(?:de\s+|d[eo]s\s+)?(?!e\s)([a-zà-ÿ][a-zà-ÿ\s]{2,40}?)"
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
        if p in _GRAFIAS_DE_PASTEL:
            p = "pastel"
        elif p in ("paes", "pao"):
            # "25 paes pizza" virava "pae pizza" e o Pao Pizza sumia do pedido
            # (testes do dono, 07/10 20h29 e 21h02).
            p = "pao"
        elif p in ("risole", "risoles"):
            # "risoles queijo e presunto" so casava com a chave curta "risole"
            # e virava Rissole de Carne na comanda (teste de 27/10 18h31).
            p = "rissole"
        elif len(p) > 3 and p.endswith("s"):
            p = p[:-1]
        palavras.append(p)
    return " ".join(palavras)


# "PASTEIS" perdia o "l" no corte do plural ("pastei") e "PASTEID" e como o
# cliente digitou as 14h37 de 07/10: os dois sumiam do pedido.
_GRAFIAS_DE_PASTEL = frozenset({"pasteis", "pastei", "pasteid", "pasteiz", "pastes",
                                "pastels", "pasteu", "pasteus",
                                # "100 PATEIS DE QUEIJO" (teste do dono, 07/10 18h39)
                                "pateis", "patel", "pateu", "pateus", "patels"})


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


def _corrigir_grafia(bruto: str) -> str:
    """"salschisa" -> "salsicha": a palavra do cardapio mais parecida, ou "".

    Conservador de proposito: so palavra de 6 letras ou mais, que nao casou
    com nada, e com semelhanca alta (0,8). Palavra comum da conversa
    ("pagamento", "amanha") nao chega perto de nenhum produto.
    """
    import difflib
    palavras = _forma_de_busca(bruto).split()
    if not palavras:
        return ""
    vocabulario = sorted({w for chave in _indice_de_produtos() for w in chave.split()
                          if len(w) >= 6})
    trocou = False
    for i, w in enumerate(palavras):
        if len(w) < 6 or w in vocabulario:
            continue
        perto = difflib.get_close_matches(w, vocabulario, n=2, cutoff=0.8)
        # Dois candidatos igualmente perto: nao chuta.
        if perto and (len(perto) == 1 or difflib.SequenceMatcher(None, w, perto[0]).ratio()
                      > difflib.SequenceMatcher(None, w, perto[1]).ratio()):
            palavras[i] = perto[0]
            trocou = True
    return " ".join(palavras) if trocou else ""


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
    # Preparo sozinho nao e produto: "3 centos assados" achava "assado" dentro
    # da chave "enroladinho assado" e o pedido saia com 300 Enroladinhos de
    # salsicha com Catupiry, sabor que a cliente nunca disse (teste de 22/10).
    if alvo in ("assado", "assada", "frito", "frita", "salgado", "salgadinho"):
        return None
    # Tamanho no meio do nome: "risole grande de queijo e presunto" casava so
    # "rissole" e virava o de carne (27/10). O tamanho e lido a parte.
    if len(alvo.split()) > 2:
        alvo = " ".join(w for w in alvo.split() if w != "grande")

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


def sabores_possiveis(bruto: str, so_lote: bool = True, preparo: str = "") -> list[str]:
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
    # "1940 e de hoje" (audio, 19h40): "e de" cabia em "rissole DE carne" e
    # virou pergunta do sabor de 1940 salgados (teste do dono, 24/10).
    if not any(len(w) >= 3 for w in alvo.split() if w not in ("de", "da", "do", "e", "com")):
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
    # Sabores de pacote do cardapio que o mapa do codigo nao conhece: "30 de
    # queijo" nunca oferecia o Misto de Queijo e Presunto (teste de 03/10).
    do_cardapio = []
    if so_lote:
        try:
            import facts
            ja = {facts.normalize((facts.resolve_unit_price(a, 25) or {}).get("item_oficial") or a)
                  for a in achados}
            for p in facts.get()["produtos"]:
                if (facts.normalize(p["categoria"]) in ("fritos", "assados")
                        and p.get("preco") is None and p.get("disponivel", True)
                        and alvo in _forma_de_busca(p["item"])
                        and facts.normalize(p["item"]) not in ja):
                    achados.append(p["item"])
                    do_cardapio.append(p["item"])
                    ja.add(facts.normalize(p["item"]))
        except Exception:
            pass
    if len(achados) < 2:
        return []
    if so_lote:
        de_lote_do_dono = {e["nome_planilha"] for e in _extras_do_dono() if e["tipo"] == "sabor"}
        de_lote = [a for a in achados if a in _SABORES_DE_LOTE or a in de_lote_do_dono
                   or a in do_cardapio]
        if de_lote:
            achados = de_lote
    # So entra o que o cardapio vende no pacote: o sabor tem de cair numa linha
    # de Fritos/Assados. "Pastel Queijo" so existe no combo e era resolvido
    # para o Rissole de Queijo e Presunto grande - passava como disponivel e
    # era oferecido para "30 de queijo" (teste de 03/10).
    # E, se o cliente disse o preparo, so os dele: "100 salgados fritos: 30 de
    # carne" recebia Esfirra de carne, que e assada.
    categorias = {"fritos", "assados"} if so_lote else set()
    if preparo:
        categorias = {"fritos"} if preparo == "frito" else {"assados"}
    # Esgotado tambem nao entra: oferecer o que a fabrica nao tem e so adiar
    # a pergunta.
    disponiveis = []
    for nome in achados:
        try:
            import facts
            oficial = facts.resolve_unit_price(nome, 25)
            if not oficial:
                continue
            categoria = facts.normalize(str(oficial.get("base", "")).split(":")[0])
            if categorias and categoria not in categorias:
                continue
            disponiveis.append(nome)
        except Exception:
            disponiveis.append(nome)
    if not disponiveis and preparo:
        # Nenhum sabor daquele preparo: pergunta entre os que existem.
        return sabores_possiveis(bruto, so_lote)
    return disponiveis or achados


def volume_sem_sabor(msg_lower: str) -> int:
    """Unidades pedidas por volume SEM dizer o sabor. 0 quando nao ha.

    "Quero um cento de salgados fritos" nao tem sabor nenhum para virar item, e
    a ficha ficava vazia. A regra do dono (25/09) pede resposta especifica:
    oferecer ate Total/25 sabores e perguntar o preparo.
    """
    achados = []
    for _um, formato, produto in _PACOTE_POR_EXTENSO.findall(msg_lower):
        unidades = _unidades_do_formato(formato)
        # "meio cento de brigadeiro" e doce, com formato proprio - nao e volume
        # de salgado esperando sabor.
        if _RX_DOCE.search(produto or ""):
            continue
        if unidades and not _produto_conhecido(produto):
            achados.append((unidades, produto or ""))
    if achados:
        # "1 cento e meio de frito e 1 cento e meio assados": um volume por
        # preparo, e o total e a soma. Saia 150 e ela oferecia 6 sabores para
        # 300 salgados (teste da Thayline, 23/10).
        preparos = {"frito" if _RX_FRITO.search(p) else "assado" if _RX_ASSADO.search(p) else ""
                    for _, p in achados}
        if len(achados) > 1 and preparos == {"frito", "assado"}:
            return sum(u for u, _ in achados)
        return achados[0][0]
    # "500 salgados", "200 salgadinhos": o total em numero. No teste de 01/10
    # nao era lido como volume e o modelo inventou a conta ("10 sabores fritos
    # e 10 assados" para 20 porcoes). Acima de 1.000 quem atende e a equipe
    # comercial, e quantidade fora do multiplo de 25 tem outra regra.
    m = _RX_TOTAL_DE_SALGADOS.search(msg_lower or "")
    if m:
        unidades = int(m.group(1))
        if 25 <= unidades < 1000 and unidades % 25 == 0:
            return unidades
    # "300 fritos", "200 fritos e 100 assados": o preparo no lugar de
    # "salgados" tambem e o total (teste de 22/10 - a ficha ficava sem volume).
    unidades = sum(int(n) for n in _RX_TOTAL_POR_PREPARO.findall(msg_lower or ""))
    if 25 <= unidades < 1000 and unidades % 25 == 0:
        return unidades
    # "Quero 1 cento, mas nao sei quais sabores. Escolhe por mim" (teste de
    # 06/10): o cento sozinho, sem "de salgados", tambem e o volume.
    m = _RX_CENTO_SOLTO.search(msg_lower or "")
    if m:
        if m.group("meio"):
            return 50
        if m.group("e_meio"):
            return 150
        n = m.group("n")
        n = int(n) if n.isdigit() else _CENTOS_SOLTOS.get(n, 1)
        if 1 <= n <= 9:
            return n * 100
    return 0


_CENTOS_SOLTOS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3, "quatro": 4,
                  "cinco": 5}
_RX_CENTO_SOLTO = re.compile(
    r"\b(?:(?P<meio>meio\s+cento)|(?P<n>\d|um|uma|dois|duas|tres|três|quatro|cinco)\s+cento"
    r"(?P<e_meio>\s+e\s+meio)?s?)\b"
    # "meio cento de brigadeiro" e doce com formato proprio: so vale o cento
    # solto ou "de salgados/fritos/assados".
    r"(?!\s+(?:de|do|da|dos|das)\s+(?!salgad|salgadinh|frit|assad|variad|sortid)\w)")


# "QUERO 400 VARIADOS" tambem e o total (teste do dono, 03/10).
_RX_TOTAL_DE_SALGADOS = re.compile(
    r"(?<![\d,.])(\d{2,3})\s+(?:mini\s*)?(?:salgad\w*|variad\w*|sortid\w*|unidades)\b(?!\s+filho)")
_RX_NOME_DE_RUA = re.compile(
    r"\b(?:rua|r\.|av\.?|avenida|travessa|tv\.?|alameda|al\.?|estrada|rodovia|pra[cç]a)"
    r"\s+[^\d,.;\n]{2,60}?(?=\s*,?\s*(?:n[º°o.]?\s*)?\d)",
    re.IGNORECASE)
_RX_TOTAL_POR_PREPARO = re.compile(r"(?<![\d,.])(\d{2,3})\s+(?:frit|assad)[oa]s\b")


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
    r"|\b(?P<numero>\d{2,3})\s+(?:salgad\w*|unidades?|variad\w*|sortid\w*|misturad\w*)\b",
    re.IGNORECASE)

# Onde a lista de sabores acaba: logistica, outro numero ou fim de frase.
_RX_FIM_DOS_SABORES = re.compile(
    r"\bpra\b|\bpara\b|\bhoje\b|\bamanh[aã]\b|\bretir\w*|\bentreg\w*|\bbusc\w*"
    r"|\b[aà]s\b|\d|[.;!?:\n]")

# O que vem antes do sabor e nao e sabor: "sendo metade", "a outra metade de".
_RX_ANTES_DO_SABOR = re.compile(
    r"^(?:(?:sendo|de|dos|das|com|a|o|outra|outro|metade|meio|variad[oa]s?"
    r"|sortid[oa]s?|misturad[oa]s?|mist[oa]s)(?:\s+|$))+",
    re.IGNORECASE)

# Palavra de volume, nao de sabor: "cento de salgados fritos" nao diz o sabor.
_RX_SO_GENERICO = re.compile(
    r"^(?:salgad\w*|salgadinh\w*|frit\w*|assad\w*|sortid\w*|variad\w*|misturad\w*|\s)+$")


_RX_ANTES_DO_SABOR_CITADO = re.compile(
    r"^(?:(?:eu|quero|queria|gostaria|pode|podes|coloca|colocar|poe|bota|mais|so|"
    # "Acrescenta bolinha", "aumenta a coxinha": resposta a pergunta do dono
    # sobre o cento com 3 sabores (reuniao de 01/10).
    r"acrescenta|acrescentar|acrescente|adiciona|adicionar|aumenta|aumentar|"
    r"aumente|inclui|incluir|outro|outra|porcao|porção|de mais|"
    r"somente|apenas|tambem|os|as|o|a|de|dos|das|com|sabor|sabores|esses|um|uma|"
    r"sendo|ser|e)(?:\s+|$))+",
    re.IGNORECASE)


def sabores_citados(texto: str) -> list[str]:
    """Sabores de LOTE nomeados na frase, sem quantidade. [] se nenhum.

    "Quero coxinha, pastel e churros" depois de "1 cento de salgado": os
    sabores vinham sem numero, nada entrava na ficha e a divisao do cento
    ficava com o modelo, que se perdeu (teste do dono, 30/09 15h49).
    """
    alvo = " ".join((texto or "").lower().split())
    if re.search(r"\d", alvo):
        return []            # com numero, quem trata e a extracao normal
    de_lote_do_dono = {e["nome_planilha"] for e in _extras_do_dono() if e["tipo"] == "sabor"}

    def _de_lote(nome):
        return bool(nome) and (nome in _SABORES_DE_LOTE or nome in de_lote_do_dono)

    achados = []
    # "canudo de frango e camarao": o produto vale para o sabor seguinte. O
    # camarao sumia e o cento inteiro ia para o frango (Camyli, 26/10).
    prefixo = ""
    for parte in re.split(r"\s*(?:,|;|/|\+|\be\b|\bou\b)\s*", alvo):
        parte = _RX_ANTES_DO_SABOR_CITADO.sub("", parte.strip()).strip(" .!?")
        if len(parte) < 3:
            continue
        for pedaco in _separar_sabores_sem_virgula(parte, _de_lote):
            nome = _produto_conhecido(pedaco)
            if not _de_lote(nome) and prefixo:
                nome = _produto_conhecido(prefixo + re.sub(r"^d[eao]\s+", "", pedaco))
            if _de_lote(nome) and nome not in achados:
                achados.append(nome)
                m = re.match(r"(.+?\bd[eao]\s+)", pedaco)
                if m:
                    prefixo = m.group(1)
    return achados


_LIGA_SABOR = {"de", "da", "do", "com", "e"}


# "quiche de frango 2 unidades": a quantidade DEPOIS do produto.
_RX_QTD_NO_FIM = re.compile(
    r"^\s*(?P<prod>[a-zà-ÿ][a-zà-ÿ\s]{2,60}?)\s+(?P<qtd>\d{1,3})\s*(?:unidades?|un|unid|uni)?\s*[.!]*\s*$")
_RX_SO_PORCOES = re.compile(
    r"^\s*(?:quero\s+|pode ser\s+|manda\s+|vou querer\s+)?(?P<n>\d{1,2}|uma|duas|tres|três|quatro)"
    r"\s+por[cç](?:ao|ão|oes|ões)\s*[.!]*\s*$")
# "de frango 2 unidades", "2 de frango": so o complemento do produto.
_RX_SO_COMPLEMENTO = re.compile(
    r"^\s*(?:(?P<qtd1>\d{1,3})\s+)?(?:o\s+|a\s+)?(?P<comp>d[eoa]s?\s+[a-zà-ÿ][a-zà-ÿ\s]{2,40}?)"
    r"(?:\s+(?P<qtd2>\d{1,3})\s*(?:unidades?|un|unid|uni)?)?\s*[.!]*\s*$")


def completar_pelo_contexto(texto: str, ultima_do_agente: str, ultima_do_cliente: str = "") -> str:
    """Reescreve, SO para a extracao, o pedido que depende da mensagem anterior.

    Teste de 01/10: a Sandrinha disse "temos Quiche de Frango e Quiche de Carne
    de Sol", a cliente respondeu "de frango 2 unidades" e nada entrou na ficha
    - mais adiante ela recebeu "Itens do pedido" na lista do que faltava. O
    complemento ("de frango") vale para o produto que a mensagem anterior
    nomeou e que termina nele, quando ha um so. A quantidade no fim ("quiche de
    frango 2 unidades") vai para a frente, onde a extracao a procura.
    """
    import facts
    t = " ".join((texto or "").lower().split())
    if not t or len(t) > 80:
        return texto
    # "quero de maracuja" depois de "Os sabores do suco natural sao: ...".
    if ("sabores do suco" in (ultima_do_agente or "").lower() and "suco" not in t
            and not re.search(r"\b(?:n[aã]o|nem|sem)\b", t)):
        from conversation_memory import casar_sabor
        sabor = casar_sabor(t, opcoes_do_suco())
        if sabor:
            q = re.search(r"\b(\d{1,2})\b", t)
            return f"{q.group(1) if q else 1} suco de {sabor.lower()}"
    m = _RX_SO_COMPLEMENTO.match(t)
    if m and ultima_do_agente and (m.group("qtd1") or m.group("qtd2")):
        comp = facts.normalize(m.group("comp"))
        anterior = facts.normalize(ultima_do_agente)
        try:
            nomes = {p["item"] for p in facts.get().get("produtos", [])}
        except Exception:
            nomes = set()
        candidatos = [n for n in nomes
                      if facts.normalize(n) in anterior and facts.normalize(n).endswith(comp)]
        if len(candidatos) == 1:
            return f"{m.group('qtd1') or m.group('qtd2')} {candidatos[0]}"
    # "quero 2 porcoes" depois de "a porcao de 25 de Esfirra de Carne custa R$
    # 30,00": porcao de 25 do produto que acabou de ser citado, se for um so.
    m = _RX_SO_PORCOES.match(t)
    if m and ultima_do_agente:
        bruto = m.group("n")
        n = int(bruto) if bruto.isdigit() else _QTD_POR_EXTENSO.get(bruto, 0)
        anterior = facts.normalize(ultima_do_agente)
        try:
            nomes = {p["item"] for p in facts.get().get("produtos", []) if p.get("preco") is None}
        except Exception:
            nomes = set()
        citados = [x for x in nomes if facts.normalize(x) in anterior]
        if 0 < n <= 40 and len(citados) == 1:
            return f"{n * 25} {citados[0]}"
    # "Quero 50 uni" depois de "Quantas unidades de canudinhos de camarao voce
    # quer?": a quantidade e do produto que a pergunta nomeou (teste do dono,
    # 05/10 - o pedido sumiu e ela perguntou "Qual o seu pedido?").
    m = _RX_SO_QUANTIDADE.match(t)
    # "Queria fazer um pedido de canudinhos de camarao" e depois "Quero 50
    # uni": o produto e o que o CLIENTE citou sem quantidade. Mais firme que
    # ler a pergunta dela, que variou ("Qual a quantidade...?", citando ate o
    # combo) e o pedido sumiu (teste do dono, 06/10).
    if m and ultima_do_cliente and not re.search(r"\d", ultima_do_cliente):
        doc = _forma_de_busca(ultima_do_cliente)
        # "quero risole grande de queijo e presunto" e depois "1": o "grande" no
        # meio deixava so a chave "rissole" e virava o de carne (27/10). O
        # tamanho dito vai junto, senao ela perguntava de novo.
        _grande = " grande" if re.search(r"\bgrande\b", doc) else ""
        doc = " ".join(w for w in doc.split() if w != "grande")
        casados_c = [(chave, canon) for chave, canon in _indice_de_produtos().items()
                     if len(chave) >= 6 and re.search(r"\b" + re.escape(chave) + r"\b", doc)]
        prod_c = {canon for chave, canon in casados_c
                  if not any(chave != outra and chave in outra for outra, _c in casados_c)}
        if len(prod_c) == 1:
            return f"{m.group('qtd')} {prod_c.pop()}{_grande}"
    if m and ultima_do_agente and re.search(r"\bquant\w*\b", ultima_do_agente.lower()):
        anterior = _forma_de_busca(ultima_do_agente)
        casados = [(chave, canon) for chave, canon in _indice_de_produtos().items()
                   if len(chave) >= 6 and re.search(r"\b" + re.escape(chave) + r"\b", anterior)]
        # "canudinho de camarao" contem o apelido "canudinho": fica a chave
        # mais especifica.
        achados = {canon for chave, canon in casados
                   if not any(chave != outra and chave in outra for outra, _c in casados)}
        if len(achados) == 1:
            return f"{m.group('qtd')} {achados.pop()}"
    m = _RX_QTD_NO_FIM.match(t)
    if m and not re.search(r"\b(?:rua|av|avenida|numero|n)\b", m.group("prod")):
        return f"{m.group('qtd')} {m.group('prod').strip()}"
    return texto


# So a quantidade: "50", "quero 50", "Quero 50 uni", "50 unidades".
_RX_SO_QUANTIDADE = re.compile(
    r"^(?:(?:quero|queria|vou\s+querer|pode\s+ser|manda|sao|serao)\s+)?(?P<qtd>\d{1,4})"
    r"\s*(?:un\w*|pcs?|pecas?)?\s*[.!]?$")


def _separar_sabores_sem_virgula(parte: str, de_lote) -> list[str]:
    """'pastel coxinha bolinha' -> ['pastel', 'coxinha', 'bolinha'].

    No teste de 01/10 a cliente escreveu os sabores so com espaco ("pastel
    coxinha bolinha e churros"): o trecho inteiro casava com um sabor so e o
    cento virou 50 coxinha + 50 churros, sem o pastel e a bolinha. Corta antes
    de cada palavra que sozinha ja e um sabor de lote, menos depois de "de"
    ("pastel de carne" continua um sabor). So vale se achar 2 sabores ou mais;
    senao o trecho segue inteiro, como antes.
    """
    palavras = parte.split()
    if len(palavras) < 2:
        return [parte]
    grupos: list[list[str]] = []
    for i, w in enumerate(palavras):
        inicio = (i == 0 or (palavras[i - 1] not in _LIGA_SABOR
                             and len(w) > 3 and de_lote(_produto_conhecido(w))))
        if inicio or not grupos:
            grupos.append([w])
        else:
            grupos[-1].append(w)
    pedacos = [" ".join(g) for g in grupos]
    sabores = {_produto_conhecido(p) for p in pedacos}
    if len(pedacos) >= 2 and all(de_lote(_produto_conhecido(p)) for p in pedacos) \
            and len(sabores) == len(pedacos):
        return pedacos
    return [parte]


def sabores_do_volume(msg_lower: str) -> list[str]:
    """Os sabores de um volume que nao fecha ("cento de X, Y e Z")."""
    quebrados: list = []
    itens_divididos(msg_lower, quebrados)
    return list(quebrados[0][1]) if len(quebrados) == 1 else []


# "200 PASTEL DE CARNE E DE QUEIJO" (teste do dono, 07/10 14h37): um volume so,
# com o tipo dito uma vez e dois sabores - o "e de" sem repetir o pastel e o
# sinal. A leitura comum pegava 200 de carne e o queijo sumia.
_RX_UM_VOLUME_DOIS_SABORES = re.compile(
    r"\b(?P<n>\d{2,3})\s+(?P<tipo>[a-z]{4,})\s+de\s+(?P<a>[a-z]{3,}(?:\s+[a-z]{3,})?)"
    r"\s+e\s+de\s+(?P<b>[a-z]{3,})\b")


def _volume_de_dois_sabores(m: re.Match) -> str:
    """'200 pastel de carne e de queijo' -> '200 salgados pastel de carne e pastel de queijo'.

    So reescreve quando os dois sabores sao produtos conhecidos e diferentes;
    a divisao (e a pergunta, se nao fechar em 25) fica com itens_divididos.
    """
    tipo, a, b = m.group("tipo"), m.group("a"), m.group("b")
    sabor_a = _produto_conhecido(f"{tipo} de {a}")
    sabor_b = _produto_conhecido(f"{tipo} de {b}")
    if not sabor_a or not sabor_b or sabor_a == sabor_b:
        return m.group(0)
    return f"{m.group('n')} salgados {tipo} de {a} e {tipo} de {b}"


_RX_METADE_DO_MESMO = re.compile(
    r"\b(\d{2,3})\s+([a-zà-ÿ]{4,})\s+(?:de\s+)?metade\s+(?:de\s+)?([a-zà-ÿ]{4,})"
    r"\s+(?:e\s+)?metade\s+(?:de\s+)?([a-zà-ÿ]{4,})\b")
_RX_DOIS_SABORES_DO_MESMO = re.compile(
    r"\b(\d{2,3}|meio\s+cento|(?:um|1)\s+cento)\s+(?:de\s+)?([a-zà-ÿ]{4,})\s+(?:de\s+)?([a-zà-ÿ]{4,})"
    r"\s+e\s+(?:de\s+)?([a-zà-ÿ]{4,})\b(?!\s*(?:,|e\s))")


def metade_do_mesmo_salgado(msg_lower: str) -> str:
    """"50 canudos metade camarao metade frango" -> "25 canudos de camarao 25
    canudos de frango". O item sumia do pedido (teste da Thayline, 23/10).

    So reescreve quando os dois nomes existem no cardapio e cada metade fecha
    em 25.
    """
    def _troca(m):
        bruto = re.sub(r"\s+", " ", m.group(1))
        n = {"meio cento": 50, "um cento": 100, "1 cento": 100}.get(bruto) or int(bruto)
        base, s1, s2 = m.group(2), m.group(3), m.group(4)
        if n % 50 or not (_produto_conhecido(f"{base} de {s1}")
                          and _produto_conhecido(f"{base} de {s2}")):
            return m.group(0)
        return f"{n // 2} {base} de {s1} {n // 2} {base} de {s2}"
    msg_lower = _RX_METADE_DO_MESMO.sub(_troca, msg_lower or "")
    # "meio cento de canudos frango e camarao": sem "metade", o camarao sumia ou
    # o pedido inteiro nao era lido (teste do dono, 27/10).
    return _RX_DOIS_SABORES_DO_MESMO.sub(_troca, msg_lower)


def itens_divididos(msg_lower: str, quebrados: list | None = None) -> list[dict]:
    """Volume dividido por igual entre os sabores ditos. [] quando nao ha.

    O balcao pede assim: "1 cento pastel e bolinha" e 50 de cada, "metade
    croquete e metade pastel de carne" e um cento meio a meio. A extracao so
    conhecia "cento de X": a ficha ficava com 100 pasteis e a bolinha sumia
    (amostra do balcao do dono, 28/09). Dois centos iguais na mesma frase somam.

    So divide quando TODOS os sabores do trecho sao conhecidos e a divisao
    fecha em lotes de 25 - senao quem decide e o fluxo normal, que pergunta.
    """
    alvo = " ".join((msg_lower or "").lower().split())
    alvo = _RX_UM_VOLUME_DOIS_SABORES.sub(_volume_de_dois_sabores, alvo)
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
                # "coxinha bolinha e churros", sem virgula: cada palavra e um
                # sabor quando todas sao (17/10).
                # "CHIRROS" digitado errado: a palavra corrigida (17/10).
                _palavras = [w if _produto_conhecido(w) else (_corrigir_grafia(w) or w)
                             for w in parte.split()]
                _cada = [_produto_conhecido(w) for w in _palavras]
                # So separa quando cada palavra e o NOME do produto ("bolinha" ->
                # Bolinha de Queijo), nao um adjetivo: "coxinha frita" casava
                # "frita" com a Mini Saltenha Frita.
                _nome_principal = all(
                    c and _forma_de_busca(c).split()[0].startswith(_forma_de_busca(w)[:4])
                    for w, c in zip(_palavras, _cada))
                # O trecho inteiro ja e um produto ("mini quiche"): nao parte em
                # "mini" + "quiche", que dividia o cento com a Quiche de Frango (23/10).
                _inteira = _forma_de_busca(parte) in _indice_de_produtos()
                if (len(_palavras) > 1 and all(_cada) and len(set(_cada)) == len(_cada)
                        and _nome_principal and not _inteira):
                    partes.extend(_palavras)
                else:
                    partes.append(parte)
        if len(partes) < 2 or not unidades:
            continue
        sabores = [_produto_conhecido(p) or _produto_conhecido(_corrigir_grafia(p) or "")
                   for p in partes]
        if not all(sabores) or len(set(sabores)) != len(sabores):
            continue
        cada = unidades // len(sabores)
        if cada * len(sabores) != unidades or cada % 25:
            # "1 cento de coxinha, churros e bolinha": 100 nao fecha em 3
            # sabores. Quem chama recebe o volume e os sabores para perguntar
            # (acrescentar um sabor ou aumentar uma porcao) em vez de ler
            # "cento de coxinha" e jogar fora os outros dois (teste do dono,
            # 01/10 20h36: o espelho saiu com 100 coxinhas).
            if quebrados is not None and unidades % 25 == 0:
                quebrados.append((unidades, sabores))
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
    r"\b(?P<marca>coca(?:[\s-]*colas?|s)?|refri(?:gerante)?s?|guaran[aá]s?|tuchaua"
    r"|fanta|sprite|pepsi|kuat|soda)\b"
    # O resto para na proxima bebida: "2 coca 2litros 1 fanta laranja e 1
    # guarana" engolia a fanta e o guarana na coca (Thayline, 24/10).
    r"(?P<resto>(?:(?!(?:\b(?:\d{1,2}|uma?|duas|dois|tr[eê]s)\s+)?\b(?:coca|refri|guaran|tuchaua"
    r"|fanta|sprite|pepsi|kuat|soda))[^,.;!?\n]){0,30})",
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
# Alfajor e doce por unidade: "6 unidades de alfajor" nao era item de nada e o
# pedido nunca entrava (Thayline, 23/10).
ALFAJOR = "Alfajor"
_RX_ALFAJOR = re.compile(
    r"(?:\b(?P<qtd>\d{1,2}|uma?|dois|duas|tr[eê]s)\s+(?:unidades?\s+(?:de\s+)?)?)?\balfajor(?:es)?\b",
    re.IGNORECASE)
# "1 suco" nao era item: "suco" esta em _NAO_E_PRODUTO e o resumo saiu sem
# ele (Camyli, 26/10). O cardapio digital tem um suco so.
SUCO = "Suco Natural 300 ml"
_RX_SUCO = re.compile(
    r"(?:\b(?P<qtd>\d{1,2}|uma?|dois|duas|tr[eê]s)\s+)?\bsucos?\b", re.IGNORECASE)


def opcoes_do_suco() -> list[str]:
    """Sabores do suco, do cardapio digital. [] se ilegivel."""
    try:
        import facts
        return next((list(p.get("opcoes") or []) for p in facts.get()["produtos"]
                     if p["item"] == SUCO), [])
    except Exception:
        return []

# "4 bandejas", "duas bandeija de pao pizza", "as 4 banbeijas": a bandeja de
# assados (esfirra e pao pizza, composicao fixa) nao era item e o pedido nunca
# fechou (Thayline, 24/10).
BANDEJA_ASSADOS = "Salgadinhos Assados - Bandeja com 15"
_RX_BANDEJA = re.compile(
    r"(?:\b(?P<qtd>\d{1,2}|uma?|dois|duas|tr[eê]s|quatro|cinco)\s+)?\bba[nm][dbn]?e?i?jas?\b",
    re.IGNORECASE)
_QTD_POR_EXTENSO.update({"quatro": 4, "cinco": 5})

# O unico bolo da fabrica e o de milho. "Bolo" sozinho ("um bolo", resposta a
# oferta) e ele; "bolo" com outro complemento ("pra aniversario", "de
# chocolate", "confeitado") e um bolo que nao existe no cardapio.
_RX_OUTRO_BOLO = re.compile(
    r"\bbolos?\s+(?!de\s+milho\b)(?:de|da|do|pra|para|p/|confeit|personaliz|decorad"
    r"|recheado|tematic|aniversari|festa|casamento|chocolate|cenoura|fuba)")


# "pastel de queijo", "pasteis de frango", "torta de frango": tipo de salgado
# com sabor. Se o cardapio nao tem nada com os dois, o item e fora do cardapio.
_RX_SALGADO_COM_SABOR = re.compile(
    r"\b(past[ea]\w*|coxinh\w*|rissol\w*|esfi\w*|empad\w*|canudinh\w*|enroladinh\w*"
    r"|croquet\w*|bolinh\w*|quibe\w*|kibe\w*|calzone\w*|quiche\w*|bomba\w*|torta\w*"
    # "uma torta SALGADA de frango" passava sem aviso (teste do dono, 11/10).
    r"|pao|paes)\s+(?:salgad[ao]s?\s+|doces?\s+)?(?:de|com)\s+([a-z]+)")

# "coxinha de festa", "pastel de 25": nao e sabor. "O kibe de VOCES leva
# requeijao?" e pergunta, e virou "sabor que nao existe" (11/10).
_NAO_E_SABOR = {"festa", "lanche", "mini", "grande", "cento", "porcao", "unidade",
                "hoje", "amanha", "manha", "tarde", "noite",
                "voces", "voce", "vcs", "agora", "verdade"}


def item_fora_do_cardapio(texto: str) -> str:
    """O salgado com sabor que o cardapio nao tem ("pastel de queijo"), ou "".

    Teste do dono de 04/10: "pastelzinho de queijo" virou Pastel de CARNE no
    espelho, e "pastel de queijo" virou o Rissole de Queijo e Presunto grande a
    R$ 9,00. O dono: item fora do cardapio fixo vai para a equipe verificar a
    disponibilidade do dia, sem negativa seca.
    """
    try:
        import facts
        nomes = [facts.normalize(p["item"]) for p in facts.get()["produtos"]]
    except Exception:
        return ""
    if not nomes:
        return ""
    t = _horario_norm(texto)
    for m in _RX_SALGADO_COM_SABOR.finditer(t):
        base, sabor = m.group(1), m.group(2)
        if sabor in _NAO_E_SABOR or len(sabor) < 4:
            continue
        b, s = base[:5] if base not in ("pao", "paes") else "pao", sabor[:5]
        com_base = [n for n in nomes if any(w.startswith(b) for w in n.split())]
        # "Quibe" e uma palavra so: o sabor dele e implicito ("quibe de carne").
        if any(len(n.split()) == 1 for n in com_base):
            continue
        if not any(any(w.startswith(s) for w in n.split()) for n in com_base):
            return f"{base} de {sabor}"
    # "100 MINI SANDUICHE" (teste do dono, 06/10): o "mini" na frente
    # escondia o produto, e ela respondeu com o texto de congelado.
    for m in list(_RX_QTD_MINI_E_NOME.finditer(t)) + list(_RX_MINI_E_NOME.finditer(t)):
        nome = m.group(1)
        primeira = nome.split()[0]
        if primeira.startswith(_NAO_E_PRODUTO):
            continue
        if _produto_conhecido(nome) or _produto_conhecido(primeira) or sabores_possiveis(primeira):
            continue
        raiz = _forma_de_busca(primeira)[:5]
        if any(any(w.startswith(raiz) for w in n.split()) for n in nomes):
            continue
        return f"mini {nome}"
    # "50 travesseirinhos de carne": quantidade de um produto que o cardapio
    # nao tem. Sumia do pedido sem aviso - o espelho saiu so com as coxinhas
    # (teste do dono, 06/10).
    for m in _RX_QTD_E_NOME.finditer(t):
        nome = m.group(2)
        primeira = nome.split()[0]
        if primeira.startswith(_NAO_E_PRODUTO):
            continue
        # Pedido de produto vem no plural ("50 travesseirinhos") ou com sabor
        # ("1 torta de frango"). Sem isso o numero da casa pegava a palavra
        # seguinte: "RUA SALGADO 2325 CARTAO", "RUA GUANABARA 45 PAGAMENTO"
        # (roteiro 1.8 e 1.17, 06/10).
        if " de " not in nome and not primeira.endswith("s"):
            continue
        if _produto_conhecido(nome) or _produto_conhecido(primeira) or sabores_possiveis(primeira):
            continue
        # "500 SALASICHAS": erro de digitacao de produto que existe (17/10).
        _corr = _corrigir_grafia(primeira)
        # "50 camudo de camarao": "canudo" sozinho e ambiguo (frango ou
        # camarao), o nome inteiro corrigido nao. Ela avisava "Nao temos" e
        # punha o Canudinho de Camarao no pedido (teste de 22/10).
        if _corr and (_produto_conhecido(_corr)
                      or _produto_conhecido(nome.replace(primeira, _corr, 1))):
            continue
        raiz = _forma_de_busca(primeira)[:5]
        if any(any(w.startswith(raiz) for w in n.split()) for n in nomes):
            continue
        return nome
    # "50 salgados folhados", "salgado integral": tipo de salgado que a
    # fabrica nao faz (teste de 15/10 21h01 - listava os sabores).
    _tipo = re.search(r"\bsalgad\w*\s+(folhad\w*|integra\w*|vegan\w*|vegetarian\w*|fit\b|light\b"
                      r"|sem\s+gluten|sem\s+lactose)", t)
    if _tipo:
        return f"salgados {_tipo.group(1)}"
    # "salgado de chocolate": nenhum produto tem o sabor (bateria de 06/10 -
    # em 1 de 3 rodadas ela disse que chamaria a equipe e nao chamou).
    for m in _RX_SALGADO_DE_SABOR.finditer(t):
        sabor = m.group(1)
        if sabor in _NAO_E_SABOR or sabor in _NAO_E_SABOR_DE_SALGADO or len(sabor) < 4:
            continue
        if not any(any(w.startswith(sabor[:5]) for w in n.split()) for n in nomes):
            return f"salgado de {sabor}"
    return ""


_RX_SALGADO_DE_SABOR = re.compile(r"\bsalgad\w*\s+(?:de|com)\s+([a-z]+)")
_RX_QTD_E_NOME = re.compile(r"\b(\d{1,4})\s+([a-z]{5,}(?:\s+de\s+[a-z]{4,})?)")
# "gostaria do mini hamburgue", sem numero (teste de 14/10 20h11).
_RX_MINI_E_NOME = re.compile(r"\bminis?\s+([a-z]{5,}(?:\s+de\s+[a-z]{4,})?)")
_RX_QTD_MINI_E_NOME = re.compile(r"\b\d{1,4}\s+(?:minis?|grandes?)\s+([a-z]{4,}(?:\s+de\s+[a-z]{4,})?)")
# Palavra depois de um numero que nao e produto (comeco da palavra).
_NAO_E_PRODUTO = (
    "salgad", "frit", "assad", "variad", "sortid", "unidad", "reais", "real", "minut", "hora",
    "pessoa", "convid", "cento", "porc", "sabor", "litro", "lata", "mini", "grand", "gram",
    "dias", "misto", "refri", "guaran", "docinh", "brigad", "beijin", "coca", "bolo", "pacot",
    "caixa", "bandej", "bandei", "banbei", "kilo", "quilo", "metro", "numer", "casa", "apart", "bloco", "lote",
    "quadra", "anos", "vezes", "combo", "suco", "pudim", "alfaj", "bairr", "perto", "esquin",
    "frente", "fundos", "lado", "andar", "outr", "pedid", "opcoe", "tipos", "moto", "entreg",
    "semana", "meses", "parcel", "fatia", "pedac", "garraf", "copo", "agua", "graus",
    "pagament", "pago", "pagar", "pix", "cartao", "cartoes", "dinheiro", "debito", "credito",
    "troco", "amanha", "hoje", "agora", "retir", "busca", "avenid", "travessa", "esquina",
    "conjunto", "condomin", "residenc", "setor", "manha", "tarde", "noite", "horas", "vezes",
    "pessoas", "filhos", "irmaos", "amigos", "clientes", "funcionar", "alunos", "criancas",
    "pessa", "pesso", "convidad")
# "salgados de aniversario", "salgado de forno": ocasiao ou preparo, nao sabor.
_NAO_E_SABOR_DE_SALGADO = {"aniversario", "casamento", "evento", "eventos", "forno",
                           "fritura", "qualidade", "verdade", "voces", "vcs", "tamanho",
                           "sabor", "sabores", "todos", "tipos", "tipo"}


def _horario_norm(texto: str) -> str:
    import horario as _horario
    return _horario._norm(texto or "")


def bolo_fora_do_cardapio(texto: str) -> bool:
    import horario as _horario
    return bool(_RX_OUTRO_BOLO.search(_horario._norm(texto or "")))


# Palavra que basta para escolher o tamanho quando ela pergunta ("lata").
PALAVRAS_DA_BEBIDA = {
    BEBIDA_LATA: ("lata", "latinha"),
    BEBIDA_600: ("600",),
    # "guarana antartica" e sabor do refrigerante comum: virava 2 Tuchauas (24/10).
    BEBIDA_TUCHAUA: ("tuchaua", r"guarana(?!\s+ant)"),
    BEBIDA_2L: (r"2\s*l(?:t|ts|itros?)?\b", r"dois\s+litros"),
}


def bebidas_citadas(msg_lower: str) -> tuple[list[dict], list[dict]]:
    """(itens, pendentes) de bebida na mensagem.

    O tamanho decide o produto: lata, 600ml, 2 litros. Guarana e sempre o
    Tuchaua 2L, o unico que a fabrica vende. Refrigerante sem tamanho nao e
    chutado: volta como pendencia, para ela perguntar qual.
    """
    itens, pendentes = [], []
    tamanho_dito = ""
    qtd_herdada = 0
    # "duas cocas" e, na mensagem seguinte, "2 litros": o buffer junta as duas
    # com quebra de linha e o tamanho se perdia (24/10).
    msg_lower = re.sub(r"\s*\n\s*(?=(?:de\s+)?(?:2\s*l|dois\s+litros|lat(?:a|inha)|600))", " ",
                       msg_lower or "")
    for m in _RX_BEBIDA.finditer(msg_lower or ""):
        # "sem refri", "nao quero refrigerante": e assim que se RECUSA a oferta.
        if re.search(r"\b(?:sem|n[aã]o|nada\s+de|nem)\b[^,.;!?\n]{0,18}$",
                     (msg_lower or "")[:m.start()]):
            continue
        bruto_qtd = (m.group("qtd") or "").lower()
        qtd = int(bruto_qtd) if bruto_qtd.isdigit() else _QTD_POR_EXTENSO.get(bruto_qtd, qtd_herdada or 1)
        herdou, qtd_herdada = bool(qtd_herdada), 0
        if not 0 < qtd <= 20:
            continue
        # "3 cocas": sem o plural a marca nao casa com o sabor (23/10).
        marca = re.sub(r"^(coca(?:[\s-]*cola)?)s$", r"\1", m.group("marca").lower())
        resto = (m.group("resto") or "").lower()
        deduzido = False
        if marca.startswith("refri"):
            # "refrigerante parecis" e produto proprio, lido pelo nome.
            if re.match(r"\s*parecis\b", resto):
                continue
            # "1 refrigerante 600ml de guarana": a marca que vem logo depois e o
            # sabor deste refrigerante. Saiam dois, um sem sabor (26/10).
            if re.match(r"\s*(?:de\s+|da\s+)?(?:coca|guaran|fanta|sprite|pepsi|kuat|soda)",
                        (msg_lower or "")[m.end():]):
                if re.search(r"\blat(?:a|inha)s?\b", resto):
                    tamanho_dito = BEBIDA_LATA
                elif re.search(r"\b600", resto):
                    tamanho_dito = BEBIDA_600
                elif re.search(r"\b2\s*l\b|\b2\s*lts?\b|\b(?:2|dois)\s*litros?\b", resto):
                    tamanho_dito = BEBIDA_2L
                qtd_herdada = qtd
                continue
        # Guarana Antartica e sabor do refrigerante comum (lata, 600, 2L); o
        # guarana sozinho continua sendo o Tuchaua (24/10).
        antartica = marca.startswith("guaran") and (bool(re.search(r"\bant[aá]r", resto))
                                                    or (herdou and bool(tamanho_dito)))
        if re.search(r"\blat(?:a|inha)s?\b", resto):
            nome = BEBIDA_LATA
        elif re.search(r"\b600", resto):
            nome = BEBIDA_600
        elif marca.startswith(("guaran", "tuchaua")) and not antartica:
            nome = BEBIDA_TUCHAUA
        elif re.search(r"\b2\s*l\b|\b2\s*lts?\b|\b(?:2|dois)\s*litros?\b", resto):
            nome = BEBIDA_2L
        elif tamanho_dito:
            # "2 coca 2litros 1 fanta laranja": o tamanho dito vale para a
            # bebida seguinte da mesma mensagem.
            nome = tamanho_dito
        else:
            _marca = "" if marca.startswith("refri") else ("guarana antartica" if antartica else marca)
            _ops = _opcoes_de_refrigerante()
            if _marca:
                _ops = [o for o in _ops if o != BEBIDA_TUCHAUA] or _ops
                # "fanta uva" so existe no 2L: oferecia lata e 600 sem Fanta (26/10).
                _ops, _sabor_dito = _tamanhos_com_o_sabor(f"{_marca} {resto}", _ops)
                # "fanta uva" inteiro: a escolha "2 litros" casa o sabor por aqui.
                _marca = _sabor_dito or _marca
            if not _marca or len(_ops) != 1:
                pendentes.append({"qtd": qtd, "termo": "refrigerante", "opcoes": _ops,
                                  "marca": _marca})
                continue
            nome = _ops[0]
            deduzido = True
        if nome != BEBIDA_TUCHAUA and not deduzido and not marca.startswith("refri"):
            # "fanta uva lata": a lata nao tem Fanta uva e saia Fanta laranja.
            # Pergunta com os tamanhos que tem o sabor; trocar sozinha muda o preco (27/10).
            _todos = [o for o in _opcoes_de_refrigerante() if o != BEBIDA_TUCHAUA]
            _com, _sabor_dito = _tamanhos_com_o_sabor(f"{marca} {resto}", _todos)
            if nome in _todos and nome not in _com:
                # "pedido": no "nao" ao "so temos em 2L", ela oferece os sabores da lata.
                # "dito": a propria mensagem tem "lata" e nao pode responder a pergunta.
                pendentes.append({"qtd": qtd, "termo": "refrigerante", "opcoes": _com,
                                  "marca": _sabor_dito or marca, "pedido": nome,
                                  "dito": " ".join(msg_lower.split())})
                continue
        if nome != BEBIDA_TUCHAUA and not deduzido:
            tamanho_dito = nome
        sabor = ""
        if nome != BEBIDA_TUCHAUA and not marca.startswith("refri"):
            try:
                import cardapio_catalogo
                from conversation_memory import casar_sabor
                sabor = casar_sabor(f"{marca} {resto}", cardapio_catalogo.opcoes_do_item(nome) or [])
            except Exception:
                sabor = ""
        # Coca e Fanta de 2 litros sao duas linhas: deduplicar so pelo nome
        # juntava tudo numa linha so (24/10).
        if any(i["name"] == nome and i.get("sabor", "") == sabor for i in itens):
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
        novo = {"name": nome, "quantity": qtd, "price": oficial["preco_unitario"]}
        if sabor:
            novo["sabor"] = sabor
        itens.append(novo)

    # O bolo da oferta ("vai um Bolo de Milho?" -> "Um bolo") tambem sumia do
    # espelho: nao estava no indice de produtos (teste do dono, 29/09 15h24).
    # Entra pelo mesmo caminho da bebida, que SOMA ao pedido.
    for m in _RX_BOLO.finditer(msg_lower or ""):
        if re.search(r"\b(?:sem|n[aã]o|nada\s+de|nem)\b[^,.;!?\n]{0,18}$",
                     (msg_lower or "")[:m.start()]):
            continue
        # "bolo pra aniversario", "bolo de chocolate": outro bolo, que a
        # fabrica nao faz. Virava Bolo de Milho no espelho (teste de 01/10).
        if bolo_fora_do_cardapio(m.group(0) + (msg_lower or "")[m.end():]):
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
    for m in _RX_ALFAJOR.finditer(msg_lower or ""):
        if re.search(r"\b(?:sem|n[aã]o|nada\s+de|nem)\b[^,.;!?\n]{0,18}$",
                     (msg_lower or "")[:m.start()]):
            continue
        bruto_qtd = (m.group("qtd") or "").lower()
        qtd = int(bruto_qtd) if bruto_qtd.isdigit() else _QTD_POR_EXTENSO.get(bruto_qtd, 1)
        if not 0 < qtd <= 50 or any(i["name"] == ALFAJOR for i in itens):
            continue
        try:
            import facts
            oficial = facts.resolve_unit_price(ALFAJOR, qtd)
        except Exception:
            oficial = None
        if oficial:
            itens.append({"name": ALFAJOR, "quantity": qtd, "price": oficial["preco_unitario"]})
    for m in _RX_SUCO.finditer(msg_lower or ""):
        # "tem suco de que?" e pergunta, nao pedido.
        if re.search(r"\b(?:sem|n[aã]o|nada\s+de|nem|tem|tens|vende\w*|faz\w*|quais|qual|sabor\w*)\b[^,.;!?\n]{0,18}$",
                     (msg_lower or "")[:m.start()]):
            continue
        bruto_qtd = (m.group("qtd") or "").lower()
        qtd = int(bruto_qtd) if bruto_qtd.isdigit() else _QTD_POR_EXTENSO.get(bruto_qtd, 1)
        if not 0 < qtd <= 20 or any(i["name"] == SUCO for i in itens):
            continue
        # O sabor vem das opcoes do cardapio. Sabor que nao existe ("suco de
        # laranja") nao entra: a resposta dos sabores do suco diz quais tem.
        dito = re.match(r"\s+(?:natural\s+)?(?:\d{3}\s*ml\s+)?(de\s+)?([a-zà-ÿ]{3,}(?:\s+[a-zà-ÿ/]+)?)",
                        (msg_lower or "")[m.end():])
        sabor = ""
        if dito and not re.match(r"(?:que|qual|quais|com|pra|para|tamb)\b", dito.group(2)):
            from conversation_memory import casar_sabor
            sabor = casar_sabor(dito.group(2), opcoes_do_suco())
            if not sabor and dito.group(1):
                continue
        try:
            import facts
            oficial = facts.resolve_unit_price(SUCO, qtd)
        except Exception:
            oficial = None
        if oficial:
            novo = {"name": SUCO, "quantity": qtd, "price": oficial["preco_unitario"]}
            if sabor:
                novo["sabor"] = sabor
            itens.append(novo)
    for m in _RX_BANDEJA.finditer(msg_lower or ""):
        if re.search(r"\b(?:sem|n[aã]o|nada\s+de|nem)\b[^,.;!?\n]{0,18}$",
                     (msg_lower or "")[:m.start()]):
            continue
        # A bandeja de docinhos e do caminho dos doces.
        # "1 bandeja mista" tambem: a de assados tem composicao fixa (27/10).
        if re.search(r"\b(?:brigad|docinh|beijinh|doce|mist)", (msg_lower or "")[m.end():m.end() + 25]):
            continue
        bruto_qtd = (m.group("qtd") or "").lower()
        qtd = int(bruto_qtd) if bruto_qtd.isdigit() else _QTD_POR_EXTENSO.get(bruto_qtd, 1)
        if not 0 < qtd <= 20 or any(i["name"] == BANDEJA_ASSADOS for i in itens):
            continue
        try:
            import facts
            oficial = facts.resolve_unit_price(BANDEJA_ASSADOS, qtd)
        except Exception:
            oficial = None
        if oficial:
            itens.append({"name": BANDEJA_ASSADOS, "quantity": qtd, "price": oficial["preco_unitario"]})
    return itens, pendentes[:1]


# Doces: so existem tres formatos de brigadeiro na planilha. Ate 02/10 nada
# disso era extraido - o pedido de doce ficava so na conversa, o modelo
# inventava formato ("1 porcao de 25 brigadeiros") e o espelho do codigo saia
# sem o doce (teste do dono, 01/10 20h02: "PODE SER" e o brigadeiro sumiu).
DOCE_BANDEJA = "Bandeja de Brigadeiros (16 un)"
DOCE_MEIO_CENTO = "Meio Cento de Brigadeiros (50 un)"
DOCE_CENTO = "Cento de Brigadeiros (100 un)"
_RX_DOCE = re.compile(r"\b(?:brigadeir\w*|docinh\w*|beijinh\w*|doces)\b")
_RX_DOCE_FORA = re.compile(r"\b(?:beijinh\w*|mist[oa]s?)\b")


def _qtd_antes(texto: str, fim: int) -> int:
    m = re.search(r"(\d{1,2}|uma?|duas|dois|tr[eê]s)\s+$", texto[:fim])
    if not m:
        return 1
    bruto = m.group(1)
    return int(bruto) if bruto.isdigit() else _QTD_POR_EXTENSO.get(bruto, 1)


def formato_do_doce(texto: str) -> tuple[str, int]:
    """(formato, quantidade) dito para o brigadeiro. ('', 0) sem formato."""
    t = " ".join((texto or "").lower().split())
    m = re.search(r"\bbandejas?\b|\b16\b", t)
    if m:
        return DOCE_BANDEJA, _qtd_antes(t, m.start())
    m = re.search(r"\bmeio\s+cento\b|\b50\b|\bmeio\b", t)
    if m:
        return DOCE_MEIO_CENTO, _qtd_antes(t, m.start())
    m = re.search(r"\b(?:centos?|100)\b", t)
    if m and not re.search(r"\bcento\s+e\s+meio\b", t):
        return DOCE_CENTO, _qtd_antes(t, m.start())
    return "", 0


def opcoes_de_doce() -> list[str]:
    """Formatos de brigadeiro disponiveis hoje na planilha."""
    try:
        import facts
        saida = []
        for nome in (DOCE_BANDEJA, DOCE_MEIO_CENTO, DOCE_CENTO):
            if facts.resolve_unit_price(nome, 1):
                saida.append(nome)
        return saida
    except Exception:
        return [DOCE_BANDEJA, DOCE_MEIO_CENTO, DOCE_CENTO]


# Abreviacoes do cliente: "50 COX E 25 SAL E 25 BOLINHA" (16/10).
_ABREVIACOES = {"sal": "salsicha", "cox": "coxinha", "bol": "bolinha", "pas": "pastel",
                "esf": "esfirra", "emp": "empadinha", "enr": "enroladinho"}


# "3 combos de camarao", "1 combo mix frango", "2 combo 15 empadinhas".
_RX_COMBO = re.compile(
    r"(?:\b(?P<qtd>\d{1,2}|uma?|dois|duas|tr[eê]s)\s+)?\bcombos?\b"
    # O nome para no " e " do proximo item: "3 combos de camarao e 1 cento
    # frito" engolia o cento (15/10).
    r"(?P<resto>(?:\s+(?:de|do|da|com|mix|\d{1,3}|\+))*(?:\s+(?!e\b)[a-zà-ú]+){1,2})")
# A bebida que faz parte do nome do combo ("+ coca-cola lata", "com refri 1l").
_RX_BEBIDA_DO_COMBO = re.compile(
    r"\s*(?:(?:\+|com)\s*)?(?:uma?\s+|1\s+)?(?:coca(?:[\s-]*cola)?|refri\w*)"
    r"(?:\s+(?:de\s+)?(?:lata|latinha|1\s*l\w*))?")
_COMBO_POR_PALAVRA = (
    (r"camar", "Combo Mix Camarão"),
    (r"empad", "Combo 15 Empadinhas + Coca-Cola Lata"),
    (r"canud", "Combo 15 Canudinhos + Coca-Cola Lata"),
    (r"frit|salgadinh|\b50\b", "Combo 50 Salgadinhos Fritos + Refrigerante 1L"),
    (r"frango", "Combo Mix Frango"),
)


def combos_citados(msg_lower: str) -> tuple[list[dict], str]:
    """(combos pedidos, mensagem sem eles). O combo e um produto do cardapio
    vendido por unidade; o nome dele tem numero dentro ("15 empadinhas") que
    nao pode virar item avulso."""
    try:
        import facts
        nomes = {p["item"] for p in facts.get()["produtos"]
                 if facts.normalize(p["categoria"]) == "combos" and p.get("disponivel", True)}
    except Exception:
        return [], msg_lower
    if not nomes:
        return [], msg_lower
    achados, resto_msg = [], msg_lower
    for m in _RX_COMBO.finditer(msg_lower):
        resto = m.group("resto") or ""
        nome = next((n for rx, n in _COMBO_POR_PALAVRA if re.search(rx, resto) and n in nomes), "")
        if not nome:
            continue
        bruto = (m.group("qtd") or "1").lower()
        qtd = int(bruto) if bruto.isdigit() else _QTD_POR_EXTENSO.get(bruto, 1)
        try:
            import facts
            oficial = facts.resolve_unit_price(nome, qtd)
        except Exception:
            oficial = None
        if not any(c["name"] == nome for c in achados):
            achados.append({"name": nome, "quantity": qtd,
                            "price": oficial["preco_unitario"] if oficial else 0.0})
        # "combo 15 empadinhas + coca-cola lata": a coca e a do combo. Ficava
        # na mensagem e virava uma lata a mais, cobrada a parte (26/10).
        da_bebida = (_RX_BEBIDA_DO_COMBO.match(msg_lower[m.end():])
                     if re.search(r"coca|refri", nome.lower()) else None)
        resto_msg = resto_msg.replace(m.group(0) + (da_bebida.group(0) if da_bebida else ""), " ")
    # "combo mix de camarao e o outro de frango": o segundo combo vinha sem a
    # palavra "combo" e sumia; o total oscilou entre R$ 110 e R$ 210 (24/10).
    if achados:
        for m in re.finditer(r"\b(?:o\s+|um\s+)?outro\s+(?:combo\s+)?(?:mix\s+)?(?:de\s+)?([a-zà-ú]+)", msg_lower):
            nome = next((n for rx, n in _COMBO_POR_PALAVRA if re.search(rx, m.group(1)) and n in nomes), "")
            if nome and not any(c["name"] == nome for c in achados):
                try:
                    import facts
                    oficial = facts.resolve_unit_price(nome, 1)
                except Exception:
                    oficial = None
                achados.append({"name": nome, "quantity": 1,
                                "price": oficial["preco_unitario"] if oficial else 0.0})
                resto_msg = resto_msg.replace(m.group(0), " ")
    return achados, resto_msg


_PALAVRAS_DE_DOCE = ("brigadeiro", "brigadeiros", "beijinho", "beijinhos", "docinho", "docinhos")


def corrigir_digitacao_do_doce(texto: str) -> str:
    """"brigaeiro", "brigadeior", "beijnho" -> a palavra certa do doce."""
    import difflib

    def _troca(m):
        w = m.group(0)
        if len(w) < 7 or w in _PALAVRAS_DE_DOCE or w[0] not in "bd":
            return w
        perto = difflib.get_close_matches(w, _PALAVRAS_DE_DOCE, n=1, cutoff=0.82)
        return perto[0] if perto else w
    return re.sub(r"[a-záàãâéêíóôõúç]+", _troca, texto or "")


def doces_citados(msg_lower: str) -> tuple[list[dict], list[dict]]:
    """(itens, pendentes) de brigadeiro na mensagem."""
    t = " ".join((msg_lower or "").lower().split())
    # "2 bandejas mistas" nao diz "brigadeiro" mas e a bandeja de docinhos (27/10).
    m = _RX_DOCE.search(t) or re.search(r"\bbandejas?\s+(?:de\s+)?mist", t)
    if not m or re.search(r"\bdoce\s+de\s+leite\b", t[m.start():m.end() + 12]):
        return [], []
    if re.search(r"\b(?:sem|n[aã]o|nada\s+de|nem)\b[^,.;!?\n]{0,18}$", t[:m.start()]):
        return [], []
    # Beijinho e mista existem no cardapio digital como SABOR do doce (15/10):
    # deixaram de ser "em falta". O sabor vai no item (sabor_do_doce).
    fora = False
    # O formato e o do trecho em que o doce aparece: em "cento e meio de
    # coxinha e brigadeiro" o "meio" e da coxinha.
    # Cada trecho com doce e um pedido: "meio cento de brigadeiro, um cento de
    # brigadeiro" perdia o cento (varredura de 27/10).
    # "1 bandeja de beijinho e 1 bandeja mista": o segundo trecho nao diz
    # "brigadeiro" e sumia, e o "mista" valia para tudo (teste de 27/10).
    trechos = [s for s in re.split(r",|;|\s+e\s+", t)
               if _RX_DOCE.search(s) or (re.search(r"\bbandejas?\b", s)
                                         and not re.search(r"salgad|assad|esfirr|pizza", s))] or [t]
    lidos = [formato_do_doce(s) for s in trechos]
    # Com mais de um trecho, o sabor e o de cada trecho; com um, quem decide
    # e sabor_do_doce na mensagem inteira, como antes.
    sabores = ([sabor_do_doce(s, f) for s, (f, _q) in zip(trechos, lidos)]
               if len(trechos) > 1 else [None] * len(lidos))
    # "meio cento de brigadeiro e meio cento de beijinho" sao 100 docinhos: o
    # cento misto (beijinho puro so na bandeja). Virava UM meio cento (teste de
    # 22/10).
    if (re.search(r"\bmeio\s+cento\s+(?:de\s+|do\s+)?brigad", t)
            and re.search(r"\bmeio\s+cento\s+(?:de\s+|do\s+)?beijinh", t)):
        lidos = [(DOCE_CENTO, 1)]
        sabores = [None]
    formato, qtd = lidos[0]
    itens: list[dict] = []
    for (formato_i, qtd_i), sabor_i in zip(lidos, sabores):
        if not formato_i or fora:
            continue
        try:
            import facts
            oficial = facts.resolve_unit_price(formato_i, qtd_i)
        except Exception:
            oficial = None
        if not oficial:
            continue
        igual = next((i for i in itens if i["name"] == formato_i
                      and i.get("sabor") == sabor_i), None)
        if igual:
            igual["quantity"] += qtd_i
        else:
            itens.append({"name": formato_i, "quantity": qtd_i,
                          "price": oficial["preco_unitario"]}
                         | ({"sabor": sabor_i} if sabor_i is not None else {}))
    if itens:
        return itens, []
    pendente = {"qtd": max(qtd, 1), "termo": "docinho", "opcoes": opcoes_de_doce()}
    if fora:
        pendente["em_falta"] = "Beijinho"
    return [], [pendente]


_RX_INTENCAO_DE_PEDIDO = re.compile(
    r"\b(?:quero|queria|quer[oi]a|manda|mande|vou querer|vou levar|pode ser|coloca|coloque"
    r"|adiciona|acrescenta|inclui|me ve|me v[eê]|traz|pode mandar|bota|poe|sim|aceito)\b")


def e_pergunta_sem_pedido(msg_lower: str) -> bool:
    """Pergunta sobre produto, sem nenhuma palavra de pedido."""
    t = " ".join((msg_lower or "").lower().split())
    if "?" not in t:
        return False
    return not _RX_INTENCAO_DE_PEDIDO.search(t)


def _tamanhos_com_o_sabor(dito: str, tamanhos: list[str]) -> tuple[list[str], str]:
    """(tamanhos com sabor que tem todas as palavras ditas, essas palavras: "fanta uva")."""
    import cardapio_catalogo
    from horario import _norm
    por = {t: [_norm(s).split() for s in (cardapio_catalogo.opcoes_do_item(t) or [])] for t in tamanhos}
    vocab = {w for ss in por.values() for s in ss for w in s}
    # "cola" nao separa sabor: a lata e "Coca", o 600 e "Coca cola".
    palavras = list(dict.fromkeys(w for w in _norm(dito).split() if w in vocab and w != "cola"))
    if not palavras:
        return tamanhos, ""
    return ([t for t in tamanhos if any(all(w in s for w in palavras) for s in por[t])] or tamanhos,
            " ".join(palavras))


def _opcoes_de_refrigerante() -> list[str]:
    """Tamanhos que a fabrica tem HOJE (esgotado nao entra na pergunta)."""
    opcoes = []
    # O 2L comum entra quando o cardapio tem (11/10: "UMA COCA" -> "2 LT" nao
    # era opcao, e a pergunta se repetiu). Por ultimo: "guarana 2 litros" casa
    # antes com o Tuchaua.
    for nome in (BEBIDA_LATA, BEBIDA_600, BEBIDA_TUCHAUA, BEBIDA_2L):
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
        # "meio cento de brigadeiro misto" e DOCE: virava Misto de Queijo e
        # Presunto (15/10). Doce e lido por doces_citados.
        if re.search(r"brigad|beijinh|docinh|\bdoces?\b", produto):
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


# Numero logo depois de "as"/"pras"/"para as" e hora, mesmo sem "horas" depois:
# "pra entrega amanha as nove." perdia o horario inteiro, e sem horario o
# aviso do intervalo oferecia "adiantar para as 14h" a quem pediu para amanha
# (teste por voz, 30/09 12h51).
_RX_HORA_ANTES_DO_NUMERO = re.compile(
    r"(?:\b[aà]s|\bpras?\s+as|\bpras|\bpara\s+as|\bate\s+as|\bate\s+[aà]s)\s+$",
    re.IGNORECASE)


def _vira_hora(texto: str, inicio: int, valor: int) -> str:
    """'9h' quando o numero e hora pelo contexto; '' quando nao e."""
    if 1 <= valor <= 23 and _RX_HORA_ANTES_DO_NUMERO.search(texto[:inicio]):
        return f"{valor}h"
    return ""


def numeros_por_extenso(texto: str) -> str:
    """'cinquenta coxinhas as oito horas' -> '50 coxinhas as 8 horas'."""
    def _valor(m) -> int:
        if m.group("sozinha"):
            return _UNIDADES_EXTENSO[m.group("sozinha").lower()]
        valor = _DEZENAS_EXTENSO[m.group("dezena").lower()]
        if m.group("unidade"):
            valor += _UNIDADES_EXTENSO[m.group("unidade").lower()]
        return valor

    def _troca(m):
        resto = texto[m.end():]
        seguinte = _RX_DEPOIS_DO_NUMERO.match(resto)
        if not seguinte or not re.match(
                r"(?:(?:de|da|do)\s+)?(?:horas?|h\b|da\s+|salgad|unidade|un\b|porc|cento)",
                seguinte.group(0).strip().lower()):
            # "as nove." / "as nove e meia": hora pelo que vem ANTES.
            hora = _vira_hora(texto, m.start(), _valor(m))
            if hora:
                return hora
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

    # "uma coxinha", "uma fatia de pudim": a quiche, a coxinha e o pudim sumiam
    # do resumo (Camyli, 26/10). "Um"/"uma" so viram 1 antes de produto, para
    # "uma pergunta" e "um pouco" ficarem como estao.
    def _um(m):
        w = m.group(1).lower()
        if w.startswith(("fatia", "porc", "cento", "unidade")) or _produto_conhecido(w):
            return "1"
        return m.group(0)
    texto = _RX_UM_UMA.sub(_um, texto or "")
    return _RX_NUMERO_EXTENSO.sub(_troca, texto)


# "uma coxinha im risoles de carne": o "im" e o "um" digitado errado; virava
# um produto so e a coxinha sumia do resumo (teste de 27/10 18h04).
_RX_UM_UMA = re.compile(r"\b(?:um[a]?|im)\b(?=\s+(?:(?:de|da|do)\s+)?([a-zà-ÿ]+))", re.IGNORECASE)


def _extract_data_from_message(message: str, state: "ConversationState") -> dict:
    """Extrai dados estruturados da mensagem do cliente (nome, itens, modalidade, endereco, horario, pagamento).

    Retorna dict com os dados extraidos que devem ser aplicados ao state.
    """
    message = numeros_por_extenso(message or "")
    # CEP: consulta a rua e o bairro, e os digitos saem do texto - "76804-120"
    # nao pode virar 120 salgados nem o numero da casa (18/10).
    import cep as _cep_mod
    _cep_dito = _cep_mod.cep_na_mensagem(message)
    _dados_cep = None
    if _cep_dito:
        _dados_cep = _cep_mod.buscar(_cep_dito)
        if not _cep_mod.e_da_cidade(_dados_cep):
            _dados_cep = None
        message = _cep_mod.tirar_cep(message)
        extracted_cep = _cep_dito
    else:
        extracted_cep = ""
    msg_lower = message.lower().strip()
    # "um centoQu frito misto de coxinha e bolinha" (teste do dono, 05/10):
    # letra a mais grudada no "cento" apagava o pedido inteiro.
    msg_lower = re.sub(r"\bcento(?!s\b)[a-z]{1,2}\b", "cento", msg_lower)
    # "DOI CENTO DE SALGADOS" (dois sem o s) virava 1 cento (teste de 06/10).
    msg_lower = re.sub(r"\bdoi\s+(centos?)\b", r"dois \1", msg_lower)
    # "QURO UM BRIGAEIRO" (teste do dono, 17/10): o doce sumia do pedido.
    msg_lower = corrigir_digitacao_do_doce(msg_lower)
    # "enroladinhos de salsicha com catupiry" e o ASSADO: o "com" cortava o
    # nome e sobrava o frito (teste do dono, 08/10 12h39).
    msg_lower = re.sub(r"\benroladinhos?\s+(?:de\s+salsicha\s+)?com\s+catupiry\b",
                       "enroladinho assado", msg_lower)
    # "um centro de canudinho" e um CENTO: o pedido sumia e "Centro" virava o
    # bairro (teste do dono, 10/10 08h39). So com quantidade antes e "de"
    # depois - "entrega no centro" continua sendo o bairro.
    # "1 sento de coxinha" (digitacao) tambem: virava 1 coxinha grande (26/10).
    # "neio cento de pastel" (digitacao) virava 100 pasteis no resumo (27/10).
    msg_lower = re.sub(r"\b(?:neio|meiu|mei)\s+(?=(?:cento|centro|sento)\b)", "meio ", msg_lower)
    msg_lower = re.sub(r"\b(um|uma|meio|dois|duas|tres|\d+)\s+(?:centros?|sentos?)\s+(?=de\s)",
                       lambda m: f"{m.group(1)} {'centos' if m.group(1) not in ('um', 'uma', 'meio', '1') else 'cento'} ",
                       msg_lower)
    # "6 porcoes de coxinha" sao 150 coxinhas, nao 6 (teste do dono, 08/10
    # 19h45). "1 porcao de 25 salgadinhos" ja diz as unidades: fica como esta.
    msg_lower = re.sub(
        r"\b(\d{1,2}|uma|duas|tres|três)\s+por[cç](?:[aã]o|[oõ]es)\s+de\s+(?!\d)",
        lambda m: f"{25 * {'uma': 1, 'duas': 2, 'tres': 3, 'três': 3}.get(m.group(1), int(m.group(1)) if m.group(1).isdigit() else 1)} ",
        msg_lower)
    msg_lower = metade_do_mesmo_salgado(msg_lower)
    extracted = {}
    if extracted_cep:
        extracted["cep"] = extracted_cep

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
        # O nome da rua nao e data: "Rua Quinta do Sol 45" mudou o pedido de
        # hoje para quinta (teste no WhatsApp, 23/10). Sai so o nome, ate o
        # numero da casa; o que vem depois dele continua valendo.
        data = detectar_data(_RX_NOME_DE_RUA.sub(" ", message or ""), _now)
        # "Pra agora" e pedido de hoje (BLOCO 0: "agora/ja" registra a data de
        # hoje e o horario atual) - sem isto a cliente dizia "pra agora" e
        # ainda recebia "Data do pedido" na lista do que faltava.
        if not data and extracted.get("horario") == _horario.HORARIO_IMEDIATO:
            data = (_now or _horario.agora()).date()
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
            # "Rua Wilmam maia\n5994 Igarape 15/11/2026": a comanda saiu com a
            # data e o bairro no nome da rua (teste de 14/10).
            addr = " ".join(addr.split())
            addr = re.split(r"\s\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\s(?:as|às)\s+\d",
                            addr, maxsplit=1, flags=re.IGNORECASE)[0].strip(" ,-")
            # "rua wilamam maia 5994 dia 30 as 18:30": o "dia 30" ia para a
            # comanda (teste de 22/10). So DEPOIS do numero da casa: "Rua Dia
            # 15 de Maio" e "Av. Domingos" sao nome de rua.
            _num_casa = re.search(r"\d+", addr)
            if _num_casa:
                _cauda = re.split(r"\s(?:para\s+o\s+|pro\s+|no\s+)?dia\s+\d{1,2}\b"
                                  r"|\s(?:pra|para)\s+(?:agora|hoje|amanh[aã])\b|\s(?:hoje|amanh[aã])\b"
                                  r"|\s(?:segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo)\b",
                                  addr[_num_casa.end():], maxsplit=1, flags=re.IGNORECASE)[0]
                addr = (addr[:_num_casa.end()] + _cauda).strip(" ,-")
            _bairro_msg = detectar_bairro(message)
            import facts as _fx_end
            if _bairro_msg:
                _rx_b = re.compile(r"[\s,-]+" + re.escape(_fx_end.normalize(_bairro_msg)) + r"\s*$")
                if _rx_b.search(_fx_end.normalize(addr)):
                    addr = addr[:len(addr) - len(_rx_b.search(_fx_end.normalize(addr)).group(0))].strip(" ,-")
            extracted["endereco"] = addr
        elif _RUA_DEPOIS_DE_ENTREGA.search(message):
            # "PARA ENTREGA NA CALAMA 2025 APONIA": sem "Rua" nada era
            # guardado e ela pedia "endereco (rua e numero)" que o cliente ja
            # tinha mandado na primeira mensagem (teste do dono, 28/09 20h03).
            m = _RUA_DEPOIS_DE_ENTREGA.search(message)
            nome = " ".join(m.group("nome").split())
            if not _NAO_E_RUA.search(nome.lower()):
                extracted["endereco"] = f"{nome} {m.group('numero')}"
        elif ("entrega" in (state.modality, extracted.get("modality"))
              and _RUA_NO_INICIO.match(message)
              and not _NAO_E_RUA.search(_RUA_NO_INICIO.match(message).group("nome").lower())
              and not _NAO_E_NOME_DE_RUA.search(_RUA_NO_INICIO.match(message).group("nome").lower())):
            # Resposta direta a "qual a rua e o numero?" com o resto junto:
            # "NA CALAMA 2025 APONIA PAGAMENTO NO CARTAO". O bairro e o
            # pagamento entravam e a rua nao, e ela pedia "Endereco (rua e
            # numero)" de novo (teste de 30/09 12h56). Guarda o trecho como o
            # cliente escreveu ("SALGADO FILHO,2233").
            m = _RUA_NO_INICIO.match(message)
            extracted["endereco"] = message[m.start("nome"):m.end("numero")].strip()
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

    # ── CEP (18/10): rua e bairro pelo CEP; o numero da casa vem depois ─────
    if _dados_cep:
        import facts as _fx_cep
        # Quem manda o CEP quer entrega.
        if not state.modality and not extracted.get("modality"):
            extracted["modality"] = "entrega"
        if not state.endereco and not extracted.get("endereco") and _dados_cep.get("rua"):
            _num_cep = re.search(r"\b(?:n|n[º°o]|numero|num|casa)\s*\.?\s*(\d{1,5}[a-z]?)\b",
                                 _horario_norm(message))
            # "76824252 5994": so o CEP e o numero, sem "casa". O numero se
            # perdia e ela pedia de novo (Thayline, 24/10 15h12).
            _num_cep = _num_cep or re.fullmatch(r"\W*(\d{1,5}[a-z]?)\W*", _horario_norm(message))
            extracted["endereco"] = (f"{_dados_cep['rua']}, {_num_cep.group(1)}" if _num_cep
                                     else _dados_cep["rua"])
        _bairro_cep = _dados_cep.get("bairro") or ""
        _oficial_cep = detectar_bairro(_bairro_cep) or (
            (_fx_cep.lookup_frete(_bairro_cep) or {}).get("bairro", "") if _bairro_cep else "")
        if not state.bairro and _oficial_cep:
            extracted["bairro"] = _oficial_cep
        elif not state.bairro and _bairro_cep:
            # Bairro do CEP fora da tabela de frete: guarda o nome, e quem
            # decide o que fazer e o fluxo de bairro nao encontrado.
            extracted["bairro_do_cep"] = _bairro_cep

    # ── Bairro ──────────────────────────────────────────────────────────────
    if not state.bairro and not extracted.get("bairro"):
        bairro = detectar_bairro(message)
        if bairro:
            extracted["bairro"] = bairro
        else:
            # "no bairro vila esmeralda, rua 7": fora da tabela, a pergunta do
            # bairro diz que nao achou, como no CEP (teste de 27/10 22h21).
            _dito = re.search(r"(?:^|[,;]\s*|\b(?:no|do)\s+)bairro\s+([a-zà-ÿ ]{3,40}?)"
                              r"\s*(?:[,.;!]|$|\b(?:rua|av|avenida|n|numero|quadra|casa)\b)",
                              msg_lower.strip())
            if _dito and "?" not in msg_lower:
                extracted["bairro_do_cep"] = _dito.group(1).strip()

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
        # Combos antes dos itens: "2 combo 15 empadinhas" nao e 15 empadinhas.
        _combos, msg_lower = combos_citados(msg_lower)
        if _combos:
            extracted["combos"] = _combos
        item_pattern = re.findall(
            r'(\d+)\s*(?:unidades?|un\.?|pcs\.?)?\s*(de\s+)?([a-záàãâéêíóôõúç\s]+?)'
            r'(?:\s*(?:[,;.!?]|\be\b|\bpra\b|\bpara\b|\bcom\b|\bno\b|\bna\b|\bem\b'
            r'|\bhoje\b|\bamanh[aã]\b|\bàs\b|\bas\b|\bde\s+r\$|r\$'
            r"|(?![a-z\u00e0-\u00ff\s])|$))",
            # Hora ("7:30", "8h", "18 horas") sai so desta leitura de itens: o
            # minuto "30" virava "30 coxinhas" (teste de 14/10 20h14).
            # "25 BOLINHA RETIRADA PAGAMENTO NO PIX": a palavra do funil
            # encerra o nome - "bolinha retirada" sumia do pedido (17/10).
            re.sub(r"\b(?:retirad[ao]s?|retirar|entregas?|entregar|pagamento|pix|cartao|cartão"
                   r"|dinheiro|buscar)\b", ",",
                   re.sub(r"\b\d{1,2}\s*[:h]\s*\d{2}\b|\b\d{1,2}\s*(?:h|hrs?|horas?)\b", " ", msg_lower))
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
            if product_name.startswith("cento") or re.search(r"\bcentos?\b", product_name):
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
            # "25 bolinha IR 25 coxinhas" (o "e" digitado errado): a bolinha
            # sumia do pedido (teste de 14/10).
            _palavras_nome = product_name.split()
            if not matched_name and len(_palavras_nome) > 1 and len(_palavras_nome[-1]) <= 3:
                _sem_ultima = " ".join(_palavras_nome[:-1])
                if _produto_conhecido(_sem_ultima):
                    product_name = _sem_ultima
                    matched_name = _produto_conhecido(_sem_ultima)
            # Sabor que o cardapio nao tem ("50 empadas de queijo") nao vira
            # outro produto: virava Empada de Frango grande a R$ 9,00 (teste
            # do dono, 08/10). Fica de fora e o aviso sai com as opcoes.
            if matched_name and item_fora_do_cardapio(f"{qty} {product_name}"):
                extracted.setdefault("sabores_inexistentes", []).append(product_name.strip())
                continue

            if not matched_name and qty > 0:
                # Erro de digitacao ("25 SALSCHISA"): o item sumia do pedido
                # sem ninguem perguntar (teste do dono, 08/10 21h10).
                # Tambem sem a palavrinha do fim: "25 SALSCHISA i 50 QUIBE"
                # (o "e" digitado "i") - simulador do dono, 14/10.
                _tentativas = [product_name]
                if len(_palavras_nome) > 1 and len(_palavras_nome[-1]) <= 3:
                    _tentativas.append(" ".join(_palavras_nome[:-1]))
                for _tentativa in _tentativas:
                    _corrigido = _corrigir_grafia(_tentativa)
                    if _corrigido and _produto_conhecido(_corrigido):
                        matched_name = _produto_conhecido(_corrigido)
                        product_name = _corrigido
                        break
            if not matched_name and qty > 0:
                # Palavra que serve a varios sabores. Nao vira item (chutar foi
                # o defeito), mas tambem nao pode sumir: o cliente pediu "30 de
                # carne" e o ajuste de lote saiu sem esse sabor, como se ele
                # nunca tivesse falado (teste do dono, 26/09 18h20).
                opcoes = sabores_possiveis(product_name,
                                           preparo=preparo_dito(msg_lower))
                if len(opcoes) == 1:
                    # Depois de descartar os itens de balcao e os esgotados
                    # sobrou um so: "30 de queijo" num pedido de fritos e
                    # bolinha de queijo, e nao ha o que perguntar.
                    matched_name = opcoes[0]
                elif opcoes:
                    sabores_pendentes.append({
                        "qtd": qty,
                        # "25 SAL" -> "25 de salsicha": o cliente nao entendeu
                        # "25 de sal" (teste de 16/10 20h22).
                        "termo": _ABREVIACOES.get(product_name.strip(), product_name.strip()),
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
                _novo_item = {
                    "name": matched_name,
                    "quantity": qty,
                    "price": unit_price,
                }
                # "3 coxinha grande": o tamanho ja veio dito (teste de 14/10 -
                # ela perguntou "grande ou mini?" duas vezes).
                # "1 coxinha de frango com catupiry" so existe grande (27/10).
                if qty < 25 and (re.search(r"\b(?:grandes?|lanche)\b", product_name)
                                 or (matched_name == "Coxinha" and re.search(
                                     re.escape(product_name) + r"\s+com\s+catupiry", msg_lower))):
                    _novo_item["tamanho"] = "lanche"
                    _novo_item["price"] = 0.0
                items_found.append(_novo_item)

        # "1 cento pastel e bolinha", "metade X e metade Y": o volume dividido
        # vale mais do que qualquer leitura parcial do mesmo sabor acima.
        _quebrados: list = []
        divididos = itens_divididos(msg_lower, _quebrados)
        if divididos:
            nomes = {i["name"] for i in divididos}
            items_found = [i for i in items_found if i["name"] not in nomes] + divididos
            extracted["pedido_por_volume"] = True
        elif _quebrados and len(_quebrados) == 1:
            # Volume que nao fecha nos sabores ditos: nenhum item ainda. A
            # ficha guarda o volume, e aplicar_divisao_do_volume faz a
            # pergunta do dono com os sabores desta mesma mensagem.
            _vol, _sab = _quebrados[0]
            nomes = set(_sab)
            items_found = [i for i in items_found if i["name"] not in nomes]
            if not items_found:
                extracted["volume_pendente"] = _vol
                extracted["pedido_por_volume"] = True

        # "meio cento de coxinha" nao tem numero e escapava do padrao acima.
        # So entra o produto que o padrao numerado nao pegou.
        ja_vistos = {i["name"] for i in items_found}
        if extracted.get("volume_pendente"):
            ja_vistos |= {_produto_conhecido(p) for p in sabores_do_volume(msg_lower)}
        for extra in _itens_por_extenso(msg_lower):
            if extra["name"] not in ja_vistos:
                items_found.append(extra)
                ja_vistos.add(extra["name"])

        # "100 PATEIS DE QUEIJO E 100 DE CARNE": o "de carne" e do mesmo
        # salgado citado na frase (pastel), e nao duvida entre pastel, rissole,
        # croquete e esfirra (teste do dono, 07/10 18h39).
        if sabores_pendentes and items_found:
            bases = {_forma_de_busca(str(i["name"])).split()[0] for i in items_found}
            resolvidos = []
            for p in sabores_pendentes:
                opcao = [o for o in p.get("opcoes") or []
                         if _forma_de_busca(o).split()[0] in bases]
                if len(opcao) == 1:
                    try:
                        import facts
                        oficial = facts.resolve_unit_price(opcao[0], p["qtd"])
                        preco = oficial["preco_unitario"] if oficial else 0.0
                    except Exception:
                        preco = 0.0
                    items_found.append({"name": opcao[0], "quantity": p["qtd"], "price": preco})
                    resolvidos.append(p)
            sabores_pendentes = [p for p in sabores_pendentes if p not in resolvidos]

        if sabores_pendentes:
            extracted["sabores_pendentes"] = sabores_pendentes

        # "25 empada" num pedido de assados e a Empadinha do lote, nao a Empada
        # grande vendida por unidade. Virava a grande, em falta, e a troca
        # entrou em laco (Thayline, 24/10).
        for _it in items_found:
            if _it["name"] == "Empada de Frango" and int(_it.get("quantity") or 0) >= 25:
                _it["name"] = "Empadinha de Frango"
                _it["price"] = _PRECO_UNITARIO.get("Empadinha de Frango", _it["price"])
        _somados_msg: list = []
        for _it in items_found:
            _igual = next((x for x in _somados_msg if x["name"] == _it["name"]
                           and x.get("tamanho") == _it.get("tamanho")), None)
            if _igual:
                _igual["quantity"] += _it["quantity"]
            else:
                _somados_msg.append(dict(_it))
        items_found = _somados_msg

        if items_found:
            extracted["items"] = items_found
        else:
            # Volume dito sem sabor: guarda para a ficha pedir os sabores.
            unidades = volume_sem_sabor(msg_lower)
            # "meio cento de canudo": o produto vale mais que o volume. Virava
            # "frito ou assado? ate 2 sabores" e "frango e camarao" nao fechava
            # (teste de 27/10 17h47).
            _opcoes_vol = [(p, sabores_possiveis(p)) for _um, _f, p
                           in _PACOTE_POR_EXTENSO.findall(msg_lower) if p]
            _opcoes_vol = [(p, o) for p, o in _opcoes_vol if len(o) > 1]
            if unidades and len(_opcoes_vol) == 1:
                extracted["sabores_pendentes"] = list(extracted.get("sabores_pendentes") or []) + [
                    {"qtd": unidades, "termo": _opcoes_vol[0][0], "opcoes": _opcoes_vol[0][1]}]
            elif unidades:
                extracted["volume_pendente"] = unidades

    # Bebida em qualquer mensagem, inclusive com o pedido ja na ficha: ela
    # costuma vir como resposta a oferta ("pode mandar uma coca lata"). Vai
    # em campo proprio porque SOMA aos itens - como "items" ela trocaria o
    # pedido inteiro pela bebida.
    bebidas, bebida_sem_tamanho = bebidas_citadas(msg_lower)
    doces, doce_sem_formato = doces_citados(msg_lower)
    # Pergunta sobre o produto nao e pedido: "tem refrigerante de 1 litro?" e
    # "o combo vem com qual refrigerante?" abriam "Qual refrigerante voce
    # prefere?" como se a cliente tivesse pedido um (varredura de 02/10).
    if e_pergunta_sem_pedido(msg_lower):
        bebidas, bebida_sem_tamanho, doces, doce_sem_formato = [], [], [], []
    if bebidas:
        extracted["bebidas"] = bebidas
    if bebida_sem_tamanho:
        extracted["bebidas_pendentes"] = bebida_sem_tamanho
    if doces:
        extracted["doces"] = doces
    if doce_sem_formato:
        extracted["doces_pendentes"] = doce_sem_formato

    # Preparo dito em qualquer mensagem: o "frito" pode vir junto com o cento
    # ou na resposta a pergunta dos sabores.
    preparo = preparo_dito(msg_lower)
    if preparo:
        extracted["preparo_pendente"] = preparo
    elif _RX_FRITO.search(msg_lower) and _RX_ASSADO.search(msg_lower):
        # "500 salgados fritos e assados": os dois preparos, divididos entre
        # os sabores. Perguntar "e frito ou assado?" seria repetir o que ele
        # disse.
        extracted["preparo_pendente"] = "misto"

    # ── Referencia ──────────────────────────────────────────────────────────
    if not state.referencia:
        ref = detectar_referencia(message)
        if ref:
            extracted["referencia"] = ref

    # "Cento de pastel", sem o sabor: carne ou queijo? Entrava como carne sem
    # perguntar (teste de 15/10 16h32).
    _sem_sabor = [i for i in extracted.get("items") or [] if _e_pastel_sem_sabor(i.get("name"))]
    if _sem_sabor:
        extracted["items"] = [i for i in extracted["items"] if i not in _sem_sabor]
        if not extracted["items"]:
            extracted.pop("items")
        extracted["sabores_pendentes"] = list(extracted.get("sabores_pendentes") or []) + [
            {"qtd": i["quantity"], "termo": "pastel", "opcoes": opcoes_de_pastel()}
            for i in _sem_sabor]

    # "Bandeja de beijinho", "meio cento misto": o sabor do doce vai junto
    # (o cardapio digital tem Brigadeiro, Beijinho e Mista).
    for _d in extracted.get("doces") or []:
        if "sabor" in _d:
            if not _d["sabor"]:
                _d.pop("sabor")
            continue          # ja lido no proprio trecho (doces_citados)
        _d_sabor = sabor_do_doce(msg_lower, _d.get("name", ""))
        if _d_sabor:
            _d["sabor"] = _d_sabor

    return extracted


def _e_pastel_sem_sabor(nome) -> bool:
    import facts as _fx
    return _fx.normalize(str(nome or "")) in ("pastel", "pasteis")


def opcoes_de_pastel() -> list[str]:
    import facts as _fx
    return [p["item"] for p in _fx.get()["produtos"] if p.get("disponivel", True)
            and p.get("preco") is None and _fx.normalize(p["item"]).startswith("pastel")] \
        or ["Pastel carne", "Pastel queijo"]


def sabor_do_doce(msg_lower: str, nome_doce: str) -> str:
    """O sabor do doce dito na mensagem, com o nome do cardapio digital."""
    import facts as _fx
    t = _fx.normalize(msg_lower or "")
    bandeja = "bandeja" in _fx.normalize(nome_doce)
    if re.search(r"\bmist[oa]s?\b|\bmeio a meio\b|\bos dois\b", t):
        return "Mista (brigadeiro e beijinho)"
    if re.search(r"\bbeijinhos?\b", t):
        return "Beijinho" if bandeja else "Mista (brigadeiro e beijinho)"
    return ""


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
