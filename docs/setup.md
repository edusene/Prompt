# Setup do toolkit

- **Data:** 23/10/2026
- **Toolkit:** `soumatheusgomes/vibe-coding-toolkit`, commit `790a895304e0c08b70a5b215c9d334f1c86b816c` (19/10/2026), branch `main`. Todos os guias e templates usados vêm desse commit.
- **Cliente:** Claude Code 2.1.308, escopo de usuário, autenticação e modelo já existentes (não alterados). Modelo desta sessão: Haiku 5.5.
- **Aprovação do desenho:** 23/10/2026, com escopo "só documentação, configuração de ferramentas e testes; código da aplicação não muda".
- **Backup antes de alterar:** bundle do git (`repo-pre-setup.bundle`) e cópia da memória, em `C:\Users\Usuario\AppData\Local\Temp\claude\C--Users-Usuario-Documents-Projetos-AgenteBalbs-Prompt\27a0e114-4612-46bd-a66a-8d360d7d0302\scratchpad\backup\`. Também cópia de `~/.claude/settings.json` anterior ao Graphify. Os segredos (`.env`) não entram no backup do repositório.

## Limitação de ambiente

Nesta sessão o cliente não recarrega plugins nem MCPs recém-instalados. Tudo que está marcado como "configurado" precisa ser confirmado numa sessão nova.

## Cobertura

Legenda: **Validado** = executado e observado. **Configurado** = instalado, não observado em uso. **Reaproveitado** = já existia. **Não aplicável** = motivo informado. **Pendente** = bloqueado, com condição para retomar.

| Componente | Estado | Ação e escopo | Evidência / motivo |
|---|---|---|---|
| Documentação canônica (PRODUCT, AGENTS, CLAUDE, docs/architecture, design, onboarding, quality, setup) | Validado (conteúdo) | Criados no projeto | Fatos conferidos no código; divergências do README listadas abaixo |
| README | Preservado | Não reescrito | Divergências registradas, não corrigidas |
| Superpowers (brainstorming, planejamento, revisão) | Configurado | Plugin `superpowers@claude-plugins-official` v7.0.0, escopo usuário, habilitado | Instalado; carregamento em sessão nova pendente |
| Graphify | Validado (CLI) | `graphifyy` 0.9.95 via `uv tool install` (isolado); skill `graphify` em `~/.claude/skills/` via `graphify install --platform claude` (sem `--project`, sem hook) | `graphify extract --code-only` e `query` funcionaram; `.graphifyignore` no projeto; `graphify-out/` no `.gitignore` |
| Context7 | Pendente (autenticação) | Plugin `context7@claude-plugins-official` habilitado; MCP `plugin:context7:context7` aparece como "Needs authentication" | Condição: autenticar o MCP pelo fluxo do cliente (`/mcp`) com a conta do usuário. Credencial fica fora do repositório. Consulta real pendente |
| Ponytail | Configurado | Plugin `ponytail@ponytail` v5.3.2 | Opções `webhookUrl`, `webhookToken`, `ledger`, `sendNames` deixadas vazias: enviariam dados para fora |
| Caveman | Configurado | Plugin `caveman@caveman` v4.0.1 | Aviso do instalador: pacotes de yarn/pnpm não instalados. Efeito pendente de verificação |
| differential-review | Validado (carregada) | Skill do projeto em `.claude/skills/differential-review/` (Trail of Bits, `main`). Conteúdo ignorado pelo git; `skills-lock.json` versionado com origem e hash | Aparece na lista de skills após reinício. Reinstalar: `npx --yes skills@latest add https://github.com/trailofbits/skills/tree/main/plugins/differential-review --skill differential-review --agent claude-code --yes` |
| supabase-postgres-best-practices | Reaproveitado | Já existia em `~/.claude/skills/`; não duplicado | Plugin `supabase@supabase-agent-skills` já estava instalado e desabilitado; não alterado |
| Harness2 (adaptação nativa, prompt 15) | Validado (smoke) | Procedimento em `docs/prompts/harness-improvement.md` | Linha de base do fixture: 5 de 7 checks, como esperado. Casos com agente não rodados |
| Revisão de segurança (prompt 13) | Validado (execução) | Procedimento em `docs/prompts/security-review.md` | Executada em 23/10/2026, modo repositório, só leitura. 3 achados, relatório privado fora do repositório |
| Gates de qualidade Python | Validado | `ruff.toml` com E9, F63, F7, F82 (zero violações hoje), via `uvx ruff@0.9.10` | Passa no projeto; reprova arquivo descartável com nome indefinido. Demais regras só medidas |
| RTK | Validado (hook ativo) | `rtk.exe` 1.0.1 do release oficial (SHA-256 conferido com `checksums.txt`) em `~/.local/bin`; `rtk init -g --auto-patch`: hook `PreToolUse` (Bash) no `settings.json`, `~/.claude/RTK.md` e `@RTK.md` no `CLAUDE.md` global | `rtk git status` funciona; `rtk hook claude` reescreve `git status` para `rtk git status`. Na mesma sessão, `ls` já saiu no formato compacto do RTK. Desfazer: `rtk init -g --uninstall` |
| agent-browser | Pendente (não funcional) | npm global 0.39.2 (Apache-2.0); `postinstall` revisado e rodado (baixa o binário nativo do repositório oficial); usa o Chrome do sistema com perfil temporário | `agent-browser open` em página local travou 3 vezes (mais de 90 s, sem saída). Condição: investigar o daemon no Windows antes de usar |
| chrome-devtools-mcp | Não instalado | Plugin oficial disponível | Alternativa ao agent-browser para o painel. Não instalado para não duplicar sem antes saber por que o agent-browser travou |
| Hooks do cliente | Não aplicável | Nenhum criado | Sem necessidade comprovada; o instalador do Graphify não criou hook |
| Sentry | Não aplicável | Não adotado | Não há serviço escolhido nem organização autorizada |
| gh-fix-ci | Não aplicável | — | Não há GitHub Actions; `gh` não está instalado |
| Skills Next.js / shadcn / Vercel web / Drizzle / ESLint JS | Não aplicável | — | Stack Python/Flask, sem frontend JS de framework |
| Obsidian | Não aplicável | — | Memória já existe no cliente; sem vault necessário |
| Harness2 CLI oficial (laboratório) | Pendente | Não instalado | Exige autorização para dependências, modelo, custo e isolamento |

## Memória e instruções

- Criado `~/.claude/CLAUDE.md` (global, pelo Graphify): 3 linhas, só o gatilho `/graphify`. Não há hook.
- Projeto: `AGENTS.md` (instruções compartilhadas) e `CLAUDE.md` (`@AGENTS.md` + detalhes do Claude Code).
- Memória existente (6 arquivos) não foi alterada.

## Mudanças no projeto

- Novos: `AGENTS.md`, `CLAUDE.md`, `PRODUCT.md`, `.graphifyignore`, `docs/architecture.md`, `docs/design.md`, `docs/onboarding.md`, `docs/quality.md`, `docs/setup.md`, `docs/prompts/security-review.md`, `docs/prompts/harness-improvement.md`, `.claude/skills/differential-review/`, `skills-lock.json`.
- Alterados: `.gitignore` (inclui `graphify-out/` e `.claude/settings.local.json`); `README.md` (seção de documentação adicionada).
- Gerado localmente, ignorado pelo git: `graphify-out/`.
- Nada foi commitado, enviado ou publicado.

## Divergências do README (a confirmar com o dono)

- Modo de operação: Meta Cloud direta ou Coexistence.
- Modelo padrão: README diz `gpt-4o-mini`.
- Planilha: citada no README, mas removida como fonte em 02/10/2026.
- `verificar_catalogo.py`: citado no README, ausente do repositório.

## Pendências de código (propostas, não aplicadas)

1. `app.py` (mais de 10 mil linhas), `database.py`, `conversation_memory.py`, `test_regressao.py`: divisão por responsabilidade, fora do setup.
2. Achados S1, S2 e S3 da revisão de segurança de 23/10/2026 (`docs/quality.md`): correção exige instrução separada.

A asserção fraca de `test_regressao.py` foi corrigida em 23/10/2026.

## Decisões em aberto

- S3: confirmar no Render que `DATAFY_WEBHOOK_SECRET` existe; depois, fazer o webhook falhar fechado.
- SQLite quebrado: consertar o esquema para os dois bancos ou tirar o SQLite como padrão. É mudança de banco.
- agent-browser travando no Windows: investigar ou trocar pelo chrome-devtools-mcp.
- Se o dono quiser CI ou Sentry, são decisões separadas.

## Mudanças de máquina feitas pelo setup

- Plugins de escopo usuário: superpowers, context7 (autenticado), ponytail, caveman.
- `~/.claude/skills/graphify/`, `~/.claude/CLAUDE.md` (graphify + `@RTK.md`), `~/.claude/RTK.md`, hook do RTK no `~/.claude/settings.json` (backup em `settings.json.bak`).
- `uv tool`: graphifyy. `~/.local/bin/rtk.exe`. npm global: agent-browser.

## Como repetir este setup

1. Verificar `git status` e preservar mudanças locais.
2. Rodar `.venv\Scripts\python.exe test_regressao.py`.
3. Reaplicar só o que estiver pendente. Não reinstalar plugins já presentes.
4. Atualizar esta página com a data e o commit do toolkit usados.
