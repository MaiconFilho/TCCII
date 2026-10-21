import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipelines.identificacao_interacoes.aplicar_migracoes import conferir_aditiva, listar_migracoes
from pipelines.identificacao_interacoes.main_identificacao import (
    configuracao_do_ambiente,
    criar_argumentos,
    main,
    precisam_de_modelo,
)
from pipelines.identificacao_interacoes.tests.auxiliares import fonte


class TestMigracoes(unittest.TestCase):
    def test_migracao_unica_e_aditiva(self) -> None:
        migracoes = listar_migracoes()
        self.assertEqual(
            [m.name for m in migracoes],
            ["001_criar_interacoes.sql", "002_vigencia_aprovada.sql", "003_vinculo_sem_sal.sql"],
        )
        sql_sem_sal = conferir_aditiva(migracoes[2])
        self.assertIn("'PRINCIPIO_ATIVO_SEM_SAL'", sql_sem_sal)
        for criterio in ("PRINCIPIO_ATIVO_EXATO", "NOME_MEDICAMENTO_EXATO", "COMPOSICAO_EQUIVALENCIA_DECLARADA"):
            self.assertIn(f"'{criterio}'", sql_sem_sal)
        sql_vigencia = conferir_aditiva(migracoes[1])
        self.assertIn("p.invalidado_em IS NULL", sql_vigencia)
        self.assertIn("CREATE OR REPLACE VIEW processamentos_interacoes_registrados", sql_vigencia)
        sql = conferir_aditiva(migracoes[0])
        for objeto in ("interacoes", "interacoes_medicamentos", "processamentos_interacoes"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {objeto}", sql)
        self.assertNotIn("chave_deduplicacao", sql)
        self.assertIn("ON DELETE RESTRICT", sql)

    def test_comando_destrutivo_e_recusado(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            for conteudo in ("DROP TABLE bulas;", "TRUNCATE interacoes;", "DELETE FROM bulas;"):
                caminho = Path(pasta) / "999_teste.sql"
                caminho.write_text(f"-- comentário DROP\n{conteudo}", encoding="utf-8")
                with self.assertRaises(ValueError):
                    conferir_aditiva(caminho)
            caminho.write_text("COMMENT ON TABLE x IS 'não DROP aqui';\nCREATE TABLE y (z int REFERENCES w ON DELETE RESTRICT);", encoding="utf-8")
            conferir_aditiva(caminho)

    def test_so_aceita_trocar_check_pelo_mesmo_nome(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "999_teste.sql"
            caminho.write_text(
                "ALTER TABLE t DROP CONSTRAINT IF EXISTS ck_t_x, ADD CONSTRAINT ck_t_x CHECK (x > 0);",
                encoding="utf-8",
            )
            conferir_aditiva(caminho)
            for recusado in (
                "ALTER TABLE t DROP CONSTRAINT IF EXISTS ck_t_x;",
                "ALTER TABLE t DROP CONSTRAINT IF EXISTS ck_t_x, ADD CONSTRAINT ck_t_y CHECK (x > 0);",
                "ALTER TABLE t DROP CONSTRAINT IF EXISTS fk_t_x, ADD CONSTRAINT fk_t_x CHECK (x > 0);",
                "ALTER TABLE t DROP CONSTRAINT IF EXISTS ck_t_x, ADD CONSTRAINT ck_t_x CHECK (x > 0), DROP COLUMN y;",
            ):
                caminho.write_text(recusado, encoding="utf-8")
                with self.assertRaises(ValueError, msg=recusado):
                    conferir_aditiva(caminho)


class TestCli(unittest.TestCase):
    def test_padrao_e_piloto_de_um(self) -> None:
        args = criar_argumentos([])
        self.assertEqual((args.limite, args.todos, args.reprocessar, args.atualizar_vinculos), (1, False, False, False))

    def test_orcamento_de_saida_proprio(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            configuracao = configuracao_do_ambiente()
        self.assertEqual(configuracao.max_tokens_saida, 3072)
        self.assertNotEqual(configuracao.max_tokens_saida, 256)
        configuracao.validar()

    def test_configuracao_invalida_retorna_2(self) -> None:
        with mock.patch.dict(os.environ, {"IDENTIFICACAO_LLM_INFERENCE_CONCURRENCY": "2"}, clear=True), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.load_dotenv"), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.configurar_log"):
            self.assertEqual(main([]), 2)

    def test_retirada_exige_motivo_antes_de_conectar(self) -> None:
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://teste"}, clear=True), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.load_dotenv"), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.configurar_log"), \
                mock.patch("pipelines.identificacao_interacoes.repositorio.RepositorioIdentificacao") as repositorio:
            self.assertEqual(main(["--retirar-processamento", "7", "--motivo", "  "]), 2)
        repositorio.assert_not_called()

    def test_retirada_nao_carrega_modelo(self) -> None:
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://teste"}, clear=True), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.load_dotenv"), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.configurar_log"), \
                mock.patch("pipelines.identificacao_interacoes.repositorio.RepositorioIdentificacao") as repositorio, \
                mock.patch("pipelines.identificacao_interacoes.provedor.carregar_provedor") as carregar:
            self.assertEqual(main(["--retirar-processamento", "7", "--motivo", "Relação incorreta."]), 0)
        repositorio.return_value.invalidar_processamento.assert_called_once_with(7, "Relação incorreta.")
        repositorio.return_value.fechar.assert_called_once()
        carregar.assert_not_called()

    def test_estrategia_padrao_e_dicionario_sem_modelo(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            configuracao = configuracao_do_ambiente()
        self.assertEqual(configuracao.estrategia, "dicionario")
        self.assertFalse(configuracao.usa_modelo)
        with mock.patch.dict(os.environ, {"IDENTIFICACAO_ESTRATEGIA": "atual"}, clear=True):
            self.assertTrue(configuracao_do_ambiente().usa_modelo)

    def _executar(self, argumentos):
        repositorio = mock.MagicMock()
        repositorio.carregar_catalogo.return_value = []
        repositorio.selecionar_fontes.return_value = [fonte("Texto qualquer de interação.")]
        with tempfile.TemporaryDirectory() as pasta, \
                mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://teste"}, clear=True), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.load_dotenv"), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.configurar_log"), \
                mock.patch("pipelines.identificacao_interacoes.repositorio.RepositorioIdentificacao", return_value=repositorio), \
                mock.patch("pipelines.identificacao_interacoes.provedor.carregar_provedor") as carregar, \
                mock.patch("pipelines.identificacao_interacoes.pipeline.processar_lote", return_value={}) as processar, \
                mock.patch("pipelines.identificacao_interacoes.pipeline.simular_lote", return_value={}) as simular:
            codigo = main([*argumentos, "--todos", "--relatorio", str(Path(pasta) / "r.csv")])
        return codigo, repositorio, carregar, processar, simular

    def test_dicionario_grava_sem_carregar_modelo(self) -> None:
        from pipelines.identificacao_interacoes.dicionario import ServicoDicionario

        codigo, repositorio, carregar, processar, simular = self._executar([])
        self.assertEqual(codigo, 0)
        carregar.assert_not_called()
        simular.assert_not_called()
        servico = processar.call_args.args[1]
        self.assertIsInstance(servico, ServicoDicionario)
        selecao = repositorio.selecionar_fontes.call_args.kwargs
        self.assertEqual((selecao["versao"], selecao["reprocessar"]), (servico.versao_processamento, False))

    def test_simular_nao_grava(self) -> None:
        codigo, repositorio, carregar, processar, simular = self._executar(["--simular"])
        self.assertEqual(codigo, 0)
        processar.assert_not_called()
        simular.assert_called_once()
        carregar.assert_not_called()
        self.assertTrue(repositorio.selecionar_fontes.call_args.kwargs["reprocessar"])
        repositorio.gravar_resultado.assert_not_called()

    def test_estrategia_invalida_retorna_2(self) -> None:
        with mock.patch.dict(os.environ, {"IDENTIFICACAO_ESTRATEGIA": "outra"}, clear=True), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.load_dotenv"), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.configurar_log"):
            self.assertEqual(main([]), 2)

    def test_todos_e_nome_sao_exclusivos(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.load_dotenv"), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.configurar_log"):
            self.assertEqual(main(["--todos", "--nome-normalizado", "x"]), 2)

    def test_modelo_so_e_carregado_se_houver_o_que_processar(self) -> None:
        class Repositorio:
            def __init__(self, versao):
                self.versao = versao

            def processamento_registrado(self, _nome):
                return {"versao_processamento": self.versao} if self.versao else None

        fontes = [fonte("Texto qualquer de interação.")]
        self.assertFalse(precisam_de_modelo(fontes, Repositorio("v1"), "v1", reprocessar=False))
        self.assertTrue(precisam_de_modelo(fontes, Repositorio("v0"), "v1", reprocessar=False))
        self.assertTrue(precisam_de_modelo(fontes, Repositorio(None), "v1", reprocessar=False))
        self.assertTrue(precisam_de_modelo(fontes, Repositorio("v1"), "v1", reprocessar=True))

    def test_sem_database_url_nao_carrega_modelo(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.load_dotenv"), \
                mock.patch("pipelines.identificacao_interacoes.main_identificacao.configurar_log"), \
                mock.patch("pipelines.identificacao_interacoes.provedor.carregar_provedor") as carregar:
            self.assertEqual(main([]), 4)
        carregar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
