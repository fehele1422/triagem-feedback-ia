"""Uso: python -m triagem "Meu pedido 1001 chegou 2 horas atrasado" """
import json
import sys

from .agente import triagem

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('uso: python -m triagem "texto do feedback"')
    print(json.dumps(triagem(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
