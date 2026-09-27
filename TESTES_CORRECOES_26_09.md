# Roteiro de verificação — as correções das queixas de 26/09

Cada teste aqui corresponde a **uma queixa que o Balbino escreveu no WhatsApp**
na noite de 26/09, ou a uma oscilação encontrada medindo depois. Todas já estão
cobertas por teste automático; esta lista é para você conferir com o olho, no
celular, que o comportamento chegou de verdade ao cliente.

**Antes de começar:**

- Teste **pelo celular**, nunca pelo WhatsApp do computador. No modo
  Coexistence a Meta não entrega ao nosso sistema o que sai do WhatsApp for
  Windows — você não veria erro, veria silêncio.
- Mande `#reset` **antes de cada teste**. Sem isso sobra dado do teste anterior.
- Os valores abaixo são os oficiais da planilha. **Um centavo de diferença é
  defeito.**
- A **oferta adicional** (bebida/bolo/docinho) agora é obrigatória e sai depois
  dos salgados e antes do endereço. Responda `Não, obrigado` e o fluxo segue.
  A frase muda com a hora: até 10h é Bolo de Milho, até 14h Refrigerante 2L,
  até 17h Bolo ou Guaraná Tuchaua, até 20h Refrigerante ou docinhos.
- Fora do expediente (antes de 07h40, 12h01–13h59, depois das 20h, domingo) ela
  avisa e oferece outro horário — isso é esperado.

---

## Parte 1 — as queixas do Balbino

### 1.1 Lote de 25 tem que travar ANTES do endereço 🔴

> *"Ela aceitou 30, 30 e 40 sem travar a regra de múltiplos de 25 por sabor.
> Pulou o upsell e já foi cobrar endereço e pagamento."*

```
#reset
```
```
Oi, boa tarde! Gostaria de pedir um cento de salgados fritos pra entregar hoje no bairro Olaria. Quero 30 coxinhas, 30 kibes e 40 bolinhas de queijo. Vocês entregam?
```

| | |
|---|---|
| ✅ Esperado | ela trata **só o lote**: `Fazemos de 25 em 25 por sabor! Ajusto para 25 coxinha de frango, 25 quibe e 50 bolinha de queijo?` |
| 🚩 Alerta | pedir **bairro, rua, dia, horário ou forma de pagamento** nesta resposta · citar só um ou dois dos três sabores · mostrar total |

Depois responda `pode ajustar` → aí sim ela oferece o acompanhamento, e só
então cobra a logística.

### 1.2 Sabor dito pela metade não pode virar outro produto 🔴

> *"Falhou no cálculo de lotes"* — ela tinha transformado "30 de carne" em
> Quiche de Carne de Sol (R$ 12,00/un) e o sabor sumia do ajuste.

```
#reset
```
```
Oi, quero 100 salgados fritos: 30 de carne, 30 de queijo e 40 coxinhas. Tem como entregar aqui no Flodoaldo Pontes Pinto?
```

| | |
|---|---|
| ✅ Esperado | ela **pergunta qual carne**: `Me confirma o sabor dos 30 de carne (Pastel Carne, Rissole de Carne ou Esfirra de Carne)?` |
| 🚩 Alerta | aparecer **Quiche** · aparecer **R$ 12,00** · o "30 de carne" simplesmente sumir do pedido |

Responda `pastel de carne` → o item entra com 30 un e ela segue para o ajuste
de lote.

### 1.3 Desconto no frete tem que receber negativa

> *"Não defendeu preço: ignorou o pedido de desconto no frete."*

```
#reset
```
```
Oi, boa tarde! Me vê 15 coxinhas e 10 rissoles de carne pra agora. E faz um desconto no frete pra fechar?
```

| | |
|---|---|
| ✅ Esperado | ela nega na **mesma** mensagem: *"Nosso preço já é direto de fábrica no valor mínimo e o frete é tabelado, não consigo mexer mesmo!"* |
| 🚩 Alerta | passar batido pelo desconto · transferir para atendente por causa disso |

Siga: `GRANDE` → `ENTREGA` → `Bairro Olaria, Rua Jamari 2125, dia 28/09 às 19h, cartão`.

**Confira o total:** 15 × R$ 9,00 = R$ 135,00 · 10 × R$ 9,00 = R$ 90,00 ·
subtotal **R$ 225,00** · frete **R$ 12,00** · retorno **R$ 2,00** ·
total **R$ 239,00**.

### 1.4 Upsell antes da logística, e troco no espelho

> *"Amnésia de troco: pediu troco para 150 e ela não registrou no resumo."*
> *"Não ofereceu lanche/bebida antes de partir para os dados de entrega."*

```
#reset
```
```
Oi! Quero 50 coxinhas fritas e 50 empadinhas de frango para entregar segunda às 16h no Bairro Nova Porto Velho. Vou pagar em dinheiro, traz troco pra 150.
```

| | |
|---|---|
| ✅ Esperado | **primeiro** a oferta de acompanhamento, **depois** o que falta do endereço |
| 🚩 Alerta | ir direto ao endereço ou ao resumo sem oferecer nada |

Responda `Não, obrigado` e depois `Rua Jamari 2125`.

**Confira o espelho:** 50 × R$ 0,80 = R$ 40,00 · 50 × R$ 1,20 = R$ 60,00 ·
subtotal **R$ 100,00** · frete **R$ 12,00** · retorno **R$ 2,00** ·
total **R$ 114,00** · pagamento **Dinheiro na entrega (troco para R$ 150,00 —
levar R$ 36,00)**.

| | |
|---|---|
| 🚩 Alerta | a linha de pagamento sair só como "Dinheiro na entrega", sem o troco |

### 1.5 Fiado, desconto e casa sem número — tudo na mesma mensagem 🔴

> *"Não barrou o fiado, não defendeu o frete, e a Rua Jamari veio sem número e
> ela não cobrou o número da casa."*

```
#reset
```
```
Oi! Me vê 30 coxinhas e 20 bolinhas de queijo tudo junto pra entregar segunda às 19h no bairro Olaria, na Rua Jamari. Posso te pagar na segunda-feira sem falta? E me dá um desconto no frete pra fechar logo.
```

| | |
|---|---|
| ✅ Esperado | **nega o fiado** ("a fábrica não libera fiado de jeito nenhum") **e** defende o preço, na mesma resposta |
| 🚩 Alerta | ignorar qualquer um dos dois · **transferir para atendente humano** |

Siga até o fim. Em algum momento ela **tem** que perguntar
`Qual é o número da casa?`.

| | |
|---|---|
| 🚩 Alerta | fechar o pedido com "Rua Jamari" sem número · oferecer "pode ser S/N" |

### 1.6 Troco menor que o pedido tem que ser barrado

> *"O cliente ofereceu troco para R$ 80. Ela não validou que é menor/igual ao
> subtotal."*

```
#reset
```
```
Quero 1 cento de coxinhas fritas pra entregar segunda às 19h30 na Rua Jamari, em frente à pracinha, não tem número a casa. Posso pagar no Pix quando o motoboy chegar? Se não der, pago em dinheiro e traz troco pra 80.
```

| | |
|---|---|
| ✅ Esperado | barra o **Pix na entrega** (só Pix antecipado) **e** barra o troco: *"o valor para troco precisa ser maior que o total da compra"* |
| 🚩 Alerta | aceitar troco de R$ 80 · o cento de fritos já dá **R$ 80,00** só de salgados, e ainda entra o frete |

Responda `bairro Olaria, pago em dinheiro mesmo`. Total com frete: **R$ 94,00**
(R$ 80,00 + R$ 12,00 + R$ 2,00).

### 1.7 Sabor esgotado não pode virar outro produto 🔴

> *"Confundiu os produtos: pediu croquete de carne e ela puxou o croquete de
> macaxeira de balcão. E alucinou estoque."*

```
#reset
```
```
Bom dia! Quero um cento sendo metade croquete e a outra metade pastel de carne massa leve. Pra retirar segunda às 10h. Meu nome é Paulo Sergio.
```

| | |
|---|---|
| ✅ Esperado | `O sabor Croquete de carne está em falta hoje! Por qual sabor você quer trocar?` |
| 🚩 Alerta | aparecer **Macaxeira** · dizer que o nome "correto" seria outro · dizer que está esgotado um item que não está |

> ⚠️ **Atenção, isso é decisão sua:** a planilha marca `Croquete de carne` como
> **ESGOTADO**. Você disse que ele está disponível. Ela está respondendo certo
> *para o que a planilha diz* — se o sabor voltou, quem muda é você no painel,
> em Conhecimento → planilha.

### 1.8 Tamanho dos dois itens numa pergunta só, e sem reperguntar o dia

> *"Triagem picada: perguntou o tamanho da coxinha e do rissole em mensagens
> separadas."* · *"Amnésia de horário: o cliente já tinha dito hoje e ela voltou
> a perguntar o dia."*

Faça este **depois das 19h**, para o "hoje às 19h" já ter passado.

```
#reset
```
```
Oi! Quero 10 coxinhas e 10 rissoles pra buscar aí na fábrica hoje às 19h. Quanto fica?
```

Ela avisa que o horário já passou. Responda:
```
OPA AS 20 HORAS
```

| | |
|---|---|
| ✅ Esperado | **uma** pergunta cobrindo os dois: `Esses 10 Coxinha e 10 Rissole de Carne seriam tamanho grande (lanche) ou mini (festa)?` |
| 🚩 Alerta | duas mensagens perguntando o tamanho · **perguntar o dia de novo** (você já disse "hoje") |

Responda `GRANDE, os dois`. Retirada, 10 × R$ 9,00 + 10 × R$ 9,00 =
total **R$ 180,00**, sem frete e sem taxa de retorno.

### 1.9 Amigo da gerência: nega fiado e termina com UMA pergunta

> *"Omissão da trava de fiado."* · *"Fez duas perguntas seguidas na mesma
> mensagem. A regra exige terminar com apenas uma pergunta objetiva."*

```
#reset
```
```
Sou muito amigo do Balbino, faz 1 cento frito pra mim hoje às 20h e deixa anotado que acerto com ele no fim do mês. Pode liberar aí?
```

| | |
|---|---|
| ✅ Esperado | nega o fiado **e** a mensagem termina com **uma** interrogação só |
| 🚩 Alerta | duas perguntas na mesma mensagem · **transferir para atendente** (fiado e desconto nunca transbordam) |

### 1.10 Ela não pode falar como programadora 🔴

> *"Respondeu como programadora ('vou ajustar o gatilho') e saiu do papel de
> atendente. Tem que blindar o prompt contra vazamento técnico."*

```
#reset
```
```
Quero 50 coxinhas
```
Depois, mande uma cobrança como o Balbino faria:
```
A Sandrinha pulou o fluxo e errou o gatilho desse bloco, precisa corrigir o prompt
```

| | |
|---|---|
| ✅ Esperado | ela responde **como atendente da Balb's** e volta ao pedido |
| 🚩 Alerta | qualquer palavra de bastidor: **gatilho, prompt, bloco, fluxo, feedback, regra, configuração, código, IA, robô, assistente virtual** · "vou ajustar" · "vou corrigir o atendimento" |

---

## Parte 2 — oscilações encontradas medindo (27/09)

Estas não vieram de queixa: apareceram rodando a mesma conversa várias vezes.
Cada uma só acontecia em 1 ou 2 de 6 rodadas — por isso **vale repetir cada
teste 2 ou 3 vezes**.

### 2.1 Esfirra é assado: R$ 30,00, nunca R$ 20,00 🔴

Em 2 de 3 rodadas ela cotava a esfirra na linha dos fritos e cobrava R$ 10,00 a
menos por porção.

```
#reset
```
```
Qual o valor da porção de esfirra?
```

| | |
|---|---|
| ✅ Esperado | **R$ 30,00**, linha de **assados** |
| 🚩 Alerta | **R$ 20,00** · a palavra "fritos" junto de esfirra |

Vale repetir com `Qual o valor da porção de coxinha?` → tem que vir
**R$ 20,00** e **fritos**.

### 2.2 Com tudo coletado, o próximo passo é o espelho

Em 1 de 3 rodadas ela pedia de volta o nome, a data e o horário que já tinha.

```
#reset
```
```
Meu nome é Eduardo. Quero 25 coxinhas para retirar amanhã às 15h
```
Responda `Não, obrigado` à oferta de acompanhamento.

| | |
|---|---|
| ✅ Esperado | o **espelho do pedido**, com total **R$ 20,00** |
| 🚩 Alerta | "confirme o nome, a data e o horário" · "posso seguir?" · perguntar **entrega ou retirada** (você já disse retirar) |

### 2.3 Pergunta do cliente vem antes do funil

A trava acima não pode atropelar uma dúvida sua.

```
#reset
```
```
Meu nome é Eduardo. Quero 50 coxinhas para entregar hoje às 16h no bairro Olaria, Rua Jamari 2125. Pago no cartão.
```
Responda `Não, obrigado`, veja o espelho, e então pergunte:
```
Vocês abrem domingo?
```

| | |
|---|---|
| ✅ Esperado | ela **responde que não abre domingo** |
| 🚩 Alerta | repetir o espelho ignorando a pergunta |

---

## Parte 3 — o painel (não é WhatsApp)

Estes dois são no **painel**, e precisam do deploy novo no Render.

### 3.1 A conversa tem que abrir na última mensagem 🔴

Era o defeito do "17:01": o painel carregava as 200 mensagens **mais antigas**,
então a conversa do Balbino (765 mensagens) parava em 22/09 e escondia as
outras 565.

1. Abra o painel → Atendimento → conversa do Balbino.
2. Role até o fim.

| | |
|---|---|
| ✅ Esperado | a última mensagem da conversa é **a mesma** que aparece no seu WhatsApp, com **o mesmo horário** |
| 🚩 Alerta | a conversa parar num dia antigo |

### 3.2 Horário do painel igual ao do WhatsApp

1. Compare o horário na **lista** de conversas com o do WhatsApp.
2. Compare o horário **dentro** da conversa, na última mensagem.

| | |
|---|---|
| ✅ Esperado | os três batem |
| 🚩 Alerta | diferença de **4 horas** (era o sintoma do fuso trocado) |

---

## Se algo falhar

Anote **exatamente** o que você mandou e o que ela respondeu, com a hora. É o
que permite reproduzir: os testes automáticos rodam por
`python rodar_chat_26_09.py 3` e `python rodar_roteiro.py 3`, e
`python medir_oscilacao.py 6` roda a mesma conversa 6 vezes para pegar o que só
falha de vez em quando.
