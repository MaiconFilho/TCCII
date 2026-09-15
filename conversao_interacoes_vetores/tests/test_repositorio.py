import sys
import unittest
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conversao_interacoes_vetores.erros import (
    DimensaoInvalidaError,
    ErroBancoError,
    EsquemaBancoIncompativelError,
    ExtensaoVectorIndisponivelError,
)
from conversao_interacoes_vetores.modelos import EmbeddingChunk
from conversao_interacoes_vetores.repositorio import (
    INDICE_HNSW,
    RepositorioEmbeddings,
    vetor_para_literal,
)


class CursorRoteado:
    """Devolve respostas conforme o trecho identificador de cada consulta."""

    def __init__(self, rotas=None) -> None:
        self.rotas = dict(rotas or {})
        self.consultas: list[tuple] = []
        self.erro_em = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def _resposta(self, consulta):
        for marcador, linhas in self.rotas.items():
            if marcador in consulta:
                return linhas
        return []

    def execute(self, consulta, parametros=None):
        self.consultas.append((consulta, parametros))
        if self.erro_em and self.erro_em in consulta:
            raise RuntimeError("falha simulada de banco")
        self._ultima = self._resposta(consulta)

    def fetchone(self):
        return self._ultima[0] if self._ultima else None

    def fetchall(self):
        return self._ultima

    def consultas_com(self, marcador: str) -> list[tuple]:
        return [c for c in self.consultas if marcador in c[0]]


class ConexaoRoteada:
    def __init__(self, cursor: CursorRoteado) -> None:
        self.cursor_falso = cursor
        self.transacoes = 0
        self.fechada = False

    def cursor(self):
        return self.cursor_falso

    def transaction(self):
        self.transacoes += 1
        return nullcontext()

    def close(self):
        self.fechada = True


def criar(cursor: CursorRoteado) -> RepositorioEmbeddings:
    repositorio = RepositorioEmbeddings.__new__(RepositorioEmbeddings)
    repositorio.conexao = ConexaoRoteada(cursor)
    return repositorio


def chunk(indice=0, dimensao=4, texto="trecho") -> EmbeddingChunk:
    return EmbeddingChunk(
        chunk_index=indice,
        texto_chunk=texto,
        texto_hash="h" * 64,
        quantidade_tokens=10,
        vetor=tuple(0.1 * (posicao + 1) for posicao in range(dimensao)),
    )


COLUNAS_OK = [
    {"column_name": "nome_normalizado", "data_type": "text", "is_nullable": "NO"},
    {"column_name": "trecho_interacoes", "data_type": "text", "is_nullable": "YES"},
]


class TestValidacaoDeEsquema(unittest.TestCase):
    def test_esquema_valido_e_aceito(self):
        cursor = CursorRoteado({"information_schema.columns": COLUNAS_OK})

        criar(cursor)._validar_esquema_interacoes()  # não deve levantar

    def test_coluna_ausente_e_recusada(self):
        cursor = CursorRoteado({"information_schema.columns": COLUNAS_OK[:1]})

        with self.assertRaises(EsquemaBancoIncompativelError):
            criar(cursor)._validar_esquema_interacoes()

    def test_nome_normalizado_nao_textual_e_recusado(self):
        colunas = [dict(COLUNAS_OK[0], data_type="integer"), COLUNAS_OK[1]]
        cursor = CursorRoteado({"information_schema.columns": colunas})

        with self.assertRaises(EsquemaBancoIncompativelError):
            criar(cursor)._validar_esquema_interacoes()

    def test_nome_normalizado_anulavel_e_recusado(self):
        colunas = [dict(COLUNAS_OK[0], is_nullable="YES"), COLUNAS_OK[1]]
        cursor = CursorRoteado({"information_schema.columns": colunas})

        with self.assertRaises(EsquemaBancoIncompativelError):
            criar(cursor)._validar_esquema_interacoes()


class TestExtensaoPgvector(unittest.TestCase):
    def test_cria_a_extensao_quando_disponivel(self):
        cursor = CursorRoteado(
            {
                "FROM pg_extension": [],
                "FROM pg_available_extensions": [{"?column?": 1}],
            }
        )

        criar(cursor).garantir_extensao_vector()

        self.assertTrue(cursor.consultas_com("CREATE EXTENSION IF NOT EXISTS vector"))

    def test_nao_recria_extensao_ja_instalada(self):
        cursor = CursorRoteado({"FROM pg_extension": [{"?column?": 1}]})

        criar(cursor).garantir_extensao_vector()

        self.assertFalse(cursor.consultas_com("CREATE EXTENSION"))

    def test_extensao_indisponivel_gera_erro_explicito(self):
        cursor = CursorRoteado(
            {"FROM pg_extension": [], "FROM pg_available_extensions": []}
        )

        with self.assertRaises(ExtensaoVectorIndisponivelError) as contexto:
            criar(cursor).garantir_extensao_vector()

        self.assertIn("pgvector", str(contexto.exception))


class TestCriacaoDaTabela(unittest.TestCase):
    def test_cria_a_tabela_com_a_dimensao_do_modelo(self):
        cursor = CursorRoteado({"format_type": []})

        criar(cursor).garantir_tabela(384)

        criacoes = cursor.consultas_com("CREATE TABLE IF NOT EXISTS")
        self.assertTrue(criacoes)
        self.assertIn("VECTOR(384)", criacoes[0][0])

    def test_tabela_existente_com_mesma_dimensao_nao_e_recriada(self):
        cursor = CursorRoteado({"format_type": [{"tipo": "vector(384)"}]})

        criar(cursor).garantir_tabela(384)

        self.assertFalse(cursor.consultas_com("CREATE TABLE"))

    def test_dimensao_divergente_e_recusada_sem_alterar_a_tabela(self):
        cursor = CursorRoteado({"format_type": [{"tipo": "vector(768)"}]})

        with self.assertRaises(DimensaoInvalidaError):
            criar(cursor).garantir_tabela(384)

        self.assertFalse(cursor.consultas_com("CREATE TABLE"))
        self.assertFalse(cursor.consultas_com("DROP"))

    def test_dimensao_nao_positiva_e_recusada(self):
        with self.assertRaises(DimensaoInvalidaError):
            criar(CursorRoteado()).garantir_tabela(0)

    def test_leitura_da_dimensao_da_tabela(self):
        cursor = CursorRoteado({"format_type": [{"tipo": "vector(1024)"}]})

        self.assertEqual(criar(cursor).dimensao_da_tabela(), 1024)

    def test_tabela_inexistente_devolve_none(self):
        self.assertIsNone(criar(CursorRoteado({"format_type": []})).dimensao_da_tabela())

    def test_colunas_legadas_sao_detectadas(self):
        cursor = CursorRoteado(
            {
                "format_type": [{"tipo": "vector(384)"}],
                "information_schema.columns": [
                    {"column_name": "modelo"},
                    {"column_name": "criado_em"},
                ],
            }
        )

        self.assertEqual(
            criar(cursor).colunas_legadas_presentes(), ["criado_em", "modelo"]
        )

    def test_esquema_antigo_interrompe_sem_alterar_nada(self):
        cursor = CursorRoteado(
            {
                "format_type": [{"tipo": "vector(384)"}],
                "information_schema.columns": [{"column_name": "modelo"}],
            }
        )

        with self.assertRaises(EsquemaBancoIncompativelError) as contexto:
            criar(cursor).garantir_tabela(384)

        self.assertIn("criar_tabela.sql", str(contexto.exception))
        self.assertFalse(cursor.consultas_com("ALTER TABLE"))
        self.assertFalse(cursor.consultas_com("DROP"))

    def test_tabela_atual_nao_acusa_esquema_antigo(self):
        cursor = CursorRoteado(
            {
                "format_type": [{"tipo": "vector(384)"}],
                "information_schema.columns": [],
            }
        )

        criar(cursor).garantir_tabela(384)  # não deve levantar

    def test_ddl_nao_contem_mais_modelo_nem_criado_em(self):
        cursor = CursorRoteado({"format_type": []})

        criar(cursor).garantir_tabela(384)

        ddl = cursor.consultas_com("CREATE TABLE IF NOT EXISTS")[0][0]
        self.assertNotIn("modelo", ddl)
        self.assertNotIn("criado_em", ddl)
        self.assertIn("UNIQUE (nome_normalizado, chunk_index)", ddl)
        self.assertIn("atualizado_em", ddl)


class TestIndice(unittest.TestCase):
    def test_cria_indice_hnsw_de_cosseno(self):
        cursor = CursorRoteado({"format_type": [{"tipo": "vector(384)"}]})

        criar(cursor).garantir_indice(384)

        criacoes = cursor.consultas_com("CREATE INDEX IF NOT EXISTS")
        self.assertTrue(criacoes)
        self.assertIn("hnsw", criacoes[0][0])
        self.assertIn("vector_cosine_ops", criacoes[0][0])
        self.assertIn(INDICE_HNSW, criacoes[0][0])

    def test_indice_com_dimensao_incompativel_nao_e_criado(self):
        cursor = CursorRoteado({"format_type": [{"tipo": "vector(768)"}]})

        with self.assertRaises(DimensaoInvalidaError):
            criar(cursor).garantir_indice(384)

        self.assertFalse(cursor.consultas_com("CREATE INDEX"))

    def test_indice_exige_tabela_existente(self):
        cursor = CursorRoteado({"format_type": []})

        with self.assertRaises(ErroBancoError):
            criar(cursor).garantir_indice(384)


class TestSelecao(unittest.TestCase):
    def test_filtra_por_nome_normalizado(self):
        cursor = CursorRoteado(
            {
                "FROM bulas_interacoes AS bi": [
                    {"nome_normalizado": "a saude da mulher",
                     "trecho_interacoes": "texto"}
                ]
            }
        )

        selecionadas = criar(cursor).selecionar_interacoes(nome_normalizado="a saude da mulher")

        self.assertEqual(len(selecionadas), 1)
        self.assertEqual(selecionadas[0].nome_normalizado, "a saude da mulher")
        _, parametros = cursor.consultas[0]
        self.assertEqual(parametros[0], "a saude da mulher")

    def test_limite_padrao_devolve_apenas_um_registro(self):
        linhas = [
            {"nome_normalizado": f"m{indice}", "trecho_interacoes": "texto"}
            for indice in range(5)
        ]
        cursor = CursorRoteado({"FROM bulas_interacoes AS bi": linhas})

        self.assertEqual(
            len(criar(cursor).selecionar_interacoes()), 1
        )

    def test_limite_nulo_devolve_todos(self):
        linhas = [
            {"nome_normalizado": f"m{indice}", "trecho_interacoes": "texto"}
            for indice in range(5)
        ]
        cursor = CursorRoteado({"FROM bulas_interacoes AS bi": linhas})

        self.assertEqual(
            len(criar(cursor).selecionar_interacoes(limite=None)), 5
        )

    def test_o_corte_e_delegado_ao_postgresql(self):
        cursor = CursorRoteado({"FROM bulas_interacoes AS bi": []})
        repositorio = criar(cursor)

        repositorio.selecionar_interacoes(inicio=3, limite=10)
        consulta, parametros = cursor.consultas[-1]
        self.assertIn("OFFSET %s", consulta)
        self.assertIn("LIMIT %s", consulta)
        self.assertEqual(parametros[3], 3)
        self.assertEqual(parametros[4], 10)

        # limite None vira LIMIT NULL, que o PostgreSQL trata como "todos".
        repositorio.selecionar_interacoes(limite=None)
        self.assertIsNone(cursor.consultas[-1][1][4])

    def test_sem_reprocessar_o_lote_ignora_quem_ja_tem_vetor(self):
        cursor = CursorRoteado({"FROM bulas_interacoes AS bi": []})

        criar(cursor).selecionar_interacoes()

        consulta, parametros = cursor.consultas[0]
        self.assertIn("NOT EXISTS", consulta)
        self.assertFalse(parametros[2])

    def test_reprocessar_desliga_o_filtro_de_vetor_existente(self):
        cursor = CursorRoteado({"FROM bulas_interacoes AS bi": []})

        criar(cursor).selecionar_interacoes(reprocessar=True)

        self.assertTrue(cursor.consultas[0][1][2])

    def test_selecao_por_nome_devolve_a_linha_mesmo_ja_vetorizada(self):
        # Assim o controle de hash pode responder IGNORADO_JA_EXISTENTE
        # em vez de a CLI dizer que nada é elegível.
        cursor = CursorRoteado({"FROM bulas_interacoes AS bi": []})

        criar(cursor).selecionar_interacoes(nome_normalizado="dipirona")

        self.assertTrue(cursor.consultas[0][1][2])

    def test_hashes_existentes_sao_indexados_por_chunk(self):
        cursor = CursorRoteado(
            {
                "SELECT chunk_index, texto_hash": [
                    {"chunk_index": 0, "texto_hash": "a"},
                    {"chunk_index": 1, "texto_hash": "b"},
                ]
            }
        )

        self.assertEqual(
            criar(cursor).hashes_existentes("dipirona"),
            {0: "a", 1: "b"},
        )


class TestGravacao(unittest.TestCase):
    def test_grava_todos_os_chunks_em_uma_unica_transacao(self):
        cursor = CursorRoteado()
        repositorio = criar(cursor)

        gravados = repositorio.gravar_embeddings("dipirona", 4, [chunk(0), chunk(1)])

        self.assertEqual(gravados, 2)
        self.assertEqual(repositorio.conexao.transacoes, 1)
        self.assertEqual(len(cursor.consultas_com("INSERT INTO")), 2)

    def test_upsert_evita_duplicidade_por_nome_e_chunk(self):
        cursor = CursorRoteado()

        criar(cursor).gravar_embeddings("dipirona", 4, [chunk(0)])

        consulta = cursor.consultas_com("INSERT INTO")[0][0]
        self.assertIn(
            "ON CONFLICT (nome_normalizado, chunk_index) DO UPDATE", consulta
        )

    def test_remove_chunks_sobrando_de_um_texto_menor(self):
        cursor = CursorRoteado()

        criar(cursor).gravar_embeddings("dipirona", 4, [chunk(0)])

        remocoes = cursor.consultas_com("DELETE FROM")
        self.assertEqual(len(remocoes), 1)
        self.assertEqual(remocoes[0][1], ("dipirona", 0))

    def test_dimensao_divergente_impede_a_gravacao(self):
        cursor = CursorRoteado()

        with self.assertRaises(DimensaoInvalidaError):
            criar(cursor).gravar_embeddings("dipirona", 4, [chunk(0, dimensao=8)])

        self.assertFalse(cursor.consultas_com("INSERT INTO"))

    def test_lista_vazia_e_recusada(self):
        with self.assertRaises(ErroBancoError):
            criar(CursorRoteado()).gravar_embeddings("dipirona", 4, [])

    def test_falha_no_insert_vira_erro_de_banco_e_desfaz_a_transacao(self):
        cursor = CursorRoteado()
        cursor.erro_em = "INSERT INTO"

        with self.assertRaises(ErroBancoError):
            criar(cursor).gravar_embeddings("dipirona", 4, [chunk(0)])

        # Nenhum DELETE chegou a ser executado: o vetor anterior permanece.
        self.assertFalse(cursor.consultas_com("DELETE FROM"))

    def test_vetor_e_convertido_para_literal_do_pgvector(self):
        self.assertEqual(vetor_para_literal((0.5, -1.25)), "[0.5,-1.25]")

    def test_parametro_do_vetor_usa_cast_explicito(self):
        cursor = CursorRoteado()

        criar(cursor).gravar_embeddings("dipirona", 4, [chunk(0)])

        consulta, parametros = cursor.consultas_com("INSERT INTO")[0]
        self.assertIn("%s::vector", consulta)
        self.assertTrue(parametros[-1].startswith("["))


if __name__ == "__main__":
    unittest.main()
