# WhatsApp AI Agent + CRM Panel — Flask + UAZAPI + Gemini

Agente de IA no WhatsApp com painel CRM completo. Usa a [UAZAPI](https://www.uazapi.com) como gateway e Google Gemini 2.5 Flash como LLM. Mensagens são agrupadas via debounce e respostas chegam divididas em múltiplas mensagens.

---

## 1. Instalação

```bash
pip install -r requirements.txt
```

---

## 2. Configuração

```bash
cp .env.example .env
```

Edite `.env` e preencha:

| Variável | Descrição |
|---|---|
| `UAZAPI_BASE_URL` | URL do seu servidor UAZAPI |
| `UAZAPI_INSTANCE_TOKEN` | Token da instância |
| `WEBHOOK_URL` | URL pública do seu servidor (ngrok/localtunnel) — ex: `https://abc-123.ngrok-free.app/webhook` |
| `GEMINI_API_KEY` | API Key do Google AI Studio — obtenha **grátis** em https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | Modelo Gemini (padrão: `gemini-2.5-flash`) |
| `BUFFER_SECONDS` | Segundos de debounce (padrão: `8`) |
| `MAX_HISTORY` | Máximo de mensagens no histórico (padrão: `20`) |
| `SYSTEM_PROMPT` | Instrução de sistema base para o LLM |
| `SYSTEM_PROMPT_DIR` | Pasta com arquivos .md/.txt de conhecimento (padrão: `knowledge`) |
| `PORT` | Porta do Flask (padrão: `5000`) |
| `DB_PATH` | Caminho do SQLite (padrão: `whatsapp.db`) |
| `ADMIN_USER` | Usuário do painel (padrão: `admin`) |
| `ADMIN_PASSWORD` | Senha do painel |
| `SECRET_KEY` | Chave secreta para sessões Flask |

---

## 3. Rodando

```bash
python app.py
```

---

## 4. Expondo na internet

```bash
ngrok http 5000
```

---

## 5. Configurando webhook na UAZAPI

```bash
curl -X PUT "$UAZAPI_BASE_URL/webhook" \
  -H "token: $UAZAPI_INSTANCE_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"webhookUrl\": \"$WEBHOOK_URL\", \"events\": [\"message\"]}"
```

**Ou manualmente** — substitua os valores do `.env`:
```bash
curl -X PUT https://SEU-DOMINIO.uazapi.com/webhook \
  -H "token: SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"webhookUrl": "https://SEU-NGROK.ngrok-free.app/webhook", "events": ["message"]}'
```

---

## 6. Deploy no Render

### Variáveis de ambiente (Environment Variables)
No painel do Render, adicione todas as variáveis do `.env`:

| Variável | Valor |
|---|---|
| `UAZAPI_BASE_URL` | `https://free.uazapi.com` (ou seu domínio) |
| `UAZAPI_INSTANCE_TOKEN` | Token da instância |
| `WEBHOOK_URL` | `https://SEU-APP.onrender.com/webhook` |
| `GEMINI_API_KEY` | Sua chave do Gemini |
| `GEMINI_MODEL` | `gemini-2.5-flash` |
| `SYSTEM_PROMPT` | Instrução base do agente |
| `SYSTEM_PROMPT_DIR` | `knowledge` |
| `PORT` | `5000` |
| `ADMIN_USER` | `admin` |
| `ADMIN_PASSWORD` | Senha do painel |
| `SECRET_KEY` | Chave aleatória longa |

### Build & Start
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python app.py`

### Importante
- O webhook é configurado **automaticamente** quando o app inicia (se `WEBHOOK_URL` estiver definido).
- O banco SQLite é recriado a cada deploy (disco efémero). Para persistência, use PostgreSQL.
- A pasta `knowledge/` precisa ser commitada no Git.

---

## 7. Painel CRM

Acesse `http://localhost:5000/panel` e faça login com as credenciais do `.env`.

### Funcionalidades

| Feature | Descrição |
|---|---|
| **Dashboard** | Métricas: contatos, conversas abertas, mensagens |
| **Atendimento** | Chat 3 colunas: lista, mensagens, info do cliente |
| **Auto/Humano** | Toggle por conversa — agente responde ou operador assume |
| **Contatos** | CRUD completo com notas, empresa, email |
| **Tags** | Organize conversas e contatos com etiquetas coloridas |
| **Templates** | Mensagens rápidas pré-definidas |
| **WhatsApp** | Status, QR code, reconexão |
| **SSE** | Atualização em tempo real via Server-Sent Events |

### Layout

```
┌──────┬───────────────┬──────────────┬────────────────┐
│      │  CONVERSAS    │  MENSAGENS   │  INFO CLIENTE   │
│  S   │  [busca]      │              │  Nome           │
│  I   │  [filtros]    │  ┌────────┐  │  Telefone       │
│  D   │               │  │ msg... │  │  Tags           │
│  E   │  João         │  │ msg... │  │  [Auto|Humano]  │
│  B   │  Maria  ●2    │  └────────┘  │                 │
│  A   │               │  [____] [▶]  │                 │
└──────┴───────────────┴──────────────┴────────────────┘
```

---

## 7. Adicionando tools (function calling) no futuro

O `llm.py` já está preparado. O parâmetro `tools` existe e é passado direto ao Gemini:

```python
# llm.py — hoje
generate_reply(history, system, tools=None)

# Amanhã: passe uma lista de Tools do Gemini
from google.genai import types

my_tools = [types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="buscar_pedido",
        description="Busca status de um pedido pelo ID",
        parameters=types.Schema(
            type="OBJECT",
            properties={"pedido_id": types.Schema(type="STRING")},
            required=["pedido_id"],
        ),
    )
])]

reply = generate_reply(history, system, tools=my_tools)
```

---

## 8. Expandindo features

Consulte `LLMs-uazapi.txt` para endpoints disponíveis:

| Feature | Endpoint |
|---|---|
| Enviar imagem | `POST /send/image` |
| Enviar áudio | `POST /send/audio` |
| Botões interativos | `POST /send/menu` |
| Suporte a grupos | Filtrar `isGroup=true` |
| Campanhas em massa | `POST /campaign/simple` |

---

## Estrutura de arquivos

```
├── app.py            # Flask + webhook + rotas API + SSE
├── database.py       # SQLite + CRUD completo
├── auth.py           # Login/logout com sessão Flask
├── events.py         # SSE pub/sub thread-safe
├── uazapi.py         # Cliente UAZAPI (MVP + endpoints extras)
├── llm.py            # Cliente Gemini
├── buffer.py         # Debounce buffer por usuário
├── memory.py         # Histórico in-memory para o agente
├── knowledge/        # Memória do agente (empresa, produtos, FAQ)
│   ├── 00-empresa.md
│   ├── 01-produtos.md
│   ├── 02-valores.md
│   └── 03-faq.md
├── requirements.txt
├── .env.example
├── templates/
│   ├── login.html
│   └── index.html    # SPA do painel
└── static/
    ├── style.css
    └── app.js
```
