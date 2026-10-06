# Predição de preço de imóveis no Rio de Janeiro

Trabalho de Machine Learning: prever o preço de venda de casas e apartamentos
na cidade do Rio de Janeiro com **dados reais e atuais**, coletados por
scraping de dois portais imobiliários em 5 e 6 de outubro de 2026.

O trabalho segue, capítulo a capítulo, o livro *O Preço da Precisão: A Saga
da Regressão* (Alvaro Riz de Barros, 2025), aplicando cada passo à base do Rio.

## Capítulos entregues

| Capítulos | Notebook | Colab |
|---|---|---|
| 1 — A Separação Sagrada dos Dados<br>2 — O Olhar do Detetive: Análise Exploratória | [notebooks/cap01_cap02.ipynb](notebooks/cap01_cap02.ipynb) | [![Abrir no Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/vianaR25/imoveis-rio-ml/blob/main/notebooks/cap01_cap02.ipynb) |

O notebook roda no Colab sem nenhuma instalação: ele baixa a base
(`data/processed/imoveis_rio.parquet`) direto deste repositório.

## Dados

| Fonte | Como coletamos | Anúncios coletados | Cobertura |
|---|---|---|---|
| [QuintoAndar](https://www.quintoandar.com.br) | API JSON de busca usada pelo próprio site | 48.588 | 100% |
| [Chaves na Mão](https://www.chavesnamao.com.br) | API JSON de listagem usada pelo próprio site | 117.654 distintos | 98,65% |

As duas APIs limitam a paginação. O QuintoAndar vai até 1.000 resultados
por busca e o Chaves na Mão até a página 99. Para cobrir a cidade inteira,
a coleta divide a busca em faixas de preço, e de área quando um preço único
passa do limite (`src/scraping/common.py`). A API do Chaves na Mão esconde
cerca de 14 anúncios por consulta; o scraper reduz essa perda juntando
faixas vizinhas e completando as faixas pequenas com outras ordenações.
O relatório completo do reconhecimento dos sites está em
[docs/reconhecimento.md](docs/reconhecimento.md).

### Limpeza

Feita por `src/data/preparar.py`, antes da divisão treino/teste. Para não
vazar informação do conjunto de teste (capítulo 1 do livro), nenhuma etapa
calcula médias ou percentis do preço. A deduplicação consulta a base inteira
e compara preços entre anúncios, só para achar repetições. A padronização das
grafias de bairro e a decisão entre as duas Freguesias também consultam a
base inteira, sem usar o preço. Os limites de IPTU anual e de condomínio +
IPTU implausível foram calibrados olhando a base completa; eles não usam o
preço e só mexem em variáveis explicativas.

| Etapa | Imóveis |
|---|---|
| Anúncios coletados (QuintoAndar + Chaves na Mão) | 176.144 |
| Casas e apartamentos na cidade do Rio | 175.794 |
| Sem ids repetidos (a busca de apartamentos também traz as coberturas) | 165.892 |
| Sem vendas da Caixa (leilão, venda direta, licitação) | 161.365 |
| Com preço, área e bairro | 160.341 |
| Bairro reconhecido no município do Rio | 160.275 |
| Valores plausíveis (R$ 50 mil a R$ 50 mi; 15 a 5.000 m²; até 10 quartos e vagas e 12 banheiros) | 159.522 |
| Preço por m² entre R$ 1.000 e R$ 50.000 | 158.516 |
| **Sem anúncios repetidos do mesmo imóvel** | **103.301** |

A última etapa remove 34,8% da base: o mesmo imóvel é anunciado por vários
corretores e nos dois portais. Contam como o mesmo imóvel os anúncios com a
mesma área e os mesmos quartos que estejam no mesmo bairro e na mesma rua,
com o mesmo preço ou com preço na mesma faixa de R$ 10 mil; na mesma rua em
qualquer bairro, com o mesmo preço; no mesmo bairro, com o mesmo preço e
ruas de grafia parecida ("Corrêa" e "Correia"); ou no mesmo bairro e no
mesmo portal, com coordenadas, preço e todas as características idênticos.
Quando o mesmo imóvel aparece em vários anúncios, a linha mantida herda o
que falta dos outros, como a coordenada, que o QuintoAndar não informa
(só de anúncios do mesmo bairro).

Para a divisão treino/teste há ainda a coluna `grupo`, um critério mais
largo que junta anúncios que **podem** ser o mesmo imóvel. A divisão mantém
cada grupo inteiro de um lado só.

Outras correções:

- **Coordenadas.** Latitude e longitude trocadas são desfeitas; coordenadas fora do retângulo que envolve o município, ou do lado errado entre as duas Freguesias, são anuladas.
- **IPTU anual convertido para mensal.** No Chaves na Mão o campo vem nos dois formatos. Valores acima de R$ 10 por m² são tratados como anuais.
- **Condomínio + IPTU não informado.** Os valores 0 e −1 do QuintoAndar viram ausentes.
- **Tipo do imóvel.** Coberturas e casas em condomínio são reconhecidas nas duas fontes.
- **Bairros.** Grafias diferentes do mesmo bairro são unificadas, as duas Freguesias são separadas pelo CEP, depois pela rua e por último pela latitude, e cada bairro é mapeado para uma região da cidade (`src/data/zonas.py`).

### Arquivo publicado

`data/processed/imoveis_rio.parquet` (2 MB) tem só o necessário para a
modelagem: as 19 características do imóvel usadas como variáveis, o preço,
o grupo de imóvel e a data da coleta. Não há link, rua, CEP nem dado do
anunciante.

### Por que não ZAP, VivaReal ou OLX?

Esses portais respondem 403 com desafio da Cloudflare já na primeira
requisição. Contornar exigiria navegador disfarçado e proxies residenciais,
o que fere os termos de uso. Ficamos com os portais que respondem a
requisições comuns.

### Ética e privacidade

- **Uso acadêmico.** Os dados foram coletados só para este trabalho.
- **Ritmo.** Cerca de uma requisição por segundo, com cache em disco para não repetir chamadas.
- **robots.txt.** O do Chaves na Mão proíbe URLs com parâmetros. Decidimos coletar mesmo assim, para uso exclusivamente acadêmico e no ritmo acima.
- **Dados pessoais.** O bloco do anunciante (nome, telefones, CRECI) e as descrições em texto livre são descartados no momento da coleta. Os dados brutos locais, que não são publicados, guardam ainda o título e a referência do anúncio do Chaves na Mão, usados para identificar vendas da Caixa e que às vezes trazem um contato digitado pelo anunciante, e o link do anunciante no Google, que a versão atual do coletor já descarta.

## Estrutura

```
notebooks/cap01_cap02.ipynb   capítulos 1 e 2 do livro (divisão e análise exploratória)
data/processed/               imoveis_rio.parquet, a base limpa
src/scraping/common.py        cliente HTTP educado, cache, fatiamento por preço e área
src/scraping/quintoandar.py   coletor do QuintoAndar
src/scraping/chavesnamao.py   coletor do Chaves na Mão
src/scraping/run_all.py       linha de comando da coleta
src/data/zonas.py             bairro -> região da cidade
src/data/preparar.py          unificação, limpeza, deduplicação e grupos de imóvel
docs/reconhecimento.md        relatório de reconhecimento dos sites
```

## Como rodar

O jeito mais simples é abrir o notebook no Colab pelo botão acima. Para
refazer tudo localmente:

```bash
pip install -r requirements.txt

# 1. Coleta (a completa leva algumas horas; --teste coleta só as primeiras faixas de preço)
python -m src.scraping.run_all --fonte quintoandar --teste
python -m src.scraping.run_all

# 2. Base limpa
python -m src.data.preparar

# 3. Notebook
jupyter nbconvert --to notebook --execute --inplace notebooks/cap01_cap02.ipynb
```

Os dados brutos da coleta (cerca de 300 MB) não estão no repositório. Uma
nova coleta traz os anúncios do dia em que for feita, então os números vão
diferir dos apresentados aqui.

## Limitações

- **Preço de anúncio, não de venda.** Imóveis costumam fechar abaixo do anunciado.
- **Retrato de um momento.** Os dados valem para a data da coleta.
- **Cobertura dos portais.** A Zona Oeste tem só 4,4% dos anúncios, apesar de ser uma das regiões mais populosas: os portais cobrem pouco o mercado popular.
- **Dados digitados pelo anunciante.** A limpeza reduz, mas não elimina, erros de área, preço e condomínio.
