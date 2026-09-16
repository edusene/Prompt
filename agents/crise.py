"""
crise.py — Crise Agent.
Gerencia reclamacoes, escaladas e cancelamentos.
"""

import llm
from agents.conversation import ConversationState


class CriseAgent:
    """Agente responsavel por reclamacoes e escaladas."""

    def __init__(self):
        self.name = "crise"
        self._prompt = None

    def _load_prompt(self) -> str:
        if self._prompt is None:
            try:
                with open("prompts/crise.md", "r", encoding="utf-8") as f:
                    self._prompt = f.read()
            except Exception:
                self._prompt = self._default_prompt()
        return self._prompt

    def _default_prompt(self) -> str:
        return """Voce e a Sandrinha da BALBS.
PRIORIDADE MAXIMA para reclamacoes.
Respostas fixas, SEM perguntas.
Transfira IMEDIATAMENTE para humano."""

    def process(self, message: str, state: ConversationState, media=None) -> str:
        """Processa mensagem na fase de crise."""

        system = self._build_system_prompt(state)
        history = self._build_history(state, message)
        tools = self._get_tools()
        tool_handlers = self._get_tool_handlers(state)

        response = llm.generate_reply(
            history=history,
            system=system,
            tools=tools,
            tool_handlers=tool_handlers,
        )

        return response

    def _build_system_prompt(self, state: ConversationState) -> str:
        prompt = self._load_prompt()

        if state.name:
            prompt += f"\n\nCliente: {state.name}"

        if state.items:
            prompt += "\n\nItens do pedido:"
            for item in state.items:
                prompt += f"\n- {item.get('quantity', 0)} un - {item.get('name', '?')}"

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
                    "name": "transfer_to_human",
                    "description": "Transfere atendimento para humano. Use para reclamacoes, cancelamentos, ou situações graves.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "description": "Motivo da transferencia",
                            }
                        },
                        "required": ["reason"],
                    },
                },
            }
        ]

    def _get_tool_handlers(self, state: ConversationState) -> dict:
        def _transfer(reason: str = "", **kwargs) -> str:
            import app
            performed = app.perform_transfer(state.phone, reason, send_message=False)
            if performed:
                return "Transferencia realizada com sucesso."
            return "Cliente ja esta em atendimento humano."

        return {"transfer_to_human": _transfer}
