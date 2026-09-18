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

    # Tempo atual (injetado pelo ConversationManager)
    current_time: str = ""

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
            "history": self.history[-20:],
            "collected_data": self.collected_data,
            "last_bot_message": self.last_bot_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationState":
        """Cria instancia a partir de dicionario."""
        fields = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in fields})

    def save_to_db(self):
        """Salva estado no banco de dados."""
        try:
            import database
            database.save_conversation_state(self.phone, self.to_dict())
        except Exception as e:
            print(f"[state] Erro ao salvar: {e}")

    @classmethod
    def load_from_db(cls, phone: str) -> "ConversationState":
        """Carrega estado do banco de dados."""
        try:
            import database
            data = database.get_conversation_state(phone)
            if data:
                return cls.from_dict(data)
        except Exception as e:
            print(f"[state] Erro ao carregar: {e}")
        return cls(phone=phone)


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
    "rissole": "Rissole de Carne",
    "rissole de carne": "Rissole de Carne",
    "churros": "Churros",
    "croquete": "Croquete de Carne",
    "croquete de carne": "Croquete de Carne",
    "misto": "Misto",
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


def _extract_data_from_message(message: str, state: "ConversationState") -> dict:
    """Extrai dados estruturados da mensagem do cliente (nome, itens, modalidade, endereco, horario, pagamento).

    Retorna dict com os dados extraidos que devem ser aplicados ao state.
    """
    msg_lower = message.lower().strip()
    extracted = {}

    # ── Nome ────────────────────────────────────────────────────────────────
    name_patterns = [
        r"(?:sou o|meu nome (?:e|é)|me chamo|aqui (?:e|é)|aqui o)\s+([A-ZÀ-Ú][a-zà-ú]+)",
        r"(?:Oi|Ola|Olá|Bom dia|Boa tarde|Boa noite)[^,!,.]*[,!,.]?\s*(?:sou o|meu nome (?:e|é)|me chamo)\s+([A-ZÀ-Ú][a-zà-ú]+)",
    ]
    for pat in name_patterns:
        m = re.search(pat, message, re.IGNORECASE)
        if m and not state.name:
            extracted["name"] = m.group(1).strip()
            break

    # ── Modalidade ──────────────────────────────────────────────────────────
    if not state.modality:
        if re.search(r'\b(entregar|entrega|receber em casa|levar na casa|chegar na casa)\b', msg_lower):
            extracted["modality"] = "entrega"
        elif re.search(r'\b(retirar|retirada|buscar|ir buscar|na fabrica|na fábrica|balcao|balcão)\b', msg_lower):
            extracted["modality"] = "retirada"

    # ── Horario ─────────────────────────────────────────────────────────────
    if not state.horario:
        # Padrões: "18h", "18:00", "as 18h", "às 18h", "hoje as 18h"
        time_match = re.search(r'(?:as?|às?)\s*(\d{1,2})[h:](\d{2})?', msg_lower)
        if time_match:
            hour = time_match.group(1)
            minute = time_match.group(2) or "00"
            extracted["horario"] = f"{hour}h{minute}" if minute != "00" else f"{hour}h"

    # ── Data ────────────────────────────────────────────────────────────────
    if not state.data_agendada:
        if re.search(r'\b(hoje)\b', msg_lower):
            extracted["data_agendada"] = "hoje"
        elif re.search(r'\b(amanhã|amanha)\b', msg_lower):
            extracted["data_agendada"] = "amanha"
        elif re.search(r'\b(segunda|terca|terça|quarta|quinta|sexta|sabado|sábado)\b', msg_lower):
            day_match = re.search(r'\b(segunda|terca|terça|quarta|quinta|sexta|sabado|sábado)\b', msg_lower)
            if day_match:
                extracted["data_agendada"] = day_match.group(1)
        else:
            date_match = re.search(r'(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?', msg_lower)
            if date_match:
                extracted["data_agendada"] = date_match.group(0)

    # ── Endereco ────────────────────────────────────────────────────────────
    if not state.endereco:
        # Padrão: "Av. Calama, 3234" ou "Rua X, 123"
        addr_match = re.search(
            r'((?:Av\.|Avenida|Rua|Al\.|Alameda|Travessa|Tv\.|Estrada)\s+[^\.,]+(?:,\s*\d+)?)',
            message, re.IGNORECASE
        )
        if addr_match:
            extracted["endereco"] = addr_match.group(1).strip()

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
        elif re.search(r'\b(cartão|cartao|credito|crédito|débito|debito|maestro|visa|elo)\b', msg_lower):
            if re.search(r'\b(crédito|credito)\b', msg_lower):
                extracted["payment_method"] = "cartao_credito"
            elif re.search(r'\b(débito|debito)\b', msg_lower):
                extracted["payment_method"] = "cartao_debito"
            else:
                extracted["payment_method"] = "cartao_debito"
        elif re.search(r'\b(dinheiro|na entrega|pagar na entrega)\b', msg_lower):
            extracted["payment_method"] = "dinheiro"

    # ── Itens ───────────────────────────────────────────────────────────────
    if not state.items:
        # Padrão: "50 coxinhas", "25 kibes", "100 esfirras"
        item_pattern = re.findall(
            r'(\d+)\s*(?:unidades?|un\.?|pcs\.?)?\s*(de\s+)?([a-záàãâéêíóôõúç\s]+?)(?:\s*(?:,|e\b|$))',
            msg_lower
        )
        items_found = []
        for qty_str, _, product_name in item_pattern:
            qty = int(qty_str)
            product_name = product_name.strip()
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
                if key in product_name or product_name in key:
                    matched_name = canonical
                    break
            # Tenta match parcial
            if not matched_name:
                for key, canonical in _TODOS_PRODUTOS.items():
                    if key.startswith(product_name_singular[:4]) or product_name_singular.startswith(key[:4]):
                        matched_name = canonical
                        break

            if matched_name and qty > 0:
                items_found.append({
                    "name": matched_name,
                    "quantity": qty,
                    "price": 0,
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

        # 1.1 Injeta horario atual (fuso de Porto Velho = UTC-4)
        from datetime import datetime, timedelta
        _now = (datetime.utcnow() - timedelta(hours=4)).strftime("%d/%m/%Y %H:%M")
        state.current_time = _now

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
            if "data_agendada" in extracted and not state.data_agendada:
                state.data_agendada = extracted["data_agendada"]
                print(f"[extract] data: {state.data_agendada}")
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
            if new_idx >= current_idx or state.items:
                state.phase = new_phase
                state.active_agent = router_result.get("agente", state.active_agent)
                state.intent = router_result.get("intencao", state.intent)

        # 6. Executa agente ativo
        agent = self.agents.get(state.active_agent)
        if not agent:
            agent = self.agents["triagem"]
            state.active_agent = "triagem"

        response = agent.process(message, state, media)

        # 7. Adiciona resposta ao historico
        state.history.append({
            "role": "assistant",
            "text": response,
            "timestamp": time.time(),
        })
        state.last_bot_message = response

        # 8. Salva estado
        state.save_to_db()

        return response

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
