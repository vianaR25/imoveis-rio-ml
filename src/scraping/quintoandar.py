"""Scraper do QuintoAndar (imóveis à venda na cidade do Rio de Janeiro).

Fonte: API JSON usada pelo próprio site, descoberta no reconhecimento
(docs/reconhecimento.md):

    POST https://apigw.prod.quintoandar.com.br/house-listing-search/v3/search/list

A API pagina por `offset` até offset + pageSize <= 1000. Para cobrir todos
os anúncios, fatiamos por faixa de preço (e de área, se preciso) e paginamos
cada fatia ordenando por MOST_RECENT, que é determinístico.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Iterator

from .common import DIR_RAW, Fetcher, fatias_preco_area, log, salvar_jsonl

URL_LISTA = "https://apigw.prod.quintoandar.com.br/house-listing-search/v3/search/list"
SLUG_RIO = "rio-de-janeiro-rj-brasil"
LIMITE_API = 1000
TAM_PAGINA = 100

CAMPOS = [
    "id", "salePrice", "iptuPlusCondominium", "area", "address", "regionName",
    "city", "type", "forSale", "isPrimaryMarket", "bedrooms", "parkingSpaces",
    "suites", "bathrooms", "neighbourhood", "isFurnished", "installations",
    "amenities", "categories", "listingTags", "developmentName",
    "constructionStatus", "shortSaleDescription",
]

CORPO_BASE = {
    "slug": SLUG_RIO,
    "topics": [],
    "fields": CAMPOS,
    "sorting": {"criteria": "MOST_RECENT", "order": "DESC"},
    "pagination": {"pageSize": TAM_PAGINA, "offset": 0},
    "context": {"listShowing": True, "mapShowing": False, "numPhotos": 0, "isSSR": False},
    "filters": {
        "unknownSlugs": [],
        "enableFlexibleSearch": False,
        "businessContext": "SALE",
        "location": {"coordinate": {}, "viewport": {}, "neighborhoods": [], "countryCode": "BR"},
        "priceRange": [],
        "availability": "ANY",
        "occupancy": "ANY",
        "partnerIds": [],
        "specialConditions": [],
        "excludedSpecialConditions": [],
        "blocklist": [],
        "selectedHouses": [],
        "categories": [],
        "houseSpecs": {
            "area": {"range": {}},
            "houseTypes": [],
            "amenities": [],
            "installations": [],
            "bathrooms": {"range": {}},
            "bedrooms": {"range": {}},
            "parkingSpace": {"range": {}},
            "suites": {"range": {}},
        },
        "origin": "HYBRID",
    },
    "locationDescriptions": [{"description": SLUG_RIO}],
}


class QuintoAndar:
    def __init__(self, fetcher: Fetcher | None = None) -> None:
        self.f = fetcher or Fetcher(
            delay=1.0,
            headers_extra={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.quintoandar.com.br",
                "Referer": "https://www.quintoandar.com.br/",
            },
        )

    def _buscar(self, pmin: int, pmax: int, offset: int, tam: int = TAM_PAGINA,
                amin: int | None = None, amax: int | None = None) -> dict:
        corpo = copy.deepcopy(CORPO_BASE)
        corpo["filters"]["priceRange"] = [
            {"costType": "SALE_PRICE", "range": {"min": pmin, "max": pmax}}
        ]
        if amin is not None:
            corpo["filters"]["houseSpecs"]["area"] = {"range": {"min": amin, "max": amax}}
        corpo["pagination"] = {"pageSize": tam, "offset": offset}
        return self.f.post_json(URL_LISTA, corpo)

    def contar(self, pmin: int, pmax: int, amin: int | None = None, amax: int | None = None) -> int:
        # A primeira página (offset 0) também serve de contagem e fica em cache.
        return self._buscar(pmin, pmax, 0, amin=amin, amax=amax)["hits"]["total"]["value"]

    def anuncios(self, pmin: int = 1, pmax: int = 300_000_000,
                 max_fatias: int | None = None) -> Iterator[dict]:
        vistos: set[int] = set()
        fatias = fatias_preco_area(self.contar, pmin, pmax, LIMITE_API)
        for i, (a, b, amin, amax, n) in enumerate(fatias):
            if max_fatias is not None and i >= max_fatias:
                break
            log.info("QuintoAndar faixa R$ %s–%s%s: %d anúncios", f"{a:,}", f"{b:,}",
                     "" if amin is None else f" área {amin}–{amax} m²", n)
            for offset in range(0, min(n, LIMITE_API), TAM_PAGINA):
                tam = min(TAM_PAGINA, LIMITE_API - offset)
                hits = self._buscar(a, b, offset, tam, amin, amax)["hits"]["hits"]
                baixado_em = self.f.data_ultima_resposta
                if not hits:
                    break
                for h in hits:
                    src = h["_source"]
                    if src["id"] in vistos:
                        continue
                    vistos.add(src["id"])
                    src.pop("shortSaleDescription", None)  # texto livre: não guardamos
                    src["_origem"] = h.get("origin")
                    src["_data_coleta"] = baixado_em
                    yield src


def coletar(teste: bool = False, data_coleta: str | None = None) -> int:
    hoje = data_coleta or date.today().isoformat()
    qa = QuintoAndar()
    registros = []
    for r in qa.anuncios(max_fatias=2 if teste else None):
        r.setdefault("_data_coleta", hoje)
        registros.append(r)
    sufixo = "_teste" if teste else ""
    caminho = DIR_RAW / f"quintoandar_{hoje}{sufixo}.jsonl"
    n = salvar_jsonl(registros, caminho)
    log.info("QuintoAndar: %d anúncios salvos em %s", n, caminho)
    return n
