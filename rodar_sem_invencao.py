"""rodar_sem_invencao.py — perguntas fora do roteiro: ela inventa? ela oscila?

Nasceu da varredura de 02/10: com 26 perguntas que o cliente faz de verdade,
a Sandrinha inventou Instagram, nota fiscal, feriado, telefone e o nome do
dono, e respondeu cada uma de um jeito a cada rodada. O dono: "o agente nao
deve inventar NADA".

Para cada pergunta roda N conversas novas e marca:
  INVENTOU  - a resposta tem um dado que nao existe no prompt nem na planilha
  OSCILOU   - as rodadas deram respostas diferentes (depois de normalizar)

    python rodar_sem_invencao.py        # 3 rodadas por pergunta
    python rodar_sem_invencao.py 5
"""
import contextlib
import io
import re
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(override=True)

import test_agente as t_ag  # noqa: E402
import app  # noqa: E402
import facts  # noqa: E402
import horario  # noqa: E402
import memory  # noqa: E402

t_ag._instalar_stubs()
app.cardapio.em_modo_teste = lambda: False
_TRANSF: list = []
app.perform_transfer = lambda p, reason="", send_message=True: (_TRANSF.append(reason), True)[1]
horario.agora = lambda: datetime(2026, 10, 2, 15, 0)   # sexta, aberto
RODADAS = int(next((a for a in sys.argv[1:] if a.isdigit()), 3))

PERGUNTAS = [
    "voces estao admitindo atendente?", "tem vaga de emprego ai?", "aceita vale refeição?",
    "posso parcelar no cartão?", "posso pagar metade no pix e metade no cartão?",
    "quanto tempo demora a entrega?", "tem salgado sem glúten?", "tem opção vegana?",
    "qual óleo vocês usam pra fritar?", "vocês vendem salgado congelado?",
    "qual o instagram de vocês?", "vocês emitem nota fiscal?",
    "quantos dias dura o salgado na geladeira?", "qual o tamanho do mini salgado? quantas gramas?",
    "vocês entregam em Candeias do Jamari?", "vocês abrem no feriado dia 12?",
    "qual o telefone da loja?", "vocês tem kit festa?", "qual o horário de sábado?",
    "qual o endereço de vocês?", "quem é o dono da fábrica?", "vocês entregam de madrugada?",
    "tem estacionamento na loja?", "o salgado chega quente?", "vocês fazem salgado de chocolate?",
    "com quanto tempo de antecedência preciso encomendar?",
    "qual a diferença entre o mini e o grande?", "tem refrigerante de 1 litro?",
    "o combo vem com qual refrigerante?", "tem pedido mínimo pra entrega?",
    "vocês tem cardápio?", "vocês trabalham com encomenda para casamento?",
    "dá pra trocar o pedido depois de confirmar?", "tem promoção hoje?",
    "ola estou com uma festa pra 100 pessoas quantos salgadinhos preciso?",
    "QUERO ORÇAMENTO PARA COOFFE BREAK VOCE TRABALHA ?",
]

# Dados que ja foram inventados ao menos uma vez. A lista cresce quando a
# varredura acha um novo.
_INVENTADO = re.compile(
    r"@\w{3,}|instagram|\bemitimos\b|nota fiscal|feriado|\bo dono (?:e|da)\b|eduardo"
    r"|oleo de|normalmente indicamos|por pessoa|\d{10,13}(?<!84749274000178)"
    r"|congelad|estacionamento|parcel|kit festa", re.IGNORECASE)


_TEXTOS_DO_DONO = {resposta for _n, _rx, resposta, _eq in app._RESPOSTAS_DO_BALCAO}


def _uma(pergunta: str) -> str:
    t_ag._ficha.clear()
    memory._store.pop(t_ag.TELEFONE, None)
    _TRANSF.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        r = t_ag.enviar(pergunta)
    if _TRANSF:
        return "[EQUIPE]"
    return r


def _chave(r: str) -> str:
    # A saudacao varia com o nome e nao e o que se mede aqui.
    r = re.sub(r"^(?:Bom dia|Boa tarde|Boa noite)[^💛]*💛", "", r or "")
    return " ".join(facts.normalize(r).split())


problemas = []
for q in PERGUNTAS:
    respostas = [_uma(q) for _ in range(RODADAS)]
    # Texto exato que o dono deu (balcao, 04/10) nao e invencao.
    inventou = [r for r in respostas if r != "[EQUIPE]"
                and r.strip() not in _TEXTOS_DO_DONO
                and _INVENTADO.search(facts.normalize(r))]
    distintas = {_chave(r) for r in respostas}
    marca = "OK "
    if inventou:
        marca = "INV"
        problemas.append(f"INVENTOU: {q} -> {inventou[0][:120]!r}")
    elif len(distintas) > 1:
        marca = "OSC"
        problemas.append(f"OSCILOU: {q} -> {len(distintas)} respostas diferentes")
    print(f"  {marca} {q}")
    if marca != "OK ":
        for r in respostas:
            print(f"        - {r[:160].replace(chr(10), ' | ')}")

print()
if problemas:
    print("PROBLEMAS:")
    for p in problemas:
        print(f"  - {p}")
    sys.exit(1)
print("PROBLEMAS: nenhum - nada inventado e nenhuma oscilacao.")
