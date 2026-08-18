#!/usr/bin/env python3
"""
emendas_transparencia.py
========================

Baixa e extrai os dados abertos de Emendas Parlamentares do Portal da
Transparencia (CGU).

O portal esta atras de AWS WAF Bot Control: requests/httpx/curl levam 403.
O download so funciona por navegador real, entao este script usa Playwright
(Chromium) e reaproveita o mesmo contexto -- o cookie aws-waf-token obtido ao
abrir a pagina HTML vale para os downloads seguintes.

    pip install playwright
    playwright install chromium

    python3 emendas_transparencia.py                # usa a config abaixo
    python3 emendas_transparencia.py --simular      # so mostra o plano
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# =========================================================================== #
#  CONFIGURACAO
# =========================================================================== #

# Pasta raiz onde tudo sera salvo.
#
# Resolucao, em ordem de precedencia:
#   1. --destino na linha de comando
#   2. variavel de ambiente EMENDAS_RAIZ
#   3. a pasta `raw/` do projeto, procurada subindo a partir deste arquivo
#
# A opcao 3 faz o projeto funcionar em qualquer maquina sem editar o arquivo.
# A busca e por pasta que contenha `raw/` ou `processed/`, em vez de contar
# niveis com `.parent.parent`: assim o script pode mudar de lugar na arvore
# sem quebrar.
def _raiz_padrao() -> Path:
    do_ambiente = os.environ.get("EMENDAS_RAIZ")
    if do_ambiente:
        return Path(do_ambiente).expanduser()

    aqui = Path(__file__).resolve()
    for pasta in aqui.parents:
        if (pasta / "raw").is_dir() or (pasta / "processed").is_dir():
            return pasta / "raw"
    return aqui.parent.parent / "raw"


PASTA_RAIZ = _raiz_padrao()

# Periodo padrao. Aceita "2015:2026", "2015-2026" ou "2015,2016,2020".
#
# O fim acompanha o relogio: nao ha ano fixo para atualizar na virada. Em 2027
# o teto vira 2027 sozinho, que e exatamente quando o exercicio de 2027 passa a
# existir -- despesa de 2027 nao e empenhada antes de 2027.
#
# Se algum dia o portal publicar um exercicio adiantado, o log avisa
# ("Portal oferece alem do periodo pedido") e basta passar --anos.
ANO_CORRENTE = dt.date.today().year
PERIODO = f"2015:{ANO_CORRENTE}"

# Datasets processados, na ordem.
DATASETS_PADRAO = ["emendas-parlamentares", "documentos-despesa", "apoiamento-emendas"]

# =========================================================================== #

BASE = "https://portaldatransparencia.gov.br/download-de-dados"

# Estrutura confirmada por inspecao do DOM e do download-planilhas.js (13/08/2026):
#   modo UNICO -> {BASE}/{modulo}/UNICO
#   modo ANO   -> {BASE}/{modulo}/{ano}
DATASETS: Dict[str, dict] = {
    "emendas-parlamentares": {
        "titulo": "Emendas Parlamentares",
        "modulo": "emendas-parlamentares",
        "modo": "UNICO",
        "anos_portal": None,          # snapshot completo, sem filtro de ano
        "dicionario": "/dicionario-de-dados/emendas-parlamentares",
        # 3 tabelas no mesmo ZIP (emenda / convenios / favorecido)
        "colunas_esperadas": {28, 12, 13},
        "atualizacao": "diaria",
    },
    "documentos-despesa": {
        "titulo": "Emendas parlamentares por Documentos de Despesa",
        "modulo": "emendas-parlamentares-documentos",
        "modo": "ANO",
        # Fallback: so vale se a leitura do <select> da pagina falhar.
        # O teto acompanha o relogio para nao envelhecer sozinho.
        "anos_portal": (2014, ANO_CORRENTE),
        "dicionario": "/dicionario-de-dados/emendas-parlamentares-por-documento",
        "colunas_esperadas": {48},
        "atualizacao": "diaria",
    },
    "apoiamento-emendas": {
        "titulo": "Apoiamento emendas parlamentares",
        "modulo": "apoiamento-emendas-parlamentares-documentos",
        "modo": "ANO",
        # O texto da pagina fala em "exercicio e mes", mas o modo e ANO e o
        # select de meses vem vazio: so ha filtro de ano.
        "anos_portal": (2020, ANO_CORRENTE),
        "dicionario": "/dicionario-de-dados/apoiamentos-emendas-parlamentares",
        # O dicionario oficial lista 27 campos, mas o arquivo real traz 31:
        # ele inclui tambem autor, numero da emenda e localidade de aplicacao.
        # Verificado em 18/08/2026 no arquivo de 2022. O numero real e 31.
        "colunas_esperadas": {31},
        "atualizacao": "semanal",
    },
}

ALIASES = {
    "emendas": "emendas-parlamentares",
    "documentos": "documentos-despesa",
    "documentos-despesas": "documentos-despesa",
    "apoiamento": "apoiamento-emendas",
    "apoiamentos": "apoiamento-emendas",
}

MANIFEST_NOME = "_manifest.json"
ZIP_MAGIC = b"PK"

# Headers literais observados nos arquivos reais do portal. A conferencia por
# numero de colunas nao pega reordenacao nem renomeacao, entao guardamos o
# header inteiro dos arquivos ja baixados.
#
# A chave e o nome do arquivo sem o prefixo de ano ("2024_" -> "").
# Verificado nos arquivos reais em 01/08, 13/08 e 18/08 de 2026 -- as cinco
# tabelas ja estao catalogadas.
#
# ATENCAO: a coluna 7 de EmendasParlamentares.csv vem como "Localidade de
# aplicacao do recurso", mas o dicionario oficial a chama de "Localidade do Gasto".
# Divergencia da fonte -- o nome real e o que esta aqui.
HEADERS_CONFIRMADOS: Dict[str, List[str]] = {
    "EmendasParlamentares.csv": [
        "Código da Emenda", "Ano da Emenda", "Tipo de Emenda",
        "Código do Autor da Emenda", "Nome do Autor da Emenda", "Número da emenda",
        "Localidade de aplicação do recurso", "Código Município IBGE", "Município",
        "Código UF IBGE", "UF", "Região",
        "Código Função", "Nome Função", "Código Subfunção", "Nome Subfunção",
        "Código Programa", "Nome Programa", "Código Ação", "Nome Ação",
        "Código Plano Orçamentário", "Nome Plano Orçamentário",
        "Valor Empenhado", "Valor Liquidado", "Valor Pago",
        "Valor Restos A Pagar Inscritos", "Valor Restos A Pagar Cancelados",
        "Valor Restos A Pagar Pagos",
    ],
    "EmendasParlamentares_Convenios.csv": [
        "Código da Emenda", "Código Função", "Nome Função", "Código Subfunção",
        "Nome Subfunção", "Localidade do gasto", "Tipo de Emenda",
        "Data Publicação Convênio", "Convenente", "Objeto Convênio",
        "Número Convênio", "Valor Convênio",
    ],
    "EmendasParlamentares_PorFavorecido.csv": [
        "Código da Emenda", "Código do Autor da Emenda", "Nome do Autor da Emenda",
        "Número da emenda", "Tipo de Emenda", "Ano/Mês", "Código do Favorecido",
        "Favorecido", "Natureza Jurídica", "Tipo Favorecido", "UF Favorecido",
        "Município Favorecido", "Valor Recebido",
    ],
    "EmendasParlamentares_PorDocumento.csv": [
        "Código da Emenda", "Ano da Emenda", "Código do Autor da Emenda",
        "Nome do Autor da Emenda", "Número da emenda", "Valor Empenhado",
        "Valor Pago", "Tipo de Emenda", "Data Documento", "Código Documento",
        "Localidade de aplicação do recurso", "UF de aplicação do recurso",
        "Município de aplicação do recurso",
        "Código IBGE do município de aplicação do recurso", "Fase da despesa",
        "Código favorecido", "Favorecido", "Tipo Favorecido", "UF Favorecido",
        "Município Favorecido", "Código UG", "UG",
        "Código Unidade Orçamentária", "Unidade Orçamentária",
        "Código Órgão SIAFI", "Órgão", "Código Órgão Superior SIAFI",
        "Órgão Superior", "Código Grupo Despesa", "Grupo Despesa",
        "Código Elemento Despesa", "Elemento Despesa",
        "Código Modalidade Aplicação Despesa", "Modalidade Aplicação Despesa",
        "Código Plano Orçamentário", "Plano Orçamentário",
        "Código Função", "Função", "Código SubFunção", "SubFunção",
        "Código Programa", "Programa", "Código Ação", "Ação",
        "Linguagem Cidadã", "Código Subtítulo (Localizador)",
        "Subtítulo (Localizador)", "Possui convênio?",
    ],
    # Base 5 -- catalogado em 18/08/2026 a partir do arquivo de 2022.
    # 31 colunas, nao as 27 do dicionario: as quatro a mais (autor, nome do
    # autor, numero da emenda e localidade de aplicacao) permitem comparar
    # autor formal x apoiador real sem precisar juntar com a base 1.
    "ApoiamentoEmendasParlamentares.csv": [
        "Código Apoiador", "Apoiador", "Data do Apoio", "Data Retirada do Apoio",
        "Empenho", "Data última movimentação Empenho",
        "Código favorecido", "Favorecido", "Tipo Favorecido",
        "UF Favorecido", "Município Favorecido",
        "Código da Emenda", "Tipo de Emenda", "Ano da Emenda",
        "Código do Autor da Emenda", "Nome do Autor da Emenda",
        "Número da emenda", "Localidade de aplicação do recurso",
        "Código UG", "UG", "Código Unidade Orçamentária", "Unidade Orçamentária",
        "Código Órgão SIAFI", "Órgão",
        "Código Órgão Superior SIAFI", "Órgão Superior",
        "Código Ação", "Ação",
        "Valor Empenhado", "Valor Cancelado", "Valor Pago",
    ],
}


def chave_de_header(nome_arquivo: str) -> str:
    """'2024_EmendasParlamentares_PorDocumento.csv' -> sem o prefixo de ano."""
    return re.sub(r"^\d{4}_", "", nome_arquivo)

log = logging.getLogger("emendas")


# --------------------------------------------------------------------------- #
# Utilitarios
# --------------------------------------------------------------------------- #


def parse_anos(spec: str) -> List[int]:
    """Interpreta '2015:2026', '2015-2026', '2015,2018,2020' ou combinacoes."""
    anos: List[int] = []
    for parte in re.split(r"[,\s]+", spec.strip()):
        if not parte:
            continue
        m = re.fullmatch(r"(\d{4})\s*[:\-]\s*(\d{4})", parte)
        if m:
            ini, fim = int(m.group(1)), int(m.group(2))
            if ini > fim:
                ini, fim = fim, ini
            anos.extend(range(ini, fim + 1))
            continue
        if re.fullmatch(r"\d{4}", parte):
            anos.append(int(parte))
            continue
        raise argparse.ArgumentTypeError(
            f"Trecho de ano invalido: {parte!r}. Use 2015:2026, 2015-2026 ou 2015,2016."
        )
    if not anos:
        raise argparse.ArgumentTypeError("Nenhum ano informado.")
    ano_max = dt.date.today().year + 1
    fora = [a for a in anos if a < 2000 or a > ano_max]
    if fora:
        raise argparse.ArgumentTypeError(
            f"Anos fora do intervalo plausivel (2000-{ano_max}): {sorted(set(fora))}"
        )
    return sorted(set(anos))


def humano(nbytes: int) -> str:
    unidade = ["B", "KB", "MB", "GB", "TB"]
    valor = float(nbytes)
    i = 0
    while valor >= 1024 and i < len(unidade) - 1:
        valor /= 1024
        i += 1
    return f"{valor:.1f} {unidade[i]}"


def sha256_arquivo(caminho: Path, bloco: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as fh:
        for pedaco in iter(lambda: fh.read(bloco), b""):
            h.update(pedaco)
    return h.hexdigest()


def carregar_json(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def salvar_json(caminho: Path, dados: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(caminho)


def configurar_log(raiz: Path, verboso: bool) -> None:
    raiz.mkdir(parents=True, exist_ok=True)
    (raiz / "logs").mkdir(exist_ok=True)
    arquivo = raiz / "logs" / f"emendas_{dt.datetime.now():%Y%m%d_%H%M%S}.log"

    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verboso else logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))
    log.addHandler(console)
    fh = logging.FileHandler(arquivo, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s"))
    log.addHandler(fh)
    log.debug("Log desta execucao: %s", arquivo)


# --------------------------------------------------------------------------- #
# Plano: quais parametros baixar de cada dataset
# --------------------------------------------------------------------------- #


def parametros_do_dataset(dataset: str, anos_pedidos: Sequence[int],
                          anos_no_portal: Optional[Sequence[int]] = None) -> List[str]:
    """
    Retorna os parametros de URL a baixar.
      UNICO -> ["UNICO"]
      ANO   -> anos pedidos que existem no portal, em ordem crescente
    `anos_no_portal` vem da leitura do <select> da pagina; se ausente, usa a
    faixa declarada em DATASETS.
    """
    cfg = DATASETS[dataset]
    if cfg["modo"] == "UNICO":
        return ["UNICO"]

    if anos_no_portal:
        disponiveis = set(int(a) for a in anos_no_portal)
    else:
        ini, fim = cfg["anos_portal"]
        disponiveis = set(range(ini, fim + 1))
    return [str(a) for a in sorted(set(anos_pedidos) & disponiveis)]


def caminhos_do_item(raiz: Path, dataset: str, param: str, carimbo: Optional[str] = None):
    """Devolve (caminho_zip, pasta_csv, chave_no_manifesto)."""
    pasta = raiz / dataset
    if param == "UNICO":
        # Snapshot sem filtro de ano: o portal so publica o estado atual, sem
        # historico versionado. ZIP *e* pasta de CSV levam a data da execucao,
        # entao cada download vira uma competencia identificavel e as extracoes
        # nao se sobrescrevem -- e o que permite montar serie temporal depois.
        #   zip/emendas-parlamentares_20260801.zip
        #   csv_20260801/EmendasParlamentares.csv
        selo = carimbo or f"{dt.date.today():%Y%m%d}"
        return (pasta / "zip" / f"{dataset}_{selo}.zip",
                pasta / f"csv_{selo}",
                f"{dataset}/{selo}")
    return (pasta / "zip" / f"{dataset}_{param}.zip",
            pasta / "csv" / param,
            f"{dataset}/{param}")


def url_do_item(dataset: str, param: str) -> str:
    return f"{BASE}/{DATASETS[dataset]['modulo']}/{param}"


# --------------------------------------------------------------------------- #
# Extracao e conferencia de layout
# --------------------------------------------------------------------------- #


def extrair(caminho_zip: Path, pasta_saida: Path) -> List[str]:
    """Extrai o ZIP protegendo contra zip-slip. Retorna nomes extraidos."""
    if pasta_saida.exists():
        shutil.rmtree(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)

    extraidos: List[str] = []
    raiz = pasta_saida.resolve()
    with zipfile.ZipFile(caminho_zip) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            nome = info.filename.replace("\\", "/")
            alvo = (raiz / nome).resolve()
            if not str(alvo).startswith(str(raiz) + os.sep):
                log.warning("    entrada suspeita ignorada: %s", info.filename)
                continue
            alvo.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as origem, alvo.open("wb") as saida:
                shutil.copyfileobj(origem, saida)
            extraidos.append(str(alvo.relative_to(raiz)))
    return extraidos


def conferir_layout(pasta_csv: Path, dataset: str) -> List[dict]:
    """Le o header de cada CSV extraido e compara com o dicionario de dados."""
    esperadas = DATASETS[dataset]["colunas_esperadas"]
    achados: List[dict] = []
    for csv_path in sorted(pasta_csv.rglob("*.csv")):
        try:
            with csv_path.open("r", encoding="latin-1", newline="") as fh:
                header = next(csv.reader(fh, delimiter=";"), [])
        except OSError as exc:
            log.warning("    nao consegui ler %s: %s", csv_path.name, exc)
            continue
        n = len(header)
        registro = {"arquivo": csv_path.name, "colunas": n}

        if n not in esperadas:
            log.warning("    layout inesperado em %s: %d colunas (esperado %s)",
                        csv_path.name, n, sorted(esperadas))
            registro["alerta"] = f"{n} colunas, esperado {sorted(esperadas)}"

        # Conferencia forte: nomes e ordem das colunas, quando ja conhecidos.
        conhecido = HEADERS_CONFIRMADOS.get(chave_de_header(csv_path.name))
        if conhecido is None:
            log.debug("    %s: %d colunas (header ainda nao catalogado)",
                      csv_path.name, n)
            registro["header"] = header      # registra para catalogar depois
        elif header == conhecido:
            log.debug("    %s: %d colunas, header identico ao esperado", csv_path.name, n)
        else:
            faltando = [c for c in conhecido if c not in header]
            novas = [c for c in header if c not in conhecido]
            log.warning("    HEADER MUDOU em %s", csv_path.name)
            if faltando:
                log.warning("      sumiram : %s", ", ".join(faltando))
            if novas:
                log.warning("      surgiram: %s", ", ".join(novas))
            if not faltando and not novas:
                log.warning("      mesmas colunas, ordem diferente")
            registro["header"] = header
            registro["alerta"] = "header diferente do catalogado"

        achados.append(registro)
    return achados


# --------------------------------------------------------------------------- #
# Navegador (Playwright)
# --------------------------------------------------------------------------- #


class NavegadorPortal:
    """Contexto Chromium unico, reaproveitando o cookie aws-waf-token."""

    def __init__(self, headless: bool = False, espera_desafio: float = 5.0,
                 timeout_download: int = 900):
        self.headless = headless
        self.espera_desafio = espera_desafio
        self.timeout_download = timeout_download
        self._pw = self._browser = self._ctx = self._page = None

    def __enter__(self) -> "NavegadorPortal":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SystemExit(
                "Playwright nao instalado. O portal usa AWS WAF e bloqueia clientes "
                "HTTP simples, entao o navegador e obrigatorio:\n"
                "    pip install playwright && playwright install chromium"
            )
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._ctx = self._browser.new_context(accept_downloads=True, locale="pt-BR")
        self._page = self._ctx.new_page()
        return self

    def __exit__(self, *exc):
        for obj in (self._ctx, self._browser):
            try:
                obj and obj.close()
            except Exception:
                pass
        try:
            self._pw and self._pw.stop()
        except Exception:
            pass

    def abrir_pagina(self, modulo: str) -> None:
        """Abre a pagina HTML: resolve o desafio do WAF e grava o cookie."""
        self._page.goto(f"{BASE}/{modulo}", wait_until="networkidle")
        self._page.wait_for_timeout(int(self.espera_desafio * 1000))

    def anos_disponiveis(self, modulo: str) -> List[int]:
        """Le os <option> de #links-anos da pagina ja aberta."""
        try:
            valores = self._page.eval_on_selector_all(
                "#links-anos option", "os => os.map(o => o.value)")
        except Exception as exc:
            log.debug("  nao consegui ler #links-anos: %s", exc)
            return []
        return sorted(int(v) for v in valores if str(v).strip().isdigit())

    def link_unico(self) -> Optional[str]:
        try:
            return self._page.get_attribute("#link-unico a", "href")
        except Exception:
            return None

    def baixar(self, url: str, destino_zip: Path) -> None:
        """Dispara o download navegando para a URL e salva o arquivo."""
        destino_zip.parent.mkdir(parents=True, exist_ok=True)
        with self._page.expect_download(timeout=self.timeout_download * 1000) as info:
            self._page.evaluate("u => { location.href = u; }", url)
        download = info.value
        download.save_as(str(destino_zip))
        with destino_zip.open("rb") as fh:
            if not fh.read(2).startswith(ZIP_MAGIC):
                destino_zip.unlink(missing_ok=True)
                raise RuntimeError("o arquivo baixado nao e um ZIP")


# --------------------------------------------------------------------------- #
# Processamento
# --------------------------------------------------------------------------- #


def processar_item(navegador, dataset: str, param: str, raiz: Path,
                   manifest: dict, args) -> str:
    """Retorna 'ok', 'pulado' ou 'erro'."""
    caminho_zip, pasta_csv, chave = caminhos_do_item(raiz, dataset, param)
    url = url_do_item(dataset, param)

    completo = (chave in manifest and caminho_zip.exists()
                and (not args.extrair or pasta_csv.exists()))
    if completo and not args.forcar:
        log.info("[%s] ja presente, pulando", chave)
        return "pulado"

    if args.simular:
        log.info("[%s] (simulacao) baixaria %s", chave, url)
        return "ok"

    log.info("[%s] baixando %s", chave, url)
    try:
        navegador.baixar(url, caminho_zip)
    except Exception as exc:
        log.error("[%s] falhou: %s", chave, type(exc).__name__)
        log.debug("[%s] detalhe: %s", chave, exc)
        return "erro"

    tamanho = caminho_zip.stat().st_size
    log.info("[%s] %s (%s)", chave, caminho_zip.name, humano(tamanho))

    arquivos: List[str] = []
    layout: List[dict] = []
    if args.extrair:
        try:
            arquivos = extrair(caminho_zip, pasta_csv)
        except zipfile.BadZipFile as exc:
            log.error("[%s] ZIP corrompido: %s", chave, exc)
            return "erro"
        log.info("[%s] extraido: %d arquivo(s) -> %s", chave, len(arquivos),
                 pasta_csv.relative_to(raiz))
        layout = conferir_layout(pasta_csv, dataset)

    manifest[chave] = {
        "dataset": dataset,
        "parametro": param,
        "url": url,
        "zip": str(caminho_zip.relative_to(raiz)),
        "bytes": tamanho,
        "sha256": sha256_arquivo(caminho_zip),
        "baixado_em": dt.datetime.now().isoformat(timespec="seconds"),
        "arquivos": arquivos,
        "layout": layout,
    }
    return "ok"


def executar(args, navegador) -> Dict[str, int]:
    raiz: Path = args.destino.expanduser().resolve()
    caminho_manifest = raiz / MANIFEST_NOME
    manifest = carregar_json(caminho_manifest)
    contagem = {"ok": 0, "pulado": 0, "erro": 0}

    for dataset in args.lista_datasets:
        cfg = DATASETS[dataset]
        log.info("=" * 62)
        log.info("%s  (%s, atualizacao %s)", cfg["titulo"], cfg["modo"], cfg["atualizacao"])
        log.info("Pagina: %s/%s", BASE, cfg["modulo"])

        anos_portal: List[int] = []
        if navegador is not None:
            navegador.abrir_pagina(cfg["modulo"])
            if cfg["modo"] == "ANO":
                anos_portal = navegador.anos_disponiveis(cfg["modulo"])
                if anos_portal:
                    log.info("Anos oferecidos pelo portal: %d-%d",
                             anos_portal[0], anos_portal[-1])
                else:
                    log.warning("Nao consegui ler os anos na pagina; usando a faixa conhecida.")

        params = parametros_do_dataset(dataset, args.anos, anos_portal)
        if not params:
            log.info("Nenhum ano do periodo esta disponivel neste dataset.")
            continue
        if cfg["modo"] == "ANO":
            faltando = sorted(set(args.anos) - {int(p) for p in params})
            if faltando:
                log.info("Fora do portal, ignorados: %s",
                         ", ".join(str(a) for a in faltando))
            # O espelho do aviso acima: o portal tem exercicio que o periodo
            # pedido nao alcanca. Sem isto, um ano publicado adiantado seria
            # pulado em silencio -- exatamente o tipo de falha que so aparece
            # meses depois, quando falta dado.
            adiante = sorted(set(anos_portal) - set(args.anos))
            if adiante:
                log.warning("Portal oferece alem do periodo pedido: %s "
                            "-- use --anos para incluir",
                            ", ".join(str(a) for a in adiante))
        log.info("%d item(ns) a baixar", len(params))

        for i, param in enumerate(params, 1):
            log.debug("--- (%d/%d) %s/%s ---", i, len(params), dataset, param)
            resultado = processar_item(navegador, dataset, param, raiz, manifest, args)
            contagem[resultado] += 1
            if not args.simular:
                salvar_json(caminho_manifest, manifest)

    return contagem


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="emendas_transparencia",
        description="Baixa e extrai os dados de Emendas Parlamentares do Portal da Transparencia.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  %(prog)s                                # config do topo do arquivo\n"
            "  %(prog)s --simular                      # so mostra o plano\n"
            "  %(prog)s --anos 2025:2026 --forcar      # atualiza os anos recentes\n"
            "  %(prog)s --datasets apoiamento --anos 2020:2026\n"
        ),
    )
    p.add_argument("--destino", type=Path, default=PASTA_RAIZ,
                   help=f"pasta raiz (padrao: {PASTA_RAIZ})")
    p.add_argument("--anos", default=PERIODO, type=parse_anos,
                   help=f"periodo: 2015:2026, 2015-2026 ou 2015,2018 (padrao: {PERIODO})")
    p.add_argument("--datasets", default=",".join(DATASETS_PADRAO),
                   help="datasets separados por virgula (padrao: todos)")
    p.add_argument("--sem-extrair", dest="extrair", action="store_false",
                   help="apenas baixar os ZIPs, sem descompactar")
    p.add_argument("--forcar", action="store_true",
                   help="rebaixa itens que ja constam no manifesto")
    p.add_argument("--simular", action="store_true",
                   help="dry-run: mostra o plano sem abrir o navegador")
    p.add_argument("--headless", action="store_true",
                   help="roda o Chromium sem janela (o WAF costuma barrar; teste antes)")
    p.add_argument("--espera-desafio", type=float, default=5.0,
                   help="segundos aguardando o desafio do WAF apos abrir a pagina")
    p.add_argument("--timeout-download", type=int, default=900,
                   help="timeout por arquivo, em segundos (o ZIP e gerado sob demanda)")
    p.add_argument("-v", "--verboso", action="store_true", help="log detalhado")
    p.set_defaults(extrair=True)

    args = p.parse_args(argv)

    datasets: List[str] = []
    for bruto in args.datasets.split(","):
        nome = ALIASES.get(bruto.strip(), bruto.strip())
        if not nome:
            continue
        if nome not in DATASETS:
            p.error(f"dataset desconhecido: {bruto!r}. Opcoes: {list(DATASETS)}")
        if nome not in datasets:
            datasets.append(nome)
    args.lista_datasets = datasets

    raiz: Path = args.destino.expanduser().resolve()
    configurar_log(raiz, args.verboso)
    log.info("Pasta raiz : %s", raiz)
    log.info("Periodo    : %d-%d (%d exercicios)", args.anos[0], args.anos[-1], len(args.anos))
    log.info("Datasets   : %s", ", ".join(datasets))
    log.info("Extrair    : %s", "sim" if args.extrair else "nao")
    if args.simular:
        log.info("MODO SIMULACAO - navegador nao sera aberto, nada sera gravado")
        contagem = executar(args, None)
    else:
        with NavegadorPortal(headless=args.headless,
                             espera_desafio=args.espera_desafio,
                             timeout_download=args.timeout_download) as nav:
            contagem = executar(args, nav)

    log.info("=" * 62)
    log.info("Concluido: %d baixado(s), %d pulado(s), %d erro(s)",
             contagem["ok"], contagem["pulado"], contagem["erro"])
    if contagem["erro"] and not args.headless:
        log.info("Dica: erros seguidos costumam ser o WAF. Tente aumentar "
                 "--espera-desafio ou rode sem --headless.")
    return 1 if contagem["erro"] else 0


if __name__ == "__main__":
    sys.exit(main())
