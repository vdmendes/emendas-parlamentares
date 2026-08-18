#!/usr/bin/env python3
"""
Testes do emendas_transparencia.py.

Nao abre navegador nem acessa a internet: injeta um baixador falso no lugar do
Playwright e valida toda a logica de plano, caminhos, extracao, manifesto,
conferencia de layout e idempotencia.
"""

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import emendas_transparencia as et

FALHAS = []


def checar(cond, msg):
    print(("  OK   " if cond else "  FALHA") + f"  {msg}")
    if not cond:
        FALHAS.append(msg)


def zip_com(arquivos: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for nome, conteudo in arquivos.items():
            zf.writestr(nome, conteudo)
    return buf.getvalue()


def linha(n_colunas: int) -> str:
    cab = ";".join(f"Coluna {i}" for i in range(1, n_colunas + 1))
    dados = ";".join(str(i) for i in range(1, n_colunas + 1))
    return f"{cab}\n{dados}\n"


# Conteudo que o "portal" devolveria para cada URL.
CONTEUDO = {
    "emendas-parlamentares/UNICO": {
        "emenda.csv": linha(28), "convenios.csv": linha(12), "favorecido.csv": linha(13),
    },
}
for ano in range(2014, 2027):
    CONTEUDO[f"emendas-parlamentares-documentos/{ano}"] = {f"{ano}_doc.csv": linha(48)}
for ano in range(2020, 2027):
    CONTEUDO[f"apoiamento-emendas-parlamentares-documentos/{ano}"] = {
        f"{ano}_apoio.csv": linha(31)
    }
# 2026 ainda nao publicado nos dois datasets anuais
del CONTEUDO["emendas-parlamentares-documentos/2026"]


class NavegadorFalso:
    """Substitui o Playwright: serve os ZIPs de CONTEUDO."""

    def __init__(self, anos_no_select=None, quebrar=()):
        self.anos_no_select = anos_no_select
        self.quebrar = set(quebrar)
        self.paginas_abertas = []
        self.baixados = []

    def abrir_pagina(self, modulo):
        self.paginas_abertas.append(modulo)

    def anos_disponiveis(self, modulo):
        if self.anos_no_select is not None:
            return self.anos_no_select
        return sorted({int(k.rsplit("/", 1)[1]) for k in CONTEUDO
                       if k.startswith(modulo + "/") and k.rsplit("/", 1)[1].isdigit()})

    def baixar(self, url, destino_zip):
        chave = url.replace(et.BASE + "/", "")
        self.baixados.append(chave)
        if chave in self.quebrar:
            raise RuntimeError("simulando falha do WAF")
        if chave not in CONTEUDO:
            raise RuntimeError("404")
        destino_zip.parent.mkdir(parents=True, exist_ok=True)
        destino_zip.write_bytes(zip_com(CONTEUDO[chave]))


def _args(destino, extra):
    """Mesmos defaults do main(), sem abrir navegador."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", type=Path, default=et.PASTA_RAIZ)
    ap.add_argument("--anos", default=et.PERIODO, type=et.parse_anos)
    ap.add_argument("--datasets", default=",".join(et.DATASETS_PADRAO))
    ap.add_argument("--sem-extrair", dest="extrair", action="store_false")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--simular", action="store_true")
    ap.set_defaults(extrair=True)
    ns = ap.parse_args(["--destino", str(destino), *extra])
    ns.lista_datasets = [et.ALIASES.get(d.strip(), d.strip())
                         for d in ns.datasets.split(",") if d.strip()]
    return ns


def rodar(destino, extra=(), navegador=None):
    nav = navegador if navegador is not None else NavegadorFalso()
    return et.executar(_args(destino, extra), nav), nav


def main():
    et.log.addHandler(__import__("logging").NullHandler())
    et.log.setLevel(__import__("logging").CRITICAL)  # silencia o log durante os testes

    print("\n1) parse_anos")
    checar(et.parse_anos("2015:2026") == list(range(2015, 2027)), "2015:2026 -> 12 anos")
    checar(et.parse_anos("2015-2018") == [2015, 2016, 2017, 2018], "2015-2018")
    checar(et.parse_anos("2020,2018,2020") == [2018, 2020], "duplicata ordenada")
    checar(et.parse_anos("2026:2024") == [2024, 2025, 2026], "intervalo invertido")
    for ruim in ("abc", "20155", "1990"):
        try:
            et.parse_anos(ruim)
            checar(False, f"{ruim!r} deveria falhar")
        except Exception:
            checar(True, f"{ruim!r} rejeitado")

    print("\n2) configuracao e URLs confirmadas")
    checar(str(et.PASTA_RAIZ).endswith("database_portal/raw"), "pasta raiz no topo do arquivo")
    # O periodo padrao nao pode ser um ano fixo: envelheceria sozinho na virada
    # do ano e o exercicio novo deixaria de ser baixado em silencio.
    ano_agora = __import__("datetime").date.today().year
    checar(et.PERIODO == f"2015:{ano_agora}",
           f"periodo padrao acompanha o relogio (hoje: {et.PERIODO})")
    checar(et.parse_anos(et.PERIODO)[-1] == ano_agora,
           "o periodo padrao vai ate o exercicio corrente")
    checar(et.DATASETS["documentos-despesa"]["anos_portal"][1] == ano_agora,
           "fallback de anos tambem acompanha o relogio")
    checar(et.url_do_item("emendas-parlamentares", "UNICO")
           == "https://portaldatransparencia.gov.br/download-de-dados/"
              "emendas-parlamentares/UNICO", "URL do arquivo unico")
    checar(et.url_do_item("documentos-despesa", "2019")
           == "https://portaldatransparencia.gov.br/download-de-dados/"
              "emendas-parlamentares-documentos/2019", "URL por ano (documentos)")
    checar(et.url_do_item("apoiamento-emendas", "2026")
           == "https://portaldatransparencia.gov.br/download-de-dados/"
              "apoiamento-emendas-parlamentares-documentos/2026", "URL por ano (apoiamento)")

    print("\n3) parametros por dataset")
    anos = list(range(2015, 2027))
    checar(et.parametros_do_dataset("emendas-parlamentares", anos) == ["UNICO"],
           "arquivo unico ignora o periodo")
    checar(et.parametros_do_dataset("documentos-despesa", anos)
           == [str(a) for a in range(2015, 2027)], "documentos: 2015-2026")
    checar(et.parametros_do_dataset("apoiamento-emendas", anos)
           == [str(a) for a in range(2020, 2027)], "apoiamento: corta anos < 2020")
    checar(et.parametros_do_dataset("documentos-despesa", [2010, 2014, 2015])
           == ["2014", "2015"], "anos anteriores a 2014 sao descartados")
    checar(et.parametros_do_dataset("documentos-despesa", anos, anos_no_portal=[2023, 2024])
           == ["2023", "2024"], "select da pagina tem prioridade sobre a faixa fixa")

    print("\n4) caminhos")
    z, c, k = et.caminhos_do_item(Path("/raw"), "documentos-despesa", "2024")
    checar(z == Path("/raw/documentos-despesa/zip/documentos-despesa_2024.zip")
           and c == Path("/raw/documentos-despesa/csv/2024") and k == "documentos-despesa/2024",
           "item anual: zip/ + csv/<ano>")
    z, c, k = et.caminhos_do_item(Path("/raw"), "emendas-parlamentares", "UNICO", carimbo="20260813")
    checar(z.name == "emendas-parlamentares_20260813.zip"
           and c.name == "csv_20260813"
           and k == "emendas-parlamentares/20260813",
           "arquivo unico: ZIP e pasta de CSV datados (serie temporal)")
    # duas execucoes em datas diferentes nao podem se sobrescrever
    _, c1, k1 = et.caminhos_do_item(Path("/raw"), "emendas-parlamentares", "UNICO",
                                    carimbo="20260801")
    _, c2, k2 = et.caminhos_do_item(Path("/raw"), "emendas-parlamentares", "UNICO",
                                    carimbo="20260901")
    checar(c1 != c2 and k1 != k2,
           "competencias diferentes vao para pastas diferentes")

    tmp = Path(tempfile.mkdtemp())
    try:
        print("\n5) execucao completa com navegador falso")
        raiz = tmp / "raw"
        contagem, nav = rodar(raiz, ["--anos", "2015:2026"])
        checar(nav.paginas_abertas == ["emendas-parlamentares",
                                       "emendas-parlamentares-documentos",
                                       "apoiamento-emendas-parlamentares-documentos"],
               "abriu a pagina HTML de cada modulo antes de baixar (cookie do WAF)")
        checar(contagem["ok"] == 1 + 11 + 7, "baixou 1 unico + 11 anos doc + 7 anos apoio")
        checar(contagem["erro"] == 0, "sem erros")
        checar("emendas-parlamentares-documentos/2026" not in nav.baixados,
               "nao tentou baixar ano ausente no select")

        hoje = f"{__import__('datetime').date.today():%Y%m%d}"
        checar((raiz / "emendas-parlamentares" / "zip"
                / f"emendas-parlamentares_{hoje}.zip").exists(), "ZIP unico datado gravado")
        checar((raiz / "emendas-parlamentares" / f"csv_{hoje}" / "emenda.csv").exists(),
               "3 CSVs do dataset 1 extraidos em csv_<data>/")
        checar((raiz / "documentos-despesa" / "csv" / "2024" / "2024_doc.csv").exists(),
               "documentos 2024 extraido em csv/2024/")
        checar((raiz / "apoiamento-emendas" / "csv" / "2022" / "2022_apoio.csv").exists(),
               "apoiamento 2022 extraido em csv/2022/")
        checar(not (raiz / "apoiamento-emendas" / "csv" / "2019").exists(),
               "apoiamento: nada antes de 2020")

        print("\n6) manifesto e conferencia de layout")
        man = json.loads((raiz / et.MANIFEST_NOME).read_text())
        checar(len(man) == 19, "19 entradas no manifesto")
        checar(all(len(v["sha256"]) == 64 and v["bytes"] > 0 for v in man.values()),
               "sha256 e tamanho registrados")
        cols_doc = {c["colunas"] for c in man["documentos-despesa/2024"]["layout"]}
        checar(cols_doc == {48}, "documentos: 48 colunas conferidas")
        cols_ap = {c["colunas"] for c in man["apoiamento-emendas/2022"]["layout"]}
        checar(cols_ap == {31}, "apoiamento: 31 colunas conferidas")
        cols_un = {c["colunas"] for c in man[f"emendas-parlamentares/{hoje}"]["layout"]}
        checar(cols_un == {28, 12, 13}, "emendas: 3 tabelas com 28/12/13 colunas")

        print("\n7) idempotencia e --forcar")
        z24 = raiz / "documentos-despesa" / "zip" / "documentos-despesa_2024.zip"
        mtime = z24.stat().st_mtime_ns
        contagem, nav2 = rodar(raiz, ["--anos", "2015:2026"])
        checar(contagem["ok"] == 0 and contagem["pulado"] == 19, "segunda execucao pula tudo")
        checar(nav2.baixados == [], "nada foi rebaixado")
        contagem, nav3 = rodar(raiz, ["--anos", "2024", "--datasets", "documentos", "--forcar"])
        checar(nav3.baixados == ["emendas-parlamentares-documentos/2024"],
               "--forcar rebaixa so o item pedido (alias 'documentos' aceito)")

        print("\n8) falha de download nao derruba a execucao")
        d2 = tmp / "falha"
        nav_ruim = NavegadorFalso(quebrar={"emendas-parlamentares-documentos/2023"})
        contagem, _ = rodar(d2, ["--anos", "2023:2025", "--datasets", "documentos"],
                               navegador=nav_ruim)
        checar(contagem["erro"] == 1 and contagem["ok"] == 2, "1 erro, 2 sucessos")
        checar(not (d2 / "documentos-despesa" / "zip"
                    / "documentos-despesa_2023.zip").exists(), "nada gravado do item que falhou")

        print("\n9) --sem-extrair")
        d3 = tmp / "so_zip"
        rodar(d3, ["--anos", "2024", "--datasets", "documentos", "--sem-extrair"])
        checar((d3 / "documentos-despesa" / "zip" / "documentos-despesa_2024.zip").exists(),
               "ZIP baixado")
        checar(not (d3 / "documentos-despesa" / "csv").exists(), "nada extraido")

        print("\n10) layout inesperado gera aviso, nao quebra")
        d4 = tmp / "layout"
        pasta = d4 / "csv"
        pasta.mkdir(parents=True)
        (pasta / "estranho.csv").write_text(linha(5), encoding="latin-1")
        achados = et.conferir_layout(pasta, "documentos-despesa")
        checar(len(achados) == 1 and achados[0]["colunas"] == 5,
               "conferencia reporta a contagem real (5 != 48)")
        checar("alerta" in achados[0], "contagem divergente registra alerta no manifesto")

        print("\n11) conferencia de header por nome e ordem")
        d5 = tmp / "header"
        pasta5 = d5 / "csv"
        pasta5.mkdir(parents=True)

        # 11a) header identico ao catalogado -> sem alerta
        certo = et.HEADERS_CONFIRMADOS["EmendasParlamentares_PorDocumento.csv"]
        (pasta5 / "2024_EmendasParlamentares_PorDocumento.csv").write_text(
            ";".join(certo) + "\n", encoding="latin-1")
        a = et.conferir_layout(pasta5, "documentos-despesa")[0]
        checar("alerta" not in a, "header correto passa sem alerta")
        checar(a["colunas"] == 48, "header correto tem 48 colunas")

        # 11b) coluna renomeada, mesma contagem -> pega o que a contagem nao pega
        renomeado = list(certo)
        renomeado[10] = "Localidade do Gasto"
        (pasta5 / "2024_EmendasParlamentares_PorDocumento.csv").write_text(
            ";".join(renomeado) + "\n", encoding="latin-1")
        b = et.conferir_layout(pasta5, "documentos-despesa")[0]
        checar(b["colunas"] == 48, "renomeacao mantem 48 colunas")
        checar(b.get("alerta") == "header diferente do catalogado",
               "renomeacao de coluna e detectada")
        checar(b.get("header") == renomeado, "header divergente fica gravado no manifesto")

        # 11c) mesmas colunas em ordem trocada -> tambem detectado
        trocado = list(certo)
        trocado[5], trocado[6] = trocado[6], trocado[5]
        (pasta5 / "2024_EmendasParlamentares_PorDocumento.csv").write_text(
            ";".join(trocado) + "\n", encoding="latin-1")
        c = et.conferir_layout(pasta5, "documentos-despesa")[0]
        checar(c.get("alerta") == "header diferente do catalogado",
               "reordenacao de colunas e detectada")

        # 11d) arquivo sem header catalogado (base 3) -> registra, nao alerta
        pasta6 = d5 / "csv3"
        pasta6.mkdir(parents=True)
        (pasta6 / "2022_Apoiamento.csv").write_text(linha(31), encoding="latin-1")
        e = et.conferir_layout(pasta6, "apoiamento-emendas")[0]
        checar("alerta" not in e, "header nao catalogado nao gera alerta")
        checar(len(e.get("header", [])) == 31,
               "header nao catalogado fica gravado para catalogacao futura")

        # 11e) base 5 catalogada: 31 colunas, nao as 27 do dicionario oficial
        pasta7 = d5 / "csv5"
        pasta7.mkdir(parents=True)
        b5 = et.HEADERS_CONFIRMADOS["ApoiamentoEmendasParlamentares.csv"]
        checar(len(b5) == 31, "base 5 catalogada com 31 colunas (dicionario dizia 27)")
        for extra in ("Código do Autor da Emenda", "Nome do Autor da Emenda",
                      "Número da emenda", "Localidade de aplicação do recurso"):
            checar(extra in b5, f"base 5 traz {extra!r}, ausente do dicionario")
        (pasta7 / "2022_ApoiamentoEmendasParlamentares.csv").write_text(
            ";".join(b5) + "\n", encoding="latin-1")
        g = et.conferir_layout(pasta7, "apoiamento-emendas")[0]
        checar("alerta" not in g, "header real da base 5 passa sem alerta")

        print("\n12) prefixo de ano e removido para casar o header")
        checar(et.chave_de_header("2024_EmendasParlamentares_PorDocumento.csv")
               == "EmendasParlamentares_PorDocumento.csv", "prefixo de ano removido")
        checar(et.chave_de_header("EmendasParlamentares.csv")
               == "EmendasParlamentares.csv", "nome sem prefixo fica intacto")

        print("\n13) protecao contra zip-slip")
        mal = tmp / "mal.zip"
        with zipfile.ZipFile(mal, "w") as zf:
            zf.writestr("../../fora.csv", "x")
            zf.writestr("ok.csv", "y")
        checar(et.extrair(mal, tmp / "extrai") == ["ok.csv"], "entrada com ../ ignorada")
        checar(not (tmp / "fora.csv").exists(), "nada escrito fora da pasta de saida")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S):")
        for f in FALHAS:
            print("  -", f)
        return 1
    print("Todos os testes passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
