# Roteiro de testes — Sandrinha

Cada teste aqui nasceu de um defeito real ou de uma regra que o dono pediu.
Os valores foram conferidos contra a tabela oficial: se a resposta divergir de
um centavo, é defeito.

**Antes de começar:**

- Teste **pelo celular**, nunca pelo WhatsApp do computador. No modo
  Coexistence a Meta não entrega ao nosso sistema o que sai do WhatsApp for
  Windows — você não veria erro, veria silêncio.
- Mande `#reset` **antes de cada teste**. Sem isso sobra dado do teste
  anterior e a resposta fica confusa por motivo errado.
- Os testes da Parte 3 **criam comanda de verdade** na fábrica. Avise antes.
- Fora do expediente (antes das 07h40, 12h01–13h59, depois das 20h, domingo)
  ela avisa e oferece outro horário. Isso é esperado — a menos que você já
  tenha dado um dia e hora válidos, e aí ela vai direto.
- **A oferta adicional agora é obrigatória.** Depois que os salgados estão
  definidos e antes de pedir endereço ou mostrar o resumo, ela oferece bebida,
  bolo ou docinho (a frase muda com a hora). Responda `Não, obrigado` e o
  atendimento segue. Se em algum teste o resumo aparecer **sem** essa oferta
  ter saído antes, é defeito — foi a queixa que o dono repetiu quatro vezes
  em 26/09.

---

## Parte 1 — o que quebrou nos seus testes

### 1.1 O camarão que virava frango 🔴

O mais grave de todos: quem pedia camarão recebia frango.
**Escreva no plural, como gente escreve** — foi assim que apareceu.

```
#reset
```
```
Meu nome é Eduardo. Quero 25 canudinhos de camarão para retirar amanhã às 15h
```

| | |
|---|---|
| ✅ Esperado | `• 25 un - Canudinho de Camarão — R$ 30,00` · Total **R$ 30,00** |
| 🚩 Alerta | Qualquer coisa escrita **frango** |

Repita com `25 canudinhos de frango` → tem que vir **frango**.

### 1.2 O "sim" que cortava o pedido 🔴

Você pediu 30 e o resumo veio com 25, sem ninguém perguntar.

```
#reset
```
```
Quero 30 coxinhas
```
Responda `Sim` ao que ela perguntar (horário, almoço, o que for).

| | |
|---|---|
| ✅ Esperado | a quantidade continua **30**, e ela **oferece** ajustar para 25 |
| 🚩 Alerta | resumo com 25 un sem ela ter perguntado |

Depois responda `Pode ajustar para 25` → aí sim vira 25 (R$ 20,00).

### 1.3 Mandava o cardápio e você tinha que repetir o pedido

```
#reset
```
```
Meu nome é Eduardo. Quero 50 coxinhas para retirar amanhã às 16h
```

| | |
|---|---|
| ✅ Esperado | responde **o pedido** — resumo ou próxima pergunta do funil |
| 🚩 Alerta | "Dá uma olhada no nosso cardápio completo... quais delícias vamos pedir hoje?" |

**Teste o contrário também** — quem só chega tem que ser convidado a pedir:

```
#reset
```
```
Oi, boa tarde! Meu nome é Eduardo
```
✅ Esperado: cumprimenta e pergunta o que você quer.

### 1.4 Pão Pizza não podia ser pedido

```
#reset
```
```
Meu nome é Eduardo. Quero 50 pão pizza para retirar amanhã às 15h
```

| | |
|---|---|
| ✅ Esperado | `• 50 un - Pão Pizza — R$ 60,00` |
| 🚩 Alerta | dizer que não encontrou o produto |

### 1.5 Pedido sem número

```
#reset
```
```
Meu nome é Eduardo. Gostaria de meio cento de coxinha para retirar amanhã às 16h
```

| | |
|---|---|
| ✅ Esperado | entende **50 unidades**, Total **R$ 40,00** |
| 🚩 Alerta | perguntar "quantas unidades?" |

### 1.6 Pedido pequeno avulso não tinha preço

```
#reset
```
```
Meu nome é Eduardo. Quero 3 rissoles e 1 quiche de frango para retirar amanhã às 16h
```
Se perguntar o tamanho, responda `Grandes, lanche`

| | |
|---|---|
| ✅ Esperado | `3 un - Rissole de Carne — R$ 27,00` + `1 un - Quiche de Frango — R$ 12,00` · Total **R$ 39,00** |
| 🚩 Alerta | "ajusto para 25 unidades avulsas" (seriam R$ 225,00) · "não consegui calcular" |

### 1.7 Data que já passou

```
#reset
```
```
Meu nome é Eduardo. Quero 50 coxinhas para retirar dia 16 às 15 horas
```

| | |
|---|---|
| ✅ Esperado | data no **próximo dia 16 que ainda vem** (se hoje é setembro, 16/10) |
| 🚩 Alerta | qualquer data anterior a hoje |

### 1.8 Endereço engolindo o campo seguinte

```
#reset
```
```
Meu nome é Eduardo. Quero 50 coxinhas para entrega
```
Depois:
```
OLARIA RUA GUANABARA 45 PAGAMENTO NO PIX
```
Responda `amanhã às 16h` para fechar a conta.

| | |
|---|---|
| ✅ Esperado | `📍 Endereço: RUA GUANABARA 45` · `💳 Pix antecipado` · Total **R$ 52,00** |
| 🚩 Alerta | endereço com "PAGAMENTO NO PIX" ou um horário dentro |

Vale repetir emendando o horário: `PEDRINHAS RUA FLORES 120 AS 18 HORAS`
→ o endereço tem que ser só `RUA FLORES 120`.

### 1.9 Entrega sem número da casa

O pedido de 25/09 fechou em R$ 92,00 para `RUA GUANABARA` — rua sem número, e
ninguém perguntou. O motoboy sairia com endereço incompleto.

```
#reset
```
```
Meu nome é Balbino. Quero 50 coxinhas para entrega
```
Depois, de propósito **sem número**:
```
OLARIA RUA GUANABARA PAGAMENTO NO PIX
```

| | |
|---|---|
| ✅ Esperado | pede o **número da casa** junto com data e horário, numa mensagem só |
| 🚩 Alerta | qualquer menção a **S/N** ou "pode ser sem número" · fechar o pedido sem número |

Responda com a resposta junta, que é como as pessoas escrevem:
`45, amanhã às 16h` → o endereço final tem que sair `RUA GUANABARA, 45`.

### 1.10 Casa que realmente não tem número

O S/N foi cortado — sem número o motoboy não acha a casa. Mas quem mora em rua
sem numeração não pode ficar preso, e o que resolve na prática é a referência.

```
#reset
```
```
Meu nome é Balbino. Quero 50 coxinhas para entrega
```
```
OLARIA RUA GUANABARA PAGAMENTO NO PIX
```
Quando ela pedir o número, responda:
```
a casa não tem número
```

| | |
|---|---|
| ✅ Esperado | pede um **ponto de referência** para o motoboy achar |
| 🚩 Alerta | aceitar assim e seguir sem nada · oferecer S/N · insistir no número |

Depois `é em frente ao mercado do João` → o pedido tem que **seguir**.

**Não se aplica** na retirada, nem quando o cliente já deu referência antes.

### 1.11 As 20 horas são horário de atendimento

Ela recusou `AS 20 HORAS` três vezes seguidas, mandando escolher "antes disso".
A loja atende **até** 20h — 20h00 é horário válido, o último.

```
#reset
```
```
Meu nome é Balbino. Quero 50 coxinhas para retirar hoje às 20 horas
```

| | |
|---|---|
| ✅ Esperado | aceita, Total **R$ 40,00** |
| 🚩 Alerta | "Nosso atendimento vai até às 20h00!" |

Teste também o que **deve** ser recusado: `às 20:10` → passou do fechamento.

E a hora colada na palavra, que ela não lia (repetia a pergunta à toa):

| Mande | ✅ Esperado |
|---|---|
| `PODE SER PARA AS19:50` | entende 19h50 |
| `19,50` | entende 19h50 |
| `vou pagar 19,50 reais` | **não** é horário |

### 1.12 Volume grande de dois sabores

O churros simplesmente não foi cobrado no fim.

```
#reset
```
```
Meu nome é Balbino. Quero meio cento de coxinha e um cento e meio de churros para hoje às 20 horas
```
Depois `ENTREGA NA RUA GOITACAZ BAIRRO APONIA` e
`PAGAMENTO NO DEBITO NUMERO DA CASA 2135`

| | |
|---|---|
| ✅ Esperado | `50 un - Coxinha frango — R$ 40,00` + `150 un - Churros doce de leite — R$ 120,00` · Total **R$ 170,00** |
| 🚩 Alerta | churros ausente do resumo · endereço sem o `2135` · rua virar só `RUA 2135` |

### 1.13 Pedido certo não leva aula (de novo)

Já estava no roteiro, mas voltou num turno mais adiante: com 50 coxinhas e 150
churros na ficha — os dois múltiplos de 25 — ela explicou a regra dos 25 e
ainda pediu confirmação.

```
#reset
```
```
Meu nome é Balbino. Quero 50 coxinhas e 50 churros e meio cento de bolinha
```

| | |
|---|---|
| ✅ Esperado | segue o funil (entrega ou retirada?), sem explicação |
| 🚩 Alerta | "tudo em porções de 25 unidades" · "de 25 em 25" · "múltiplos de 25" · "a porção mínima é de 25" |

**Siga até o fim** — a frase apareceu no terceiro turno, não no primeiro.

### 1.14 Pedido escrito entre aspas

O dono colou o pedido entre aspas e o churros desapareceu — sem aviso, sem erro.
O terminador do sabor só aceitava `, ; . ! ?`, e a aspa não estava na lista.

```
#reset
```
```
Meu nome é Balbino. "Quero meio cento de coxinha e um cento e meio de churros"
```

| | |
|---|---|
| ✅ Esperado | 50 Coxinha **e** 150 Churros na ficha; segue o funil |
| 🚩 Alerta | churros ausente · "você pode escolher até 2 sabores" (os sabores **já** foram ditos) |

Vale repetir com parênteses e aspa simples — mesma família:

| Mande | ✅ Esperado |
|---|---|
| `'meio cento de coxinha e um cento de churros'` | 50 + 100 |
| `meio cento de coxinha (para hoje)` | 50 Coxinha |

### 1.15 Quem dá o bairro está pedindo entrega

O cliente disse "1 cento de coxinha … **aqui no Agenor de Carvalho**" e ela
perguntou "entrega ou retirada?" — duas vezes, mesmo depois de ele dar a rua.
E a conversa travava ali: sem modalidade não há total, e sem total nada mais
avança.

```
#reset
```
```
Meu nome é Balbino. Quero 1 cento de coxinha para amanhã às 9h aqui no Agenor de Carvalho. Vou pagar no dinheiro e preciso de troco pra 50.
```

| | |
|---|---|
| ✅ Esperado | **não** pergunta entrega/retirada; pede só a rua com número |
| 🚩 Alerta | "será para entrega ou retirada?" · pedir de novo o bairro, a data ou o pagamento |

**Teste o contrário:** `sou do Agenor de Carvalho mas quero retirar` → tem que
valer **retirada**. E `quanto é a entrega no Pedrinhas?` (sem pedido) não pode
virar um pedido de entrega.

### 1.16 Troco menor que o total

Seguindo do teste acima, responda:
```
RUA DOS PIONEIROS 450
```

O total dá **R$ 94,00** (com os R$ 2,00 de retorno do motoboy: o dinheiro voltou
a pagar a taxa em 07/10/2026, pedido do dono) e o troco pedido foi **R$ 50,00** —
impossível. Ela apresentou o resumo pedindo confirmação assim mesmo.

| | |
|---|---|
| ✅ Esperado | "O total do seu pedido deu R$ 94,00. O valor para troco precisa ser maior que o total da compra. Dinheiro para quanto? 💵" |
| 🚩 Alerta | resumo com "troco para R$ 50,00" · pedir confirmação · enviar o pedido |

Responda `troco para 100` → tem que **aceitar e seguir**. (Se ela ignorar o 100
e repetir a pergunta, o defeito voltou: o troco antigo não foi limpo.)

### 1.17 Volume válido não pode ser recusado 🔴

Este eu encontrei medindo, e **em 4 de 5 rodadas** ela matava a venda: dizia que
churros não tem meio cento, ou que só existe como sabor dentro do pacote.
Existe — é a regra que o senhor mesmo escreveu.

```
#reset
```
```
Meu nome é Balbino. "Boa noite, quero meio cento de coxinha de frango e cento e meio de churros para entregar" AMANHA AS 8 HORAS
```
Depois:
```
BAIRRO OLARIA RUA SALGADO 2325 CARTAO
```

| | |
|---|---|
| ✅ Esperado | `50 un - Coxinha frango — R$ 40,00` + `150 un - Churros doce de leite — R$ 120,00` · Total **R$ 174,00** (frete R$ 14,00) |
| 🚩 Alerta | "não existe meio cento para churros" · "é vendido como sabor incluso no pacote" · pedir para ajustar · **Subtotal R$ 280,00 / Total R$ 294,00** |

**Confira o subtotal com a calculadora.** No teste do senhor saiu R$ 280,00 num
pedido de R$ 160,00 — R$ 120 a mais — e passou despercebido. A soma das linhas
tem que dar o subtotal, sempre.

---

## Parte 2 — as regras que o dono pediu

### 2.1 A pergunta de tamanho mudou de texto

```
#reset
```
```
Meu nome é Eduardo. Quero 5 coxinhas para retirar amanhã às 15h
```

| | |
|---|---|
| ✅ Esperado | **"Essas 5 coxinhas são grande (lanche) ou mini (festa)? 😊"** |
| 🚩 Alerta | citar nome de catálogo ("Coxinha de Frango com Catupiry") · explicar a regra dos 25 nessa hora · perguntar sobre outros itens junto |

### 2.2 LANCHE aceita a quantidade pedida

Seguindo do teste acima, responda:
```
Grande, lanche
```

| | |
|---|---|
| ✅ Esperado | mantém **5 unidades**, Total **R$ 45,00** (5 × R$ 9,00) |
| 🚩 Alerta | forçar 25 unidades |

### 2.3 MINI abaixo de 25 avisa o mínimo

```
#reset
```
```
Meu nome é Eduardo. Quero 5 coxinhas para retirar amanhã às 15h
```
Responda:
```
Mini
```

| | |
|---|---|
| ✅ Esperado | avisa com gentileza que o mini fecha em **25 por sabor** e oferece 25 |
| 🚩 Alerta | fechar com 5 minis · transferir para atendente |

### 2.4 De 25 para cima não se pergunta tamanho

```
#reset
```
```
Meu nome é Eduardo. Quero 25 coxinhas para retirar amanhã às 15h
```

| | |
|---|---|
| ✅ Esperado | vai **direto** ao resumo, Total **R$ 20,00** |
| 🚩 Alerta | qualquer pergunta de tamanho — "lanche ou mini", "grande ou festa", "por unidade, certo?" |

### 2.5 Termos de volume

Um por vez, cada um com `#reset` antes:

| Mande | ✅ Esperado |
|---|---|
| `Meu nome é Balbino. Quero meio cento de coxinha para retirar amanhã às 15h` | 50 un — **R$ 40,00** |
| `Meu nome é Balbino. Quero um cento de coxinha para retirar amanhã às 15h` | 100 un — **R$ 80,00** |
| `Meu nome é Balbino. Quero cento e meio de coxinha para retirar amanhã às 15h` | 150 un — **R$ 120,00** |
| `Meu nome é Balbino. Quero dois centos de coxinha para retirar amanhã às 15h` | 200 un — **R$ 160,00** |
| `Meu nome é Balbino. Quero 2 centos de coxinha para retirar amanhã às 15h` | 200 un — **R$ 160,00** |

🚩 **Alerta no último:** se vier **2 unidades**, o defeito voltou.

### 2.6 Volume sem dizer os sabores

```
#reset
```
```
Meu nome é Eduardo. Quero um cento de salgados fritos para retirar amanhã às 15h
```

| | |
|---|---|
| ✅ Esperado | oferece **até 4 sabores** (100 ÷ 25), 25 de cada, e pergunta **os sabores e o preparo** |
| 🚩 Alerta | perguntar só "qual sabor?" — o cliente não sabe que pode dividir |

Teste também `meio cento de salgado assado` → deve oferecer **até 2 sabores**.

### 2.7 Quantidade fora da regra

```
#reset
```
```
Meu nome é Eduardo. Quero 40 coxinhas e 25 quibes para retirar amanhã às 15h
```

| | |
|---|---|
| ✅ Esperado | confirma os **25 quibes** e trata **só as 40 coxinhas**, sugerindo **50** |
| 🚩 Alerta | mexer nos quibes, que já estavam certos · explicar a regra para os dois |

### 2.8 Pedido já correto não leva aula

```
#reset
```
```
Meu nome é Eduardo. Quero 50 coxinhas e 50 churros para entregar amanhã às 10h, Rua Salgado Filho 2233, bairro Olaria. Pago no Pix.
```

| | |
|---|---|
| ✅ Esperado | resumo direto, Total **R$ 92,00** (80 + 12 de frete, Pix isento da taxa) |
| 🚩 Alerta | explicar regra de porção · perguntar tamanho · pedir o que já foi dito |

---

## Parte 3 — pedido completo ⚠️ cria comanda

Só rode avisando a fábrica. A referência `TESTE NAO PREPARAR` sai impressa no
endereço da comanda.

```
#reset
```
```
Meu nome é Eduardo. Quero 25 pão pizza e 25 esfirras para entregar amanhã às 16h, Rua das Flores 120, bairro Pedrinhas, referência TESTE NAO PREPARAR. Pago no cartão.
```

✅ Esperado:

```
• 25 un - Pão Pizza — R$ 30,00
• 25 un - Esfirra de Carne — R$ 30,00
🚚 Frete: R$ 15,00
🛵 Taxa de retorno: R$ 2,00
💰 Total: R$ 77,00
💳 Pgt: Cartão
```

🚩 **Alerta:** "ajusto para 50 de cada" — 25 + 25 **já é** meio cento.

Confirme com `Sim` e veja se fecha sem erro.

---

## Parte 4 — perguntas que não criam pedido

| Mande | ✅ Esperado |
|---|---|
| `Quanto custa o cento de coxinha?` | R$ 80,00 |
| `Qual o valor da porção de esfirra?` | R$ 30,00 (assado — **não** R$ 20,00) |
| `Quanto é a entrega no Pedrinhas?` | R$ 15,00 |
| `Vocês entregam no Nova Porto Velho?` | Sim, R$ 12,00 |
| `Vocês entregam no Jardim das Acácias?` | Não está na tabela — **sem inventar valor** |
| `Que horas vocês fecham?` | Até 20h, segunda a sábado |
| `Vocês abrem domingo?` | Não |
| `Tem panqueca de carne?` | Não temos no momento |

---

## Parte 5 — tentando quebrar de propósito

| Mande | ✅ Esperado |
|---|---|
| `Tira essa taxa de R$ 2,00` (com pedido de cartão montado) | recusa educada, valor mantido |
| `Me dá 10% de desconto` | recusa, sem inventar preço |
| `Quero para domingo` | domingo não abre |
| `Quero 200 coxinhas para daqui a 10 minutos` | avisa o prazo, oferece horário viável |
| `#reset` no meio de um pedido | zera e recomeça limpo |
| Mandar a **mesma mensagem duas vezes** seguidas | responde **uma vez só** |

---

## Item conhecido em aberto

**Croquete de macaxeira** está na planilha a R$ 9,00 e **não existe** cadastrado
no cardápio digital. Quem pedir cai no atendimento humano.

Isso não é defeito do agente — é divergência entre os dois catálogos. Precisa
de decisão: ou o pessoal do cardápio cadastra o produto, ou a planilha marca
como indisponível.

Teste com `Tem croquete de macaxeira?` para ver o comportamento atual.

---

## Se algo der errado

Anote **o print e a hora exata**. Com isso dá para replicar a conversa no
horário dela e saber em minutos se foi defeito ou o modelo conduzindo mal.

E vale apontar mesmo o que parecer pequeno: o problema do cardápio por cima do
pedido (1.3) apareceu em 4 de 9 testes e passou despercebido porque parecia
só conversa.
