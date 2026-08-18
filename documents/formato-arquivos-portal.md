# Formato físico dos arquivos do Portal da Transparência

Verificação empírica sobre os arquivos realmente baixados, não sobre o que a documentação
do portal supõe. Feita em 18/08/2026 sobre `raw/`.

Arquivo de referência: `documentos-despesa/csv/2024/2024_EmendasParlamentares_PorDocumento.csv`
(a extração de teste de 13/08). Os três CSVs do snapshot da base 1 foram conferidos em
paralelo e têm **exatamente as mesmas convenções**.

---

## 1. Encoding — ISO-8859-1, sem BOM

| teste | resultado |
|---|---|
| `file -bi` | `text/plain; charset=iso-8859-1` |
| decodificar 50 MB como UTF-8 | falha no byte 2 (`0xF3` = `ó` de "Código") |
| decodificar como `latin-1` / `cp1252` | ambos OK |
| BOM | ausente |
| bytes > 127 distintos | 25 — apenas `ª º Á Â Ã Ç É Ê Í Ó Ô Õ Ú à á â ã ç é ê í ó ô õ ú` |
| bytes na faixa `0x80`–`0x9F` | **nenhum** |

A última linha é a que resolve a dúvida "ISO-8859-1 ou Windows-1252?": é justamente na faixa
`0x80`–`0x9F` que os dois divergem (aspas curvas, travessão, `€`). Como nenhum byte dessa
faixa aparece, **os dois encodings produzem resultado idêntico** neste conjunto de dados.
Use `latin-1` e não se preocupe.

```python
encoding="latin-1"      # ou "cp1252" — dá no mesmo aqui
```

## 2. Separador e quoting — `;` com todos os campos entre aspas

| característica | valor |
|---|---|
| delimitador | `;` (47 ocorrências no header de 48 colunas) |
| detecção pelo `csv.Sniffer` | `;` |
| aspas | `"` em **todos** os campos, inclusive numéricos e vazios |
| `;` dentro de campo | **0** ocorrências |
| `"` escapado dentro de campo | **0** ocorrências |
| quebra de linha dentro de campo | **0** ocorrências |
| fim de linha | **CRLF** (`\r\n`) |

Ou seja: o arquivo é bem-comportado. Como não há separador nem aspas dentro de campo,
um `split(";")` ingênuo até funcionaria — mas não vale o risco, já que isso é
característica *destes* arquivos e pode mudar (um `Objeto Convênio` com ponto e vírgula
resolveria o assunto).

**Integridade:** 362.091 linhas de dados, **todas** com exatamente 48 campos. Nenhuma linha
malformada.

## 3. Formato numérico — vírgula decimal, **sem** ponto de milhar

724.182 valores monetários analisados (`Valor Empenhado` + `Valor Pago`):

| característica | resultado |
|---|---|
| no padrão `-?\d+,\d{2}` | **724.182 de 724.182** (100%) |
| com ponto de milhar `.` | **0** |
| negativos | 1.386 (cancelamentos) |
| casas decimais | sempre exatamente 2 |
| maior parte inteira | 9 dígitos (centenas de milhões) |

```python
decimal=","             # correto
thousands="."           # ERRADO — não usar
```

Passar `thousands="."` aqui não daria erro: apenas corromperia silenciosamente qualquer
valor que um dia viesse com ponto. O ponto **não é** separador de milhar neste arquivo, e
o campo já vem sem formatação de exibição.

**Datas:** `Data Documento` está em `DD/MM/AAAA` em 100% das linhas, sem exceção.

## 4. Códigos — a razão de ler tudo como texto

| campo | tamanhos observados | começam com `0` |
|---|---|---:|
| `Código da Emenda` | 12 (361.693 linhas) · "Sem informação" (398) | 0 |
| `Código favorecido` | 14 (CNPJ) · 9 · 6 · 3 · 2 | **145.170** |
| `Código IBGE do município…` | 7 · "Sem informação" (176.596) | 0 |
| `Código Documento` | 23, fixo em todas as linhas | 0 |

**145.170 valores de `Código favorecido` começam com zero** — 40% das linhas. Lê-los como
número apagaria o primeiro dígito do CNPJ. Essa é a confirmação empírica da regra
"código é texto".

`Código Documento` tem **23 caracteres**, não 22. Exemplo: `155023264432024NS006323` =
UG (6) + Gestão (5) + ano (4) + tipo (2) + número (6). O dicionário descreve o campo
`Empenho` da base 5 como tendo 22 caracteres (UG 6 + Gestão 5 + Empenho 11). **Conferir na
primeira carga da base de apoiamento** se as duas chaves realmente casam — a junção
base 2 → base 5 depende disso.

## 5. Sentinelas de nulo

O portal não usa campo vazio para ausência; usa strings:

| sentinela | onde | ocorrências |
|---|---|---:|
| `Sem informação` | `Código IBGE do município de aplicação` | 176.596 (**48,8%**) |
| `Sem informação` | `Código da Emenda` | 398 (0,1%) |
| `-1` | `Código favorecido` | 14.286 (3,9%) |
| `S/I` | `Código do Autor`, `Número da emenda` (base 1) | — |

⚠️ **Quase metade das linhas de 2024 não tem código IBGE de município.** São despesas de
abrangência nacional ou estadual, em que a regionalização não desce ao município. Análise
territorial municipal trabalha, portanto, com pouco mais da metade da base — isso precisa
ser dito explicitamente, não silenciado por um `dropna()`.

## 6. Tamanho e compressão

| arquivo | MB | linhas | colunas | bytes/linha |
|---|---:|---:|---:|---:|
| `2024_..._PorDocumento.csv` | 329,2 | 362.091 | 48 | 909 |
| `EmendasParlamentares.csv` | 47,2 | 94.304 | 28 | 501 |
| `EmendasParlamentares_Convenios.csv` | 25,9 | 84.877 | 12 | 305 |
| `EmendasParlamentares_PorFavorecido.csv` | 180,2 | 816.789 | 13 | 221 |
| **total em `raw/`** | **582,5** | 1.358.061 | | |

**Compressão (base 2, ano 2024):**

```
ZIP  15,6 MB  →  CSV 329,2 MB      21,1x   (95% de compressão — texto repetitivo)
CSV 329,2 MB  →  parquet 8,4 MB    39x     (zstd + dicionário de strings)
```

O parquet fica **1,9x menor que o próprio ZIP** e ainda é consultável sem descompactar.

**Projeção para os 13 anos da base 2**, se todos se parecerem com 2024:

| camada | tamanho |
|---|---|
| ZIPs | ~0,2 GB |
| CSVs extraídos | ~4,3 GB |
| parquet | ~0,11 GB |
| **pico de disco** (as três camadas ao mesmo tempo) | **~4,6 GB** |

Projeção conservadora: 2024 é provavelmente o teto, já que o volume de emendas cresceu
muito no período. Anos anteriores a 2020 devem ser bem menores.

## 7. Nota sobre frescor do arquivo

O ZIP baixado em 13/08 tinha data interna de **01/08/2026 17:21**. O portal não gera o
arquivo no momento do download — serve um pacote pré-gerado. Logo, "atualização diária"
significa que o *pacote* é regerado periodicamente, e a data interna do ZIP é o carimbo
real dos dados. Vale registrar essa data no manifesto para saber a que dia o snapshot
se refere, em vez de assumir a data do download.

---

## 8. Base 5 (Apoiamentos) — onde o dicionário erra

Verificado em 18/08/2026 no arquivo de 2022 (94 KB de ZIP → 1,5 MB de CSV, 2.670 linhas).
Encoding, separador, quoting e formato numérico são **idênticos** aos das outras bases.
Duas coisas, porém, contrariam o dicionário oficial:

### 8.1 São 31 colunas, não 27

As quatro que o dicionário não lista:

| # | coluna | por que importa |
|---|---|---|
| 15 | `Código do Autor da Emenda` | permite comparar autor formal × apoiador real **sem** juntar com a base 1 |
| 16 | `Nome do Autor da Emenda` | idem |
| 17 | `Número da emenda` | completa a identificação |
| 18 | `Localidade de aplicação do recurso` | análise territorial direto na base 5 |

Isso torna a base 5 bem mais autossuficiente do que a documentação sugere.

### 8.2 A chave `Empenho` tem 23 caracteres, não 22 — e a junção funciona

O dicionário descreve `Empenho` como UG (6) + Gestão (5) + Empenho (11) = 22. O campo real
tem **23**, com a mesma estrutura do `Código Documento` da base 2:

```
110594000012022NE000135
└─UG─┘└Gest┘└ano┘└┘└nº─┘
  6      5    4  2   6      = 23
```

Confirmação de que a junção **base 2 → base 5** é direta, sem tratamento:

| base | posições 15–17 | contagem |
|---|---|---|
| base 5, campo `Empenho` | `NE` em 100% das linhas | 2.670 |
| base 2, fase `Empenho` | `NE` em 100% das linhas | 91.514 |
| base 2, fase `Liquidação` | `NS` (134.350), `NL` (349) | — |
| base 2, fase `Pagamento` | `OB` (116.576), `DF` (15.136), `DR` (3.211), … | — |

`NE` = Nota de Empenho, `NS` = Nota de Sistema, `OB` = Ordem Bancária. Como a base 5 só
tem empenhos, ela casa exatamente com o subconjunto `fase_despesa == "Empenho"` da base 2.
No ETL o campo é renomeado para `codigo_documento` justamente para deixar isso explícito.

### 8.3 ⚠️ Duas convenções de data no mesmo arquivo

| coluna | formato | exemplo |
|---|---|---|
| `Data do Apoio` | **ISO com hora** | `2025-06-13 12:18:16` |
| `Data Retirada do Apoio` | **ISO com hora** | `2025-10-21 03:25:00` |
| `Data última movimentação Empenho` | `DD/MM/AAAA` | `15/06/2022` |

Um parser único com `format="%d/%m/%Y"` transformaria as duas primeiras colunas em nulo
**silenciosamente**. O `etl_emendas.py` tenta `DD/MM/AAAA` e, no que sobrar, tenta ISO8601.

### 8.4 ⚠️ `Data do Apoio` não é a data do ato político

No arquivo de **2022**, todas as 2.670 datas de apoio caem entre **março/2025 e julho/2026**
— 260 valores distintos, o mais frequente (`2025-03-06 09:00:00`, 1.023 linhas) com cara de
carga em lote. A `Data última movimentação Empenho`, essa sim, começa em 01/06/2022.

A leitura mais provável é que `Data do Apoio` seja o **carimbo de registro no sistema**, e
não a data em que o parlamentar apoiou a emenda — coerente com o fato de a publicação
desses dados ter começado depois da ADPF 854. **Não use esse campo como data do evento**
sem confirmar com a CGU ou com o Congresso. Para ordenar no tempo, `Data última
movimentação Empenho` é a referência confiável.

### 8.5 Outras observações do arquivo de 2022

- **Autor ≠ apoiador em 100% das linhas**, como esperado: 5 autores (todos coletivos —
  `RELATOR GERAL`, comissões) contra **201 apoiadores individuais**.
- `Tipo de Emenda` usa nomenclatura **diferente** das outras bases: aqui vem `RP9` (2.427
  linhas, relator) e `RP8` (243, comissão); nas bases 1–4 vem por extenso
  ("Emenda Individual - Transferências com Finalidade Definida"). Padronizar no ETL exige
  um de-para explícito.
- **503 apoios retirados** (18,8%) — `Data Retirada do Apoio` preenchida. Análise de
  autoria precisa decidir se conta apoio retirado.
- 61 empenhos (2,3%) com `Sem informação` na última movimentação.
- Arquivo pequeno: 2.670 linhas contra 362.091 da base 2 em um ano. A carga completa da
  base 5 deve levar segundos.

---

**Conclusão prática — a chamada correta:**

```python
import pandas as pd

df = pd.read_csv(
    caminho,
    sep=";",
    encoding="latin-1",
    decimal=",",              # sem thousands
    dtype=str,                # tipar depois; nunca deixar o pandas adivinhar códigos
    keep_default_na=False,    # "Sem informação" e "-1" tratados explicitamente
    na_values=[],
)
```

É exatamente o que o `script-scrap/etl/etl_emendas.py` faz.
