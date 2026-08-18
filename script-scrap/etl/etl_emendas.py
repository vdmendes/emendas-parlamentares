#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_emendas.py
==============

Transforma os CSVs crus baixados pelo `emendas_transparencia.py` em parquet
particionado, prontos para analise.

    raw/<dataset>/csv*/**.csv   ->   processed/<base>/<base>_AAAA.parquet

O que o ETL resolve (as armadilhas documentadas em
`documents/estrutura_bases_emendas_parlamentares.md`):

* **Codigos como texto.** Codigo da Emenda, favorecido, IBGE, SIAFI, empenho e
  afins tem zero a esquerda significativo. Ler como numero destroi a chave.
* **Decimal virgula, SEM ponto de milhar.** Verificado nos arquivos reais:
  `-161998,38`, `1000000,00`. Passar `thousands="."` aqui seria errado.
  Valores negativos existem (cancelamentos) e sao preservados.
* **Nomes de coluna divergem entre as bases** para o mesmo conceito
  (`Nome Funcao` vs `Funcao`, `Municipio Favorecido` sem acento no apoiamento).
  Tudo vira snake_case sem acento, e um mapa de sinonimos unifica os conceitos.
* **Arquivos gigantes.** O CSV de documentos de 2020 tem 1,1 GB. A leitura e
  feita em blocos e os blocos sao costurados num unico parquet por ano, entao o
  pico de memoria nao acompanha o tamanho do arquivo.
* **Competencias do download unico.** As bases de emenda, favorecido e convenios
  nao tem filtro de ano no portal: cada download e uma fotografia inteira. A
  mais recente fica em `processed/<base>/`, as anteriores em
  `processed/_historico/<base>/<competencia>/`.

O ETL **nao** agrega nem junta bases -- so limpa e tipa. Junções e somas ficam
para a analise, onde as regras de 1:N precisam ser decididas caso a caso.

    python3 etl_emendas.py                    # incremental: so o novo ou alterado
    python3 etl_emendas.py --bases documento  # so a base de documentos
    python3 etl_emendas.py --listar           # mostra o que seria processado
    python3 etl_emendas.py --forcar           # reprocessa tudo
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

try:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:                                          # pragma: no cover
    raise SystemExit("pandas/pyarrow nao instalados:  pip install pandas pyarrow")

# =========================================================================== #
#  CONFIGURACAO
# =========================================================================== #


def _pasta_projeto() -> Path:
    """
    Acha a raiz do projeto (`database_portal/`) subindo a partir deste arquivo.

    Procura pela pasta que contem `raw/` ou `processed/`, em vez de contar
    niveis com `.parent.parent`. Assim o script pode ser movido de lugar --
    para dentro de `script-scrap/`, por exemplo -- sem quebrar.
    """
    do_ambiente = os.environ.get("EMENDAS_PROJETO")
    if do_ambiente:
        return Path(do_ambiente).expanduser()

    aqui = Path(__file__).resolve()
    for pasta in aqui.parents:
        if (pasta / "raw").is_dir() or (pasta / "processed").is_dir():
            return pasta
    # nada encontrado: assume o avo, que e o layout historico do projeto
    return aqui.parent.parent


PASTA_PROJETO = _pasta_projeto()
PASTA_RAW = Path(os.environ.get("EMENDAS_RAIZ", PASTA_PROJETO / "raw")).expanduser()
PASTA_PROCESSED = Path(
    os.environ.get("EMENDAS_PROCESSED", PASTA_PROJETO / "processed")
).expanduser()

# Leitura em blocos: 200 mil linhas por vez segura o pico de memoria em ~1 GB
# mesmo no arquivo de 329 MB.
LINHAS_POR_BLOCO = 200_000

ENCODING = "latin-1"
SEPARADOR = ";"

# =========================================================================== #

log = logging.getLogger("etl")


# --------------------------------------------------------------------------- #
# Normalizacao de nomes
# --------------------------------------------------------------------------- #


def sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(c)
    )


def snake(nome: str) -> str:
    """'Código IBGE do município de aplicação do recurso' -> 'codigo_ibge_...'."""
    s = sem_acento(nome).lower().strip()
    s = s.replace("?", "").replace("/", "_")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


# Sinonimos: o portal usa nomes diferentes para o mesmo conceito entre as bases.
# Chave = nome ja em snake_case; valor = nome canonico do projeto.
SINONIMOS: Dict[str, str] = {
    # a base 1 chama de "Nome Funcao", a base 2 chama so de "Funcao"
    "nome_funcao": "funcao",
    "nome_subfuncao": "subfuncao",
    "nome_programa": "programa",
    "nome_acao": "acao",
    "nome_plano_orcamentario": "plano_orcamentario",
    # identificacao da emenda -- o portal alterna "da"/"de" e as vezes omite
    "codigo_da_emenda": "codigo_emenda",
    "ano_da_emenda": "ano_emenda",
    "tipo_da_emenda": "tipo_emenda",
    "tipo_de_emenda": "tipo_emenda",
    "numero_da_emenda": "numero_emenda",
    "codigo_do_autor_da_emenda": "codigo_autor_emenda",
    "nome_do_autor_da_emenda": "nome_autor_emenda",
    "codigo_do_favorecido": "codigo_favorecido",
    "fase_da_despesa": "fase_despesa",
    # base 5 (apoiamentos) -- confirmado no arquivo de 2022 em 18/08/2026
    "codigo_apoiador": "codigo_apoiador",
    "data_do_apoio": "data_apoio",
    "data_retirada_do_apoio": "data_retirada_apoio",
    "data_ultima_movimentacao_empenho": "data_ultima_movimentacao_empenho",
    # `Empenho` da base 5 tem o MESMO formato do `Codigo Documento` da base 2
    # (23 caracteres, UG 6 + Gestao 5 + ano 4 + tipo 2 + numero 6). Recebe o
    # mesmo nome para deixar a juncao 2 <-> 5 obvia.
    "empenho": "codigo_documento",
    "localidade_de_aplicacao_do_recurso": "localidade_aplicacao",
    "localidade_do_gasto": "localidade_gasto",
    "uf_de_aplicacao_do_recurso": "uf_aplicacao",
    "municipio_de_aplicacao_do_recurso": "municipio_aplicacao",
    "codigo_ibge_do_municipio_de_aplicacao_do_recurso": "codigo_ibge_aplicacao",
    "possui_convenio": "possui_convenio",
    "ano_mes": "ano_mes",
}


def canonizar(colunas: Iterable[str]) -> List[str]:
    return [SINONIMOS.get(snake(c), snake(c)) for c in colunas]


# --------------------------------------------------------------------------- #
# Tipagem
# --------------------------------------------------------------------------- #

# Colunas que DEVEM ser texto (zero a esquerda significativo ou identificador).
# O casamento e por prefixo/sufixo depois da canonizacao, para pegar tambem as
# colunas que ainda nao catalogamos (base 3).
PADROES_TEXTO = (
    r"^codigo",          # codigo_emenda, codigo_favorecido, codigo_ug, ...
    r"^cod_",
    r"_ibge$",
    r"^numero",
    r"^empenho$",
    r"^ano_mes$",
    r"^ano_da_emenda$",
    r"^ano_emenda$",
)

# Colunas monetarias: decimal virgula, sem ponto de milhar, podem ser negativas.
PADROES_VALOR = (r"^valor", r"^vl_")

# Colunas de data no formato DD/MM/AAAA.
PADROES_DATA = (r"^data")


def e_texto(coluna: str) -> bool:
    return any(re.search(p, coluna) for p in PADROES_TEXTO)


def e_valor(coluna: str) -> bool:
    return any(re.search(p, coluna) for p in PADROES_VALOR)


def e_data(coluna: str) -> bool:
    return bool(re.search(PADROES_DATA, coluna))


def para_decimal(serie: pd.Series) -> pd.Series:
    """'-161998,38' -> -161998.38. Vazio e 'Sem informacao' viram NaN."""
    limpo = (serie.astype("string")
                  .str.strip()
                  .str.replace(".", "", regex=False)   # milhar, se algum dia vier
                  .str.replace(",", ".", regex=False))
    return pd.to_numeric(limpo, errors="coerce")


def para_data(serie: pd.Series) -> pd.Series:
    """
    O portal usa DUAS convencoes de data, as vezes no mesmo arquivo:

      * `DD/MM/AAAA`            -- bases 1, 2, 4 e o campo de movimentacao da 5
      * `AAAA-MM-DD HH:MM:SS`   -- `Data do Apoio` e `Data Retirada do Apoio`
                                   na base 5 (timestamp de registro no sistema)

    Tenta a primeira; o que sobrar como nulo tenta a segunda. Assim uma coluna
    homogenea custa uma passada so, e uma coluna mista ainda e convertida certo.
    """
    texto = serie.astype("string").str.strip()
    saida = pd.to_datetime(texto, format="%d/%m/%Y", errors="coerce")

    faltando = saida.isna() & texto.notna() & (texto != "")
    if faltando.any():
        saida = saida.fillna(
            pd.to_datetime(texto.where(faltando), format="ISO8601", errors="coerce")
        )
    return saida


def tipar(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if e_valor(col):
            df[col] = para_decimal(df[col])
        elif e_data(col):
            df[col] = para_data(df[col])
        else:
            # tudo o mais fica como texto: mais seguro que adivinhar, e o
            # parquet comprime string repetida muito bem.
            df[col] = df[col].astype("string").str.strip()
            # "Sem informacao" e o nulo do portal
            df[col] = df[col].replace({"": pd.NA, "Sem informação": pd.NA,
                                       "Sem informacao": pd.NA, "-1": pd.NA})
    return df


# --------------------------------------------------------------------------- #
# Descoberta das bases em raw/
# --------------------------------------------------------------------------- #

# Cada base: como reconhecer o arquivo e de onde tirar o ano da particao.
#   ano_de: "arquivo"  -> prefixo AAAA_ no nome (base 2 e 3)
#           "coluna"   -> valor da coluna indicada (base 1, snapshot sem ano)
#   snapshot: True  -> vem do download unico, sem filtro de ano. O portal so
#             publica o estado atual, entao cada extracao e uma COMPETENCIA
#             diferente da mesma base. Vao para
#             processed/<base>/snapshot=AAAAMMDD/ano=AAAA/, e nunca se
#             sobrescrevem: e assim que a serie temporal se forma.
#   snapshot: False -> arquivo anual. O portal republica o ano inteiro a cada
#             atualizacao, entao a versao mais nova SUBSTITUI a anterior.
#             Vao para processed/<base>/ano=AAAA/.
BASES: Dict[str, dict] = {
    "emenda": {
        "titulo": "Por Emenda Parlamentar",
        "arquivo": "EmendasParlamentares.csv",
        "colunas": 28,
        "snapshot": True,
        "ano_de": "coluna",
        "coluna_ano": "ano_emenda",
    },
    "favorecido": {
        "titulo": "Por Favorecido",
        "arquivo": "EmendasParlamentares_PorFavorecido.csv",
        "colunas": 13,
        "snapshot": True,
        "ano_de": "coluna",
        "coluna_ano": "ano_mes",          # AAAAMM -> corta os 4 primeiros
    },
    "convenios": {
        "titulo": "Convenios",
        "arquivo": "EmendasParlamentares_Convenios.csv",
        "colunas": 12,
        "snapshot": True,
        "ano_de": "chave_emenda",         # 4 primeiros digitos do codigo
    },
    "documento": {
        "titulo": "Por Documentos de Despesa",
        "arquivo": "EmendasParlamentares_PorDocumento.csv",
        "colunas": 48,
        "snapshot": False,
        "ano_de": "arquivo",
    },
    "apoiamento": {
        "titulo": "Apoiamentos",
        "arquivo": "ApoiamentoEmendasParlamentares.csv",
        "colunas": 31,                    # o dicionario diz 27; o arquivo tem 31
        "snapshot": False,
        "ano_de": "arquivo",
        "pasta_raw": "apoiamento-emendas",
    },
}

# Onde as competencias superadas ficam guardadas.
PASTA_HISTORICO = "_historico"

RE_PASTA_SNAPSHOT = re.compile(r"^csv_(\d{8})$")


def snapshot_do_caminho(caminho: Path) -> Optional[str]:
    """
    Extrai a competencia da pasta `csv_AAAAMMDD` que contem o arquivo.

    Retorna None se o CSV nao estiver numa pasta datada -- caso das extracoes
    antigas, feitas quando a pasta se chamava so `csv`.
    """
    for parte in caminho.parts:
        m = RE_PASTA_SNAPSHOT.match(parte)
        if m:
            return m.group(1)
    return None


def sem_prefixo_ano(nome: str) -> str:
    return re.sub(r"^\d{4}_", "", nome)


def ano_do_arquivo(caminho: Path) -> Optional[str]:
    m = re.match(r"^(\d{4})_", caminho.name)
    if m:
        return m.group(1)
    # fallback: pasta csv/<ano>/
    if re.fullmatch(r"\d{4}", caminho.parent.name):
        return caminho.parent.name
    return None


def identificar_base(caminho: Path) -> Optional[str]:
    """Descobre a qual das 5 bases um CSV pertence."""
    nome = sem_prefixo_ano(caminho.name)
    for base, cfg in BASES.items():
        if cfg["arquivo"] and nome == cfg["arquivo"]:
            return base
    # apoiamento: identificavel tambem pela pasta, caso o portal mude o nome
    if "apoiamento-emendas" in caminho.parts:
        return "apoiamento"
    return None


def descobrir(raw: Path) -> List[dict]:
    """
    Varre raw/ e devolve a lista de CSVs a processar, com base e ano.

    Duas defesas contra processar o mesmo dado duas vezes:

    1. **Ignora CSV dentro de `zip/`.** Essa pasta e para os ZIPs originais.
       CSV ali dentro e resto de extracao manual (descompactar o ZIP com o
       gerenciador de arquivos cria uma pasta irma com o mesmo conteudo).
    2. **Deduplica por (base, ano, nome do arquivo).** Se a mesma tabela
       aparecer em dois caminhos, processa a primeira e avisa sobre as demais.
       Sem isso, duas copias com nomes diferentes gerariam partes parquet
       distintas e a leitura contaria as linhas em dobro -- silenciosamente.
    """
    achados: List[dict] = []
    vistos: Dict[tuple, Path] = {}

    for csv_path in sorted(raw.rglob("*.csv")):
        if csv_path.name.startswith("._"):        # lixo do macOS
            continue
        if "zip" in csv_path.relative_to(raw).parts[:-1]:
            log.debug("ignorado (CSV dentro de zip/): %s", csv_path.relative_to(raw))
            continue

        base = identificar_base(csv_path)
        if base is None:
            log.warning("nao reconheci a base de %s -- ignorado",
                        csv_path.relative_to(raw))
            continue

        ano = ano_do_arquivo(csv_path)
        snap = snapshot_do_caminho(csv_path)
        if BASES[base]["snapshot"] and snap is None:
            log.warning("%s esta fora de uma pasta csv_AAAAMMDD -- sem "
                        "competencia identificavel, sera tratado como 'legado'",
                        csv_path.relative_to(raw))
            snap = "legado"

        chave = (base, snap, ano, sem_prefixo_ano(csv_path.name))
        if chave in vistos:
            log.warning("copia duplicada ignorada: %s (ja processando %s)",
                        csv_path.relative_to(raw), vistos[chave].relative_to(raw))
            continue
        vistos[chave] = csv_path

        st = csv_path.stat()
        achados.append({
            "caminho": csv_path,
            "base": base,
            "ano_arquivo": ano,
            "snapshot": snap,
            # rotulo = como o item se identifica no manifesto: a competencia,
            # para bases de snapshot; o exercicio, para as anuais.
            "rotulo": snap if BASES[base]["snapshot"] else (ano or "sem-ano"),
            "bytes": st.st_size,
            "mtime": int(st.st_mtime),
        })

    definir_destinos(achados)
    return achados


def definir_destinos(itens: List[dict]) -> None:
    """
    Decide, para cada item, em que pasta de `processed/` ele vai cair.

    Bases anuais vao sempre para `<base>/`.

    Bases de snapshot: a competencia **mais recente** encontrada em `raw/` fica
    em `<base>/`, e as anteriores vao para `_historico/<base>/<competencia>/`.
    A regra e derivada dos dados, nao de estado guardado -- entao quando chega
    o download de setembro, agosto migra sozinho para o historico na proxima
    execucao, sem precisar mover arquivo nenhum na mao.
    """
    mais_recente: Dict[str, str] = {}
    for item in itens:
        base = item["base"]
        if not BASES[base]["snapshot"]:
            continue
        atual = mais_recente.get(base)
        if atual is None or str(item["snapshot"]) > str(atual):
            mais_recente[base] = item["snapshot"]

    for item in itens:
        base = item["base"]
        if not BASES[base]["snapshot"]:
            item["destino_rel"] = base
            item["corrente"] = True
        elif item["snapshot"] == mais_recente[base]:
            item["destino_rel"] = base
            item["corrente"] = True
        else:
            item["destino_rel"] = f"{PASTA_HISTORICO}/{base}/{item['snapshot']}"
            item["corrente"] = False


# --------------------------------------------------------------------------- #
# Incremental: o que ja foi processado e continua igual
# --------------------------------------------------------------------------- #


def identidade(item: dict) -> str:
    """Chave estavel do item no manifesto do ETL."""
    return f"{item['base']}/{item['rotulo']}/{sem_prefixo_ano(item['caminho'].name)}"


def ja_processado(item: dict, manifesto: dict, destino: Path) -> bool:
    """
    True se este CSV ja virou parquet e nao mudou desde entao.

    A impressao digital e (tamanho, mtime) -- barata e suficiente: o portal
    republica o arquivo inteiro a cada atualizacao, entao qualquer mudanca de
    conteudo mexe no tamanho ou na data. Nao usa sha256 porque isso custaria
    varios segundos por arquivo de 1 GB a cada execucao, justamente o que o
    modo incremental quer evitar.

    Tambem confere se as partes parquet continuam no disco, e se a pasta de
    destino continua sendo a mesma: quando chega uma competencia nova, a
    anterior passa a apontar para `_historico/` e precisa ser regravada la.
    """
    registro = manifesto.get(identidade(item))
    if not registro:
        return False
    if registro.get("bytes") != item["bytes"] or registro.get("mtime") != item["mtime"]:
        return False
    if registro.get("destino_rel") != item["destino_rel"]:
        return False

    arquivos = registro.get("arquivos_parquet") or []
    if not arquivos:
        return False
    return all((destino / a).exists() for a in arquivos)


# --------------------------------------------------------------------------- #
# Processamento
# --------------------------------------------------------------------------- #


# Ano desconhecido. Precisa ser numerico de 4 digitos, nao a palavra
# "desconhecido": o pyarrow infere o tipo do valor da particao, e um unico
# valor de texto faria a coluna `ano` virar string NAQUELA base e inteiro nas
# outras -- o mesmo filtro deixaria de funcionar de uma base para a outra.
# Com 0000, `ano` e inteiro em todas, e `ano == 0` isola as linhas sem ano.
ANO_DESCONHECIDO = "0000"


def ano_particao(df: pd.DataFrame, base: str, ano_arquivo: Optional[str]) -> pd.Series:
    """Devolve a coluna 'ano' usada para particionar o parquet."""
    cfg = BASES[base]
    modo = cfg["ano_de"]

    if modo == "arquivo":
        return pd.Series([ano_arquivo or ANO_DESCONHECIDO] * len(df),
                         index=df.index, dtype="string")

    if modo == "coluna":
        col = cfg["coluna_ano"]
        if col in df.columns:
            # ano_mes vem como AAAAMM; ano_da_emenda ja e AAAA
            return df[col].astype("string").str.slice(0, 4).fillna(ANO_DESCONHECIDO)
        log.warning("  coluna de ano %r ausente; caindo para o codigo da emenda", col)

    # chave_emenda (ou fallback): os 4 primeiros digitos do codigo da emenda
    if "codigo_emenda" in df.columns:
        return (df["codigo_emenda"].astype("string")
                .str.slice(0, 4).fillna(ANO_DESCONHECIDO))
    return pd.Series([ANO_DESCONHECIDO] * len(df), index=df.index, dtype="string")


def processar_arquivo(item: dict, destino: Path, forcar: bool) -> dict:
    """
    Le um CSV em blocos e grava UM parquet por ano.

        processed/emenda/emenda_2014.parquet … emenda_2026.parquet
        processed/documento/documento_2014.parquet … documento_2026.parquet

    A leitura continua em blocos de 200 mil linhas -- o CSV de 2020 tem 1,1 GB
    e nao cabe confortavelmente na memoria. Para que os blocos virem um arquivo
    so, cada ano ganha um `ParquetWriter` aberto, e os blocos vao sendo
    costurados dentro dele. E por isso que existe um esquema canonico: todos os
    blocos precisam ter exatamente os mesmos tipos para entrarem no mesmo
    arquivo.
    """
    caminho: Path = item["caminho"]
    base: str = item["base"]
    pasta = destino / item["destino_rel"]
    pasta.mkdir(parents=True, exist_ok=True)

    onde = "" if item["corrente"] else "  -> historico"
    comp = f"  competencia {item['snapshot']}" if BASES[base]["snapshot"] else ""
    log.info("[%s] %s%s%s (%.1f MB)", base, caminho.name, comp, onde,
             item["bytes"] / 1024 / 1024)

    leitor = pd.read_csv(
        caminho,
        sep=SEPARADOR,
        encoding=ENCODING,
        dtype=str,                 # tudo texto na leitura; a tipagem vem depois
        keep_default_na=False,
        na_values=[],
        chunksize=LINHAS_POR_BLOCO,
        low_memory=False,
    )

    total = 0
    sem_chave = 0          # linhas sem Codigo da Emenda -- nao dao join
    anos: Dict[str, int] = {}
    colunas_finais: List[str] = []
    escritores: Dict[str, "pq.ParquetWriter"] = {}
    esquema: Optional["pa.Schema"] = None

    try:
        for i, bloco in enumerate(leitor):
            bloco.columns = canonizar(bloco.columns)
            bloco = tipar(bloco)
            if "codigo_emenda" in bloco.columns:
                sem_chave += int(bloco["codigo_emenda"].isna().sum())

            # `ano` deixa de ser pasta e vira coluna de verdade: sem particao
            # Hive, essa e a unica forma de o recorte sobreviver a leitura.
            bloco["ano"] = ano_particao(bloco, base, item["ano_arquivo"]).astype("Int64")
            if BASES[base]["snapshot"]:
                # marca a competencia dentro do proprio arquivo, para que ele
                # continue se explicando depois de sair da pasta
                bloco["competencia"] = pd.Series(
                    [item["snapshot"]] * len(bloco), index=bloco.index, dtype="string")

            if not colunas_finais:
                colunas_finais = list(bloco.columns)

            for ano, parte in bloco.groupby("ano", observed=True):
                tabela = pa.Table.from_pandas(parte, preserve_index=False)
                if esquema is None:
                    esquema = tabela.schema
                elif tabela.schema != esquema:
                    tabela = tabela.cast(esquema)

                chave = f"{int(ano):04d}"
                if chave not in escritores:
                    alvo = pasta / f"{base}_{chave}.parquet"
                    escritores[chave] = pq.ParquetWriter(alvo, esquema, compression="zstd")
                escritores[chave].write_table(tabela)
                anos[chave] = anos.get(chave, 0) + len(parte)

            total += len(bloco)
            log.debug("  bloco %d: %d linhas (acumulado %d)", i, len(bloco), total)
    finally:
        for w in escritores.values():
            w.close()

    gerados = [f"{item['destino_rel']}/{base}_{a}.parquet" for a in sorted(anos)]

    # Anos que existiam de uma execucao anterior e agora nao vieram: so pode
    # limpar quando ESTE item responde pela pasta inteira (bases de snapshot).
    # Nas bases anuais cada arquivo responde por um ano so, e apagar o resto
    # destruiria os outros exercicios.
    if BASES[base]["snapshot"]:
        for velho in sorted(pasta.glob(f"{base}_*.parquet")):
            if f"{item['destino_rel']}/{velho.name}" in gerados:
                continue
            try:
                velho.unlink()
                log.debug("  ano que sumiu da fonte, arquivo removido: %s", velho.name)
            except OSError:
                log.warning("  sobrou %s de uma execucao anterior e nao consegui "
                            "remover -- APAGUE A MAO, senao duplica na leitura", velho)

    log.info("[%s] %d linhas -> %d arquivo(s): %s", base, total, len(anos),
             ", ".join(sorted(anos)))
    if sem_chave:
        log.warning("[%s] %d linhas (%.1f%%) sem Codigo da Emenda "
                    "-- nao participam de nenhum join",
                    base, sem_chave, 100 * sem_chave / max(total, 1))

    return {
        "origem": caminho.name,
        "base": base,
        "snapshot": item["snapshot"],
        "rotulo": item["rotulo"],
        "destino_rel": item["destino_rel"],
        "corrente": item["corrente"],
        "bytes": item["bytes"],
        "mtime": item["mtime"],
        "linhas": total,
        "linhas_sem_codigo_emenda": sem_chave,
        "colunas": colunas_finais,
        "linhas_por_ano": dict(sorted(anos.items())),
        "arquivos_parquet": gerados,
        "processado_em": dt.datetime.now().isoformat(timespec="seconds"),
    }


def configurar_log(verboso: bool) -> None:
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.DEBUG if verboso else logging.INFO)
    h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                                     "%H:%M:%S"))
    log.addHandler(h)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="etl_emendas",
        description="Converte os CSVs crus de emendas parlamentares em parquet particionado.",
    )
    p.add_argument("--raw", type=Path, default=PASTA_RAW,
                   help=f"pasta dos CSVs crus (padrao: {PASTA_RAW})")
    p.add_argument("--destino", type=Path, default=PASTA_PROCESSED,
                   help=f"pasta de saida (padrao: {PASTA_PROCESSED})")
    p.add_argument("--bases", default="",
                   help="processa so estas bases, separadas por virgula "
                        f"(opcoes: {', '.join(BASES)})")
    p.add_argument("--listar", action="store_true",
                   help="mostra o que seria processado e sai")
    p.add_argument("--forcar", action="store_true",
                   help="reprocessa tudo, mesmo o que ja esta no manifesto e "
                        "nao mudou (o padrao e incremental)")
    p.add_argument("--tudo", dest="forcar", action="store_true",
                   help=argparse.SUPPRESS)     # apelido de --forcar
    p.add_argument("--limpar", action="store_true",
                   help="apaga a pasta de destino antes de comecar")
    p.add_argument("-v", "--verboso", action="store_true")
    args = p.parse_args(argv)

    configurar_log(args.verboso)

    raw: Path = args.raw.expanduser().resolve()
    destino: Path = args.destino.expanduser().resolve()
    if not raw.exists():
        log.error("pasta raw nao encontrada: %s", raw)
        return 1

    itens = descobrir(raw)
    if args.bases:
        pedidas = {b.strip() for b in args.bases.split(",") if b.strip()}
        desconhecidas = pedidas - set(BASES)
        if desconhecidas:
            p.error(f"base desconhecida: {sorted(desconhecidas)}. Opcoes: {list(BASES)}")
        itens = [i for i in itens if i["base"] in pedidas]

    manifesto = destino / "_etl.json"
    anterior: dict = {}
    if manifesto.exists():
        try:
            anterior = json.loads(manifesto.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("manifesto ilegivel -- vou reprocessar tudo")

    # Competencias antigas primeiro: assim, quando chega um download novo, a
    # anterior e gravada no historico antes de a nova ocupar a pasta corrente.
    itens.sort(key=lambda i: (i["base"], str(i["snapshot"] or ""), i["rotulo"]))

    # Incremental: separa o que ja virou parquet e nao mudou desde entao.
    pendentes, prontos = [], []
    for item in itens:
        (prontos if (not args.forcar and ja_processado(item, anterior, destino))
         else pendentes).append(item)

    log.info("Raw     : %s", raw)
    log.info("Destino : %s", destino)
    log.info("Encontrados %d arquivo(s): %d ja processado(s), %d a processar",
             len(itens), len(prontos), len(pendentes))
    for i in prontos:
        log.debug("  = %-12s %-46s inalterado", i["base"], i["caminho"].name)
    for i in pendentes:
        rot = i["snapshot"] if BASES[i["base"]]["snapshot"] else (i["ano_arquivo"] or "-")
        motivo = "novo" if identidade(i) not in anterior else "mudou"
        if not i["corrente"]:
            motivo = "vai para o historico"
        log.info("  + %-12s %-46s %7.1f MB  %s  (%s)", i["base"], i["caminho"].name,
                 i["bytes"] / 1024 / 1024, rot, motivo)
    if args.listar:
        return 0
    if not itens:
        log.warning("nada a fazer -- rode o emendas_transparencia.py primeiro")
        return 0
    if not pendentes:
        log.info("Tudo em dia. Use --forcar para reprocessar mesmo assim.")
        return 0

    if args.limpar and destino.exists():
        # esvazia o conteudo em vez de remover a pasta: em pastas montadas
        # (Dropbox, iCloud, volumes de rede) o rmdir da raiz costuma dar
        # PermissionError mesmo com o conteudo apagavel.
        log.info("limpando %s", destino)
        for filho in destino.iterdir():
            if filho.is_dir() and not filho.is_symlink():
                shutil.rmtree(filho, ignore_errors=True)
            else:
                filho.unlink(missing_ok=True)
    destino.mkdir(parents=True, exist_ok=True)

    if args.limpar:
        anterior = {}          # a saida foi embora; o manifesto tambem tem de ir

    relatorio: List[dict] = []
    erros = 0
    for item in pendentes:
        try:
            r = processar_arquivo(item, destino, args.forcar)
            relatorio.append(r)
            # grava o manifesto a cada arquivo: se a execucao morrer no meio,
            # a proxima retoma de onde parou em vez de recomecar.
            anterior[identidade(item)] = r
            manifesto.write_text(json.dumps(anterior, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception as exc:
            erros += 1
            log.error("[%s] falhou em %s: %s: %s", item["base"],
                      item["caminho"].name, type(exc).__name__, exc)

    total = sum(r["linhas"] for r in relatorio)
    log.info("=" * 62)
    log.info("Concluido: %d arquivo(s), %s linhas, %d erro(s)",
             len(relatorio), f"{total:,}".replace(",", "."), erros)
    log.info("Manifesto: %s", manifesto)
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
