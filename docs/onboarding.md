# Onboarding

## 1. Preparar o ambiente

```powershell
cd "C:\Users\Usuario\Documents\Projetos\AgenteBalbs\Prompt"
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Crie o `.env` a partir de `.env.example` e preencha as chaves. Nunca cole valores reais em conversa, issue ou documento versionado.

## 2. Entender o produto

Leia, nesta ordem: `PRODUCT.md`, `docs/architecture.md`, `README.md` (seção 11 para regras de negócio) e `TESTES_SANDRINHA.md`.

## 3. Verificar o repositório

```powershell
.venv\Scripts\python.exe test_regressao.py
```

Esperado: última linha `FALHAS: nenhuma`. Este teste não usa rede, banco nem modelo.

## 4. Rodar localmente

```powershell
.venv\Scripts\python.exe app.py
```

O painel fica em `http://localhost:<PORT>/panel` (padrão 5000). Login com `ADMIN_USER` e `ADMIN_PASSWORD`. Para receber mensagens reais é preciso expor o servidor (ex.: `ngrok`), o que envolve a Meta/Datafy e deve ser feito com autorização.

## 5. Antes de mudar comportamento

1. Escreva o caso em `test_regressao.py`, com o defeito reproduzido.
2. Rode a bateria e confirme que o caso novo falha.
3. Faça a mudança e rode de novo.
4. Se a mudança afeta o modelo, use `medir_oscilacao.py` só com autorização (gasta OpenAI).

## 6. Onde ficam as dúvidas

- Como o código se organiza: `graphify query "<pergunta>"` (com `graphify-out/` gerado).
- O que foi decidido e por quê: `docs/setup.md`.
- Pendências conhecidas: `docs/setup.md`, seção "Pendências".
