import tempfile
import unittest
from pathlib import Path

from identificacao_interacoes.erros import (
    ErroBancoError,
    FonteAlteradaError,
    MedicamentoEmAndamentoError,
    ProcessamentoJaExisteError,
)
from identificacao_interacoes.modelos import (
    RelacaoIdentificada,
    ResultadoIdentificacao,
    StatusProcessamento,
    TipoAlvo,
)
from identificacao_interacoes.pipeline import atualizar_vinculos_lote, processar_lote
from identificacao_interacoes.relatorio import RelatorioIdentificacao
from identificacao_interacoes.resolucao import CatalogoMedicamentos, MedicamentoCatalogo
from identificacao_interacoes.tests.auxiliares import fonte


TEXTO = "A varfarina pode ter o efeito aumentado; monitore o tempo de protrombina."


def relacao(alvo="varfarina", tipo=TipoAlvo.PRINCIPIO_ATIVO) -> RelacaoIdentificada:
    return RelacaoIdentificada(alvo, tipo, "Pode ter o efeito aumentado.", TEXTO, 0, len(TEXTO), 0)


class ServicoFalso:
    versao_processamento = "v-teste"
    modelo = "modelo/falso.gguf"

    def __init__(self, status=StatusProcessamento.CONCLUIDO_COM_RELACOES, relacoes=None) -> None:
        self.status = status
        self.relacoes = relacoes if relacoes is not None else [relacao(), relacao("anticoagulantes", TipoAlvo.CLASSE_FARMACOLOGICA)]
        self.chamadas = 0

    def identificar(self, fonte_):
        self.chamadas += 1
        relacoes = [] if self.status == StatusProcessamento.FALHA else list(self.relacoes)
        return ResultadoIdentificacao(
            status=self.status, nome_normalizado=fonte_.nome_normalizado, relacoes=relacoes,
            quantidade_janelas=1, janelas_concluidas=0 if self.status == StatusProcessamento.FALHA else 1,
            detalhe_erro="Janela 1/1: falha simulada" if self.status == StatusProcessamento.FALHA else "",
        )


class RepositorioFalso:
    """Imita a semântica do repositório real com estado em memória."""

    def __init__(self) -> None:
        self.bloqueados: set[str] = set()
        self.processamentos: list[dict] = []
        self.interacoes: list[dict] = []
        self.vinculos: dict[tuple[int, str], dict] = {}
        self.erro_gravacao: Exception | None = None
        self.liberacoes = 0

    def bloquear_medicamento(self, nome):
        if nome in self.bloqueados:
            return False
        self.bloqueados.add(nome)
        return True

    def liberar_medicamento(self, nome):
        self.bloqueados.discard(nome)
        self.liberacoes += 1

    def processamento_vigente(self, nome):
        vigentes = [p for p in self.processamentos if p["origem"] == nome and p["status"] != "FALHA"]
        return {"id": vigentes[-1]["id"], "versao_processamento": vigentes[-1]["versao"]} if vigentes else None

    # O falso não distingue revisão de conclusão: ambos bloqueiam a repetição.
    processamento_registrado = processamento_vigente

    def gravar_resultado(self, fonte_, resultado, vinculos, *, versao, reprocessar=False, **_):
        if self.erro_gravacao:
            raise self.erro_gravacao
        vigente = self.processamento_vigente(fonte_.nome_normalizado)
        if not reprocessar and vigente and vigente["versao_processamento"] == versao:
            raise ProcessamentoJaExisteError("já existe")
        pid = len(self.processamentos) + 1
        self.processamentos.append({"id": pid, "origem": fonte_.nome_normalizado, "status": resultado.status.value, "versao": versao})
        total = 0
        for rel, lista in zip(resultado.relacoes, vinculos, strict=True):
            iid = len(self.interacoes) + 1
            self.interacoes.append({"id": iid, "processamento": pid, "alvo": rel.alvo, "tipo_alvo": rel.tipo_alvo.value})
            for v in lista:
                self.vinculos[(iid, v.medicamento_relacionado)] = {"invalidado": False}
                total += 1
        return pid, total

    def registrar_falha(self, fonte_, resultado, *, versao, **_):
        pid = len(self.processamentos) + 1
        self.processamentos.append({"id": pid, "origem": fonte_.nome_normalizado, "status": "FALHA", "versao": versao})
        return pid

    def interacoes_do_processamento(self, processamento_id):
        return [i for i in self.interacoes if i["processamento"] == processamento_id]

    def atualizar_vinculos(self, processamento_id, origem, desejados):
        if origem in self.bloqueados:
            raise MedicamentoEmAndamentoError("ocupado")
        contagem = {"criados": 0, "invalidados": 0, "mantidos": 0, "reativados": 0, "atualizados": 0}
        alvo = {(iid, v.medicamento_relacionado) for iid, lista in desejados.items() for v in lista}
        for chave in alvo:
            if chave not in self.vinculos:
                self.vinculos[chave] = {"invalidado": False}
                contagem["criados"] += 1
            elif self.vinculos[chave]["invalidado"]:
                self.vinculos[chave]["invalidado"] = False
                contagem["reativados"] += 1
            else:
                contagem["mantidos"] += 1
        for chave, dados in self.vinculos.items():
            if chave[0] in desejados and chave not in alvo and not dados["invalidado"]:
                dados["invalidado"] = True
                contagem["invalidados"] += 1
        return contagem


def catalogo(*extras) -> CatalogoMedicamentos:
    base = [
        ("varfarina a", "Varfarina A", "varfarina", None),
        ("varfarina b", "Varfarina B", "varfarina", None),
        *extras,
    ]
    return CatalogoMedicamentos(MedicamentoCatalogo(*m) for m in base)


class TestPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.pasta = tempfile.TemporaryDirectory()
        self.caminho = Path(self.pasta.name) / "relatorio.csv"

    def tearDown(self) -> None:
        self.pasta.cleanup()

    def executar(self, fontes, servico, repositorio, catalogo_=None, **kwargs):
        if self.caminho.exists():
            self.caminho = self.caminho.with_stem(self.caminho.stem + "_nova")
        with RelatorioIdentificacao(self.caminho) as relatorio:
            return processar_lote(fontes, servico, catalogo_ or catalogo(), repositorio, relatorio, **kwargs)

    def test_grava_relacoes_com_vinculos_e_classe_sem_vinculo(self) -> None:
        repositorio = RepositorioFalso()
        resumo = self.executar([fonte(TEXTO)], ServicoFalso(), repositorio)
        self.assertEqual(resumo, {"CONCLUIDO_COM_RELACOES": 1})
        self.assertEqual(len(repositorio.interacoes), 2)
        self.assertEqual(sorted(k[1] for k in repositorio.vinculos), ["varfarina a", "varfarina b"])
        self.assertEqual(repositorio.bloqueados, set())
        detalhes = self.caminho.with_name("relatorio_detalhes.jsonl").read_text(encoding="utf-8")
        self.assertIn('"anticoagulantes"', detalhes)
        self.assertIn("relacoes_extraidas", self.caminho.read_text(encoding="utf-8-sig"))

    def test_reexecucao_nao_duplica_e_nao_chama_o_modelo(self) -> None:
        repositorio, servico = RepositorioFalso(), ServicoFalso()
        self.executar([fonte(TEXTO)], servico, repositorio)
        resumo = self.executar([fonte(TEXTO)], servico, repositorio)
        self.assertEqual(resumo, {"IGNORADO_JA_PROCESSADO": 1})
        self.assertEqual(servico.chamadas, 1)
        self.assertEqual(len(repositorio.interacoes), 2)

    def test_reprocessar_cria_nova_versao_e_preserva_historico(self) -> None:
        repositorio, servico = RepositorioFalso(), ServicoFalso()
        self.executar([fonte(TEXTO)], servico, repositorio)
        self.executar([fonte(TEXTO)], servico, repositorio, reprocessar=True)
        self.assertEqual([p["id"] for p in repositorio.processamentos], [1, 2])
        self.assertEqual(len(repositorio.interacoes), 4)  # nada apagado
        self.assertEqual(repositorio.processamento_vigente("produto teste")["id"], 2)

    def test_bloqueio_de_outro_processo(self) -> None:
        repositorio, servico = RepositorioFalso(), ServicoFalso()
        repositorio.bloqueados.add("produto teste")
        resumo = self.executar([fonte(TEXTO)], servico, repositorio)
        self.assertEqual(resumo, {"EM_ANDAMENTO_OUTRO_PROCESSO": 1})
        self.assertEqual(servico.chamadas, 0)

    def test_falha_registra_controle_sem_relacoes_e_mantem_anterior(self) -> None:
        repositorio = RepositorioFalso()
        self.executar([fonte(TEXTO)], ServicoFalso(), repositorio)
        resumo = self.executar([fonte(TEXTO)], ServicoFalso(StatusProcessamento.FALHA), repositorio, reprocessar=True)
        self.assertEqual(resumo, {"FALHA": 1})
        self.assertEqual(repositorio.processamentos[-1]["status"], "FALHA")
        self.assertEqual(len(repositorio.interacoes), 2)
        self.assertEqual(repositorio.processamento_vigente("produto teste")["id"], 1)

    def test_fonte_alterada_durante_a_inferencia(self) -> None:
        repositorio = RepositorioFalso()
        repositorio.erro_gravacao = FonteAlteradaError("trecho_interacoes alterado")
        resumo = self.executar([fonte(TEXTO)], ServicoFalso(), repositorio)
        self.assertEqual(resumo, {"FONTE_ALTERADA": 1})
        self.assertEqual(repositorio.interacoes, [])
        self.assertEqual(repositorio.processamentos[-1]["status"], "FALHA")
        self.assertEqual(repositorio.bloqueados, set())

    def test_erro_de_banco_na_gravacao_libera_bloqueio(self) -> None:
        repositorio = RepositorioFalso()
        repositorio.erro_gravacao = ErroBancoError("rollback")
        resumo = self.executar([fonte(TEXTO)], ServicoFalso(), repositorio)
        self.assertEqual(resumo, {"ERRO_BANCO": 1})
        self.assertEqual(repositorio.liberacoes, 1)

    def test_identificadores_incoerentes_nao_sao_processados(self) -> None:
        servico = ServicoFalso()
        resumo = self.executar(
            [fonte(TEXTO, identificadores_coerentes=False), fonte(TEXTO, nome_normalizado="b", hash_trecho_banco="x")],
            servico, RepositorioFalso(),
        )
        self.assertEqual(resumo, {"FONTE_INCOERENTE": 2})
        self.assertEqual(servico.chamadas, 0)

    def test_atualizacao_de_vinculos_sem_llm(self) -> None:
        repositorio, servico = RepositorioFalso(), ServicoFalso()
        self.executar([fonte(TEXTO)], servico, repositorio, catalogo_=catalogo())
        novo_catalogo = CatalogoMedicamentos([
            # Outra substância: o cadastro deixa de sustentar o vínculo (sal ou
            # hidrato diferentes continuariam vinculados, por PRINCIPIO_ATIVO_SEM_SAL).
            MedicamentoCatalogo("varfarina b", "Varfarina B", "femprocumona", None),
            MedicamentoCatalogo("varfarina c", "Varfarina C", "varfarina", None),
            MedicamentoCatalogo("varfarina a", "Varfarina A", "varfarina", None),
        ])
        if self.caminho.exists():
            self.caminho = self.caminho.with_stem(self.caminho.stem + "_nova")
        with RelatorioIdentificacao(self.caminho) as relatorio:
            resumo = atualizar_vinculos_lote(
                [{"id": 1, "medicamento_origem": "produto teste"}], novo_catalogo, repositorio, relatorio
            )
        self.assertEqual(resumo, {"VINCULOS_ATUALIZADOS": 1})
        self.assertEqual(servico.chamadas, 1)
        self.assertFalse(repositorio.vinculos[(1, "varfarina a")]["invalidado"])
        self.assertTrue(repositorio.vinculos[(1, "varfarina b")]["invalidado"])
        self.assertFalse(repositorio.vinculos[(1, "varfarina c")]["invalidado"])
        self.assertEqual(len(repositorio.vinculos), 3)  # nada apagado

    def test_atualizacao_de_vinculos_respeita_bloqueio(self) -> None:
        repositorio = RepositorioFalso()
        self.executar([fonte(TEXTO)], ServicoFalso(), repositorio)
        repositorio.bloqueados.add("produto teste")
        if self.caminho.exists():
            self.caminho = self.caminho.with_stem(self.caminho.stem + "_nova")
        with RelatorioIdentificacao(self.caminho) as relatorio:
            resumo = atualizar_vinculos_lote(
                [{"id": 1, "medicamento_origem": "produto teste"}], catalogo(), repositorio, relatorio
            )
        self.assertEqual(resumo, {"EM_ANDAMENTO_OUTRO_PROCESSO": 1})


if __name__ == "__main__":
    unittest.main()
