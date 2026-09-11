# WhatsApp AI Agent + CRM Panel — Meta Cloud API + OpenAI

Agente de IA no WhatsApp com painel CRM completo. Usa a [WhatsApp Business Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api/overview) da Meta como gateway e OpenAI como LLM. Mensagens sao agrupadas via debounce e respostas chegam divididas em multiplas mensagens.

---

## 1. Pre-requisitos

- **Conta Meta Business** com WhatsApp Business Account (WABA)
- **App no [developers.facebook.com](https://developers.facebook.com)** com permissoes:
  - `whatsapp_business_messaging` (envio/recebimento de mensagens)
  - `whatsapp_business_management` (gestao de templates, numeros)
- **Phone Number ID** do numero conectado
- **System User Access Token** permanente (nao o token de desenvolvimento temporario)
- **WABA ID** (WhatsApp Business Account ID)
- Python 3.11+

### Como obter os valores

| Valor | Onde encontrar |
|---|---|
| `META_ACCESS_TOKEN` | App Dashboard > WhatsApp > API Setup > System User Access Token |
| `META_PHONE_NUMBER_ID` | WhatsApp Manager > Phone Numbers > clique no numero |
| `META_WABA_ID` | WhatsApp Manager > Account Settings > WABA ID |
| `WEBHOOK_VERIFY_TOKEN` | Qualquer string secreta voce escolher |

---

## 2. Instalacao

```bash
pip install -r requirements.txt
```

---

## 3. Configuracao

```bash
cp .env.example .env
```

Edite `.env` e preencha:

| Variavel | Descricao |
|---|---|
| `META_ACCESS_TOKEN` | System User Access Token (permanente) |
| `META_PHONE_NUMBER_ID` | ID do numero de telefone business |
| `META_WABA_ID` | WhatsApp Business Account ID |
| `META_API_VERSION` | Versao do Graph API (padrao: `v21.0`) |
| `WEBHOOK_VERIFY_TOKEN` | Token secreto para verificacao do webhook |
| `OPENAI_API_KEY` | Chave da OpenAI |
| `OPENAI_MODEL` | Modelo OpenAI (padrao: `gpt-4o-mini`) |
| `BUFFER_SECONDS` | Segundos de debounce (padrao: `8`) |
| `MAX_HISTORY` | Maximo de mensagens no historico (padrao: `20`) |
| `SYSTEM_PROMPT` | Instrucao de sistema para o LLM |
| `PORT` | Porta do Flask (padrao: `5000`) |

---

## 4. Rodando

```bash
python app.py
```

---

## 5. Expondo na internet

```bash
ngrok http 5000
```

---

## 6. Configurando webhook no Meta

### Opcao 1: Via App Dashboard (recomendado)

1. Acesse [developers.facebook.com](https://developers.facebook.com)
2. Va em **App Dashboard > WhatsApp > Configuration**
3. Em **Webhook**, clique em **Edit**
4. Preencha:
   - **Callback URL**: `https://SEU-NGROK.ngrok-free.app/webhook`
   - **Verify Token**: o mesmo valor de `WEBHOOK_VERIFY_TOKEN` no `.env`
5. Clique em **Verify and save**
6. Em **Webhook fields**, assine ao campo **messages**

### Opcao 2: Verificar manualmente

```bash
# Testar se o webhook responde ao challenge
curl "https://SEU-NGROK.ngrok-free.app/webhook?hub.mode=subscribe&hub.verify_token=SEU_TOKEN&hub.challenge=12345"
# Deve retornar: 12345
```

### Importante

- O webhook precisa de HTTPS com certificado valido
- Assine ao campo **messages** no painel
- O app deve estar em **Live mode** (nao Dev mode) para receber webhooks reais
- Meta retries por ate 7 dias se seu endpoint retornar erro

---

## 7. Painel CRM

Acesse `http://localhost:5000/panel` e faca login com as credenciais do `.env`.

### Funcionalidades

| Feature | Descricao |
|---|---|
| **Dashboard** | Metricas: contatos, conversas abertas, mensagens |
| **Atendimento** | Chat 3 colunas: lista, mensagens, info do cliente |
| **Auto/Humano** | Toggle por conversa — agente responde ou operador assume |
| **Contatos** | CRUD completo com notas, empresa, email |
| **Tags** | Organize conversas e contatos com etiquetas coloridas |
| **Templates** | Mensagens rapidas pre-definidas |
| **WhatsApp** | Status do numero, templates, business profile |
| **SSE** | Atualizacao em tempo real via Server-Sent Events |

---

## 8. API do WhatsApp (rotas novas)

| Rota | Metodo | Descricao |
|---|---|---|
| `/api/whatsapp/status` | GET | Status do numero (quality rating, display name) |
| `/api/whatsapp/templates` | GET | Lista templates aprovados na WABA |
| `/api/whatsapp/templates/<id>` | GET | Detalhe de um template |
| `/api/whatsapp/templates/<name>` | DELETE | Deleta um template |
| `/api/whatsapp/profile` | GET | Business profile do numero |
| `/api/whatsapp/profile` | POST | Atualiza business profile |
| `/api/whatsapp/send-template` | POST | Envia template message |
| `/api/whatsapp/send-interactive` | POST | Envia mensagem interativa (botoes/lista) |
| `/api/whatsapp/upload-media` | POST | Upload de midia para o servidor Meta |

### Exemplo: enviar template

```bash
curl -X POST http://localhost:5000/api/whatsapp/send-template \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "5511999999999",
    "template_name": "hello_world",
    "language": "pt_BR"
  }'
```

### Exemplo: enviar botoes interativos

```bash
curl -X POST http://localhost:5000/api/whatsapp/send-interactive \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "5511999999999",
    "type": "button",
    "body": "Escolha uma opcao:",
    "buttons": [
      {"type": "reply", "reply": {"id": "op1", "title": "Opcao 1"}},
      {"type": "reply", "reply": {"id": "op2", "title": "Opcao 2"}}
    ]
  }'
```

---

## 9. Features da Meta Cloud API

### Mensagens de texto
Servico: envio de texto simples durante a janela de 24h do servico.

### Templates
Mensagens aprovadas pela Meta para envio fora da janela de 24h. Criadas no WhatsApp Manager > Message Templates.

| Categoria | Uso |
|---|---|
| `MARKETING` | Promocoes, anuncios |
| `UTILITY` | Atualizacoes de pedido, confirmacoes |
| `AUTHENTICATION` | OTP/2FA |

### Midia
Upload e envio de imagens, videos, audios e documentos. Limites:
- Imagem: 5 MB (JPEG, PNG)
- Video: 16 MB (MP4, 3GPP)
- Audio: 16 MB (AAC, AMR, MP3, OGG/Opus)
- Documento: 100 MB (PDF, DOCX, XLSX, etc.)

### Mensagens interativas
- **Botoes**: ate 3 botoes de resposta rapida
- **Lista**: ate 10 secoes com ate 10 rows cada

### Indicadores
- Typing indicator: exibido por 25 segundos ou ate responder
- Read receipts: marcacao automatica de leitura

### Webhooks
Meta envia webhooks para notificacoes:
- `messages`: mensagens recebidas e status de envio
- Outros campos disponiveis para assinatura

---

## 10. Deploy no Render

### Variaveis de ambiente

| Variavel | Valor |
|---|---|
| `META_ACCESS_TOKEN` | System User Access Token |
| `META_PHONE_NUMBER_ID` | Phone Number ID |
| `META_WABA_ID` | WABA ID |
| `META_API_VERSION` | `v21.0` |
| `WEBHOOK_VERIFY_TOKEN` | Token secreto |
| `OPENAI_API_KEY` | Chave OpenAI |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `SYSTEM_PROMPT` | Instrucao base do agente |
| `PORT` | `5000` |
| `ADMIN_USER` | `admin` |
| `ADMIN_PASSWORD` | Senha do painel |
| `SECRET_KEY` | Chave aleatoria longa |

### Build & Start
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python app.py`

---

## 11. Estrutura de arquivos

```
├── app.py            # Flask + webhook + rotas API + SSE
├── database.py       # ORM + CRUD completo (Supabase/PostgreSQL)
├── db.py             # Camada de abstracao de banco
├── auth.py           # Login/logout com sessao Flask
├── events.py         # SSE pub/sub thread-safe
├── meta.py           # Cliente Meta WhatsApp Cloud API
├── llm.py            # Cliente OpenAI
├── buffer.py         # Debounce buffer por usuario
├── memory.py         # Historico in-memory para o agente
├── knowledge_rag.py  # RAG com embeddings
├── knowledge/        # Memoria do agente
├── requirements.txt
├── .env.example
├── templates/
│   ├── login.html
│   └── index.html    # SPA do painel
└── static/
    ├── style.css
    └── app.js
```
