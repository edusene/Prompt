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
            self._prompt = knowledge_rag.load_prompt_db_first(
                db_name="logistica.md",
                local_path="prompts/logistica.md",
                fallback_content=self._default_prompt(),
            )
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
        tools = self._get_tools()
        tool_handlers = self._get_tool_handlers()

        response = llm.generate_reply(history=history, system=system, tools=tools, tool_handlers=tool_handlers)

        # Atualiza estado baseado na resposta
        self._update_state(state, message, response)

        return response

    def _get_knowledge(self, message: str, state: ConversationState) -> str:
        """Busca knowledge de frete e bairros."""
        queries = ["frete entrega bairros delivery taxa"]
        if state.bairro:
            queries.insert(0, f"frete {state.bairro}")
            queries.append(f"taxa entrega {state.bairro}")

        context_parts = []
        for q in queries:
            result = knowledge_rag.search_context(q, top_k=5)
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
        import re
        msg_lower = message.lower().strip()

        # Detecta modalidade
        if "entrega" in msg_lower:
            state.modality = "entrega"
        elif "retirada" in msg_lower or "balcao" in msg_lower or "fabrica" in msg_lower:
            state.modality = "retirada"

        # Detecta bairro de varias formas
        if not state.bairro:
            bairro = None
            # Padroes comuns: "bairro X", "no X", "entrega X", "e X"
            m = re.search(r'(?:bairro|no|na|entrega\s+(?:no|na)|para|em)\s+([A-ZÁÉÍÓÚÇÃÕ][a-záéíóúçãõ]+(?:\s+[A-ZÁÉÍÓÚÇÃÕ]?[a-záéíóúçãõ]+)*)', message)
            if m:
                bairro = m.group(1).strip()
            elif len(words := message.strip().split()) <= 3 and len(message.strip()) > 3:
                # Frase curta que pode ser so o bairro
                candidate = message.strip()
                if not any(x in candidate.lower() for x in ["sim", "nao", "ok", "entrega", "retirada", "hoje", "amanha"]):
                    bairro = candidate
            if bairro and len(bairro) > 2:
                state.bairro = bairro

        # Detecta endereco (rua, numero)
        if not state.endereco:
            m = re.search(r'(?:rua|av\.?|avenida|alameda|travessa)\s+[^,]+,?\s*\d+', message, re.IGNORECASE)
            if m:
                state.endereco = m.group(0).strip()

        # Detecta horario (apenas numeros no formato HH:MM ou HHhMM)
        m = re.search(r'(\d{1,2})\s*[h:]\s*(\d{2})', message)
        if m:
            state.horario = m.group(0).strip()

        # Detecta data - SO se for claramente uma resposta de data
        # Nao detecta "hoje" em frases como "hoje eu queria saber..."
        msg_stripped = msg_lower.rstrip("?!.,;:")
        if msg_stripped in ("hoje", "hoje sim", "sim hoje"):
            state.data_agendada = "hoje"
        elif msg_stripped in ("amanha", "amanhã", "amanha sim", "sim amanha"):
            state.data_agendada = "amanha"
        elif re.match(r'^\d{1,2}/\d{1,2}(/\d{2,4})?$', msg_stripped):
            state.data_agendada = msg_stripped

    def _get_tools(self) -> list:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_knowledge",
                    "description": "Busca informacoes na base de conhecimento (frete, bairros, taxas, horarios)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Busca descritiva (ex: 'frete Aponia', 'taxa entrega Centro')",
                            },
                            "filename": {
                                "type": "string",
                                "description": "Nome exato do arquivo knowledge",
                            },
                        },
                    },
                },
            }
        ]

    def _get_tool_handlers(self) -> dict:
        def _search(query="", filename="", **kwargs):
            if filename:
                return knowledge_rag.get_file_content(filename.strip())
            return knowledge_rag.search_context(query.strip(), top_k=5)

        return {"search_knowledge": _search}
