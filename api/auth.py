"""Autenticación por empresa: aquí es donde se implementa el aislamiento.

Cada request autenticado trae un `empresa_id` derivado del token. Ningún
endpoint de datos acepta `empresa_id` como parámetro de query — siempre
sale del token, nunca de lo que el cliente diga que es. Así una empresa no
puede ver los leads de otra simplemente cambiando un parámetro en la URL.
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from src.config import CFG


def empresa_autenticada(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    empresa_id = CFG.tokens_empresa.get(token)
    if not empresa_id:
        raise HTTPException(status_code=401, detail="Token inválido o ausente.")
    return empresa_id


def admin_autenticado(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not CFG.admin_token or token != CFG.admin_token:
        raise HTTPException(status_code=401, detail="Token de administrador inválido o ausente.")
