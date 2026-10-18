"""CEP -> rua e bairro (pedido do Eduardo, 18/10).

O cliente manda "76804-120" e a Sandrinha ja sabe a rua e o bairro: so falta
perguntar o numero da casa. A consulta e na ViaCEP e, se ela falhar, na
BrasilAPI - as duas sao publicas e gratuitas. Sem resposta das duas (rede fora,
CEP generico da cidade), nada e preenchido e o fluxo segue perguntando o
endereco como sempre.
"""
import re

import requests

TIMEOUT_SEG = 3
CIDADE_ATENDIDA = "porto velho"

# "CEP 76804120", "76804-120", "76.804-120". Sem a palavra CEP, so o formato
# com hifen ou um numero de 8 digitos de Porto Velho (768xx-xxx): qualquer
# outro numero de 8 digitos pode ser telefone ou valor.
_RX_CEP = re.compile(
    r"\bcep\s*:?\s*(\d{2})\.?(\d{3})-?(\d{3})\b"
    r"|\b(\d{2})\.?(\d{3})-(\d{3})\b"
    r"|\b(768)(\d{2})(\d{3})\b",
    re.IGNORECASE)

_CACHE: dict[str, dict | None] = {}


def cep_na_mensagem(texto: str) -> str:
    """Os 8 digitos do CEP citado, ou ''."""
    m = _RX_CEP.search(texto or "")
    if not m:
        return ""
    return "".join(g for g in m.groups() if g)


def tirar_cep(texto: str) -> str:
    """O texto sem o CEP: os digitos dele nao podem virar quantidade nem numero."""
    return _RX_CEP.sub(" ", texto or "")


def _via_cep(cep: str) -> dict | None:
    r = requests.get(f"https://viacep.com.br/ws/{cep}/json/", timeout=TIMEOUT_SEG)
    d = r.json() if r.ok else {}
    if not d or d.get("erro"):
        return None
    return {"rua": d.get("logradouro") or "", "bairro": d.get("bairro") or "",
            "cidade": d.get("localidade") or "", "uf": d.get("uf") or ""}


def _brasil_api(cep: str) -> dict | None:
    r = requests.get(f"https://brasilapi.com.br/api/cep/v1/{cep}", timeout=TIMEOUT_SEG)
    d = r.json() if r.ok else {}
    if not d or not d.get("cep"):
        return None
    return {"rua": d.get("street") or "", "bairro": d.get("neighborhood") or "",
            "cidade": d.get("city") or "", "uf": d.get("state") or ""}


def buscar(cep: str) -> dict | None:
    """{'rua', 'bairro', 'cidade', 'uf'} do CEP, ou None quando nao achou."""
    cep = re.sub(r"\D", "", cep or "")
    if len(cep) != 8:
        return None
    if cep in _CACHE:
        return _CACHE[cep]
    achado, respondeu = None, False
    for fonte in (_via_cep, _brasil_api):
        try:
            achado = fonte(cep)
            respondeu = True
        except Exception as e:
            print(f"[cep] {fonte.__name__} falhou para {cep}: {e}")
            continue
        if achado:
            break
    # Falha de rede nao fica no cache: na proxima mensagem tenta de novo. CEP
    # que nao existe fica, para nao consultar de novo a cada leitura.
    if respondeu:
        _CACHE[cep] = achado
    print(f"[cep] {cep} -> {achado}")
    return achado


def e_da_cidade(dados: dict | None) -> bool:
    import facts
    return bool(dados) and facts.normalize(dados.get("cidade", "")) == CIDADE_ATENDIDA
