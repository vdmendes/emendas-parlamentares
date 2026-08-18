# Emendas Parlamentares — Portal da Transparência

Baixa e extrai os três datasets de Emendas Parlamentares da CGU.

## Por que Playwright e não `requests`

O portal está atrás de **AWS WAF Bot Control**. `requests`, `httpx`, `curl` e até `fetch()`
de dentro da própria página levam 403 / erro de rede. O download só funciona em navegador
real, que executa o desafio JS e obtém o cookie `aws-waf-token`. O script abre um Chromium,
visita a página HTML de cada módulo (resolve o desafio) e reaproveita **o mesmo contexto**
para todos os downloads.

```bash
pip install playwright
playwright install chromium
```

## Configuração

A pasta de destino é resolvida nesta ordem, então **não é preciso editar o arquivo**:

1. `--destino` na linha de comando
2. variável de ambiente `EMENDAS_RAIZ`
3. a pasta `raw/` do projeto, procurada subindo a partir do arquivo do script — a busca é
   por uma pasta que contenha `raw/` ou `processed/`, então mover o script na árvore não quebra

O resto fica no topo do `emendas_transparencia.py`:

```python
ANO_CORRENTE = dt.date.today().year
PERIODO      = f"2015:{ANO_CORRENTE}"           # não envelhece na virada do ano
DATASETS_PADRAO = ["emendas-parlamentares", "documentos-despesa", "apoiamento-emendas"]
```

**Não há ano fixo para atualizar na virada do ano.** Em 2027 o teto vira 2027 sozinho, que
é exatamente quando o exercício de 2027 passa a existir — despesa de 2027 não é empenhada
antes de 2027.

Os anos que realmente existem vêm do `<select id="links-anos">` da página a cada execução, e
o script baixa a interseção entre eles e o período pedido. Os dois desencontros possíveis
aparecem no log:

| situação | mensagem |
|---|---|
| pediu ano que o portal não tem | `Fora do portal, ignorados: 2014` |
| portal tem ano além do período | `WARNING Portal oferece alem do periodo pedido: 2027` |

As faixas em `anos_portal` são só fallback para o caso de a leitura do `<select>` falhar, e
o teto delas também acompanha o relógio.

```bash
python3 emendas_transparencia.py --simular   # plano, sem abrir navegador
python3 emendas_transparencia.py             # baixa de verdade
```

## Os três datasets

| pasta | módulo / URL de download | modo | anos |
|---|---|---|---|
| `emendas-parlamentares` | `/emendas-parlamentares/UNICO` | `UNICO` | — (snapshot completo) |
| `documentos-despesa` | `/emendas-parlamentares-documentos/{ano}` | `ANO` | 2014–2026 |
| `apoiamento-emendas` | `/apoiamento-emendas-parlamentares-documentos/{ano}` | `ANO` | 2020–2026 |

Os anos são lidos do `<select id="links-anos">` da própria página a cada execução; a faixa
acima é só o fallback se a leitura falhar. Anos do período que não existem no portal são
listados no log e ignorados.

> A página do apoiamento diz "selecione o exercício **e o mês**", mas isso é texto residual:
> `modoApresentacao` é `ANO` e o select de meses vem vazio. **Só há filtro de ano.**

## Estrutura gerada

```
raw/
├── emendas-parlamentares/
│   ├── zip/emendas-parlamentares_20260801.zip   ← ZIP datado a cada execução
│   ├── csv_20260801/{emenda,convenios,favorecido}.csv   ← competência de agosto
│   └── csv_20260901/…                                   ← a do mês seguinte
├── documentos-despesa/
│   ├── zip/documentos-despesa_2015.zip …_2026.zip
│   └── csv/2015/*.csv … csv/2026/*.csv
├── apoiamento-emendas/
│   ├── zip/apoiamento-emendas_2020.zip …_2026.zip
│   └── csv/2020/*.csv …
├── logs/emendas_20260813_131500.log
└── _manifest.json     ← url, sha256, bytes, data, arquivos e nº de colunas de cada item
```

O dataset 1 é um snapshot sem histórico versionado, então **ZIP e pasta de CSV levam a data
da execução** (`_AAAAMMDD`). Cada download vira uma competência identificável, e as
extrações não se sobrescrevem — é o que permite montar série temporal depois. O ETL lê essa
data da pasta: a competência mais recente ocupa `processed/<base>/` e as anteriores vão para
`processed/_historico/<base>/<competência>/`.

Arquivos que já estavam em `raw/` não são tocados.

## Flags

| flag | padrão | o que faz |
|---|---|---|
| `--destino` | `PASTA_RAIZ` | sobrescreve a pasta raiz |
| `--anos` | `PERIODO` | `2015:2026`, `2015-2026` ou `2015,2018,2020` |
| `--datasets` | os 3 | apelidos aceitos: `emendas`, `documentos`, `apoiamento` |
| `--simular` | — | mostra o plano sem abrir o navegador |
| `--forcar` | — | rebaixa itens já no manifesto |
| `--sem-extrair` | — | só baixa os ZIPs |
| `--headless` | desligado | Chromium sem janela — **o WAF costuma barrar headless**, teste antes de agendar |
| `--espera-desafio` | `5.0` | segundos aguardando o desafio do WAF após abrir a página |
| `--timeout-download` | `900` | segundos por arquivo (o ZIP é gerado sob demanda) |
| `-v` | — | log detalhado |

```bash
# atualizar só os anos recentes
python3 emendas_transparencia.py --anos 2025:2026 --forcar

# só o apoiamento
python3 emendas_transparencia.py --datasets apoiamento --anos 2020:2026
```

## Conferência de layout

Após extrair, o script lê o header de cada CSV (`;`, latin-1) e faz **duas** conferências:

1. **Contagem de colunas** contra o dicionário de dados: **48** em documentos de despesa,
   **31** em apoiamento (o dicionário diz 27 — ver abaixo), **28/12/13** nas três tabelas de emendas.
2. **Header literal** — nomes e ordem — contra `HEADERS_CONFIRMADOS`, catalogado a partir
   dos arquivos reais. Isso pega renomeação e reordenação de coluna, que a contagem não
   pega. As cinco tabelas já estão catalogadas. Arquivo ainda não catalogado não gera
   alerta: o header é gravado no manifesto para ser catalogado depois.

Divergência vira `WARNING` no log, com a lista do que sumiu e do que surgiu, e fica
registrada no manifesto em `layout[].alerta`. É o alarme de que o layout mudou.

> **Duas divergências conhecidas entre dicionário e arquivo:**
> 1. A coluna 7 de "Por Emenda Parlamentar" chega como `Localidade de aplicação do recurso`,
>    mas o dicionário a chama de `Localidade do Gasto`.
> 2. O apoiamento tem **31** colunas, não as 27 do dicionário — traz também autor, número da
>    emenda e localidade de aplicação. E a chave `Empenho` tem 23 caracteres, não 22.
>
> O arquivo é que vale. Detalhes em `../documents/formato-arquivos-portal.md`.

## Lendo os CSVs

```python
import pandas as pd
df = pd.read_csv("csv/2024/xxx.csv", sep=";", encoding="latin-1",
                 decimal=",", thousands=".")
```

Dicionários:
[emendas](https://portaldatransparencia.gov.br/dicionario-de-dados/emendas-parlamentares) ·
[por documento](https://portaldatransparencia.gov.br/dicionario-de-dados/emendas-parlamentares-por-documento) ·
[apoiamentos](https://portaldatransparencia.gov.br/dicionario-de-dados/apoiamentos-emendas-parlamentares)

## Testes

```bash
python3 test_emendas.py
```

63 checagens com um navegador falso injetado no lugar do Playwright — sem rede, sem browser.
Cobrem período, seleção de anos por dataset, URLs, caminhos, extração, manifesto, conferência
de layout (contagem **e** header por nome/ordem), idempotência, `--forcar`, `--sem-extrair`,
falha isolada de download e zip-slip.

## Cadência sugerida

Datasets 1 e 2 são atualizados diariamente; o 3, semanalmente (o índice do portal diz mensal,
o texto da página diz semanal). Rodar 1×/dia de madrugada resolve os três — itens já baixados
são pulados pelo manifesto.

## Estado da carga (18/08/2026) — completa

| dataset | itens | volume |
|---|---|---|
| `emendas-parlamentares` | 1 competência (01/08) | 3 CSVs, 253 MB, 28/12/13 colunas ✔ |
| `documentos-despesa` | 2014–2026 (13 anos) | 4,1 GB, 4.700.200 linhas, 48 colunas ✔ |
| `apoiamento-emendas` | 2020–2026 (7 anos) | 55 MB, 98.847 linhas, 31 colunas ✔ |

Layout estável: header idêntico byte a byte nos 13 anos de documentos e nos 7 de apoiamento.

## Pendências para a primeira execução real

- [x] Confirmar encoding (ISO-8859-1) e separador `;` — **confirmados**
- [x] Confirmar que o ZIP do dataset 1 traz mesmo 3 CSVs — **confirmado** (28/12/13 colunas)
- [x] Formato numérico — decimal vírgula e **sem** ponto de milhar; existem valores negativos
      (cancelamentos). Não usar `thousands="."` no `read_csv`.
- [x] Medir tamanho dos ZIPs anuais — 2020 é o maior (36 MB → 1,1 GB extraído), não 2024
- [x] Baixar a base de apoiamento e catalogar o header — **feito**, e são 31 colunas
- [ ] Testar se `--headless` passa pelo WAF antes de agendar em cron/CI
