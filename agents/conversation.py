"""
conversation.py — ConversationState e ConversationManager.
Gerencia estado compartilhado e orquestra sub-agentes.
"""

from dataclasses import dataclass, field
import time
import json
import re


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
    "pastel queijo": "Pastel Queijo",
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

_MODALIDADE_PERGUNTA = re.compile(
    r"\b(quanto|qual|quais|tem|voces|vcs|fazem|faz|taxa|valor|preco|custa|cobram|"
    r"funciona|como)\b"
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

_RETIRADA_EXPLICITA = re.compile(
    r"\bretirad\w*\b"
    r"|\bretirar\b"
    r"|\b(?:vou|posso|prefiro|quero|queria)\s+buscar\b"
    r"|\beu\s+busco\b"
    r"|\beu\s+pego\b"
    r"|\bna\s+fabrica\b"
    r"|\bno\s+balcao\b"
)


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

    # Pergunta sobre entrega nao e escolha de entrega.
    if "?" in message or _MODALIDADE_PERGUNTA.search(t):
        return ""

    # Resposta curta e seca a "entrega ou retirada?".
    curto = t.rstrip(" .!,")
    if curto in ("entrega", "e entrega", "eh entrega", "entrega mesmo", "entregar"):
        return "entrega"
    if curto in ("retirada", "e retirada", "eh retirada", "retirar", "vou buscar", "busco"):
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


def _extract_data_from_message(message: str, state: "ConversationState") -> dict:
    """Extrai dados estruturados da mensagem do cliente (nome, itens, modalidade, endereco, horario, pagamento).

    Retorna dict com os dados extraidos que devem ser aplicados ao state.
    """
    msg_lower = message.lower().strip()
    extracted = {}

    # ── Nome ────────────────────────────────────────────────────────────────
    # "sou a Ana" nao era reconhecido — so "sou o". O resultado era perguntar o
    # nome de novo para metade dos clientes, logo depois de eles o dizerem.
    name_patterns = [
        r"(?:sou (?:o|a)?|meu nome (?:e|é)|me chamo|aqui (?:e|é)|aqui (?:o|a)|quem fala (?:e|é))\s+([A-ZÀ-Ú][a-zà-ú]+)",
        r"(?:Oi|Ola|Olá|Bom dia|Boa tarde|Boa noite)[^,!,.]*[,!,.]?\s*(?:sou (?:o|a)?|meu nome (?:e|é)|me chamo)\s+([A-ZÀ-Ú][a-zà-ú]+)",
    ]
    for pat in name_patterns:
        m = re.search(pat, message, re.IGNORECASE)
        if m and not state.name:
            extracted["name"] = m.group(1).strip()
            break

    # ── Modalidade ──────────────────────────────────────────────────────────
    if not state.modality:
        modalidade = detectar_modalidade(message)
        if modalidade:
            extracted["modality"] = modalidade

    # ── Horario ─────────────────────────────────────────────────────────────
    if not state.horario:
        # Padrões: "18h", "18:00", "as 18h", "às 18h", "hoje as 18h".
        # O "as"/"às" era obrigatorio, entao o cliente que respondia so "16h" a
        # pergunta de horario nao era entendido e a pergunta se repetia. O "h"
        # ou ":" logo apos os digitos continua obrigatorio, para numero de rua
        # ("Av. Calama, 3234") nao virar horario.
        time_match = re.search(r'\b(?:as|às|a|à)?\s*(\d{1,2})\s*[h:](\d{2})?\b', msg_lower)
        if time_match:
            hour = time_match.group(1)
            minute = time_match.group(2) or "00"
            extracted["horario"] = f"{hour}h{minute}" if minute != "00" else f"{hour}h"

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
            # Remove "bairro" e o que vem depois se estiver junto
            addr = re.split(r'\s+bairro', addr, flags=re.IGNORECASE)[0].strip()
            extracted["endereco"] = addr

    # ── Bairro ──────────────────────────────────────────────────────────────
    if not state.bairro:
        # Padrão: "bairro Olaria" ou "no bairro Olaria"
        bairro_match = re.search(r'(?:bairro|bairro)\s+([A-ZÀ-Ú][a-zà-ú]+)', message, re.IGNORECASE)
        if bairro_match:
            extracted["bairro"] = bairro_match.group(1).strip()

    # ── Pagamento ───────────────────────────────────────────────────────────
    if not state.payment_method:
        if re.search(r'\b(pix|chave pix)\b', msg_lower):
            extracted["payment_method"] = "pix"
        elif re.search(r'\b(crédito|credito)\b', msg_lower):
            extracted["payment_method"] = "cartao_credito"
        elif re.search(r'\b(débito|debito)\b', msg_lower):
            extracted["payment_method"] = "cartao_debito"
        elif re.search(r'\b(dinheiro|na entrega|pagar na entrega)\b', msg_lower):
            extracted["payment_method"] = "dinheiro"
        elif re.search(r'\b(cartão|cartao|maquininha|maestro|visa|elo)\b', msg_lower):
            # Cartao sem dizer se e debito ou credito. Antes isso nao era
            # extraido de jeito nenhum e a ficha continuava pedindo "forma de
            # pagamento" mesmo depois de o cliente responder "cartao" duas
            # vezes. Agora fica registrado como pendente de detalhe: a pergunta
            # passa a ser debito ou credito, e nao a mesma de novo.
            extracted["payment_method"] = "cartao_a_definir"

    # ── Itens ───────────────────────────────────────────────────────────────
    if not state.items:
        # Padrão: "50 coxinhas", "25 kibes", "100 esfirras"
        # O fim do nome do produto so era reconhecido em virgula, " e " ou fim
        # da mensagem. Com isso "quero 25 coxinhas pra retirar as 16h" nao
        # extraia item nenhum — o pedido sumia e a Sandrinha perguntava de novo
        # o que o cliente ja tinha dito. Aqui o nome termina tambem em ponto e
        # nas preposicoes que costumam vir depois do produto.
        item_pattern = re.findall(
            r'(\d+)\s*(?:unidades?|un\.?|pcs\.?)?\s*(de\s+)?([a-záàãâéêíóôõúç\s]+?)'
            r'(?:\s*(?:[,;.!?]|\be\b|\bpra\b|\bpara\b|\bcom\b|\bno\b|\bna\b|\bem\b'
            r'|\bhoje\b|\bamanh[aã]\b|\bàs\b|\bas\b|$))',
            msg_lower
        )
        items_found = []
        for qty_str, _, product_name in item_pattern:
            qty = int(qty_str)
            product_name = product_name.strip()

            # Nome curto demais nao identifica produto. Sem esta guarda, "hoje as
            # 18h" virava "18 x Coxinha": o resto "h" casava por substring com
            # "coxinha" e o pedido inteiro era trocado por um horario.
            if len(product_name) < 3:
                continue

            # Remove plural
            if product_name.endswith('s') and not product_name.endswith('es'):
                product_name_singular = product_name[:-1]
            elif product_name.endswith('es'):
                product_name_singular = product_name[:-2] if len(product_name) > 3 else product_name
            else:
                product_name_singular = product_name

            # Busca produto conhecido
            matched_name = None
            # Tenta match exato primeiro
            for key, canonical in _TODOS_PRODUTOS.items():
                if key == product_name or key == product_name_singular:
                    matched_name = canonical
                    break
            # Depois, o nome do produto contido no texto (ou vice-versa),
            # exigindo um trecho longo o bastante para nao casar por acaso
            if not matched_name:
                for key, canonical in _TODOS_PRODUTOS.items():
                    if (key in product_name or product_name in key) and min(len(key), len(product_name)) >= 4:
                        matched_name = canonical
                        break
            # Por ultimo, prefixo de 4 letras (trata plural/typo leve)
            if not matched_name and len(product_name_singular) >= 4:
                for key, canonical in _TODOS_PRODUTOS.items():
                    if key.startswith(product_name_singular[:4]) or product_name_singular.startswith(key[:4]):
                        matched_name = canonical
                        break

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

        if items_found:
            extracted["items"] = items_found

    # ── Referencia ──────────────────────────────────────────────────────────
    if not state.referencia:
        ref_match = re.search(r'(?:referência|referencia|ponto de referência|ponto de referencia|proximidade|perto de|em frente|ao lado)\s+(.+?)(?:\s*(?:,|\.|hoje|amanhã|amanha|as |às |para |pra ))', message, re.IGNORECASE)
        if ref_match:
            extracted["referencia"] = ref_match.group(1).strip()

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
            ok, motivo, _sugestao = horario.validar_agendamento(
                _date.fromisoformat(state.data_iso), state.horario, now
            )
            if not ok and motivo and not motivo.startswith("Falta"):
                if "horario" in motivo.lower() or "intervalo" in motivo.lower() or "20h00" in motivo:
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
        if faltando:
            linhas.append(
                f"2. Ainda faltam: {', '.join(faltando)}. Pergunte APENAS "
                f'"{faltando[0]}" nesta mensagem, uma pergunta so. Nao pergunte o '
                f"resto agora e nao apresente resumo nem total."
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
