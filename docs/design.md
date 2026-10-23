# Design e texto

Este projeto não tem UI de produto, mas tem duas superfícies que pedem critérios próprios:

1. **Texto que a cliente lê** (WhatsApp).
2. **Painel do dono** (`templates/index.html`, `static/app.js`, `static/style.css`).

## Texto da Sandrinha

Os critérios abaixo vêm das conversas de teste do dono e são verificados pela bateria em `test_regressao.py`.

- Português simples, tom de atendente de fábrica. Sem jargão de programação.
- Uma pergunta por mensagem, no fim.
- Não inventar dado. Se não sabe, diz que não sabe ou encaminha.
- Valores e prazos só vêm do cardápio digital e das tabelas oficiais.
- Não pedir de novo o que a cliente já informou.
- Negativa clara para fiado e desconto.
- Não oferecer produto fora do cardápio ativo.

## Painel

- Entrada em português. Ações de risco (limpar conversa, reset, envio em massa) pedem confirmação.
- Valores de segredo aparecem mascarados. Nenhuma tela deve exibir a chave completa.
- Layout responsivo para uso no celular, porque o dono testa pelo celular (`TESTES_SANDRINHA.md`).
- Visual atual é o do `static/style.css`. Não há design system adotado; shadcn e Tailwind não se aplicam.

## Verificação

- Texto: `test_regressao.py` (determinístico) e, manualmente, o roteiro em `TESTES_SANDRINHA.md`, pelo celular.
- Painel: verificação local com navegador, sem usar sessões pessoais nem produção. Ainda não configurada nesta etapa.
