# Produto: Sandrinha (BALBS Salgados)

## O que é

Atendimento de WhatsApp da fábrica de salgados BALBS, do Balbino. A Sandrinha conversa com clientes, monta o pedido, calcula valores e frete, confirma data, horário e pagamento, e envia o resumo para a fábrica. Quando a conversa sai do que ela resolve, passa para um atendente humano.

Há também um painel CRM para o dono acompanhar conversas, assumir atendimentos e ajustar configurações.

## Quem usa

- **Cliente final:** pede pelo WhatsApp.
- **Balbino (dono):** usa o painel. Ajusta frases, produtos ligados ou bloqueados e o modo de atendimento. Preços vêm do cardápio digital, não do painel.
- **Equipe da fábrica:** recebe o resumo do pedido e os encaminhamentos.

## Fluxos principais

1. Pedido: itens, quantidades, tamanhos e sabores, com validação de lotes (cento, meio cento, unidades).
2. Oferta de acompanhamento, conforme a faixa de horário.
3. Entrega: endereço, bairro e taxa pela tabela oficial. Retirada não tem pagamento nem troco.
4. Data e horário: respeita o expediente e os prazos da fábrica.
5. Pagamento e troco.
6. Resumo e fechamento só com confirmação explícita da cliente.
7. Encaminhamento para atendente (reclamação, desvio, pedido de humano).

Detalhes operacionais e exemplos de regra estão em `README.md`, seção 11.

## Fontes de verdade

- **Preços e produtos:** cardápio digital (Base44), cópia interna usada para cobrar. A planilha foi removida em 02/10/2026 e não é mais fonte. O código e partes históricas do README ainda dizem "planilha" para a tabela de produtos do cardápio digital.
- **Regras de frete, horário e FAQ:** `facts.py` e `horario.py`, e a base de conhecimento (`knowledge_rag.py`).
- **Comportamento do agente:** prompts em `prompts/*.md` e os blocos guardados no banco, editáveis pelo painel.

## Restrições do negócio

- O agente não inventa dados (endereço, preço, prazo, telefone ou nome do dono). Perguntas fora da base são encaminhadas ou respondidas com "não sei".
- Cliente não pode ser cobrado por valor diferente do cardápio digital.
- Fora do expediente, a Sandrinha avisa e oferece outro horário.
- Respostas devem terminar com uma única pergunta.

## Escopo deste repositório

Cobre o agente, o webhook, o painel e os scripts de teste e simulação. Não cobre o cardápio digital nem o cadastro na Meta/Datafy, que são sistemas externos.

## Operação

- WhatsApp em modo Coexistence, via Datafy (`TESTES_SANDRINHA.md`).
- Banco: Supabase (PostgreSQL).
- Modelo: `OPENAI_MODEL`, padrão do código `gpt-4.1-mini`.
