"""
pedido.py — Pedido Agent.
Monta pedido, valida com calculate_order e apresenta resumo.
"""

import json
import llm
import cardapio
import knowledge_rag
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
        if any(
            marca in response
            for marca in ("Posso confirmar", "confirmar o seu pedido", "Confirma o pedido")
        ):
            state.resumo_apresentado = True

        return response

    def _build_system_prompt(self, state: ConversationState) -> str:
        prompt = self._load_prompt()

        if state.knowledge_context:
            prompt += "\n\n=== BASE OFICIAL DA EMPRESA ===\n" + state.knowledge_context

        if state.name:
            prompt += f"\n\nCliente: {state.name}"

        if state.items:
            prompt += "\n\n=== ITENS DO PEDIDO ==="
            for item in state.items:
                prompt += f"\n- {item.get('quantity', 0)} un - {item.get('name', '?')} (R$ {item.get('price', 0):.2f}/un)"
            prompt += "\n⚠️ USE EXATAMENTE estes itens, nomes e precos no resumo. NAO invente outros produtos."

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
        if state.taxa_retorno > 0:
            prompt += f"\nTaxa de retorno: R$ {state.taxa_retorno:.2f} (INCLUIR no resumo - SE obrigatorio para cartao/dinheiro)"

        # Status dos dados obrigatorios
        # Uma lista so, vinda do estado: a ordem das perguntas (modalidade
        # antes de endereco, data antes de horario) e a mesma em todo agente.
        dados_faltando = state.dados_faltando()

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

        # Calendario resolvido + diretivas da vez, sempre no fim do prompt.
        prompt += state.bloco_runtime()

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
                    "description": "Consulta a base oficial para precos, produtos, frete e regras antes de validar um pedido.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Busca descritiva"},
                            "filename": {"type": "string", "description": "Nome exato do arquivo"},
                        },
                    },
                },
            },
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
            # A tool se recusa a fechar conta com dado faltando. O prompt ja
            # dizia isso; como instrucao ela era ignorada, como retorno de tool
            # nao ha resumo para o modelo copiar.
            faltando = state.dados_faltando()
            if faltando:
                return json.dumps({
                    "success": False,
                    "erro": "DADOS_INCOMPLETOS",
                    "faltando": faltando,
                    "instrucao": (
                        f"NAO apresente resumo nem valores. Pergunte ao cliente "
                        f"apenas: {faltando[0]}. Uma pergunta so."
                    ),
                }, ensure_ascii=False)

            result = cardapio.calcular_pedido(
                items, bairro or state.bairro,
                modalidade=state.modality,
                dia=state.data_agendada,
                horario=state.horario,
                pagamento=state.payment_method,
                cliente=state.name,
                endereco=state.endereco,
            )
            if result.get("success"):
                state.subtotal = result.get("subtotal", 0)
                # So atualiza frete se o novo valor for > 0 ou se ainda nao foi calculado
                new_fee = result.get("delivery_fee", 0)
                if new_fee > 0 or state.frete == 0:
                    state.frete = new_fee
                # A regra da taxa de retorno mora em cardapio.calcular_taxa_retorno,
                # para nao existir em duas versoes que divergem com o tempo.
                state.taxa_retorno = result.get("return_fee", 0)
                state.total = state.subtotal + state.frete + state.taxa_retorno

                # O resumo vem pronto de cardapio.montar_resumo, no mesmo
                # formato do BLOCO 5 do prompt operacional. Existir um
                # segundo formato aqui era o que fazia o modelo misturar os
                # dois e reapresentar o resumo depois do 'sim' do cliente.
                result["subtotal"] = state.subtotal
                result["delivery_fee"] = state.frete
                result["return_fee"] = state.taxa_retorno
                result["total"] = state.total

            return json.dumps(result, ensure_ascii=False)

        def _search(query: str = "", filename: str = "", **kwargs) -> str:
            if filename:
                return knowledge_rag.get_file_content(filename.strip()) or "Arquivo nao encontrado."
            if not query.strip():
                return "Forneca uma busca descritiva."
            return knowledge_rag.search_context(query.strip(), top_k=5)

        return {"calculate_order": _calculate, "search_knowledge": _search}
