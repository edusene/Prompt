"""
pedido.py — Pedido Agent.
Monta pedido, valida com calculate_order e apresenta resumo.
"""

import json
import llm
import cardapio
from agents.conversation import ConversationState


class PedidoAgent:
    """Agente responsavel por montar e validar pedidos."""

    def __init__(self):
        self.name = "pedido"
        self._prompt = None

    def _load_prompt(self) -> str:
        if self._prompt is None:
            try:
                with open("prompts/pedido.md", "r", encoding="utf-8") as f:
                    self._prompt = f.read()
            except Exception:
                self._prompt = self._default_prompt()
        return self._prompt

    def _default_prompt(self) -> str:
        return """Voce e a Sandrinha da BALBS.
Valide pedidos com calculate_order antes do resumo.
Apresente resumo em lista vertical.
Aguarde confirmacao explicita."""

    def process(self, message: str, state: ConversationState, media=None) -> str:
        """Processa mensagem na fase de pedido."""

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

        # Detecta se resumo foi apresentado
        if "Posso confirmar" in response or "confirmar o seu pedido" in response:
            state.resumo_apresentado = True

        return response

    def _build_system_prompt(self, state: ConversationState) -> str:
        prompt = self._load_prompt()

        if state.name:
            prompt += f"\n\nCliente: {state.name}"

        if state.items:
            prompt += "\n\n=== ITENS DO PEDIDO ==="
            for item in state.items:
                prompt += f"\n- {item.get('quantity', 0)} un - {item.get('name', '?')} (R$ {item.get('price', 0):.2f}/un)"

        if state.modality:
            prompt += f"\n\nModalidade: {state.modality}"
        if state.bairro:
            prompt += f"\nBairro: {state.bairro}"
        if state.endereco:
            prompt += f"\nEndereco: {state.endereco}"
        if state.horario:
            prompt += f"\nHorario: {state.horario}"
        if state.payment_method:
            prompt += f"\nPagamento: {state.payment_method}"

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
                    "name": "calculate_order",
                    "description": "Valida pedido: verifica produtos, calcula subtotal, frete e total",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "description": "Lista de itens com nome, quantidade e preco",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "quantity": {"type": "integer"},
                                        "price": {"type": "number"},
                                    },
                                    "required": ["name", "quantity"],
                                },
                            },
                            "bairro": {"type": "string", "description": "Bairro de entrega"},
                        },
                        "required": ["items"],
                    },
                },
            }
        ]

    def _get_tool_handlers(self, state: ConversationState) -> dict:
        def _calculate(items: list, bairro: str = "", **kwargs) -> str:
            result = cardapio.calcular_pedido(items, bairro or state.bairro)
            if result.get("success"):
                state.subtotal = result.get("subtotal", 0)
                state.frete = result.get("delivery_fee", 0)
                state.taxa_retorno = result.get("return_fee", 0)
                state.total = result.get("total", 0)
            return json.dumps(result, ensure_ascii=False)

        return {"calculate_order": _calculate}
