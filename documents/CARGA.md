# Roteiro da carga completa

O download **tem que rodar na sua máquina** — o portal está atrás de AWS WAF e exige
Chromium com janela. O que roda em qualquer lugar é o ETL, depois.

## 1. Preparar o ambiente (uma vez)

```bash
cd ~/…/emendas-parlamentares/database_portal

python3 -m venv .venv
source .venv/bin/activate
pip install -r script-scrap/requirements.txt
pip install pandas pyarrow          # para o ETL
playwright install chromium
```

Não precisa mais editar caminho nenhum: o script resolve a pasta a partir da própria
localização (`database_portal/raw`).

## 2. Conferir o plano antes de baixar

```bash
cd script-scrap
python3 emendas_transparencia.py --simular
```

Deve listar 1 item do snapshot + os anos de documentos + os anos de apoiamento, pulando o
que já está no manifesto (`documentos-despesa/2024` e o snapshot de 01/08).

## 3. Baixar, em três levas

Separado de propósito: se o WAF derrubar no meio, você não perde o que já veio. Deixe a
janela do Chromium visível e não a use para outra coisa.

```bash
# a) snapshot completo — rápido, é o ZIP de 32 MB com as 3 tabelas
python3 emendas_transparencia.py --datasets emendas

# b) documentos de despesa — a leva pesada, ~13 arquivos
#    referência: 2024 = ZIP de 14,9 MB → CSV de 314 MB
python3 emendas_transparencia.py --datasets documentos --anos 2014:2026

# c) apoiamento — nunca foi executado, então rode um ano sozinho primeiro
python3 emendas_transparencia.py --datasets apoiamento --anos 2022 -v
python3 emendas_transparencia.py --datasets apoiamento --anos 2020:2026
```

Na leva (c), o log vai avisar que o header ainda não está catalogado e vai gravá-lo no
manifesto. Depois da primeira execução, copie esse header para `HEADERS_CONFIRMADOS` no
`emendas_transparencia.py` — a partir daí o script passa a detectar mudança de layout
também nessa base.

**Dimensionamento:** se os outros anos se parecerem com 2024, espere ~4 GB de CSV extraído
para os 13 anos de documentos, mais ~250 MB do snapshot. Os ZIPs somam bem menos.
Se algum ano demorar, aumente `--timeout-download` (o portal gera o ZIP sob demanda).

**Se der erro seguido:** é o WAF. Aumente `--espera-desafio 10`, confirme que não está com
`--headless`, e refaça só os itens que faltaram — o manifesto pula o que já veio.

## 4. Processar

```bash
cd etl
python3 etl_emendas.py --listar     # confere o que foi reconhecido
python3 etl_emendas.py
```

Referência de tempo: 582 MB de CSV → 33 MB de parquet em 19 s.

## 5. Conferir

```bash
cd ../script-scrap && python3 test_emendas.py     # 55 checagens, sem rede
```

E no ETL, olhe os `WARNING` de linhas sem `codigo_emenda`: são reais (o portal grava
"Sem informação") e precisam entrar na nota metodológica de qualquer análise que junte bases.

## Cadência depois da carga inicial

Datasets 1 e 2 são atualizados diariamente; o 3, semanalmente. Uma execução por dia de
madrugada resolve os três — o manifesto pula o que já está lá. O snapshot da base 1 é o
único que precisa de `--forcar` para virar série temporal (cada execução guarda um ZIP
datado):

```bash
python3 emendas_transparencia.py --datasets emendas --forcar   # nova competência
python3 emendas_transparencia.py --anos 2025:2026 --forcar     # anos recentes
python3 etl/etl_emendas.py                                     # incremental: só o novo
```

O ETL **não** precisa de `--forcar` aqui: ele compara tamanho e data de cada CSV com o que
está no `_etl.json` e processa só o que é novo ou mudou. Uma execução mensal típica lê
apenas os três arquivos da competência nova — segundos, em vez dos 2min20 da carga cheia.
