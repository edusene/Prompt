# Arquitetura

Visão de módulos e do fluxo de mensagem. Detalhe de função: `graphify-out/graph.json` (regerável, ignorado pelo git) ou `graphify query`.

## Fluxo de uma mensagem

1. Meta/Datafy envia o evento a `POST /webhook` (`app.py`). A assinatura é checada com `DATAFY_WEBHOOK_SECRET`.
2. `buffer.py` agrupa mensagens seguidas do mesmo cliente (debounce de `BUFFER_SECONDS`).
3. `handle_flush` / `_handle_flush` (`app.py`) monta o contexto: estado da conversa (`conversation_memory.py`), fatos (`facts.py`) e base de conhecimento (`knowledge_rag.py`).
4. `agents/` decide a etapa: `router` classifica a intenção; `triagem`, `pedido`, `cardapio`, `pagamento`, `logistica`, `crise` e `conversation` cuidam de cada etapa. Os prompts ficam em `prompts/*.md`.
5. Cálculos e regras determinísticas ficam fora do modelo: valores e frete em `cardapio.py` e `facts.py`, horário em `horario.py`, CEP em `cep.py`.
6. A resposta é enviada pelo gateway (`meta.py`), gravada no banco (`database.py`) e publicada no painel (`events.py`, SSE em `/api/events`).

## Módulos

| Área | Arquivos | Papel |
|---|---|---|
| Entrada e painel | `app.py` (rotas `/webhook`, `/panel`, `/api/*`), `templates/`, `static/` | Webhook, painel CRM, configurações, envio manual |
| Autenticação | `auth.py` | Login do painel (`login_required`) |
| Buffer | `buffer.py` | Debounce de mensagens |
| Agentes | `agents/`, `prompts/` | Roteamento e etapas do atendimento |
| Memória e estado | `conversation_memory.py`, `memory.py` | Histórico e estado do pedido |
| Cardápio digital | `cardapio.py`, `cardapio_catalogo.py`, `cardapio_fonte.py`, `conferir_cardapio.py`, `verificar_credencial.py` | Produtos, preços e envio de pedido ao cardápio digital |
| Regras de negócio | `facts.py`, `horario.py`, `cep.py`, `config_dono.py` | Frete, expediente, CEP e configurações do dono |
| Conhecimento | `knowledge_rag.py`, `parse_knowledge.py`, `knowledge_repair.py`, `reindex_knowledge.py` | Base de conhecimento com embeddings |
| Banco | `db.py` (conexão e SQL genérico), `database.py` (esquema e operações) | SQLite ou PostgreSQL |
| Gateway | `meta.py` | Envio pela Datafy/Meta Cloud |
| Agendamento | `scheduler.py` (follow-ups), `volta_automatica.py` (retorno ao modo automático) | Jobs em background (APScheduler) |
| Testes e simulação | `test_regressao.py`, `test_agente.py`, `simular_dono*.py`, `rodar_*.py`, `medir_oscilacao.py` | Ver `docs/quality.md` |

## Persistência

- Em uso: PostgreSQL com pgvector (`DB_DRIVER=postgresql`, `DATABASE_URL`). Migração descrita em `SUPABASE_MIGRATION.md`.
- O padrão do código ainda é SQLite (`DB_PATH`, padrão `whatsapp.db`), mas `init_db` falha nele: o esquema usa `SERIAL` e `DEFAULT NOW()`, que só existem no PostgreSQL (`sqlite3.OperationalError: near "(": syntax error`, verificado em 23/10/2026). Consertar é mudança de banco, ainda não decidida.
- Tabelas principais: `conversations`, `messages`, `contacts`, `conversation_states`, `cardapio_orders`, `knowledge`, `knowledge_files`, `settings`, `crm_follow_ups`, `crm_logs`, `templates`, `tasks`, `tags`.
- `settings` guarda configurações editáveis pelo painel. O painel também regrava o `.env` com os valores enviados (`_write_env_file` em `app.py`).

## Configuração

- Variáveis em `.env.example`. Não versionar o `.env`.
- Precedência do cardápio: banco primeiro, ambiente depois, depois a URL padrão de segurança (`cardapio._config()`).

## Pontos de atenção

- `app.py` tem mais de 10 mil linhas. Divisão em módulos é proposta separada, não parte do setup.
- O painel grava segredos no `.env` a partir de requisição HTTP. Revisar na análise de segurança.
- Chamadas ao modelo e ao cardápio digital têm custo ou efeito externo. Não as dispare em testes sem autorização.
