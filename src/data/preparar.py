"""Unifica os dados brutos das duas fontes num dataset limpo para modelagem.

Uso:
    python -m src.data.preparar            # usa os arquivos de coleta completa
    python -m src.data.preparar --teste    # usa os arquivos *_teste.jsonl

Saída:
    data/processed/imoveis_rio.parquet  colunas usadas na modelagem (vai para o GitHub)
    data/processed/imoveis_rio.csv      todas as colunas, com url, rua e CEP (só local)
    data/processed/<fonte>_rio.csv      uma planilha por fonte para o Excel (só local)

A limpeza é feita antes da divisão treino/teste, então evita estatísticas
da base que pudessem vazar informação do teste. Nenhuma etapa calcula
médias ou percentis do preço. A deduplicação consulta a base inteira e
compara preços entre anúncios, só para achar repetições. A padronização de
grafias de bairro e a decisão entre as duas Freguesias também consultam a
base inteira, sem usar o preço. Os limites de IPTU anual e de condomínio +
IPTU implausível foram calibrados olhando a base completa; eles não usam o
preço e só mexem em variáveis explicativas.
"""
from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd

from .zonas import canonizar_bairros, normalizar, normalizar_rua, regiao_do_bairro

RAIZ = Path(__file__).resolve().parents[2]
DIR_RAW = RAIZ / "data" / "raw"
DIR_OUT = RAIZ / "data" / "processed"

# Comodidades que viram colunas 0/1. Cada uma é procurada (sem acento,
# minúsculas) nas listas de itens das duas fontes. Só entram comodidades
# medidas do mesmo jeito nas DUAS fontes: elevador e portaria só existem
# no Chaves na Mão, e "mobiliado" vem de um campo booleano no QuintoAndar
# e de um item opcional no Chaves na Mão (as duas medidas discordam para
# o mesmo imóvel). A coluna sem_itens marca anúncios sem nenhuma lista de
# itens, em que as flags valem 0 por falta de informação.
COMODIDADES = {
    "tem_piscina": ["piscina"],
    "tem_churrasqueira": ["churrasqueira"],
    "tem_academia": ["academia"],
    "tem_varanda": ["varanda", "sacada"],
}

# No Chaves na Mão o IPTU vem às vezes mensal e às vezes anual. Decidimos
# pelo valor por m², sem olhar o preço (que é o alvo do modelo): acima de
# R$ 10/m² o valor é tratado como anual. Calibrado em 16.438 imóveis
# anunciados nos dois portais, em que o QuintoAndar dá o total mensal certo:
# acerta 87% dos casos, o mesmo que uma regra baseada no preço.
IPTU_ANUAL_SE_ACIMA_DE_POR_M2 = 10.0
# Condomínio + IPTU mensal acima de R$ 150/m² é erro de digitação evidente
# (mesmo os condomínios de luxo do Leblon ficam abaixo de R$ 100/m²).
COND_IPTU_MAX_POR_M2 = 150.0

# Preço por m² fora desta faixa é erro de digitação (preço ou área). São
# limites de domínio, não percentis da base: nenhum imóvel no Rio é vendido
# abaixo de R$ 1.000/m², e nem a orla do Leblon chega a R$ 50.000/m².
PRECO_M2_MIN, PRECO_M2_MAX = 1_000, 50_000

# Colunas do parquet: as 19 que podem virar variáveis explicativas, o alvo
# (preco), o grupo de imóvel (usado só na divisão treino/teste) e a data.
COLUNAS_MODELO = [
    "tipo", "subtipo", "bairro", "regiao", "fonte",
    "area_m2", "quartos", "banheiros", "suites", "vagas", "condominio_iptu",
    "latitude", "longitude", "lancamento",
    "tem_piscina", "tem_churrasqueira", "tem_academia", "tem_varanda", "sem_itens",
    "preco", "grupo", "data_coleta",
]

# Imóveis retomados pela Caixa (venda direta, leilão, licitação): o preço é
# lance mínimo ou preço com desconto, não preço pedido no mercado.
PADRAO_CAIXA = re.compile(r"venda direta|leil[aã]o|licita[cç]|venda online", re.I)


def _coord(valor) -> float:
    """'-22.97781' -> -22.97781 ; inválido -> nan."""
    try:
        return float(valor)
    except (TypeError, ValueError):
        return np.nan


def _num(valor) -> float:
    """Valores monetários e contagens: 'R$ 1.234' -> 1234.0 ; '220' -> 220.0 ; None -> nan."""
    if valor is None or valor == "":
        return np.nan
    if isinstance(valor, (int, float)):
        return float(valor)
    digitos = re.sub(r"[^\d,]", "", str(valor)).replace(",", ".")
    try:
        return float(digitos) if digitos else np.nan
    except ValueError:
        return np.nan


def _flags(itens: list[str]) -> dict[str, int]:
    palavras = {p for i in itens for p in re.split(r"[\s_()/,]+", normalizar(i)) if p}
    flags = {col: int(any(k in palavras for k in chaves)) for col, chaves in COMODIDADES.items()}
    flags["sem_itens"] = int(not itens)
    return flags


def _ler(padrao: str) -> list[dict]:
    regs = []
    for arq in sorted(DIR_RAW.glob(padrao)):
        with arq.open(encoding="utf-8") as f:
            regs += [json.loads(l) for l in f if l.strip()]
    return regs


# --------------------------------------------------------------- QuintoAndar
TIPOS_QA = {
    "Apartamento": ("apartamento", "apartamento"),
    "StudioOuKitchenette": ("apartamento", "kitnet"),
    "Casa": ("casa", "casa"),
    "CasaCondominio": ("casa", "casa_condominio"),
}


def _linha_quintoandar(r: dict) -> dict | None:
    if r.get("city") != "Rio de Janeiro" or r.get("type") not in TIPOS_QA:
        return None
    tipo, subtipo = TIPOS_QA[r["type"]]
    itens = (r.get("amenities") or []) + (r.get("installations") or [])
    if subtipo == "apartamento" and "APARTAMENTO_COBERTURA" in itens:
        subtipo = "cobertura"
    cond_iptu = _num(r.get("iptuPlusCondominium"))
    if cond_iptu <= 0:  # 0 e -1 significam "não informado"
        cond_iptu = np.nan
    # Anúncios próprios do QuintoAndar ("TRANSACTIONAL") e classificados de
    # imobiliárias parceiras ("CLASSIFIED") têm páginas em rotas diferentes.
    classificado = r.get("_origem") == "CLASSIFIED"
    rota = "classificado" if classificado else "imovel"
    linha = {
        "fonte": "quintoandar",
        "id_fonte": str(r["id"]),
        "origem": "classificado" if classificado else "quintoandar",
        "url": f"https://www.quintoandar.com.br/{rota}/{r['id']}/comprar",
        "tipo": tipo,
        "subtipo": subtipo,
        "preco": _num(r.get("salePrice")),
        "area_m2": _num(r.get("area")),
        "quartos": _num(r.get("bedrooms")),
        "banheiros": _num(r.get("bathrooms")),
        "suites": _num(r.get("suites")),
        "vagas": _num(r.get("parkingSpaces")),
        "condominio_iptu": cond_iptu,
        "bairro": r.get("neighbourhood") or r.get("regionName"),
        "rua": r.get("address"),
        "cep": None,
        "latitude": np.nan,
        "longitude": np.nan,
        "lancamento": int(bool(r.get("isPrimaryMarket"))),
        "venda_caixa": 0,
        "data_coleta": r.get("_data_coleta"),
    }
    linha.update(_flags(itens))
    return linha


# ------------------------------------------------------------- Chaves na Mão
# O tipo vem do próprio anúncio, não do arquivo: a busca de apartamentos
# também devolve as coberturas, e as buscas de casa se misturam nas bordas.
TIPOS_CN = {
    "Apartamento": ("apartamento", "apartamento"),
    "Cobertura": ("apartamento", "cobertura"),
    "Casa / Sobrado": ("casa", "casa"),
    "Casa / Sobrado em Condomínio": ("casa", "casa_condominio"),
}


def _linha_chavesnamao(r: dict) -> dict | None:
    loc = r.get("location") or {}
    if (loc.get("city") or {}).get("name") != "Rio de Janeiro":
        return None
    nome_tipo = (r.get("realtyType") or {}).get("name")
    tipo, subtipo = TIPOS_CN.get(nome_tipo, (r.get("_tipo"), r.get("_subtipo")))
    precos = r.get("prices") or {}
    area = r.get("area") or {}
    geo = loc.get("geoposition") or {}
    preco = _num(precos.get("rawPrice"))
    area_m2 = _num(area.get("useful")) if _num(area.get("useful")) > 0 else _num(area.get("total"))
    cond, iptu = _num(precos.get("condominiumFee")), _num(precos.get("iptuValue"))
    iptu_anual = area_m2 > 0 and iptu / area_m2 > IPTU_ANUAL_SE_ACIMA_DE_POR_M2
    if iptu_anual:
        iptu = iptu / 12
    cond_iptu = np.nan if np.isnan(cond) and np.isnan(iptu) else np.nansum([cond, iptu])
    if not cond_iptu > 0:
        cond_iptu = np.nan
    caixa = (str(r.get("reference") or "").upper().startswith("IMCX")
             or bool(PADRAO_CAIXA.search(r.get("title") or "")))
    itens = [i.get("name", "") for i in (r.get("privativeItems") or []) + (r.get("commonItems") or [])]
    cnt = lambda campo: _num((r.get(campo) or {}).get("count"))
    linha = {
        "fonte": "chavesnamao",
        "id_fonte": str(r["id"]),
        "origem": "chavesnamao",
        "url": "https://www.chavesnamao.com.br" + (r.get("url") or ""),
        "tipo": tipo,
        "subtipo": subtipo,
        "preco": preco,
        "area_m2": area_m2,
        "quartos": cnt("bedrooms"),
        "banheiros": cnt("bathrooms"),
        # O site omite suítes e vagas quando são zero.
        "suites": np.nan_to_num(cnt("suites"), nan=0.0),
        "vagas": np.nan_to_num(cnt("garages"), nan=0.0),
        "condominio_iptu": cond_iptu,
        "bairro": (loc.get("neighborhood") or {}).get("name"),
        "rua": (loc.get("street") or {}).get("name"),
        "cep": loc.get("zipCode") or None,
        "latitude": _coord(geo.get("lat")),
        "longitude": _coord(geo.get("lon")),
        "lancamento": int("/lancamento/" in (r.get("url") or "")),
        "venda_caixa": int(caixa),
        "_iptu_anual": int(iptu_anual),
        "data_coleta": r.get("_data_coleta"),
    }
    linha.update(_flags(itens))
    return linha


# ----------------------------------------------------------------- limpeza
def limpar(df: pd.DataFrame, coletados: int | None = None) -> tuple[pd.DataFrame, dict[str, int], dict[str, int]]:
    """Devolve a base limpa, o funil (linhas após cada etapa) e a contagem
    das correções feitas em valores (sem remover linhas)."""
    passos: dict[str, int] = {} if coletados is None else {"coletados": coletados}
    passos["casas e apartamentos na cidade do Rio"] = len(df)
    ajustes: dict[str, int] = {}

    df = df.drop_duplicates(subset=["fonte", "id_fonte"])
    passos["sem duplicatas por id"] = len(df)

    df = df[df["venda_caixa"] == 0].drop(columns="venda_caixa")
    passos["sem vendas da Caixa (leilão, venda direta)"] = len(df)

    df = df.dropna(subset=["preco", "area_m2", "bairro"])
    df = df[(df["preco"] > 0) & (df["area_m2"] > 0)]
    passos["com preço, área e bairro"] = len(df)

    # Bairro fora do município ou irreconhecível (Niterói, "900", ...).
    df = df[df["regiao"].notna()]
    passos["bairro reconhecido no município do Rio"] = len(df)

    df = df[df["preco"].between(50_000, 50_000_000) & df["area_m2"].between(15, 5_000)]
    df = df[(df["quartos"].isna() | df["quartos"].between(0, 10))
            & (df["banheiros"].isna() | df["banheiros"].between(0, 12))
            & (df["vagas"].isna() | df["vagas"].between(0, 10))]
    passos["faixas plausíveis"] = len(df)

    df = df.assign(preco_m2=df["preco"] / df["area_m2"])
    df = df[df["preco_m2"].between(PRECO_M2_MIN, PRECO_M2_MAX)]
    passos["preço/m² entre R$ 1.000 e R$ 50.000"] = len(df)

    # As correções de valores são marcadas por linha (colunas _aj_*) e
    # contadas no fim, só nas linhas que ficam na base.
    df = df.assign(_aj_iptu_anual=df["_iptu_anual"].fillna(0).astype(bool)).drop(columns="_iptu_anual")
    implausivel = df["condominio_iptu"] > COND_IPTU_MAX_POR_M2 * df["area_m2"]
    df.loc[implausivel, "condominio_iptu"] = np.nan
    df["_aj_cond_implausivel"] = implausivel

    # Latitude e longitude trocadas pelo anunciante: desfaz a troca.
    lat_ok = lambda v: v.between(-23.10, -22.73)
    lon_ok = lambda v: v.between(-43.80, -43.09)
    trocadas = lat_ok(df["longitude"]) & lon_ok(df["latitude"])
    df.loc[trocadas, ["latitude", "longitude"]] = df.loc[trocadas, ["longitude", "latitude"]].to_numpy()
    df["_aj_coord_trocada"] = trocadas

    # Coordenadas fora do retângulo que envolve o município (erros de
    # geocodificação do anunciante).
    fora = df["latitude"].notna() & ~(lat_ok(df["latitude"]) & lon_ok(df["longitude"]))
    df.loc[fora | df["latitude"].isna() | df["longitude"].isna(), ["latitude", "longitude"]] = np.nan
    df["_aj_coord_fora"] = fora

    # O Chaves na Mão usa uma coordenada padrão da Ilha do Governador para as
    # duas Freguesias. Depois de corrigido o bairro, a coordenada do lado
    # errado da latitude que separa as duas é anulada.
    na_ilha = df["latitude"] > LATITUDE_ENTRE_FREGUESIAS
    errada = (((df["bairro"] == JACAREPAGUA) & na_ilha)
              | ((df["bairro"] == ILHA) & df["latitude"].notna() & ~na_ilha))
    df.loc[errada, ["latitude", "longitude"]] = np.nan
    df["_aj_coord_freguesia"] = errada

    # Mesmo imóvel anunciado mais de uma vez (por corretores diferentes ou
    # nas duas fontes). Fica a primeira linha e, com ela, o tipo, quando o
    # mesmo imóvel aparece como casa e como apartamento. É um desempate fixo:
    # as linhas do QuintoAndar vêm antes. O subtipo é o mais específico do
    # grupo entre as linhas desse tipo, porque o QuintoAndar nem sempre marca
    # cobertura ou condomínio.
    df = df.reset_index(drop=True)
    componente = pd.Series(componentes_mesmo_imovel(df))
    primeira = ~componente.duplicated()

    # O que falta na linha mantida vem dos outros anúncios do mesmo imóvel:
    # a coordenada (o QuintoAndar não informa), o condomínio e os banheiros.
    # A coordenada só vem de anúncios do mesmo bairro, porque a regra (b) da
    # deduplicação junta bairros vizinhos e um deles pode ter a coordenada
    # padrão de outro bairro.
    tem_coord = df["latitude"].notna()
    chave = componente.astype(str) + "|" + df["bairro"].astype(str)
    coord = df.loc[tem_coord, ["latitude", "longitude"]].groupby(chave[tem_coord]).first()
    sem_coord = primeira & ~tem_coord
    df.loc[sem_coord, ["latitude", "longitude"]] = coord.reindex(chave[sem_coord]).to_numpy()
    df["_aj_coord_herdada"] = sem_coord & df["latitude"].notna()
    for col in ["condominio_iptu", "banheiros"]:
        faltava = df[col].isna()
        df[col] = df[col].fillna(df[col].groupby(componente).transform("first"))
        df[f"_aj_{col}_herdado"] = faltava & df[col].notna()
    tipo_do_grupo = componente.map(pd.Series(df["tipo"][primeira].to_numpy(), index=componente[primeira]))
    mesmo_tipo = df["tipo"] == tipo_do_grupo
    prioridade = {"cobertura": 3, "casa_condominio": 3, "kitnet": 2, "apartamento": 1, "casa": 1}
    melhor = (df[mesmo_tipo].assign(_r=df["subtipo"].map(prioridade).fillna(0), _c=componente)
              .sort_values("_r", ascending=False, kind="stable").drop_duplicates("_c"))
    df = df.assign(subtipo=componente.map(melhor.set_index("_c")["subtipo"]))
    df = df[primeira]
    passos["sem anúncios repetidos (mesmo imóvel)"] = len(df)

    nomes = {
        "_aj_iptu_anual": "IPTU anual convertido para mensal",
        "_aj_cond_implausivel": "condomínio + IPTU implausível anulado",
        "_aj_coord_trocada": "coordenadas trocadas desfeitas",
        "_aj_coord_fora": "coordenadas fora do retângulo anuladas",
        "_aj_coord_freguesia": "coordenadas da Freguesia errada anuladas",
        "_aj_condominio_iptu_herdado": "condomínio + IPTU herdado do mesmo imóvel",
        "_aj_banheiros_herdado": "banheiros herdados do mesmo imóvel",
    }
    for col, nome in nomes.items():
        ajustes[nome] = int(df[col].sum())
    for fonte, n in df.loc[df["_aj_coord_herdada"], "fonte"].value_counts().items():
        ajustes[f"coordenada herdada do mesmo imóvel ({fonte})"] = int(n)
    df = df.drop(columns=[c for c in df.columns if c.startswith("_aj_")])

    df = df.reset_index(drop=True)
    df["grupo"] = grupos_quase_duplicados(df)
    return df, passos, ajustes


class _UniaoBusca:
    def __init__(self, n: int) -> None:
        self.pai = np.arange(n)

    def raiz(self, i: int) -> int:
        while self.pai[i] != i:
            self.pai[i] = self.pai[self.pai[i]]
            i = self.pai[i]
        return i

    def unir_grupos(self, rotulos: pd.Series) -> None:
        """Une todas as linhas que têm o mesmo rótulo (NaN = sem rótulo)."""
        rotulos = rotulos.dropna()
        for idx in rotulos.groupby(rotulos).indices.values():
            linhas = rotulos.index[idx]
            for j in linhas[1:]:
                ri, rj = self.raiz(linhas[0]), self.raiz(j)
                if ri != rj:
                    self.pai[rj] = ri

    def componentes(self) -> np.ndarray:
        return np.array([self.raiz(i) for i in range(len(self.pai))])


def _rotulo(*colunas: pd.Series) -> pd.Series:
    return pd.concat(colunas, axis=1).astype(str).agg("|".join, axis=1)


def componentes_mesmo_imovel(df: pd.DataFrame) -> np.ndarray:
    """Identifica anúncios do mesmo imóvel (deduplicação).

    São o mesmo imóvel os anúncios com mesma área arredondada e mesmos
    quartos e que estejam
    (a) no mesmo bairro e na mesma rua, com o mesmo preço exato;
    (b) na mesma rua, mesmo com bairros diferentes (os portais divergem
        no bairro de ruas de divisa), com o mesmo preço exato;
    (c) no mesmo bairro e na mesma rua, com preço na mesma faixa de
        R$ 10 mil: o mesmo imóvel anunciado com preços um pouco diferentes
        por corretores diferentes ou nos dois portais; ou
    (d) no mesmo bairro, no mesmo portal, com coordenadas, o mesmo preço
        exato e todas as outras características iguais (inclusive o
        subtipo), mesmo com ruas diferentes: grafias diferentes da mesma
        rua ("Av. Ator José Wilker" e "Av. José Wilker") ou as duas ruas
        de um prédio de esquina. Sem coordenada, a igualdade das demais
        características não basta; ou
    (e) no mesmo bairro, com o mesmo preço exato e ruas de grafia parecida
        (similaridade de texto de pelo menos 0,85): "Corrêa" e "Correia",
        "Gen. San Martin" e "General San Martin", "Rua Riachuelo" e
        "Rua Riachuelo Al".
    Anúncio com a rua em branco recebe, uma única vez, a rua mais
    anunciada entre os pares com o mesmo preço exato ou, na falta deles,
    com preço na mesma faixa; sem nenhum par com rua, os anúncios sem rua
    se juntam entre si.
    O tipo não entra, porque o mesmo imóvel às vezes é anunciado como casa
    num portal e apartamento no outro (ou pelos dois corretores do mesmo
    portal). Fora da regra (d), anúncios do mesmo bairro com ruas
    diferentes NÃO são juntados: são imóveis distintos com as mesmas
    características.
    """
    rua = df["rua"].map(normalizar_rua).replace("", np.nan)
    bairro = df["bairro"].map(normalizar)
    base = [df["area_m2"].round(), df["quartos"]]
    chave_exata = _rotulo(*base, df["preco"], bairro)
    chave_faixa = _rotulo(*base, (df["preco"] / 1e4).round(), bairro)
    rua_completa = (rua.fillna(_rua_mais_anunciada(chave_exata, rua))
                    .fillna(_rua_mais_anunciada(chave_faixa, rua)).fillna("sem rua"))
    uf = _UniaoBusca(len(df))
    uf.unir_grupos(_rotulo(chave_exata, rua_completa))                                  # (a)
    uf.unir_grupos(_rotulo(*base, df["preco"], rua.fillna("")).where(rua.notna()))     # (b)
    uf.unir_grupos(_rotulo(chave_faixa, rua_completa))                                  # (c)
    resto = [df[c] for c in COLUNAS_MODELO if c not in ("tipo", "area_m2", "quartos", "grupo", "data_coleta")]
    uf.unir_grupos(_rotulo(*base, df["area_m2"], *resto).where(df["latitude"].notna()))  # (d)
    uf.unir_grupos(_ruas_parecidas(chave_exata, rua))                                   # (e)
    return uf.componentes()


SIMILARIDADE_RUA = 0.85


def _ruas_parecidas(chave: pd.Series, rua: pd.Series) -> pd.Series:
    """Rótulo chave + rua canônica: dentro de cada chave, ruas com grafias
    parecidas recebem o mesmo nome (o primeiro em ordem alfabética)."""
    rotulo = pd.Series(np.nan, index=rua.index, dtype=object)
    candidatos = rua.notna() & chave.duplicated(keep=False)
    for k, idx in rua[candidatos].groupby(chave[candidatos]).groups.items():
        canonicas: list[str] = []
        nome: dict[str, str] = {}
        for r in sorted(set(rua[idx])):
            par = next((c for c in canonicas if SequenceMatcher(None, c, r).ratio() >= SIMILARIDADE_RUA), None)
            if par is None:
                canonicas.append(r)
            nome[r] = par or r
        rotulo.loc[idx] = [f"{k}|{nome[r]}" for r in rua[idx]]
    return rotulo


def _rua_mais_anunciada(chave: pd.Series, rua: pd.Series) -> pd.Series:
    """Para anúncios sem rua, a rua mais anunciada entre os que têm a mesma chave."""
    com_rua = rua.notna()
    mais = rua[com_rua].groupby(chave[com_rua]).agg(lambda s: s.value_counts().index[0])
    return chave.map(mais).where(~com_rua)


def grupos_quase_duplicados(df: pd.DataFrame) -> np.ndarray:
    """Agrupa anúncios que PODEM ser o mesmo imóvel, para a divisão
    treino/teste não separar um imóvel entre os dois lados. É mais largo
    que a deduplicação. O tipo não entra, porque o mesmo imóvel às vezes é
    anunciado como casa num portal e apartamento no outro.

    Duas linhas com os mesmos quartos ficam no mesmo grupo se tiverem
    (a) mesmo bairro, mesma área arredondada e preço na mesma faixa de R$ 10 mil;
    (b) mesmo bairro e o mesmo preço exato não redondo (área pode variar);
    (c) mesma rua, mesma área arredondada e preço na mesma faixa de R$ 10 mil
        em uma de duas grades, a segunda deslocada em R$ 5 mil. Assim,
        qualquer par até R$ 5 mil diferente fica junto, mesmo dos dois lados
        da borda de uma faixa, e pares até R$ 9.999 diferentes podem ficar; ou
    (d) mesma rua, o mesmo preço exato e área até 2 m² diferente.
    Grupos ligados por transitividade são unidos (union-find).

    Limitação aceita: na regra (a), sem a rua, dois preços quase iguais em
    lados opostos da borda de uma faixa (R$ 205.000 e R$ 205.500) ficam em
    grupos diferentes. Deslocar a faixa no nível do bairro encadearia
    milhares de anúncios parecidos num grupo só.
    """
    rua = df["rua"].map(normalizar_rua).replace("", np.nan)
    tem_rua = rua.notna()
    rua = rua.fillna("")
    quartos = df["quartos"]
    bairro = df["bairro"].map(normalizar)
    area = df["area_m2"].round()
    faixa_preco = (df["preco"] / 1e4).round()
    uf = _UniaoBusca(len(df))
    uf.unir_grupos(_rotulo(quartos, bairro, area, faixa_preco))                                # (a)
    uf.unir_grupos(_rotulo(quartos, bairro, df["preco"]).where(df["preco"] % 10_000 != 0))      # (b)
    # (c): como na regra (d), duas grades de faixas, a segunda deslocada pela
    # metade, para que qualquer par até R$ 5 mil diferente caia numa faixa em comum.
    for faixa in [np.floor(df["preco"] / 1e4), np.floor((df["preco"] + 5_000) / 1e4)]:
        uf.unir_grupos(_rotulo(quartos, rua, area, faixa).where(tem_rua))
    # (d): faixas de área de 4 m² usadas duas vezes, a segunda deslocada em 2 m²,
    # para que qualquer par com diferença de até 2 m² caia numa faixa em comum.
    for faixa_area in [np.floor(df["area_m2"] / 4), np.floor((df["area_m2"] + 2) / 4)]:
        uf.unir_grupos(_rotulo(quartos, rua, df["preco"], faixa_area).where(tem_rua))
    return uf.componentes()


ILHA, JACAREPAGUA = "Freguesia (Ilha do Governador)", "Freguesia (Jacarepaguá)"
LATITUDE_ENTRE_FREGUESIAS = -22.86  # ao norte, Ilha do Governador; ao sul, Jacarepaguá


def corrigir_freguesia(df: pd.DataFrame) -> pd.Series:
    """O Rio tem duas Freguesias, e o Chaves na Mão às vezes rotula a de
    Jacarepaguá como a da Ilha do Governador (com uma coordenada padrão na
    Ilha e sem CEP). Decide, nesta ordem: pelo CEP (219xx = Ilha;
    227xx/223xx = Jacarepaguá), pela rua (ruas vistas com CEP ou rótulo
    confiável de uma das duas) e pela latitude, ignorando coordenadas
    repetidas por muitos anúncios.
    """
    bairro = df["bairro"].copy()
    freg = bairro.map(normalizar).fillna("").str.startswith("freguesia")
    if not freg.any():
        return bairro
    cep = df["cep"].fillna("").astype(str).str.replace(r"\D", "", regex=True)
    por_cep = pd.Series(np.select([cep.str.startswith("219"), cep.str[:3].isin(["227", "223"])],
                                  [ILHA, JACAREPAGUA], default=""), index=df.index)
    rua = df["rua"].map(normalizar_rua)
    rotulo_confiavel = por_cep.where(por_cep != "", bairro)
    confiavel = (freg & ((por_cep != "") | (df["fonte"] == "quintoandar"))
                 & rotulo_confiavel.isin([ILHA, JACAREPAGUA]))
    votos = (pd.DataFrame({"rua": rua[confiavel], "b": rotulo_confiavel[confiavel]})
             .query("rua != ''").groupby("rua")["b"].agg(lambda s: s.value_counts().index[0]))
    por_rua = rua.map(votos).fillna("")
    coord = df["latitude"].round(5).astype(str) + "," + df["longitude"].round(5).astype(str)
    repetida = coord.map(coord[freg].value_counts()).fillna(0) >= 20
    por_lat = pd.Series(np.where(df["latitude"] > LATITUDE_ENTRE_FREGUESIAS, ILHA, JACAREPAGUA), index=df.index)
    por_lat = por_lat.where(df["latitude"].notna() & ~repetida, "")
    decidido = por_cep.where(por_cep != "", por_rua.where(por_rua != "", por_lat))
    alterar = freg & (df["fonte"] == "chavesnamao") & (decidido != "")
    bairro[alterar] = decidido[alterar]
    return bairro


COLUNAS_PLANILHA = [
    "id_fonte", "origem", "url", "tipo", "subtipo", "bairro", "rua", "regiao", "preco", "area_m2",
    "preco_m2", "quartos", "banheiros", "suites", "vagas", "condominio_iptu", "latitude",
    "longitude", "tem_piscina", "tem_churrasqueira", "tem_academia", "tem_varanda",
    "sem_itens", "lancamento", "grupo", "data_coleta",
]


def exportar_planilhas(df: pd.DataFrame) -> None:
    """Um CSV por fonte no padrão do Excel em português (';' e vírgula decimal)."""
    for fonte, parte in df.groupby("fonte"):
        p = parte[COLUNAS_PLANILHA].sort_values(["regiao", "bairro", "preco"]).copy()
        if p["latitude"].isna().all():
            p = p.drop(columns=["latitude", "longitude"])
        p["preco_m2"] = p["preco_m2"].round(0)
        for c in ["quartos", "banheiros", "suites", "vagas"]:
            p[c] = p[c].astype("Int64")
        p.to_csv(DIR_OUT / f"{fonte}_rio.csv", index=False, sep=";", decimal=",", encoding="utf-8-sig")


def preparar(teste: bool = False) -> pd.DataFrame:
    sufixo = "_teste.jsonl" if teste else ".jsonl"
    filtro = (lambda p: p.name.endswith("_teste.jsonl")) if teste else (lambda p: not p.name.endswith("_teste.jsonl"))
    qa = [r for p in sorted(DIR_RAW.glob("quintoandar_*" + sufixo)) if filtro(p) for r in _ler(p.name)]
    cn = [r for p in sorted(DIR_RAW.glob("chavesnamao_*" + sufixo)) if filtro(p) for r in _ler(p.name)]

    linhas = [l for l in map(_linha_quintoandar, qa) if l] + [l for l in map(_linha_chavesnamao, cn) if l]
    df = pd.DataFrame(linhas)
    df["bairro"] = canonizar_bairros(df["bairro"])
    df["bairro"] = corrigir_freguesia(df)
    df["regiao"] = df["bairro"].map(regiao_do_bairro)

    df, passos, ajustes = limpar(df, coletados=len(qa) + len(cn))

    DIR_OUT.mkdir(parents=True, exist_ok=True)
    nome = "imoveis_rio_teste" if teste else "imoveis_rio"
    df.to_csv(DIR_OUT / f"{nome}.csv", index=False)
    df[COLUNAS_MODELO].to_parquet(DIR_OUT / f"{nome}.parquet", index=False)
    if not teste:
        exportar_planilhas(df)

    print("\nFunil de limpeza:")
    for etapa, n in passos.items():
        print(f"  {etapa:<45} {n:>8,}")
    print("\nCorreções de valores (linhas da base final):")
    for ajuste, n in ajustes.items():
        print(f"  {ajuste:<50} {n:>8,}")
    print("\nPor fonte e tipo:\n", df.groupby(["fonte", "tipo"]).size().to_string())
    print("\nSem região reconhecida:", df["regiao"].isna().sum(),
          "| bairros:", df.loc[df["regiao"].isna(), "bairro"].value_counts().head(15).to_dict())
    print("\nNulos por coluna (%):\n", (df.isna().mean() * 100).round(1)[lambda s: s > 0].to_string())
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--teste", action="store_true")
    preparar(teste=ap.parse_args().teste)
