# Sandrinha (BALBS Salgados) — instruções para agentes

Atendente de WhatsApp da fábrica de salgados do Balbino. Produto, fluxos e regras de negócio: `PRODUCT.md`. Arquitetura: `docs/architecture.md`. Qualidade e testes: `docs/quality.md`. Histórico de configuração do toolkit: `docs/setup.md`.

## Stack

- Python (3.11+; o ambiente local usa a `.venv`), Flask + Flask-Limiter, APScheduler.
- Banco: Supabase (PostgreSQL com pgvector, `DB_DRIVER=postgresql`). O padrão do código é `sqlite`, mas o esquema usa sintaxe de PostgreSQL (`SERIAL`, `NOW()`) e o app não inicializa em SQLite (verificado em 23/10/2026).
- LLM: OpenAI (`OPENAI_MODEL`, `OPENAI_TEMPERATURE`). Gateway WhatsApp: AutoZap, a API própria (`AUTOZAP_*`). A Datafy saiu em 26/10/2026.
- Não há Node, Next.js, ORM, CI nem gates de lint configurados.

## Comandos

- Instalar: `pip install -r requirements.txt` (dentro da `.venv`)
- Rodar o painel e o webhook: `python app.py` (porta `PORT`, padrão 5000)
- Teste determinístico (sem LLM, rede ou banco; custo zero): `python test_regressao.py`. Termina com `FALHAS: nenhuma` quando passa.
- Compilação: `python -m compileall -q .`
- Lint (gate mínimo, `ruff.toml`: erro de sintaxe e nome indefinido): `uvx ruff@0.9.10 check .`
- Rodar o app localmente carrega o `.env` real (`load_dotenv(override=True)`): banco, AutoZap e OpenAI de produção. Para teste, use uma cópia sem `.env` e variáveis sintéticas.

## Não rodar sem autorização explícita

Estes scripts gastam OpenAI, criam dados reais ou tocam sistemas de produção:

- `test_agente.py`, `simular_dono_conversas.py`, `rodar_roteiro.py`, `rodar_chat_26_09.py`, `rodar_sem_invencao.py`, `medir_oscilacao.py`: chamam o modelo (custo). `rodar_roteiro.py` também cria comandas reais na fábrica.
- `verificar_credencial.py`: faz POST em `createOrderFromAgent` do cardápio digital.
- `conferir_cardapio.py`: lê o cardápio digital de produção.

## Restrições

- Não envie segredos, conversas de clientes (`chat*.txt`, `uploads/`), `fixtures/` nem o `.env` para serviços externos, inclusive consultas de documentação e extração de grafo.
- Não altere comportamento do agente, prompts guardados no banco ou regras de preço sem pedido explícito. A fonte de preços é o cardápio digital, não o código.
- Não faça commit, push ou publicação sem pedido.
- Não crie abstrações ou dependências para necessidades não pedidas.
- Mudança de comportamento: teste de regressão antes e depois (ver `docs/quality.md`).
- Texto que a cliente lê é o que o Balbino cobra. Ao escrever respostas do agente, siga `docs/design.md`.

## Mapa

- Produto e regras: `PRODUCT.md`
- Módulos e fluxo de mensagem: `docs/architecture.md`
- Como rodar e verificar: `docs/onboarding.md`
- Qualidade, testes e grafo: `docs/quality.md`
- Configuração do setup e decisões: `docs/setup.md`
- Estilo de texto e painel: `docs/design.md`
- Grafo do código (regerável com `graphify extract . --code-only --no-cluster`): `graphify-out/` (ignorado pelo git)
