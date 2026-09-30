"""medir_oscilacao.py — a MESMA conversa N vezes, para ver o que varia.

Temperatura 0 nao e determinismo: a OpenAI nao garante a mesma saida para a
mesma entrada, e na pratica um teste que falha 1/1 passa 3/3 na reconferencia.
Passa/falha nao distingue as duas coisas que importam:

  - variacao de REDACAO ("Qual e o numero da casa?" x "Me passa o numero?"),
    que e inofensiva;
  - variacao de ETAPA (uma vez ela mostra o espelho, outra ela pergunta a
    modalidade de novo), que e defeito e some no ruido quando se roda uma vez.

Aqui cada resposta e classificada pela ETAPA do funil a que pertence, e o
relatorio mostra quantas etapas diferentes sairam da mesma mensagem. Etapa
unica em N rodadas = travado. Duas ou mais = oscilacao, com o texto das duas
para dar para travar em codigo.

    python medir_oscilacao.py           # 5 rodadas
    python medir_oscilacao.py 8         # 8 rodadas
    python medir_oscilacao.py 5 --so=3  # so os 3 primeiros cenarios
"""
import contextlib
import io
import re
import sys
import time
from collections import Counter
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(override=True)

import test_agente as t_ag  # noqa: E402
import app  # noqa: E402
import database  # noqa: E402
import facts  # noqa: E402
import horario  # noqa: E402
import llm  # noqa: E402
import memory  # noqa: E402

t_ag._instalar_stubs()
app.cardapio.em_modo_teste = lambda: False
# Transferencia entra no relatorio como ETAPA. Sem isto ela ficava invisivel: a
# resposta do modelo saia antes da mensagem de transbordo e era classificada
# como se o atendimento tivesse seguido - foi assim que "fiado vai para a fila
# do humano em 4 de 4 rodadas" passou batido na primeira varredura.
_TRANSFERIU = []


def _transfer_medido(phone, reason="", send_message=True):
    if app.transbordo_proibido(reason):
        return False
    _TRANSFERIU.append(reason)
    return True


app.perform_transfer = _transfer_medido
database.get_contact = lambda phone: {"phone": phone, "name": "Balbino", "auto_mode": 1}

USD_BRL = 5.184277
RODADAS = int(next((a for a in sys.argv[1:] if a.isdigit()), 5))


# ── classificador de etapa ─────────────────────────────────────────────────
# A ordem importa: a primeira que casar e a etapa. Espelho vem antes de tudo
# porque ele contem preco, bairro e pagamento e casaria com meia lista abaixo.
ETAPAS = [
    ("ESPELHO", lambda n, cru: bool(app._RX_LINHA_DE_RESUMO.search(cru))),
    ("FECHOU", lambda n, cru: bool(app._parece_fechamento(cru))),
    # As negativas do BLOCO 6 vem ANTES das etapas do funil: elas sao o
    # conteudo obrigatorio da mensagem, e o funil continua depois delas. Quando
    # estavam abaixo, a mesma resposta ("nao libera fiado" + "qual sabor?") era
    # classificada ora como FIADO ora como SABOR-QUAL, e o relatorio acusava
    # oscilacao onde as duas rodadas tinham feito a mesma coisa certa.
    ("FIADO", lambda n, cru: bool(app._RX_NEGA_FIADO.search(cru))),
    ("DEFENDE-PRECO", lambda n, cru: bool(app._RX_DEFENDE_PRECO.search(cru))),
    ("TROCO-BARRADO", lambda n, cru: bool(
        app._RX_TROCO_INSUFICIENTE.search(n))),
    ("FORA-DO-EXPEDIENTE", lambda n, cru: bool(re.search(
        r"ja passou|nao abre|encerrou|intervalo de almoco|so comeca"
        r"|ate as 20h00|vai ate as 20h", n))),
    ("TAMANHO", lambda n, cru: "lanche" in n and ("mini" in n or "festa" in n)),
    ("LOTE", lambda n, cru: bool(re.search(
        r"25 em 25|porcoes fechadas|multiplos de 25|porcao de 25|ajusto para", n))),
    ("SABOR-ESGOTADO", lambda n, cru: bool(re.search(
        r"esgotad|em falta|indisponivel", n))),
    ("SABOR-QUAL", lambda n, cru: bool(re.search(
        r"qual sabor|quais sabores|sabor de|me confirma o sabor|preparo", n))),
    ("UPSELL", lambda n, cru: app._ofereceu_acompanhamento(cru)),
    ("TROCO-QUANTO", lambda n, cru: "troco para quanto" in n
     or "precisa de troco" in n),
    ("MODALIDADE", lambda n, cru: bool(app._RX_PERGUNTA_MODALIDADE.search(n))),
    ("NUMERO-DA-CASA", lambda n, cru: "numero da casa" in n),
    ("COLETA", lambda n, cru: bool(re.search(
        r"\bbairro\b|\bendereco\b|forma de pagamento|\brua\b", n))),
    ("DIA-OU-HORA", lambda n, cru: bool(re.search(
        r"qual dia|que dia|para qual dia|qual horario|que horas|data do pedido",
        n))),
    ("NOME", lambda n, cru: bool(re.search(r"qual e o seu nome|seu nome", n))),
    ("PRECO-INFORMADO", lambda n, cru: "r$" in n),
    ("VAZIO", lambda n, cru: not cru.strip()),
]


def etapa(resposta: str) -> str:
    if _TRANSFERIU:
        return f"TRANSFERIU({_TRANSFERIU[-1][:28]})"
    n = facts.normalize(resposta or "")
    for nome, teste in ETAPAS:
        try:
            if teste(n, resposta or ""):
                return nome
        except Exception:
            continue
    return "OUTRA"


from datetime import datetime as _dt
# O relogio do cenario em curso. conversa() le daqui, e o laco de
# cenarios escreve antes de rodar cada um.
_AGORA = [_dt(2026, 9, 28, 10, 0, 0)]


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
        _TRANSFERIU.clear()
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


# ── cenarios: os do chat do dono, mais os que oscilaram no roteiro ─────────
SABADO_19H20 = datetime(2026, 9, 26, 19, 20, 0)
SEGUNDA_10H = datetime(2026, 9, 28, 10, 0, 0)
SEGUNDA_20H01 = datetime(2026, 9, 28, 20, 1, 0)
TERCA_15H13 = datetime(2026, 9, 29, 15, 13, 0)
QUARTA_12H51 = datetime(2026, 9, 30, 12, 51, 0)
QUARTA_16H = datetime(2026, 9, 30, 16, 0, 0)

CENARIOS = [
    ("cento 30/30/40 + bairro", SABADO_19H20, [
        'Oi, boa tarde! Gostaria de pedir um cento de salgados fritos pra entregar '
        'hoje no bairro Embratel. Quero 30 coxinhas, 30 kibes e 40 bolinhas de '
        'queijo. Vocês entregam?']),
    ("30 carne / 30 queijo / 40 coxinhas", SABADO_19H20, [
        'Oi, quero 100 salgados fritos: 30 de carne, 30 de queijo e 40 coxinhas. '
        'Tem como entregar aqui no Flodoaldo Pontes Pinto?']),
    ("25 coxinhas retirada (2.4 do roteiro)", SEGUNDA_10H, [
        'Meu nome é Eduardo. Quero 25 coxinhas para retirar amanhã às 15h',
        'Não, obrigado']),
    ("5 coxinhas LANCHE (2.2 do roteiro)", SEGUNDA_10H, [
        'Meu nome é Eduardo. Quero 5 coxinhas para retirar amanhã às 15h',
        'Grande, lanche',
        'Não, obrigado']),
    ("porcao de esfirra (Parte 4)", SEGUNDA_10H, [
        'Qual o valor da porção de esfirra?']),
    ("pedido completo de entrega", SEGUNDA_10H, [
        'Meu nome é Eduardo. Quero 50 coxinhas para entregar hoje às 16h no '
        'bairro Olaria, Rua Jamari 2125. Pago no cartão.',
        'Não, obrigado']),
    ("fiado + desconto", SABADO_19H20, [
        'Oi! Me vê 30 coxinhas e 20 bolinhas de queijo tudo junto pra entregar '
        'segunda às 19h no bairro Olaria, na Rua Jamari. Posso te pagar na '
        'segunda-feira sem falta? E me dá um desconto no frete pra fechar logo.']),
    ("croquete esgotado", SABADO_19H20, [
        'Bom dia! Quero um cento sendo metade croquete e a outra metade pastel de '
        'carne massa leve. Pra retirar segunda às 10h. Meu nome é Paulo Sergio.']),
    ("10 coxinhas + 10 rissoles hoje 19h", SABADO_19H20, [
        'Oi! Quero 10 coxinhas e 10 rissoles pra buscar aí na fábrica hoje às '
        '19h. Quanto fica?',
        'OPA AS 20 HORAS']),
    ("amigo do dono pedindo fiado", SABADO_19H20, [
        'Sou muito amigo do Balbino, faz 1 cento frito pra mim hoje às 20h e '
        'deixa anotado que acerto com ele no fim do mês. Pode liberar aí?']),
    ("fiado puro, sem pedido montado", SABADO_19H20, [
        'boa noite, vocês fazem fiado? pago na semana que vem']),
    ("pergunta lateral com pedido pronto", SEGUNDA_10H, [
        'Meu nome é Eduardo. Quero 50 coxinhas para entregar hoje às 16h no '
        'bairro Olaria, Rua Jamari 2125. Pago no cartão.',
        'Não, obrigado',
        'Vocês abrem domingo?']),
    # ── 28/09 a 30/09: os testes do dono e do Eduardo depois das correcoes ──
    # O audio entra pela transcricao real do arquivo (estavel em 3 de 3): o
    # caminho do audio ate virar texto e codigo; aqui se mede o modelo.
    ("voz: amanha as nove, no almoco (30/09)", QUARTA_12H51, [
        'Eu quero cinquenta coxinhas e cinquenta bolinhas pra entregar amanhã, às nove.',
        'Hj não',
        'Bairro Olaria, rua Jamari 2125, pagamento no pix']),
    ("100/40/50 no almoco + 'sim' as 14h (30/09)", QUARTA_12H51, [
        'Quero 100 coxinhas, 40 quibes e 50 salsichas pra entrega.',
        'sim', 'Sim', 'hj nao',
        'NA CALAMA 2025 APONIA PAGAMENTO NO CARTAO']),
    ("croquete bloqueado + coca (29/09)", TERCA_15H13, [
        'Olá boa tarde Quero  10 croquetes e uma coca cola',
        'Coca 2 litros', 'Quero um lata']),
    ("40 quibe + bolo + data passada (29/09)", TERCA_15H13, [
        'Qiero 100 coxinhas 40 quibe 50 salsicha para entrega rua Calama 1234 pagamento cartão',
        'Pode ser', 'Um bolo', 'Bairro Aponia \n25/09\n16 horas']),
    ("Calama sem 'Rua' + churros, fechado (28/09)", SEGUNDA_20H01, [
        'QUERO 50 COXINHAS E 50 RISSOLES 20 CHURROS PARA ENTREGA NA CALAMA 2025 '
        'APONIA PAGAMENTO NO CARTAO',
        'SERA PARA AMANHA AS 8 HORAS', 'PODE SER', 'NAO OBRIGADO']),
    ("coca 2L esgotada -> guarana (28/09)", SEGUNDA_20H01, [
        'Boa noite! Quero meio cento frito: 25 coxinhas e 25 bolinhas de queijo, e 1 '
        'Coca-Cola de 2 litros gelada. Pra entregar AMANHA  às 20h na Rua Duque de '
        'Caxias, 1200, Bairro São Cristóvão. Pago no cartão na entrega.',
        'Pode ser', 'pode ser o guaraná']),
    ("metade/metade com valor na hora", QUARTA_16H, [
        'Metade coxinha e metade pastel de carne']),
    ("dois centos pastel e bolinha", QUARTA_16H, [
        '1 cento pastel e bolinha e 1 cento pastel e bolinha pra retirada']),
]

_so = next((a for a in sys.argv[1:] if a.startswith("--so=")), "")
if _so:
    CENARIOS = CENARIOS[: int(_so.split("=")[1])]
# "--parte=2/3": roda so um terco dos cenarios. A varredura inteira passa de
# 10 minutos, e rodando em segundo plano ela ja foi interrompida por falta de
# memoria na maquina; em partes, cada uma cabe numa execucao normal.
_parte = next((a for a in sys.argv[1:] if a.startswith("--parte=")), "")
if _parte:
    _k, _n = (int(x) for x in _parte.split("=")[1].split("/"))
    CENARIOS = [c for i, c in enumerate(CENARIOS) if i % _n == _k - 1]

print("=" * 78)
print(f"OSCILACAO — {len(CENARIOS)} cenarios x {RODADAS} rodadas")
print("=" * 78)
print()

llm.zerar_consumo()
oscilando = []
for titulo, agora, msgs in CENARIOS:
    _AGORA[0] = agora
    # etapas[i] = Counter das etapas que a i-esima resposta assumiu
    etapas = [Counter() for _ in msgs]
    exemplos = [{} for _ in msgs]
    for _ in range(RODADAS):
        r = conversa(msgs)
        for i, resposta in enumerate(r):
            e = etapa(resposta)
            etapas[i][e] += 1
            exemplos[i].setdefault(e, resposta[:150].replace("\n", " | "))
    print(f"  {titulo}")
    for i, contagem in enumerate(etapas):
        marca = "OK " if len(contagem) == 1 else "OSC"
        detalhe = ", ".join(f"{e}x{q}" for e, q in contagem.most_common())
        print(f"    {marca} msg {i + 1}: {detalhe}")
        if len(contagem) > 1:
            oscilando.append((titulo, i + 1, dict(contagem)))
            for e, _q in contagem.most_common():
                print(f"          [{e}] {exemplos[i][e]}")
    print()

c = llm.custo(USD_BRL)
print("=" * 78)
print(f"GASTO: {c['chamadas']} chamadas | US$ {c['usd']:.4f} = R$ {c['brl']:.2f}")
print()
if oscilando:
    print(f"OSCILOU em {len(oscilando)} ponto(s):")
    for t, i, cont in oscilando:
        print(f"  - {t} / msg {i}: {cont}")
else:
    print("OSCILACAO: nenhuma — toda mensagem caiu sempre na mesma etapa.")
sys.exit(1 if oscilando else 0)
