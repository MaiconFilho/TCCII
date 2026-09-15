"""Preserva leitura por coluna e separa cabecalhos colados ao corpo."""
import re
import pymupdf
from .validacao import PADRAO_TITULO_INTERACOES, normalizar_linha, _eh_titulo_principal_conhecido


def separar_cabecalhos(texto):
    saida=[]
    for linha in texto.splitlines():
        # Titulo numerado em maiusculas seguido de ':' e corpo na mesma linha.
        # Sem numeracao/caixa alta, pode ser apenas uma frase do corpo.
        corte=None
        if re.match(r'^\s*\d{1,2}\s*[.):\-–—]',linha):
            for i in range(5,len(linha)+1):
                prefixo=linha[:i].rstrip()
                resto=linha[i:]
                if not prefixo: continue
                normalizado=normalizar_linha(prefixo)
                reconhecido=PADRAO_TITULO_INTERACOES.fullmatch(normalizado) or _eh_titulo_principal_conhecido(normalizado)
                if not reconhecido or not prefixo.isupper(): continue
                corte=len(linha[:i].rstrip())
            if corte:
                prefixo,resto=linha[:corte].rstrip(),linha[corte:]
                if not resto.strip() or not (prefixo.endswith(':') or
                    (resto[0].isspace() and resto.lstrip()[0].isupper()) or resto.startswith('INTERAÇÕES')):
                    corte=None
        if corte:
            saida.extend((linha[:corte].rstrip(),linha[corte:].lstrip()))
        else: saida.append(linha)
    return '\n'.join(saida)


def ler_texto_nativo(pagina):
    linhas=[]
    for bloco in pagina.get_text('dict')['blocks']:
        if bloco.get('type') != 0: continue
        for linha in bloco.get('lines',[]):
            texto=''.join(s['text'] for s in linha.get('spans',[])).strip()
            if texto: linhas.append((pymupdf.Rect(linha['bbox']),texto))
    ordenadas=ordenar_colunas(linhas)
    texto='\n'.join(t for _,t in ordenadas) if ordenadas is not None else pagina.get_text('text',sort=True)
    return separar_cabecalhos(texto or '')


def ordenar_colunas(linhas, profundidade=0):
    """Procura espaco real entre colunas, nao assume que o divisor e o meio da pagina."""
    if len(linhas)<10 or profundidade>=3: return None
    xs=sorted({r.x0 for r,_ in linhas}|{r.x1 for r,_ in linhas})
    opcoes=[]
    for a,b in zip(xs,xs[1:]):
        if b-a<5: continue
        x=(a+b)/2
        esq=[l for l in linhas if l[0].x1<=x]
        direita=[l for l in linhas if l[0].x0>=x]
        cruzadas=[l for l in linhas if l[0].x0<x<l[0].x1]
        if min(len(esq),len(direita))<5 or len(cruzadas)>len(linhas)*0.10: continue
        # Uma tabela de numeros/rotulos curtos nao deve ser lida como colunas de prosa.
        if min(sum(len(t.split())>=5 for _,t in esq),sum(len(t.split())>=5 for _,t in direita))<3: continue
        opcoes.append(((len(cruzadas),-min(len(esq),len(direita)),-(b-a)),x,esq,direita,cruzadas))
    if not opcoes: return None
    _,x,esq,direita,cruzadas=min(opcoes,key=lambda o:o[0])
    ordenadas=[];restantes=esq+direita
    for separador in sorted(cruzadas,key=lambda l:l[0].y0)+[None]:
        limite=separador[0].y0 if separador else float('inf')
        faixa=[l for l in restantes if l[0].y0<limite]
        restantes=[l for l in restantes if l[0].y0>=limite]
        for coluna in ([l for l in faixa if l[0].x1<=x],[l for l in faixa if l[0].x0>=x]):
            recursiva=ordenar_colunas(coluna,profundidade+1)
            ordenadas.extend(recursiva if recursiva is not None else sorted(coluna,key=lambda l:(l[0].y0,l[0].x0)))
        if separador: ordenadas.append(separador)
    return ordenadas
