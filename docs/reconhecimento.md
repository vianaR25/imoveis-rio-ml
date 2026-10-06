# Relatório de reconhecimento de scraping

Gerado em 2026-10-05 e revisado em 2026-10-06, seguindo um roteiro de
reconhecimento em fases (0 a 5) e um relatório em 7 seções.

As fases com navegador foram feitas com Playwright (Chromium headless),
capturando as requisições de rede da própria página.

## Triagem inicial (Fase 0, só curl)

Busca de casas à venda no Rio de Janeiro em oito portais:

| Portal | Status | Proteção | Decisão |
|---|---|---|---|
| ZAP Imóveis | 403 | Cloudflare com desafio | descartado |
| VivaReal | 403 | Cloudflare com desafio | descartado |
| OLX | 403 | Cloudflare com desafio | descartado |
| Imovelweb | 403 | Cloudflare "Just a moment" | descartado |
| Wimoveis | 403 | Cloudflare "Just a moment" | descartado |
| Lopes | 200 | sem desafio, mas pouca oferta | descartado |
| **QuintoAndar** | 200 | nenhuma no HTML e na API | **usado** |
| **Chaves na Mão** | 200 | Cloudflare só a partir da página 100 | **usado** |

Contornar o desafio da Cloudflare exigiria navegador com disfarce de
impressão digital e proxies residenciais. Isso fere os termos de uso dos
portais e foi descartado.

---

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INTELLIGENCE REPORT: quintoandar.com.br
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 1. Arquitetura

| Campo | Valor |
|---|---|
| Framework | Next.js (pages router) |
| Renderização | Híbrida: SSR da primeira página, listagem seguinte via XHR |
| Detecção | cabeçalho `x-powered-by: Next.js` e `<script id="__NEXT_DATA__">` |

### 2. Dados desejados

| Dado | Onde | Fase | Método | Validado? |
|---|---|---|---|---|
| Preço de venda | API | 1 | `hits.hits[]._source.salePrice` | SIM |
| Área | API | 1 | `_source.area` | SIM |
| Quartos, banheiros, suítes, vagas | API | 1 | `bedrooms`, `bathrooms`, `suites`, `parkingSpaces` | SIM |
| Condomínio + IPTU | API | 1 | `iptuPlusCondominium` (valor somado) | SIM |
| Bairro | API | 1 | `neighbourhood` | SIM |
| Tipo | API | 1 | `type`: Apartamento, Casa, CasaCondominio, StudioOuKitchenette | SIM |
| Comodidades | API | 1 | `amenities`, `installations` | SIM |
| Coordenadas | não encontrado | — | — | NÃO |

### 3. Endpoints

| Campo | Valor |
|---|---|
| Endpoint | `POST https://apigw.prod.quintoandar.com.br/house-listing-search/v3/search/list` |
| Autenticação | nenhuma |
| Corpo | JSON com `filters`, `pagination`, `sorting`, `fields`, `slug` (ver `CORPO_BASE` em `src/scraping/quintoandar.py`) |
| Resposta | JSON no formato Elasticsearch: `hits.total.value` e `hits.hits[]._source` |
| Paginação | `pagination.offset` e `pageSize`, com `offset + pageSize <= 1000` |
| Filtros usados | `priceRange: [{costType: "SALE_PRICE", range: {min, max}}]` e `houseSpecs.area.range` |
| Ordenação | `MOST_RECENT` é determinística. `RELEVANCE` repete ids entre páginas |
| Limite de taxa | nenhum 429 observado a 1 requisição por segundo |
| Validado? | SIM, replay com `requests` |

### 4. Proteção

| Mecanismo | Status | Impacto |
|---|---|---|
| Cloudflare | não detectado | nenhum |
| Limite de paginação | 1000 resultados por busca | resolvido fatiando por preço e área |
| CAPTCHA | não acionado | nenhum |
| Autenticação | não exigida | nenhum |

### 5. Estratégias

| Rank | Estratégia | Cobertura | Validado? |
|---|---|---|---|
| 1 | API v3 com fatias de preço e área | todos os anúncios de venda | SIM |
| 2 | `__NEXT_DATA__` da página | só os 26 primeiros por busca | SIM |

### 6. Implementação

- [x] Montar o corpo do `POST v3/search/list` a partir da requisição capturada, sem campos de sessão (`src/scraping/quintoandar.py`).
- [x] Ordenar por `MOST_RECENT` para a paginação ser determinística.
- [x] Fatiar por preço e, quando um preço único passa de 1.000 anúncios, por área (`fatias_preco_area`).
- [x] Paginar cada fatia com `pageSize` 100 até `offset + pageSize <= 1000`.
- [x] Guardar respostas em cache e manter ~1 requisição por segundo.
- [x] Descartar a descrição em texto livre ao salvar.

### 7. Autocrítica

- **Cobertura.** A soma dos totais das 84 faixas é 48.588, exatamente o número coletado.
- **Lacunas.** Não há latitude e longitude. A localização fica no nível de bairro.
- **Classificados.** Metade dos anúncios são classificados de imobiliárias parceiras (`origin: CLASSIFIED`). A página deles fica em `/classificado/{id}/comprar`, e não em `/imovel/{id}/comprar`. Conferimos 20 no navegador: todos ativos e com o mesmo preço.
- **Condomínio + IPTU.** Os valores 0 e −1 significam "não informado".
- **Etapas puladas.** Fase 4 completa, por não haver proteção.
- **Suposições.** A busca pelo slug do Rio traz alguns anúncios de cidades vizinhas. Eles são filtrados pelo campo `city`.
- **Risco de obsolescência.** O corpo da requisição depende do front-end atual. Se mudar, recapturar com Playwright.

---

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INTELLIGENCE REPORT: chavesnamao.com.br
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 1. Arquitetura

| Campo | Valor |
|---|---|
| Framework | Next.js (app router, streaming com `self.__next_f`) |
| Renderização | SSR, com cards e JSON-LD no HTML |
| Detecção | `x-powered-by: Next.js` e chunks `static/chunks/app/...` |

### 2. Dados desejados

| Dado | Onde | Fase | Método | Validado? |
|---|---|---|---|---|
| Preço | API e JSON-LD | 0 e 1 | `items[].prices.rawPrice` | SIM |
| Área útil e total | API | 1 | `items[].area.useful`, `area.total` | SIM |
| Quartos, banheiros, suítes, vagas | API | 1 | `bedrooms.count`, `bathrooms.count`, `suites.count`, `garages.count` | SIM |
| Condomínio e IPTU | API | 1 | `prices.condominiumFee`, `prices.iptuValue` | SIM |
| Bairro e coordenadas | API | 1 | `location.neighborhood.name`, `location.geoposition` | SIM |
| Comodidades | API | 1 | `privativeItems[].name`, `commonItems[].name` | SIM |

### 3. Endpoints

| Campo | Valor |
|---|---|
| Endpoint | `GET https://www.chavesnamao.com.br/api/realestate/listing/items/` |
| Parâmetros | `level1={tipo}-a-venda`, `level2=rj-rio-de-janeiro`, `pg=N`, `filtro=pmin:X,pmax:Y[,amin:A,amax:B]`, `server=0`, `viewport=desktop` |
| Autenticação | nenhuma |
| Resposta | JSON com `metadata.realResults` e `items[]` |
| Paginação | 15 itens por página, fixo. Página 100 em diante devolve 403 da Cloudflare |
| Itens escondidos | Cada consulta mostra no máximo `realResults − 14` anúncios (mínimo 1). As últimas páginas trazem "imóveis similares" no lugar dos 14 restantes, com ou sem o parâmetro `quebra` |
| Ordenações | `filtro=...,or:N`, com N de 0 a 4. As ordenações 1 a 4 escondem outros 14 e repetem alguns ids entre páginas |
| Tipos usados | `casas`, `casas-em-condominio`, `apartamentos`, `coberturas` |
| Validado? | SIM, replay com `requests` |

O formato `filtro=pmin:X,pmax:Y` foi descoberto movendo o slider de preço
com Playwright. Os parâmetros soltos `pmin` e `pmax` são ignorados.

### 4. Proteção

| Mecanismo | Status | Impacto |
|---|---|---|
| Cloudflare | ativo, regra de WAF para `pg >= 100` | 1485 resultados por busca, resolvido com fatias de preço e área |
| robots.txt | `Disallow: /*?*` e `/api/*?pg=` | decisão do autor: coletar mesmo assim, com ritmo de 1 req/s, cache e uso só acadêmico |
| CAPTCHA | não acionado | nenhum |

### 5. Estratégias

| Rank | Estratégia | Cobertura | Validado? |
|---|---|---|---|
| 1 | API de itens com fatias de preço e área, fatias vizinhas juntadas, e fatias de até 700 anúncios completadas com as ordenações 3 e 4 | 98,65% dos anúncios (ver autocrítica) | SIM |
| 2 | JSON-LD `RealEstateListing` do HTML | mesmos 15 por página, menos campos | SIM |
| 3 | Título do card por regex | texto livre e inconsistente | PARCIAL |

### 6. Implementação

- [x] Usar a API de itens com `filtro=pmin:X,pmax:Y[,amin:A,amax:B]` (`src/scraping/chavesnamao.py`).
- [x] Fatiar por preço e área até cada fatia caber em 99 páginas de 15 anúncios.
- [x] Juntar fatias vizinhas para fazer menos consultas (`juntar_fatias`).
- [x] Ler a contagem de cada fatia na primeira página da própria consulta.
- [x] Completar fatias de até 700 anúncios com as ordenações `or:3` e `or:4`.
- [x] Ignorar os "imóveis similares" depois do marcador `recommendedCount`.
- [x] Registrar por fatia os anúncios coletados, os anunciados e os ids repetidos.
- [x] Descartar dados do anunciante e textos livres; guardar em `_data_coleta` o dia em que cada página foi baixada.
- [x] Tratar 403 no meio da coleta como bloqueio temporário, com nova tentativa.

### 7. Autocrítica

- **Lacunas de cobertura.** A API esconde cerca de 14 anúncios por consulta. Testamos numa faixa de 618 anúncios: a ordem padrão mostra 604, e a união de três ordenações mostra 617. Os escondidos ficam espalhados pelas páginas das outras ordenações, então recuperar todos exigiria uma segunda passada completa, dobrando o tráfego no site. Escolhemos reduzir a perda sem isso: juntamos fatias vizinhas, para fazer menos consultas, e completamos as fatias pequenas com mais duas ordenações. A perda restante é de 1,35%: as quatro buscas trazem 127.556 resultados de 129.299 anunciados. Como a busca de apartamentos também traz as coberturas, isso corresponde a 117.654 anúncios distintos. Por bloco de preço, a perda fica entre 0,7% e 1,6%, sem tendência clara, e o log registra cada fatia. Ao dividir um preço por área, 9 anúncios sem área (todos a R$ 200 mil) ficam fora das buscas, e o scraper avisa isso no log.
- **Ordem instável.** Durante a coleta dos apartamentos, a ordem padrão mudou no meio da paginação de algumas fatias. Na coleta final, 236 ids vieram repetidos, e cerca de 250 anúncios foram pulados (0,3%). O scraper conta essas repetições por fatia e no total.
- **Vendas da Caixa.** O site publica imóveis retomados pela Caixa (referência `IMCX…`, título com "Venda Direta Online", "Leilão" ou "Licitação"). O preço deles é lance mínimo, então a preparação os exclui.
- **Datas.** A coleta do Chaves na Mão foi interrompida durante a noite e retomada pelo cache. Cada anúncio guarda em `_data_coleta` o dia em que sua página foi baixada, 5 ou 6 de outubro.
- **Campos omitidos.** O site omite suítes e vagas quando são zero. Tratamos ausência como zero.
- **IPTU.** O campo `iptuValue` vem às vezes mensal e às vezes anual. A preparação trata como anual o valor acima de R$ 10 por m² e divide por 12. A regra não usa o preço, que é o alvo do modelo, e acerta 87% dos 16.438 imóveis anunciados nos dois portais.
- **Freguesia.** O site às vezes rotula a Freguesia de Jacarepaguá como a da Ilha do Governador, com uma coordenada padrão na Ilha. A preparação decide pelo CEP, depois pela rua e por último pela coordenada.
- **Suposições.** Coordenadas são informadas pelo anunciante e às vezes estão erradas. Pontos fora do retângulo que envolve o município são anulados na preparação, e latitude e longitude trocadas são desfeitas.
- **Dados pessoais.** A API devolve nome, telefone e CRECI do corretor. Esses campos e a descrição livre são descartados na coleta.
- **Risco de obsolescência.** O limite de página 99 e o formato do `filtro` podem mudar.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
END OF REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
