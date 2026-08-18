# Emendas Parlamentares — documentação do projeto

Pipeline que baixa, valida e estrutura os dados abertos de Emendas Parlamentares do Portal
da Transparência (CGU), transformando 4,4 GB de CSV bruto em 135 MB de parquet tipado.

> **Estado em 18/08/2026:** carga completa. 5.795.017 linhas em cinco bases, 2014–2026.

---

## 1. Mapa rápido

```
database_portal/
│
├── script-scrap/                 ← todo o código
│   ├── COMANDOS.md                 ⇦ COMECE AQUI ao retomar
│   ├── README.md                   como o scraper funciona e por quê
│   ├── emendas_transparencia.py    baixa do portal          (685 linhas)
│   ├── test_emendas.py             63 checagens, sem rede   (333)
│   ├── build_dic.py                gera o dicionário .xlsx  (300)
│   └── etl/
│       ├── README.md               armadilhas de análise — leitura obrigatória
│       └── etl_emendas.py          CSV → parquet            (775)
│
├── raw/                          ← como veio do portal, intocado
├── processed/                    ← parquet tipado, um arquivo por ano
│
├── documents/                    ← documentação
│   ├── PROJETO.md                  este arquivo
│   ├── CARGA.md                    roteiro da carga completa
│   ├── portaltransparenciaemendasestrutura.md   engenharia reversa do portal
│   ├── estrutura_bases_emendas_parlamentares.md modelo relacional das 5 bases
│   ├── formato-arquivos-portal.md  encoding, separador, formatos — medido
│   └── prints/                     dicionários oficiais em imagem
│
└── metadata/
    └── dicionario_emendas_parlamentares.xlsx    uma aba por base
```

**Para retomar em 30 segundos:** `script-scrap/COMANDOS.md`.
**Antes de escrever qualquer análise:** `script-scrap/etl/README.md`.

---

## 2. Arquitetura de dados

```
   ┌────────────────────────────────────────────────────────────┐
   │  portaldatransparencia.gov.br   (atrás de AWS WAF Bot Ctrl) │
   └────────────────────────┬───────────────────────────────────┘
                            │  Chromium real resolve o desafio JS
                            │  e obtém o cookie aws-waf-token
                            ▼
   ┌────────────────────────────────────────────────────────────┐
   │  emendas_transparencia.py                                   │
   │  · lê os anos do <select> da página (não confia em constante)│
   │  · baixa o ZIP, extrai, confere header contra o catálogo     │
   │  · grava sha256 + bytes + layout no _manifest.json           │
   └────────────────────────┬───────────────────────────────────┘
                            ▼
   ┌────────────────────────────────────────────────────────────┐
   │  raw/            4,4 GB   — como veio, nunca reescrito      │
   │                                                             │
   │   emendas-parlamentares/  zip/…_20260801.zip                │
   │                           csv_20260801/  ← competência       │
   │   documentos-despesa/     zip/…_2014.zip … _2026.zip        │
   │                           csv/2014/ … csv/2026/             │
   │   apoiamento-emendas/     csv/2020/ … csv/2026/             │
   │   logs/                   _manifest.json                    │
   └────────────────────────┬───────────────────────────────────┘
                            │  latin-1 · ';' · decimal vírgula
                            │  códigos → texto · datas → datetime
                            ▼
   ┌────────────────────────────────────────────────────────────┐
   │  etl_emendas.py   incremental: só o novo ou o que mudou     │
   │  · leitura em blocos de 200 mil linhas (o maior CSV: 1,1 GB)│
   │  · blocos costurados num único parquet por ano              │
   └────────────────────────┬───────────────────────────────────┘
                            ▼
   ┌────────────────────────────────────────────────────────────┐
   │  processed/       135 MB  — 5.795.017 linhas                │
   │                                                             │
   │   emenda/      emenda_2014.parquet … emenda_2026.parquet    │
   │   documento/   documento_2014.parquet … _2026.parquet       │
   │   favorecido/  favorecido_2014.parquet … _2026.parquet      │
   │   convenios/   convenios_0000.parquet, _2015 … _2026        │
   │   apoiamento/  apoiamento_2020.parquet … _2026.parquet      │
   │   _historico/  competências superadas do download único     │
   └────────────────────────────────────────────────────────────┘
```

### O que há em cada base

| base | linhas | col | arquivos | anos | 1 linha = |
|---|---:|---:|---:|---|---|
| `emenda` | 94.304 | 30 | 13 | 2014–2026 | emenda × classificação × localidade |
| `documento` | 4.700.200 | 49 | 13 | 2014–2026 | documento SIAFI (empenho/liquidação/pagamento) |
| `favorecido` | 816.789 | 15 | 13 | 2014–2026 | emenda × favorecido × ano-mês |
| `convenios` | 84.877 | 14 | 13 | 2015–2026 | convênio |
| `apoiamento` | 98.847 | 32 | 7 | 2020–2026 | apoiador × empenho |

Compressão da cadeia inteira: **4,4 GB de CSV → 135 MB de parquet**, fator 33×.

---

## 3. Como as bases se ligam

```
                    ┌──────────────────────────────┐
                    │  emenda                       │
                    │  PK: codigo_emenda (12 díg.)  │
                    │  valores ACUMULADOS até a      │
                    │  data da competência           │
                    └──────────────┬───────────────┘
                                   │ codigo_emenda
              ┌────────────────────┼────────────────────┐
              │                    │                    │
   ┌──────────▼─────────┐ ┌────────▼────────┐ ┌────────▼────────┐
   │  documento          │ │  favorecido      │ │  convenios       │
   │  PK: codigo_documento│ │ emenda+favorec. │ │ PK: num_convenio │
   │  23 caracteres      │ │ +ano_mes        │ │                  │
   │  fase: emp/liq/pgto │ │                 │ │                  │
   └──────────┬──────────┘ └─────────────────┘ └──────────────────┘
              │ codigo_documento  (só linhas com fase = Empenho)
   ┌──────────▼──────────┐
   │  apoiamento          │
   │  codigo_documento +  │
   │  codigo_apoiador     │
   │  → autoria real das  │
   │    emendas coletivas │
   └──────────────────────┘
```

**Anatomia das chaves**

| chave | formato | exemplo |
|---|---|---|
| `codigo_emenda` | 12 díg. = ano(4) + autor(4) + número(4) | `202038950002` |
| `codigo_documento` | 23 car. = UG(6) + gestão(5) + ano(4) + tipo(2) + nº(6) | `110594000012022NE000135` |
| `codigo_favorecido` | CPF/CNPJ — **40% começam com zero** | `01234567000189` |

`tipo` no `codigo_documento` identifica a fase: `NE` empenho, `NS`/`NL` liquidação,
`OB`/`DF`/`DR` pagamento. A base `apoiamento` é 100% `NE`, então casa exatamente com o
subconjunto `fase_despesa == "Empenho"` de `documento`.

---

## 4. As decisões, e por quê

### 4.1 Navegador real em vez de `requests`

O portal está atrás de **AWS WAF Bot Control**. `requests`, `httpx`, `curl` e até `fetch()`
de dentro da própria página levam 403. Não é rate limit: é desafio JS.

A saída é um Chromium de verdade (Playwright), que executa o desafio e obtém o cookie
`aws-waf-token`. O script reaproveita **o mesmo contexto** entre downloads — o cookie vive
ali. Consequência prática: **a carga não roda headless nem em CI** sem teste prévio, e
precisa de sessão gráfica.

> Alternativa descartada: a API oficial `api-de-dados` (com chave) não tem WAF, mas é
> paginada e imprópria para carga histórica. Serve para enriquecimento pontual.

### 4.2 Os anos vêm do portal, não de uma constante

`PERIODO` acompanha o relógio (`f"2015:{ANO_CORRENTE}"`), e os anos que realmente existem
são lidos do `<select id="links-anos">` a cada execução. Não há ano fixo para atualizar na
virada — e os dois desencontros possíveis viram log:

```
Fora do portal, ignorados: 2014
WARNING Portal oferece alem do periodo pedido: 2027 -- use --anos para incluir
```

O segundo aviso existe para que um exercício publicado adiantado não seja pulado em
silêncio.

### 4.3 Conferência de layout em duas camadas

Contar colunas não pega renomeação nem reordenação. Então, além da contagem, o header
literal de cada CSV é comparado com `HEADERS_CONFIRMADOS` — catalogado a partir dos arquivos
reais. Divergência vira `WARNING` com a lista do que sumiu e do que surgiu, e fica no
manifesto.

Foi esse alarme que revelou as 31 colunas do apoiamento (ver §5.1).

### 4.4 Competências datadas para o download único

As bases `emenda`, `favorecido` e `convenios` vêm de um download **sem filtro de ano**: o
portal publica só o estado atual, sem histórico versionado. Cada download é uma fotografia
inteira.

Por isso ZIP e pasta de CSV levam a data: `csv_20260801/`. E em `processed/`, a competência
mais recente ocupa `<base>/`, as anteriores vão para `_historico/<base>/<competência>/`.

**A regra é derivada do que está em `raw/`, não de estado guardado.** Quando chega
`csv_20260901`, agosto migra sozinho para o histórico na execução seguinte. Ninguém move
arquivo à mão.

### 4.5 Por que os anos antigos são reescritos a cada competência

Seria tentador congelar 2014–2025 e atualizar só o ano corrente. Os dados dizem que não:

- **36,3%** dos documentos emitidos em 2026 são de emendas de anos anteriores —
  **R$ 8,40 bi** pagos em 2026 para emendas de 2015 a 2025;
- em `emenda`, **57.799 linhas** de 2014–2025 já têm restos a pagar pagos.

Uma emenda de 2020 continua se movendo em 2026. Congelar anos antigos os faria divergir da
realidade **em silêncio** — o pior modo de errar. A competência nova reescreve os 13
arquivos; a anterior fica inteira no histórico para comparação.

### 4.6 Tudo é texto até prova em contrário

O ETL lê o CSV inteiro como `str` e só então tipa. Nada de deixar o pandas adivinhar:

- **códigos → texto.** 145.170 valores de `codigo_favorecido` começam com zero. Ler como
  número apagaria o primeiro dígito do CNPJ.
- **valores → `Float64`** com `decimal=","` e **sem** `thousands="."`. Medido: 724.182
  valores, zero com ponto de milhar. Passar `thousands="."` corromperia em silêncio.
- **datas → `datetime64`**, com **dois** parsers: `DD/MM/AAAA` e ISO com hora — o portal usa
  as duas convenções no mesmo arquivo (§5.2).
- **`"Sem informação"`, `""` e `-1` → nulo.** São os sentinelas do portal.

### 4.7 Um arquivo por ano, não partição Hive

O layout começou como `base/ano=2024/parte-0000.parquet` (padrão Hive) e virou
`base/base_2024.parquet`. Três razões:

1. **Legibilidade** — `ls processed/emenda/` mostra a série temporal inteira.
2. **Recorte por arquivo** é mais direto que `filters=` e sem pegadinha de tipo: o pyarrow
   inferia `ano=2024` como inteiro, então `filters=[("ano","==","2024")]` levantava
   `ArrowNotImplementedError`.
3. **Um arquivo, não vários pedaços.** Exigiu trocar o gravador: cada ano tem um
   `ParquetWriter` aberto e os blocos de 200 mil linhas são costurados dentro dele — a
   leitura continua em blocos porque o maior CSV tem 1,1 GB.

O ano virou **coluna** (`ano`, `Int64`). Ano indeterminável vira `0000` e não a palavra
`desconhecido`, para o tipo da coluna não variar entre bases.

### 4.8 Incremental por impressão digital

O `_etl.json` guarda tamanho, `mtime`, pasta de destino e a lista de parquets de cada CSV
processado. Reprocessa apenas o que é novo, mudou de tamanho/data, mudou de destino, ou
perdeu a saída do disco.

Sem `sha256`: seriam vários segundos por arquivo de 1 GB a cada execução — o custo que o
incremental existe para evitar. O portal republica o arquivo inteiro, então tamanho + mtime
detectam qualquer mudança real.

O manifesto é gravado **a cada arquivo**. Execução interrompida retoma de onde parou.

### 4.9 Caminhos resolvidos subindo a árvore

Nenhum caminho absoluto no código. Os scripts sobem a árvore até achar a pasta que contém
`raw/` ou `processed/`. Foi o que permitiu mover `etl/` para dentro de `script-scrap/` sem
quebrar nada — e permitirá a próxima reorganização.

Precedência: `--destino` → variável de ambiente → busca na árvore.

---

## 5. O que os dados revelaram

Tudo aqui foi **medido nos arquivos**, não lido na documentação. Detalhes em
`formato-arquivos-portal.md`.

### 5.1 O dicionário oficial erra em dois pontos

| item | dicionário | realidade |
|---|---|---|
| colunas do apoiamento | 27 | **31** — traz também autor, número da emenda e localidade |
| chave `Empenho` | 22 caracteres | **23** — e casa direto com `codigo_documento` |
| coluna 7 de `emenda` | "Localidade do Gasto" | "Localidade de aplicação do recurso" |

As 4 colunas extras do apoiamento importam: permitem comparar **autor formal × apoiador
real** sem juntar com outra base. Em 2022, autor ≠ apoiador em **100%** das linhas — 5
autores coletivos contra 201 apoiadores individuais.

### 5.2 Duas convenções de data no mesmo arquivo

| coluna | formato |
|---|---|
| `Data última movimentação Empenho` | `15/06/2022` |
| `Data do Apoio`, `Data Retirada do Apoio` | `2025-06-13 12:18:16` |

Um parser único teria zerado as duas últimas **em silêncio**.

⚠️ E `data_apoio` provavelmente **não é a data do ato político**: no arquivo de 2022, todas
as datas caem entre março/2025 e julho/2026, com 1.023 das 2.670 linhas no mesmo carimbo.
Parece registro em lote, coerente com a publicação ter começado depois da ADPF 854. Para
ordenar no tempo, use `data_ultima_movimentacao_empenho`.

### 5.3 Ausências que mudam o recorte de qualquer análise

| campo | ausência | onde |
|---|---|---|
| `codigo_emenda` | **100% em 2014**, 57,8% em 2015, 47,8% em 2017 → 0,0% em 2026 | `documento` |
| `codigo_emenda` | 18,9% | `emenda` |
| `codigo_ibge_aplicacao` | **48,8%** em 2024 | `documento` |

O `codigo_emenda` só passa a ser preenchido de forma confiável **a partir de 2018** — análise
que dependa de junção deveria começar aí, ou declarar a perda. E quase metade das linhas não
tem município: análise territorial municipal roda em pouco mais da metade da base.

Essas linhas são um **achado, não um bug**. O portal grava `"Sem informação"`.

### 5.4 Três armadilhas que geram número errado sem dar erro

**a) `ano` não significa a mesma coisa nas cinco bases.**

| base | `ano` é o ano da… |
|---|---|
| `emenda`, `convenios`, `apoiamento` | **emenda** (quando foi proposta) |
| `documento`, `favorecido` | **execução** (quando o dinheiro se moveu) |

Em `favorecido_2024` estão pagamentos feitos em 2024 — de emendas de 2024 (36.447), mas
também de 2023 (23.301), 2022, 2021, 2020, 2019. Somar `emenda_2024` com `favorecido_2024`
mistura perguntas diferentes.

**b) Em `apoiamento`, o valor repete por apoiador.** Não é rateado: aparece integral em cada
linha. 2,1 apoiadores por empenho em média, até 108 no extremo. Somar direto infla de 1,3×
a 2,5× — R$ 73,72 bi em vez de **R$ 39,22 bi**.

```python
b5.drop_duplicates("codigo_documento").valor_empenhado.sum()   # total correto
b5.groupby("apoiador").valor_empenhado.sum()                   # atribuição por parlamentar
```

**c) Em `documento`, `valor_empenhado` e `valor_pago` são mutuamente exclusivos por linha.**
Filtre por `fase_despesa` antes de somar.

### 5.5 Outros padrões

- **2020 é atípico:** 1,3 milhão de documentos, mais que o triplo de qualquer outro ano.
- **Corte limpo em 2023 no apoiamento:** RP9 (relator) até 2022, RP8 (comissão) de 2023 em
  diante. Só 2022 é misto.
- **Retiradas de apoio zeram a partir de 2024:** 11,2% · 11,5% · 18,8% · 4,7% · 0 · 0 · 0.
  Abrupto demais para ser comportamento — parece mudança de regra ou campo que deixou de ser
  alimentado.
- **O ZIP é pré-gerado:** o baixado em 13/08 tinha data interna de 01/08. "Atualização
  diária" é do pacote; a data interna do ZIP é o carimbo real dos dados.

---

## 6. Como retomar

```bash
cd /datalake/financas_publicas/emendas-parlamentares/database_portal
source .venv/bin/activate
cd script-scrap

python3 emendas_transparencia.py --simular     # o que falta baixar
python3 emendas_transparencia.py               # baixa (abre Chromium — não mexa na janela)
python3 etl/etl_emendas.py                     # só o novo vira parquet
python3 test_emendas.py                        # 63 checagens, sem rede
```

```python
import pandas as pd
pd.read_parquet("processed/emenda")                            # base inteira
pd.read_parquet("processed/documento/documento_2024.parquet")  # um ano
```

Comandos completos em `../script-scrap/COMANDOS.md`.

### Ordem de leitura, se estiver frio

1. `script-scrap/COMANDOS.md` — o que rodar
2. este arquivo, §4 — por que está assim
3. `script-scrap/etl/README.md` — armadilhas antes de analisar
4. `estrutura_bases_emendas_parlamentares.md` — modelo relacional em detalhe
5. `formato-arquivos-portal.md` — evidência empírica de cada afirmação

---

## 7. Pendências

- [ ] Testar se `--headless` passa pelo WAF, antes de pensar em cron/CI
- [ ] Confirmar com a CGU o significado de `Data do Apoio` (§5.2)
- [ ] Investigar por que as retiradas de apoio zeram em 2024 (§5.5)
- [ ] Segunda competência do download único, para validar o `_historico/` com dado real
- [ ] Cruzamento com `REPASSE-FAF-COM-POPULACAO-2025.xlsx` e
      `PlanilhaPesquisaConsolidadaTodosEstados.xlsx` (na raiz da pasta do projeto)

## 8. Cadência sugerida

Emendas e documentos são atualizados diariamente; apoiamento, semanalmente. Uma execução por
dia resolve os três — o manifesto pula o que já veio. O download único precisa de `--forcar`
para gerar competência nova.

---

*Última atualização: 18/08/2026.*
