"""Hashes e seleção de versões, somente biblioteca padrão; também usado pela API."""

import hashlib
import json

VERSAO_REESCRITA = "linguagem-simples/36"
# A 36 reconstrói com a referência focada no alvo (prompt e validação), completa
# frases cortadas pela bula e troca coadministr*/clearance no acabamento.
# A 35 acrescenta a retomada dos blocos da reconstrução do resumo geral.
# Publicações anteriores continuam compatíveis quando fonte, hash e modelo correspondem.
VERSOES_INDIVIDUAIS = frozenset({"linguagem-simples/25", "linguagem-simples/26"})
VERSOES_COMPATIVEIS = frozenset({VERSAO_REESCRITA, "linguagem-simples/35", "linguagem-simples/34", "linguagem-simples/33", "linguagem-simples/32", "linguagem-simples/31", "linguagem-simples/30", "linguagem-simples/29", "linguagem-simples/28", "linguagem-simples/27"}) | VERSOES_INDIVIDUAIS


def hash_dados(dados) -> str:
    return hashlib.sha256(json.dumps(dados, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def entrada_card(linha: dict) -> dict:
    entrada = {"tipo": "card", "origem": linha["medicamento_origem"],
            "processamento_id": linha["processamento_id"], "hash_trecho": linha["hash_trecho"],
            "sha256_pdf": linha.get("sha256_pdf"), "id": linha["id"], "alvo": linha["alvo"],
            "principios_ativos": linha.get("principios_ativos"),
            "tipo_alvo": linha["tipo_alvo"], "resumo_original": linha.get("resumo_card") or "",
            "evidencias": [{"id": linha["id"], "alvo": linha["alvo"], "texto": linha["evidencia"]}]}
    grupo = sorted(linha.get("resumos_mesmo_alvo") or [], key=lambda i: i["id"])
    if len(grupo) > 1:
        entrada.update(id=grupo[0]["id"], alvo=grupo[0]["alvo"], tipo_alvo=grupo[0]["tipo_alvo"],
                       resumo_original="\n".join(dict.fromkeys(i["resumo_card"].strip() for i in grupo)),
                       evidencias=[{"id": i["id"], "alvo": i["alvo"], "texto": i["evidencia"]} for i in grupo],
                       resumos_agrupados=[{"id": i["id"], "texto": i["resumo_card"]} for i in grupo])
    return entrada


def entrada_geral(linha: dict, interacoes: list[dict]) -> dict:
    return {"tipo": "geral", "origem": linha["medicamento_origem"],
            "processamento_id": linha["processamento_id"], "hash_trecho": linha["hash_trecho"],
            "sha256_pdf": linha.get("sha256_pdf"), "resumo_original": linha.get("resumo_geral") or "",
            "principios_ativos": linha.get("principios_ativos"),
            "evidencias": [{"id": i["id"], "alvo": i["alvo"], "texto": i["evidencia"]}
                           for i in sorted(interacoes, key=lambda i: i["id"])]}


def simples_atual(texto, controle, entrada, modelo=None) -> bool:
    """Texto antigo permanece armazenado, mas nunca é servido com entradas novas."""
    publicado = (controle or {}).get("publicado") or {}
    referencia = entrada
    if publicado.get("prompt") in VERSOES_INDIVIDUAIS and entrada.get("resumos_agrupados"):
        # As versões anteriores à consolidação gravavam cada registro
        # separadamente. Confere sua própria fonte, sem exigir uma nova
        # inferência apenas porque a interface agora agrupa o mesmo alvo.
        registro = publicado.get("id")
        originais = [i for i in entrada["resumos_agrupados"] if i["id"] == registro]
        evidencias = [e for e in entrada["evidencias"] if e["id"] == registro]
        if len(originais) != 1 or len(evidencias) != 1:
            return False
        referencia = {k: v for k, v in entrada.items() if k != "resumos_agrupados"}
        referencia.update(id=registro, resumo_original=originais[0]["texto"], evidencias=evidencias)
    return bool(texto and texto.strip()
                and publicado.get("status") == "CONCLUIDO"
                and publicado.get("prompt") in VERSOES_COMPATIVEIS
                and publicado.get("entrada_hash") == hash_dados(referencia)
                and publicado.get("texto_hash") == hash_dados(texto)
                and (modelo is None or publicado.get("modelo") == modelo))


def reescrita_salva(texto, controle) -> bool:
    """Retomada: publicação já salva não é refeita por mudança de versão."""
    publicado = (controle or {}).get("publicado") or {}
    return bool(texto and texto.strip() and publicado.get("status") == "CONCLUIDO")


def origem_original(linha: dict) -> str:
    # Os relatórios antigos distinguem reaproveitamento, mas nem toda linha do
    # banco tinha essa informação. Não inventa retroativamente a proveniência.
    return linha.get("resumo_origem") or (
        "literal" if linha.get("resumo_modelo") == "trecho_literal" else "nao_registrada")
