"""Utilitários compartilhados pelos scrapers.

- Fetcher: sessão HTTP com User-Agent de navegador, ritmo educado
  (~1 requisição/segundo com jitter), retries com backoff exponencial
  e cache em disco para que reexecuções não batam de novo no site.
- salvar_jsonl / ler_jsonl: persistência simples, um registro por linha.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import requests

RAIZ = Path(__file__).resolve().parents[2]
DIR_CACHE = RAIZ / "cache"
DIR_RAW = RAIZ / "data" / "raw"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

log = logging.getLogger("scraping")
if not log.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


class Fetcher:
    """Cliente HTTP educado, com cache e retries."""

    def __init__(
        self,
        delay: float = 1.0,
        jitter: float = 0.5,
        tentativas: int = 4,
        timeout: float = 30.0,
        usar_cache: bool = True,
        headers_extra: dict[str, str] | None = None,
        status_retry: tuple[int, ...] = (429, 500, 502, 503, 504),
    ) -> None:
        self.status_retry = status_retry
        self.delay = delay
        self.jitter = jitter
        self.tentativas = tentativas
        self.timeout = timeout
        self.usar_cache = usar_cache
        self._ultimo = 0.0
        # Data em que a última resposta foi baixada (a do arquivo, se veio do cache).
        self.data_ultima_resposta: str = date.today().isoformat()
        self.sessao = requests.Session()
        self.sessao.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            }
        )
        if headers_extra:
            self.sessao.headers.update(headers_extra)
        DIR_CACHE.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ cache
    @staticmethod
    def _chave(metodo: str, url: str, corpo: Any) -> Path:
        bruto = metodo + " " + url + " " + json.dumps(corpo, sort_keys=True, ensure_ascii=False)
        return DIR_CACHE / (hashlib.sha256(bruto.encode()).hexdigest() + ".txt")

    # ------------------------------------------------------------- ritmo
    def _esperar(self) -> None:
        alvo = self.delay + random.uniform(0, self.jitter)
        decorrido = time.monotonic() - self._ultimo
        if decorrido < alvo:
            time.sleep(alvo - decorrido)
        self._ultimo = time.monotonic()

    # ------------------------------------------------------------- requisição
    def requisitar(self, url: str, metodo: str = "GET", json_body: Any = None) -> str:
        """Retorna o corpo da resposta como texto. Lança exceção após esgotar retries."""
        arq = self._chave(metodo, url, json_body)
        if self.usar_cache and arq.exists():
            self.data_ultima_resposta = datetime.fromtimestamp(arq.stat().st_mtime).date().isoformat()
            return arq.read_text(encoding="utf-8")
        self.data_ultima_resposta = date.today().isoformat()

        ultimo_erro: Exception | None = None
        for tentativa in range(1, self.tentativas + 1):
            self._esperar()
            try:
                resp = self.sessao.request(metodo, url, json=json_body, timeout=self.timeout)
                if resp.status_code == 200:
                    texto = resp.text
                    if self.usar_cache:
                        arq.write_text(texto, encoding="utf-8")
                    return texto
                if resp.status_code in self.status_retry:
                    ultimo_erro = RuntimeError(f"HTTP {resp.status_code}")
                else:
                    raise RuntimeError(f"HTTP {resp.status_code} em {url}: {resp.text[:200]}")
            except (requests.Timeout, requests.ConnectionError) as erro:
                ultimo_erro = erro
            espera = 5 * 2 ** tentativa + random.uniform(0, 2)
            log.warning("Falha (%s) em %s; tentativa %d/%d, aguardando %.1fs",
                        ultimo_erro, url, tentativa, self.tentativas, espera)
            time.sleep(espera)
        raise RuntimeError(f"Esgotadas as tentativas para {url}: {ultimo_erro}")

    def get(self, url: str) -> str:
        return self.requisitar(url, "GET")

    def post_json(self, url: str, corpo: Any) -> Any:
        return json.loads(self.requisitar(url, "POST", corpo))


def salvar_jsonl(registros: Iterable[dict], caminho: Path) -> int:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with caminho.open("w", encoding="utf-8") as f:
        for r in registros:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def ler_jsonl(caminho: Path) -> list[dict]:
    with caminho.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def fatiar(contar, minimo: int, maximo: int, limite: int, rotulo: str = "preço"):
    """Divide [minimo, maximo] em faixas com no máximo `limite` anúncios.

    Os dois portais só deixam paginar até certo ponto (QuintoAndar: 1000
    resultados; Chaves na Mão: página 99). Para cobrir a cidade inteira,
    fazemos bissecção na faixa de preço até cada fatia caber no limite.

    `contar(a, b)` devolve quantos anúncios existem com o valor em [a, b].
    Gera tuplas (a, b, total). Uma fatia de valor único acima do limite
    é devolvida mesmo assim; quem chama decide como subdividi-la (por
    exemplo, por área).
    """
    pilha = [(minimo, maximo)]
    while pilha:
        a, b = pilha.pop()
        n = contar(a, b)
        if n == 0:
            continue
        if n <= limite or a == b:
            if n > limite:
                log.info("Faixa de %s %s tem %d anúncios (> %d): será subdividida", rotulo, a, n, limite)
            yield a, b, n
            continue
        meio = (a + b) // 2
        pilha.append((meio + 1, b))
        pilha.append((a, meio))


def fatias_preco_area(contar, pmin: int, pmax: int, limite: int):
    """Fatia por preço e, quando um preço único estoura o limite, por área.

    `contar(pmin, pmax, amin, amax)` aceita amin/amax = None (sem filtro de área).
    Gera tuplas (pmin, pmax, amin, amax, total).
    """
    for a, b, n in fatiar(lambda x, y: contar(x, y, None, None), pmin, pmax, limite, "preço"):
        if n <= limite:
            yield a, b, None, None, n
            continue
        soma = 0
        for amin, amax, m in fatiar(lambda x, y: contar(a, b, x, y), 0, 100_000, limite, "área"):
            if m > limite:
                log.warning("Preço R$ %s e área %s m²: %d anúncios, truncado em %d", a, amin, m, limite)
            soma += m
            yield a, b, amin, amax, m
        if soma < n:
            log.warning("Preço R$ %s: %d anúncios sem área informada ficam fora das buscas por área",
                        a, n - soma)


def juntar_fatias(fatias, limite: int) -> list[tuple]:
    """Junta fatias vizinhas enquanto a soma couber no limite.

    A bissecção pode deixar fatias pequenas lado a lado (por exemplo, na
    cauda de preços altos). Menos fatias significa menos consultas, e no
    Chaves na Mão cada consulta esconde ~14 anúncios. Recebe e devolve
    tuplas (pmin, pmax, amin, amax, total). Só junta fatias contíguas do
    mesmo tipo: faixas de preço sem filtro de área, ou faixas de área
    dentro do mesmo preço.
    """
    juntas: list[tuple] = []
    for f in fatias:
        if juntas:
            a, b, amin, amax, n = juntas[-1]
            fa, fb, famin, famax, fn = f
            contigua_preco = amin is None and famin is None and b + 1 == fa
            contigua_area = (amin is not None and famin is not None
                             and (a, b) == (fa, fb) and amax + 1 == famin)
            if (contigua_preco or contigua_area) and n + fn <= limite:
                juntas[-1] = (a, fb, None, None, n + fn) if contigua_preco else (a, b, amin, famax, n + fn)
                continue
        juntas.append(tuple(f))
    return juntas
