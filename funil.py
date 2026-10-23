"""
funil.py — Funil duplo do Balbino: respostas do balcao, desistencia, crise
e os textos de quando a conversa sai do trilho e vai para a equipe.

Saiu de app.py em 23/10; app.py reexporta os nomes.
"""

import re as _re_mod

import facts


# ── Funil duplo (decisao do Balbino, 12/10) ─────────────────────────────────
# Trilho 1: o codigo valida produto e multiplo de 25 e o robo conduz o pedido.
# Trilho 2: qualquer desvio (quantidade quebrada, item fora do cardapio, duvida
# sem resposta, pedido de desconto, risco/reclamacao) vai para a equipe NA HORA,
# com esta frase, e o robo para de responder (auto_mode = HUMAN_ATTENDANCE).
TEXTO_DESARME = "Vou chamar nossa equipe para verificar agora! Um instante."
HUMAN_ATTENDANCE = 0          # valor de contacts.auto_mode com a equipe atendendo


# Respostas do balcao padronizadas pelo dono (04/10), com o texto exato dele.
# (nome, regex sobre o texto normalizado, resposta, passa para a equipe).
# A ordem importa: "motoboy ja esta chegando?" e a 2, nao a 1.
_RESPOSTAS_DO_BALCAO = (
    ("frio", _re_mod.compile(
        r"\b(?:chegou|chegaram|veio|vieram|esta|estao|ta|tao)\b.{0,25}"
        r"\b(?:frio|fria|frios|frias|revirad\w*|amassad\w*|desmanchad\w*)\b"),
     "Sentimos muito por isso! Já passei a situação para a equipe responsável "
     "verificar o transporte e te dar uma solução agora.", True),
    ("faltando", _re_mod.compile(
        r"\b(?:veio|vieram|chegou|chegaram|esta|ta)\b.{0,25}\bfaltando\b"
        # Numero so com produto: "faltou cobrar a taxa de 2,00" nao e item
        # faltando (teste do dono, 07/10 14h16).
        r"|\bfaltou\b(?!.{0,30}\b(?:cobrar|taxa|troco|frete)\b).{0,30}"
        r"\b(?:salgad\w*|coxinh\w*|caixa|pedido|unidades?|\d+\s+(?:salgad|coxinh|past|bolinh|quib|rissol|churr|un))\w*\b"
        r"|\bfaltando\b.{0,30}\b(?:na|no)\s+(?:minha\s+|meu\s+)?(?:caixa|pedido)\b"),
     "Pedimos sinceras desculpas pelo ocorrido! Nossa equipe já está assumindo seu "
     "atendimento aqui para resolver isso imediatamente.", True),
    ("cancelamento", _re_mod.compile(
        r"\bcancel\w*\s+(?:o\s+|meu\s+|a\s+|minha\s+|esse\s+|essa\s+)?(?:pedido|compra|encomenda)\b"
        r"|\b(?:pedido|compra|encomenda)\b.{0,20}\bcancel\w*|^\s*(?:quero\s+)?cancel\w*\s*$"),
     "Já estou acionando a equipe para atender sobre o cancelamento.", True),
    # "Tenho encomenda para as 20h, mas vou demorar uns 10 minutinhos": aviso
    # de atraso do cliente. Recebia "Qual o seu pedido?" e depois o texto de
    # crise da gerencia (teste do dono, 17/10). Vem antes da urgencia.
    ("atraso", _re_mod.compile(
        r"\b(?:vou|vamos|devo|vo)\s+(?:me\s+)?(?:demor\w*|dmeor\w*|demro\w*|atras\w*"
        r"|chegar\s+(?:atrasad\w*|mais\s+tarde|um\s+pouco\s+depois|depois))"
        r"|\b(?:me|mim)\s+(?:esper\w*|aguard\w*)\b|\b(?:estou|to|tou)\s+atrasad\w*"),
     "Sem problemas! Vamos te aguardar. 💛", False),
    ("comprovante", _re_mod.compile(
        r"\bcomprovante\b.{0,60}\b(?:confirm\w*|receb\w*|chegou|viu|olhou)"
        r"|\b(?:confirm\w*|receb\w*)\b.{0,30}\bcomprovante\b"),
     "Comprovante recebido!", False),
    # "chego em 5 min" e o CLIENTE chegando, nao urgencia de entrega (teste
    # do dono, 06/10).
    ("urgencia", _re_mod.compile(
        r"^(?!.*\b(?:chego|chegando|to\s+indo|estou\s+indo|passo\s+ai)\b)"
        r".*(?:\bdaqui\s+(?:a\s+)?\d{1,2}\s*min|\bem\s+\d{1,2}\s*min\w*|\burgen(?:te|cia)\b)"),
     "Nosso tempo médio de preparo e entrega é de 30 a 40 minutos, mas vou verificar "
     "com a equipe para agilizar.", True),
    # Congelado: texto do dono (04/10). Antes ia para a equipe.
    # So quem quer COMPRAR congelado: "veio tudo congelado e murcho" e
    # reclamacao (teste do dono, 06/10 - recebia o texto de venda).
    ("congelado", _re_mod.compile(
        r"\b(?:vend\w*|tem|tens|fazem|faz|trabalh\w*|quero|queria|encomend\w*|compr\w*)\b"
        r".{0,40}\bcongelad\w*|\bcongelad\w*\b.{0,25}\b(?:pra|para)\s+fritar"),
     # Texto de venda do dono (08/10): a frase seca nao vendia.
     "Sim, trabalhamos sim! ❄️\n"
     "Mini Salgados Congelados – R$ 40 o cento\n"
     "Salgadinhos prontos para fritar quando você quiser!\n"
     "Nossos sabores:\n"
     "• Coxinha\n"
     "• Bolinha de Queijo\n"
     "• Risole de Carne\n"
     "• Presunto e Queijo\n"
     "• Kibe\n"
     "💡 Dica: Fritar ainda congelados em óleo bem quente (180°C) até dourar.\n"
     "Qual sabor ou combinação você gostaria de pedir hoje?", False),
    ("motoboy", _re_mod.compile(
        r"\b(?:motoboy|motoqueiro|entregador|motoca)\b.{0,30}"
        r"\b(?:chegando|chega|vindo|vem|perto|demora|onde)\b"),
     "JÁ SAIU SEU PEDIDO!", False),
    ("saiu", _re_mod.compile(
        r"\bja\s+saiu\b|\bsaiu\s+(?:pra|para)\s+(?:a\s+)?entrega\b|\bsaiu\s+o\s+(?:meu\s+)?pedido\b"),
     "Vou verificar !", True),
    ("maquininha", _re_mod.compile(
        r"^(?=.*\b(?:posso|pode|da\s+pra|da\s+para|aceita\w*|leva|manda|tem)\b)"
        r".*\bmaquin\w*"),
     "SIM MANDO PELO MOTOBOY", False),
)

_TEXTO_CANCELAMENTO = "Já estou acionando a equipe para atender sobre o cancelamento."
# "quero mais nao", "nao quero mais", "desisto", "deixa pra la" (17/10).
_RX_DESISTENCIA = _re_mod.compile(
    r"\bquero\s+mais\s+nao\b|\bnao\s+quero\s+mais\s*(?:nao)?\s*$|\bnao\s+quero\s+mais\s+(?:o\s+)?pedido\b"
    r"|\bdesist\w*|\bdeixa\s+(?:pra|para)\s+la\b|\besquece\s+(?:o\s+pedido|tudo)\b")

# Convite ao pedido depois de falar de um produto ("A porcao de Esfirra de
# Carne sai R$ 30,00. Qual o seu pedido?", "Bora pedir quantas?").
_RX_CONVITE_AO_PEDIDO = _re_mod.compile(
    r"\bbora\s+pedir\b|\bqual\s+o\s+seu\s+pedido\b|\bquer\s+que\s+eu\s+(?:ja\s+)?anote\b"
    r"|\bquantas?\s+(?:voce\s+)?(?:quer|vai\s+querer)\b")


def _produto_do_convite(texto: str) -> str:
    """O unico produto de que a mensagem fala ('Esfirra de Carne', 'esfirra'), ou ''."""
    norm = facts.normalize(texto or "")
    try:
        nomes = {p["item"] for p in facts.get()["produtos"] if p.get("disponivel", True)}
    except Exception:
        return ""
    achados = {n for n in nomes
               if _re_mod.search(rf"\b{_re_mod.escape(facts.normalize(n))}\b", norm)}
    achados = {a for a in achados
               if not any(a != b and facts.normalize(a) in facts.normalize(b) for b in achados)}
    # As familias citadas ("coxinha", "bolinha"): duas ou mais e nao ha um
    # produto so de que se fala.
    # Palavra de formato ("porcao", "cento", "bandeja") nao e familia.
    cabecas = {facts.normalize(n).split()[0] for n in nomes
               if facts.normalize(n) and len(facts.normalize(n).split()[0]) >= 5} - {
        "porcao", "cento", "bandeja", "combo", "salgadinhos"}
    citadas = {c for c in cabecas if _re_mod.search(rf"\b{c}s?\b", norm)}
    if len(citadas) > 1 or len(achados) > 1:
        return ""
    if achados:
        return achados.pop()
    # So o nome da familia ("Nossas esfirras sao..."): quem decide o sabor ou
    # o tamanho e o fluxo normal, com o pedido em maos.
    return citadas.pop() if citadas else ""


_RX_SO_QUANTIDADE = _re_mod.compile(
    r"\s*(?:(?:quero|manda|pode\s+ser|vou\s+querer|sim)\s*,?\s*)?"
    r"(?P<qtd>\d{1,4}\s*(?:un|unidades?|pecas?)?|(?:um\s+|meio\s+|dois\s+|duas\s+|\d\s+)?centos?"
    r"(?:\s+e\s+meio)?|uma?\s+porcao|\d\s+porcoes)\s*[.!]*\s*")

# 4 e 9 so valem para pedido ja feito: no meio do funil, mudar o endereco e
# corrigir um dado, e "mais um cento" e pedido.
_RX_MUDA_ENDERECO = _re_mod.compile(
    r"\b(?:mudar|muda|trocar|troca|alterar|altera|corrigir)\b.{0,25}\bendereco\b")
_RX_ACRESCENTA = _re_mod.compile(
    r"\b(?:acrescentar|acrescenta|adicionar|adiciona|incluir|inclui|somar|soma"
    r"|colocar\s+mais|mais\s+um)\b")
_RX_PEDIDO_JA_FEITO = _re_mod.compile(
    r"\b(?:forno|pronto|producao|acabei\s+de\s+fazer|ja\s+fiz|que\s+fiz|ja\s+(?:esta|ta)"
    r"|pedido\s+(?:anterior|feito))\b")

TEXTO_MUDA_ENDERECO = "Envie o novo endereço completo aqui, por favor!"
_RX_MUDA_MODALIDADE = _re_mod.compile(
    r"\b(?:quero|queria|prefiro|vou\s+querer|resolvi|mud\w*|troc\w*|melhor|pode\s+ser)\b"
    r".{0,20}?\b(entrega|entregar|retirada|retirar|buscar)\b")
TEXTO_ACRESCENTA = "PODE SIM QUAL SABOR VOCE QUER"


# BLOCO 4 do dono: termo critico -> texto padrao, transfere e silencia. Vem
# antes de tudo: "veio congelado... quero o estorno do Pix" recebia a resposta
# de venda de congelado (teste do dono, 06/10).
_RX_CRISE = _re_mod.compile(
    r"\bestorn\w*|\bdevolv\w*\s+(?:o\s+|meu\s+)?(?:pix|dinheiro)\b|\bdinheiro\s+de\s+volta\b"
    r"|\bprocon\b|\bvigilancia\b|\bpass(?:a|ou|ei|aram|ando)\w*\s+mal\b|\barame\b|\binseto\b"
    r"|\bbarata\b")
TEXTO_CRISE = ("Compreendo a situação e a sua preocupação. Já direcionei o seu atendimento "
               "com prioridade máxima para a gerência da fábrica assumir a conversa agora mesmo.")
