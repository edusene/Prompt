"""
logistica.py — Logistica Agent.
Gerencia entrega, retirada, endereco, frete e horarios.
"""

import llm
import knowledge_rag
from agents.conversation import ConversationState


class LogisticaAgent:
    """Agente responsavel por logistica (entrega/retirada, frete, endereco)."""

    def __init__(self):
        self.name = "logistica"
        self._prompt = None

    def _load_prompt(self) -> str:
        if self._prompt is None:
            try:
                with open("prompts/logistica.md", "r", encoding="utf-8") as f:
                    self._prompt = f.read()
            except Exception:
                self._prompt = self._default_prompt()
        return self._prompt

    def _default_prompt(self) -> str:
        return """Voce e a Sandrinha da BALBS.
Colete dados de entrega ou retirada.
Consulte frete na tabela oficial.
Taxa retorno: R$ 2,00 (dinheiro/cartao) ou R$ 0,00 (Pix)."""

    def process(self, message: str, state: ConversationState, media=None) -> str:
        """Processa mensagem na fase de logistica."""

        context = self._get_knowledge(message, state)
        system = self._build_system_prompt(state, context)
        history = self._build_history(state, message)

        response = llm.generate_reply(history=history, system=system)

        # Atualiza estado baseado na resposta
        self._update_state(state, message, response)

        return response

    def _get_knowledge(self, message: str, state: ConversationState) -> str:
        """Busca knowledge de frete e bairros."""
        queries = ["frete entrega bairros delivery"]
        if state.bairro:
            queries.append(f"frete {state.bairro}")

        context_parts = []
        for q in queries:
            result = knowledge_rag.search_context(q, top_k=3)
            if result:
                context_parts.append(result)

        return "\n\n".join(context_parts)

    def _build_system_prompt(self, state: ConversationState, context: str) -> str:
        prompt = self._load_prompt()

        if context:
            prompt += f"\n\n=== TABELA DE FRETE ===\n{context}"

        if state.name:
            prompt += f"\n\nCliente: {state.name}"

        if state.modality:
            prompt += f"\nModalidade: {state.modality}"
        if state.bairro:
            prompt += f"\nBairro: {state.bairro}"
        if state.endereco:
            prompt += f"\nEndereco: {state.endereco}"
        if state.referencia:
            prompt += f"\nReferencia: {state.referencia}"
        if state.horario:
            prompt += f"\nHorario: {state.horario}"
        if state.data_agendada:
            prompt += f"\nData: {state.data_agendada}"

        if state.items:
            prompt += "\n\nItens do pedido:"
            for item in state.items:
                prompt += f"\n- {item.get('quantity', 0)} un - {item.get('name', '?')}"

        if state.current_time:
            prompt += f"\n\nData e hora atual (Porto Velho): {state.current_time}"

        return prompt

    def _build_history(self, state: ConversationState, message: str) -> list:
        history = []
        for msg in state.history[-6:]:
            history.append({
                "role": msg.get("role", "user"),
                "parts": [{"text": msg.get("text", "")}],
            })
        history.append({"role": "user", "parts": [{"text": message}]})
        return history

    def _update_state(self, state: ConversationState, message: str, response: str):
        """Atualiza estado baseado na interacao."""
        msg_lower = message.lower()

        # Detecta modalidade
        if "entrega" in msg_lower:
            state.modality = "entrega"
        elif "retirada" in msg_lower or "balcao" in msg_lower or "fabrica" in msg_lower:
            state.modality = "retirada"

        # Detecta bairro (palavra unica que pode ser bairro)
        words = message.strip().split()
        if len(words) == 1 and len(words[0]) > 3 and not state.bairro:
            if state.modality == "entrega" or not state.modality:
                # Pode ser bairro
                pass

        # Detecta horario
        for termo in ["hoje", "amanha", "agora"]:
            if termo in msg_lower:
                state.data_agendada = termo
                break
