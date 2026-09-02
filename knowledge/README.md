# Knowledge Base do Agente

Coloque arquivos `.md` ou `.txt` nesta pasta para alimentar a memória do agente.

## Como funciona

- Todo arquivo `.md` ou `.txt` dentro desta pasta é lido automaticamente na inicialização.
- O conteúdo de todos os arquivos é concatenado e injetado no `system instruction` do Gemini.
- Arquivos são lidos em ordem alfabética — use prefixos numéricos para controlar a ordem:
  - `00-empresa.md` — informações gerais da empresa
  - `01-produtos.md` — lista de produtos/serviços
  - `02-valores.md` — valores, missão, visão
  - `03-faq.md` — perguntas frequentes

## Exemplo de estrutura

```
knowledge/
├── 00-empresa.md      # Quem somos, história, contato
├── 01-produtos.md     # Catálogo de produtos/serviços
├── 02-valores.md      # Missão, visão, valores
├── 03-faq.md          # Perguntas frequentes dos clientes
└── README.md          # Este arquivo
```

## Dicas

- Mantenha cada arquivo focado em um tema.
- Use linguagem clara e direta — o agente vai "ler" isso como contexto.
- Evite arquivos muito grandes (>50k chars pode impactar performance).
- Para atualizar a memória, edite os arquivos e reinicie o app.
- Não use caracteres especiais ou markdown complexo — texto simples funciona melhor.
