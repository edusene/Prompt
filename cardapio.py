"""
cardapio.py — Cliente HTTP para a API do cardapio digital BALBS.
Gerencia envio de pedidos (createOrderFromAgent) e validacao (calculateOrder).
"""

import os
import json
import httpx


def em_modo_teste() -> bool:
    """Le o modo teste da MESMA fonte que app._send_order_handler: o banco.

    Ler so de os.environ nao serve: varios modulos chamam
    load_dotenv(override=True) — db.py no import e meta.reload_config() depois
    de salvar o .env pelo painel — e isso devolve a variavel ao valor do
    arquivo, revertendo em silencio o modo producao ligado pelo painel.
    O banco e onde o painel grava, entao e ele que manda.
    """
    valor = ""
    try:
        import database
        valor = database.get_setting("CARDÁPIO_TEST_MODE", "") or ""
    except Exception:
        valor = ""
    if not valor:
        valor = os.getenv("CARDÁPIO_TEST_MODE", "") or os.getenv("CARD_TEST_MODE", "") or "true"
    return valor.strip().lower() == "true"


def _get_base_url() -> str:
    test_mode = em_modo_teste()
    if test_mode:
        url = os.getenv("CARDÁPIO_API_URL_TEST", "") or os.getenv("CARD_API_URL_TEST", "")
        if not url:
            url = os.getenv("CARDÁPIO_API_URL", "") or os.getenv("CARD_API_URL", "")
        return url.rstrip("/")
    url = os.getenv("CARDÁPIO_API_URL", "") or os.getenv("CARD_API_URL", "")
    return url.rstrip("/")


def _get_api_key() -> str:
    return os.getenv("BALBS_AGENTE_API_KEY", "")


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "x-api-key": _get_api_key(),
    }


def _brl(value: float) -> str:
    return ("R$ %.2f" % float(value or 0)).replace(".", ",")


def _normalizar_itens(items: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """Define o preco de cada item pela TABELA OFICIAL, nao pelo que o modelo mandou.

    Duas coisas acontecem aqui:

    1. Preco vem sempre do cardapio. O modelo pode mandar o item sem 'price'
       (ja aconteceu: 50 coxinhas + 25 quibes fecharam com subtotal R$ 0,00) ou
       com um valor inventado. O que ele manda e ignorado quando a tabela sabe
       responder.
    2. Sabor nao soma junto com o pacote. No cardapio o preco esta no FORMATO
       (Porcao 25 / Meio Cento / Cento) e o sabor vem "Incluso no Pacote"; se o
       pedido traz a linha do pacote E a linha do sabor, o sabor entra zerado.

    Retorna (itens, avisos, nao_encontrados).
    """
    if not items:
        return [], [], []

    try:
        import facts
    except Exception as e:
        print(f"[cardapio] WARN tabela de precos indisponivel: {e}")
        return list(items), [], []

    normalizados = []
    avisos = []
    nao_encontrados = []
    tem_pacote = False
    indices_sabor = []

    for idx, item in enumerate(items):
        novo = dict(item)
        nome = str(item.get("name", ""))
        try:
            quantidade = int(float(item.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            quantidade = 0

        correspondencias = facts.lookup_preco(nome)
        exata = next(
            (c for c in correspondencias if facts.normalize(c["item"]) == facts.normalize(nome)),
            None,
        )
        if exata is not None:
            if exata["preco"] is None:
                indices_sabor.append(idx)
            else:
                tem_pacote = True

        oficial = facts.resolve_unit_price(nome, quantidade)
        if oficial is None:
            nao_encontrados.append(nome)
            print(f"[cardapio] item FORA do cardapio: '{nome}'")
        else:
            informado = float(item.get("price", 0) or 0)
            novo["price"] = oficial["preco_unitario"]
            novo["name"] = novo.get("name") or oficial["item_oficial"]
            if abs(informado - oficial["preco_unitario"]) > 0.001:
                avisos.append(
                    f"'{nome}': preco ajustado para {_brl(oficial['preco_unitario'])}/un "
                    f"pela tabela oficial ({oficial['base']})"
                )

            # Onde a planilha e o cardapio digital discordam, vale o preco do
            # CARDAPIO: e o que o cliente ve ao abrir o menu. Cotar a planilha
            # faria o agente prometer um valor que o cardapio nao pratica.
            # Fica depois do ajuste acima para as duas mensagens nao se
            # contradizerem no log.
            try:
                import cardapio_catalogo
                do_cardapio = cardapio_catalogo.preco_do_cardapio(nome)
            except Exception:
                do_cardapio = None
            if do_cardapio is not None and abs(do_cardapio - novo["price"]) > 0.001:
                avisos.append(
                    f"'{nome}': vale o preco do cardapio digital "
                    f"{_brl(do_cardapio)}/un (a planilha diz {_brl(novo['price'])})"
                )
                novo["price"] = do_cardapio
                oficial = dict(oficial, preco_unitario=do_cardapio)

            if oficial.get("ambiguo"):
                avisos.append(
                    f"'{nome}' cobrado como {oficial['item_oficial']} "
                    f"({_brl(oficial['preco_unitario'])}/un) por causa da quantidade "
                    f"({quantidade}). Existe tambem '{oficial['alternativa']}' - "
                    "confirme com o cliente qual ele quer antes de fechar."
                )
        normalizados.append(novo)

    if tem_pacote and indices_sabor:
        for idx in indices_sabor:
            nome = normalizados[idx].get("name", "")
            if float(normalizados[idx].get("price", 0) or 0) > 0:
                avisos.append(f"'{nome}' e sabor do pacote: nao cobrado separadamente")
                normalizados[idx]["price"] = 0

    if avisos:
        print(f"[cardapio] itens normalizados: {avisos}")

    return normalizados, avisos, nao_encontrados


_MODALIDADE_ROTULO = {
    "entrega": "Entrega",
    "retirada": "Retirada na fabrica",
}

# Prompt operacional, BLOCO 3: "entrega paga em Dinheiro, Cartao de Debito ou
# Cartao de Credito tem taxa fixa de R$ 2,00. No Pix antecipado e na retirada,
# e isenta". Fica em codigo porque estava so no texto: a tool devolvia
# return_fee=0 sempre, e o total saia R$ 2,00 menor do que o devido.
TAXA_RETORNO = 2.00

_PAGAMENTOS_COM_RETORNO = {
    "dinheiro", "cartao", "cartão", "cartao_debito", "cartao_credito",
    "cartao_a_definir", "debito", "débito", "credito", "crédito",
}


def calcular_taxa_retorno(modalidade: str, pagamento: str) -> float:
    """R$ 2,00 na entrega paga em dinheiro ou cartao; R$ 0,00 no resto.

    Na retirada o pagamento e no balcao, entao nao ha retorno a cobrar. No Pix
    o pagamento e antecipado e a taxa e isenta.
    """
    if (modalidade or "").strip().lower() != "entrega":
        return 0.0
    if (pagamento or "").strip().lower() in _PAGAMENTOS_COM_RETORNO:
        return TAXA_RETORNO
    return 0.0


ENDERECO_FABRICA = "Av. Calama, 5892 - Igarapé"

_PAGAMENTO_ROTULO = {
    "pix": "Pix antecipado",
    "dinheiro": "Dinheiro na entrega",
    "cartao_debito": "Cartão de débito na entrega",
    "cartao_credito": "Cartão de crédito na entrega",
    "cartao": "Cartão na entrega",
    "cartao_a_definir": "Cartão (confirmar se é débito ou crédito)",
}


def montar_resumo(items: list[dict], subtotal: float, delivery_fee: float,
                  return_fee: float, total: float, bairro: str = "",
                  modalidade: str = "", dia: str = "", horario: str = "",
                  cliente: str = "", pagamento: str = "", endereco: str = "") -> str:
    """Resumo pronto para o agente copiar, com os numeros ja calculados aqui.

    Segue exatamente o modelo do BLOCO 5 do prompt operacional. Isso e
    proposital: enquanto existiam dois formatos — o do prompt e o desta tool —
    o modelo misturava os dois e, depois do "sim" do cliente, reapresentava o
    resumo em vez de fechar o pedido. Um formato so, gerado aqui, com os
    numeros vindos da tabela oficial.

    Dia e horario aparecem sempre: antes o resumo trazia so itens e valores, e
    o cliente confirmava sem nunca ter visto para que dia o pedido ia.
    """
    retirada = (modalidade or "").strip().lower() == "retirada"

    linhas = [f"👤 Cliente: {cliente or '[Nome]'}"]
    linhas.append(f"📅 Data: {dia or '[DIA - PERGUNTE AO CLIENTE]'}")
    linhas.append(f"⏰ Horário: {horario or '[HORARIO]'}")

    if retirada:
        linhas.append(f"📍 Retirada: No balcão da fábrica ({ENDERECO_FABRICA})")
    else:
        linhas.append(f"📍 Bairro: {bairro or '[BAIRRO]'}")
        linhas.append(f"📍 Endereço: {endereco or '[ENDERECO]'}")

    linhas.append("Itens:")
    for item in items or []:
        nome = item.get("name", "")
        qtd = item.get("quantity", 0)
        preco = float(item.get("price", 0) or 0)
        if preco <= 0:
            linhas.append(f"• {qtd} un - {nome} (incluso no pacote)")
        else:
            linhas.append(f"• {qtd} un - {nome} — {_brl(qtd * preco)}")

    if retirada:
        linhas.append(f"💰 Total: {_brl(total)}")
        linhas.append("💳 Pagamento: Balcão da fábrica")
    else:
        linhas.append(f"💰 Subtotal: {_brl(subtotal)}")
        if delivery_fee:
            linhas.append(f"🚚 Frete: {_brl(delivery_fee)}")
        # Taxa de retorno e obrigatoria em dinheiro/cartao na entrega
        # (BLOCO 3 do prompt) e isenta no Pix.
        if return_fee:
            linhas.append(f"🛵 Taxa de retorno: {_brl(return_fee)}")
        linhas.append(f"💵 Total: {_brl(total)}")
        rotulo_pag = _PAGAMENTO_ROTULO.get(pagamento, pagamento) or "[PAGAMENTO]"
        linhas.append(f"💳 Pagamento: {rotulo_pag}")

    # A confirmacao cita dia e hora juntos: ultima chance de o cliente corrigir
    # o dia antes de o pedido entrar na producao.
    if dia and horario:
        linhas.append(f"Posso confirmar o seu pedido para {dia} às {horario}? 💛")
    else:
        linhas.append("Posso confirmar o seu pedido? 💛")

    return "\n".join(linhas)


def calcular_pedido(items: list[dict], bairro: str = "", modalidade: str = "",
                    dia: str = "", horario: str = "", pagamento: str = "",
                    cliente: str = "", endereco: str = "") -> dict:
    """Valida o pedido e calcula subtotal, frete e total.

    O frete vem da tabela oficial (facts.lookup_frete), nao mais de regex sobre
    texto recuperado do RAG: o texto vinha quebrado e produzia valor errado ou
    zero silencioso.

    Returns:
        dict com success, subtotal, delivery_fee, total, resumo_formatado
        ou error
    """
    test_mode = em_modo_teste()

    # Retirada nao tem frete. O modelo as vezes manda o bairro do cliente mesmo
    # na retirada, e a taxa entrava no total sem aparecer no resumo — o cliente
    # via "25 coxinhas R$ 20,00" e um total de R$ 28,00.
    if (modalidade or "").strip().lower() == "retirada":
        bairro = ""

    # Preco vem da planilha nos DOIS modos. Isto rodava so no ramo de teste, e
    # desligar o modo teste reabria o bug de o preco inventado pelo modelo ir
    # direto para o pedido.
    items, avisos_itens, nao_encontrados = _normalizar_itens(items)

    # Sem preco oficial nao existe resumo: melhor a tool falhar e o agente
    # consultar do que apresentar um total errado ao cliente.
    if nao_encontrados:
        return {
            "success": False,
            "error": (
                "Itens fora do cardapio oficial: " + ", ".join(nao_encontrados) +
                ". Use consultar_preco para achar o nome correto e chame "
                "calculate_order de novo. NAO informe total ao cliente antes disso."
            ),
            "itens_nao_encontrados": nao_encontrados,
        }

    # Traducao para o cardapio digital. Os dois catalogos divergem em varios
    # itens (ver cardapio_catalogo.BLOQUEADOS); fechar pedido com um deles
    # cobraria do cliente um valor que o cardapio nao reconhece.
    import cardapio_catalogo
    items_api, bloqueados = cardapio_catalogo.traduzir(items)

    if bloqueados and not test_mode:
        return {
            "success": False,
            "error": (
                "Estes itens nao podem ser fechados pelo agente: "
                + "; ".join(bloqueados)
                + ". Informe ao cliente que precisa confirmar com a equipe e "
                "transfira o atendimento. NAO informe total."
            ),
            "itens_bloqueados": bloqueados,
        }

    if test_mode:
        subtotal = 0.0
        for item in items or []:
            try:
                subtotal += float(item.get("quantity", 0)) * float(item.get("price", 0))
            except (ValueError, TypeError):
                pass

        delivery_fee = 0.0
        frete_nao_encontrado = False
        bairro_oficial = bairro
        if bairro:
            try:
                import facts
                encontrado = facts.lookup_frete(bairro)
            except Exception as e:
                print(f"[cardapio] ERRO ao consultar tabela de frete: {e}")
                encontrado = None
            if encontrado:
                delivery_fee = float(encontrado["taxa"])
                bairro_oficial = encontrado["bairro"]
                print(f"[cardapio] frete oficial: {bairro_oficial} = R$ {delivery_fee:.2f} ({encontrado['match']})")
            else:
                frete_nao_encontrado = True
                print(f"[cardapio] frete NAO ENCONTRADO na tabela oficial para: '{bairro}'")

        return_fee = calcular_taxa_retorno(modalidade, pagamento)
        total = subtotal + delivery_fee + return_fee
        resultado = {
            "success": True,
            "subtotal": round(subtotal, 2),
            "delivery_fee": round(delivery_fee, 2),
            "return_fee": round(return_fee, 2),
            "total": round(total, 2),
            "bairro_oficial": bairro_oficial,
            "frete_nao_encontrado": frete_nao_encontrado,
            "resumo_formatado": montar_resumo(
                items, subtotal, delivery_fee, return_fee, total, bairro_oficial,
                modalidade=modalidade, dia=dia, horario=horario,
                cliente=cliente, pagamento=pagamento, endereco=endereco,
            ),
        }
        if avisos_itens:
            resultado["itens_ajustados"] = avisos_itens
        if bloqueados:
            # Em modo teste nada e enviado, entao isto avisa sem travar:
            # o mesmo pedido em producao seria recusado.
            resultado["aviso_catalogo"] = (
                "Estes itens divergem do cardapio digital e seriam recusados em "
                "producao: " + "; ".join(bloqueados)
            )
        if frete_nao_encontrado:
            resultado["aviso"] = (
                f"O bairro '{bairro}' nao esta na tabela oficial de entrega. NAO informe "
                "uma taxa estimada: confirme o bairro com o cliente ou transfira para um atendente."
            )
        print(f"[cardapio] calculateOrder (teste): subtotal={subtotal} frete={delivery_fee} total={total}")
        return resultado

    base_url = _get_base_url()
    if not base_url:
        return {"success": False, "error": "CARDÁPIO_API_URL nao configurada"}

    url = f"{base_url}/functions/calculateOrder"
    # Itens no formato do cardapio digital: pacote pelo nome + sabores em
    # selected_flavors, quantidade em unidades.
    payload = {"items": items_api}
    if bairro:
        payload["bairro"] = bairro

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload)
            data = resp.json()
            print(f"[cardápio] calculateOrder status={resp.status_code} response={json.dumps(data, ensure_ascii=False)[:300]}")
            if resp.status_code != 200 or not isinstance(data, dict):
                return {"success": False, "error": f"HTTP {resp.status_code}"}

            # A resposta do cardapio digital nao tem campo "success" e usa
            # deliveryFee em camelCase. Sem traduzir, todo pedido em producao
            # saia com success=None e sem resumo.
            invalidos = [
                f"{i.get('name')}: {i.get('reason') or 'nao aceito'}"
                for i in (data.get("items") or []) if not i.get("valid")
            ]
            if invalidos:
                return {
                    "success": False,
                    "error": "O cardapio digital recusou estes itens: "
                             + "; ".join(invalidos)
                             + ". NAO informe total ao cliente; confirme com a equipe.",
                    "itens_recusados": invalidos,
                }

            subtotal = float(data.get("subtotal") or 0)
            delivery_fee = float(data.get("deliveryFee") or data.get("delivery_fee") or 0)
            return_fee = calcular_taxa_retorno(modalidade, pagamento)
            total = round(subtotal + delivery_fee + return_fee, 2)

            resultado = {
                "success": True,
                "subtotal": round(subtotal, 2),
                "delivery_fee": round(delivery_fee, 2),
                "return_fee": round(return_fee, 2),
                "total": total,
                "bairro_oficial": bairro,
                "resposta_cardapio": data,
                "resumo_formatado": montar_resumo(
                    items, subtotal, delivery_fee, return_fee, total, bairro,
                    modalidade=modalidade, dia=dia, horario=horario,
                    cliente=cliente, pagamento=pagamento, endereco=endereco,
                ),
            }
            if avisos_itens:
                resultado["itens_ajustados"] = avisos_itens
            # Mesma guarda do modo teste: frete zerado numa entrega com bairro
            # informado quase sempre e bairro fora da tabela.
            if bairro and not delivery_fee:
                resultado["aviso"] = (
                    f"O bairro '{bairro}' nao retornou taxa de entrega. NAO informe "
                    "uma taxa estimada: confirme o bairro com o cliente ou transfira "
                    "para um atendente."
                )
            print(f"[cardapio] calculateOrder (producao): subtotal={subtotal} "
                  f"frete={delivery_fee} retorno={return_fee} total={total}")
            return resultado
    except Exception as e:
        print(f"[cardápio] calculateOrder ERRO: {e}")
        return {"success": False, "error": str(e)}

def criar_pedido(
    customer_name: str,
    customer_phone: str,
    items: list[dict],
    total: float,
    customer_confirmed: bool = True,
    delivery_address: str = "",
    distance_km: float = 0,
    payment_method: str = "",
    scheduled_date: str = "",
    is_scheduled: bool = False,
    subtotal: float = 0,
    delivery_fee: float = 0,
    return_fee: float = 0,
    notes: str = "",
    whatsapp_message_id: str = "",
    human_confirmed: bool = False,
) -> dict:
    """Envia pedido para o cardapio digital via createOrderFromAgent.

    Returns:
        dict com success, order_id, display_id, status ou error
    """
    base_url = _get_base_url()
    if not base_url:
        return {"success": False, "error": "CARDÁPIO_API_URL nao configurada"}

    api_key = _get_api_key()
    if not api_key:
        return {"success": False, "error": "BALBS_AGENTE_API_KEY nao configurada"}

    # Regra do dono do cardapio: pedido no Pix so grava com human_confirmed,
    # depois de alguem conferir o comprovante. Sem isso a chamada volta 400.
    if (payment_method or "").strip().lower().startswith("pix") and not human_confirmed:
        return {
            "success": False,
            "error": (
                "Pedido no Pix exige conferencia do comprovante por uma pessoa "
                "antes de entrar no cardapio (human_confirmed)."
            ),
            "aguardando_validacao_humana": True,
        }

    # Itens no formato do cardapio digital (pacote + selected_flavors).
    import cardapio_catalogo
    items, bloqueados = cardapio_catalogo.traduzir(items)
    if bloqueados:
        return {
            "success": False,
            "error": "Itens sem equivalente confiavel no cardapio digital: "
                     + "; ".join(bloqueados),
            "itens_bloqueados": bloqueados,
        }

    url = f"{base_url}/functions/createOrderFromAgent"

    payload = {
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "items": items,
        "total": total,
        "customer_confirmed": customer_confirmed,
    }

    if delivery_address:
        payload["delivery_address"] = delivery_address
    if distance_km:
        payload["distance_km"] = distance_km
    if payment_method:
        payload["payment_method"] = payment_method
    if scheduled_date:
        payload["scheduled_date"] = scheduled_date
    if is_scheduled:
        payload["is_scheduled"] = is_scheduled
    if subtotal:
        payload["subtotal"] = subtotal
    if delivery_fee:
        payload["delivery_fee"] = delivery_fee
    if return_fee:
        payload["return_fee"] = return_fee
    if notes:
        payload["notes"] = notes
    if whatsapp_message_id:
        payload["whatsapp_message_id"] = whatsapp_message_id
    if human_confirmed:
        payload["human_confirmed"] = human_confirmed

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload, headers=_headers())
            data = resp.json()
            print(f"[cardápio] createOrder status={resp.status_code} response={json.dumps(data, ensure_ascii=False)[:500]}")
            if resp.status_code == 200 and data.get("success"):
                return {
                    "success": True,
                    "order_id": data.get("order_id", ""),
                    "display_id": data.get("display_id", ""),
                    "status": data.get("status", "pendente"),
                }
            return {
                "success": False,
                "error": data.get("error", data.get("message", f"HTTP {resp.status_code}")),
                "status_code": resp.status_code,
            }
    except Exception as e:
        print(f"[cardápio] createOrder ERRO: {e}")
        return {"success": False, "error": str(e)}
