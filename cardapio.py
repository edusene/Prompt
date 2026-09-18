"""
cardapio.py — Cliente HTTP para a API do cardapio digital BALBS.
Gerencia envio de pedidos (createOrderFromAgent) e validacao (calculateOrder).
"""

import os
import json
import httpx


def _extract_delivery_fee(context: str, bairro: str) -> float:
    """Extrai a taxa apenas de uma linha de tabela do bairro informado."""
    import re

    bairro = bairro.strip()
    if not bairro:
        return 0
    pattern = re.compile(
        rf"^\s*{re.escape(bairro)}\s*(?:[:;|\-]\s*)?(?:R\$\s*)?(\d+(?:[.,]\d{{1,2}})?)\b",
        re.IGNORECASE,
    )
    for line in context.splitlines():
        match = pattern.search(line)
        if match:
            return float(match.group(1).replace(",", "."))
    return 0


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


def calcular_pedido(items: list[dict], bairro: str = "") -> dict:
    """Valida pedido via calculateOrder.

    Args:
        items: Lista de {"name": str, "quantity": int}
        bairro: Bairro de entrega (opcional, para calcular frete)

    Returns:
        dict com success, subtotal, delivery_fee, total ou error
    """
    test_mode = (os.getenv("CARDÁPIO_TEST_MODE", "true") or os.getenv("CARD_TEST_MODE", "true")).lower() == "true"

    if test_mode:
        # Modo teste: simula cálculo sem chamar API
        subtotal = 0
        for item in items:
            try:
                q = float(item.get("quantity", 0))
                p = float(item.get("price", 0))
                subtotal += q * p
            except (ValueError, TypeError):
                pass

        # Busca frete no knowledge se tiver bairro
        delivery_fee = 0
        if bairro:
            try:
                import knowledge_rag
                import re
                # Tenta varias buscas para encontrar o frete
                search_queries = [
                    bairro,
                    f"frete {bairro}",
                    f"frete entrega {bairro}",
                    f"tabela frete {bairro}",
                ]
                result = ""
                for q in search_queries:
                    result = knowledge_rag.search_context(q, top_k=3)
                    if result and bairro.lower() in result.lower():
                        break
                
                if result and bairro.lower() in result.lower():
                    # Log completo do resultado para debug
                    print(f"[cardápio] MODO TESTE - knowledge retornado para '{bairro}':")
                    print(f"[cardápio] CONTEUDO COMPLETO: {result}")
                    
                    # Tenta extrair valor do frete do resultado
                    bairro_lower = bairro.lower().strip()
                    
                    # 1. Busca linha que contenha o bairro (case-insensitive)
                    lines = result.split('\n')
                    for line in lines:
                        # Somente uma linha de tabela iniciada pelo bairro e
                        # valida. Isso exclui exemplos como "50 un ... Olaria".
                        if re.match(rf'^\s*{re.escape(bairro_lower)}(?:\s|:|\||$)', line, re.IGNORECASE):
                            # Tenta com R$: "Aponia: R$ 8,00" ou "Aponia ... R$ 8,00"
                            m = re.search(r'R\$\s*(\d+[.,]?\d*)', line)
                            if not m:
                                # Tenta sem R$: "Aponia 8" ou "Aponia 8,00"
                                m = re.search(r'(\d+[.,]?\d+)', line)
                            if m:
                                delivery_fee = float(m.group(1).replace(',', '.'))
                                print(f"[cardápio] MODO TESTE - frete EXTRAIDO: {bairro} = R$ {delivery_fee} (regex: {m.group(0)})")
                                break
                    
                    # 2. Se nao encontrou, tenta regex geral
                    if delivery_fee == 0:
                        # Procura "bairro ... R$ valor" ou "bairro valor"
                        m = re.search(rf'{re.escape(bairro)}[^0-9\n]*R\$\s*(\d+[.,]?\d*)', result, re.IGNORECASE)
                        if m:
                            delivery_fee = float(m.group(1).replace(',', '.'))
                            print(f"[cardápio] MODO TESTE - frete encontrado (regex): {bairro} = R$ {delivery_fee}")
                        else:
                            m = re.search(rf'{re.escape(bairro)}\s+(\d+[.,]?\d+)', result, re.IGNORECASE)
                            if m:
                                delivery_fee = float(m.group(1).replace(',', '.'))
                                print(f"[cardápio] MODO TESTE - frete encontrado (regex2): {bairro} = R$ {delivery_fee}")
                    
                    if delivery_fee == 0:
                        print(f"[cardápio] MODO TESTE - frete NAO encontrado para: {bairro}")
                        print(f"[cardápio] MODO TESTE - conteudo retornado: {result[:300]}")
                        frete_nao_encontrado = True
                else:
                    print(f"[cardápio] MODO TESTE - knowledge vazio para: {bairro}")
                    frete_nao_encontrado = True
            except Exception as e:
                print(f"[cardápio] MODO TESTE - erro ao buscar frete: {e}")
                frete_nao_encontrado = True

        # Se o RAG trouxe apenas exemplos de prompt, busca a tabela completa
        # antes de aceitar frete zero. Nao reutiliza numeros de exemplos.
        if bairro and delivery_fee == 0:
            try:
                import knowledge_rag
                delivery_fee = _extract_delivery_fee(
                    knowledge_rag.get_full_knowledge_content(), bairro
                )
                if delivery_fee > 0:
                    frete_nao_encontrado = False
            except Exception as e:
                print(f"[cardÃ¡pio] MODO TESTE - erro no fallback de frete: {e}")

        total = subtotal + delivery_fee
        print(f"[cardápio] MODO TESTE - calculateOrder simulado: subtotal={subtotal} frete={delivery_fee} total={total}")
        return {
            "success": True,
            "subtotal": subtotal,
            "delivery_fee": delivery_fee,
            "return_fee": 0,
            "total": total,
            "frete_nao_encontrado": frete_nao_encontrado if 'frete_nao_encontrado' in locals() else False,
        }

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
