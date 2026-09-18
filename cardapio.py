"""
cardapio.py — Cliente HTTP para a API do cardapio digital BALBS.
Gerencia envio de pedidos (createOrderFromAgent) e validacao (calculateOrder).
"""

import os
import json
import httpx


def _get_base_url() -> str:
    test_mode = (os.getenv("CARDÁPIO_TEST_MODE", "true") or os.getenv("CARD_TEST_MODE", "true")).lower() == "true"
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


def _normalizar_itens(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Evita cobrar duas vezes o mesmo salgado.

    No cardapio, o preco esta no FORMATO (Porcao 25 / Meio Cento / Cento) e o
    sabor vem "Incluso no Pacote". Quando o agente monta o pedido com a linha do
    pacote E a linha do sabor, o sabor nao pode somar de novo — era assim que o
    total saia dobrado.
    """
    if not items:
        return [], []

    try:
        import facts
    except Exception:
        return list(items), []

    normalizados = []
    avisos = []
    tem_pacote = False
    indices_sabor = []

    for idx, item in enumerate(items):
        novo = dict(item)
        correspondencias = facts.lookup_preco(str(item.get("name", "")))
        melhor = correspondencias[0] if correspondencias else None
        if melhor and facts.normalize(melhor["item"]) == facts.normalize(str(item.get("name", ""))):
            if melhor["preco"] is None:
                indices_sabor.append(idx)
            else:
                tem_pacote = True
        normalizados.append(novo)

    if tem_pacote and indices_sabor:
        for idx in indices_sabor:
            nome = normalizados[idx].get("name", "")
            if float(normalizados[idx].get("price", 0) or 0) > 0:
                avisos.append(f"'{nome}' e sabor do pacote: nao cobrado separadamente")
                normalizados[idx]["price"] = 0
        print(f"[cardapio] itens normalizados: {avisos}")

    return normalizados, avisos


def montar_resumo(items: list[dict], subtotal: float, delivery_fee: float,
                  return_fee: float, total: float, bairro: str = "") -> str:
    """Resumo pronto para o agente copiar, com os numeros ja calculados aqui.

    O prompt manda o agente copiar este texto em vez de somar sozinho — era
    exatamente onde apareciam totais errados.
    """
    linhas = ["*Resumo do pedido*"]
    for item in items or []:
        nome = item.get("name", "")
        qtd = item.get("quantity", 0)
        preco = float(item.get("price", 0) or 0)
        if preco <= 0:
            linhas.append(f"- {qtd} x {nome} (incluso no pacote)")
        else:
            linhas.append(f"- {qtd} x {nome} — {_brl(qtd * preco)}")
    linhas.append(f"Subtotal: {_brl(subtotal)}")
    if delivery_fee:
        rotulo = f"Taxa de entrega ({bairro})" if bairro else "Taxa de entrega"
        linhas.append(f"{rotulo}: {_brl(delivery_fee)}")
    if return_fee:
        linhas.append(f"Taxa de retorno: {_brl(return_fee)}")
    linhas.append(f"*Total: {_brl(total)}*")
    return "\n".join(linhas)


def calcular_pedido(items: list[dict], bairro: str = "") -> dict:
    """Valida o pedido e calcula subtotal, frete e total.

    O frete vem da tabela oficial (facts.lookup_frete), nao mais de regex sobre
    texto recuperado do RAG: o texto vinha quebrado e produzia valor errado ou
    zero silencioso.

    Returns:
        dict com success, subtotal, delivery_fee, total, resumo_formatado
        ou error
    """
    test_mode = (os.getenv("CARDÁPIO_TEST_MODE", "true") or os.getenv("CARD_TEST_MODE", "true")).lower() == "true"

    if test_mode:
        items, avisos_itens = _normalizar_itens(items)

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

        total = subtotal + delivery_fee
        resultado = {
            "success": True,
            "subtotal": round(subtotal, 2),
            "delivery_fee": round(delivery_fee, 2),
            "return_fee": 0,
            "total": round(total, 2),
            "bairro_oficial": bairro_oficial,
            "frete_nao_encontrado": frete_nao_encontrado,
            "resumo_formatado": montar_resumo(items, subtotal, delivery_fee, 0, total, bairro_oficial),
        }
        if avisos_itens:
            resultado["itens_ajustados"] = avisos_itens
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
    payload = {"items": items}
    if bairro:
        payload["bairro"] = bairro

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload)
            data = resp.json()
            print(f"[cardápio] calculateOrder status={resp.status_code} response={json.dumps(data, ensure_ascii=False)[:300]}")
            if resp.status_code == 200:
                if isinstance(data, dict) and data.get("success"):
                    data.setdefault("resumo_formatado", montar_resumo(
                        items,
                        data.get("subtotal", 0),
                        data.get("delivery_fee", 0),
                        data.get("return_fee", 0),
                        data.get("total", 0),
                        bairro,
                    ))
                return data
            return {"success": False, "error": data.get("error", f"HTTP {resp.status_code}")}
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
