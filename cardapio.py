"""
cardapio.py — Cliente HTTP para a API do cardapio digital BALBS.
Gerencia envio de pedidos (createOrderFromAgent) e validacao (calculateOrder).
"""

import os
import json
import httpx


def _get_base_url() -> str:
    test_mode = os.getenv("CARDÁPIO_TEST_MODE", "true").lower() == "true"
    if test_mode:
        return os.getenv("CARDÁPIO_API_URL_TEST", os.getenv("CARDÁPIO_API_URL", "")).rstrip("/")
    return os.getenv("CARDÁPIO_API_URL", "").rstrip("/")


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
    test_mode = os.getenv("CARDÁPIO_TEST_MODE", "true").lower() == "true"

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
        print(f"[cardápio] MODO TESTE - calculateOrder simulado: subtotal={subtotal}")
        return {
            "success": True,
            "subtotal": subtotal,
            "delivery_fee": 0,
            "return_fee": 0,
            "total": subtotal,
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
