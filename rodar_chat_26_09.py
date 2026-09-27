"""rodar_chat_26_09.py — replica as conversas do chat.txt do dono (26/09).

Cada cenario aqui e uma conversa que ele testou no WhatsApp naquela noite, com
a queixa que ele escreveu em seguida. O teste nao confere texto bonito: confere
exatamente o que ele cobrou — que a regra dos lotes trave antes da logistica,
que o upsell saia, que fiado e desconto recebam a negativa, que o troco entre
no espelho, que ela nao fale como programadora e que a mensagem termine com uma
pergunta so.

    python rodar_chat_26_09.py          # 1 rodada por cenario
    python rodar_chat_26_09.py 3        # 3 rodadas (o modelo varia)
"""
import contextlib
import io
import re
import sys
import time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(override=True)

import test_agente as t_ag  # noqa: E402
import app  # noqa: E402
import database  # noqa: E402
import horario  # noqa: E402
import llm  # noqa: E402
import memory  # noqa: E402

t_ag._instalar_stubs()
app.cardapio.em_modo_teste = lambda: False
app.perform_transfer = lambda p, reason="", send_message=True: True
database.get_contact = lambda phone: {"phone": phone, "name": "Balbino", "auto_mode": 1}

# A hora do teste do dono: sabado, 26/09, 19h20. E ela importa — a oferta
# adicional das 17h01 as 20h00 e refrigerante/docinhos, e "hoje as 19h" ja
# passou.
AGORA = datetime(2026, 9, 26, 19, 20, 0)
USD_BRL = 5.184277
RODADAS = int(next((a for a in sys.argv[1:] if a.isdigit()), 1))


_AGORA = [AGORA]


def conversa(msgs: list) -> list:
    """A conversa inteira. Refaz quando a OpenAI devolve 429.

    Sem isto o limite de taxa vira diagnostico errado: na varredura de 27/09 uma
    rodada caiu em 429, o codigo respondeu pelo fallback de falha (que nao passa
    pela cadeia de conferencia) e o relatorio acusou "a negativa de fiado sumiu"
    - defeito que nao existia. Ruido de rede tem de ser refeito, nao medido.
    """
    horario.agora = lambda a=_AGORA[0]: a
    for tentativa in (1, 2, 3):
        t_ag._ficha.clear()
        t_ag._enviadas.clear()
        app._orders_sent.clear()
        memory._store.pop(t_ag.TELEFONE, None)
        out, limitou = [], False
        for m in msgs:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                out.append(t_ag.enviar(m) or "")
            log = buf.getvalue()
            if "no credits" in log or "insufficient_quota" in log:
                raise SystemExit(
                    "\nABORTADO: a OpenAI respondeu 'no credits remaining'. "
                    "Nada foi medido."
                )
            if "429" in log or "Rate limit" in log:
                limitou = True
        if not limitou:
            return out
        print(f"    (429 na rodada; refazendo em {20 * tentativa}s)")
        time.sleep(20 * tentativa)
    return out


def norm(t: str) -> str:
    import facts
    return facts.normalize(t or "")


# ── as checagens, na lingua da queixa do dono ──────────────────────────────
def trava_lote(r: list) -> str:
    """A primeira resposta trata do lote e NAO cobra logistica."""
    primeira = r[0]
    n = norm(primeira)
    if "25 em 25" not in n and "25 por sabor" not in n and "porcoes de 25" not in n:
        return "a 1a resposta nao travou a regra dos 25"
    if re.search(r"\bforma de pagamento\b|\bpix antecipado\b", n):
        return "cobrou pagamento com o lote ainda quebrado"
    return ""


def cita_todos_os_sabores(*sabores):
    def _ck(r):
        n = norm(r[0])
        faltou = [s for s in sabores if norm(s) not in n]
        return f"a frase de ajuste nao citou {faltou}" if faltou else ""
    return _ck


def sem_vazamento(r: list) -> str:
    for i, m in enumerate(r):
        achado = app._RX_VAZAMENTO_TECNICO.search(m)
        if achado:
            return f"msg {i + 1} falou de {achado.group(0)!r}"
    return ""


def uma_pergunta(r: list) -> str:
    for i, m in enumerate(r):
        if app._RX_LINHA_DE_RESUMO.search(m):
            continue
        # Lista vertical de coleta: o "?" unico fica no cabecalho.
        perguntas = [p for p in app._RX_FRASE_COM_PERGUNTA.findall(m) if p.strip()]
        if len(perguntas) > 1:
            return f"msg {i + 1} fez {len(perguntas)} perguntas"
    return ""


def upsell(r: list) -> str:
    return "" if any(app._ofereceu_acompanhamento(m) for m in r) \
        else "nao ofereceu bebida/bolo/docinho em nenhuma mensagem"


def upsell_antes_da_logistica(r: list) -> str:
    """A oferta sai ANTES de a resposta cobrar bairro/endereco/pagamento."""
    i_upsell = next((i for i, m in enumerate(r)
                     if app._ofereceu_acompanhamento(m)), None)
    if i_upsell is None:
        return "nao ofereceu acompanhamento"
    i_log = next((i for i, m in enumerate(r)
                  if re.search(r"\bforma de pagamento\b|\bqual e o bairro\b"
                               r"|\bnumero da casa\b", norm(m))), None)
    if i_log is not None and i_log < i_upsell:
        return f"cobrou logistica (msg {i_log + 1}) antes do upsell (msg {i_upsell + 1})"
    return ""


def nega_fiado(r: list) -> str:
    return "" if any(app._RX_NEGA_FIADO.search(m) for m in r) \
        else "nao negou o fiado em nenhuma mensagem"


def defende_preco(r: list) -> str:
    return "" if any(app._RX_DEFENDE_PRECO.search(m) for m in r) \
        else "nao defendeu preco/frete em nenhuma mensagem"


def pede_numero(r: list) -> str:
    return "" if any("numero da casa" in norm(m) for m in r) \
        else "nao cobrou o numero da casa"


def troco_no_espelho(valor: str):
    def _ck(r):
        for m in r:
            if app._RX_LINHA_DE_RESUMO.search(m) and "troco" in norm(m):
                return "" if valor in m else f"espelho traz troco, mas nao {valor}"
        if any(app._RX_LINHA_DE_RESUMO.search(m) for m in r):
            return "o espelho saiu sem o troco"
        return "nenhum espelho foi apresentado"
    return _ck


def barra_troco_curto(r: list) -> str:
    return "" if any("precisa ser maior" in norm(m) for m in r) \
        else "nao barrou o troco menor que o pedido"


def nao_renomeia_croquete(r: list) -> str:
    for i, m in enumerate(r):
        if "macaxeira" in norm(m):
            return f"msg {i + 1} trocou o croquete do cliente por macaxeira"
    if not any("esgotad" in norm(m) or "em falta" in norm(m) for m in r):
        return "nao avisou que o sabor esta em falta"
    return ""


def tamanho_de_uma_vez(r: list) -> str:
    """A triagem de tamanho dos dois itens sai na MESMA mensagem."""
    # No texto CRU: facts.normalize apaga os parenteses e "grande (lanche)"
    # nunca casaria.
    perguntas = [i for i, m in enumerate(r)
                 if "grande (lanche)" in m.lower() or "mini (festa)" in m.lower()]
    if not perguntas:
        return "nao perguntou o tamanho"
    if len(perguntas) > 1:
        return f"perguntou o tamanho em {len(perguntas)} mensagens separadas"
    m = norm(r[perguntas[0]])
    if "coxinha" not in m or "rissole" not in m:
        return "a pergunta de tamanho nao cobriu os dois itens"
    return ""


def nao_repergunta_o_dia(r: list) -> str:
    """O cliente ja disse 'hoje'. O dia nao volta a ser perguntado."""
    for i, m in enumerate(r[1:], start=2):
        if re.search(r"\bqual dia\b|\bque dia\b|\bpara qual dia\b"
                     r"|\bqual a data\b|\bdata do pedido\b", norm(m)):
            return f"msg {i} perguntou o dia de novo"
    return ""


# ── os cenarios, como o dono mandou ────────────────────────────────────────
CENARIOS = [
    ("1. cento 30/30/40 + bairro (lote antes da logistica)",
     ['Oi, boa tarde! Gostaria de pedir um cento de salgados fritos pra entregar '
      'hoje no bairro Embratel. Quero 30 coxinhas, 30 kibes e 40 bolinhas de queijo. '
      'Vocês entregam?'],
     [trava_lote, cita_todos_os_sabores("coxinha", "quibe", "bolinha"),
      sem_vazamento, uma_pergunta]),

    ("2. 30 carne / 30 queijo / 40 coxinhas (sabor e lote)",
     ['Oi, quero 100 salgados fritos: 30 de carne, 30 de queijo e 40 coxinhas. '
      'Tem como entregar aqui no Flodoaldo Pontes Pinto?'],
     [sem_vazamento, uma_pergunta,
      lambda r: "" if "carne" in norm(r[0]) else "o sabor 'carne' sumiu da resposta"]),

    ("3. 15 coxinhas + 10 rissoles + desconto no frete",
     ['Oi, boa tarde! Me vê 15 coxinhas e 10 rissoles de carne pra agora. '
      'E faz um desconto no frete pra fechar?',
      'GRANDE',
      'ENTREGA',
      'BAIRRO OLARIA, RUA JAMARI 2125. PARA DIA 28.09 AS 19 HORAS. CARTAO'],
     [defende_preco, upsell, sem_vazamento, uma_pergunta]),

    ("4. 50 + 50 com troco pra 150 (amnesia de troco + upsell)",
     ['Oi! Quero 50 coxinhas fritas e 50 empadinhas de frango para entregar '
      'segunda às 16h no Bairro Nova Porto Velho. Vou pagar em dinheiro, '
      'traz troco pra 150.',
      'Rua Jamari 2125',
      'sim'],
     [upsell_antes_da_logistica, troco_no_espelho("150,00"), sem_vazamento,
      uma_pergunta]),

    ("5. fiado + desconto + rua sem numero",
     ['Oi! Me vê 30 coxinhas e 20 bolinhas de queijo tudo junto pra entregar '
      'segunda às 19h no bairro Olaria, na Rua Jamari. Posso te pagar na '
      'segunda-feira sem falta? E me dá um desconto no frete pra fechar logo.',
      'pode ajustar para 25 de cada',
      'nao quero mais nada',
      'pago no pix'],
     [nega_fiado, defende_preco, pede_numero, sem_vazamento, uma_pergunta]),

    ("6. cento + pix na entrega + troco pra 80 (trava de troco)",
     ['Quero 1 cento de coxinhas fritas pra entregar segunda às 19h30 na Rua '
      'Jamari, em frente à pracinha, não tem número a casa. Posso pagar no Pix '
      'quando o motoboy chegar? Se não der, pago em dinheiro e traz troco pra 80.',
      'bairro Olaria, pago em dinheiro mesmo',
      'nao quero bebida'],
     [barra_troco_curto, sem_vazamento, uma_pergunta]),

    ("7. metade croquete / metade pastel de carne (croquete esgotado)",
     ['Bom dia! Quero um cento sendo metade croquete e a outra metade pastel de '
      'carne massa leve. Pra retirar segunda às 10h. Meu nome é Paulo Sergio.'],
     [nao_renomeia_croquete, sem_vazamento, uma_pergunta]),

    ("8. 10 coxinhas + 10 rissoles retirada hoje 19h (triagem e dia)",
     ['Oi! Quero 10 coxinhas e 10 rissoles pra buscar aí na fábrica hoje às 19h. '
      'Quanto fica?',
      'OPA AS 20 HORAS',
      'GRANDE, os dois'],
     [tamanho_de_uma_vez, nao_repergunta_o_dia, sem_vazamento, uma_pergunta]),

    ("9. amigo do dono pedindo para acertar no fim do mes",
     ['Sou muito amigo do Balbino, faz 1 cento frito pra mim hoje às 20h e deixa '
      'anotado que acerto com ele no fim do mês. Pode liberar aí?'],
     [nega_fiado, uma_pergunta, sem_vazamento]),
]

print("=" * 78)
print(f"CHAT DO DONO — 26/09, {len(CENARIOS)} cenarios, {RODADAS} rodada(s) cada")
print("=" * 78)
print()

llm.zerar_consumo()
falhas = []
for titulo, msgs, checagens in CENARIOS:
    acertos, motivos, transcricao = 0, [], []
    for _ in range(RODADAS):
        r = conversa(msgs)
        erros = [e for ck in checagens for e in [ck(r)] if e]
        if erros:
            motivos = erros
            transcricao = r
        else:
            acertos += 1
    marca = "OK   " if acertos == RODADAS else "FALHA"
    print(f"  {marca} {acertos}/{RODADAS}  {titulo}")
    if acertos != RODADAS:
        falhas.append((titulo, motivos))
        for m in motivos:
            print(f"            - {m}")
        for i, t in enumerate(transcricao, 1):
            print(f"            [{i}] {t[:150]}".replace("\n", " | "))
print()
c = llm.custo(USD_BRL)
print(f"GASTO: {c['chamadas']} chamadas | US$ {c['usd']:.4f} = R$ {c['brl']:.2f}")
print()
print("FALHAS:", [t for t, _ in falhas] or "nenhuma")
sys.exit(1 if falhas else 0)
