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
            self._prompt = knowledge_rag.load_prompt_db_first(
                db_name="pedido.md",
                local_path="prompts/pedido.md",
                fallback_content=self._default_prompt(),
            )
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
        if state.data_agendada:
            prompt += f"\nData: {state.data_agendada}"
        if state.payment_method:
            prompt += f"\nPagamento: {state.payment_method}"

        if state.current_time:
            prompt += f"\n\nData e hora atual (Porto Velho): {state.current_time}"

        # Status dos dados obrigatorios
        dados_faltando = []
        if not state.modality:
            dados_faltando.append("modalidade (entrega ou retirada)")
        if not state.data_agendada:
            dados_faltando.append("data")
        if not state.horario:
            dados_faltando.append("horario")
        if state.modality == "entrega" and not state.bairro:
            dados_faltando.append("bairro")
        if state.modality == "entrega" and not state.endereco:
            dados_faltando.append("endereco")

        # SE TODOS OS DADOS ESTAO COMPLETOS, avise para apresentar resumo imediatamente
        if not dados_faltando and state.items and state.modality:
            prompt += (
                "\n\n✅ TODOS OS DADOS OBRIGATORIOS ESTAO COMPLETOS!"
                "\nNAO PERGUNTE nada ao cliente. Va DIRETO para chamar calculate_order e apresentar o RESUMO do pedido."
                "\nNAO diga 'deixa eu confirmar', NAO repita os dados, NAO faca perguntas."
                "\nAPRESENTE O RESUMO IMEDIATAMENTE apos a validacao com calculate_order."
            )
        elif dados_faltando:
            prompt += f"\n\n⚠️ DADOS OBRIGATORIOS FALTANDO: {', '.join(dados_faltando)}"
            prompt += "\nNAO apresente resumo SEM TODOS os dados acima. Pergunte ao cliente os dados que faltam."

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
                                "description": "Lista de itens com nome, quantidade e preco UNITARIO por unidade (ex: Coxinha=0.80, NAO 20.00)",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "quantity": {"type": "integer"},
                                        "price": {"type": "number", "description": "Preco por UNIDADE, nao por porcao"},
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
                # So atualiza frete se o novo valor for > 0 ou se ainda nao foi calculado
                new_fee = result.get("delivery_fee", 0)
                if new_fee > 0 or state.frete == 0:
                    state.frete = new_fee
                state.total = result.get("total", 0)
                # Calcula taxa de retorno baseado no pagamento
                if state.payment_method in ("dinheiro", "cartao", "cartao_debito", "cartao_credito"):
                    state.taxa_retorno = 2.00
                else:
                    state.taxa_retorno = 0.00
            return json.dumps(result, ensure_ascii=False)

        return {"calculate_order": _calculate}
