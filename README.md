# WhatsApp AI Agent + CRM Panel — Datafy API + OpenAI

Agente de IA no WhatsApp com painel CRM completo. Usa a [Datafy API](https://app.datafyapi.com.br) como gateway oficial para a Meta Cloud API, e OpenAI como LLM. Mensagens sao agrupadas via debounce e respostas chegam divididas em multiplas mensagens.

---

## 1. Pre-requisitos

- **Conta na Datafy API** em [app.datafyapi.com.br](https://app.datafyapi.com.br)
- **Token de Acesso (API Key)**
- **Phone Number ID** (encontrado via GET /me na Datafy API, ou associado ao seu token)
- **WABA ID** (WhatsApp Business Account ID vinculado à sua Datafy API ou Meta Cloud Provider)
- Python 3.11+

### Como obter os valores

| Valor | Onde encontrar |
|---|---|
| `DATAFY_TOKEN` | Seu token `sk_live_xxx` fornecido pelo painel Datafy. |
| `DATAFY_PHONE_NUMBER_ID` | Obtenha na aba Dev da Datafy ou via `GET /me` (se necessário). |
| `DATAFY_WABA_ID` | O ID da WABA retornado via Meta ou associado ao seu app. |
| `DATAFY_WEBHOOK_SECRET` | Chave mostrada na aba Webhooks no painel (ex: `whsec_xxx`). |

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
| `OPENAI_TEMPERATURE` | Temperatura do modelo (padrao: `0.1`) |
| `BUFFER_SECONDS` | Segundos de debounce (padrao: `3`) |
| `MAX_HISTORY` | Maximo de mensagens no historico (padrao: `40`) |
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
| `OPENAI_TEMPERATURE` | `0.1` |
| `CARDAPIO_API_URL` | `https://balbs-cardapio.base44.app` (apelido ASCII de `CARDÁPIO_API_URL`) |
| `BALBS_AGENTE_API_KEY` | Chave do cardapio digital |
| `SYSTEM_PROMPT` | Instrucao base do agente |
| `PORT` | `5000` |
| `ADMIN_USER` | `admin` |
| `ADMIN_PASSWORD` | Senha do painel |
| `SECRET_KEY` | Chave aleatoria longa |

### Build & Start
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python app.py`

---

## 11. Memoria e base de conhecimento do agente

O que o agente le antes de responder, em ordem:

1. **Prompt operacional** (o `.docx` no topo de `parse_knowledge.PROMPT_DOCX_VERSOES`,
   carregado por `parse_knowledge`)
2. **Base oficial** (`knowledge_rag.get_full_knowledge_content()`) — só os arquivos de
   DADOS. Arquivos de prompt (o .docx e os `prompts/*.md` dos sub-agentes) ficam de
   fora: injetá-los junto duplicava instruções e gerava regras conflitantes.
3. **Regras de execucao** e, por ultimo, a **ficha do atendimento**.

### Numeros nunca saem da cabeca do modelo

| Dado | De onde vem |
|---|---|
| Taxa de entrega | tool `consultar_frete` → `facts.lookup_frete` (tabela oficial, com apelidos e tolerancia a erro de digitacao) |
| Preco de produto | tool `consultar_preco` → `facts.lookup_preco` |
| Subtotal / frete / total | tool `calculate_order` → `cardapio.calcular_pedido`, que devolve `resumo_formatado` pronto para copiar |

Se o bairro ou o produto nao existir na tabela, a tool diz isso explicitamente e o
prompt manda nao estimar. Depois de cada resposta, `_auditar_valores` registra no log
todo valor em `R$` que nao venha da base (`[auditoria] valores SEM ORIGEM na base: [...]`).

### Memoria do atendimento

`conversation_memory` guarda no banco (`conversation_states`) nome, itens, modalidade,
endereco, bairro, data, horario e pagamento, e devolve ao prompt uma ficha com o que ja
foi coletado e qual a **proxima pergunta obrigatoria**. O historico de mensagens sozinho
nao bastava: a janela expirava e o agente reperguntava o que ja sabia.

### Tempo de resposta e temperatura

O cliente ve a resposta entre **5 e 10 segundos**. O tempo se divide em:

| Etapa | Variavel | Padrao |
|---|---|---|
| Debounce (silencio do cliente antes de responder) | `BUFFER_SECONDS` | `3` |
| Pausa antes da 1a mensagem | `PAUSA_PRIMEIRA_SEG` | `0.4` |
| Pausa entre as mensagens seguintes | `PAUSA_MIN_SEG` / `PAUSA_MAX_SEG` | `0.8` / `2.0` |
| Piso total, contado do cliente | `RESPOSTA_MIN_SEG` | `5.0` |

`app.enviar_em_blocos()` e o unico caminho de envio: ele divide, aplica o ritmo,
respeita o piso e espelha no painel. Antes cada trecho esperava `1 + len/200`
segundos — inclusive o primeiro — e com o debounce de 8s a resposta levava ~21s.

`OPENAI_TEMPERATURE` (padrao `0.1`) prende o modelo ao catalogo. `llm._create()`
aplica a temperatura e, se o modelo for de uma familia que so aceita o valor
default (gpt-5, o1, o3), repete a chamada sem ela em vez de derrubar a resposta.

### O cardapio digital em producao

`CARDÁPIO_TEST_MODE` (gravado pelo painel no banco) decide entre calcular
localmente e chamar a API do cardapio. Toda configuracao do cardapio e lida por
`cardapio._config()`, **banco primeiro, ambiente depois**, e cada uma aceita o
nome oficial com acento e apelidos em ASCII (`CARDAPIO_API_URL`,
`CARD_API_URL`). Isso importa: nome de variavel fora do ASCII nao sobrevive em
painel de deploy. Com o modo producao ligado pelo painel e a URL so no `.env`
— que nao existe no servidor — `_get_base_url()` devolvia `""` e todo pedido
morria em "CARDÁPIO_API_URL nao configurada", que o agente traduzia ao cliente
como "estou com dificuldade tecnica para calcular o valor do seu pedido".
`cardapio.URL_CARDAPIO_PADRAO` e a rede de seguranca final.

O **frete sai sempre da tabela oficial** (`facts.lookup_frete`), nos dois modos.
Em producao o bairro era mandado cru para a API: "agenor" nao casava com
cadastro nenhum e voltava `deliveryFee: 0`, enquanto `consultar_frete` ja tinha
dito R$ 12,00 ao cliente. Hoje o apelido e resolvido antes da chamada e, se a
API discordar da tabela, a tabela vence e a divergencia fica no log.

### Conferir a credencial do cardapio

`createOrderFromAgent` exige `x-api-key`; `calculateOrder` nao. Por isso o
`verificar_catalogo.py` passava mesmo com a chave errada — ele so chama o
endpoint publico, e esta escrito no docstring dele. Resultado: a primeira vez
que se descobria que a chave nao servia era quando o pedido de um cliente real
falhava, depois de ele ja ter confirmado.

```bash
python verificar_credencial.py            # veredito
python verificar_credencial.py --detalhe  # mostra cada sonda
```

Tambem em `GET /api/cardapio/health` (logado) e no botao **Testar chave** da aba
Cardapio. O botao dispara sozinho quando alguem desliga o modo teste: e o
instante em que uma chave errada deixa de ser inofensiva.

Nao cria pedido. Sao tres POSTs com um corpo que **nao tem nenhum dos cinco
campos obrigatorios**, mudando so o header:

| Sonda | Header | Esperado |
|---|---|---|
| 1 | nenhum | 401 — o endpoint exige chave |
| 2 | chave inventada | 401 — chave errada e mesmo recusada |
| 3 | a nossa chave | 401 = recusada; resto = aceita |

A sonda 2 e o grupo de controle. Sem ela, um 401 por outro motivo (rota
renomeada, gateway fora) acusaria a chave e mandaria o dono atras do problema
errado; e se o endpoint parasse de validar, um "aceita" nao provaria nada. Se a
sonda 2 passar, o veredito e `inconclusivo`, nunca `aceita`.

Estados: `aceita`, `recusada`, `nao_configurada`, `inconclusivo`,
`indisponivel` (rede fora ou 5xx — nunca confundido com recusa). O retorno diz
tambem a **origem da chave** (`banco` ou `ambiente`): sem isso, trocar a chave
no painel e continuar vendo 401 nao teria explicacao.

> O corpo da sonda (`cardapio._SONDA_DE_CREDENCIAL`) precisa continuar sem
> campo de pedido. No dia em que ele descrever um pedido valido, cada clique em
> "Testar chave" vira comanda na fabrica.

### Troco

A lista oficial de campos do `createOrderFromAgent` **nao tem `change_for`**. O
prompt V7 tem uma TRAVA DE TROCO completa, entao o valor ia ate o fim da
conversa e morria ali: o entregador nunca ficava sabendo. Hoje ele vai no
`notes` do pedido, que e o unico campo livre que chega a comanda.

Observacao POR ITEM nao serve: `cardapio_catalogo.traduzir()` reconstroi cada
item do zero (`name`, `quantity`, `selected_flavors`) e descarta `price` e
`notes`. So o `notes` de nivel de pedido sobrevive.

`troco_para` na ficha e **string**, com quatro estados — `""` nao perguntado,
`"nao"` cliente tem trocado, `"100.00"` valor, `"nao_informado"` perguntamos e
nao deu para entender. Float nao serve: `0.0` e falsy e sumiria no `merge`, no
`load` e em toda checagem por truthiness.

| Etapa | Onde |
|---|---|
| Captura ("para 100", "1.000", "cem", "nao precisa", "tenho trocado") | `conversation_memory.capturar_troco` |
| Validacao troco > total | `conversation_memory.pendencia_de_detalhe` |
| Troco no espelho do pedido | `cardapio._rotulo_pagamento` |
| Texto que vai a comanda | `cardapio.montar_notes` |
| Portao final | `app._send_handler` |

`capturar_resposta_direta` **nao** serve aqui: ela descarta candidato com
digito, guarda que e o que impede "50" de virar nome ou bairro. A captura do
troco tem dois gatilhos — o cliente falar "troco", ou o agente ter perguntado
do troco na mensagem anterior. Sem a segunda amarra, "quero 100 coxinhas"
viraria troco de R$ 100. O valor e lido do texto CRU: `facts.normalize` apaga
pontuacao e "1.000" viraria "1 000".

O troco **nao** entra em `faltando()`. Entraria em deadlock: `calculate_order`
se recusa a rodar com campo faltando, mas a regra "troco maior que o total"
precisa do total, que so existe depois do `calculate_order`. A cobranca fica em
`pendencia_de_detalhe` (que roda com o total ja na mao) mais um portao em
`_send_handler`.

Esse portao tem escape: se a pergunta do troco ja foi feita e a resposta nao
casou com nada, o troco vira `"nao_informado"` e o pedido sai com "Troco:
cliente nao informou". Sem isso, uma frase fora do padrao ("pode ser uma nota
de cem pila") travaria o pedido num laco de reperguntas.

### Palavra-chave de reset (#reset)

Mandar `#reset` no WhatsApp reinicia o atendimento daquele numero: reativa a
Sandrinha (`auto_mode=1`), tira a conversa da fila humana, limpa a ficha e
corta o historico que vai ao modelo. Existe porque o transbordo so tinha ida —
depois de um transbordo o numero ficava preso em modo humano ate alguem abrir
o painel.

| Onde | Como |
|---|---|
| WhatsApp | manda `#reset` (palavra inteira; `#reset por favor` vale, `resetar` nao) |
| Painel / API | `POST /api/conversations/<phone>/reset`, com `{"avisar": false}` para nao avisar o cliente |
| Trocar a palavra | `PUT /api/settings/reset` com `reset_keyword` (uma palavra, sem espaco) e/ou `reset_message` |

O comando e tratado no webhook, **antes** do buffer e antes de qualquer
checagem de modo: ele precisa funcionar justamente quando a conversa esta em
modo humano, que e onde `handle_flush` desiste logo na primeira linha.

Nada e apagado do banco. O corte e a coluna `conversations.reset_at`, que
`get_recent_messages` usa como piso — o painel continua mostrando a conversa
inteira, so o agente e que recomeca do zero. Por isso o corte e uma coluna e
nao uma variavel: com o Render reiniciando, um corte em memoria traria a
conversa velha de volta do banco na primeira mensagem.

### Manutencao da base

```bash
python reindex_knowledge.py --dry-run   # mostra o que mudaria
python reindex_knowledge.py --embed     # regrava documentos + chunks + embeddings
python test_agente.py                   # bateria de perguntas (nao envia WhatsApp)
python test_agente.py --fluxo           # conversa completa de pedido
```

`GET /api/knowledge/health` (logado) diagnostica a base: arquivo sem documento
original, texto com trechos duplicados, status de indexacao e quantas linhas de
cardapio/bairro foram reconhecidas.

> O chunker antigo gravava 50 caracteres do chunk anterior dentro do seguinte e
> cortava no meio de linhas e palavras. Como o texto oficial era remontado desses
> chunks, o prompt e a tabela de precos chegavam ao modelo duplicados e quebrados.
> Hoje o texto original fica em `knowledge_files.content` e os chunks servem so para
> busca; `knowledge_repair.py` conserta bases antigas.

---

## 12. Estrutura de arquivos

```
├── app.py                  # Flask + webhook + rotas API + SSE
├── database.py             # ORM + CRUD completo (Supabase/PostgreSQL)
├── db.py                   # Camada de abstracao de banco
├── auth.py                 # Login/logout com sessao Flask
├── events.py               # SSE pub/sub thread-safe
├── meta.py                 # Cliente Meta WhatsApp Cloud API
├── llm.py                  # Cliente OpenAI
├── buffer.py               # Debounce buffer por usuario
├── memory.py               # Historico de mensagens do agente
├── conversation_memory.py  # Ficha do atendimento (dados coletados, persistente)
├── facts.py                # Tabelas oficiais: fretes, precos, horarios
├── knowledge_rag.py        # RAG com embeddings + documento original
├── knowledge_repair.py     # Conserto de bases danificadas pelo chunker antigo
├── parse_knowledge.py      # Leitura do .docx/.xlsx -> texto limpo
├── reindex_knowledge.py    # Reconstroi a base de conhecimento
├── test_agente.py          # Simulador de conversa (sem WhatsApp, sem CRM)
├── requirements.txt
├── .env.example
├── templates/
│   ├── login.html
│   └── index.html    # SPA do painel
└── static/
    ├── style.css
    └── app.js
```
