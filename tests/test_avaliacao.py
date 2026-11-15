"""Exportação da avaliação sem rede e sem alterar resultados existentes."""
import codecs
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import avaliacao


class TestAvaliacaoCSV(unittest.TestCase):
    def setUp(self):
        self.temporario = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporario.cleanup)
        self.raiz = Path(self.temporario.name)
        self.patch_raiz = mock.patch.object(avaliacao, "RAIZ", self.raiz)
        self.patch_raiz.start()
        self.addCleanup(self.patch_raiz.stop)
        (self.raiz / "dados").mkdir()
        self.texto = 'Não gostei; veio "frio", sem talheres.\nPéssimo!'
        with (self.raiz / "dados" / "avaliacao.csv").open(
                "w", encoding="utf-8", newline="") as f:
            escritor = csv.DictWriter(f, fieldnames=["id", "texto", "categoria", "urgencia"])
            escritor.writeheader()
            for i in range(1, 6):
                escritor.writerow({
                    "id": str(i), "texto": self.texto,
                    "categoria": "qualidade_comida", "urgencia": "alta",
                })

    def ler_csv(self, modo):
        arquivo = self.raiz / "resultados" / f"avaliacao_{modo}.csv"
        self.assertTrue(arquivo.read_bytes().startswith(codecs.BOM_UTF8))
        with arquivo.open(encoding="utf-8-sig", newline="") as f:
            leitor = csv.DictReader(f, delimiter=";")
            self.assertEqual(leitor.fieldnames, [
                "ID", "Texto", "Categoria esperada", "Categoria prevista",
                "Urgência esperada", "Urgência prevista", "Acertou", "Erro",
            ])
            return list(leitor)

    def test_cria_csv_com_colunas_acentos_e_todos_os_tipos_de_erro(self):
        respostas = [
            {"categoria": "qualidade_comida", "urgencia": "alta"},
            {"categoria": "outro", "urgencia": "alta"},
            {"categoria": "qualidade_comida", "urgencia": "baixa"},
            {"categoria": "outro", "urgencia": "baixa"},
            RuntimeError("Serviço indisponível"),
        ]
        with mock.patch.object(avaliacao, "triagem", side_effect=respostas) as triagem:
            resumo = avaliacao.avaliar("gemini")
        triagem.assert_called_with(self.texto, modo="gemini", registrar=False)
        linhas = self.ler_csv("gemini")
        self.assertEqual(len(linhas), 5)
        self.assertEqual([l["ID"] for l in linhas], ["1", "2", "3", "4", "5"])
        self.assertTrue(all(l["Texto"] == self.texto for l in linhas))
        self.assertTrue(all(l["Categoria esperada"] == "qualidade_comida" for l in linhas))
        self.assertTrue(all(l["Urgência esperada"] == "alta" for l in linhas))
        self.assertEqual([l["Erro"] for l in linhas], [
            "nenhum", "categoria", "urgência", "categoria e urgência", "falha na avaliação",
        ])
        self.assertEqual([l["Acertou"] for l in linhas], ["sim", "não", "não", "não", "não"])
        self.assertEqual([l["Categoria prevista"] for l in linhas],
                         ["qualidade_comida", "outro", "qualidade_comida", "outro", ""])
        self.assertEqual([l["Urgência prevista"] for l in linhas],
                         ["alta", "alta", "baixa", "baixa", ""])
        self.assertEqual(resumo["acuracia_categoria"], 0.4)
        self.assertEqual(resumo["acuracia_urgencia"], 0.4)
        self.assertEqual(resumo["acuracia_ambos"], 0.2)
        self.assertEqual(resumo["falhas"], 1)
        self.assertEqual(len(resumo["erros"]), 3)

    def test_comando_mantem_json_e_resumo_e_sobrescreve_csv_sem_duplicar(self):
        with mock.patch.object(sys, "argv", ["avaliacao.py", "--modo", "regras"]):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as saida:
                avaliacao.main()
                avaliacao.main()
        self.assertIn("Modo: regras | casos: 5 | falhas: 0", saida.getvalue())
        self.assertIn("Categoria:", saida.getvalue())
        self.assertEqual(len(self.ler_csv("regras")), 5)
        resumo = json.loads(
            (self.raiz / "resultados" / "avaliacao_regras.json").read_text(encoding="utf-8"))
        self.assertEqual(set(resumo), {
            "modo", "casos", "falhas", "acuracia_categoria", "acuracia_urgencia",
            "acuracia_ambos", "latencia_media_s", "erros", "falhas_detalhe",
        })
        self.assertEqual(resumo["casos"], 5)


if __name__ == "__main__":
    unittest.main()
