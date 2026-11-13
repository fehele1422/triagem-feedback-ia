"""Ferramentas que o agente pode chamar (tool use).

São funções comuns de Python. O LLM não executa nada: ele só PEDE a chamada,
e o nosso código decide executar, validando os argumentos.
"""
import json
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PEDIDOS = json.loads((RAIZ / "dados" / "pedidos.json").read_text(encoding="utf-8"))
ARQUIVO_CHAMADOS = RAIZ / "resultados" / "chamados.jsonl"


def consultar_pedido(pedido_id: str) -> dict:
    return PEDIDOS.get(str(pedido_id).strip(), {"erro": "pedido não encontrado"})


def abrir_chamado(categoria: str, urgencia: str, resumo: str, registrar: bool = True) -> dict:
    chamado = {
        "id": f"CH-{uuid.uuid4().hex[:6].upper()}",
        "categoria": categoria,
        "urgencia": urgencia,
        "resumo": resumo,
        "criado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if registrar:  # na avaliação usamos registrar=False para não poluir o arquivo
        with ARQUIVO_CHAMADOS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(chamado, ensure_ascii=False) + "\n")
    return chamado


def executar(nome: str, argumentos: dict, registrar: bool = True) -> dict:
    """Despacha a chamada pedida pelo modelo; erros viram resultado, não exceção."""
    try:
        if nome == "consultar_pedido":
            return consultar_pedido(argumentos["pedido_id"])
        if nome == "abrir_chamado":
            return abrir_chamado(argumentos["categoria"], argumentos["urgencia"],
                                 argumentos["resumo"], registrar=registrar)
        return {"erro": f"ferramenta desconhecida: {nome}"}
    except (KeyError, TypeError) as e:
        return {"erro": f"argumentos inválidos: {e}"}
