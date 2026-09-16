"""
pagamento.py — Pagamento Agent.
Gerencia formas de pagamento e finalizacao de pedido.
"""

import json
import llm
import cardapio
from agents.conversation import ConversationState


class PagamentoAgent:
    """Agente responsavel por pagamento e envio de pedido."""

    def __init__(self):
        self.name = "pagamento"
        self._prompt = None

    def _load_prompt(self) -> str:
        if self._prompt is None:
            try:
                with open("prompts/pagamento.md", "r", encoding="utf-8") as f:
                    self._prompt = f.read()
            except Exception:
                self._prompt = self._default_prompt()
        return self._prompt

    def _default_prompt(self) -> str:
        return """Voce e a Sandrinha da BALBS.
Chave Pix CNPJ: 84749274000178 - Balbs Salgados.
Pix e antecipado. Cartao/dinheiro na entrega.
Apos finalizar, encerre sem perguntas."""

    def process(self, message: str, state: ConversationState, media=None) -> str:
        """Processa mensagem na fase de pagamento."""

        system = self._build_system_prompt(state)
        history = self._build_history(state, message)
        tools = self._get_tools(state)
        tool_handlers = self._get_tool_handlers(state)

        response = llm.generate_reply(
            history=history,
            system=system,
            tools=tools,
            tool_handlers=tool_handlers,
        )

        # Detecta se pedido foi enviado
        if "Pedido finalizado" in response or "enviado com sucesso" in response:
            state.pedido_enviado = True

        return response

    def _build_system_prompt(self, state: ConversationState) -> str:
        prompt = self._load_prompt()

        if state.name:
            prompt += f"\n\nCliente: {state.name}"

        if state.items:
            prompt += "\n\n=== RESUMO DO PEDIDO ==="
            for item in state.items:
                prompt += f"\n- {item.get('quantity', 0)} un - {item.get('name', '?')} (R$ {item.get('price', 0):.2f})"
            prompt += f"\n\nSubtotal: R$ {state.subtotal:.2f}"
            if state.frete > 0:
                prompt += f"\nFrete: R$ {state.frete:.2f}"
            if state.taxa_retorno > 0:
                prompt += f"\nTaxa retorno: R$ {state.taxa_retorno:.2f}"
            prompt += f"\nTOTAL: R$ {state.total:.2f}"

        if state.modality:
            prompt += f"\n\nModalidade: {state.modality}"
        if state.payment_method:
            prompt += f"\nPagamento: {state.payment_method}"
        if state.endereco:
            prompt += f"\nEndereco: {state.endereco}"
        if state.bairro:
            prompt += f"\nBairro: {state.bairro}"

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

    def _get_tools(self, state: ConversationState) -> list:
        tools = []

        if not state.pedido_enviado:
            tools.append({
                "type": "function",
                "function": {
                    "name": "send_order_to_cardapio",
                    "description": "Envia pedido confirmado para o cardapio digital da BALBS",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "customer_name": {"type": "string"},
                            "customer_phone": {"type": "string"},
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "quantity": {"type": "integer"},
                                        "price": {"type": "number"},
                                    },
                                },
                            },
                            "total": {"type": "number"},
                            "delivery_address": {"type": "string"},
                            "payment_method": {"type": "string"},
                            "subtotal": {"type": "number"},
                            "delivery_fee": {"type": "number"},
                            "return_fee": {"type": "number"},
                            "notes": {"type": "string"},
                        },
                        "required": ["customer_name", "customer_phone", "items", "total"],
                    },
                },
            })

        return tools

    def _get_tool_handlers(self, state: ConversationState) -> dict:
        def _send_order(
            customer_name: str,
            customer_phone: str,
            items: list,
            total: float,
            delivery_address: str = "",
            payment_method: str = "",
            subtotal: float = 0,
            delivery_fee: float = 0,
            return_fee: float = 0,
            notes: str = "",
            **kwargs,
        ) -> str:
            import app

            # Calcula totais se nao informados
            if not subtotal:
                subtotal = sum(
                    float(i.get("quantity", 0)) * float(i.get("price", 0))
                    for i in items
                )
            if not total:
                total = float(subtotal) + float(delivery_fee) - float(return_fee)

            # Chama handler existente do app
            result_str = app._send_order_handler(
                customer_name=customer_name,
                customer_phone=customer_phone,
                items=items,
                total=total,
                delivery_address=delivery_address or (
                    f"{state.endereco}, {state.bairro}" if state.endereco else "Retirada"
                ),
                payment_method=payment_method or state.payment_method,
                subtotal=subtotal,
                delivery_fee=delivery_fee or state.frete,
                return_fee=return_fee or state.taxa_retorno,
                notes=notes,
            )

            state.pedido_enviado = True
            return result_str

        return {"send_order_to_cardapio": _send_order}
