"""Baseline SEM IA: regras de palavra-chave.

Serve de ponto de comparação: se o LLM não for melhor que isto, não vale o custo.
"""
import re

from .prompts import CATEGORIAS

# A ordem importa: a primeira regra que bater define a categoria.
_CATEGORIA_KW = [
    ("cobranca", ["cobrad", "cobranc", "cobraram", "duas vezes", "duplicad", "estorno", "reembolso"]),
    ("item_faltando", ["falt", "esquec", "veio sem", "não veio", "nao veio"]),
    ("atraso", ["atras", "demor", "horas", "não chegou", "nao chegou"]),
    ("qualidade_comida", ["frio", "fria", "estragad", "cru", "queimad", "cabelo", "azedo"]),
    ("app_bug", ["app", "aplicativo", "travou", "erro", "tela", "não abre", "nao abre", "não carrega"]),
    ("elogio", ["parabéns", "parabens", "excelente", "ótimo", "otimo", "adorei", "amei", "perfeito"]),
]
_ALTA_KW = ["alergi", "passando mal", "passei mal", "passar mal", "intoxic", "cabelo", "inseto",
            "barata", "estragad", "duas vezes", "duplicad", "2 horas", "duas horas"]


def _tem(texto: str, palavras: list[str]) -> bool:
    return any(re.search(r"\b" + re.escape(p), texto) for p in palavras)


def classificar_regras(texto: str) -> dict:
    t = texto.lower()
    categoria = "outro"
    for nome, palavras in _CATEGORIA_KW:
        if _tem(t, palavras):
            categoria = nome
            break
    if _tem(t, _ALTA_KW):
        urgencia = "alta"
    elif categoria == "elogio":
        urgencia = "baixa"
    else:
        urgencia = "media"
    assert categoria in CATEGORIAS
    return {"categoria": categoria, "urgencia": urgencia, "resposta_sugerida": ""}
