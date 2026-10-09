"""Correspondência determinística entre alvos e medicamentos do cadastro.

Regras (todas por igualdade após a normalização de caixa, acentos e espaços
que gerou ``bulas.nome_normalizado``; nunca por substring):

1. ``PRINCIPIO_ATIVO``: compara com cada substância de
   ``bulas.principios_ativos`` separada por ``;``. A substância inteira é a
   chave (``PRINCIPIO_ATIVO_EXATO``): "omeprazol" não corresponde a
   "esomeprazol". Duas formas literais da própria entrada também são
   aceitas, porque não criam equivalência nova:
   - anotação entre parênteses ao final: "amoxicilina (sob a forma
     tri-hidratada)" e "omeprazol (pellets)" também respondem pela substância
     escrita antes dos parênteses;
   - sufixo "base": "epinefrina base" também responde por "epinefrina".
2. ``PRINCIPIO_ATIVO`` também consulta equivalências **declaradas** na
   composição e ligadas a um princípio ativo (ver ``composicao``).
3. ``PRINCIPIO_ATIVO`` também compara as duas formas sem sal ou hidrato
   (``PRINCIPIO_ATIVO_SEM_SAL``): "varfarina" corresponde a "varfarina
   sódica" e "amiodarona" a "cloridrato de amiodarona". Continua sendo
   igualdade da substância inteira, e um sal nunca é reduzido ao ânion:
   "cloreto de sódio" não responde por "cloreto".
4. ``MEDICAMENTO``: compara com ``nome_normalizado`` e ``nome_anvisa``.

Classes, alimentos, grupos alimentares e demais tipos não são expandidos nem
procurados no cadastro; um alvo sem correspondência continua válido.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .composicao import equivalencias_declaradas
from .modelos import CriterioVinculo, TipoAlvo, Vinculo
from .normalizacao import normalizar_alvo


ANOTACAO_FINAL = re.compile(r"^(?P<cabeca>[^()]+?)\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*$")

# Sal ou forma antes da substância ("cloridrato de amiodarona") e depois dela
# ("varfarina sódica", "dipirona monoidratada"), sobre palavras sem acentos.
SAL_ANTES = re.compile(
    r"^(?:(?:mono|di)?cloridrato|hidrocloreto|bromidrato|hidrobrometo|brometo|maleato|mesilato|"
    r"besilato|napsilato|(?:hemi)?succinato|(?:hemi|bi)?tartarato|(?:hemi)?fumarato|citrato|"
    r"(?:bis)?sulfato|fosfato|acetato|(?:di)?propionato|valerato|butirato|lactato|gluconato|"
    r"estearato|palmitato|pamoato|embonato|tosilato|nitrato|carbonato|oxalato|hiclato|decanoato|"
    r"enantato|cipionato|undecilato|furoato|acetonido|aceponato) de "
)
SAL_DEPOIS = re.compile(
    r" (?:(?:di)?sodic[oa]|potassic[oa]|calcic[oa]|magnesic[oa]|hiclato|trometamol|base"
    r"|(?:mono|di|tri|sesqui|hemi)? ?hidratad[oa]|monoidratad[oa]|anidr[oa]"
    r"|de (?:sodio|potassio|calcio|magnesio|trometamol))$"
)
# O que sobra de um sal quando a substância é o ânion: não vira chave sozinho.
ANIONS = frozenset({
    "cloreto", "bicarbonato", "carbonato", "fosfato", "sulfato", "citrato", "lactato", "acetato",
    "gluconato", "hidroxido", "oxido", "iodeto", "fluoreto", "nitrato", "salicilato", "silicato",
    "trissilicato",
})


@dataclass(frozen=True, slots=True)
class MedicamentoCatalogo:
    nome_normalizado: str
    nome_exibicao: str
    principios_ativos: str | None
    composicao: str | None


def chaves_do_principio(principio: str) -> set[str]:
    chave = normalizar_alvo(principio)
    if not chave:
        return set()
    chaves = {chave}
    nota = ANOTACAO_FINAL.match(principio.strip())
    if nota and normalizar_alvo(nota.group("cabeca")):
        chaves.add(normalizar_alvo(nota.group("cabeca")))
    for atual in list(chaves):
        if atual.endswith(" base") and len(atual) > 5:
            chaves.add(atual[: -len(" base")].strip())
    return chaves


def palavras_chave(texto_normalizado: str) -> str:
    """Só as palavras de um texto normalizado: "tri-hexifenidil" vira "tri hexifenidil"."""
    return " ".join(re.findall(r"[a-z0-9]+", texto_normalizado))


def forma_sem_sal(chave: str) -> str:
    """Substância sem sal, hidrato ou forma; as próprias palavras se nada mudar."""
    palavras = palavras_chave(chave)
    base = SAL_ANTES.sub("", palavras, count=1)
    anterior = None
    while anterior != base:
        anterior, base = base, SAL_DEPOIS.sub("", base)
    if len(base) < 4 or base in ANIONS:
        return palavras
    return base


def substancias(principios_ativos: str | None) -> frozenset[str]:
    """Cada princípio ativo de ``bulas.principios_ativos`` na forma sem sal.

    Usa a chave mais curta do princípio: "amoxicilina (sob a forma
    tri-hidratada)" conta como "amoxicilina".
    """
    saida = set()
    for principio in (principios_ativos or "").split(";"):
        chaves = sorted(chaves_do_principio(principio.strip()), key=len)
        if chaves:
            saida.add(forma_sem_sal(chaves[0]))
    return frozenset(saida)


class CatalogoMedicamentos:
    """Índices em memória, montados uma vez por execução."""

    def __init__(self, medicamentos: Iterable[MedicamentoCatalogo]) -> None:
        self._principios: dict[str, list[Vinculo]] = defaultdict(list)
        self._sem_sal: dict[str, list[Vinculo]] = defaultdict(list)
        self._nomes: dict[str, list[Vinculo]] = defaultdict(list)
        self._equivalencias: dict[str, list[Vinculo]] = defaultdict(list)
        # Termos procuráveis no texto, em palavras: substâncias e nomes.
        self._termos_principio: set[str] = set()
        self._termos_nome: set[str] = set()
        self._substancias: dict[str, frozenset[str]] = {}
        self.quantidade = 0
        for medicamento in medicamentos:
            self.quantidade += 1
            self._indexar(medicamento)

    def _indexar(self, medicamento: MedicamentoCatalogo) -> None:
        nome = medicamento.nome_normalizado
        chaves_principios: set[str] = set()
        for principio in (medicamento.principios_ativos or "").split(";"):
            principio = principio.strip()
            for chave in chaves_do_principio(principio):
                chaves_principios.add(chave)
                exato = Vinculo(nome, CriterioVinculo.PRINCIPIO_ATIVO_EXATO, principio)
                for forma in {chave, palavras_chave(chave)}:
                    self._principios[forma].append(exato)
                sem_sal = forma_sem_sal(chave)
                self._sem_sal[sem_sal].append(
                    Vinculo(nome, CriterioVinculo.PRINCIPIO_ATIVO_SEM_SAL, principio)
                )
                self._termos_principio.update({palavras_chave(chave), sem_sal})
        self._substancias[nome] = substancias(medicamento.principios_ativos)
        for chave in {normalizar_alvo(nome), normalizar_alvo(medicamento.nome_exibicao)}:
            if chave:
                vinculo = Vinculo(
                    nome,
                    CriterioVinculo.NOME_MEDICAMENTO_EXATO,
                    medicamento.nome_exibicao or nome,
                )
                for forma in {chave, palavras_chave(chave)}:
                    self._nomes[forma].append(vinculo)
                self._termos_nome.add(palavras_chave(chave))
        for equivalencia in equivalencias_declaradas(
            medicamento.composicao, medicamento.principios_ativos
        ):
            chave = normalizar_alvo(equivalencia.substancia)
            if chave and chave not in chaves_principios:
                vinculo = Vinculo(
                    nome,
                    CriterioVinculo.COMPOSICAO_EQUIVALENCIA_DECLARADA,
                    equivalencia.passagem,
                )
                for forma in {chave, palavras_chave(chave)}:
                    self._equivalencias[forma].append(vinculo)

    def substancias_de(self, nome_normalizado: str) -> frozenset[str]:
        """Princípios ativos do medicamento, sem sal; vazio se não houver cadastro."""
        return self._substancias.get(nome_normalizado, frozenset())

    def termos(self) -> dict[str, TipoAlvo]:
        """Termos do cadastro, em palavras, com o tipo que cada um vincula.

        Princípios ativos e suas formas sem sal valem como ``PRINCIPIO_ATIVO``;
        os demais nomes, como ``MEDICAMENTO``. Equivalências da composição só
        servem ao vínculo: lidas de texto livre, trariam termos como "ácido".
        """
        termos = {termo: TipoAlvo.MEDICAMENTO for termo in self._termos_nome if termo}
        termos.update({termo: TipoAlvo.PRINCIPIO_ATIVO for termo in self._termos_principio if termo})
        return termos

    def resolver(
        self, alvo: str, tipo_alvo: TipoAlvo, medicamento_origem: str
    ) -> list[Vinculo]:
        """Todos os medicamentos com correspondência válida, sem a própria origem."""
        chave = normalizar_alvo(alvo)
        if not chave:
            return []
        palavras = palavras_chave(chave)
        if tipo_alvo == TipoAlvo.PRINCIPIO_ATIVO:
            candidatos = [
                *self._principios.get(chave, ()),
                *self._principios.get(palavras, ()),
                *self._equivalencias.get(chave, ()),
                *self._equivalencias.get(palavras, ()),
                *self._sem_sal.get(forma_sem_sal(chave), ()),
            ]
        elif tipo_alvo == TipoAlvo.MEDICAMENTO:
            candidatos = [*self._nomes.get(chave, ()), *self._nomes.get(palavras, ())]
        else:
            return []
        escolhidos: dict[str, Vinculo] = {}
        for vinculo in candidatos:
            if vinculo.medicamento_relacionado == medicamento_origem:
                continue
            # O critério mais direto prevalece quando há mais de um.
            escolhidos.setdefault(vinculo.medicamento_relacionado, vinculo)
        return [escolhidos[nome] for nome in sorted(escolhidos)]
