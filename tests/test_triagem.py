"""Testes sem rede: o Claude é SUBSTITUÍDO por um dublê que devolve respostas pré-definidas.

Rodar: python -m unittest discover -s tests -v
"""
import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triagem import agente, ferramentas  # noqa: E402
from triagem.api import criar_servidor  # noqa: E402
from triagem.regras import classificar_regras  # noqa: E402


def _bloco_texto(t):
    return {"type": "text", "text": t}


class TestRegras(unittest.TestCase):
    def test_cabelo_e_alta(self):
        r = classificar_regras("Encontrei um cabelo na minha comida")
        self.assertEqual((r["categoria"], r["urgencia"]), ("qualidade_comida", "alta"))

    def test_elogio_e_baixa(self):
        self.assertEqual(classificar_regras("Amei o lanche")["urgencia"], "baixa")


class TestFerramentas(unittest.TestCase):
    def test_pedido_existente_e_inexistente(self):
        self.assertEqual(ferramentas.consultar_pedido("1001")["real_min"], 98)
        self.assertIn("erro", ferramentas.consultar_pedido("9999"))

    def test_argumentos_invalidos_viram_erro_nao_excecao(self):
        self.assertIn("erro", ferramentas.executar("abrir_chamado", {"categoria": "atraso"}))

    def test_chamado_sem_registrar_nao_escreve(self):
        antes = ferramentas.ARQUIVO_CHAMADOS.exists()
        ferramentas.abrir_chamado("atraso", "alta", "teste", registrar=False)
        self.assertEqual(ferramentas.ARQUIVO_CHAMADOS.exists(), antes)


class TestAgenteLLM(unittest.TestCase):
    def test_loop_usa_ferramenta_e_devolve_json_validado(self):
        respostas = [
            {"stop_reason": "tool_use", "content": [
                _bloco_texto("Vou consultar."),
                {"type": "tool_use", "id": "t1", "name": "consultar_pedido", "input": {"pedido_id": "1003"}}]},
            {"stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": "t2", "name": "abrir_chamado",
                 "input": {"categoria": "atraso", "urgencia": "alta", "resumo": "125 min"}}]},
            {"stop_reason": "end_turn", "content": [_bloco_texto(
                'Segue: {"categoria": "atraso", "urgencia": "alta", "resposta_sugerida": "Sentimos muito."}')]},
        ]
        with mock.patch.object(agente, "chamar_claude", side_effect=respostas) as m:
            r = agente.triagem_llm("Pedido 1003 não chegou", registrar=False)
        self.assertEqual((r["categoria"], r["urgencia"]), ("atraso", "alta"))
        self.assertEqual([p["ferramenta"] for p in r["passos"]], ["consultar_pedido", "abrir_chamado"])
        self.assertEqual(r["passos"][0]["saida"]["real_min"], 125)
        # o resultado da ferramenta foi devolvido ao modelo no formato tool_result,
        # amarrado ao id do pedido de chamada (o mock guarda a MESMA lista, já completa)
        historico = m.call_args_list[-1].args[0]
        resultados = [b for msg in historico if isinstance(msg["content"], list)
                      for b in msg["content"] if b["type"] == "tool_result"]
        self.assertEqual([b["tool_use_id"] for b in resultados], ["t1", "t2"])
        self.assertEqual(json.loads(resultados[0]["content"])["real_min"], 125)

    def test_valores_fora_da_lista_sao_corrigidos(self):
        resp = {"stop_reason": "end_turn", "content": [_bloco_texto(
            '{"categoria": "inventada", "urgencia": "urgentissima", "resposta_sugerida": "x"}')]}
        with mock.patch.object(agente, "chamar_claude", return_value=resp):
            r = agente.triagem_llm("qualquer coisa", registrar=False)
        self.assertEqual((r["categoria"], r["urgencia"]), ("outro", "media"))

    def test_limite_de_passos(self):
        loop = {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "x", "name": "consultar_pedido", "input": {"pedido_id": "1001"}}]}
        with mock.patch.object(agente, "chamar_claude", return_value=loop):
            with self.assertRaises(RuntimeError):
                agente.triagem_llm("loop infinito", registrar=False)


class TestAgenteGemini(unittest.TestCase):
    def test_loop_com_function_call(self):
        respostas = [
            {"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "consultar_pedido", "args": {"pedido_id": "1003"}}}]}}]},
            {"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "abrir_chamado",
                                  "args": {"categoria": "atraso", "urgencia": "alta", "resumo": "125 min"}}}]}}]},
            {"candidates": [{"content": {"role": "model", "parts": [
                {"text": '{"categoria": "atraso", "urgencia": "alta", "resposta_sugerida": "Sentimos muito."}'}]}}]},
        ]
        with mock.patch.object(agente, "chamar_gemini", side_effect=respostas) as m:
            r = agente.triagem_gemini("Pedido 1003 não chegou", registrar=False)
        self.assertEqual((r["categoria"], r["urgencia"], r["modo"]), ("atraso", "alta", "gemini"))
        self.assertEqual([p["ferramenta"] for p in r["passos"]], ["consultar_pedido", "abrir_chamado"])
        historico = m.call_args_list[-1].args[0]
        retornos = [p["functionResponse"] for c in historico for p in c["parts"] if "functionResponse" in p]
        self.assertEqual(retornos[0]["response"]["result"]["real_min"], 125)

    def test_formato_da_requisicao_http(self):
        capturado = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"candidates": []}'

        def falso_urlopen(req, timeout=None):
            capturado["url"], capturado["chave"] = req.full_url, req.get_header("X-goog-api-key")
            capturado["corpo"] = json.loads(req.data)
            return Resp()

        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "chave-falsa"}):
            with mock.patch("urllib.request.urlopen", falso_urlopen):
                agente.chamar_gemini([{"role": "user", "parts": [{"text": "oi"}]}])
        self.assertIn(":generateContent", capturado["url"])
        self.assertEqual(capturado["chave"], "chave-falsa")
        nomes = [d["name"] for d in capturado["corpo"]["tools"][0]["functionDeclarations"]]
        self.assertEqual(nomes, ["consultar_pedido", "abrir_chamado"])
        self.assertIn("systemInstruction", capturado["corpo"])

    def test_timeout_tenta_de_novo_e_depois_funciona(self):
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}'

        chamadas = {"n": 0}

        def urlopen_lento(req, timeout=None):
            chamadas["n"] += 1
            if chamadas["n"] < 3:
                raise TimeoutError("The read operation timed out")
            return Resp()

        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "x"}):
            with mock.patch("urllib.request.urlopen", urlopen_lento), mock.patch("time.sleep"):
                resp = agente.chamar_gemini([{"role": "user", "parts": [{"text": "oi"}]}])
        self.assertEqual(chamadas["n"], 3)
        self.assertIn("candidates", resp)

    def test_timeout_persistente_vira_erro_claro(self):
        def sempre_lento(req, timeout=None):
            raise TimeoutError("The read operation timed out")

        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "x"}):
            with mock.patch("urllib.request.urlopen", sempre_lento), mock.patch("time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "não respondeu a tempo"):
                    agente.chamar_gemini([])

    def test_sem_chave_da_erro_claro(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                agente.chamar_gemini([])

    def test_provedor_padrao(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(agente.provedor_padrao(), "regras")
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "x"}, clear=True):
            self.assertEqual(agente.provedor_padrao(), "gemini")
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "x", "ANTHROPIC_API_KEY": "y"}, clear=True):
            self.assertEqual(agente.provedor_padrao(), "llm")


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = criar_servidor(0)
        cls.porta = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, corpo: bytes):
        req = urllib.request.Request(f"http://127.0.0.1:{self.porta}/triagem", data=corpo,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_triagem_ok_em_modo_regras(self):
        # sem NENHUMA chave de provedor, mesmo que o computador tenha alguma definida
        with mock.patch.dict("os.environ", {}, clear=True):
            status, corpo = self._post(json.dumps({"texto": "Achei um inseto na salada"}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(corpo["urgencia"], "alta")

    def test_texto_vazio_da_400(self):
        self.assertEqual(self._post(b'{"texto": "  "}')[0], 400)

    def test_json_invalido_da_400(self):
        self.assertEqual(self._post(b"isso nao e json")[0], 400)


if __name__ == "__main__":
    unittest.main()
