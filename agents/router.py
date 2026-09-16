"""
router.py — Router Agent.
Classifica intencao e seleciona agente ativo.
"""

import json
import llm


ROUTER_SYSTEM_PROMPT = """Voce e o ROTEADOR do chatbot da BALBS SALGADOS.

Analise a mensagem do cliente e o contexto da conversa para classificar e rotear.

## Saidas (JSON)

INTENCAO:
- greeting: Saudacao, inicio de conversa
- product_inquiry: Pergunta sobre produto, preco, sabor, porcao
- order_build: Montar pedido, adicionar itens, confirmar quantidade
- delivery_question: Entrega, frete, bairro, endereco
- payment_question: Pagamento, Pix, cartao, comprovante
- complaint: Reclamacao, problema, insatisfacao, atraso
- schedule: Agendar pedido para depois
- confirm: Confirmacao simples (sim, ok, pode ser, isso)
- other: Qualquer outra coisa

FASE:
- triagem: Inicio, coleta de nome
- cardapio: Produtos, precos, porcoes
- pedido: Montagem, quantidades, resumo
- logistica: Entrega/retirada, endereco, horario, frete
- pagamento: Pagamento, confirmacao final, envio
- crise: Reclamacao grave, escalada, cancelamento

AGENTE:
- triagem, cardapio, pedido, logistica, pagamento, crise

Formato:
{
  "intencao": "...",
  "fase": "...",
  "agente": "...",
  "dados_necessarios": [...],
  "confianca": 0.95
}

Exemplos:
"Oi" -> {"intencao": "greeting", "fase": "triagem", "agente": "triagem", "dados_necessarios": ["nome"], "confianca": 0.99}
"Quanto custa?" -> {"intencao": "product_inquiry", "fase": "cardapio", "agente": "cardapio", "dados_necessarios": [], "confianca": 0.95}
"Quero 30 coxinhas" -> {"intencao": "order_build", "fase": "cardapio", "agente": "cardapio", "dados_necessarios": ["sabor", "porcao"], "confianca": 0.9}
"sim" -> {"intencao": "confirm", "fase": "pedido", "agente": "pedido", "dados_necessarios": [], "confianca": 0.95}
"E pra entrega" -> {"intencao": "delivery_question", "fase": "logistica", "agente": "logistica", "dados_necessarios": ["bairro", "endereco"], "confianca": 0.95}
"Meu pedido atrasou" -> {"intencao": "complaint", "fase": "crise", "agente": "crise", "dados_necessarios": [], "confianca": 0.9}

Regras:
- Se ultima msg do bot terminou com "?", provavel e confirmacao
- Se cliente ja tem itens e pede mais, e order_build no cardapio
- Se ja tem endereco e esta confirmando, e confirm na fase logistica/pagamento
- Reclamacoes sempre vao para crise
"""


def classify_intent(message: str, state: dict) -> dict:
    """Classifica a intencao do cliente."""

    context = f"""CONTEXTO:
- Nome: {state.get('name', 'nao informado')}
- Fase: {state.get('phase', 'triagem')}
- Itens: {state.get('items', [])}
- Modalidade: {state.get('modality', 'nao definida')}
- Ultima msg bot: {state.get('last_bot_message', '')[:200]}

MENSAGEM: {message}"""

    messages = [
        {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
        {"role": "user", "content": context},
    ]

    try:
        response = llm._client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_completion_tokens=200,
            temperature=0.1,
        )

        text = response.choices[0].message.content.strip()

        # Remove markdown code block se presente
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        return json.loads(text)
    except Exception as e:
        print(f"[router] Erro: {e}")
        return {
            "intencao": "other",
            "fase": state.get("phase", "triagem"),
            "agente": state.get("phase", "triagem"),
            "dados_necessarios": [],
            "confianca": 0.5,
        }
