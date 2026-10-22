"""
auth.py — Login/logout simples via sessão Flask.
Usuário/senha definidos em variáveis de ambiente (.env).
"""

import os
from functools import wraps

from flask import session, redirect, url_for, request

import hmac

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
# Sem padrao: o repositorio e publico (22/10) e a senha de exemplo do
# .env.example abriria o painel para qualquer um. Sem ADMIN_PASSWORD no
# ambiente, ou com a de exemplo, ninguem entra.
_SENHA_DE_EXEMPLO = "troque_esta_senha"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
if not ADMIN_PASSWORD or ADMIN_PASSWORD == _SENHA_DE_EXEMPLO:
    print("[auth] ATENCAO: ADMIN_PASSWORD ausente ou igual ao exemplo - login do painel bloqueado")
    ADMIN_PASSWORD = ""


def login_required(f):
    """Decorator que redireciona para /login se não autenticado."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def check_credentials(username: str, password: str) -> bool:
    """Verifica se as credenciais são válidas."""
    if not ADMIN_PASSWORD:
        return False
    return (hmac.compare_digest((username or "").encode(), ADMIN_USER.encode())
            and hmac.compare_digest((password or "").encode(), ADMIN_PASSWORD.encode()))


def do_login(username: str, password: str) -> bool:
    """Tenta fazer login. Retorna True se bem-sucedido."""
    if check_credentials(username, password):
        session["logged_in"] = True
        session["username"] = username
        return True
    return False


def do_logout() -> None:
    """Encerra a sessão."""
    session.pop("logged_in", None)
    session.pop("username", None)
