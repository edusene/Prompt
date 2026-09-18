"""
triagem.py — Triagem Agent.
Coleta nome do cliente e detecta intencao inicial.
"""

import llm
import knowledge_rag
from agents.conversation import ConversationState


class TriagemAgent:
    """Agente responsavel por saudacao e coleta de nome."""

    def __init__(self):
        self.name = "triagem"
        self._prompt = None

    def _load_prompt(self) -> str:
        if self._prompt is None:
            self._prompt = knowledge_rag.load_prompt_db_first(
                db_name="triagem.md",
                local_path="prompts/triagem.md",
                fallback_content=self._default_prompt(),
            )
        return self._prompt

    def _default_prompt(self) -> str:
        return """Voce e a Sandrinha da BALBS SALGADOS.
Nunca diga que e robo ou IA.
Maximo 2-3 linhas por mensagem.
1 pergunta por vez."""

    def process(self, message: str, state: ConversationState, media=None) -> str:
        """Processa mensagem na fase de triagem."""

        system = self._build_system_prompt(state)
        history = self._build_history(state, message)

        response = llm.generate_reply(history=history, system=system)
        return response

    def _build_system_prompt(self, state: ConversationState) -> str:
        prompt = self._load_prompt()

        if state.knowledge_context:
            prompt += (
                "\n\n=== INFORMACOES OFICIAIS DA EMPRESA ===\n"
                "Use esta base como fonte verdadeira para horarios, cardapio e regras.\n"
                + state.knowledge_context
            )

        if state.name:
            prompt += f"\n\nO nome do cliente e: {state.name}"
        else:
            prompt += "\n\nO cliente ainda NAO informou o nome. Colete o nome."

        if state.items:
            prompt += "\n\nItens ja pedidos:"
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
