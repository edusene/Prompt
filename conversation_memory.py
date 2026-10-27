"""
conversation_memory.py — Memoria estruturada do atendimento, por telefone.

O historico de mensagens sozinho nao e memoria confiavel: a janela e curta, o
modelo reinterpreta mensagens antigas e volta a perguntar o que ja foi dito.
Aqui cada dado coletado (nome, itens, modalidade, endereco, bairro, data,
horario, pagamento) fica guardado no banco e volta ao prompt como uma FICHA
curta e explicita, junto com a lista do que ainda falta.

Efeito pratico: o agente nao repergunta o que ja sabe nem inventa o que nao foi
dito — para cada campo ele ve "preenchido" ou "FALTA PERGUNTAR".
"""

import datetime as _dt
import re as _re
import time

import database

# Campos que o cliente pode corrigir durante a conversa (a fala mais recente vence)
_CAMPOS_REVISAVEIS = (
    "name", "modality", "bairro", "endereco", "referencia",
    "horario", "data_agendada", "data_iso", "payment_method",
)

_CAMPOS = _CAMPOS_REVISAVEIS + ("items", "volume_pendente", "sem_numero",
                                "sabores_pendentes", "preparo_pendente",
                                "pedido_por_volume")

_ROTULOS = {
    "name": "Nome do cliente",
    "items": "Itens do pedido",
    "modality": "Modalidade (entrega/retirada)",
    "bairro": "Bairro",
    "endereco": "Endereco (rua e numero)",
    "endereco_numero": "Numero da casa",
    "endereco_referencia": "Ponto de referencia (a casa nao tem numero)",
    "referencia": "Ponto de referencia",
    "data_agendada": "Data",
    "horario": "Horario",
    "payment_method": "Forma de pagamento",
    "troco_para": "Troco",
}

# O que e obrigatorio antes do resumo, por modalidade.
# Pagamento NAO entra aqui: na retirada e no balcao, e o prompt operacional
# proibe perguntar. Ele e exigido so na entrega (ver faltando()).
_OBRIGATORIOS_COMUNS = ("name", "items", "modality", "data_agendada", "horario")
_OBRIGATORIOS_ENTREGA = ("bairro", "endereco")


def _empty() -> dict:
    return {
        "name": "",
        "items": [],
        "volume_pendente": 0,
        # "frito"/"assado" dito junto com um volume sem sabor. Sem ele a
        # pergunta do preparo voltava depois de o cliente ja ter respondido.
        "preparo_pendente": "",
        # Quantidades ditas com uma palavra que serve a varios sabores
        # ("30 de carne"). [{"qtd", "termo", "opcoes"}]. Nao viram item: viram
        # pergunta. Enquanto ficarem aqui, o funil nao anda.
        "sabores_pendentes": [],
        "modality": "",
        "bairro": "",
        "bairro_nao_achado": "",
        # "metade coxinha e metade bolinha" numa porcao de 25: a proposta de 25
        # de cada, que so vira item com o "sim" do cliente.
        "proposta_metade": [],
        # "2 combos" sem dizer qual (16/10): quantos combos faltam escolher.
        "combo_pendente": 0,
        # O cliente pediu a lista dos sabores (17/10): so ai ela vai inteira.
        "mostrar_sabores": False,
        # "Quantas unidades de X?" depois do "sim" ao convite (19/10): o
        # numero da proxima mensagem e desse produto.
        "produto_em_foco": "",
        "endereco": "",
        "referencia": "",
        # O cliente afirmou que a casa nao tem numero. Nao completa o
        # endereco - troca a pendencia do numero pela do ponto de referencia,
        # que e o que o motoboy usa para achar a casa.
        "sem_numero": False,
        "horario": "",
        "data_agendada": "",
        # Dia resolvido (AAAA-MM-DD). data_agendada e o texto que vai para a
        # tela ("hoje, sexta-feira (19/09)"); data_iso e o dia de verdade, usado
        # para validar o expediente.
        "data_iso": "",
        "payment_method": "",
        # Troco, so para pagamento em dinheiro. E STRING de proposito, com
        # quatro estados: "" nao perguntado, "nao" cliente tem trocado,
        # "100.00" precisa de troco para esse valor, "nao_informado"
        # perguntamos e nao deu para entender a resposta.
        # Float nao serve: 0.0 e falsy e sumiria no merge, no load e em toda
        # checagem por truthiness; "nao" atravessa os tres intacto.
        "troco_para": "",
        "frete": None,
        # Total da ultima calculate_order. Guardado porque a regra do troco
        # ("precisa ser maior que o total") so pode ser conferida contra ele.
        "total": 0,
        "pedido_enviado": False,
        # A saudacao sai uma vez so, e na primeira resposta.
        "saudacao_feita": False,
        # A OFERTA ADICIONAL do BLOCO 2 (bebida/doce/bolo) ja foi feita nesta
        # conversa. O funil do dono e Produto/Tamanho -> Upsell -> Entrega, e
        # era sempre o upsell que sumia: em 4 dos testes de 26/09 ela foi
        # direto do pedido para o endereco, e o dono cobrou isso quatro vezes.
        "upsell_feito": False,
        # Aviso de "estamos fechados" ja dado nesta conversa.
        "aviso_horario_dado": False,
        # Quando o ultimo pedido foi fechado. Sobrevive a limpeza da ficha: e o
        # que distingue "quero mais 30 coxinhas" logo apos um pedido de um
        # pedido novo dias depois.
        "ultimo_pedido_em": "",
        # Entrega ou retirada do ultimo pedido. Sobrevive a limpeza como o
        # ultimo_pedido_em: "meu marido ja ta indo buscar" pede a resposta de
        # retirada, "saiu pra entrega?" a de entrega.
        "ultimo_pedido_modalidade": "",
        # "hoje, sexta-feira (23/10) às 17h": o "que horas chega?" depois do
        # pedido responde com ele (23/10).
        "ultimo_pedido_quando": "",
        # Pedido dito por volume dividido ("1 cento pastel e bolinha",
        # "metade/metade"). O balcao responde esses com o valor na hora.
        "pedido_por_volume": False,
        # Data (ISO) que o AGENTE ofereceu e o cliente ainda nao aceitou. Sem
        # isto, "Pode sim" depois de "Posso deixar para segunda-feira (21/09)?"
        # nao preenchia data nenhuma e a Sandrinha perguntava o dia de novo.
        "data_proposta": "",
        # Horario que o AGENTE ofereceu ("adiantar para as 14h?"), para o
        # "sim" seguinte gravar a hora junto com o dia.
        "horario_proposto": "",
        # Volume com sabores que nao dividem em lotes de 25 ("1 cento" +
        # "coxinha, pastel e churros"): {"volume": 100, "sabores": [...]}.
        # Enquanto existir, a pergunta e "a porcao a mais fica com qual sabor?".
        "divisao_pendente": {},
        # A conversa acabou de voltar da fila humana (volta_automatica): a
        # instrucao com o que a equipe tratou, para a PRIMEIRA resposta do
        # modelo. Usada uma vez e apagada.
        "contexto_retomada": "",
        # O cliente disse que mandou os sabores errados (pedido aberto): os
        # salgados da proxima mensagem TROCAM os da ficha, nao somam (22/10).
        "trocar_sabores": False,
        "updated_at": "",
    }


# Depois deste tempo parado — ou virando o dia — a conversa recomeça: o cliente
# e cumprimentado de novo e volta a ser avisado se a fabrica estiver fechada.
HORAS_ATE_NOVA_SESSAO = 6

# Marcas que valem por CONVERSA, nao pela vida do cliente.
_CAMPOS_DE_SESSAO = ("saudacao_feita", "aviso_horario_dado", "upsell_feito")


def _sessao_expirou(updated_at: str, agora=None) -> bool:
    """A ultima mensagem foi em outro dia ou ha muitas horas?

    Sem isto, `saudacao_feita` ficava True para sempre: o cliente era
    cumprimentado uma unica vez na vida e nunca mais, e quem ja tinha ouvido
    "estamos fechados" nunca mais era avisado.
    """
    if not updated_at:
        return False
    agora = agora or _dt.datetime.now()
    try:
        ultima = _dt.datetime.fromisoformat(str(updated_at))
    except (TypeError, ValueError):
        return False
    if ultima.date() != agora.date():
        return True
    return (agora - ultima) >= _dt.timedelta(hours=HORAS_ATE_NOVA_SESSAO)


def _pedido_abandonado(updated_at: str, agora=None) -> bool:
    """O pedido pela metade ja pode ser descartado?

    Criterio diferente do da saudacao de proposito. A saudacao volta a valer
    tambem na virada do dia; o PEDIDO nao pode sumir so porque o relogio passou
    da meia-noite — quem esta montando um pedido as 23h50 e continua as 00h05
    perderia tudo no meio da conversa. Aqui vale so o silencio longo.
    """
    if not updated_at:
        return False
    agora = agora or _dt.datetime.now()
    try:
        ultima = _dt.datetime.fromisoformat(str(updated_at))
    except (TypeError, ValueError):
        return False
    return (agora - ultima) >= _dt.timedelta(hours=HORAS_ATE_NOVA_SESSAO)


def load(phone: str) -> dict:
    """Carrega a memoria estruturada do cliente (vazia se nao houver)."""
    data = _empty()
    try:
        stored = database.get_conversation_state(phone)
    except Exception as e:
        print(f"[memoria] WARN falha ao carregar {phone}: {e}")
        stored = None
    if stored:
        for key in data:
            if key in stored and stored[key] not in (None, ""):
                data[key] = stored[key]

        marca = stored.get("updated_at", "")
        if _pedido_abandonado(marca):
            # Pedido montado pela metade e abandonado ontem NAO volta hoje. So
            # os campos de sessao eram zerados aqui, entao itens, data, horario
            # e pagamento de uma conversa antiga ressuscitavam: o cliente dizia
            # "ola boa noite" e a Sandrinha ja respondia com um resumo cheio de
            # dados que ele nunca informou nesta conversa.
            tinha = [c for c in ("items", "data_iso", "horario", "payment_method",
                                 "modality") if data.get(c)]
            if tinha:
                print(f"[memoria] {phone}: pedido abandonado descartado "
                      f"({', '.join(tinha)}) - parado desde {marca}")
            data = _so_dados_do_cliente(data)

        if _sessao_expirou(marca):
            for campo in _CAMPOS_DE_SESSAO:
                data[campo] = False
            print(f"[memoria] {phone}: conversa nova (ultima em "
                  f"{marca}) - saudacao volta a valer")
    return data


def save(phone: str, data: dict) -> None:
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        database.save_conversation_state(phone, data)
    except Exception as e:
        print(f"[memoria] WARN falha ao salvar {phone}: {e}")


def reset(phone: str) -> None:
    try:
        database.delete_conversation_state(phone)
    except Exception as e:
        print(f"[memoria] WARN falha ao limpar {phone}: {e}")


def _e_bebida(nome) -> bool:
    """O item e bebida (categoria Bebidas da planilha)?"""
    return _categoria_do_item(str(nome or "")) == "bebidas"


def _e_acompanhamento(nome) -> bool:
    """Bebida, bolo ou doce: o que a oferta adicional soma ao pedido."""
    return _categoria_do_item(str(nome or "")) in ("bebidas", "bolos e doces")


# O cliente esta TROCANDO o pedido, e nao somando a ele.
_RX_TROCA_O_PEDIDO = _re.compile(
    r"\b(?:na\s+verdade|corrig\w*|troca\w*|em\s+vez|ao\s+inves|no\s+lugar|so\s+quero|quero\s+so"
    r"|somente|apenas|esquece\w*|tira\w*|retira\w*|mud\w*\s+(?:o\s+)?pedido|refaz\w*)\b")


# "tira as esfirras", "retira os 30 pasteis", "sem a coca", "em vez da esfirra".
_RX_TIRA_ITEM = _re.compile(
    r"\b(?:tira|tirar|tire|retira|retirar|retire|remove|remover|exclui|excluir|cancela|cancelar"
    # "nao quero tuchaua" foi ignorado 4 vezes (Thayline, 24/10).
    r"|sem|em\s+vez\s+d[eoa]s?|ao\s+inves\s+d[eoa]s?|nao\s+(?:quero|vou\s+querer))\s+"
    # "tira SO os quibes" (teste do dono, 08/10 12h39)
    r"(?:(?:so|somente|apenas)\s+)?"
    r"(?:o\s+|a\s+|os\s+|as\s+|aquel[ea]s?\s+|tod[oa]s\s+(?:os\s+|as\s+)?)?(?:(?P<qtd>\d+)\s+)?(?:de\s+)?"
    r"(?P<alvo>[a-z]{4,}(?:\s+de\s+[a-z]{4,})?)")


def _tiradas_parciais(texto: str, removidos: list) -> dict:
    """"tira 25 coxinhas" com 50 na ficha: tira 25, nao a linha inteira.

    {id(item): quantidade a tirar} so quando o numero e MENOR que o da ficha.
    Antes "tira 50 coxinhas" de um cento apagava as 100 (teste do dono, 08/10
    16h04 e 19h37).
    """
    if not texto or not removidos:
        return {}
    try:
        from agents.conversation import _forma_de_busca
    except Exception:
        return {}
    parciais = {}
    for m in _RX_TIRA_ITEM.finditer(_sem_acento(texto)):
        if not m.group("qtd"):
            continue
        n = int(m.group("qtd"))
        palavras = [w[:5] for w in _forma_de_busca(m.group("alvo")).split()
                    if len(w) >= 4 and w != "de"]
        for item in removidos:
            nome = _forma_de_busca(f"{item.get('name', '')} {item.get('sabor') or ''}")
            try:
                atual = int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                atual = 0
            if palavras and all(any(w.startswith(p) for w in nome.split()) for p in palavras) \
                    and 0 < n < atual:
                parciais[id(item)] = n
    return parciais


def _itens_a_tirar(texto: str, atuais: list) -> list:
    """Itens da ficha que o cliente mandou tirar - so os citados."""
    if not texto or not atuais:
        return []
    try:
        from agents.conversation import _forma_de_busca
    except Exception:
        return []
    alvos = [_forma_de_busca(m.group("alvo")) for m in _RX_TIRA_ITEM.finditer(_sem_acento(texto))]
    tirar = []
    for alvo in alvos:
        palavras = [w[:5] for w in alvo.split() if len(w) >= 4 and w != "de"]
        if not palavras:
            continue
        for item in atuais:
            # O sabor conta: "tira a coca" e o Refrigerante 2 Litros (Coca cola).
            nome = _forma_de_busca(f"{item.get('name', '')} {item.get('sabor') or ''}")
            # Todas as palavras do que foi dito batem com o item ("esfirra de
            # carne" tira so a de carne; "esfirras" tira a esfirra).
            if all(any(w.startswith(p) for w in nome.split()) for p in palavras) \
                    and item not in tirar:
                tirar.append(item)
    return tirar


def _pede_tirar_produto(texto: str) -> bool:
    """"tira a coca", "remove os quibes": o cliente mandou tirar UM produto.

    Mesmo que ele nao esteja na ficha, nao e troca do pedido inteiro: o "tira"
    sem item correspondente apagava o carrinho todo (pedido do dono, 08/10).
    "Tira tudo" e "retirar amanha" (retirada) nao contam.
    """
    try:
        from agents.conversation import _produto_conhecido
    except Exception:
        return False
    for m in _RX_TIRA_ITEM.finditer(_sem_acento(texto or "")):
        verbo = m.group(0).split()[0]
        if not verbo.startswith(("tir", "retir", "remov", "exclu", "cancel")):
            continue
        if m.group("alvo").split()[0] in ("tudo", "pedido"):
            continue
        if _produto_conhecido(m.group("alvo")):
            return True
    return False


# Categorias de salgado que entram na lista de opcoes do sabor que nao existe.
_CATEGORIAS_DE_SALGADO = ("fritos", "assados", "salgados grandes")


def alternativas_do_sabor(item_pedido: str, quantidade: int = 0) -> list[str]:
    """Os sabores da mesma categoria do que o cliente pediu e nao existe.

    "50 empadas de queijo" -> os assados de festa (a empada e assada). Festa
    (25 ou mais, ou sem quantidade) oferece os sabores de pacote; abaixo de 25,
    os salgados grandes. Tipo que o cardapio nao tem (torta, sanduiche): os
    fritos e assados de festa. Bolo: os bolos e doces. Pedido do dono (08/10):
    o bot contorna sozinho, sem chamar a equipe.
    """
    try:
        import facts
        from agents.conversation import _forma_de_busca
        produtos = [p for p in facts.get()["produtos"] if p.get("disponivel", True)]
    except Exception:
        return []
    if _sem_acento(item_pedido or "").startswith("bolo"):
        return list(dict.fromkeys(p["item"] for p in produtos
                                  if _sem_acento(p["categoria"]) == "bolos e doces"))
    salgados = [p for p in produtos if _sem_acento(p["categoria"]) in _CATEGORIAS_DE_SALGADO]
    pacote = [p for p in salgados if p.get("preco") is None]
    grandes = [p for p in salgados if p.get("preco") is not None
               and _sem_acento(p["categoria"]) == "salgados grandes"]
    palavras = [w for w in _forma_de_busca(item_pedido or "").split()
                if w not in ("mini", "grande", "salgado", "salgadinho")]
    base = palavras[0][:5] if palavras else ""
    if base in ("past", "pate"):
        base = "paste"
    if 0 < quantidade < 25:
        return list(dict.fromkeys(p["item"] for p in grandes))
    mesma_base = [p for p in pacote if base
                  and _forma_de_busca(p["item"]).split()[0].startswith(base[:4])]
    categorias = {_sem_acento(p["categoria"]) for p in mesma_base}
    return list(dict.fromkeys(p["item"] for p in pacote
                              if not categorias or _sem_acento(p["categoria"]) in categorias))


def _rotulo_das_opcoes(opcoes: list[str]) -> str:
    """"assados", "fritos", "salgados grandes", "bolos e doces" ou "salgados"."""
    try:
        import facts
        # A categoria comum a TODAS as opcoes: Quibe e Rissole tambem existem
        # como salgado grande, e a lista dos fritos saia como "salgados".
        produtos = facts.get()["produtos"]
        categorias = None
        for opcao in opcoes:
            suas = {_sem_acento(p["categoria"]) for p in produtos if p["item"] == opcao}
            categorias = suas if categorias is None else categorias & suas
        categorias = categorias or set()
    except Exception:
        categorias = set()
    return categorias.pop() if len(categorias) == 1 else "salgados"


def aviso_de_sabor_inexistente(item_pedido: str, opcoes: list[str]) -> str:
    """Texto do dono (08/10): avisa, mostra as opcoes e segue o atendimento."""
    try:
        from agents.conversation import _produto_conhecido
        tipo_existe = bool(_produto_conhecido(item_pedido or ""))
    except Exception:
        tipo_existe = True
    esse = "esse sabor" if tipo_existe else "esse item"
    if _sem_acento(item_pedido or "").startswith("bolo"):
        esse = "bolo"
    return (f"Não temos {esse} no momento. Nossos {_rotulo_das_opcoes(opcoes)} "
            f"disponíveis são:\n" + "\n".join(f"• {o}" for o in opcoes)
            + "\nQual você prefere? 😊")


def _mesmo_item(a: dict, b: dict) -> bool:
    """O mesmo produto da ficha, mesmo com o nome escrito de outro jeito."""
    # Coxinha GRANDE (lanche) e coxinha mini sao linhas diferentes: "25
    # coxinhas" depois de "3 coxinha grande" apagava as 3 grandes (14/10).
    if (a.get("tamanho") == "lanche") != (b.get("tamanho") == "lanche"):
        return False
    na, nb = _sem_acento(str(a.get("name"))), _sem_acento(str(b.get("name")))
    if na == nb:
        return True
    try:
        import facts
        oa = (facts.resolve_unit_price(str(a.get("name")), int(a.get("quantity") or 25)) or {})
        ob = (facts.resolve_unit_price(str(b.get("name")), int(b.get("quantity") or 25)) or {})
        return bool(oa.get("item_oficial")) and oa.get("item_oficial") == ob.get("item_oficial")
    except Exception:
        return False


def _ajustar_ao_preparo(memoria: dict, texto: str, mudancas: list) -> None:
    """Sabor que existe frito E assado segue o preparo que o cliente disse.

    "2 centos de salgados assados" e depois "25 enroladinho": entrou o
    Enroladinho de Salsicha (frito), e nao o com Catupiry (assado) - teste da
    Thayline, 23/10. So troca quando o preparo e um so e a mensagem nao diz o
    outro.
    """
    preparo = (memoria.get("preparo_pendente") or "").strip()
    if preparo not in ("frito", "assado"):
        return
    outro = "frito" if preparo == "assado" else "assado"
    if _re.search(rf"\b{outro[:4]}", _sem_acento(texto or "")):
        return
    do_preparo = _sabores_do_pacote(preparo + "s")
    do_outro = {_sem_acento(n) for n in _sabores_do_pacote(outro + "s")}
    for item in memoria.get("items") or []:
        if not isinstance(item, dict) or _sem_acento(str(item.get("name", ""))) not in do_outro:
            continue
        raiz = _sem_acento(str(item["name"])).split()[0]
        par = [n for n in do_preparo if _sem_acento(n).split()[0] == raiz]
        if len(par) != 1:
            continue
        antigo = item["name"]
        item["name"] = par[0]
        try:
            import facts
            oficial = facts.resolve_unit_price(par[0], int(item.get("quantity") or 0))
            if oficial:
                item["price"] = oficial["preco_unitario"]
        except Exception:
            pass
        mudancas.append(f"{antigo} -> {par[0]} (pedido {preparo})")


def merge(memoria: dict, extraido: dict, novo_da_mensagem: dict | None = None,
          texto: str = "") -> tuple[dict, list]:
    """Funde o que foi extraido da mensagem na memoria acumulada.

    `extraido` vem da extracao feita ja considerando o que a memoria tem (so
    preenche lacunas). `novo_da_mensagem` vem de uma extracao sem estado: serve
    para o cliente CORRIGIR um dado ("na verdade e no Olaria"), caso em que o
    valor novo substitui o antigo.
    """
    mudancas = []

    for campo, valor in (extraido or {}).items():
        if campo not in _CAMPOS or not valor:
            continue
        if not memoria.get(campo):
            memoria[campo] = valor
            mudancas.append(f"{campo}={valor}")

    # CEP com bairro fora da tabela de frete (18/10): a pergunta do bairro
    # diz que nao achou, como no bairro digitado.
    _bairro_cep = ((novo_da_mensagem or {}).get("bairro_do_cep")
                   or (extraido or {}).get("bairro_do_cep"))
    if _bairro_cep and not memoria.get("bairro"):
        memoria["bairro_nao_achado"] = str(_bairro_cep).title()

    for campo in _CAMPOS_REVISAVEIS:
        novo = (novo_da_mensagem or {}).get(campo)
        # O mesmo CEP mandado de novo traz so a rua: nao apaga o numero ja dito.
        if (campo == "endereco" and novo and memoria.get(campo)
                and str(memoria[campo]).lower().startswith(str(novo).lower())):
            continue
        if novo and memoria.get(campo) and novo != memoria[campo]:
            print(f"[memoria] {campo}: '{memoria[campo]}' -> '{novo}' (cliente corrigiu)")
            memoria[campo] = novo
            mudancas.append(f"{campo}={novo}")

    itens_novos = (novo_da_mensagem or {}).get("items")
    atuais = [i for i in (memoria.get("items") or []) if isinstance(i, dict)]
    # "Na verdade tira as esfirras e coloca 25 coxinhas": sai SO o item citado
    # e o resto do carrinho fica. Antes o "tira" trocava o pedido inteiro e os
    # 100 pasteis sumiram junto (pedido do dono, 08/10).
    removidos = _itens_a_tirar(texto, atuais)
    removidos_inteiros = []
    if removidos:
        parciais = _tiradas_parciais(texto, removidos)
        for item in removidos:
            if id(item) in parciais:
                item["quantity"] = int(item.get("quantity") or 0) - parciais[id(item)]
                mudancas.append(f"tirados {parciais[id(item)]} de {item.get('name')}")
        removidos_inteiros = [r for r in removidos if id(r) not in parciais]
        atuais = [i for i in atuais
                  if not any(i is r for r in removidos) or id(i) in parciais]
        memoria["items"] = atuais
        mudancas.append(f"tirados: {[i.get('name') for i in removidos if id(i) not in parciais]}")
        itens_novos = [n for n in (itens_novos or []) if isinstance(n, dict)
                       and not any(_mesmo_item(n, r) for r in removidos)]
    _troca_pedida = bool(memoria.get("trocar_sabores")) and any(
        isinstance(n, dict) and not _e_acompanhamento(n.get("name")) for n in (itens_novos or []))
    if _troca_pedida:
        memoria["trocar_sabores"] = False
        mudancas.append("troca de sabores pedida antes: salgados substituidos")
    if itens_novos and itens_novos != memoria.get("items") and atuais \
            and not _troca_pedida \
            and (removidos or _pede_tirar_produto(texto)
                 or not _RX_TROCA_O_PEDIDO.search(_sem_acento(texto))):
        # Lista CUMULATIVA (pedido do dono, 07/10): item citado de novo tem a
        # quantidade atualizada, item novo entra, e o que nao foi citado FICA.
        # Antes a mensagem com itens trocava o pedido inteiro: "Sim, ajusta para
        # 50 esfirras" apagou a coxinha, e o Pao Pizza sumiu do espelho final.
        combinado = [dict(i) for i in atuais]
        # Resposta a "quais sabores completam os que faltam?": o sabor que ja
        # esta no pedido SOMA. "50 pao pizza" trocou os 25 por 50 e a conta
        # ficou em 125 de 200 (teste da Thayline, 23/10).
        _falta = falta_para_o_volume(memoria)
        try:
            _soma_nova = sum(int(n.get("quantity") or 0) for n in itens_novos
                             if isinstance(n, dict) and not _e_acompanhamento(n.get("name")))
        except (TypeError, ValueError):
            _soma_nova = 0
        _completa = bool(_falta) and 0 < _soma_nova <= _falta and not removidos
        for novo in itens_novos:
            if not isinstance(novo, dict):
                continue
            igual = next((i for i in combinado if _mesmo_item(i, novo)), None)
            if igual and _completa and not _e_acompanhamento(novo.get("name")):
                igual["quantity"] = int(igual.get("quantity") or 0) + int(novo.get("quantity") or 0)
                continue
            if igual:
                for campo in ("quantity", "price", "tamanho", "sabor"):
                    if novo.get(campo) not in (None, "", 0):
                        igual[campo] = novo[campo]
            else:
                combinado.append(dict(novo))
        if combinado != atuais:
            memoria["items"] = combinado
            mudancas.append(f"items={combinado}")
    elif itens_novos and itens_novos != memoria.get("items"):
        # Troca dita ("na verdade", "em vez de", "so quero"): os salgados sao
        # trocados; a bebida ja pedida fica. "Troca para 50 coxinhas" nao e
        # "tira o refrigerante". Vale tambem para bolo e doce.
        bebidas_antigas = [i for i in atuais if _e_acompanhamento(i.get("name"))]
        if not any(_e_acompanhamento(i.get("name")) for i in itens_novos if isinstance(i, dict)):
            itens_novos = list(itens_novos) + bebidas_antigas
        memoria["items"] = itens_novos
        mudancas.append(f"items={itens_novos}")

    fonte = novo_da_mensagem or extraido or {}
    # Combos somam ao pedido como a bebida (15/10).
    for _cb in fonte.get("combos") or (extraido or {}).get("combos") or []:
        _itens_cb = list(memoria.get("items") or [])
        _igual_cb = next((i for i in _itens_cb if isinstance(i, dict) and i.get("name") == _cb["name"]), None)
        if _igual_cb:
            _igual_cb["quantity"] = _cb["quantity"]
        else:
            _itens_cb.append(dict(_cb))
        memoria["items"] = _itens_cb
        mudancas.append(f"combo={_cb['quantity']} x {_cb['name']}")
    bebidas = fonte.get("bebidas") or (extraido or {}).get("bebidas") or []
    if bebidas:
        itens = list(memoria.get("items") or [])
        # "pode ser o guarana" RESPONDE a pergunta do refrigerante: a bebida
        # fecha a pendencia com a quantidade pedida. Somada como item novo e
        # depois de novo pela escolha do sabor, saiam 2 Tuchauas no lugar de
        # 1 e o cliente pagava R$ 12,00 a mais (teste do Eduardo, 28/09 21h39).
        pendentes = list(memoria.get("sabores_pendentes") or [])
        aberta = next((p for p in pendentes if p.get("termo") == "refrigerante"), None)
        if aberta:
            bebidas = [dict(bebidas[0], quantity=int(aberta.get("qtd") or 1))] + list(bebidas[1:])
            memoria["sabores_pendentes"] = [p for p in pendentes if p is not aberta]
            mudancas.append(f"refrigerante -> {bebidas[0]['name']}")
        for b in bebidas:
            # "tira o bolo de milho": o bolo vem de novo na extracao (a frase
            # o cita) e voltava para o pedido (teste do dono, 08/10 15h50).
            if any(_mesmo_item(b, r) for r in removidos_inteiros):
                continue
            # Coca e Fanta de 2 litros sao linhas diferentes (24/10). Sem sabor
            # de um lado, e a mesma linha, e o sabor dito completa a ficha.
            igual = next((i for i in itens if isinstance(i, dict) and i.get("name") == b["name"]
                          and (not b.get("sabor") or not i.get("sabor") or i["sabor"] == b["sabor"])), None)
            if igual is not None and b.get("sabor") and not igual.get("sabor"):
                igual["sabor"] = b["sabor"]
                mudancas.append(f"bebida {b['name']} sabor={b['sabor']}")
            if igual is None:
                itens.append(dict(b))
                mudancas.append(f"bebida={b['quantity']} x {b['name']}")
            elif (int(b.get("quantity") or 1) > 1 and b["quantity"] != igual.get("quantity")
                  and not _re.search(r"\b(?:mais|acrescent\w*|adicion\w*|inclu\w*)\b",
                                     _sem_acento(texto or ""))):
                # "3 coca cola 2litros" com o refri ja na ficha: a quantidade
                # dita vale. Ficava 1 e o resumo cobrava uma so (Thayline, 23/10).
                igual["quantity"] = int(b["quantity"])
                mudancas.append(f"bebida -> {b['quantity']} x {b['name']}")
        memoria["items"] = itens
    sem_tamanho = fonte.get("bebidas_pendentes") or (extraido or {}).get("bebidas_pendentes") or []
    # "e uma fanta pra mim tambem" com a Fanta uva ja na ficha: a segunda
    # bebida sumia sem pergunta (teste de 27/10).
    _bebida_a_mais = bool(_re.search(r"\b(?:mais|tambem|outr[ao]s?|acrescent\w*|adicion\w*|inclu\w*)\b",
                                     _sem_acento(texto or "")))
    if sem_tamanho and (_bebida_a_mais or not any(_e_bebida(i.get("name")) for i in memoria.get("items") or []
                                                  if isinstance(i, dict))):
        pendentes = list(memoria.get("sabores_pendentes") or [])
        aberta = next((p for p in pendentes if p.get("termo") == "refrigerante"), None)
        if not aberta:
            pendentes += sem_tamanho
            memoria["sabores_pendentes"] = pendentes
            mudancas.append("bebida sem tamanho -> pergunta")
        elif sem_tamanho[0].get("em_falta") and not aberta.get("em_falta"):
            # Pergunta ja aberta ("qual refrigerante?") e o cliente responde com
            # o esgotado ("Coca 2 litros"): a pergunta volta, agora dizendo que
            # esse acabou - sem isso ela so repetia as opcoes (29/09 15h14).
            aberta["em_falta"] = sem_tamanho[0]["em_falta"]
            memoria["sabores_pendentes"] = pendentes
            mudancas.append(f"refrigerante em falta: {aberta['em_falta']}")

    # Brigadeiro: com formato vira item (e fecha a pergunta do formato, se
    # estava aberta); sem formato, abre a pergunta com os tres formatos.
    doces = fonte.get("doces") or (extraido or {}).get("doces") or []
    pendentes = list(memoria.get("sabores_pendentes") or [])
    aberta_doce = next((p for p in pendentes if p.get("termo") == "docinho"), None)
    if doces:
        itens = list(memoria.get("items") or [])
        if aberta_doce:
            memoria["sabores_pendentes"] = [p for p in pendentes if p is not aberta_doce]
            aberta_doce = None
        for d in doces:
            if any(_mesmo_item(d, r) for r in removidos_inteiros):
                continue
            igual = next((i for i in itens if isinstance(i, dict) and i.get("name") == d["name"]), None)
            if igual is None:
                itens.append(dict(d))
                mudancas.append(f"doce={d['quantity']} x {d['name']}")
            elif d.get("sabor") and igual.get("sabor") != d["sabor"]:
                # "pode ser misto" com o cento de brigadeiro ja na ficha: o
                # sabor novo vale para o doce que esta la (teste de 22/10).
                igual["sabor"] = d["sabor"]
                mudancas.append(f"doce {d['name']} sabor={d['sabor']}")
        memoria["items"] = itens
    doces_sem_formato = fonte.get("doces_pendentes") or (extraido or {}).get("doces_pendentes") or []
    if doces_sem_formato and not doces:
        if not aberta_doce:
            memoria["sabores_pendentes"] = list(memoria.get("sabores_pendentes") or []) + doces_sem_formato
            mudancas.append("doce sem formato -> pergunta")
        elif doces_sem_formato[0].get("em_falta") and not aberta_doce.get("em_falta"):
            aberta_doce["em_falta"] = doces_sem_formato[0]["em_falta"]

    sabores_novos = (novo_da_mensagem or {}).get("sabores_pendentes")
    if sabores_novos and sabores_novos != memoria.get("sabores_pendentes"):
        # A pergunta do refrigerante/docinho aberta nesta mesma mensagem fica:
        # "100 pasteis e uma coca" perdia a coca quando o pastel virava
        # pergunta (simulador do dono, 15/10).
        _termos_novos = {p.get("termo") for p in sabores_novos}
        _mantidas = [p for p in (memoria.get("sabores_pendentes") or [])
                     if p.get("termo") in ("refrigerante", "docinho") and p.get("termo") not in _termos_novos]
        memoria["sabores_pendentes"] = _mantidas + list(sabores_novos)
        mudancas.append(f"sabores_pendentes={[s['termo'] for s in sabores_novos]}")

    _volume_novo(memoria, novo_da_mensagem or {}, mudancas)
    _ajustar_ao_preparo(memoria, texto, mudancas)

    # Troco so existe para dinheiro. Quem pediu troco para 100 e depois trocou
    # para Pix nao pode levar essa observacao para a comanda.
    if memoria.get("troco_para") and memoria.get("payment_method") != "dinheiro":
        memoria["troco_para"] = ""
        mudancas.append("troco_para=(limpo, pagamento nao e mais dinheiro)")

    return memoria, mudancas


# Como a pergunta do agente aparece, por campo que ela preenche. Serve para
# ler a resposta seca do cliente ("Eduardo", "Olaria"), que as regras de
# extracao nao pegam por nao ter nenhuma palavra-chave em volta. Sem isto o
# agente perguntava, o cliente respondia, e ele perguntava de novo.
_PERGUNTAS_POR_CAMPO = {
    "name": ("seu nome", "qual e o nome", "qual é o nome", "como posso te chamar",
             "com quem eu falo", "com quem falo"),
    "bairro": ("qual bairro", "qual o bairro", "qual é o bairro", "qual e o bairro",
               "nome do bairro", "em que bairro", "de que bairro"),
}

# Respostas curtas que nunca sao um nome proprio nem um bairro.
_NAO_E_RESPOSTA = {
    "sim", "nao", "não", "ok", "blz", "beleza", "pode", "pode ser", "isso",
    "certo", "claro", "obrigado", "obrigada", "entrega", "retirada", "hoje",
    "amanha", "amanhã", "pix", "dinheiro", "cartao", "cartão", "debito",
    "débito", "credito", "crédito",
}


# A diretiva de coleta pede data, horario e numero NUMA MENSAGEM SO, entao a
# resposta vem junta: "45, amanha as 16h". Para achar o numero da casa ali eu
# apago os numeros que sao de outra coisa; se sobrar exatamente um, e ele.
# Sem isto o numero ficava solto na conversa e o modelo inventava sentido para
# ele - leu "45" como quantidade e respondeu com a regra do minimo de 25.
_NUMERO_DE_OUTRA_COISA = _re.compile(
    r"\b(?:as|ate)\s+\d{1,2}\s+\d{2}\b"              # "as 14 00" (14:00 sem os dois pontos)
    r"|\d{1,2}\s*[:h]\s*\d{0,2}\b"                    # 16h, 16:30
    r"|\b(?:as|ate|apos|depois\s+das)\s+\d{1,2}\b"      # as 16
    r"|\d{1,2}\s*h[oras]{0,4}\b"                         # 16 horas, 16 hras
    r"|\d{1,2}\s*/\s*\d{1,2}(?:\s*/\s*\d{2,4})?"   # 26/09/2026
    r"|\bdia\s+\d{1,2}\b"                                # dia 26
    r"|\d{1,2}\s+de\s+\w+"                              # 26 de setembro
    r"|\d+\s*(?:un\b|unidades?|centos?|por[cs][oa]{1,2}es?|por[cs]ao|salgad\w*)"
    r"|\d+\s*reais|r$\s*\d[\d.,]*",                      # dinheiro
    _re.IGNORECASE)

# "numero 45", "n 45", "casa 45": rotulo explicito dispensa adivinhacao.
_NUMERO_COM_ROTULO = _re.compile(
    r"\b(?:numero|num|n|no|casa|lote)\s*[.:]?\s*(?:o\s*)?(\d{1,6})\b",
    _re.IGNORECASE)

_QUALQUER_NUMERO = _re.compile(r"\b(\d{1,6})\b")

# Quem esta mexendo no pedido nao esta respondendo o endereco.
_MEXENDO_NO_PEDIDO = _re.compile(
    r"\b(quero|queria|gostaria|manda|coloca|poe|adiciona|acrescenta|"
    r"tira|troca|mais)\b", _re.IGNORECASE)


def _numero_da_casa_no_texto(norm: str) -> str:
    """O numero de casa dentro de uma frase, ou "" quando nao da para afirmar."""
    rotulado = _NUMERO_COM_ROTULO.search(norm)
    if rotulado:
        return rotulado.group(1)

    # Um produto citado significa pedido, nao endereco: "quero 50 pao pizza"
    # nao pode virar casa 50.
    try:
        from agents.conversation import _produto_conhecido
        for pedaco in _re.split(r"[^a-z]+", norm):
            if len(pedaco) > 3 and _produto_conhecido(pedaco):
                return ""
    except Exception:
        pass
    if _MEXENDO_NO_PEDIDO.search(norm):
        return ""

    sobrou = _QUALQUER_NUMERO.findall(_NUMERO_DE_OUTRA_COISA.sub(" ", norm))
    return sobrou[0] if len(sobrou) == 1 else ""


def capturar_numero_da_casa(memoria: dict, texto: str) -> str:
    """Junta ao endereco o numero que o cliente acabou de responder.

    Sem isto a pergunta nao tinha resposta possivel: o cliente dizia "45" ou
    "e S/N, casa sem numero", nada entrava na ficha, e a pendencia do numero
    ficava de pe para sempre. Exigir sem saber receber e travar o pedido.

    So age quando o numero esta REALMENTE pendente, e devolve o endereco novo
    (ou "" quando nao capturou).
    """
    if not endereco_sem_numero(memoria):
        return ""
    bruto = " ".join((texto or "").split())
    if not bruto or len(bruto) > 120:
        return ""

    # O CEP nao e o numero da casa: "76804-120" virava "Rua X, 76804" (18/10).
    import cep as _cep_mod
    norm = _normalizar_sem_acento(_cep_mod.tirar_cep(bruto))
    endereco = str(memoria.get("endereco") or "").strip()
    if _SEM_NUMERO_ACEITO.search(norm):
        # Nao completa o endereco: marca que nao ha numero, e a pendencia
        # passa a ser o ponto de referencia.
        memoria["sem_numero"] = True
        return "casa sem numero - vai pedir ponto de referencia"

    numero = _numero_da_casa_no_texto(norm)
    if not numero:
        return ""
    memoria["endereco"] = f"{endereco}, {numero}"
    memoria["sem_numero"] = False
    return memoria["endereco"]


def _endereco_da_loja() -> str:
    try:
        import cardapio
        return cardapio.endereco_fabrica()
    except Exception:
        return "Av. Calama, 5892 - Igarapé"


def capturar_resposta_direta(memoria: dict, pergunta_anterior: str, texto: str) -> str:
    """Preenche o campo que a pergunta anterior pediu, a partir da resposta seca.

    Retorna o nome do campo preenchido, ou "" se a mensagem nao for uma
    resposta direta. So age em campo ainda vazio: nunca sobrescreve um dado
    que o cliente ja tinha dado.
    """
    pergunta = (pergunta_anterior or "").lower()
    candidato = (texto or "").strip().strip(".!,;:")
    if not pergunta or not candidato:
        return ""
    if candidato.lower() in _NAO_E_RESPOSTA or any(c.isdigit() for c in candidato):
        return ""
    if not (0 < len(candidato.split()) <= 3) or len(candidato) < 2:
        return ""

    for campo, marcas in _PERGUNTAS_POR_CAMPO.items():
        if memoria.get(campo):
            continue
        if any(m in pergunta for m in marcas):
            if campo == "bairro":
                # "IGAPARE" entrava cru: o frete saia R$ 0,00 e o resumo sem a
                # linha do frete (teste do dono, 08/10 21h49). Vale o nome da
                # tabela; fora dela, a pergunta volta dizendo que nao achou.
                try:
                    import facts
                    achado = facts.lookup_frete(candidato)
                except Exception:
                    achado = None
                if not achado:
                    memoria["bairro_nao_achado"] = candidato.title()
                    return ""
                memoria["bairro_nao_achado"] = ""
                memoria[campo] = achado["bairro"]
                return campo
            memoria[campo] = candidato.title()
            return campo
    return ""


_NEGATIVAS_DE_TROCO = (
    "nao precisa", "nao vou precisar", "nao preciso", "sem troco", "tenho trocado",
    "ta trocado", "esta trocado", "valor certo", "valor exato", "certinho",
    "tenho o valor", "levo certo", "pago certinho", "trocado", "nao", "certo",
)

_EXTENSO_DE_TROCO = {
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60,
    "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100, "cento": 100,
    "duzentos": 200, "trezentos": 300, "quatrocentos": 400, "quinhentos": 500,
    "mil": 1000,
}


def _troco_por_extenso(palavras: list) -> float | None:
    """'cento e cinquenta' -> 150; 'mil e quinhentos' -> 1500.

    Pegar a primeira palavra do dicionario lia "cento e cinquenta" como 50
    (teste de 22/10): os numeros ligados por "e" se somam.
    """
    total = 0
    achou = False
    for i, p in enumerate(palavras):
        numero = _EXTENSO_DE_TROCO.get(p)
        if numero is None:
            if achou and not (p == "e" and i + 1 < len(palavras)
                              and palavras[i + 1] in _EXTENSO_DE_TROCO):
                break
            continue
        if p == "mil" and total:
            total *= 1000
        else:
            total += numero
        achou = True
    return float(total) if achou else None

# Resposta que e pedido, e nao dinheiro (sobre o texto sem acento): "cento"
# seguido de produto/sabor, ou produto citado. "cento e cinquenta" passa.
_RX_CARA_DE_PEDIDO = _re.compile(
    r"\b(?:meio\s+)?centos?\s+(?:de|do|da|mist[oa]s?|frit\w*|assad\w*|[a-z]{4,})\b"
    r"|\b(?:brigad|beijinh|docinh|salgad|coxinh|bolinh|refri|coca|guarana|esfirr|pastel|pasteis)\w*")

# Resposta que e endereco, e nao dinheiro (sobre o texto sem acento).
_RX_CARA_DE_ENDERECO = _re.compile(
    r"\b(rua|av|avenida|travessa|tv|alameda|estrada|rodovia|bairro|quadra"
    r"|qd|lote|lt|conjunto|cond|condominio|residencial|apto|apartamento|bloco"
    r"|casa|numero|cep|perto|proximo|esquina)\b")
# "Nº 2252", "n° 40", "n. 12" (sobre o texto cru: o normalize apaga o º).
_RX_NUMERO_DE_CASA = _re.compile(r"\bn\s*[º°o.]\s*\d", _re.IGNORECASE)

_NUMERO_RE = _re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?|\d+")


def _data_numerica(data_iso: str) -> str:
    """'2026-09-22' -> '22/09/2026'. Vazio se nao der para converter."""
    try:
        return _dt.date.fromisoformat(str(data_iso)).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return ""


def _brl(valor: float) -> str:
    return ("R$ %.2f" % float(valor or 0)).replace(".", ",")


def _sem_acento(texto: str) -> str:
    try:
        import facts
        return facts.normalize(texto or "")
    except Exception:
        return (texto or "").strip().lower()


# O valor do troco grudado na palavra que o anuncia. Sem esta ancora o troco
# era o PRIMEIRO numero da mensagem: em "Quero 1 porcao de 25 coxinhas ... nota
# de 100, traz troco" a ficha guardava troco de R$ 1,00, e a comanda saia
# mandando o motoboy levar um real de troco para uma nota de cem.
_NUM = r"(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_RX_TROCO_ANCORADO = [
    _re.compile(r"troco\s+(?:para|pra|p/|de)?\s*(?:r\$)?\s*" + _NUM, _re.IGNORECASE),
    _re.compile(r"(?:nota|cedula|cédula)\s+de\s+(?:r\$)?\s*" + _NUM, _re.IGNORECASE),
    _re.compile(r"(?:pagar?|pago)\s+com\s+(?:uma\s+|um\s+)?(?:nota\s+de\s+)?"
               r"(?:r\$)?\s*" + _NUM, _re.IGNORECASE),
    _re.compile(_NUM + r"\s*reais", _re.IGNORECASE),
    _re.compile(r"(?:r\$)\s*" + _NUM, _re.IGNORECASE),
]


def _valor_do_troco(texto: str) -> float | None:
    """O valor que o cliente deu como troco, ou None quando nao da para saber.

    Procura o numero ancorado em "troco"/"nota de"/"reais". So cai no primeiro
    numero solto quando a mensagem e curta — ai ela e resposta direta a
    pergunta do troco ("100") e nao ha o que confundir. Em mensagem longa, sem
    ancora, prefere nao saber: um troco errado vai para a comanda e o motoboy
    sai com o dinheiro errado no bolso.
    """
    cru = texto or ""
    for rx in _RX_TROCO_ANCORADO:
        achado = rx.search(cru)
        if achado:
            valor = _numero_brasileiro(achado.group(1))
            if valor:
                return valor

    if len(cru.split()) > 8:
        return None
    # Mensagem curta: "100", "R$ 100". Hora nao e dinheiro ("19h" nunca e troco).
    sem_hora = _re.sub(r"\b\d{1,2}\s*[h:]\S*", " ", cru, flags=_re.IGNORECASE)
    return _valor_em_reais(sem_hora)


def _numero_brasileiro(bruto: str) -> float | None:
    """'1.000,50' -> 1000.5; '100' -> 100.0. None no que nao for numero > 0."""
    if "." in bruto and "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    elif "," in bruto:
        bruto = bruto.replace(",", ".")
    elif bruto.count(".") == 1 and len(bruto.split(".")[1]) == 3:
        bruto = bruto.replace(".", "")
    try:
        valor = float(bruto)
    except ValueError:
        return None
    return valor if valor > 0 else None


def _valor_em_reais(texto: str) -> float | None:
    """Le o primeiro valor monetario do texto CRU, no formato brasileiro.

    Cru de proposito: _sem_acento apaga pontuacao ("1.000" vira "1 000",
    "R$ 100,00" vira "r 100 00"), e sem o ponto e a virgula nao da para
    distinguir milhar de centavo.
    """
    achado = _NUMERO_RE.search(texto or "")
    if not achado:
        return None
    bruto = achado.group(0)
    if "." in bruto and "," in bruto:        # 1.000,50
        bruto = bruto.replace(".", "").replace(",", ".")
    elif "," in bruto:                        # 100,50
        bruto = bruto.replace(",", ".")
    elif bruto.count(".") == 1 and len(bruto.split(".")[1]) == 3:
        bruto = bruto.replace(".", "")        # 1.000
    try:
        valor = float(bruto)
    except ValueError:
        return None
    return valor if valor > 0 else None


def capturar_troco(memoria: dict, pergunta_anterior: str, texto: str) -> str:
    """Grava o troco na ficha a partir da resposta do cliente.

    Devolve o valor gravado ("nao", "100.00") ou "" quando a mensagem nao e
    sobre troco.

    `capturar_resposta_direta` nao serve aqui: ela descarta qualquer candidato
    que tenha digito — guarda deliberada, que e o que impede "50" de virar nome
    ou bairro. Afrouxar aquela funcao estragaria as duas coisas.

    Dois gatilhos, e e a combinacao deles que evita falso positivo:
      - o CLIENTE falou "troco" -> vale sempre;
      - o AGENTE perguntou do troco na mensagem anterior -> ai uma resposta
        seca ("100", "nao precisa") conta.
    Sem a segunda amarra, "quero 100 coxinhas" viraria troco de R$ 100.

    Nao valida contra o total: isso e trabalho de pendencia_de_detalhe, que
    roda quando o total ja existe. Guardar um valor invalido e melhor do que
    perder a resposta do cliente em silencio.
    """
    if memoria.get("payment_method") != "dinheiro":
        return ""
    if memoria.get("troco_para"):
        return ""

    limpo = _sem_acento(texto)
    if not limpo:
        return ""

    cliente_falou_troco = "troco" in limpo
    agente_perguntou = "troco" in _sem_acento(pergunta_anterior)
    if not (cliente_falou_troco or agente_perguntou):
        return ""
    # Endereco nao e troco. O agente perguntou troco e rua juntos, a cliente
    # respondeu "rua bejamin 2253" e o espelho saiu com troco para R$ 2253,00
    # (teste do dono, 02/10 19h02). So o "troco" dito pela cliente passa por
    # cima disto.
    if not cliente_falou_troco and (_RX_CARA_DE_ENDERECO.search(limpo)
                                    or _RX_NUMERO_DE_CASA.search(texto)):
        return ""
    # Pedido nao e troco: "pode ser o cento misto" virou troco de R$ 100
    # ("cento" por extenso) e o pedido saiu com outro total (teste de 22/10).
    if not cliente_falou_troco and _RX_CARA_DE_PEDIDO.search(limpo):
        return ""

    # "tenho troco" / "vou precisar de troco" nao sao negativa; "nao preciso de
    # troco" e. Por isso a negativa e testada antes do numero, mas so casa se a
    # frase inteira for curta o bastante para nao carregar outra informacao.
    palavras = limpo.split()
    if len(palavras) <= 6:
        for marca in _NEGATIVAS_DE_TROCO:
            if limpo == marca or limpo.startswith(marca + " ") or f" {marca}" in f" {limpo}":
                if not any(p.isdigit() for p in limpo):
                    memoria["troco_para"] = "nao"
                    return "nao"

    valor = _valor_do_troco(texto)
    if valor is None:
        valor = _troco_por_extenso(palavras)

    if valor is not None:
        memoria["troco_para"] = f"{valor:.2f}"
        return memoria["troco_para"]

    # Perguntamos e nao deu para entender ("sim, preciso"): nada e gravado e
    # a pergunta volta. O dono proibiu espelho sem o troco definido (01/10);
    # antes ficava "nao_informado" e o pedido saia sem troco nenhum.
    if agente_perguntou:
        print(f"[memoria] troco nao entendido em {texto!r}; pergunta de novo")
    return ""


# "S/N", "SN", "sem numero": o proprio prompt oferece essa saida a quem mora
# em casa sem numeracao. Vale como numero respondido.
_SEM_NUMERO_ACEITO = _re.compile(r"\bs\s*/?\s*n\b|sem\s+numero", _re.IGNORECASE)


def endereco_sem_numero(memoria: dict) -> bool:
    """Entrega com rua, mas sem numero e sem nada que o substitua.

    O funil exigia so que o campo endereco nao estivesse vazio, e "RUA
    GUANABARA" contava como completo: o pedido do dono fechou assim em 25/09,
    R$ 92,00 para uma rua sem numero de casa. O prompt pedia o numero; pedir
    nao e garantir.

    NAO cobra quando o cliente deu ponto de referencia: quem disse como achar
    a casa ja resolveu o problema que o numero resolve, e o dono cortou a
    cobranca de referencia justamente para nao empilhar pergunta.
    """
    if (memoria.get("modality") or "") != "entrega":
        return False
    endereco = str(memoria.get("endereco") or "").strip()
    if not endereco:
        return False          # falta o endereco inteiro: quem cobra e o campo
    if any(c.isdigit() for c in endereco):
        return False
    if str(memoria.get("referencia") or "").strip():
        return False
    return True


def _normalizar_sem_acento(texto: str) -> str:
    try:
        import facts
        return facts.normalize(texto)
    except Exception:
        return (texto or "").lower()


def inferir_modalidade(memoria: dict) -> str:
    """Bairro ou rua na ficha, com itens pedidos, significa ENTREGA.

    Ninguem informa o proprio bairro para buscar no balcao. O cliente disse
    "1 cento de coxinha ... aqui no Agenor de Carvalho", e ela perguntou
    "entrega ou retirada?" - duas vezes, mesmo depois de ele dar a rua (teste
    do dono, 25/09 20h45). E a conversa travava ali: sem modalidade nao ha
    total, e sem total a trava do troco nunca dispara. As duas queixas dele
    eram este mesmo defeito.

    So preenche quando esta VAZIO: quem disse "retirada" tem a palavra, mesmo
    tendo dado o bairro. E so com itens no pedido, para uma pergunta de frete
    solta ("quanto e a entrega no Pedrinhas?") nao virar pedido de entrega.

    Devolve "entrega" quando inferiu, ou "" quando nao havia o que inferir.
    """
    if memoria.get("modality"):
        return ""
    if not (memoria.get("items") or memoria.get("volume_pendente")):
        return ""
    if not (str(memoria.get("bairro") or "").strip()
            or str(memoria.get("endereco") or "").strip()):
        return ""
    memoria["modality"] = "entrega"
    return "entrega"


def faltando(memoria: dict) -> list[str]:
    """Campos obrigatorios ainda nao coletados, na ordem do funil."""
    obrigatorios = list(_OBRIGATORIOS_COMUNS)
    if memoria.get("modality") == "entrega":
        # endereco so faz sentido depois de saber que e entrega
        idx = obrigatorios.index("modality") + 1
        obrigatorios[idx:idx] = list(_OBRIGATORIOS_ENTREGA)
        # Na retirada o pagamento e no balcao e perguntar e proibido; so a
        # entrega precisa da forma de pagamento antes do resumo.
        obrigatorios.append("payment_method")
        # Dinheiro: o troco antes do espelho (pedido do dono, BLOCOS 3 e 5).
        # O resumo saia com "Pagamento: Dinheiro", a cliente respondia o troco
        # e o espelho ia de novo - dois resumos para o mesmo pedido.
        if memoria.get("payment_method") == "dinheiro":
            obrigatorios.append("troco_para")
    pendentes = [c for c in obrigatorios if not memoria.get(c)]

    # Rua sem numero e endereco incompleto. Entra aqui, e nao numa pendencia
    # propria, porque faltando() ja e o que governa as tres portas: a diretiva
    # de coleta, a troca por resumo e o fechamento do pedido.
    if "endereco" not in pendentes and endereco_sem_numero(memoria):
        # O dono cortou o "pode ser S/N" em 25/09: sem numero o motoboy nao
        # acha a casa. Quem afirma que a casa nao tem numero cai no unico
        # substituto que funciona - o ponto de referencia.
        pendentes.append("endereco_referencia" if memoria.get("sem_numero")
                         else "endereco_numero")
    return pendentes


# Pedido de dados no formato do dono (reuniao de 01/10): lista curta, sem
# "Por favor, informe numa unica mensagem os seguintes dados", sem
# "antecipado"/"na entrega" nas formas de pagamento.
_ITEM_DA_LISTA = {
    "name": "Nome",
    "items": "Itens do pedido",
    "modality": "Entrega ou retirada",
    "bairro": "Bairro",
    "endereco_numero": "Número da casa",
    "endereco_referencia": "Ponto de referência (a casa não tem número)",
    "referencia": "Ponto de referência",
    "data_agendada": "Data do pedido",
    "horario": "Horário desejado",
    "payment_method": "Forma de pagamento (Pix, Cartão ou Dinheiro)",
}

# Um dado so: pergunta curta, e nao a lista.
_PERGUNTA_CURTA = {
    "name": "Qual o seu nome? 😊",
    "items": "Qual o seu pedido? 😊",
    "modality": "Será entrega ou retirada? 😊",
    "bairro": "Qual o bairro? 😊",
    "endereco": "Qual a rua e o número? 😊",
    # Texto do dono (02/10): "Qual numero da casa? 🛵", sem o "Falta o numero
    # da casa." que o modelo punha na frente.
    "endereco_numero": "Qual número da casa? 🛵",
    "endereco_referencia": "Qual o ponto de referência da casa? 😊",
    "data_agendada": "Para qual dia? 😊",
    "horario": "Qual horário? 😊",
    "payment_method": "Qual a forma de pagamento (Pix, Cartão ou Dinheiro)? 😊",
    "troco_para": "Precisa de troco para quanto? 💵",
}

# Rua e bairro saem do CEP (cep.py): so falta o numero da casa.
DICA_DO_CEP = "📮 Se preferir, pode me enviar só o CEP e o número da casa!"

_DADOS_DE_LOGISTICA = ("bairro", "endereco", "data_agendada", "horario", "payment_method")


def pedido_de_dados(memoria: dict, pendentes: list[str] | None = None) -> str:
    """O texto que pede o que falta, no formato curto do dono.

    Nada de logistica informado ainda -> "Informe:" com Rua e Numero em linhas
    separadas; parte ja informada -> "Faltam essas informacoes:" com "Rua e
    numero"; um dado so -> pergunta curta. '' quando nao falta nada.
    """
    pendentes = faltando(memoria) if pendentes is None else list(pendentes)
    if not pendentes:
        return ""
    # A ordem do funil e Produto -> Upsell -> Entrega/Retirada -> resto. A
    # lista misturando "Itens do pedido", "Entrega ou retirada" e "Horario"
    # saiu no teste de 01/10 como resposta a "tem refrigerante de 2 litros?":
    # sem itens, a pergunta e o pedido; sem modalidade, e a modalidade - a
    # lista da entrega (bairro, rua, numero...) so existe depois dela.
    if "items" in pendentes:
        return _PERGUNTA_CURTA["items"]
    # O troco tem pergunta propria (BLOCO 3) e vem depois do resto: nao entra
    # na lista "Faltam essas informacoes".
    if "troco_para" in pendentes and len(pendentes) > 1:
        pendentes = [c for c in pendentes if c != "troco_para"]
    if "modality" in pendentes:
        return "Será para entrega ou retirada na loja? 😊"
    if len(pendentes) == 1:
        # O dia ja foi oferecido ("Posso deixar para segunda-feira (05/10)?"):
        # repete a oferta, que o "sim" seguinte aplica. "Para qual dia?" fazia
        # a cliente escolher de novo o que ja estava proposto (teste de 01/10).
        if pendentes[0] == "data_agendada" and memoria.get("data_proposta"):
            try:
                from datetime import date as _date
                import horario as _horario
                dia = _horario.formatar_data(_date.fromisoformat(memoria["data_proposta"]))
                return f"Posso deixar para {dia}? 😊"
            except Exception:
                pass
        if pendentes[0] == "troco_para":
            return pergunta_do_troco(memoria)
        if pendentes[0] == "bairro" and memoria.get("bairro_nao_achado"):
            return (f"Não achei o bairro {memoria['bairro_nao_achado']} na nossa lista de "
                    f"entrega 🤔 Qual é o bairro certinho? Se preferir, a retirada é na "
                    f"loja ({_endereco_da_loja()}). 💛")
        if pendentes[0] in ("bairro", "endereco"):
            return f"{_PERGUNTA_CURTA[pendentes[0]]}\n\n{DICA_DO_CEP}"
        return _PERGUNTA_CURTA.get(pendentes[0], f"Qual {pendentes[0]}? 😊")
    nada_ainda = not any(memoria.get(c) for c in _DADOS_DE_LOGISTICA)
    linhas = []
    for campo in pendentes:
        if campo == "endereco":
            linhas += ["Rua", "Número"] if nada_ainda else ["Rua e número"]
        else:
            linhas.append(_ITEM_DA_LISTA.get(campo, _ROTULOS.get(campo, campo)))
    cabecalho = "Informe:" if nada_ainda else "Faltam essas informações:"
    texto = cabecalho + "\n" + "\n".join(f"- {l}" for l in linhas)
    # O cliente fica sabendo que o CEP basta (Eduardo, 18/10).
    if "bairro" in pendentes or "endereco" in pendentes:
        texto += f"\n\n{DICA_DO_CEP}"
    return texto


def _itens_ambiguos(memoria: dict) -> list[str]:
    """Itens cujo nome serve para o sabor de pacote E para o salgado grande.

    "Enroladinho de Salsicha" e "Rissole de Carne" existem nas duas familias do
    cardapio, e "coxinha" casa tanto com o sabor quanto com a Coxinha de Frango
    com Catupiry. A diferenca de preco e grande (R$ 0,80/un no pacote contra
    R$ 9,00/un avulso), entao o agente confirma em vez de escolher sozinho.

    So vale abaixo de facts.QTD_MINIMA_FESTA: dali para cima a leitura de
    pacote e a unica que faz sentido (ninguem pede 50 coxinhas grandes a
    R$ 9,00 cada) e perguntar seria so atrito. A duvida real e o pedido
    pequeno, onde as duas leituras fazem sentido.
    """
    try:
        import facts
    except Exception:
        return []

    avisos = []
    for item in memoria.get("items") or []:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("name", ""))
        try:
            qtd = int(float(item.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            continue
        if qtd >= facts.QTD_MINIMA_FESTA:
            continue
        if item.get("tamanho"):
            continue          # o cliente ja respondeu: nao pergunte de novo
        try:
            oficial = facts.resolve_unit_price(nome, qtd)
        except Exception:
            continue
        if oficial and oficial.get("ambiguo"):
            avisos.append(
                f"{qtd} x '{nome}': pode ser {oficial['item_oficial']} "
                f"(R$ {oficial['preco_unitario']:.2f}/un) ou "
                f"{oficial['alternativa']}"
            )
    return avisos


def _itens_ambiguos_detalhe(memoria: dict) -> list[dict]:
    """[{qtd, sabor}] dos itens sem tamanho definido, para montar a pergunta."""
    saida = []
    for item in memoria.get("items") or []:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("name", ""))
        try:
            qtd = int(float(item.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            continue
        if qtd <= 0 or qtd >= _minimo_de_festa():
            continue
        if item.get("tamanho"):
            continue          # respondido: fora da pergunta
        # Doce e bebida nao tem tamanho de salgado (14/10: "Essas 1 porcao de
        # 25 brigadeiros sao grande ou mini?").
        if _re.search(r"brigad|doce|bolo|pudim|alfajor|beijinho|refri|suco|coca|guarana",
                      _sem_acento(nome)):
            continue
        try:
            import facts
            oficial = facts.resolve_unit_price(nome, qtd)
        except Exception:
            continue
        if oficial and oficial.get("ambiguo"):
            saida.append({"qtd": qtd, "sabor": nome})
    return saida


# Plural de quem compra, nao de catalogo: "10 coxinhas", "3 rissoles de carne".
_PLURAL_IRREGULAR = {"pastel": "pastéis", "pao": "pães", "pão": "pães"}
_FEMININOS = ("coxinha", "empada", "esfirra", "bolinha", "saltenha", "panqueca",
              "bomba", "quiche", "torta", "pizza")


def _plural_do_item(nome: str, qtd: int) -> str:
    palavras = (nome or "").lower().split()
    if not palavras or qtd == 1:
        return " ".join(palavras)
    w = palavras[0]
    if w in _PLURAL_IRREGULAR:
        palavras[0] = _PLURAL_IRREGULAR[w]
    elif w.endswith(("s", "x")):
        pass
    elif w.endswith(("r", "z")):
        palavras[0] = w + "es"
    elif w.endswith("l"):
        palavras[0] = w[:-1] + "is"
    else:
        palavras[0] = w + "s"
    return " ".join(palavras)


def pergunta_de_tamanho(detalhe: list[dict]) -> str:
    """A pergunta do dono (01/10): "Essas 10 coxinhas são grande (lanche) ou
    mini (festa)? 😊". Antes: "Essas 10 Coxinha seriam tamanho grande...".
    """
    if not detalhe:
        return ""
    feminino = all(_sem_acento(a["sabor"]).split()[0] in _FEMININOS
                   for a in detalhe if _sem_acento(a["sabor"]).split())
    uma = len(detalhe) == 1 and int(detalhe[0]["qtd"]) == 1
    lista = " e ".join(f"{a['qtd']} {_plural_do_item(a['sabor'], int(a['qtd']))}"
                       for a in detalhe)
    if uma:
        return f"{'Essa' if feminino else 'Esse'} {lista} é grande (lanche) ou mini (festa)? 😊"
    return f"{'Essas' if feminino else 'Esses'} {lista} são grande (lanche) ou mini (festa)? 😊"


def pendencia_de_item(memoria: dict) -> str:
    """Duvida sobre O QUE o cliente pediu. Tem prioridade sobre o funil.

    Confirmar o produto depois de ja ter coletado endereco e horario obriga a
    refazer a conta do pedido inteiro, entao esta pergunta fura a fila.
    """
    ambiguos = _itens_ambiguos(memoria)
    if not ambiguos:
        return ""

    # A frase e do dono (25/09): "1 a 24 un sem tamanho especificado: isole
    # apenas esse item e pergunte 'Essas [Qtd] [Sabor] seriam tamanho grande
    # (lanche) ou mini (festa)?'". Antes a Sandrinha citava os nomes de
    # catalogo ("Coxinha de Frango com Catupiry ou Coxinha frango"), que nao
    # dizem nada para quem esta comprando.
    detalhe = _itens_ambiguos_detalhe(memoria)
    if len(detalhe) > 1:
        # Dois itens sem tamanho viravam duas mensagens seguidas, cada uma com
        # a mesma pergunta: "Essas 10 Coxinha...?" e depois "Esses 10 Rissole
        # de Carne...?". O dono chamou isso de triagem picada e pediu uma fala
        # so (26/09 19h24). A pergunta e uma, e responde pelos dois.
        exemplos = f"'{pergunta_de_tamanho(detalhe)}'"
        consolida = (
            f" SAO {len(detalhe)} ITENS SEM TAMANHO: pergunte pelos "
            f"{len(detalhe)} NA MESMA MENSAGEM, numa frase so. E PROIBIDO "
            f"mandar uma pergunta de tamanho por item."
        )
    else:
        exemplos = pergunta_de_tamanho(detalhe)
        consolida = " ISOLE SO ESSE ITEM."
    return (
        "TAMANHO NAO INFORMADO - " + "; ".join(ambiguos)
        + ". Pergunte AGORA, nesta forma: "
        + (exemplos or "'... são grande (lanche) ou mini (festa)? 😊'")
        + consolida
        + " NAO explique a regra dos 25 nem cite nome de catalogo: a pergunta e "
        "so grande (lanche) ou mini (festa). Se ele responder LANCHE ou GRANDE, "
        "aceite a quantidade que ele pediu. Se responder MINI ou FESTA, avise "
        "com gentileza que o mini fecha em 25 por sabor. E PROIBIDO transferir "
        "para humano por causa disto."
    )


def pendencia_de_sabor(memoria: dict) -> str:
    """Quantidade pedida com uma palavra que serve a varios sabores.

    Fura a fila: nao da para ajustar lote, calcular total nem coletar endereco
    de um sabor que ninguem sabe qual e. Antes a palavra virava o produto de
    nome mais comprido e o cliente recebia o pedido errado, ou - depois que
    isso foi barrado - o sabor simplesmente sumia do pedido.
    """
    pendentes = memoria.get("sabores_pendentes") or []
    if not pendentes:
        return ""
    perguntas = []
    for p in pendentes:
        opcoes = " ou ".join(p.get("opcoes") or [])
        perguntas.append(f"os {p.get('qtd')} de {p.get('termo')} ({opcoes})")
    return (
        "SABOR NAO IDENTIFICADO - o cliente disse a quantidade mas nao qual "
        "sabor em: " + "; ".join(perguntas) + ". Pergunte AGORA, numa frase so, "
        "qual sabor ele quer em cada um, OFERECENDO as opcoes entre parenteses. "
        "E PROIBIDO escolher por ele, PROIBIDO deixar esse item de fora do "
        "pedido e PROIBIDO apresentar resumo ou total antes de saber o sabor."
    )


def _volume_novo(memoria: dict, novo: dict, mudancas: list) -> None:
    """O cliente mudou o total: "3 centos" e depois "quero 400".

    merge so preenche lacuna, entao o 300 ficava e os sabores eram divididos
    em 75; e "POCA PEDIR 400 SALGADINHOS" depois do espelho repetia o espelho
    de 300 (teste do dono, 03/10). Sem itens, o volume novo troca o antigo;
    com itens vindos de volume, divide de novo entre os mesmos sabores.
    """
    try:
        volume = int(novo.get("volume_pendente") or 0)
    except (TypeError, ValueError):
        return
    if not volume or novo.get("items") or volume % 25:
        return
    itens = [i for i in (memoria.get("items") or []) if isinstance(i, dict)]
    if not itens:
        if memoria.get("volume_pendente") and int(memoria["volume_pendente"]) != volume:
            memoria["volume_pendente"] = volume
            memoria["divisao_pendente"] = {}
            mudancas.append(f"volume_pendente={volume} (cliente mudou o total)")
        return
    if not memoria.get("pedido_por_volume"):
        return
    acompanhamentos = [i for i in itens if _e_acompanhamento(i.get("name"))]
    salgados = [i for i in itens if i not in acompanhamentos]
    try:
        total = sum(int(float(i.get("quantity") or 0)) for i in salgados)
    except (TypeError, ValueError):
        return
    if not salgados or total == volume:
        return
    nomes = [str(i.get("name")) for i in salgados]
    if (volume // 25) % len(nomes) == 0:
        memoria["items"] = _itens_da_divisao(volume, nomes) + acompanhamentos
        mudancas.append(f"volume {total} -> {volume}: dividido de novo entre {nomes}")
    else:
        memoria["divisao_pendente"] = {"volume": volume, "sabores": nomes}
        mudancas.append(f"volume {total} -> {volume}: divisao pendente em {nomes}")


# Cliente que deixa a escolha com ela: "pode escolher por mim", "aleatorio".
# Excecao do dono (04/10): ai ela escolhe os sabores sozinha.
_RX_ESCOLHE_POR_MIM = _re.compile(
    r"\b(?:pode|podem|voce|vc|vcs|voces)\s+(?:mesm[oa]\s+)?escolh"
    r"|\bescolh\w*\s+(?:voce|vc|por\s+mim|pra\s+mim|para\s+mim)"
    r"|\baleatori|\btanto\s+faz\b|\bqualquer\s+sabor|\ba\s+sua\s+escolha\b"
    r"|\bvoce\s+que\s+sabe\b|\bsurpreend")

# Doce nao entra na escolha de salgados.
_FORA_DA_ESCOLHA = ("churros",)


def escolheu_por_mim(texto: str) -> bool:
    return bool(_RX_ESCOLHE_POR_MIM.search(_sem_acento(texto)))


def escolher_sabores(volume: int, preparo: str = "", rng=None) -> list[dict]:
    """Sabores aleatorios para o volume, em porcoes de 25 por sabor.

    So sabor de pacote disponivel no cardapio, do preparo pedido (fritos
    quando o cliente nao disse). As porcoes vao em rodizio numa ordem
    aleatoria: cada sabor fica com multiplo de 25 e a soma e o volume.
    """
    import random
    rng = rng or random
    if not volume or volume % 25:
        return []
    try:
        import facts
        categoria = "assados" if preparo == "assado" else "fritos"
        sabores = [p["item"] for p in facts.get()["produtos"]
                   if facts.normalize(p["categoria"]) == categoria
                   and p.get("preco") is None and p.get("disponivel", True)
                   and not any(f in facts.normalize(p["item"]) for f in _FORA_DA_ESCOLHA)]
    except Exception:
        return []
    if not sabores:
        return []
    sabores = list(dict.fromkeys(sabores))
    rng.shuffle(sabores)
    porcoes = volume // 25
    escolhidos = sabores[:porcoes]
    blocos = {nome: 0 for nome in escolhidos}
    for n in range(porcoes):
        blocos[escolhidos[n % len(escolhidos)]] += 1
    return [i for nome, b in blocos.items()
            for i in _itens_da_divisao(b * 25, [nome])]


def _itens_da_divisao(volume: int, sabores: list[str], extra_para: str = "",
                      so_o_que_fecha: bool = False) -> list[dict]:
    """Os itens do volume dividido em lotes de 25 pelos sabores."""
    lotes = volume // 25
    base, sobra = divmod(lotes, len(sabores))
    itens = []
    for nome in sabores:
        blocos = base + (sobra if (nome == extra_para and not so_o_que_fecha) else 0)
        if blocos <= 0:
            continue
        qtd = blocos * 25
        preco = 0.0
        try:
            import facts
            oficial = facts.resolve_unit_price(nome, qtd)
            if oficial:
                preco = oficial["preco_unitario"]
        except Exception:
            pass
        itens.append({"name": nome, "quantity": qtd, "price": preco})
    return itens


def tem_salgado_na_ficha(memoria: dict) -> bool:
    """Ha salgado no pedido? Combo, bebida e doce nao contam: "3 combos e 1
    cento frito" tinha o combo na ficha e o cento nunca era dividido nos
    sabores (simulador do dono, 15/10)."""
    return any(isinstance(i, dict) and not _re.search(
        r"^combo|refrigerante|guarana|suco|brigadeir|bolo|pudim|alfajor|empada doce",
        _sem_acento(str(i.get("name", "")))) for i in memoria.get("items") or [])


def _soma_de_salgados(memoria: dict) -> int:
    """Salgados da ficha mais os que esperam o sabor ("25 pastel" sem sabor)."""
    total = 0
    for i in memoria.get("items") or []:
        if isinstance(i, dict) and i not in _itens_que_nao_sao_salgado(memoria):
            try:
                total += int(float(i.get("quantity") or 0))
            except (TypeError, ValueError):
                pass
    for p in memoria.get("sabores_pendentes") or []:
        if isinstance(p, dict) and p.get("termo") not in ("refrigerante", "docinho"):
            try:
                total += int(p.get("qtd") or 0)
            except (TypeError, ValueError):
                pass
    return total


def falta_para_o_volume(memoria: dict) -> int:
    """Quantos salgados faltam para o total que o cliente disse. 0 = nada.

    "2 centos fritos" e depois "50 coxinha 25 enroladinho 25 croquete 25
    pastel": 125 de 200, e o pedido seguia sem aviso (teste de 22/10).
    """
    try:
        volume = int(memoria.get("volume_pendente") or 0)
    except (TypeError, ValueError):
        return 0
    if not volume or not tem_salgado_na_ficha(memoria):
        return 0
    soma = _soma_de_salgados(memoria)
    return volume - soma if 0 < soma < volume else 0


def repetir_os_mesmos(memoria: dict) -> bool:
    """"pode repetir os mesmos": completa o que falta com os mesmos sabores,
    em partes iguais. False quando nao fecha em 25 por sabor.

    125... de 200 com "repete os mesmos" repetia a pergunta do que falta
    (teste da Thayline, 23/10).
    """
    falta = falta_para_o_volume(memoria)
    salgados = [i for i in memoria.get("items") or []
                if isinstance(i, dict) and not _e_acompanhamento(i.get("name"))]
    if not falta or not salgados or falta % (25 * len(salgados)):
        return False
    cada = falta // len(salgados)
    for i in salgados:
        i["quantity"] = int(i.get("quantity") or 0) + cada
    return True


def _itens_que_nao_sao_salgado(memoria: dict) -> list[dict]:
    """Combos, bebidas e doces da ficha: a divisao do cento nao os apaga."""
    return [dict(i) for i in memoria.get("items") or []
            if isinstance(i, dict) and _re.search(
                r"^combo|refrigerante|guarana|suco|brigadeir|bolo|pudim|alfajor|empada doce",
                _sem_acento(str(i.get("name", ""))))]


def aplicar_divisao_do_volume(memoria: dict, texto: str) -> list[str]:
    """Volume dito antes ("1 cento") + sabores ditos depois -> itens na ficha.

    Divide por igual quando fecha em lotes de 25. Quando nao fecha (100 em 3
    sabores), guarda a divisao e pergunta para qual sabor vai a porcao a
    mais; a resposta com um sabor ("pode colocar mais pasteis") fecha a
    conta. Era o modelo quem fazia isso, e ele repetia a mesma pergunta depois
    de o cliente responder (teste do dono, 30/09 15h50).
    """
    from agents.conversation import sabores_citados, sabores_do_volume
    pendente = memoria.get("divisao_pendente") or {}
    # sabores_citados recusa mensagem com numero; "cento de coxinha, churros e
    # bolinha pra amanha as 9 horas" tem os sabores do volume na mesma frase.
    citados = sabores_citados(texto) or sabores_do_volume(" ".join((texto or "").lower().split()))
    if pendente:
        memoria["pedido_por_volume"] = True
        sabores = pendente.get("sabores") or []
        volume = int(pendente.get("volume") or 0)
        # "Acrescentar um sabor": sabor novo entra na divisao. Se a conta
        # fecha com ele (100 em 4 sabores), os itens saem; se nao, a pergunta
        # continua com a lista nova.
        novos = [s for s in citados if s not in sabores]
        if novos:
            lista = sabores + novos
            if (volume // 25) % len(lista) == 0:
                memoria["items"] = _itens_que_nao_sao_salgado(memoria) + _itens_da_divisao(volume, lista)
                memoria["divisao_pendente"] = {}
                return [f"divisao com sabor novo: {', '.join(f'{i['quantity']} {i['name']}' for i in memoria['items'])}"]
            memoria["divisao_pendente"] = {"volume": volume, "sabores": lista}
            return [f"divisao pendente com sabor novo: {lista}"]
        escolhido = next((s for s in citados if s in sabores), "")
        so_esses = bool(_re.search(r"\b" + str((volume // 25 // len(sabores)) * 25 * len(sabores)) + r"\b",
                                   texto or "")) if sabores else False
        if not (escolhido or so_esses) or not sabores:
            return []
        memoria["items"] = _itens_que_nao_sao_salgado(memoria) + _itens_da_divisao(volume, sabores, escolhido, so_o_que_fecha=not escolhido)
        memoria["divisao_pendente"] = {}
        return [f"divisao: {', '.join(f'{i['quantity']} {i['name']}' for i in memoria['items'])}"]

    try:
        volume = int(memoria.get("volume_pendente") or 0)
    except (TypeError, ValueError):
        volume = 0
    if not volume or tem_salgado_na_ficha(memoria) or not citados or volume % 25:
        return []
    if len(citados) == 1:
        # "todos coxinha", "quero so coxinha": o volume inteiro num sabor. A
        # divisao exigia 2 sabores, nada entrava na ficha e a pergunta dos
        # sabores voltava em laco (teste do dono, 06/10: 2 centos de coxinha).
        memoria["volume_pendente"] = 0
        memoria["pedido_por_volume"] = True
        memoria["items"] = _itens_que_nao_sao_salgado(memoria) + _itens_da_divisao(volume, citados)
        return [f"volume num sabor so: {volume} {citados[0]}"]
    memoria["volume_pendente"] = 0
    # Os itens vem de um total: um total novo depois os divide de novo.
    memoria["pedido_por_volume"] = True
    if (volume // 25) % len(citados) == 0:
        memoria["items"] = _itens_que_nao_sao_salgado(memoria) + _itens_da_divisao(volume, citados)
        return [f"volume dividido: {', '.join(f'{i['quantity']} {i['name']}' for i in memoria['items'])}"]
    memoria["divisao_pendente"] = {"volume": volume, "sabores": citados}
    return [f"divisao pendente: {volume} em {citados}"]


def pendencia_de_divisao(memoria: dict) -> str:
    """A pergunta da porcao a mais, quando o volume nao divide pelos sabores."""
    pendente = memoria.get("divisao_pendente") or {}
    sabores = pendente.get("sabores") or []
    if not sabores:
        return ""
    # Sobra UMA porcao (cento em 3 sabores): um sabor vai em dobro (Balbino,
    # 15/10). Mais de uma: o texto do dono de 01/10.
    try:
        volume = int(pendente.get("volume") or 0)
    except (TypeError, ValueError):
        volume = 0
    if volume and volume // 25 - len(sabores) == 1:
        return (f"Com {len(sabores)} sabores, fica {50} de um e 25 de cada um dos outros. "
                f"Qual sabor vai em dobro? 😊")
    return (f"Você quer acrescentar um sabor ou aumentar uma porção dos "
            f"{len(sabores)} sabores? 😊")


def _opcao_pela_palavra_propria(pendente: dict, limpo: str) -> str:
    """A opcao citada pela palavra que so ela tem: "misto", "bolinha", "pasteis".

    Ninguem repete "Misto de Queijo e Presunto" inteiro. A palavra tem de ser
    so daquela opcao (nao o termo do cliente, nem palavra comum a outra) e
    tem de ser uma opcao so - "pastel ou rissole" nao escolhe nada.
    """
    citadas = _opcoes_pela_palavra_propria(pendente, limpo)
    return citadas[0] if len(citadas) == 1 else ""


def _opcoes_pela_palavra_propria(pendente: dict, limpo: str) -> list[str]:
    """Todas as opcoes citadas pela palavra que so cada uma tem."""
    opcoes = pendente.get("opcoes") or []
    termo = set(_sem_acento(pendente.get("termo") or "").split())
    palavras = {o: {w for w in _sem_acento(o).split() if len(w) >= 4} - termo
                for o in opcoes}
    citadas = []
    for opcao, suas in palavras.items():
        outras = set().union(*(v for k, v in palavras.items() if k != opcao))
        for w in suas - outras:
            if _re.search(r"\b" + _re.escape(w[:5]), limpo):
                citadas.append(opcao)
                break
    return citadas


def aplicar_escolha_de_sabor(memoria: dict, texto: str) -> list[str]:
    """O cliente respondeu qual sabor era. Vira item na ficha.

    Sem isto a resposta morria no texto: a pendencia continuava, a pergunta
    voltava na mensagem seguinte e o pedido nao andava - foi o que ja
    aconteceu com o aceite de lote (23/09) e com a escolha de tamanho.
    """
    pendentes = memoria.get("sabores_pendentes") or []
    if not pendentes or not texto:
        return []
    limpo = _sem_acento(texto)
    itens = list(memoria.get("items") or [])
    sobraram, mudancas = [], []
    for p in pendentes:
        escolhido = ""
        for opcao in p.get("opcoes") or []:
            if _sem_acento(opcao) in limpo:
                escolhido = opcao
                break
        if not escolhido and p.get("termo") not in ("docinho", "refrigerante"):
            escolhido = _opcao_pela_palavra_propria(p, limpo)
            divididas = ([] if escolhido or _re.search(r"\bou\b", limpo)
                         else _opcoes_pela_palavra_propria(p, limpo))
            try:
                _qtd_div = int(p.get("qtd") or 0)
            except (TypeError, ValueError):
                _qtd_div = 0
            if len(divididas) > 1 and _qtd_div and _qtd_div % (25 * len(divididas)) == 0:
                # "carne e queijo pode ser assim" ao meio cento de pastel: o
                # volume se divide por igual. Ela perguntava o sabor de novo
                # (Thayline, 23/10).
                parte = _qtd_div // len(divididas)
                for opcao in divididas:
                    try:
                        import facts
                        preco = (facts.resolve_unit_price(opcao, parte) or {}).get("preco_unitario", 0.0)
                    except Exception:
                        preco = 0.0
                    ja = next((i for i in itens if isinstance(i, dict)
                               and _sem_acento(str(i.get("name"))) == _sem_acento(opcao)), None)
                    if ja:
                        ja["quantity"] = int(ja.get("quantity") or 0) + parte
                    else:
                        itens.append({"name": opcao, "quantity": parte, "price": preco})
                    mudancas.append(f"{p.get('termo')} -> {parte} x {opcao}")
                continue
        _qtd_doce = 0
        if not escolhido and p.get("termo") == "docinho":
            # "bandeja", "o meio cento", "2 bandejas": o formato pelo nome curto.
            from agents.conversation import formato_do_doce
            _formato, _qtd_doce = formato_do_doce(texto)
            # "50 bolinha e 50 pastel": o 50 e de salgado, nao o meio cento
            # (teste de 22/10 - o docinho pendente virou meio cento sozinho).
            _numero_de_salgado = _re.search(
                r"\b\d{2,3}\s+(?:de\s+)?(?!un\b|unidades?\b|brigad|docinh|doces?\b|beijinh|bandej)[a-z]{4,}",
                limpo)
            if _formato in (p.get("opcoes") or []) and not _numero_de_salgado:
                escolhido = _formato
        if not escolhido and p.get("termo") == "refrigerante":
            # "lata", "a de 600": ninguem repete "Refrigerante Lata" inteiro.
            from agents.conversation import PALAVRAS_DA_BEBIDA
            for opcao in p.get("opcoes") or []:
                if any(_re.search(r"\b" + palavra, limpo)
                       for palavra in PALAVRAS_DA_BEBIDA.get(opcao, ())):
                    escolhido = opcao
                    break
        if not escolhido:
            sobraram.append(p)
            continue
        try:
            qtd = int(p.get("qtd") or 0)
        except (TypeError, ValueError):
            qtd = 0
        if _qtd_doce > 1:
            qtd = _qtd_doce
        if qtd <= 0:
            continue
        preco = 0.0
        try:
            import facts
            oficial = facts.resolve_unit_price(escolhido, qtd)
            if oficial:
                preco = oficial["preco_unitario"]
        except Exception:
            pass
        ja = next((i for i in itens if isinstance(i, dict)
                   and _sem_acento(str(i.get("name"))) == _sem_acento(escolhido)), None)
        if ja:
            ja["quantity"] = int(ja.get("quantity") or 0) + qtd
        else:
            novo_item = {"name": escolhido, "quantity": qtd, "price": preco}
            # "Uma coca" -> "2 litros": a marca dita antes e o sabor do item.
            if p.get("termo") == "refrigerante" and p.get("marca"):
                try:
                    import cardapio_catalogo as _cat
                    _sab = casar_sabor(p["marca"], _cat.opcoes_do_item(escolhido) or [])
                    if _sab:
                        novo_item["sabor"] = _sab
                except Exception:
                    pass
            itens.append(novo_item)
        mudancas.append(f"{p.get('termo')} -> {qtd} x {escolhido}")
    if mudancas:
        memoria["items"] = itens
        memoria["sabores_pendentes"] = sobraram
    return mudancas


# ── Sabor esgotado ──────────────────────────────────────────────────────────
# Categorias que JA SAO o acompanhamento da oferta adicional. Quem pediu
# refrigerante nao precisa ouvir a oferta de refrigerante.
_CATEGORIAS_DE_ACOMPANHAMENTO = ("bebidas", "bolos e doces")


def _categoria_do_item(nome: str) -> str:
    """Categoria oficial do produto na planilha, em minusculo. '' se nao achar."""
    try:
        import facts
        linhas = facts.lookup_preco(nome) or []
    except Exception:
        return ""
    alvo = _sem_acento(nome)
    for linha in linhas:
        if _sem_acento(linha.get("item", "")) == alvo:
            return _sem_acento(linha.get("categoria", ""))
    return _sem_acento(linhas[0].get("categoria", "")) if linhas else ""


def itens_indisponiveis(memoria: dict) -> list[str]:
    """Nomes OFICIAIS dos itens do pedido que a planilha marca como esgotados.

    Nome oficial, e nao o que o cliente escreveu, porque e justamente a troca
    de nome que causou o pior erro do dia: pedido de "croquete" num cento de
    fritos, ela respondeu que "o nome correto do sabor e Croquete de
    Macaxeira, mas ele esta esgotado" - juntou o sabor de lote (que esta
    esgotado) com o salgado grande de balcao (que nao esta) e inventou uma
    falta de estoque que nao existia naquele produto (teste do dono,
    26/09 19h12).
    """
    fora = []
    for item in memoria.get("items") or []:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("name", "")).strip()
        if not nome:
            continue
        try:
            import facts
            linhas = facts.lookup_preco(nome) or []
            if not linhas:
                continue
            alvo = _sem_acento(nome)
            exatos = [l for l in linhas if _sem_acento(l.get("item", "")) == alvo]
            if not exatos:
                continue
            # Item que o cardapio digital nao tem (bloqueado e nao liberado
            # pelo dono) tambem nao pode ser vendido: avisado AGORA, como um
            # esgotado. Descoberto so no envio, ele fazia o cliente passar
            # endereco e pagamento para no fim ir para a fila do humano - e
            # no caminho travava o ajuste de lote num laco (teste do dono,
            # 29/09 15h14, croquete de carne).
            import cardapio_catalogo
            bloqueado = any(_sem_acento(k) == alvo
                            for k in cardapio_catalogo.bloqueados_efetivos())
            if not bloqueado and any(l.get("disponivel", True) for l in exatos):
                continue
            oficial = exatos[0].get("item") or nome
        except Exception:
            continue
        if oficial not in fora:
            fora.append(oficial)
    return fora


def aplicar_troca_do_esgotado(memoria: dict, texto: str) -> list[str]:
    """"pode ser canudo de camarao" com UM item em falta na ficha: troca por ele.

    A resposta a "Por qual sabor voce quer trocar?" vem sem numero e nao virava
    item; a pergunta saiu 6 vezes seguidas (Thayline, 24/10). A quantidade e a
    do item em falta.
    """
    fora = itens_indisponiveis(memoria)
    # Com numero ("mais 50 pao pizza") o item ja entrou pela extracao.
    if len(fora) != 1 or not texto or _re.search(r"\d", texto):
        return []
    try:
        from agents.conversation import _forma_de_busca, _indice_de_produtos
    except Exception:
        return []
    itens = [i for i in memoria.get("items") or [] if isinstance(i, dict)]
    velho = next((i for i in itens if _sem_acento(str(i.get("name"))) == _sem_acento(fora[0])), None)
    if velho is None:
        return []
    t = f" {_forma_de_busca(texto)} "
    achados = [(chave, nome) for chave, nome in _indice_de_produtos().items()
               if f" {chave} " in t and _sem_acento(nome) != _sem_acento(fora[0])]
    if not achados:
        return []
    novo_nome = max(achados, key=lambda c: len(c[0]))[1]
    qtd = int(velho.get("quantity") or 0)
    if itens_indisponiveis({"items": [{"name": novo_nome, "quantity": qtd}]}):
        return []
    try:
        import facts
        oficial = facts.resolve_unit_price(novo_nome, qtd)
    except Exception:
        oficial = None
    igual = next((i for i in itens if i is not velho and _sem_acento(str(i.get("name"))) == _sem_acento(novo_nome)), None)
    if igual:
        igual["quantity"] = int(igual.get("quantity") or 0) + qtd
        itens = [i for i in itens if i is not velho]
    else:
        velho.clear()
        velho.update({"name": novo_nome, "quantity": qtd,
                      "price": oficial["preco_unitario"] if oficial else 0.0})
    memoria["items"] = itens
    return [f"{fora[0]} em falta -> {qtd} x {novo_nome}"]


def pendencia_de_indisponivel(memoria: dict) -> str:
    """Sabor pedido que esta ESGOTADO na planilha. Fura a fila do funil."""
    fora = itens_indisponiveis(memoria)
    if not fora:
        return ""
    lista = ", ".join(fora)
    return (
        f"SABOR ESGOTADO - {lista}. Diga AGORA, com o nome EXATO que esta "
        f"escrito aqui, que esse sabor esta em falta hoje, e pergunte por qual "
        f"sabor o cliente quer trocar. E PROIBIDO renomear o item ('o nome "
        f"correto seria...'), PROIBIDO oferecer outro produto como se fosse o "
        f"mesmo, e PROIBIDO dizer que esta esgotado um item que nao esta nesta "
        f"lista. Nao apresente resumo nem total enquanto a troca nao for feita."
    )


# ── Oferta adicional (BLOCO 2) ──────────────────────────────────────────────
# Faixa de horario -> frase exata do dono. Fora das faixas vale a ultima.
_OFERTAS_POR_HORARIO = (
    ((7, 40), (10, 0), "Pra acompanhar, vai um Bolo de Milho quentinho? 🌽"),
    ((10, 1), (14, 0), "Pra acompanhar, que tal um Refrigerante 2L bem gelado? 🥤"),
    ((14, 1), (17, 0), "Pro lanche da tarde, vai um Bolo de Milho ou um Guaraná "
                       "Tuchaua 2L? 🥤"),
    ((17, 1), (20, 0), "Pra acompanhar, vai um Refrigerante 2L ou docinhos "
                       "(Brigadeiro/Beijinho)? 🥤🍬"),
)


def _frases_de_oferta() -> list[str]:
    """Uma frase por faixa: a do dono (painel) onde houver, senao a padrao."""
    try:
        import config_dono
        do_dono = config_dono.ofertas()
    except Exception as e:
        print(f"[oferta] frases do painel ignoradas: {e}")
        do_dono = []
    padrao = [frase for _ini, _fim, frase in _OFERTAS_POR_HORARIO]
    return [(do_dono[i] if i < len(do_dono) and do_dono[i] else frase)
            for i, frase in enumerate(padrao)]


def oferta_adicional(agora=None) -> str:
    """A frase de upsell do BLOCO 2 para a hora em que estamos."""
    frases = _frases_de_oferta()
    try:
        import horario
        agora = agora or horario.agora()
        minuto = agora.hour * 60 + agora.minute
    except Exception:
        return frases[-1]
    atual = len(frases) - 1
    for i, ((h1, m1), (h2, m2), _padrao) in enumerate(_OFERTAS_POR_HORARIO):
        if h1 * 60 + m1 <= minuto <= h2 * 60 + m2:
            atual = i
            break
    # Produto da frase esgotado no cardapio: "Bolo de Milho quentinho?" com o
    # bolo em falta, o "sim" do cliente nao virava nada (teste do dono, 10/10
    # 08h40). Vale a frase de outra faixa com produto disponivel.
    for i in [atual] + [j for j in range(len(frases)) if j != atual]:
        if not _oferta_esgotada(frases[i]):
            return frases[i]
    return frases[atual]


def _oferta_esgotada(frase: str) -> bool:
    """A frase oferece um produto que esta indisponivel no cardapio hoje?"""
    try:
        import facts
        texto = _sem_acento(frase or "")
        return any(not p.get("disponivel", True) and len(_sem_acento(p["item"])) >= 6
                   and _sem_acento(p["item"]) in texto
                   for p in facts.get()["produtos"])
    except Exception:
        return False


def e_a_oferta_configurada(reply: str, agora=None) -> bool:
    """A resposta traz a frase de oferta que vale agora (inclusive a do dono)?

    As checagens de "ja ofereceu" procuram palavras fixas (refrigerante, bolo de
    milho...). Uma frase nova do dono sem nenhuma delas nunca seria reconhecida
    e a oferta se repetiria em toda mensagem. O inicio da frase basta: o modelo
    as vezes troca o emoji ou a pontuacao do fim.

    Vale a frase de QUALQUER faixa, nao so a de agora: a oferta feita as 13h58
    tem de continuar reconhecida as 14h01, quando a faixa ja virou. (`agora`
    fica na assinatura por compatibilidade.)
    """
    texto = _sem_acento(reply or "")
    for frase in _frases_de_oferta():
        inicio = " ".join(_sem_acento(frase).split()[:5])
        if inicio and inicio in texto:
            return True
    return False


def upsell_pendente(memoria: dict, agora=None) -> str:
    """A oferta adicional ainda tem de sair antes de o funil andar?

    O BLOCO 1 fixa o funil em Produto/Tamanho -> Upsell -> Endereco/Retirada ->
    Resumo, e o upsell era a etapa que sumia. Aqui ele deixa de depender da boa
    vontade do modelo: enquanto a frase nao sair, a ficha nao libera a coleta
    de logistica nem o espelho.

    Nao sai quando: nao ha item nenhum ainda, o pedido ja ESTA no acompanhamento
    (refrigerante, bolo, docinho), ou ainda ha duvida sobre o proprio pedido -
    tamanho, lote quebrado ou sabor esgotado vem antes.
    """
    if memoria.get("upsell_feito"):
        return ""
    itens = memoria.get("items") or []
    if not itens:
        return ""
    if pendencia_de_item(memoria) or pendencia_de_lote(memoria):
        return ""
    if pendencia_de_indisponivel(memoria) or pendencia_de_sabor(memoria):
        return ""
    for item in itens:
        if not isinstance(item, dict):
            continue
        if _categoria_do_item(str(item.get("name", ""))) in _CATEGORIAS_DE_ACOMPANHAMENTO:
            return ""          # ja tem bebida/doce no pedido
    return oferta_adicional(agora)


def subtotal_dos_salgados(memoria: dict) -> float:
    """Soma dos itens da ficha pelo preco da planilha. 0 se algum nao tem preco."""
    total = 0.0
    for item in memoria.get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            import facts
            qtd = int(item.get("quantity") or 0)
            oficial = facts.resolve_unit_price(str(item.get("name", "")), qtd)
        except Exception:
            return 0.0
        if not oficial or not qtd:
            return 0.0
        total += qtd * float(oficial["preco_unitario"])
    return round(total, 2)


def total_previsto(memoria: dict) -> float:
    """Total do pedido da ficha, antes do espelho. 0 quando falta alguma parte.

    Mesma conta do calculate_order: itens pelo preco do cardapio, frete da
    tabela oficial e a taxa de retorno. Existe para a pergunta do troco: ela
    vinha antes do total, e o cliente nao tinha como saber para quanto pedir
    troco (teste do dono, 07/10 14h30: "o cliente nao sabe qual valor").
    """
    subtotal = subtotal_dos_salgados(memoria)
    if not subtotal:
        return 0.0
    frete = 0.0
    modalidade = memoria.get("modality", "")
    if modalidade == "entrega":
        try:
            import facts
            achado = facts.lookup_frete(memoria.get("bairro", ""))
        except Exception:
            achado = None
        if not achado:
            return 0.0           # sem frete oficial nao ha total para mostrar
        frete = float(achado["taxa"])
    try:
        import cardapio
        retorno = cardapio.calcular_taxa_retorno(modalidade, memoria.get("payment_method", ""))
    except Exception:
        return 0.0
    return round(subtotal + frete + retorno, 2)


def pergunta_do_troco(memoria: dict) -> str:
    """"Precisa de troco para quanto?" com o total na frente, quando ja da para somar."""
    # A conta de agora primeiro: o "total" da ficha pode ser de antes de o
    # cliente mudar os itens.
    total = total_previsto(memoria) or float(memoria.get("total") or 0)
    if total > 0:
        # Texto exato do dono (07/10).
        return f"O seu pedido com a entrega deu {_brl(total)}. Precisa de troco para quanto? 💵"
    return _PERGUNTA_CURTA["troco_para"]


def oferta_com_valor(memoria: dict, agora=None) -> str:
    """A oferta adicional, com o valor na frente quando o pedido veio por volume.

    O balcao responde "metade croquete e metade pastel" com "Ok. 80,00" - o
    dono pediu calculo imediato para esses pedidos, sem textao (28/09). Nos
    demais a oferta sai como sempre.
    """
    oferta = upsell_pendente(memoria, agora)
    if not oferta or not memoria.get("pedido_por_volume"):
        return oferta
    subtotal = subtotal_dos_salgados(memoria)
    if not subtotal:
        return oferta
    return f"Fica {_brl(subtotal)} nos salgados! {oferta}"


def pendencia_de_lote(memoria: dict) -> str:
    """Sabor de lote com quantidade que nao fecha em multiplo de 25.

    A trava ja existia, mas so no calculate_order — la no fim. O cliente pedia
    30 coxinhas, a Sandrinha seguia coletando endereco, horario e pagamento, e
    so entao descobria que 30 nao fecha lote (teste do dono, 22/09). Aqui a
    conta e a mesma (cardapio_catalogo.traduzir), so que lida a cada mensagem,
    antes de o funil andar.
    """
    items = memoria.get("items") or []
    if not items:
        return ""
    try:
        import cardapio
        import cardapio_catalogo
        norm, _avisos, nao_encontrados = cardapio._normalizar_itens(
            [dict(i) for i in items]
        )
        if nao_encontrados:
            return ""      # item fora do cardapio tem tratamento proprio
        _saida, bloqueados = cardapio_catalogo.traduzir([dict(i) for i in norm])
    except Exception as e:
        print(f"[memoria] WARN pendencia_de_lote: {e}")
        return ""

    quebrados = [b for b in bloqueados
                 if "multiplos de" in b or "exige no minimo" in b]
    if not quebrados:
        return ""

    # A pergunta precisa dizer DE QUAL SABOR se trata. O cliente pediu 30
    # coxinhas e 20 quibes e ouviu "quer 25 ou 50?", sem saber a qual dos dois
    # a Sandrinha se referia (teste do dono, 23/09).
    try:
        quebras = cardapio_catalogo.quebras_de_lote([dict(i) for i in norm])
    except Exception as e:
        print(f"[memoria] WARN quebras_de_lote: {e}")
        quebras = []

    if quebras:
        detalhe = "; ".join(
            f"{q['sabor']}: {q['qtd']} un -> ofereca {q['sugerida']} un"
            for q in quebras
        )
        exemplo = " e ".join(f"{q['sugerida']} {q['sabor']}" for q in quebras)
        return (
            f"QUANTIDADE QUEBRADA - {detalhe}. Pergunte AGORA, antes de seguir o "
            f"funil, CITANDO CADA SABOR pelo nome: 'Fazemos de 25 em 25 por "
            f"sabor! Ajusto para {exemplo}?'. E PROIBIDO perguntar 'quer 25 ou "
            f"50?' sem dizer de qual sabor voce esta falando. NAO apresente "
            f"resumo nem total enquanto a quantidade nao fechar em multiplo de 25."
        )

    return (
        "QUANTIDADE QUEBRADA - " + "; ".join(quebrados) + ". Pergunte AGORA, "
        "antes de seguir o funil, CITANDO O SABOR de que voce esta falando. "
        "NAO apresente resumo nem total enquanto a quantidade nao fechar em "
        "multiplo de 25."
    )


def quantidade_livre_do_lote(texto: str) -> int:
    """"100", "100 unidades": numero solto multiplo de 25 (0 se nao for)."""
    m = _re.fullmatch(r"\s*(?:sao\s+|quero\s+|pode\s+ser\s+)?(\d{2,4})\s*(?:un\w*)?\s*[.!]?\s*",
                      _sem_acento(texto or ""))
    n = int(m.group(1)) if m else 0
    return n if n >= 25 and n % 25 == 0 else 0


def aplicar_ajuste_de_lote(memoria: dict, texto: str = "") -> list[str]:
    """Grava na ficha o ajuste de quantidade que o cliente acabou de aceitar.

    Sem isto o aceite morria na conversa. A Sandrinha oferecia "Ajusto para 25
    quibes?", o cliente dizia "pode ser", e a ficha continuava com 5: a trava
    de lote apontava a mesma quebra na mensagem seguinte, o funil nao andava e
    o modelo acabava transferindo para um humano um pedido que o cliente ja
    tinha ajustado (teste do dono, 23/09 15:41). A quantidade e do codigo, nao
    do texto do modelo.

    Devolve a lista de mudancas, para o log.
    """
    items = memoria.get("items") or []
    if not items:
        return []
    try:
        import cardapio
        import cardapio_catalogo
        # O nome tem de passar pela tabela antes: a ficha guarda "Coxinha" e a
        # planilha diz "Coxinha frango" - sem normalizar, nenhum item casa.
        norm, _avisos, nao_encontrados = cardapio._normalizar_itens(
            [dict(i) for i in items]
        )
        if nao_encontrados or len(norm) != len(items):
            return []          # sem correspondencia 1 para 1 nao da para aplicar
        quebras = cardapio_catalogo.quebras_de_lote([dict(i) for i in norm])
    except Exception as e:
        print(f"[memoria] WARN aplicar_ajuste_de_lote: {e}")
        return []
    if not quebras:
        return []

    por_indice = {q["indice"]: q for q in quebras}
    # Qual das duas opcoes o cliente escolheu ("pode ser 25", "50 esfirras").
    # Sem numero ("sim"), vale a mais proxima.
    numeros = [int(n) for n in _re.findall(r"\d+", texto or "")]
    escolha = {}
    for q in quebras:
        opcoes = [n for n in opcoes_do_lote(q) if n]
        if len(quebras) == 1:
            achou = [n for n in numeros if n in opcoes]
        else:
            # Qualquer palavra do sabor: "25 salsicha" e o Enroladinho de Salsicha.
            palavras = [w[:5] for w in _sem_acento(str(q.get("sabor") or "")).split() if len(w) >= 5]
            achou = [int(m.group(1)) for w in palavras for m in _re.finditer(
                r"(\d+)\s*(?:un\w*\s+)?(?:de\s+)?" + _re.escape(w), _sem_acento(texto or ""))
                if int(m.group(1)) in opcoes]
        if achou:
            escolha[q["indice"]] = achou[0]
        elif len(quebras) == 1 and quantidade_livre_do_lote(texto):
            escolha[q["indice"]] = quantidade_livre_do_lote(texto)

    novos, mudancas = [], []
    for i, bruto in enumerate(items):
        it = dict(bruto)
        q = por_indice.get(i)
        alvo = escolha.get(i, q["sugerida"]) if q else None
        if q and int(it.get("quantity") or 0) != alvo:
            mudancas.append(f"{it.get('name')} {it.get('quantity')} -> {alvo} un")
            it["quantity"] = alvo
            q = dict(q, sugerida=alvo)
            # O preco tem de ser relido: abaixo de 25 o mesmo nome resolvia
            # para o salgado grande avulso (R$ 9,00), e na quantidade de
            # pacote ele vale R$ 0,80.
            try:
                import facts
                oficial = facts.resolve_unit_price(it.get("name", ""), q["sugerida"])
                it["price"] = oficial["preco_unitario"] if oficial else 0.0
            except Exception:
                it["price"] = 0.0
        novos.append(it)

    if mudancas:
        memoria["items"] = novos
    return mudancas


# Tamanho minimo de um lote de festa. Abaixo disso o cardapio digital nao
# aceita, entao quem escolhe o pacote esta escolhendo, no minimo, uma porcao.
_PORCAO = 25


def aplicar_escolha_de_lanche(memoria: dict) -> list[str]:
    """Grava que o cliente quer o salgado GRANDE, e nao o sabor de pacote.

    Irma de aplicar_escolha_de_pacote, e existia so metade: havia tratamento
    para quem escolhia o pacote, e nenhum para quem escolhia o lanche. O cliente
    respondia "Grande, lanche", nada entrava na ficha, a pendencia de tamanho
    continuava de pe e o espelho nao saia - ela encerrava com "posso ajudar com
    mais alguma coisa?" e o pedido de R$ 45,00 morria ali (medido em 1 de 3
    rodadas, 26/09).

    A regra e do dono: LANCHE aceita a quantidade pedida. Entao a quantidade NAO
    muda - so se registra o tamanho, e o preco volta para a tabela resolver (a
    mesma quantidade abaixo de 25 resolve como salgado grande).

    Devolve a lista de mudancas, para o log.
    """
    itens = memoria.get("items") or []
    if not itens:
        return []
    try:
        import facts
    except Exception:
        return []

    minimo = _minimo_de_festa()
    novos, mudancas = [], []
    for bruto in itens:
        it = dict(bruto)
        try:
            qtd = int(float(it.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            novos.append(it)
            continue
        if qtd >= minimo or it.get("tamanho"):
            novos.append(it)
            continue
        try:
            oficial = facts.resolve_unit_price(it.get("name", ""), qtd)
        except Exception:
            oficial = None
        if oficial and oficial.get("ambiguo"):
            it["tamanho"] = "lanche"
            it["price"] = 0.0     # quem precifica e a tabela, nunca este codigo
            mudancas.append(f"{it.get('name')} {qtd} un -> lanche (grande)")
        novos.append(it)

    if mudancas:
        memoria["items"] = novos
    return mudancas


def aplicar_escolha_de_pacote(memoria: dict) -> list[str]:
    """Grava a escolha entre sabor de pacote e salgado grande de balcao.

    A duvida era levantada (pendencia_de_item), o cliente respondia "pode ser a
    do pacote mesmo" — e a resposta nao ia para lugar nenhum. A ficha seguia
    com 10 unidades a R$ 9,00, a trava de lote apontava a mesma quebra na
    mensagem seguinte e o modelo acabava transferindo para um humano um pedido
    ja resolvido. A trava nao tinha saida, igual ao caso do ajuste de
    quantidade (ver aplicar_ajuste_de_lote).

    Escolher o pacote sobe a quantidade para uma porcao fechada e devolve o
    preco para a tabela decidir: abaixo de 25 o mesmo nome resolve como salgado
    grande (R$ 9,00/un); na quantidade de pacote vale R$ 0,80.

    Devolve a lista de mudancas, para o log.
    """
    itens = memoria.get("items") or []
    if not itens:
        return []
    try:
        import facts
    except Exception:
        return []

    minimo = _minimo_de_festa()
    novos, mudancas = [], []
    for bruto in itens:
        it = dict(bruto)
        try:
            qtd = int(float(it.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            novos.append(it)
            continue
        if qtd >= minimo:
            novos.append(it)          # ja e quantidade de festa: nada a decidir
            continue
        try:
            oficial = facts.resolve_unit_price(it.get("name", ""), qtd)
        except Exception:
            oficial = None
        if oficial and oficial.get("ambiguo"):
            mudancas.append(f"{it.get('name')} {qtd} -> {_PORCAO} un (pacote)")
            it["quantity"] = _PORCAO
            it["price"] = 0.0
        novos.append(it)

    if mudancas:
        memoria["items"] = novos
    return mudancas


def pendencia_de_detalhe(memoria: dict) -> str:
    """Campo preenchido que ainda precisa de um detalhe, nao de nova coleta."""
    # Nao ha mais pendencia de debito/credito: "cartao" e forma de pagamento
    # completa desde 23/09 (os dois pagam a mesma taxa de retorno).

    # O troco entra aqui, e nao em faltando(), porque depende do total: exigi-lo
    # antes travaria o calculate_order, que e justamente quem calcula o total.
    # Na retirada o pagamento e no balcao e o prompt PROIBE perguntar, entao
    # nao existe troco a cobrar.
    if memoria.get("payment_method") == "dinheiro" and memoria.get("modality") != "retirada":
        total = float(memoria.get("total") or 0)
        troco = memoria.get("troco_para", "")
        if total > 0 and not troco:
            return (
                f"Pagamento em dinheiro e o total ja esta calculado "
                f"({_brl(total)}). Pergunte exatamente: '{pergunta_do_troco(memoria)}' "
                "antes de fechar o pedido. Se o cliente perguntar o total, informe-o."
            )
        try:
            valor = float(troco)
        except (TypeError, ValueError):
            valor = None
        if valor is not None and total > 0 and valor <= total:
            return (
                f"O cliente pediu troco para {_brl(valor)}, mas o total e "
                f"{_brl(total)}. Diga exatamente: 'O total do seu pedido deu "
                f"{_brl(total)}. O valor para troco precisa ser maior que o "
                "total da compra. Dinheiro para quanto? 💵'. NAO envie o pedido "
                "antes de o cliente corrigir."
            )
    return ""


def _formatar_itens(items) -> str:
    if not items:
        return ""
    partes = []
    for item in items:
        if isinstance(item, dict):
            qtd = item.get("quantity", "")
            nome = item.get("name", "")
            if str(item.get("sabor") or "").strip():
                nome = f"{nome} ({item['sabor']})"
            partes.append(f"{qtd} x {nome}".strip())
        else:
            partes.append(str(item))
    return "; ".join(p for p in partes if p)


def _minimo_de_festa() -> int:
    """A partir de quantas unidades o pedido e sempre festa (facts manda)."""
    try:
        import facts
        return facts.QTD_MINIMA_FESTA
    except Exception:
        return 21


def tamanho_ja_definido(memoria: dict) -> bool:
    """Nao ha duvida de tamanho a resolver: todo item e quantidade de festa.

    Um item abaixo do minimo derruba a resposta inteira de proposito. Ali a
    pergunta "festa ou grande de balcao?" e legitima - e a trava que evitou os
    R$ 450,00 em coxinha de balcao (22/09).

    Quem chama: a diretiva da ficha, que PEDE ao modelo para nao perguntar, e
    app._conferir_tamanho, que TIRA a pergunta quando ele pergunta assim mesmo.
    A regra e uma so, escrita num lugar so.
    """
    itens = memoria.get("items") or []
    minimo = _minimo_de_festa()
    qtds = []
    for it in itens:
        if not isinstance(it, dict):
            continue
        try:
            qtds.append(int(float(it.get("quantity", 0) or 0)))
        except (ValueError, TypeError):
            return False       # quantidade ilegivel: nao afirme que esta definido
    return bool(qtds) and all(q >= minimo for q in qtds)


def _frase_de_lote(memoria: dict) -> str:
    """'Fazemos de 25 em 25 por sabor! Ajusto para X e Y?' — em codigo."""
    try:
        import cardapio
        import cardapio_catalogo
        norm, _avisos, fora = cardapio._normalizar_itens(
            [dict(i) for i in (memoria.get("items") or [])]
        )
        base = norm if (norm and not fora) else (memoria.get("items") or [])
        quebras = cardapio_catalogo.quebras_de_lote([dict(i) for i in base])
    except Exception:
        quebras = []
    if not quebras:
        return ""
    # As duas quantidades mais proximas, para baixo e para cima (pedido do
    # dono, 07/10: "40 sugere 25 ou 50"). Abaixo de 25 so ha uma.
    partes = []
    for q in quebras:
        baixo, cima = opcoes_do_lote(q)
        partes.append(f"{q['qtd']} {q['sabor']}: {baixo} ou {cima}" if baixo
                      else f"{q['qtd']} {q['sabor']}: {cima}")
    if len(partes) == 1 and not opcoes_do_lote(quebras[0])[0]:
        q = quebras[0]
        return f"Fazemos de 25 em 25 por sabor! Ajusto para {opcoes_do_lote(q)[1]} {q['sabor']}? 😊"
    if len(partes) == 1:
        q = quebras[0]
        baixo, cima = opcoes_do_lote(q)
        return (f"Fazemos de 25 em 25 por sabor! Para {q['sabor']}, "
                f"fica {baixo} ou {cima}? 😊")
    return ("Fazemos de 25 em 25 por sabor! Como fica?\n"
            + "\n".join(f"• {p}" for p in partes) + " 😊")


def opcoes_do_lote(quebra: dict) -> tuple[int, int]:
    """(para baixo, para cima) no passo do pacote. Para baixo e 0 abaixo de 25."""
    passo = int(quebra.get("passo") or 25)
    try:
        qtd = int(quebra.get("qtd") or 0)
    except (TypeError, ValueError):
        qtd = 0
    if not qtd:
        return 0, int(quebra.get("sugerida") or passo)
    baixo = (qtd // passo) * passo
    return baixo, baixo + passo


def etapa_coberta(memoria: dict, reply: str, agora=None) -> bool:
    """A resposta JA cumpriu a etapa travada do funil?

    Nao da para decidir isso comparando o texto com a frase que o codigo
    escreveria: o modelo diz a mesma coisa com outras palavras, e ele tem o
    direito de dizer. O que conta e a SUBSTANCIA de cada etapa - o tamanho
    oferecido nas duas opcoes, o sabor perguntado, o esgotado nomeado, cada
    quantidade quebrada com o multiplo sugerido, a oferta adicional feita.

    Sem esta funcao a checagem era "os 28 primeiros caracteres da minha frase
    aparecem na resposta?", e a frase de lote tem DUAS sentencas: a resposta
    certa ("Fazemos de 25 em 25 por sabor! Ajusto para 25 coxinha, 25 quibe e
    50 bolinha de queijo?") batia so na primeira e perdia a segunda - os
    sabores eram cortados da mensagem que o cliente recebia.
    """
    if not reply:
        return False
    texto = _sem_acento(reply)

    detalhe = _itens_ambiguos_detalhe(memoria)
    if detalhe:
        if not ("lanche" in texto and ("mini" in texto or "festa" in texto)):
            return False
        # A frase e a do dono, com "sao"/"e" (01/10): "seriam tamanho grande"
        # e "Grande ou lanche ou mini festa" nao cobrem - a do codigo entra.
        if not _re.search(r"\b(?:sao|e) grande lanche ou mini festa\b", texto):
            return False
        return all(_sem_acento(a["sabor"]).split()[0] in texto for a in detalhe)

    # Mesma ordem de pergunta_bloqueante: o sabor em falta antes do resto.
    # "Disponivel" junto do nome do item e o contrario do aviso: nao cobre.
    fora = itens_indisponiveis(memoria)
    if fora:
        if not ("esgotad" in texto or "em falta" in texto or "indisponivel" in texto):
            return False
        if "macaxeira" in texto and not any("macaxeira" in _sem_acento(n) for n in fora):
            return False
        return all(_sem_acento(n).split()[0] in texto for n in fora)

    # Divisao do volume: a pergunta oferece as duas saidas do dono.
    divisao = (memoria.get("divisao_pendente") or {}).get("sabores") or []
    if divisao:
        return "?" in reply and "acrescentar" in texto and "aumentar" in texto

    volume, _extra = _frase_do_volume(memoria)
    if volume:
        quantos = _re.search(r"ate (\d+) sabor", _sem_acento(volume))
        return ("?" in reply and "sabor" in texto and bool(quantos)
                and f"ate {quantos.group(1)} sabor" in texto)

    pendentes = memoria.get("sabores_pendentes") or []
    _nome_beb, _opcoes_beb = sabor_de_bebida_pendente(memoria)
    if _nome_beb and not pendentes:
        return ("sabor" in texto
                and all(_sem_acento(o).split()[0] in texto for o in _opcoes_beb))
    if pendentes:
        # Bebida esgotada: perguntar "qual sabor na Coca 2L?" sem dizer que o
        # 2L acabou deixa o cliente achando que ainda da (teste de 28/09).
        if any(p.get("em_falta") for p in pendentes) and not (
                "falta" in texto or "esgotad" in texto):
            return False
        # E as opcoes oferecidas tem de ser as que existem: "Tuchaua ou
        # Refrigerante 2 Litros (esgotado)" oferecia o esgotado e escondia a
        # lata e o 600ml (teste do Eduardo, 28/09 21h38).
        for p in pendentes:
            if p.get("termo") == "docinho":
                if "qual docinho voce prefere" not in texto:
                    return False
                if p.get("em_falta") and not ("falta" in texto or "esgotad" in texto):
                    return False
                continue
            if p.get("termo") != "refrigerante":
                continue
            # Frase fixa: "Qual sabor de refrigerante voce prefere: Guarana
            # Tuchaua ou 600ml ou Lata?" chamava TAMANHO de sabor - o dono
            # leu como se as opcoes do cardapio nao existissem (01/10).
            if "qual refrigerante voce prefere" not in texto and "qual tamanho voce prefere" not in texto:
                return False
            from agents.conversation import PALAVRAS_DA_BEBIDA
            for opcao in p.get("opcoes") or []:
                palavras = PALAVRAS_DA_BEBIDA.get(opcao, ())
                if palavras and not any(_re.search(w, texto) for w in palavras):
                    return False
        return all(_sem_acento(str(p.get("termo"))) in texto for p in pendentes)

    # Soma dos sabores abaixo do total dito: a resposta tem de dizer quanto
    # falta (22/10).
    falta = falta_para_o_volume(memoria)
    if falta:
        return "?" in reply and str(falta) in texto and "falt" in texto

    try:
        import cardapio
        import cardapio_catalogo
        norm, _avisos, nao_achados = cardapio._normalizar_itens(
            [dict(i) for i in (memoria.get("items") or [])]
        )
        base = norm if (norm and not nao_achados) else (memoria.get("items") or [])
        quebras = cardapio_catalogo.quebras_de_lote([dict(i) for i in base])
    except Exception:
        quebras = []
    if quebras:
        if "25" not in texto:
            return False
        for q in quebras:
            palavra = _sem_acento(str(q["sabor"])).split()[0]
            if palavra not in texto:
                return False
            # As DUAS opcoes (25 ou 50) - "Ajusto para 50?" so com uma nao
            # cobre (pedido do dono, 07/10).
            baixo, cima = opcoes_do_lote(q)
            if str(cima) not in texto or (baixo and str(baixo) not in texto):
                return False
        return True

    if upsell_pendente(memoria, agora):
        ofereceu = any(m in texto for m in (
            "refrigerante", "refri", "bolo de milho", "tuchaua", "docinho",
            "brigadeiro", "beijinho", "pra acompanhar")) or e_a_oferta_configurada(reply, agora)
        # Pedido por volume: o valor tem de estar na mesma mensagem.
        if ofereceu and memoria.get("pedido_por_volume"):
            subtotal = subtotal_dos_salgados(memoria)
            if subtotal and _brl(subtotal).split()[-1] not in reply:
                return False
        return ofereceu

    return True


def _frase_do_volume(memoria: dict) -> tuple[str, str]:
    """(frase, instrucao extra) do volume dito sem sabor. ('', '') se nao ha."""
    try:
        _volume = int(memoria.get("volume_pendente") or 0)
    except (TypeError, ValueError):
        _volume = 0
    if not _volume or tem_salgado_na_ficha(memoria) or memoria.get("divisao_pendente"):
        return "", ""
    _quantos = max(1, _volume // 25)
    _preparo = (memoria.get("preparo_pendente") or "").strip()
    # Frase curta e fixa (reuniao de 01/10: "nunca utilizar muito texto").
    # O modelo escrevia "Para 100 salgados sao 4 porcoes de 25 unidades
    # cada! Voce pode escolher ate 4 sabores diferentes e o preparo...".
    _plural = "sabor" if _quantos == 1 else "sabores"
    # As opcoes ja vem na pergunta, uma embaixo da outra (pedido do dono,
    # 08/10): o cliente tinha de perguntar "quais sabores voces tem?".
    _fritos, _assados = _sabores_do_pacote("fritos"), _sabores_do_pacote("assados")
    # "Pode escolher ate 8 sabores" com 7 assados na lista (teste de 23/10):
    # o limite e o que o cardapio tem.
    _no_cardapio = {"frito": len(_fritos), "assado": len(_assados)}.get(_preparo, 0)
    if _no_cardapio:
        _quantos = min(_quantos, _no_cardapio)
    _plural = "sabor" if _quantos == 1 else "sabores"
    # A lista inteira so quando o cliente pede os sabores (Balbino, 17/10:
    # "pergunta qual sabor; se perguntar quais tem, ai manda a lista").
    if not memoria.get("mostrar_sabores"):
        if _preparo == "misto":
            _frase = (f"Quais sabores você quer? Pode escolher até {_quantos} {_plural}, "
                      f"entre fritos e assados (25 de cada). 😊")
            _extra = (" O cliente JA disse que quer fritos E assados. E PROIBIDO "
                      "dividir o total em metade de cada por conta propria.")
        elif _preparo:
            _frase = (f"Quais sabores {_preparo}s você quer? Pode escolher até {_quantos} "
                      f"{_plural} (25 de cada). 😊")
            _extra = (f" O cliente JA disse que e {_preparo}: e PROIBIDO perguntar o "
                      f"preparo de novo.")
        else:
            _frase = (f"Quais sabores você quer, e é frito ou assado? Pode escolher até "
                      f"{_quantos} {_plural} (25 de cada). 😊")
            _extra = ""
        return _frase, _extra
    if _preparo == "misto":
        _frase = (f"Escolha até {_quantos} {_plural} (25 de cada), entre fritos "
                  f"e assados:\n" + _lista_em_linhas(_fritos, "Fritos")
                  + "\n" + _lista_em_linhas(_assados, "Assados") + "\nQuais você quer? 😊")
        _extra = (" O cliente JA disse que quer fritos E assados. E PROIBIDO "
                  "dividir o total em metade de cada por conta propria.")
    elif _preparo:
        _lista = _fritos if _preparo == "frito" else _assados
        _frase = (f"Escolha até {_quantos} {_plural} {_preparo}s (25 de cada):\n"
                  + _lista_em_linhas(_lista) + "\nQuais você quer? 😊")
        _extra = (f" O cliente JA disse que e {_preparo}: e PROIBIDO perguntar o "
                  f"preparo de novo.")
    else:
        _frase = (f"Escolha até {_quantos} {_plural} (25 de cada):\n"
                  + _lista_em_linhas(_fritos, "Fritos") + "\n"
                  + _lista_em_linhas(_assados, "Assados")
                  + "\nQuais sabores, e é frito ou assado? 😊")
        _extra = ""
    return _frase.replace("\n\n", "\n"), _extra


def _sabores_do_pacote(categoria: str) -> list[str]:
    """Sabores de pacote disponiveis no cardapio ('fritos' ou 'assados')."""
    try:
        import facts
        return [p["item"] for p in facts.get()["produtos"]
                if _sem_acento(p["categoria"]) == categoria
                and p.get("preco") is None and p.get("disponivel", True)]
    except Exception:
        return []


def _lista_em_linhas(nomes: list[str], titulo: str = "") -> str:
    """Um por linha, para ler no celular (pedido do dono, 08/10)."""
    if not nomes:
        return ""
    linhas = "\n".join(f"• {n}" for n in nomes)
    return f"{titulo}:\n{linhas}" if titulo else linhas


def sabor_de_bebida_pendente(memoria: dict) -> tuple[str, list[str]]:
    """(item, opcoes) da primeira bebida sem sabor escolhido. ('', []) se nao ha."""
    try:
        import cardapio_catalogo
    except Exception:
        return "", []
    for item in memoria.get("items") or []:
        if not isinstance(item, dict) or str(item.get("sabor") or "").strip():
            continue
        opcoes = cardapio_catalogo.opcoes_do_item(str(item.get("name", "")))
        if opcoes:
            return str(item.get("name", "")), opcoes
    return "", []


def _lista_com_ou(opcoes: list[str]) -> str:
    if len(opcoes) > 1:
        return ", ".join(opcoes[:-1]) + " ou " + opcoes[-1]
    return opcoes[0] if opcoes else ""


def pergunta_do_doce(pendente: dict) -> str:
    """"Qual docinho voce prefere: Bandeja de Brigadeiros (16 un) R$ 20,80, ..."""
    try:
        import facts
        partes = []
        for nome in pendente.get("opcoes") or []:
            oficial = facts.resolve_unit_price(nome, 1)
            partes.append(f"{nome} — {_brl(oficial['preco_unitario'])}" if oficial else nome)
    except Exception:
        partes = list(pendente.get("opcoes") or [])
    aviso = (f"O {pendente['em_falta']} está em falta hoje! "
             if pendente.get("em_falta") else "")
    # Um por linha (pedido do dono, 08/10).
    return f"{aviso}Qual docinho você prefere?\n" + "\n".join(f"• {p}" for p in partes) + " 🍬"


def pergunta_do_sabor_da_bebida(memoria: dict) -> str:
    nome, opcoes = sabor_de_bebida_pendente(memoria)
    if not nome:
        return ""
    return f"Qual sabor do {nome}: {_lista_com_ou(opcoes)}? 🥤"


def casar_sabor(texto: str, opcoes: list[str]) -> str:
    """A opcao do cardapio que o cliente escolheu ("coca" -> "Coca"). '' se
    nao der para saber sem chutar."""
    t = f" {_sem_acento(texto)} "
    if not t.strip():
        return ""
    inteiras = [o for o in opcoes if f" {_sem_acento(o)} " in t]
    if inteiras:
        return max(inteiras, key=len)
    zero = " zero " in t
    candidatas = []
    for o in opcoes:
        palavras = _sem_acento(o).split()
        if palavras and f" {palavras[0]} " in t and (("zero" in palavras) == zero):
            candidatas.append(o)
    return candidatas[0] if len(candidatas) == 1 else ""


def aplicar_sabor_de_bebida(memoria: dict, texto: str) -> list[str]:
    """Grava na ficha o sabor da bebida que o cliente disse."""
    mudancas = []
    try:
        import cardapio_catalogo
    except Exception:
        return mudancas
    for item in memoria.get("items") or []:
        if not isinstance(item, dict) or str(item.get("sabor") or "").strip():
            continue
        opcoes = cardapio_catalogo.opcoes_do_item(str(item.get("name", "")))
        escolha = casar_sabor(texto, opcoes) if opcoes else ""
        if escolha:
            item["sabor"] = escolha
            mudancas.append(f"{item.get('name')} -> sabor {escolha}")
    return mudancas


_MARCAS_DE_REFRI = {"coca": "A Coca-Cola", "fanta": "A Fanta", "pepsi": "A Pepsi",
                    "sprite": "O Sprite", "kuat": "O Kuat", "soda": "A Soda",
                    "guarana": "O Guaraná Antártica"}


def pergunta_bloqueante(memoria: dict, agora=None) -> str:
    """A pergunta que TEM de sair antes de o funil andar. '' quando nao ha.

    Mesma ordem da ficha, e o texto e o que vai para o cliente. Serve para o
    codigo reescrever a resposta quando o modelo pula a etapa — foi assim que
    as travas de tamanho, de troco e de casa sem numero pararam de depender da
    boa vontade do modelo.
    """
    detalhe = _itens_ambiguos_detalhe(memoria)
    if detalhe:
        return pergunta_de_tamanho(detalhe)

    # O sabor em falta vem ANTES da pergunta do refrigerante: com as duas
    # abertas, a do refrigerante cobria a etapa e o que o modelo dissesse do
    # salgado passava - numa rodada ele inventou "o Croquete de Carne esta
    # disponivel, mas o de Macaxeira que voce pediu nao" (medido em 30/09).
    fora = itens_indisponiveis(memoria)
    if fora:
        return (f"O sabor {', '.join(fora)} está em falta hoje! "
                f"Por qual sabor você quer trocar? 😊")

    divisao = pendencia_de_divisao(memoria)
    if divisao:
        return divisao

    # Volume sem sabor ("1 cento de fritos", "500 salgados"): os sabores vem
    # antes de tudo. Era so instrucao ao modelo, e no teste de 01/10 ele
    # escreveu "10 sabores fritos e 10 assados" para 20 porcoes.
    volume, _extra = _frase_do_volume(memoria)
    if volume:
        return volume

    pendentes = memoria.get("sabores_pendentes") or []
    refri = next((p for p in pendentes if p.get("termo") == "refrigerante"), None)
    if refri and len(pendentes) == 1:
        opcoes = refri.get("opcoes") or []
        texto = (", ".join(opcoes[:-1]) + " ou " + opcoes[-1]) if len(opcoes) > 1 \
            else (opcoes[0] if opcoes else "")
        aviso = (f"O {refri['em_falta']} está em falta hoje! "
                 if refri.get("em_falta") else "")
        if refri.get("marca"):
            # "Uma coca" -> o tamanho, com o preco (13/10: a pergunta generica
            # se repetia quando o cliente respondia "coca cola" de novo).
            try:
                import facts as _f
                com_preco = []
                for o in opcoes:
                    of = _f.resolve_unit_price(o, 1)
                    com_preco.append(f"{o} ({_brl(of['preco_unitario'])})" if of else o)
            except Exception:
                com_preco = list(opcoes)
            lista = (", ".join(com_preco[:-1]) + " ou " + com_preco[-1]) if len(com_preco) > 1 \
                else "".join(com_preco)
            _w = refri["marca"].split()
            marca = _MARCAS_DE_REFRI.get(_w[0], refri["marca"].title())
            # "A Fanta uva temos em: 2 Litros": sem o sabor parecia que a lata pedida sumiu (27/10).
            if _w[0] in _MARCAS_DE_REFRI and _w[0] != "guarana" and _w[1:]:
                marca += " " + " ".join(_w[1:])
            return f"{aviso}{marca} temos em: {lista}. Qual tamanho você prefere? 🥤"
        return f"{aviso}Qual refrigerante você prefere: {texto}? 🥤"

    doce = next((p for p in pendentes if p.get("termo") == "docinho"), None)
    if doce and len(pendentes) == 1:
        return pergunta_do_doce(doce)

    # Tamanho decidido, falta o sabor - e as opcoes sao as do cardapio digital.
    sabor_bebida = pergunta_do_sabor_da_bebida(memoria)
    if sabor_bebida and not pendentes:
        return sabor_bebida
    if pendentes:
        # As opcoes uma embaixo da outra (pedido do dono, 08/10).
        blocos = []
        for p in pendentes:
            opcoes = (p.get("opcoes") or [])
            blocos.append(f"{p.get('qtd')} de {p.get('termo')}:\n"
                          + "\n".join(f"• {o}" for o in opcoes))
        return "Me confirma o sabor:\n" + "\n".join(blocos) + "\nQual você quer? 😊"

    falta = falta_para_o_volume(memoria)
    if falta:
        volume, soma = int(memoria["volume_pendente"]), int(memoria["volume_pendente"]) - falta
        # Uma pergunta so: _conferir_uma_pergunta cortava o "Ou fica com 125
        # mesmo?" e o "fica assim" seguinte nao tinha a que responder.
        return (f"Você pediu {volume} salgados e os sabores somam {soma}. Quais sabores "
                f"completam os {falta} que faltam, ou fica com {soma} mesmo? 😊")

    frase = _frase_de_lote(memoria)
    if frase:
        return frase

    return oferta_com_valor(memoria, agora)


def render_ficha(memoria: dict) -> str:
    """Bloco compacto para o system prompt: o que ja sei e o que falta."""
    linhas = []
    for campo in ("name", "items", "modality", "bairro", "endereco", "referencia",
                  "data_agendada", "horario", "payment_method"):
        valor = memoria.get(campo)
        if campo == "items":
            valor = _formatar_itens(valor)
        if campo == "data_agendada":
            # A data numerica vem primeiro porque e ela que vai no espelho
            # (BLOCO 5: "Data: [DD/MM/AAAA]"). O modelo escreve o proprio
            # resumo em vez de copiar o da tool, entao ele precisa ver o
            # formato certo AQUI - no teste de 22/09 saiu "hoje, terca-feira
            # (22/09)" porque era so isso que a ficha mostrava.
            numerica = _data_numerica(memoria.get("data_iso", ""))
            if numerica:
                valor = f"{numerica}" + (f" ({valor})" if valor else "")
        if campo == "endereco" and valor and endereco_sem_numero(memoria):
            # Sem esta marca a ficha se contradizia: a rua entrava na lista do
            # que ja esta confirmado ("nao pergunte de novo") e o numero na
            # lista do que falta coletar. O modelo desempatava descartando o
            # numero - pedia so data e horario em 4 de 5 rodadas.
            valor = f"{valor} <-- SEM O NUMERO DA CASA, o numero ainda FALTA"
        if valor:
            linhas.append(f"- {_ROTULOS[campo]}: {valor}")

    if memoria.get("frete") is not None:
        linhas.append(f"- Frete ja calculado: R$ {float(memoria['frete']):.2f}".replace(".", ","))

    troco = memoria.get("troco_para", "")
    if troco == "nao":
        linhas.append("- Troco: cliente tem o valor trocado (nao precisa)")
    elif troco == "nao_informado":
        linhas.append("- Troco: perguntado, cliente nao informou - nao pergunte de novo")
    elif troco:
        linhas.append(f"- Troco para: {_brl(float(troco))}")

    pendentes = faltando(memoria)
    detalhe = pendencia_de_detalhe(memoria)
    duvida_item = pendencia_de_item(memoria)
    lote_quebrado = pendencia_de_lote(memoria)
    esgotado = pendencia_de_indisponivel(memoria)
    sabor_aberto = pendencia_de_sabor(memoria)
    upsell = oferta_com_valor(memoria)
    bloco = ["=== FICHA DESTE ATENDIMENTO (memoria do cliente) ==="]
    if linhas:
        bloco.append("JA CONFIRMADO PELO CLIENTE - nao pergunte de novo, nao altere:")
        bloco += linhas
    else:
        bloco.append("Nenhum dado coletado ainda.")

    # Janela em que "quero mais 30 coxinhas" e ambiguo: pode ser acrescimo ao
    # pedido que acabou de ir para a producao, ou pedido novo. Sem este aviso o
    # modelo aplicava a regra de alteracao do BLOCO 0 e transferia para humano.
    minutos = pedido_recente(memoria)
    if minutos is None and not memoria.get("pedido_enviado"):
        # Sem esta linha o modelo aplicava a regra de "pedido em linha de
        # producao" do BLOCO 0 a um pedido que ainda estava sendo montado: o
        # cliente respondeu "PODE SER" a uma pergunta de quantidade e foi
        # mandado para a fila do humano, sem nunca ter visto um resumo (teste
        # do dono, 23/09 15:32).
        bloco.append(
            "NENHUM PEDIDO FOI ENVIADO AINDA nesta conversa. A regra de "
            "'pedido ja entrou em linha de producao' do BLOCO 0 NAO se aplica: "
            "e PROIBIDO dizer que o pedido esta em producao e PROIBIDO "
            "transferir por causa dela. Mudar item, quantidade, endereco, dia "
            "ou pagamento agora e atendimento normal - apenas atualize e siga."
        )
    if minutos is not None:
        bloco.append(
            f"ATENCAO - o cliente fechou um pedido ha {minutos} minuto(s). Se ele "
            "pedir mais itens agora, NAO presuma nem transfira: PERGUNTE se e "
            "para somar ao pedido que acabou de sair ou se e um pedido novo. "
            "Somar ao anterior e alteracao (BLOCO 0: transfira). Pedido novo e "
            "atendimento normal: siga o funil desde a modalidade."
        )

    # Volume dito sem sabor ("quero um cento"): a regra do dono manda oferecer
    # ate Total/25 sabores e perguntar o preparo, em vez de so perguntar "qual
    # sabor?" — o cliente nao sabe que pode dividir.
    _volume = 0
    try:
        _volume = int(memoria.get("volume_pendente") or 0)
    except (TypeError, ValueError):
        _volume = 0
    _divisao = pendencia_de_divisao(memoria)
    if _divisao:
        bloco.append(
            f"DIVISAO DO VOLUME PENDENTE: os sabores ja foram ditos e nao fecham "
            f"em lotes de 25. Pergunte SO isso, exatamente assim: \"{_divisao}\" "
            f"E PROIBIDO pedir os sabores de novo ou oferecer outro sabor.")
    _frase, _extra = _frase_do_volume(memoria)
    if _frase:
        bloco.append(
            f"VOLUME SEM SABOR: o cliente pediu {_volume} unidades e nao disse "
            f"os sabores. Responda SO com esta frase, exatamente assim: \"{_frase}\""
            f"{_extra} Nao explique porcoes nem a regra dos 25."
        )

    if memoria.get("items"):
        bloco.append(
            "Os itens acima sao o que o cliente pediu. O preco deles vem de "
            "consultar_preco/calculate_order - nunca do seu calculo."
        )
        # O codigo ja parou de mandar perguntar o tamanho acima de 20 unidades,
        # mas o modelo continuava perguntando por conta propria - consultar_preco
        # devolve o sabor de pacote E o salgado grande com o mesmo nome, e ele
        # resolvia a duvida perguntando. Aqui a regra vira instrucao positiva:
        # nessa quantidade nao ha duvida nenhuma a resolver.
        if tamanho_ja_definido(memoria):
            _minimo = _minimo_de_festa()
            bloco.append(
                f"TAMANHO JA DEFINIDO PELA QUANTIDADE: todo item deste pedido "
                f"tem {_minimo} unidades ou mais, entao e FESTA (lote de 25 por "
                f"sabor), no preco de pacote. E PROIBIDO perguntar 'festa ou "
                f"salgado grande de balcao?', 'porcao ou unidade avulsa?' ou "
                f"qualquer variante disso - nao ha duvida a resolver. Essa "
                f"pergunta so existe de 1 a {_minimo - 1} unidades."
            )

    if lote_quebrado:
        bloco.append(lote_quebrado)
    elif memoria.get("items") and not duvida_item:
        # O dono foi explicito em 25/09: pedido certo nao leva aula. Com 50
        # coxinhas e 150 churros na ficha - os dois multiplos de 25 - ela
        # respondeu "a porcao minima e de 25 por sabor, o pedido deve ser em
        # multiplos de 25" e ainda pediu confirmacao.
        bloco.append(
            "QUANTIDADES JA NA REGRA DE LOTE: fecham nos multiplos exigidos, "
            "nao ha nada a ajustar. E PROIBIDO explicar 'multiplos de 25', "
            "'minimo por sabor', 'porcoes de 25' ou 'de 25 em 25', e PROIBIDO "
            "pedir confirmacao do que ja esta certo - confirme o pedido e siga "
            "o funil."
        )

    if duvida_item:
        bloco.append(duvida_item)
    if sabor_aberto:
        bloco.append(sabor_aberto)
    if esgotado:
        bloco.append(esgotado)
    if detalhe:
        bloco.append(detalhe)
    if upsell:
        bloco.append(
            f"OFERTA ADICIONAL PENDENTE (BLOCO 2): o pedido de salgados ja esta "
            f"definido e voce AINDA NAO ofereceu o acompanhamento. Mande esta "
            f"frase, exatamente assim: \"{upsell}\""
        )

    # Duas coisas nunca podem ser preenchidas por deducao: a modalidade e o
    # dia. Sao as duas que o agente vinha assumindo sozinho.
    if not memoria.get("modality"):
        bloco.append(
            "MODALIDADE NAO DEFINIDA: o cliente ainda nao disse se e ENTREGA ou "
            "RETIRADA. Ate ele dizer, e PROIBIDO falar em endereco, bairro, "
            "frete ou taxa de entrega, e proibido inclui-los no resumo. "
            "Cliente PERGUNTAR sobre entrega nao e o cliente ESCOLHER entrega."
        )
    if not memoria.get("data_agendada"):
        bloco.append(
            "DIA NAO DEFINIDO: o cliente nao disse para qual DIA e o pedido. "
            'E PROIBIDO assumir "hoje". Pergunte o dia.'
        )
    else:
        bloco.append(
            f"DIA DO PEDIDO: {memoria['data_agendada']}. Escreva sempre assim, "
            'com dia da semana e data — nunca so "hoje". O cliente precisa VER '
            "o dia que esta confirmando, e a confirmacao final cita dia E horario."
        )

    # Etapa travada nao apaga a lista do que falta: ela so a adia. Sem esta
    # lista a ficha deixava de dizer, por exemplo, que a rua veio SEM NUMERO -
    # e o dado voltava a se perder assim que a pergunta obrigatoria fosse
    # respondida.
    if (duvida_item or sabor_aberto or esgotado or lote_quebrado or upsell) and pendentes:
        bloco.append("AINDA FALTA COLETAR, mas NAO nesta mensagem (primeiro a "
                     "PROXIMA PERGUNTA OBRIGATORIA abaixo):")
        bloco += [f"- {_ROTULOS[c]}" for c in pendentes]

    if duvida_item:
        # Fura a fila do funil: o que o cliente pediu precisa estar definido
        # antes de coletar entrega, dia e horario.
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: esclarecer o ITEM AMBIGUO acima. "
            "Nao pergunte modalidade, endereco, dia, horario nem pagamento "
            "nesta mensagem, e nao apresente resumo."
        )
    elif sabor_aberto:
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: qual e o SABOR NAO IDENTIFICADO "
            "acima. Nao pergunte modalidade, endereco, dia, horario nem "
            "pagamento nesta mensagem, e nao apresente resumo."
        )
    elif esgotado:
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: a troca do SABOR ESGOTADO acima. "
            "Nao pergunte modalidade, endereco, dia, horario nem pagamento "
            "nesta mensagem, e nao apresente resumo."
        )
    elif lote_quebrado:
        # Mesma fila furada, e pelo mesmo motivo: a quantidade e parte do que o
        # cliente pediu. A trava ja estava escrita na ficha, mas a lista de
        # coleta saia JUNTO com ela na mesma volta - o modelo lia as duas
        # ordens, escolhia a de baixo e ia cobrar endereco e pagamento com o
        # lote ainda quebrado. Foi a primeira queixa do dono em 26/09 ("o
        # fluxo correto e travar a regra dos sabores em lotes de 25 antes de
        # puxar os dados de entrega") e voltou em outros dois testes.
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: o ajuste da QUANTIDADE QUEBRADA "
            "acima, com TODOS os sabores fora da regra numa frase so. "
            "Nao pergunte modalidade, endereco, bairro, dia, horario nem "
            "pagamento nesta mensagem, e nao apresente resumo."
        )
    elif upsell:
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: a OFERTA ADICIONAL acima, sozinha. "
            "Nao pergunte endereco, bairro, dia, horario nem pagamento nesta "
            "mensagem, e nao apresente resumo - o upsell vem ANTES da "
            "logistica, e e a etapa que voce mais pula."
        )
    elif pendentes and memoria.get("modality") == "entrega":
        # Escolhida a entrega, o resto vai numa mensagem so. O dono contou dez
        # perguntas pingadas para fechar um pedido (23/09) e pediu bairro, rua
        # com numero, dia, horario e pagamento juntos. Antes da modalidade a
        # coleta continua de uma em uma: perguntar endereco sem o cliente ter
        # escolhido entrega e exatamente o que o funil evita.
        bloco.append("AINDA FALTA COLETAR:")
        bloco += [f"- {_ROTULOS[c]}" for c in pendentes]
        bloco.append(
            "PECA TODOS ESSES DADOS NESTA MESMA MENSAGEM, em lista vertical "
            "curta (um por linha), e SO eles. E PROIBIDO perguntar um de cada "
            "vez, PROIBIDO repetir pergunta de dado que ja esta na ficha acima "
            "e PROIBIDO pedir ponto de referencia."
        )
        bloco.append(
            "PROIBIDO nesta mensagem: apresentar resumo do pedido, informar total "
            "ou chamar send_order_to_cardapio - a ficha ainda tem pendencias."
        )
    elif pendentes:
        bloco.append("AINDA FALTA COLETAR, nesta ordem:")
        bloco += [f"- {_ROTULOS[c]}" for c in pendentes]
        bloco.append(
            f"PROXIMA PERGUNTA OBRIGATORIA: {_ROTULOS[pendentes[0]]}. "
            "Faca SO essa pergunta nesta mensagem - uma pergunta por mensagem. "
            "Saudacao e resposta a uma duvida do cliente nao contam como pergunta "
            "e podem vir na mesma mensagem."
        )
        bloco.append(
            "PROIBIDO nesta mensagem: apresentar resumo do pedido, informar total "
            "ou chamar send_order_to_cardapio - a ficha ainda tem pendencias."
        )
    elif detalhe:
        bloco.append(
            "PROXIMA PERGUNTA OBRIGATORIA: o detalhe pendente acima. "
            "Faca SO essa pergunta nesta mensagem - uma pergunta por mensagem. "
            "Saudacao e resposta a uma duvida do cliente nao contam como pergunta "
            "e podem vir na mesma mensagem."
        )
    else:
        bloco.append(
            "Ficha completa. Agora: chame calculate_order com os itens e o bairro, "
            "copie o 'resumo_formatado' do retorno e peca a confirmacao do cliente. "
            "So chame send_order_to_cardapio depois que ele confirmar."
        )
    return "\n".join(bloco)


def registrar_frete(phone: str, memoria: dict, valor: float) -> None:
    memoria["frete"] = valor
    save(phone, memoria)


# O que sobrevive ao fim de um pedido: sao dados do CLIENTE, nao do pedido.
# O prompt manda nao repetir pergunta ja respondida, e endereco raramente muda.
_CAMPOS_DO_CLIENTE = ("name", "bairro", "endereco", "referencia")

# Por quanto tempo depois de um pedido sair ainda faz sentido perguntar se o
# cliente quer SOMAR ao que acabou de ir para a producao. Passado isso, pedir
# mais coisa so pode ser pedido novo. O proprio prompt estima 30 a 40 minutos
# de entrega, entao meia hora cobre a janela em que da para ajustar.
MINUTOS_PEDIDO_RECENTE = 30


def pedido_recente(memoria: dict, agora=None) -> int | None:
    """Minutos desde o ultimo pedido fechado, se ainda estiver na janela.

    Fora da janela devolve None: nao ha pedido em producao para alterar, e
    perguntar "e novo ou e alteracao?" so atrapalharia quem volta dias depois.
    """
    quando = memoria.get("ultimo_pedido_em")
    if not quando:
        return None
    agora = agora or _dt.datetime.now()
    try:
        saiu = _dt.datetime.fromisoformat(str(quando))
    except (TypeError, ValueError):
        return None
    minutos = (agora - saiu).total_seconds() / 60
    if 0 <= minutos <= MINUTOS_PEDIDO_RECENTE:
        return int(minutos)
    return None


# Janela do "pos-pedido": aviso de status ("ja saiu?", "vou autorizar a
# portaria") e acrescimo ("inclui 2 cocas") so fazem sentido enquanto o pedido
# ainda esta sendo feito ou entregue. Maior que MINUTOS_PEDIDO_RECENTE porque
# pedido agendado para a noite continua "em andamento" desde a manha.
HORAS_POS_PEDIDO = 12


def em_pos_pedido(memoria: dict, agora=None, aceita_itens: bool = False) -> bool:
    """Ha um pedido fechado ha menos de HORAS_POS_PEDIDO e nenhum em montagem?

    `aceita_itens` e para o acrescimo: "inclui 10 coxinhas" poe item na ficha
    na mesma mensagem, e mesmo assim e acrescimo, nao pedido novo.
    """
    if memoria.get("items") and not aceita_itens:
        return False
    quando = memoria.get("ultimo_pedido_em")
    if not quando:
        return False
    agora = agora or _dt.datetime.now()
    try:
        saiu = _dt.datetime.fromisoformat(str(quando))
    except (TypeError, ValueError):
        return False
    return _dt.timedelta(0) <= (agora - saiu) <= _dt.timedelta(hours=HORAS_POS_PEDIDO)


def _so_dados_do_cliente(memoria: dict) -> dict:
    """Ficha zerada mantendo so o que e do CLIENTE, nao do pedido."""
    limpa = _empty()
    for campo in _CAMPOS_DO_CLIENTE:
        if memoria.get(campo):
            limpa[campo] = memoria[campo]
    for campo in ("ultimo_pedido_em", "ultimo_pedido_modalidade", "ultimo_pedido_quando"):
        if memoria.get(campo):
            limpa[campo] = memoria[campo]
    return limpa


def registrar_pedido_enviado(phone: str, memoria: dict) -> None:
    """Fecha o pedido e deixa a ficha pronta para o proximo.

    Antes isto so marcava `pedido_enviado = True` e guardava o resto. O segundo
    pedido do mesmo cliente saia com o endereco e o pagamento do anterior e com
    a data do pedido antigo - que ja tinha passado, e ainda travava o
    atendimento no portao de agendamento.

    Guarda a hora do fechamento: e ela que diz, logo depois, se "agora quero 30
    coxinhas" pode ser um acrescimo ao pedido que acabou de sair ou ja e outro
    pedido.
    """
    limpa = _so_dados_do_cliente(memoria)
    # A sessao continua a mesma: o cliente acabou de falar com a gente.
    for campo in _CAMPOS_DE_SESSAO:
        limpa[campo] = memoria.get(campo, False)
    limpa["ultimo_pedido_em"] = _dt.datetime.now().isoformat(timespec="seconds")
    limpa["ultimo_pedido_modalidade"] = memoria.get("modality") or ""
    _dia, _hora = memoria.get("data_agendada") or "", memoria.get("horario") or ""
    limpa["ultimo_pedido_quando"] = (f"{_dia} às {_hora}" if _dia and _hora
                                     else _dia or (f"às {_hora}" if _hora else ""))

    memoria.clear()
    memoria.update(limpa)
    save(phone, memoria)
    print(f"[memoria] {phone}: pedido fechado, ficha limpa "
          f"(mantidos: {', '.join(c for c in _CAMPOS_DO_CLIENTE if limpa.get(c))})")
