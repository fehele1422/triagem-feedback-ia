"""Agente de triagem: LLM (Claude ou Gemini) + ferramentas, em um loop simples.

Fluxo (tool use):
  1. mandamos o feedback + a lista de ferramentas ao modelo;
  2. se ele responder com `tool_use`, executamos a ferramenta e devolvemos o `tool_result`;
  3. repetimos até ele dar a resposta final (JSON) ou estourar o limite de passos.

Só usa a biblioteca padrão (urllib): não precisa instalar nada.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import ferramentas
from .prompts import CATEGORIAS, SYSTEM, TOOLS_SCHEMA, URGENCIAS
from .regras import classificar_regras

API_URL = "https://api.anthropic.com/v1/messages"
MODELO = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
MAX_PASSOS = 5

# Gemini (Google AI Studio tem camada gratuita, sem cartão). Se o modelo for aposentado (erro 404),
# veja o nome atual no AI Studio e exporte GEMINI_MODEL.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
MODELO_GEMINI = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
# Modelos novos "pensam" antes de responder e podem demorar; por isso o limite de espera é generoso.
TIMEOUT_GEMINI = int(os.environ.get("GEMINI_TIMEOUT", "120"))


def chamar_claude(mensagens: list[dict]) -> dict:
    """Uma chamada à Messages API da Anthropic (HTTP puro)."""
    chave = os.environ.get("ANTHROPIC_API_KEY")
    if not chave:
        raise RuntimeError("Defina ANTHROPIC_API_KEY (veja .env.example).")
    corpo = {"model": MODELO, "max_tokens": 600, "system": SYSTEM,
             "tools": TOOLS_SCHEMA, "messages": mensagens}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(corpo).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": chave,
                 "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API respondeu {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e


def _limpar_blocos(blocos: list[dict]) -> list[dict]:
    """Mantém só os campos que a API aceita de volta no histórico."""
    saida = []
    for b in blocos:
        if b["type"] == "text":
            saida.append({"type": "text", "text": b["text"]})
        elif b["type"] == "tool_use":
            saida.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]})
    return saida


def extrair_json(texto: str) -> dict:
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini == -1 or fim <= ini:
        raise ValueError(f"resposta sem JSON: {texto[:120]!r}")
    return json.loads(texto[ini:fim + 1])


def validar(saida: dict) -> dict:
    """Não confiamos cegamente no modelo: valores fora da lista viram um padrão seguro."""
    categoria = saida.get("categoria") if saida.get("categoria") in CATEGORIAS else "outro"
    urgencia = saida.get("urgencia") if saida.get("urgencia") in URGENCIAS else "media"
    return {"categoria": categoria, "urgencia": urgencia,
            "resposta_sugerida": str(saida.get("resposta_sugerida", ""))}


def triagem_llm(texto: str, registrar: bool = True) -> dict:
    mensagens = [{"role": "user", "content": texto}]
    passos = []
    for _ in range(MAX_PASSOS):
        resp = chamar_claude(mensagens)
        mensagens.append({"role": "assistant", "content": _limpar_blocos(resp["content"])})
        if resp.get("stop_reason") != "tool_use":
            final = "".join(b["text"] for b in resp["content"] if b["type"] == "text")
            return {**validar(extrair_json(final)), "modo": "llm", "passos": passos}
        resultados = []
        for b in resp["content"]:
            if b["type"] == "tool_use":
                saida = ferramentas.executar(b["name"], b["input"], registrar=registrar)
                passos.append({"ferramenta": b["name"], "entrada": b["input"], "saida": saida})
                resultados.append({"type": "tool_result", "tool_use_id": b["id"],
                                   "content": json.dumps(saida, ensure_ascii=False)})
        mensagens.append({"role": "user", "content": resultados})
    raise RuntimeError(f"agente passou de {MAX_PASSOS} passos sem resposta final")


def chamar_gemini(conteudos: list[dict]) -> dict:
    """Uma chamada à API do Gemini (HTTP puro). Tenta de novo se o limite gratuito estourar (429)."""
    chave = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not chave:
        raise RuntimeError("Defina GEMINI_API_KEY (veja .env.example).")
    corpo = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": conteudos,
        "tools": [{"functionDeclarations": [
            {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
            for t in TOOLS_SCHEMA]}],
        "generationConfig": {"temperature": 0},
    }
    for tentativa in range(5):
        req = urllib.request.Request(
            GEMINI_URL.format(modelo=MODELO_GEMINI),
            data=json.dumps(corpo).encode("utf-8"),
            headers={"content-type": "application/json", "x-goog-api-key": chave},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_GEMINI) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and tentativa < 4:  # 503 = Google sobrecarregado; costuma passar
                time.sleep(5 * (tentativa + 1))
                continue
            raise RuntimeError(f"Gemini respondeu {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
        except (TimeoutError, urllib.error.URLError) as e:  # demora ou queda de rede: tenta de novo
            if tentativa < 2:
                time.sleep(3)
                continue
            raise RuntimeError(f"Gemini não respondeu a tempo ({TIMEOUT_GEMINI}s): {e}") from e
    raise RuntimeError("Gemini: tentativas esgotadas")


def triagem_gemini(texto: str, registrar: bool = True) -> dict:
    """Mesmo agente, outro provedor. Muda só o formato das mensagens e das ferramentas."""
    conteudos = [{"role": "user", "parts": [{"text": texto}]}]
    passos = []
    for _ in range(MAX_PASSOS):
        resp = chamar_gemini(conteudos)
        try:
            conteudo = resp["candidates"][0]["content"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Gemini sem resposta utilizável: {str(resp)[:200]}")
        conteudo.setdefault("role", "model")
        conteudos.append(conteudo)  # devolvido por inteiro, como veio (alguns modelos exigem isso)
        partes = conteudo.get("parts", [])
        chamadas = [p["functionCall"] for p in partes if "functionCall" in p]
        if not chamadas:
            final = "".join(p.get("text", "") for p in partes)
            return {**validar(extrair_json(final)), "modo": "gemini", "passos": passos}
        respostas = []
        for c in chamadas:
            saida = ferramentas.executar(c["name"], c.get("args", {}), registrar=registrar)
            passos.append({"ferramenta": c["name"], "entrada": c.get("args", {}), "saida": saida})
            respostas.append({"functionResponse": {"name": c["name"], "response": {"result": saida}}})
        conteudos.append({"role": "user", "parts": respostas})
    raise RuntimeError(f"agente passou de {MAX_PASSOS} passos sem resposta final")


def triagem_regras(texto: str, registrar: bool = True) -> dict:
    """Mesmo contrato do agente, sem IA: útil sem chave de API e como baseline."""
    r = classificar_regras(texto)
    passos = []
    m = re.search(r"pedido\s*#?(\d+)", texto.lower())
    if m:
        passos.append({"ferramenta": "consultar_pedido", "entrada": {"pedido_id": m.group(1)},
                       "saida": ferramentas.consultar_pedido(m.group(1))})
    if r["urgencia"] == "alta":
        ch = ferramentas.abrir_chamado(r["categoria"], "alta", texto[:120], registrar=registrar)
        passos.append({"ferramenta": "abrir_chamado", "entrada": {"categoria": r["categoria"]}, "saida": ch})
    return {**r, "modo": "regras", "passos": passos}


def provedor_padrao() -> str:
    """Escolhe pelo que estiver configurado: Claude, depois Gemini, senão regras (sem IA)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "llm"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    return "regras"


def triagem(texto: str, modo: str | None = None, registrar: bool = True) -> dict:
    """modo: 'llm' (Claude), 'gemini', 'regras' ou None (detecta pela chave configurada)."""
    modo = modo or provedor_padrao()
    funcoes = {"llm": triagem_llm, "gemini": triagem_gemini, "regras": triagem_regras}
    return funcoes[modo](texto, registrar=registrar)
