# Qualidade, testes e verificação

Estado medido em 23/10/2026. Decisões e pendências em `docs/setup.md`.

## Comandos e status

| Verificação | Comando | Custo / efeito | Status na linha de base |
|---|---|---|---|
| Regressão determinística | `.venv\Scripts\python.exe test_regressao.py` | Nenhum (sem LLM, rede ou banco) | Passou: `FALHAS: nenhuma` |
| Compilação | `.venv\Scripts\python.exe -m compileall -q .` | Nenhum | Passou (exit 0) |
| Lint (medição, não gate) | `uvx ruff@0.9.10 check . --exclude .venv,graphify-out --statistics` | Nenhum; não instala no projeto | 102 violações, detalhe abaixo |
| Simulação sem modelo | `.venv\Scripts\python.exe simular_dono.py` | Nenhum (camada 1) | Não rodado nesta etapa |
| Conversa com modelo | `test_agente.py`, `simular_dono_conversas.py`, `rodar_*.py`, `medir_oscilacao.py` | **Gasta OpenAI**; alguns criam comandas reais | Não rodados (sem autorização) |
| Roteiro manual | `TESTES_SANDRINHA.md`, pelo celular | Cria comandas reais | Não rodado |

Não há pytest, ruff, CI nem gate configurados. Nenhum gate foi adotado nesta etapa.

## Lint medido (ruff 0.9.10, regras padrão)

| Regra | Ocorrências | Corrigível | Observação |
|---|---|---|---|
| E402 module-import-not-at-top | 51 | não | Imports após código, comum em scripts |
| F401 unused-import | 14 | sim | |
| E741 ambiguous-variable-name | 11 | não | |
| F841 unused-variable | 8 | não | |
| E702 multiple-statements | 7 | não | |
| F541 f-string sem placeholder | 7 | sim | |
| E401, E731 | 2 + 1 | sim | |
| F821 undefined-name | 1 | não | Corrigido em 23/10/2026, ver abaixo |

Regra proposta, não aplicada: manter as violações como medição. Antes de qualquer gate, reduzir F401, depois decidir sobre E402 (pode exigir reorganizar imports de `app.py`).

## Defeito corrigido na bateria (23/10/2026)

A checagem "mensagem padrao de transferencia sem 'atendente humano'" usava `os` sem importá-lo e testava `"TRANSFER_MESSAGE" in os.environ`. Com a mensagem padrão sem "humano", o `or` curto-circuitava e a checagem passava sem testar nada. Com "humano", a bateria quebrava com `NameError` em vez de reprovar.

Correção: `import os` no topo de `test_regressao.py` e a condição passou a usar `bool(os.getenv("TRANSFER_MESSAGE"))`, a mesma regra da checagem de `TEXTO_DESARME`. Verificado por mutação: mensagem com "humano" e sem override reprova; com override, passa. A bateria segue sem falhas.

## Harness: linha de base do fixture

Smoke case do procedimento nativo (`docs/prompts/harness-improvement.md`), rodado em 23/10/2026:

- Toolkit: commit `790a895304e0c08b70a5b215c9d334f1c86b816c`. Avaliador `scripts/validate-harness.mjs` com fixture `templates/harness-evaluation/`, em cópia descartável fora do projeto.
- Resultado: 5 de 7 passaram. Falharam, como esperado, "denies matching user IDs across tenants" e "denies missing, empty, or non-string identities".
- Não executado: casos com agente (boundary fix, stale handoff, revisão somente leitura, reaplicação, conflito de recursos, bloqueio parcial, pausa e retomada). Exigem sessão de agente observável e não foram rodados.

Nenhuma instrução de trabalho foi alterada com base nesse resultado, porque não há falha observada no harness do projeto.

## Revisão de segurança

Procedimento reutilizável em `docs/prompts/security-review.md`. Executado em 23/10/2026 em modo repositório, só leitura. O relatório é privado e fica fora do repositório.

Achados, nenhum corrigido ainda:

- **S1 (média):** `_write_env_file` não remove quebras de linha do valor. Com sessão do painel, um valor multilinha injeta qualquer chave no `.env`.
- **S2 (baixa a média):** `toast()` em `static/app.js` monta HTML sem escape e recebe nomes de arquivo enviados pelo painel.
- **S3 (baixa, depende de configuração):** sem `DATAFY_WEBHOOK_SECRET`, o webhook aceita mensagem sem assinatura.

Sem achado no escopo lido: SQL com f-string (colunas de listas fixas), autenticação das rotas `/api/*` e login.

## Grafo

- Gerado com `graphify extract . --code-only --no-cluster`, sem chamada de modelo. 44 arquivos de código, 1893 nós, 4811 arestas. Docs e dados de negócio excluídos por `.graphifyignore`.
- Sem clustering e sem extração semântica: documentos (`PRODUCT.md`, `README.md`) não entram no grafo.
- Consulta testada: `graphify query` e `graphify explain` funcionam, mas o `explain` pede id completo quando há nomes repetidos, e as consultas truncam por orçamento.
- Regerar após mudanças grandes: mesmo comando. Não há hook de atualização automática.
