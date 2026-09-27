"""Roda o TESTES_SANDRINHA.md e mede o gasto exato.

Le o roteiro em vez de manter uma copia das mensagens: copia envelhece, e uma
checagem que nao corresponde ao que o dono vai testar nao serve de nada.

    python rodar_roteiro.py --listar          # mostra o que entendeu, sem gastar
    python rodar_roteiro.py 3                 # roda 3 rodadas por teste
"""
import sys, io, re, contextlib, time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import os

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

ROTEIRO = os.path.join(RAIZ, "TESTES_SANDRINHA.md")
USD_BRL = 5.184277

# Parte 3 cria comanda de verdade na fabrica: fica com o dono, avisando antes.
PARTES_FORA = ("Parte 3",)

_RX_VALOR = re.compile(r"R\$\s*([\d.]+,\d{2})")
_RX_PARENTESE = re.compile(r"\([^)]*\)")


def valores_esperados(texto: str) -> list:
    """Valores que a resposta TEM de trazer.

    O que esta entre parenteses nao conta: no roteiro, "(5 x R$ 9,00)" explica de
    onde vem o total, e "(assado - nao R$ 20,00)" e justamente o valor que NAO
    pode aparecer. Tratar os dois como esperados dava falha falsa em dois testes
    que na verdade passaram.
    """
    return _RX_VALOR.findall(_RX_PARENTESE.sub(" ", texto))
_RX_INLINE = re.compile(r"`([^`]{3,120})`")
# Prosa que pede OUTRA mensagem na MESMA conversa.
_RX_CONTINUA = re.compile(
    r"^(?:Responda|Depois responda|Depois|Confirme com|"
    r"Se perguntar o tamanho, responda)", re.IGNORECASE)
# Prosa que pede o teste DE NOVO, do zero. "Repita com 25 canudinhos de frango"
# nao e um segundo item no mesmo pedido: e o mesmo teste com outro sabor. Emendar
# como continuacao dava falha falsa - o pedido ficava com os dois itens.
_RX_DE_NOVO = re.compile(
    r"^(?:Repita com|Repita|Teste tamb[eé]m|Teste o contr[aá]rio|Vale repetir|"
    r"Vale testar)", re.IGNORECASE)
# Bloco que e RESPOSTA esperada, nao mensagem a enviar.
_RX_BLOCO_DE_SAIDA = re.compile(r"^[\u2022\U0001f4b0\U0001f69a\U0001f6f5\U0001f4b5"
                                r"\U0001f464\U0001f4c5\u23f0\U0001f4cd\U0001f4b3\U0001f4cb]",
                                re.MULTILINE)


def nomeia_produto(frase: str) -> bool:
    """A frase cita algum produto do catalogo? Usa o matcher que ja existe."""
    try:
        from agents.conversation import _produto_conhecido
        import facts
        palavras = re.split(r"[^a-zà-ÿ]+", facts.normalize(frase))
        return any(len(p) > 3 and _produto_conhecido(p) for p in palavras)
    except Exception:
        return False


class Teste:
    def __init__(self, parte, titulo):
        self.parte, self.titulo = parte, titulo
        self.msgs, self.esperado, self.alertas = [], [], []
        self.continua = False          # "Seguindo do teste acima"

    def __repr__(self):
        return f"<{self.titulo}: {len(self.msgs)} msgs>"


def ler_roteiro(caminho=ROTEIRO):
    linhas = io.open(caminho, encoding="utf-8").read().splitlines()
    testes, parte, atual, i = [], "", None, 0
    while i < len(linhas):
        l = linhas[i]
        if l.startswith("## "):
            parte = l[3:].strip()
            # Zera a secao corrente. Sem isto as Partes 4 e 5, que nao tem
            # cabecalho "###" e vao direto para a tabela, grudavam no ultimo
            # teste da Parte 2: cada pergunta de preco herdava as mensagens e os
            # valores esperados de um PEDIDO, e a Parte 3 (excluida) despejava os
            # valores dela ali tambem.
            atual = Teste(parte, parte)
        elif l.startswith("### "):
            atual = Teste(parte, l[4:].strip())
            if parte not in PARTES_FORA:
                testes.append(atual)
        elif l.strip() == "```" and atual is not None:
            j, corpo = i + 1, []
            while j < len(linhas) and linhas[j].strip() != "```":
                corpo.append(linhas[j])
                j += 1
            texto = "\n".join(corpo).strip()
            if texto and texto.lower() != "#reset":
                if _RX_BLOCO_DE_SAIDA.search(texto):
                    atual.esperado += valores_esperados(texto)   # espelho esperado
                else:
                    atual.msgs.append(" ".join(texto.split()))
            i = j
        elif atual is not None and l.startswith("|"):
            celulas = [c.strip() for c in l.strip().strip("|").split("|")]
            if len(celulas) >= 2:
                rotulo, resto = celulas[0], " ".join(celulas[1:])
                if "\u2705" in rotulo:
                    atual.esperado += valores_esperados(resto)
                elif "\U0001f6a9" in rotulo:
                    atual.alertas.append(resto)
                elif rotulo.startswith("`"):          # tabela "Mande | Esperado"
                    m = _RX_INLINE.search(rotulo)
                    if m and not m.group(1).lstrip().startswith("#"):
                        filho = Teste(parte, f"{atual.titulo} > {m.group(1)[:38]}")
                        # Herda as mensagens em bloco da secao: "19,50" e
                        # "PODE SER PARA AS19:50" sao respostas, nao pedidos -
                        # sozinhas a conversa empaca pedindo nome e item. Nas
                        # Partes 4 e 5 a secao nao tem bloco, e a linha da
                        # tabela e mesmo um teste inteiro.
                        filho.msgs = list(atual.msgs) + [m.group(1)]
                        filho.esperado = valores_esperados(resto)
                        testes.append(filho)
        elif atual is not None and _RX_DE_NOVO.match(l.strip()):
            for m in _RX_INLINE.findall(l):
                if not m.startswith("#") and len(m.split()) <= 14:
                    novo = Teste(parte, f"{atual.titulo} > de novo: {m[:32]}")
                    # Duas coisas diferentes vem escritas igual no roteiro:
                    #   "Repita com 25 canudinhos de frango" -> outro PEDIDO,
                    #     entao troca a ultima mensagem (senao o pedido fica com
                    #     camarao E frango, e a checagem falha por engano);
                    #   "Teste tambem as 20:10" -> mesmo pedido, outro dado,
                    #     entao acrescenta (sozinha, a frase nao e pedido nenhum).
                    # Quem decide e o catalogo: a frase nomeia produto ou nao.
                    novo.msgs = ((list(atual.msgs[:-1]) + [m]) if nomeia_produto(m)
                                 else list(atual.msgs) + [m]) if atual.msgs else [m]
                    testes.append(novo)
        elif atual is not None and _RX_CONTINUA.match(l.strip()):
            if re.search(r"seguindo do teste", l, re.IGNORECASE):
                atual.continua = True
            # A frase pode quebrar em duas linhas: "Depois `A` e" / "`B`". Sem
            # juntar, a segunda mensagem sumia e o teste falhava por engano -
            # foi o que aconteceu com o 1.12 (faltou o pagamento e o numero).
            trecho = l
            k = i + 1
            while (k < len(linhas) and linhas[k].strip().startswith("`")
                   and linhas[k].strip() != "```"):
                trecho += " " + linhas[k]
                k += 1
            # A seta separa o que se MANDA do que se ESPERA: "`45, amanha as
            # 16h` -> o endereco final tem que sair `RUA GUANABARA, 45`". Sem
            # cortar ali, o resultado esperado ia como mensagem (1.9).
            antes_da_seta = trecho.split("→")[0]
            for m in _RX_INLINE.findall(antes_da_seta):
                if not m.startswith("#") and len(m.split()) <= 14:
                    atual.msgs.append(m)
            i = k - 1
        elif atual is not None and re.search(r"seguindo do teste", l, re.IGNORECASE):
            atual.continua = True
        i += 1

    # Teste que continua o anterior herda as mensagens dele.
    for k, t in enumerate(testes):
        if t.continua and k > 0:
            t.msgs = testes[k - 1].msgs + t.msgs
    return [t for t in testes if t.msgs]


# ── modo listar: valida a leitura sem gastar um centavo ────────────────────
if "--listar" in sys.argv:
    ts = ler_roteiro()
    por_parte = {}
    for t in ts:
        por_parte.setdefault(t.parte, []).append(t)
    total = 0
    for parte, lista in por_parte.items():
        print(f"== {parte} ({len(lista)} testes) ==")
        for t in lista:
            total += len(t.msgs)
            val = f"  espera {t.esperado}" if t.esperado else ""
            cont = "  [continua o anterior]" if t.continua else ""
            print(f"  {t.titulo[:54]:56} {len(t.msgs)} msg{val}{cont}")
            for m in t.msgs:
                print(f"       -> {m[:86]}")
        print()
    print(f"{len(ts)} testes, {total} mensagens por rodada")
    sys.exit(0)

# ── rodagem ────────────────────────────────────────────────────────────────
import test_agente as t_ag, app, horario, memory, llm

t_ag._instalar_stubs()
app.cardapio.em_modo_teste = lambda: False
app.perform_transfer = lambda p, reason="", send_message=True: True

# O telefone do dono TEM nome salvo no CRM - foi por isso que ela respondeu
# "Prazer, Balbino!" sem ele se apresentar. O stub padrao devolve nome vazio, e
# a conversa empacava pedindo o nome em testes cuja mensagem nao o traz (1.2, por
# exemplo). Ja me custou tres diagnosticos errados nesta semana.
import database
database.get_contact = lambda phone: {"phone": phone, "name": "Balbino", "auto_mode": 1}

RODADAS = int(next((a for a in sys.argv[1:] if a.isdigit()), 3))
AGORA = datetime(2026, 9, 28, 10, 0, 0)     # segunda, 10h: dentro do expediente
_limitadas = [0]


_sem_credito = [False]


def conversa(msgs):
    """Uma conversa inteira. O 429 e ruido meu, nao defeito: refaz.

    Credito esgotado e outra coisa: repetir nao resolve, so gasta tempo. Levanta
    na hora, para nao rodar 50 testes contra uma API morta e depois eu ler
    "resposta vazia" como oscilacao do modelo - erro que ja cometi duas vezes.
    """
    horario.agora = lambda: AGORA
    out = []
    for tentativa in (1, 2, 3):
        t_ag._ficha.clear()
        t_ag._enviadas.clear()
        app._orders_sent.clear()
        memory._store.pop(t_ag.TELEFONE, None)
        out, limitou = [], False
        fila = list(msgs)
        # A OFERTA ADICIONAL (BLOCO 2) virou etapa obrigatoria do funil em
        # 26/09: o dono cobrou quatro vezes que ela some, e agora o resumo nao
        # sai antes de ela ser feita. Nenhum roteiro previa essa resposta, e sem
        # ela 13 testes paravam na oferta e "faltava" o total - o valor estava
        # certo, so nao tinha chegado a vez dele. Um cliente de verdade responde
        # a oferta; o roteiro tambem. Uma vez por conversa, e so quando a
        # ultima resposta E a oferta.
        respondeu_oferta = False
        while fila:
            m = fila.pop(0)
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                out.append(t_ag.enviar(m) or "")
            log = buf.getvalue()
            if "no credits" in log or "insufficient_quota" in log:
                _sem_credito[0] = True
                raise SystemExit(
                    "\nABORTADO: a OpenAI respondeu 'no credits remaining'.\n"
                    "Recarregue os creditos e rode de novo - nada foi medido."
                )
            if "429" in log or "Rate limit" in log:
                limitou = True
            if (not fila and not respondeu_oferta
                    and app._ofereceu_acompanhamento(out[-1])):
                respondeu_oferta = True
                fila.append("Não, obrigado")
        if not limitou:
            return out
        _limitadas[0] += 1
        time.sleep(20 * tentativa)
    return out


def alerta_que_bateu(alertas, texto):
    """Termo destacado num 'Alerta' do roteiro que apareceu na resposta."""
    baixo = texto.lower()
    for a in alertas:
        for par in re.findall(r"\*\*([^*]{4,40})\*\*|\"([^\"]{4,40})\"", a):
            termo = (par[0] or par[1]).strip().lower()
            if termo and termo in baixo:
                return termo
    return ""


llm.zerar_consumo()
testes = ler_roteiro()

# "--so N" roda apenas os N primeiros testes: serve para validar o mecanismo
# gastando centavos, antes de soltar a bateria inteira.
_so = next((a for a in sys.argv[1:] if a.startswith("--so")), "")
if "=" in _so:
    testes = testes[: int(_so.split("=")[1])]

# "--filtro=1.12" roda so os testes cujo titulo contem o texto: serve para
# reconferir um teste que falhou sem pagar a bateria inteira de novo.
_filtro = next((a for a in sys.argv[1:] if a.startswith("--filtro=")), "")
if _filtro:
    alvo = _filtro.split("=", 1)[1].lower()
    testes = [t for t in testes if alvo in t.titulo.lower()]

print("=" * 78)
print(f"TESTES_SANDRINHA.md - {len(testes)} testes, {RODADAS} rodadas cada")
print("=" * 78)
print()

problemas = []
for tst in testes:
    acertos, exemplo, motivo = 0, "", ""
    for _ in range(RODADAS):
        r = conversa(tst.msgs)
        tudo = " ".join(r)
        ultima = r[-1] if r else ""
        faltou = [v for v in sorted(set(tst.esperado)) if v not in tudo]
        alerta = alerta_que_bateu(tst.alertas, ultima)
        vazia = not ultima.strip()
        if not faltou and not alerta and not vazia:
            acertos += 1
        else:
            exemplo = ultima[:120].replace("\n", " ")
            motivo = ("resposta vazia" if vazia
                      else f"faltou {faltou}" if faltou
                      else f"alerta: {alerta!r}")
    marca = "OK  " if acertos == RODADAS else "FALHA"
    print(f"  {marca} {acertos}/{RODADAS}  {tst.titulo[:58]}")
    if acertos != RODADAS:
        problemas.append((tst.titulo, acertos, motivo))
        print(f"           {motivo} | saiu: {exemplo!r}")

c = llm.custo(USD_BRL)
print()
print("=" * 78)
print("GASTO REAL (usage da OpenAI, nao estimativa)")
print("=" * 78)
print(f"  chamadas de API : {c['chamadas']}")
print(f"  entrada         : {c['entrada']} tokens "
      f"({c['cache']} do cache, {c['entrada_cheia']} a preco cheio)")
print(f"  saida           : {c['saida']} tokens")
print(f"  custo           : US$ {c['usd']:.4f}  =  R$ {c['brl']:.2f}")
print(f"  por chamada     : R$ {c['brl_por_chamada']:.4f}")
print(f"  refeitas por limite de taxa: {_limitadas[0]}")
print()
print("PROBLEMAS:", [f"{t} ({a}/{RODADAS})" for t, a, _ in problemas] or "nenhum")
sys.exit(1 if problemas else 0)
