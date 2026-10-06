"""Scraper do Chaves na Mão (imóveis à venda na cidade do Rio de Janeiro).

Fonte: API JSON usada pelo próprio site, descoberta no reconhecimento
(docs/reconhecimento.md):

    GET https://www.chavesnamao.com.br/api/realestate/listing/items/
        ?level1=<tipo>-a-venda&level2=rj-rio-de-janeiro&pg=N
        &filtro=pmin:<min>,pmax:<max>&server=0&viewport=desktop

São 15 anúncios por página e a Cloudflare bloqueia (403) da página 100
em diante. Por isso fatiamos por faixa de preço até cada fatia ter no
máximo 99 × 15 = 1485 anúncios.

Limitação conhecida da API: na ordem padrão (relevância), cerca de 14
anúncios de cada fatia nunca aparecem. As últimas páginas trazem
"imóveis similares" no lugar deles, com ou sem o parâmetro `quebra`.
Eles existem e aparecem em outras ordenações, mas espalhados por todas
as páginas. Como a perda é por consulta, juntamos
fatias vizinhas (juntar_fatias) para fazer o menor número de consultas.
Nas fatias de até 700 anúncios, onde a perda pesa mais, completamos com
mais duas ordenações (or:3 e or:4), que mostram os anúncios escondidos.
Nas grandes, a perda (~1,4% no total) fica registrada no log, fatia por fatia.

Dados do anunciante (nome, telefones, CRECI) são descartados na coleta:
não são necessários para o modelo e são dados pessoais (LGPD).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

from .common import DIR_RAW, Fetcher, fatias_preco_area, juntar_fatias, log, salvar_jsonl

URL_ITENS = "https://www.chavesnamao.com.br/api/realestate/listing/items/"
CIDADE = "rj-rio-de-janeiro"
POR_PAGINA = 15
MAX_PAGINAS = 99
LIMITE = POR_PAGINA * MAX_PAGINAS
# Fatias com até esta quantidade de anúncios são completadas com outras ordenações.
COMPLETAR_ATE = 700
ORDENS_COMPLEMENTARES = (3, 4)

# level1 da URL do site -> (tipo no nosso schema, subtipo)
TIPOS = {
    "casas": ("casa", "casa"),
    "casas-em-condominio": ("casa", "casa_condominio"),
    "apartamentos": ("apartamento", "apartamento"),
    "coberturas": ("apartamento", "cobertura"),
}

CAMPOS_DESCARTADOS = {
    "publisher", "breadcrumb", "description", "descriptionRaw", "leadsConfig", "media",
    "pictures", "proximities", "badges", "tracking", "gmb",
}


class ChavesNaMao:
    def __init__(self, fetcher: Fetcher | None = None) -> None:
        self.f = fetcher or Fetcher(
            delay=1.0,
            headers_extra={"Accept": "application/json"},
            # 403 no meio da coleta (páginas <= 99) indica bloqueio temporário.
            status_retry=(403, 429, 500, 502, 503, 504),
        )

    def _pagina(self, level1: str, pmin: int, pmax: int, pg: int,
                amin: int | None = None, amax: int | None = None,
                ordem: int | None = None) -> dict:
        filtro = f"pmin:{pmin},pmax:{pmax}"
        if amin is not None:
            filtro += f",amin:{amin},amax:{amax}"
        if ordem is not None:
            filtro += f",or:{ordem}"
        params = {
            "level1": f"{level1}-a-venda",
            "level2": CIDADE,
            "pg": pg,
            "filtro": filtro,
            "server": 0,
            "viewport": "desktop",
        }
        url = URL_ITENS + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return json.loads(self.f.get(url))

    def anuncios(self, level1: str, pmin: int = 1, pmax: int = 300_000_000,
                 max_fatias: int | None = None) -> Iterator[dict]:
        def contar(a: int, b: int, amin: int | None, amax: int | None) -> int:
            meta = self._pagina(level1, a, b, 1, amin, amax)["metadata"]
            return int(meta.get("realResults") or 0)

        vistos: set[int] = set()
        self.resumo: list[dict] = []
        fatias = juntar_fatias(sorted(fatias_preco_area(contar, pmin, pmax, LIMITE),
                                      key=lambda f: (f[0], -1 if f[2] is None else f[2])), LIMITE)
        for i, (a, b, amin, amax, n_soma) in enumerate(fatias):
            if max_fatias is not None and i >= max_fatias:
                break
            # A contagem da fatia vem da própria consulta (a soma das fatias
            # juntadas pode vir de respostas antigas do cache).
            primeira = self._pagina(level1, a, b, 1, amin, amax)
            n = int(primeira["metadata"].get("realResults") or n_soma)
            paginas = min(MAX_PAGINAS, -(-n // POR_PAGINA))
            rotulo = f"R$ {a:,}–{b:,}" + ("" if amin is None else f" área {amin}–{amax} m²")
            log.info("Chaves na Mão %s faixa %s: %d anúncios, %d páginas", level1, rotulo, n, paginas)

            na_fatia: set[int] = set()
            novos = repetidos = 0
            ordens = [None] + (list(ORDENS_COMPLEMENTARES) if n <= COMPLETAR_ATE else [])
            for ordem in ordens:
                if ordem is not None and len(na_fatia) >= n:
                    break
                for pg in range(1, paginas + 1):
                    resposta = self._pagina(level1, a, b, pg, amin, amax, ordem)
                    baixado_em = self.f.data_ultima_resposta
                    for it in self._reais(resposta):
                        preco = (it.get("prices") or {}).get("rawPrice")
                        if preco is None or not (a <= preco <= b):
                            continue
                        if it["id"] in na_fatia:
                            repetidos += ordem is None  # repetição na ordem padrão = ordem instável
                            continue
                        na_fatia.add(it["id"])
                        if it["id"] in vistos:  # já veio em outra fatia (o preço mudou)
                            continue
                        vistos.add(it["id"])
                        novos += 1
                        registro = {k: v for k, v in it.items() if k not in CAMPOS_DESCARTADOS}
                        registro["_data_coleta"] = baixado_em
                        yield registro

            self.resumo.append({"faixa": rotulo, "anunciados": n, "coletados": len(na_fatia),
                                "novos": novos, "repetidos": repetidos})
            if len(na_fatia) < n or repetidos:
                log.info("  coletados %d de %d (faltam %d; %d ids repetidos na ordem padrão)",
                         len(na_fatia), n, max(0, n - len(na_fatia)), repetidos)

        anunciados = sum(r["anunciados"] for r in self.resumo)
        if anunciados:
            log.info("Chaves na Mão %s: %d anúncios distintos de %d anunciados (%.1f%%) em %d faixas; "
                     "%d ids repetidos na ordem padrão",
                     level1, len(vistos), anunciados, 100 * len(vistos) / anunciados, len(self.resumo),
                     sum(r["repetidos"] for r in self.resumo))

    @staticmethod
    def _reais(resposta: dict) -> list[dict]:
        """Anúncios da página antes do marcador de "imóveis similares"."""
        reais = []
        for x in resposta.get("items", []):
            if isinstance(x, dict) and "recommendedCount" in x:
                break
            if isinstance(x, dict) and "id" in x:
                reais.append(x)
        return reais


def coletar(tipos: list[str] | None = None, teste: bool = False,
            data_coleta: str | None = None) -> int:
    hoje = data_coleta or date.today().isoformat()
    cnm = ChavesNaMao()
    total = 0
    for level1 in tipos or list(TIPOS):
        tipo, subtipo = TIPOS[level1]
        registros = []
        for r in cnm.anuncios(level1, max_fatias=1 if teste else None):
            r["_tipo"], r["_subtipo"] = tipo, subtipo
            r.setdefault("_data_coleta", hoje)
            registros.append(r)
        sufixo = "_teste" if teste else ""
        caminho = DIR_RAW / f"chavesnamao_{level1}_{hoje}{sufixo}.jsonl"
        n = salvar_jsonl(registros, caminho)
        log.info("Chaves na Mão %s: %d anúncios salvos em %s", level1, n, caminho)
        total += n
    return total
