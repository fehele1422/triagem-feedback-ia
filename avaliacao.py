"""Mede a qualidade da triagem contra um gabarito feito à mão (dados/avaliacao.csv).

  python avaliacao.py --modo regras   # baseline sem IA (roda sem chave)
  python avaliacao.py --modo llm      # agente com Claude (precisa de ANTHROPIC_API_KEY)
  python avaliacao.py --modo gemini --pausa 5   # agente com Gemini (GEMINI_API_KEY); a pausa evita o limite gratuito

Saída: acurácia de categoria, de urgência e das duas juntas, latência média e a lista de erros.
Também grava resultados/avaliacao_<modo>.json.
"""
import argparse
import csv
import json
import time
from pathlib import Path

from triagem.agente import triagem

RAIZ = Path(__file__).resolve().parent


def avaliar(modo: str, pausa: float = 0) -> dict:
    with (RAIZ / "dados" / "avaliacao.csv").open(encoding="utf-8") as f:
        casos = list(csv.DictReader(f))
    acertos_cat = acertos_urg = acertos_ambos = 0
    latencias, erros, falhas = [], [], []
    for i, c in enumerate(casos):
        if i and pausa:
            time.sleep(pausa)
        t0 = time.perf_counter()
        try:
            r = triagem(c["texto"], modo=modo, registrar=False)
        except Exception as e:
            falhas.append({"id": c["id"], "erro": str(e)})
            continue
        latencias.append(time.perf_counter() - t0)
        ok_cat, ok_urg = r["categoria"] == c["categoria"], r["urgencia"] == c["urgencia"]
        acertos_cat += ok_cat
        acertos_urg += ok_urg
        acertos_ambos += ok_cat and ok_urg
        if not (ok_cat and ok_urg):
            erros.append({"id": c["id"], "texto": c["texto"],
                          "esperado": f'{c["categoria"]}/{c["urgencia"]}',
                          "previsto": f'{r["categoria"]}/{r["urgencia"]}'})
    n = len(casos)
    return {
        "modo": modo, "casos": n, "falhas": len(falhas),
        "acuracia_categoria": round(acertos_cat / n, 3),
        "acuracia_urgencia": round(acertos_urg / n, 3),
        "acuracia_ambos": round(acertos_ambos / n, 3),
        "latencia_media_s": round(sum(latencias) / len(latencias), 3) if latencias else None,
        "erros": erros, "falhas_detalhe": falhas,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--modo", choices=["regras", "llm", "gemini"], default="regras")
    ap.add_argument("--pausa", type=float, default=0, help="segundos de espera entre casos")
    args = ap.parse_args()
    modo = args.modo
    res = avaliar(modo, args.pausa)
    (RAIZ / "resultados" / f"avaliacao_{modo}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Modo: {modo} | casos: {res['casos']} | falhas: {res['falhas']}")
    print(f"Categoria: {res['acuracia_categoria']:.0%} | Urgência: {res['acuracia_urgencia']:.0%} "
          f"| Ambas: {res['acuracia_ambos']:.0%} | Latência média: {res['latencia_media_s']}s")
    for e in res["erros"]:
        print(f'  #{e["id"]:>2} esperado {e["esperado"]:<26} previsto {e["previsto"]:<26} {e["texto"][:55]}')
