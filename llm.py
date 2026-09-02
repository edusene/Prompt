"""
llm.py — Cliente Gemini via SDK novo unificado google-genai.
Preparado para function calling futuro via parâmetro tools=None.
"""

import os
from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_client = genai.Client(api_key=GEMINI_API_KEY)


def generate_reply(history: list[dict], system: str, tools: list | None = None) -> str:
    """
    Chama Gemini generate_content com o histórico completo.

    tools=None hoje; preparado para function calling depois.
    Quando tools for usado:
      - Passe tools=[types.Tool(function_declarations=[...])]
      - Verifique response.candidates[0].content.parts procurando part.function_call
      - Execute a função local e adicione o resultado ao histórico como role "tool"
    """
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=1024,
        tools=tools,  # None hoje
    )
    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=history,
        config=config,
    )
    # TODO: handle function_call parts quando tools for usado
    text = (response.text or "").strip()
    print(f"[llm] reply len={len(text)}")
    return text
