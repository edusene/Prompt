"""
llm.py — Cliente OpenAI para geração de respostas.
Suporta function calling para busca seletiva de knowledge.
"""

import os
import json
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

_client = OpenAI(api_key=OPENAI_API_KEY)


def _convert_history(history: list[dict]) -> list[dict]:
    """Converte histórico do formato interno para formato OpenAI.

    Formato interno: {"role": "user"|"assistant", "parts": [{"text": "..."}, ...]}
    Formato OpenAI:  {"role": "user"|"assistant", "content": "..." | [...]}
    """
    messages = []
    for msg in history:
        role = msg.get("role", "user")
        parts = msg.get("parts", [])

        # Verifica se tem conteúdo multimodal (inline_data / image_url)
        has_media = any(
            "inline_data" in p or "image_url" in p
            for p in parts
        )

        if has_media:
            # Formato multimodal OpenAI: content é uma lista
            content_parts = []
            for p in parts:
                if "text" in p:
                    content_parts.append({"type": "text", "text": p["text"]})
                elif "image_url" in p:
                    # Já está no formato OpenAI
                    content_parts.append(p)
                elif "inline_data" in p:
                    mime = p["inline_data"].get("mime_type", "")
                    data = p["inline_data"].get("data", "")
                    if mime.startswith("image/"):
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{data}",
                                "detail": "low",
                            },
                        })
                    elif mime == "application/pdf":
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{data}",
                            },
                        })
            if not content_parts:
                content_parts.append({"type": "text", "text": "[Midia nao processada]"})
            messages.append({"role": role, "content": content_parts})
        else:
            # Texto puro: concatena todos os parts de texto
            text = "\n".join(p.get("text", "") for p in parts if "text" in p)
            messages.append({"role": role, "content": text})

    return messages


def generate_reply(
    history: list[dict],
    system: str,
    tools: list | None = None,
    tool_handlers: dict | None = None,
) -> str:
    """Chama OpenAI com suporte a function calling.

    Se o modelo pedir uma tool, executa localmente e retorna o resultado
    para gerar a resposta final.
    """
    messages = [{"role": "system", "content": system}]
    messages.extend(_convert_history(history))

    kwargs = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "max_completion_tokens": 1024,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
        kwargs["reasoning_effort"] = "none"

    response = _client.chat.completions.create(**kwargs)
    choice = response.choices[0]

    # Log de cache hits (prompt caching)
    if hasattr(response, 'usage') and response.usage:
        _cached = getattr(response.usage.prompt_tokens_details, 'cached_tokens', 0) if response.usage.prompt_tokens_details else 0
        _total = response.usage.prompt_tokens or 0
        if _total > 0:
            print(f"[llm] cache tokens: {_cached}/{_total} cached ({_cached*100//_total}% hit)")

    # Loop de tool calling: permite múltiplos rounds de tools
    max_tool_rounds = 5
    rounds = 0
    while (
        tools
        and tool_handlers
        and choice.finish_reason == "tool_calls"
        and choice.message.tool_calls
        and rounds < max_tool_rounds
    ):
        rounds += 1
        # Adiciona a mensagem do assistente (com as chamadas de tool) ao histórico
        messages.append(choice.message.model_dump())

        for tool_call in choice.message.tool_calls:
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}

            if fn_name in tool_handlers:
                result = tool_handlers[fn_name](**fn_args)
            else:
                result = f"Função {fn_name} não encontrada"

            print(f"[llm] tool_call={fn_name} args={fn_args} result_len={len(str(result))} round={rounds}")

            # Adiciona resultado da tool ao histórico
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result),
            })

        # Gera resposta final (ou próxima tool call) com o resultado da tool
        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_completion_tokens=1024,
        )
        choice = response.choices[0]

        # Log de cache hits no tool calling loop
        if hasattr(response, 'usage') and response.usage:
            _cached = getattr(response.usage.prompt_tokens_details, 'cached_tokens', 0) if response.usage.prompt_tokens_details else 0
            _total = response.usage.prompt_tokens or 0
            if _total > 0:
                print(f"[llm] cache tokens (tool round {rounds}): {_cached}/{_total} cached ({_cached*100//_total}% hit)")

    text = (choice.message.content or "").strip()
    print(f"[llm] reply len={len(text)}")
    return text
