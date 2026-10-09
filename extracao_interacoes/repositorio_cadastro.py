"""Persistência do cadastro em bulas; não acessa bulas_interacoes."""
from pathlib import Path

from .erros import ErroBancoError, EsquemaBancoIncompativelError
from .modelos import BulaParaExtracao


class RepositorioCadastro:
    def __init__(self, conexao):
        self.conexao = conexao

    def validar_esquema(self):
        with self.conexao.cursor() as cursor:
            cursor.execute("""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bulas'
                  AND column_name IN ('principios_ativos', 'composicao')
            """)
            colunas = {r["column_name"]: r["data_type"] for r in cursor.fetchall()}
        if set(colunas) != {"principios_ativos", "composicao"} or any(
            tipo not in {"text", "character varying"} for tipo in colunas.values()
        ):
            raise EsquemaBancoIncompativelError(
                "A tabela bulas precisa dos campos TEXT principios_ativos e composicao. "
                "Execute o ALTER TABLE documentado no README antes de processar."
            )

    def selecionar(self, *, inicio=0, limite=1, nome_normalizado=None,
                  reprocessar=False, somente_composicao=False):
        with self.conexao.cursor() as cursor:
            cursor.execute("""
                SELECT nome_normalizado, numero_registro, expediente, caminho_pdf
                FROM public.bulas
                WHERE status = 'CONCLUIDO' AND numero_registro IS NOT NULL
                  AND NULLIF(BTRIM(caminho_pdf), '') IS NOT NULL
                  AND (CAST(%s AS TEXT) IS NULL OR nome_normalizado = %s)
                  AND ((%s AND (%s OR NULLIF(BTRIM(composicao), '') IS NULL))
                    OR (NOT %s AND (%s OR NULLIF(BTRIM(principios_ativos), '') IS NULL
                                      OR NULLIF(BTRIM(composicao), '') IS NULL)))
                ORDER BY nome_normalizado
                OFFSET %s LIMIT %s
            """, (nome_normalizado, nome_normalizado, somente_composicao, reprocessar,
                  somente_composicao, reprocessar, inicio, limite))
            registros = cursor.fetchall()
        # PDFs ausentes são reportados no lote, em vez de desaparecerem da seleção.
        return [BulaParaExtracao(
            r["nome_normalizado"], r["numero_registro"], r["expediente"],
            Path(r["caminho_pdf"]).resolve(), r["caminho_pdf"],
        ) for r in registros]

    def gravar(self, bula, resultado, *, reprocessar=False):
        if not resultado.composicao or not resultado.composicao.strip():
            raise ErroBancoError("Composição não localizada; gravação recusada.")
        ativo = (resultado.principios_ativos or "").strip() or None
        try:
            with self.conexao.transaction():
                with self.conexao.cursor() as cursor:
                    cursor.execute("""
                        UPDATE public.bulas
                        SET principios_ativos = CASE
                                WHEN CAST(%s AS TEXT) IS NOT NULL
                                  AND (%s OR NULLIF(BTRIM(principios_ativos), '') IS NULL) THEN %s
                                ELSE principios_ativos END,
                            composicao = CASE
                                WHEN %s OR NULLIF(BTRIM(composicao), '') IS NULL THEN %s
                                ELSE composicao END
                        WHERE nome_normalizado = %s
                          AND numero_registro IS NOT DISTINCT FROM %s
                          AND expediente IS NOT DISTINCT FROM %s
                          AND caminho_pdf = %s
                          AND status = 'CONCLUIDO'
                    """, (ativo, reprocessar, ativo, reprocessar, resultado.composicao,
                          bula.nome_normalizado, bula.numero_registro, bula.expediente,
                          bula.caminho_pdf_banco or str(bula.caminho_pdf)))
                    if cursor.rowcount != 1:
                        raise ErroBancoError("Bula mudou desde a seleção; cadastro anterior preservado.")
        except ErroBancoError:
            raise
        except Exception as erro:
            raise ErroBancoError(f"Falha ao gravar cadastro de {bula.nome_normalizado}: {erro}") from erro
