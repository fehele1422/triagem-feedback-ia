"""API HTTP mínima (sem framework) para expor o agente.

  POST /triagem  {"texto": "..."}  ->  {"categoria", "urgencia", "resposta_sugerida", "modo", "passos"}
  GET  /saude                      ->  {"status": "ok", "modo": "llm"|"gemini"|"regras"}

É o que o n8n (ou qualquer sistema) chama. Poderia ser FastAPI; escolhi a biblioteca padrão
para o projeto rodar sem instalar nada.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .agente import provedor_padrao, triagem


class Handler(BaseHTTPRequestHandler):
    def _responder(self, codigo: int, corpo: dict):
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        if self.path == "/saude":
            return self._responder(200, {"status": "ok", "modo": provedor_padrao()})
        self._responder(404, {"erro": "rota não encontrada"})

    def do_POST(self):
        if self.path != "/triagem":
            return self._responder(404, {"erro": "rota não encontrada"})
        try:
            tamanho = int(self.headers.get("Content-Length", 0))
            texto = json.loads(self.rfile.read(tamanho) or b"{}").get("texto", "")
        except (ValueError, json.JSONDecodeError):
            return self._responder(400, {"erro": "JSON inválido"})
        if not isinstance(texto, str) or not texto.strip():
            return self._responder(400, {"erro": "campo 'texto' é obrigatório"})
        if len(texto) > 2000:
            return self._responder(413, {"erro": "texto acima de 2000 caracteres"})
        try:
            self._responder(200, triagem(texto))
        except Exception as e:  # falha do provedor não derruba o servidor
            self._responder(502, {"erro": f"falha ao processar: {e}"})

    def log_message(self, fmt, *args):  # logs mais limpos
        print("[api]", fmt % args)


def criar_servidor(porta: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("0.0.0.0", porta), Handler)


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 8000))
    print(f"API em http://localhost:{porta}  (POST /triagem, GET /saude)")
    criar_servidor(porta).serve_forever()
