"""
llm.py — Cliente Gemini via SDK novo unificado google-genai.
Suporta function calling para busca seletiva de knowledge.
"""

import os
from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_client = genai.Client(api_key=GEMINI_API_KEY)


def generate_reply(
    history: list[dict],
    system: str,
    tools: list | None = None,
    tool_handlers: dict | None = None,
) -> str:
    """Chama Gemini com suporte a function calling.

    Se Gemini pedir uma tool, executa localmente e retorna o resultado
    para gerar a resposta final.
    """
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=1024,
        tools=tools,
    )

    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=history,
        config=config,
    )

    # Verifica se Gemini quer chamar alguma tool
    if tools and tool_handlers and response.candidates:
        parts = response.candidates[0].content.parts
        has_function_call = any(hasattr(p, "function_call") and p.function_call for p in parts)

        if has_function_call:
            # Adiciona a resposta do modelo (com as chamadas de tool) ao histórico
            history.append({"role": "model", "parts": _parts_to_dict(parts)})

            # Processa cada function call
            for part in parts:
                if hasattr(part, "function_call") and part.function_call:
                    fn_name = part.function_call.name
                    fn_args = dict(part.function_call.args) if part.function_call.args else {}

                    if fn_name in tool_handlers:
                        result = tool_handlers[fn_name](**fn_args)
                    else:
                        result = f"Função {fn_name} não encontrada"

                    print(f"[llm] tool_call={fn_name} args={fn_args} result_len={len(str(result))}")

                    # Adiciona resultado da tool ao histórico
                    history.append({
                        "role": "user",
                        "parts": [{
                            "function_response": {
                                "name": fn_name,
                                "response": {"result": result},
                            }
                        }],
                    })

            # Gera resposta final com o resultado da tool
            response = _client.models.generate_content(
                model=GEMINI_MODEL,
                contents=history,
                config=config,
            )

    text = (response.text or "").strip()
    print(f"[llm] reply len={len(text)}")
    return text


def _parts_to_dict(parts) -> list[dict]:
    """Converte parts do Gemini para formato serializável no histórico."""
    result = []
    for p in parts:
        if hasattr(p, "text") and p.text:
            result.append({"text": p.text})
        elif hasattr(p, "function_call") and p.function_call:
            result.append({
                "function_call": {
                    "name": p.function_call.name,
                    "args": dict(p.function_call.args) if p.function_call.args else {},
                }
            })
    return result
