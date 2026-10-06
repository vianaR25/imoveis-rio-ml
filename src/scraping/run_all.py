"""CLI de coleta.

Exemplos:
    python -m src.scraping.run_all --fonte quintoandar --teste
    python -m src.scraping.run_all --fonte chavesnamao --tipo casas --teste
    python -m src.scraping.run_all            # coleta completa das duas fontes
"""
from __future__ import annotations

import argparse

from . import chavesnamao, quintoandar


def main() -> None:
    ap = argparse.ArgumentParser(description="Coleta anúncios de imóveis à venda no Rio de Janeiro")
    ap.add_argument("--fonte", choices=["quintoandar", "chavesnamao", "todas"], default="todas")
    ap.add_argument("--tipo", action="append", choices=list(chavesnamao.TIPOS),
                    help="Tipos do Chaves na Mão (repetível). Padrão: todos.")
    ap.add_argument("--teste", action="store_true", help="Coleta só as primeiras faixas de preço")
    ap.add_argument("--data", help="Data usada nos nomes dos arquivos (padrão: hoje). Útil ao retomar "
                                   "pelo cache uma coleta de outro dia. Cada anúncio guarda, em "
                                   "_data_coleta, o dia em que sua página foi baixada.")
    args = ap.parse_args()

    if args.fonte in ("quintoandar", "todas"):
        quintoandar.coletar(teste=args.teste, data_coleta=args.data)
    if args.fonte in ("chavesnamao", "todas"):
        chavesnamao.coletar(tipos=args.tipo, teste=args.teste, data_coleta=args.data)


if __name__ == "__main__":
    main()
