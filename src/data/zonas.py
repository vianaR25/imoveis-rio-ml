"""Bairros da cidade do Rio de Janeiro agrupados em regiões.

Usamos as Áreas de Planejamento (AP) da Prefeitura, com nomes populares:
  AP1 -> Centro, AP2 -> Zona Sul e Grande Tijuca, AP3 -> Zona Norte,
  AP4 -> Barra e Jacarepaguá, AP5 -> Zona Oeste.
A Grande Tijuca (AP2.2) fica separada da Zona Sul porque o preço por m²
é bem diferente.
"""
from __future__ import annotations

import re
import unicodedata

REGIOES: dict[str, list[str]] = {
    "Centro": [
        "Saúde", "Gamboa", "Santo Cristo", "Caju", "Centro", "Lapa", "Catumbi",
        "Rio Comprido", "Cidade Nova", "Estácio", "São Cristóvão", "Mangueira",
        "Benfica", "Vasco da Gama", "Paquetá", "Santa Teresa", "Imperial de São Cristóvão",
    ],
    "Zona Sul": [
        "Flamengo", "Glória", "Laranjeiras", "Catete", "Cosme Velho", "Botafogo",
        "Humaitá", "Urca", "Leme", "Copacabana", "Ipanema", "Leblon", "Lagoa",
        "Jardim Botânico", "Gávea", "Vidigal", "São Conrado", "Rocinha",
    ],
    "Grande Tijuca": [
        "Praça da Bandeira", "Tijuca", "Alto da Boa Vista", "Maracanã",
        "Vila Isabel", "Andaraí", "Grajaú", "Usina", "Muda",
    ],
    "Zona Norte": [
        "Manguinhos", "Bonsucesso", "Ramos", "Olaria", "Penha", "Penha Circular",
        "Brás de Pina", "Cordovil", "Parada de Lucas", "Vigário Geral",
        "Jardim América", "Higienópolis", "Jacaré", "Maria da Graça",
        "Del Castilho", "Inhaúma", "Engenho da Rainha", "Tomás Coelho",
        "São Francisco Xavier", "Rocha", "Riachuelo", "Sampaio", "Engenho Novo",
        "Lins de Vasconcelos", "Méier", "Todos os Santos", "Cachambi",
        "Engenho de Dentro", "Água Santa", "Encantado", "Piedade", "Abolição",
        "Pilares", "Vila Kosmos", "Vicente de Carvalho", "Vila da Penha",
        "Vista Alegre", "Irajá", "Colégio", "Campinho", "Quintino Bocaiúva",
        "Cavalcanti", "Engenheiro Leal", "Cascadura", "Madureira", "Vaz Lobo",
        "Turiaçu", "Rocha Miranda", "Honório Gurgel", "Oswaldo Cruz",
        "Bento Ribeiro", "Marechal Hermes", "Ribeira", "Zumbi", "Cacuia",
        "Pitangueiras", "Praia da Bandeira", "Cocotá", "Bancários",
        "Freguesia (Ilha do Governador)", "Jardim Guanabara", "Jardim Carioca",
        "Tauá", "Moneró", "Portuguesa", "Galeão", "Cidade Universitária",
        "Ilha do Governador", "Guadalupe", "Anchieta", "Parque Anchieta",
        "Ricardo de Albuquerque", "Coelho Neto", "Acari", "Barros Filho",
        "Costa Barros", "Pavuna", "Parque Colúmbia", "Jacarezinho",
        "Complexo do Alemão", "Maré",
    ],
    "Barra e Jacarepaguá": [
        "Jacarepaguá", "Anil", "Gardênia Azul", "Cidade de Deus", "Curicica",
        "Freguesia (Jacarepaguá)", "Freguesia", "Pechincha", "Taquara", "Tanque",
        "Praça Seca", "Vila Valqueire", "Joá", "Itanhangá", "Barra da Tijuca",
        "Camorim", "Vargem Pequena", "Vargem Grande", "Recreio dos Bandeirantes",
        "Recreio", "Grumari", "Barra Olímpica", "Jardim Oceânico",
    ],
    "Zona Oeste": [
        "Deodoro", "Vila Militar", "Campo dos Afonsos", "Jardim Sulacap",
        "Magalhães Bastos", "Realengo", "Padre Miguel", "Bangu", "Senador Camará",
        "Gericinó", "Santíssimo", "Campo Grande", "Senador Vasconcelos",
        "Inhoaíba", "Cosmos", "Paciência", "Santa Cruz", "Sepetiba", "Guaratiba",
        "Barra de Guaratiba", "Pedra de Guaratiba", "Vila Kennedy",
    ],
}


def normalizar(nome: str | None) -> str:
    """minúsculas, sem acento, espaços simples. 'Méier ' -> 'meier'."""
    if not isinstance(nome, str) or not nome:
        return ""
    s = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode()
    s = re.sub(r"\s+", " ", s.lower()).strip(" .,-")
    return s


# Grafias alternativas usadas pelos anunciantes -> nome oficial.
APELIDOS = {
    "braz de pina": "Brás de Pina",
    "osvaldo cruz": "Oswaldo Cruz",
    "l vasconcelos": "Lins de Vasconcelos",
    "lins": "Lins de Vasconcelos",
    "jabour": "Senador Camará",
    "regiao de santa cruz": "Santa Cruz",
    "recreio": "Recreio dos Bandeirantes",
    "barra": "Barra da Tijuca",
    "ilha do governador": "Ilha do Governador",
    "freguesia de jacarepagua": "Freguesia (Jacarepaguá)",
    "freguesia jacarepagua": "Freguesia (Jacarepaguá)",
    "freguesia ilha do governador": "Freguesia (Ilha do Governador)",
    "cidade universitaria": "Cidade Universitária",
    "quintino": "Quintino Bocaiúva",
    "vila da penha": "Vila da Penha",
    "jardim oceanico": "Barra da Tijuca",
    "barra olimpica": "Barra Olímpica",
    "engenho de dentro": "Engenho de Dentro",
    "centro (rio de janeiro)": "Centro",
    "ilha de guaratiba": "Guaratiba",
    "shopping novo leblon": "Barra da Tijuca",
    "rcr bandeirantes": "Recreio dos Bandeirantes",
    "r. bandeirantes": "Recreio dos Bandeirantes",
    "rec dos bandeirante": "Recreio dos Bandeirantes",
    "rec dos bandeirantes": "Recreio dos Bandeirantes",
    "r dos bandeirantes": "Recreio dos Bandeirantes",
    "r. dos bandeirantes": "Recreio dos Bandeirantes",
    "b tijuca": "Barra da Tijuca",
    "b. tijuca": "Barra da Tijuca",
    "jardim santa cruz": "Santa Cruz",
    "tomas coelho rj": "Tomás Coelho",
    "ilha governador": "Ilha do Governador",
    "i. governador": "Ilha do Governador",
    "boulevard olimpico": "Barra Olímpica",
    "arpoador": "Ipanema",
    "rio das pedras": "Itanhangá",
    "recreio bendeirante": "Recreio dos Bandeirantes",
    "sao fco xavier": "São Francisco Xavier",
    "freguesia jacarepagu": "Freguesia (Jacarepaguá)",
    "freguesia (ilha)": "Freguesia (Ilha do Governador)",
    # "Freguesia" sem qualificação: todas as ruas observadas são de Jacarepaguá.
    "freguesia": "Freguesia (Jacarepaguá)",
    "freguesia (jacarepagua)pechincha": "Pechincha",
    "bota fogo": "Botafogo",
    "vila izabel": "Vila Isabel",
    "i. do governador": "Ilha do Governador",
    "freguesia ilha": "Freguesia (Ilha do Governador)",
    "imperial de sao cristovao": "São Cristóvão",
}

_MAPA: dict[str, str] = {}
for _regiao, _bairros in REGIOES.items():
    for _b in _bairros:
        _MAPA.setdefault(normalizar(_b), _regiao)


def regiao_do_bairro(bairro: str | None) -> str | None:
    """Devolve a região do bairro, ou None se o bairro não for reconhecido."""
    n = normalizar(bairro)
    if not n:
        return None
    if n in APELIDOS:
        n = normalizar(APELIDOS[n])
    if n in _MAPA:
        return _MAPA[n]
    sem_parenteses = re.sub(r"\s*\(.*?\)\s*", " ", n).strip()
    return _MAPA.get(sem_parenteses)


def nome_oficial(bairro: str | None) -> str | None:
    """Troca apelidos conhecidos pelo nome oficial; mantém o resto.

    Qualificadores entre parênteses de bairros que não precisam deles
    ('Pechincha (Jacarepaguá)') são retirados; os das Freguesias ficam.
    """
    if not isinstance(bairro, str) or not bairro.strip():
        return None
    n = normalizar(bairro)
    if n in APELIDOS:
        return APELIDOS[n]
    if "(" in n and not n.startswith("freguesia"):
        base = re.sub(r"\s*\(.*?\)\s*", " ", bairro).strip()
        if normalizar(base) in _MAPA:
            return base
    return bairro.strip()


def canonizar_bairros(serie):
    """Aplica nome_oficial e unifica grafias que só diferem em acento ou
    caixa ('Meier', 'MÉIER', 'Méier'), escolhendo a grafia mais frequente."""
    import pandas as pd

    s = serie.map(nome_oficial)
    chave = s.map(normalizar)
    grafia = (pd.DataFrame({"chave": chave, "nome": s}).dropna()
              .groupby("chave")["nome"].agg(lambda x: x.value_counts().index[0]))
    return chave.map(grafia).astype("string")


_PREFIXOS_RUA = {
    "rua", "r", "avenida", "av", "ave", "estrada", "est", "estr", "travessa", "tv", "trav",
    "praca", "pca", "pc", "alameda", "al", "largo", "lgo", "rodovia", "rod", "ladeira", "lad",
    "via", "beco", "servidao", "praia", "caminho", "vila", "parque", "condominio",
}


_ARTIGOS = {"de", "do", "da", "dos", "das"}


def normalizar_rua(rua: str | None) -> str:
    """'Av. das Américas, 500' -> 'americas'. Tira tipo de logradouro, artigo
    inicial, números e pontuação para comparar a mesma rua escrita de jeitos
    diferentes ('Rua do Bananal' e 'Estrada Bananal')."""
    s = normalizar(rua).replace("'", "").replace("`", "")  # "D'água" = "Dágua"
    s = re.sub(r"[^a-z ]+", " ", s)
    palavras = s.split()
    while palavras and palavras[0] in _PREFIXOS_RUA | _ARTIGOS:
        palavras = palavras[1:]
    return " ".join(palavras)
