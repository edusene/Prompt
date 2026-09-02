# Chat Conversation

Note: _This is purely the output of the chat conversation and does not contain any raw data, codebase snippets, etc. used to generate the output._

### User Input

# 🎯 TAREFA: Agente de IA no WhatsApp via UAZAPI (Flask, Python)

Você é um engenheiro Python sênior. Construa uma aplicação **Flask mínima, funcional e pronta pra rodar** que conecta o WhatsApp (via UAZAPI) a um LLM (**Google Gemini 2.5 Flash**), com **buffer de mensagens com debounce configurável** e **envio de respostas separadas em múltiplas mensagens**.

## 📚 Documentação anexa
Você recebeu o arquivo **`llms-uazapi.txt`** junto com este prompt. Ele contém a documentação completa da UAZAPI adaptada pra LLMs: todos os endpoints (instâncias, mensagens, mídia, grupos, contatos, campanhas), formatos de payload, estrutura de webhook, eventos e exemplos.

**Regras de uso da doc:**
- Para o MVP descrito abaixo, você só precisa de 3 endpoints (`/send/text`, `/send/presence`, `/send/read`) e do payload de webhook `event=message` — já resumidos aqui no prompt.
- **Consulte `llms-uazapi.txt`** sempre que: (a) tiver dúvida sobre algum campo ou comportamento, (b) precisar escrever o README explicando como configurar o webhook na UAZAPI, (c) for adicionar suporte futuro a mídia, grupos, botões interativos, etc.
- Não invente endpoints nem campos — se algo não está no doc, não existe.

## 🧭 Princípios não-negociáveis
- **KISS extremo**: código óbvio, curto, sem abstrações desnecessárias.
- **Zero infra externa**: sem Redis, sem banco, sem Celery. Tudo em memória + `threading`.
- **Arquitetura pronta pra receber tools (function calling do Gemini) depois**, mas **sem tools agora** — apenas o parâmetro existe e fica `None`.
- **Código total < 400 linhas** somando todos os arquivos.

## 📦 Stack
- Python 3.11+
- `flask`, `requests`, `google-genai`, `python-dotenv`

> ⚠️ Use o SDK **novo e unificado** `google-genai` (`pip install google-genai`), **não** o antigo `google-generativeai`. Import: `from google import genai`.

## 📁 Estrutura de arquivos
```
.
├── app.py            # Flask + rota /webhook + pipeline de flush
├── uazapi.py         # cliente UAZAPI (send_text, send_presence, mark_read)
├── llm.py            # cliente Gemini (generate_reply com hook pra tools)
├── buffer.py         # debounce buffer por usuário (threading.Timer)
├── memory.py         # histórico de conversa por usuário (dict + lock)
├── requirements.txt
├── .env.example
└── README.md
```

## 🔐 Variáveis de ambiente (.env.example)
```
UAZAPI_BASE_URL=https://seudominio.uazapi.com
UAZAPI_INSTANCE_TOKEN=cole_o_token_da_instancia_aqui
GEMINI_API_KEY=cole_a_api_key_do_aistudio_aqui
GEMINI_MODEL=gemini-2.5-flash
BUFFER_SECONDS=8
MAX_HISTORY=20
SYSTEM_PROMPT=Você é um assistente útil que responde no WhatsApp. Seja breve, natural, use quebras de parágrafo (\n\n) quando fizer sentido dividir ideias.
PORT=5000
```

## 🔁 Fluxo end-to-end (decore esse fluxo)
1. UAZAPI faz `POST /webhook` no seu Flask com um evento `message`.
2. App **ignora** se: `fromMe=true`, `isGroup=true`, ou `type != "text"`.
3. App chama `uazapi.mark_read(number, message_id)`.
4. App joga o texto no **buffer do remetente**.
5. Buffer espera `BUFFER_SECONDS` **desde a última mensagem** (debounce: cada msg nova reseta o timer).
6. Quando o timer dispara, `flush(user)` executa:
   - Envia `presence=composing` (digitando)
   - Concatena as mensagens bufferizadas em **um único turno `user`** (separadas por `\n`)
   - Acrescenta ao histórico do usuário
   - Chama `llm.generate_reply(history, system, tools=None)`
   - Acrescenta a resposta ao histórico (role `"model"`)
   - **Divide a resposta em chunks** e envia cada um como mensagem separada via UAZAPI, com `composing` entre elas e `time.sleep(1 + len(chunk)/200)` simulando digitação humana
   - Envia `presence=paused` no fim

## 🧱 Detalhamento dos módulos

### `uazapi.py`
Três funções síncronas puras (referência completa dos endpoints na seção "ENDPOINTS — ENVIAR MENSAGEM" do `llms-uazapi.txt`). Todas usam header `{"token": UAZAPI_INSTANCE_TOKEN, "Content-Type": "application/json"}`. Em erro HTTP: `print` curto e segue — **não levanta exceção**.

| Função | Método/Endpoint | Body |
|---|---|---|
| `send_text(number, text)` | `POST /send/text` | `{"number": "...", "text": "..."}` |
| `send_presence(number, presence)` | `POST /send/presence` | `{"number": "...", "presence": "composing"\|"paused"}` |
| `mark_read(number, message_id)` | `PUT /send/read` | `{"number": "...", "messageId": "..."}` |

### `llm.py`
```python
from google import genai
from google.genai import types

_client = genai.Client(api_key=GEMINI_API_KEY)

def generate_reply(history: list[dict], system: str, tools: list | None = None) -> str:
    """Chama Gemini generate_content. tools=None hoje; preparado pra function calling depois."""
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=1024,
        tools=tools,  # None hoje
    )
    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=history,
        config=config,
    )
    # TODO: handle function_call parts quando tools for usado
    return (response.text or "").strip()
```

### `memory.py`
Dict global `{number: [{"role": "user"|"model", "parts": [{"text": "..."}]}, ...]}` protegido por `threading.Lock`.

> ⚠️ Use **`"model"`** (não `"assistant"`) e estrutura **`parts: [{"text": ...}]`** — é o formato nativo do Gemini, evita conversões.

Funções:
- `append(user, role, text)` — monta `{"role": role, "parts": [{"text": text}]}`
- `get(user) -> list` — retorna o histórico pronto pra mandar pro Gemini
- `reset(user)`
- Trunca pra manter no máximo `MAX_HISTORY` mensagens (do mais recente).

### `buffer.py`
Para cada usuário, mantém:
- Lista de strings pendentes
- Um `threading.Timer` ativo (ou None)

API:
```python
class MessageBuffer:
    def __init__(self, wait_seconds: float, on_flush: Callable[[str, list[str]], None]): ...
    def add(self, user: str, text: str) -> None:
        # com lock: append à lista, cancela timer anterior, cria novo Timer(wait_seconds, self._flush, [user])
```
`_flush(user)` retira a lista pendente, limpa o estado do usuário e chama `on_flush(user, texts)` numa thread (já está numa, ok). Tudo sob `threading.Lock`.

### `app.py`
- `load_dotenv()` no topo
- Instancia `MessageBuffer(BUFFER_SECONDS, on_flush=handle_flush)`
- `GET /` → retorna `"ok"` (healthcheck)
- `POST /webhook` → parse JSON, valida, extrai `number = data["from"].split("@")[0]`, chama `mark_read`, `buffer.add(number, body)`, retorna `{"ok": true}` em < 100ms (não bloqueia)
- `handle_flush(user, texts)`:
```
  send_presence(user, "composing")
  user_turn = "\n".join(texts)
  memory.append(user, "user", user_turn)
  reply = llm.generate_reply(memory.get(user), SYSTEM_PROMPT)
  memory.append(user, "model", reply)
  for chunk in split_reply(reply):
      send_presence(user, "composing")
      time.sleep(1 + len(chunk) / 200)
      send_text(user, chunk)
  send_presence(user, "paused")
```
- `split_reply(text)`: split por `\n\n`; se algum pedaço > 800 chars, quebra por `. ` (mantendo o ponto). Remove strings vazias.
- `if __name__ == "__main__": app.run(host="0.0.0.0", port=PORT)`

## 📨 Payload de webhook (referência rápida — detalhes completos em `llms-uazapi.txt`, seção "WEBHOOK — CONFIGURAÇÃO E EVENTOS")
```json
{
  "event": "message",
  "data": {
    "id": "ABCDEF123",
    "from": "5511999999999@s.whatsapp.net",
    "fromMe": false,
    "body": "Oi, tudo bem?",
    "type": "text",
    "pushName": "João",
    "isGroup": false
  }
}
```
Extrair `number` removendo `@s.whatsapp.net`. Ignorar qualquer evento que não seja `event == "message"`.

## 📝 README.md (escreva também)
Deve conter, em ordem:
1. O que é (3 linhas)
2. `pip install -r requirements.txt`
3. `cp .env.example .env` e preencher (mencionar que `GEMINI_API_KEY` se obtém grátis em https://aistudio.google.com/apikey)
4. `python app.py`
5. Expor com `ngrok http 5000`
6. Configurar webhook na UAZAPI — **consulte `llms-uazapi.txt` seção "ENDPOINTS — WEBHOOK"** e gere o `curl` pronto: `PUT {UAZAPI_BASE_URL}/webhook` com body `{"webhookUrl": "https://SEU-NGROK.ngrok.io/webhook", "events": ["message"]}` e header `token: <INSTANCE_TOKEN>`
7. Seção **"Adicionando tools no futuro"** — apontar pro `llm.py` onde o `tools` param já existe; explicar que basta passar `tools=[types.Tool(function_declarations=[...])]` e tratar `response.candidates[0].content.parts` procurando `part.function_call`
8. Seção **"Expandindo features"** — lista curta apontando pro `llms-uazapi.txt` pra: receber mídia (`/send/download-media`), enviar imagem/áudio (`/send/image`, `/send/audio`), botões interativos (`/send/menu`), suporte a grupos, etc.

## ✅ Definição de pronto
- `pip install -r requirements.txt && python app.py` sobe sem erros
- `python -m py_compile app.py uazapi.py llm.py buffer.py memory.py` passa limpo
- Mensagens recebidas em janela de `BUFFER_SECONDS` viram **um único turno** pro Gemini
- Resposta do Gemini chega no WhatsApp **dividida em mensagens separadas** com indicador de "digitando" entre elas
- Histórico por usuário é mantido entre turnos (mesma execução do processo)
- Código total < 400 linhas

## 🚫 Restrições explícitas
- Nada de `async`/`await` — `threading` resolve
- Nada de Blueprints, Flask-RESTful, classes além de `MessageBuffer`
- Nada de SQLite ou arquivo de persistência — perder histórico no restart é OK
- Não logar payloads inteiros — só `print` curtos tipo `[buffer] flush user=5511999 msgs=3` e `[llm] reply len=412`
- Não implementar tools agora, só deixar o parâmetro pronto
- **Não use o SDK antigo `google-generativeai`** — só o novo `google-genai`
- **Não invente endpoints da UAZAPI** — qualquer coisa fora dos 3 endpoints do MVP, confirme em `llms-uazapi.txt` antes

## 🎬 Entrega
Escreva **todos os arquivos completos** (sem `...` ou TODOs além do explícito em `llm.py`). Ao final, mostre um exemplo simulado dos logs do fluxo completo: webhook recebido → buffer espera → flush → Gemini → 3 mensagens enviadas.



### Planner Response

Let me check the workspace and the referenced documentation file first.

*Listed directory [Prompt](file:///c:/Users/distr/Downloads/Prompt) *

*Viewed [LLMs-uazapi.txt](file:///c:/Users/distr/Downloads/Prompt/LLMs-uazapi.txt) *

### Planner Response

Good, I have the documentation. Now let me create all the project files.

*Edited relevant file*

*Edited relevant file*

*Edited relevant file*

*Edited relevant file*

*Edited relevant file*

*Edited relevant file*

*Edited relevant file*

*Edited relevant file*

### Planner Response

Now let's verify the syntax of all Python files:

*User accepted the command `python -m py_compile app.py uazapi.py llm.py buffer.py memory.py && echo "ALL OK"`*

*User accepted the command `python -m py_compile app.py uazapi.py llm.py buffer.py memory.py; if ($LASTEXITCODE -eq 0) { Write-Host "ALL OK" } else { Write-Host "ERRORS" }`*

### Planner Response

All files compile clean. Let's also do a quick line count check:

*User accepted the command `Get-Content app.py, uazapi.py, llm.py, buffer.py, memory.py | Measure-Object -Line`*

### Planner Response

228 lines total — well under the 400-line limit. All good. ✅

---

## Entrega completa

Todos os arquivos criados em [`c:\Users\distr\Downloads\Prompt\`](file:///c:/Users/distr/Downloads/Prompt):

| Arquivo | Função |
|---|---|
| [app.py](file:///c:/Users/distr/Downloads/Prompt/app.py) | Flask + `/webhook` + `handle_flush` + `split_reply` |
| [uazapi.py](file:///c:/Users/distr/Downloads/Prompt/uazapi.py) | Cliente UAZAPI (`send_text`, `send_presence`, `mark_read`) |
| [llm.py](file:///c:/Users/distr/Downloads/Prompt/llm.py) | Cliente Gemini via `google-genai` + hook `tools=None` |
| [buffer.py](file:///c:/Users/distr/Downloads/Prompt/buffer.py) | `MessageBuffer` com debounce por `threading.Timer` |
| [memory.py](file:///c:/Users/distr/Downloads/Prompt/memory.py) | Histórico por usuário em formato nativo Gemini |
| [requirements.txt](file:///c:/Users/distr/Downloads/Prompt/requirements.txt) | 4 dependências |
| [.env.example](file:///c:/Users/distr/Downloads/Prompt/.env.example) | Template de variáveis de ambiente |
| [README.md](file:///c:/Users/distr/Downloads/Prompt/README.md) | Setup completo + seções tools futuro + features |

**`py_compile` limpo ✅ | 228 linhas totais ✅ (< 400)**

---

### 📋 Logs simulados — fluxo completo

```
# t=0s — João manda "Oi"
[webhook] from=5511999999999 len=2

# t=2s — João manda "tudo bem?" (reseta o timer para +8s)
[webhook] from=5511999999999 len=9

# t=10s — timer dispara (8s desde a última msg)
[buffer] flush user=5511999999999 msgs=2

# flush executa em background thread
# send_presence(composing) → UAZAPI
# memory.append(user, "user", "Oi\ntudo bem?")
# chama generate_reply(history) →

[llm] reply len=187

# memory.append(user, "model", "Olá, João! ...")
# split_reply → 2 chunks (parágrafo duplo)

# Chunk 1:
# send_presence(composing) → sleep(1 + 52/200 = 1.26s) → send_text("Olá, João! Tudo ótimo por aqui 😊")

# Chunk 2:
# send_presence(composing) → sleep(1 + 135/200 = 1.68s) → send_text("Me conta, como posso te ajudar hoje?")

# send_presence(paused) → UAZAPI
```

---

### Pontos-chave de design

- **Debounce real**: cada nova msg cancela o `threading.Timer` anterior — `buffer.add()` nunca bloqueia o webhook
- **Formato Gemini nativo**: `{"role": "user"|"model", "parts": [{"text": "..."}]}` — sem conversões, funciona direto no SDK
- **`split_reply` duplo-critério**: primeiro quebra por `\n\n` (parágrafos), depois por `". "` se > 800 chars
- **Timing humano**: `sleep(1 + len(chunk)/200)` simula ~200 chars/s de digitação
- **Zero libs extras**: só `flask`, `requests`, `google-genai`, `python-dotenv` + stdlib `threading`

### User Input

Como faz pra rodar ?

*Viewed [.env.example](file:///c:/Users/distr/Downloads/Prompt/.env.example) *

### Planner Response

Para rodar a aplicação, você precisa seguir alguns passos simples de configuração inicial. Aqui está o resumo de como colocar tudo no ar:

### 1. Instalar as dependências
Abra o terminal na pasta do projeto e rode:
```bash
pip install -r requirements.txt
```

### 2. Configurar as variáveis de ambiente
Como você está no arquivo `.env.example`, faça uma cópia dele chamada `.env`. 
Preencha os valores reais no seu novo arquivo `.env`:
*   `UAZAPI_BASE_URL`: A URL da sua UAZAPI (ex: `https://seu-dominio.uazapi.com`)
*   `UAZAPI_INSTANCE_TOKEN`: O token da sua instância.
*   `GEMINI_API_KEY`: Sua chave do Google Gemini (pegue de graça no [Google AI Studio](https://aistudio.google.com/apikey)).

### 3. Iniciar o servidor Flask
Ainda no terminal, rode o arquivo principal:
```bash
python app.py
```
Seu servidor vai iniciar na porta 5000 (`http://localhost:5000`).

### 4. Expor o servidor para a Internet (Webhook)
A UAZAPI precisa enviar as mensagens para o seu servidor. Como ele está rodando no seu computador (localhost), você precisa de uma URL pública. A maneira mais fácil de fazer isso é usando o **ngrok**.

Em outra janela do terminal, rode:
```bash
ngrok http 5000
```
O ngrok vai te dar uma URL do tipo `https://abcd-123.ngrok-free.app`.

### 5. Configurar o Webhook na UAZAPI
Por fim, você precisa avisar a UAZAPI para mandar os eventos de mensagem para a sua URL do ngrok.
Você pode fazer isso rodando este `curl` no terminal (substituindo com seus dados):

```bash
curl -X PUT https://SEU-DOMINIO.uazapi.com/webhook \
  -H "token: SEU_INSTANCE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "webhookUrl": "https://SUA-URL-DO-NGROK.ngrok-free.app/webhook",
    "events": ["message"]
  }'
```

Pronto! Agora, quando alguém mandar mensagem para o seu número do WhatsApp conectado na instância, a UAZAPI vai mandar para o seu Flask, que vai agrupar (debounce), mandar pro Gemini e devolver as respostas divididas certinho.

*(O arquivo [README.md](file:///c:/Users/distr/Downloads/Prompt/README.md) que criei na pasta do projeto também tem todos esses passos documentados para consulta futura).*

### User Input

Quero que apenas verifique se esses erros são pq eu NÃO preenchi o .env ainda

*Viewed [.env.example](file:///c:/Users/distr/Downloads/Prompt/.env.example) *

*User accepted the command `pip install -r requirements.txt
`*

*User accepted the command `c:\Users\distr\Downloads\Prompt\.venv\Scripts\python.exe -m pip install -r requirements.txt`*

*User accepted the command `cls`*

*User accepted the command `python app.py
`*

### Planner Response

Exatamente! O erro é totalmente devido à falta de preenchimento do `.env`.

Olhando os logs que você gerou:
```python
ValueError: No API key was provided. Please pass a valid API key.
```

O que aconteceu foi:
1. O seu código tentou carregar as variáveis de ambiente.
2. Como você ainda não criou o arquivo `.env` (ou ele ainda está vazio/com os valores de exemplo), a variável `GEMINI_API_KEY` ficou vazia (`""`).
3. Quando o arquivo `llm.py` tentou inicializar o cliente do Gemini (`genai.Client(api_key=GEMINI_API_KEY)`), a biblioteca nova do Google recusou a chave vazia e derrubou o aplicativo com esse erro.

Para resolver e ver o aplicativo rodar perfeitamente, você só precisa:
1. Renomear o arquivo `.env.example` para `.env`
2. Colocar sua chave real do Gemini na variável `GEMINI_API_KEY`

As outras variáveis como o Token da UAZAPI não quebram o app na hora de iniciar, mas o Gemini tem essa validação estrita logo no "start". Pode preencher sua chave lá que vai subir de primeira!