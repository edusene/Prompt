"""
cardapio.py — Cardapio Agent.
Apresenta produtos, precos, sabores e porcoes.
"""

import llm
import knowledge_rag
from agents.conversation import ConversationState


class CardapioAgent:
    """Agente responsavel por cardapio, precos e porcoes."""

    def __init__(self):
        self.name = "cardapio"
        self._prompt = None

    def _load_prompt(self) -> str:
        if self._prompt is None:
            try:
                with open("prompts/cardapio.md", "r", encoding="utf-8") as f:
                    self._prompt = f.read()
            except Exception:
                self._prompt = self._default_prompt()
        return self._prompt

    def _default_prompt(self) -> str:
        return """Voce e a Sandrinha da BALBS.
Consulte precos antes de informar.
Porcoes: 25 un, 50 un (meio cento), 100 un (cento).
Nunca misture sabores na mesma porcao."""

    def process(self, message: str, state: ConversationState, media=None) -> str:
        """Processa mensagem na fase de cardapio."""

        context = self._get_knowledge(message, state)
        system = self._build_system_prompt(state, context)
        history = self._build_history(state, message)
        tools = self._get_tools()
        tool_handlers = self._get_tool_handlers()

        response = llm.generate_reply(
            history=history,
            system=system,
            tools=tools,
            tool_handlers=tool_handlers,
        )
        return response

    def _get_knowledge(self, message: str, state: ConversationState) -> str:
        """Busca knowledge relevante."""
        queries = []

        msg_lower = message.lower()
        for sabor in ["coxinha", "esfirra", "empadinha", "quibe", "pastel", "rissole", "canudinho", "enroladinho", "quiche", "saltenha", "panqueca", "bomba", "empada"]:
            if sabor in msg_lower:
                queries.append(sabor)
                break

        if not queries:
            if state.items:
                queries.append("precos porcoes salgados")
            else:
                queries.append("cardapio produtos salgados")

        context_parts = []
        for q in queries:
            result = knowledge_rag.search_context(q, top_k=3)
            if result:
                context_parts.append(result)

        return "\n\n".join(context_parts)

    def _build_system_prompt(self, state: ConversationState, context: str) -> str:
        prompt = self._load_prompt()

        if context:
            prompt += f"\n\n=== CONTEXTO ===\n{context}"

        if state.name:
            prompt += f"\n\nO nome do cliente e: {state.name}"

        if state.items:
            prompt += "\n\n=== ITENS JA SELECIONADOS ==="
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

    def _get_tools(self) -> list:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_knowledge",
                    "description": "Busca informacoes na base de conhecimento (produtos, precos, sabores)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Busca descritiva (ex: 'preco coxinha', 'sabores disponiveis')",
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
        def _search(query: str = "", filename: str = "", **kwargs) -> str:
            if filename:
                content = knowledge_rag.get_file_content(filename.strip())
                return content or f"Arquivo '{filename}' nao encontrado."
            if not query or not query.strip():
                return "Forneca uma busca descritiva."
            content = knowledge_rag.search_context(query.strip(), top_k=5)
            return content or "Nenhuma informacao encontrada."

        return {"search_knowledge": _search}
