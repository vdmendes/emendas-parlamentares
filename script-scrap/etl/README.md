# ETL — dos CSVs crus ao parquet

Transforma o que o `script-scrap/emendas_transparencia.py` baixou em parquet particionado
por ano, pronto para análise.

```
raw/<dataset>/csv*/**.csv   →   processed/<base>/<base>_AAAA.parquet
```

```bash
pip install pandas pyarrow

python3 etl_emendas.py --listar     # mostra o que seria processado
python3 etl_emendas.py              # incremental: só o que é novo ou mudou
python3 etl_emendas.py --forcar     # reprocessa tudo
```

Mesma resolução de caminhos do scraper: `--raw` / `--destino` → `EMENDAS_RAIZ` /
`EMENDAS_PROCESSED` → `database_portal/{raw,processed}`.

## Incremental por padrão

O ETL guarda no `_etl.json` o tamanho e o `mtime` de cada CSV que processou, junto com a
lista de parquets que gerou. Numa nova execução, ele só reprocessa o que:

- **é novo** — competência ou exercício que ainda não estava lá;
- **mudou** — o portal republicou o arquivo (tamanho ou data diferente);
- **perdeu a saída** — o parquet correspondente sumiu do disco.

```
Encontrados 23 arquivo(s): 22 ja processado(s), 1 a processar
  + emenda         EmendasParlamentares.csv   45.0 MB  20260901  (novo)
```

Não usa `sha256`: seriam vários segundos por arquivo de 1 GB a cada execução, justamente o
custo que o modo incremental existe para evitar. O portal republica o arquivo inteiro a cada
atualização, então tamanho + mtime detectam qualquer mudança real.

O manifesto é gravado **a cada arquivo**, não no fim. Se a execução morrer no meio da leva
pesada, a próxima retoma de onde parou.

## Estrutura de `processed/`

Uma pasta por base, **um arquivo por ano**, sem subpastas:

```
processed/
├── emenda/       emenda_2014.parquet … emenda_2026.parquet          13 arquivos
├── documento/    documento_2014.parquet … documento_2026.parquet    13
├── favorecido/   favorecido_2014.parquet … favorecido_2026.parquet  13
├── convenios/    convenios_0000.parquet, convenios_2015.parquet …   13
├── apoiamento/   apoiamento_2020.parquet … apoiamento_2026.parquet   7
├── _historico/   competências superadas (ver abaixo)
└── _etl.json     manifesto do ETL
```

| pasta | origem | granularidade | o `ano` é o da… |
|---|---|---|---|
| `emenda` | `csv_AAAAMMDD/EmendasParlamentares.csv` | emenda × classificação × localidade | emenda |
| `favorecido` | `csv_AAAAMMDD/…_PorFavorecido.csv` | emenda × favorecido × ano/mês | execução |
| `convenios` | `csv_AAAAMMDD/…_Convenios.csv` | convênio | emenda |
| `documento` | `csv/{ano}/{ano}_…_PorDocumento.csv` | documento SIAFI | execução |
| `apoiamento` | `csv/{ano}/{ano}_Apoiamento….csv` | apoiador × empenho | emenda |

O ano é **coluna** (`ano`, tipo `Int64`), não pasta. `convenios_0000.parquet` guarda as
9.190 linhas sem código de emenda, das quais não se consegue derivar ano.

### Competências: `_historico/`

As bases `emenda`, `favorecido` e `convenios` vêm do **download único**, que não tem filtro
de ano — o portal publica só o estado atual, sem histórico versionado. Cada download é uma
fotografia inteira, uma **competência**.

Regra: a competência **mais recente** encontrada em `raw/` ocupa `processed/<base>/`; as
anteriores vão para `_historico/`.

```
processed/
├── emenda/                              ← sempre o download mais recente
│   └── emenda_2014.parquet … 2026
└── _historico/emenda/20260801/          ← competência de agosto, depois que
    └── emenda_2014.parquet … 2026          chegar a de setembro
```

A regra é derivada do que está em `raw/`, não de estado guardado: quando chega
`csv_20260901`, a competência de agosto migra sozinha para o histórico na execução
seguinte. Você não move arquivo nenhum na mão.

Cada arquivo dessas três bases carrega uma coluna `competencia` (`20260801`), então continua
se explicando mesmo fora da pasta.

`documento` e `apoiamento` são **arquivos anuais**: o portal republica o exercício inteiro e
a versão nova legitimamente substitui a anterior. Não têm competência nem histórico.

### ⚠️ Por que os anos antigos são reescritos a cada competência

Seria tentador atualizar só o ano corrente e deixar 2014–2025 congelados. Os dados dizem que
isso daria errado:

- **36,3%** dos documentos emitidos em 2026 são de emendas de anos anteriores — **R$ 8,40 bi**
  pagos em 2026 para emendas de 2015 a 2025;
- na base `emenda`, **57.799 linhas** de 2014–2025 já têm restos a pagar pagos.

Uma emenda de 2020 continua recebendo pagamento em 2026, e o snapshot novo traz esses valores
atualizados na linha de 2020. Congelar anos antigos os faria divergir da realidade em
silêncio — que é o pior modo de errar. Por isso a competência nova reescreve os 13 arquivos,
e a anterior fica inteira no histórico para comparação.

### ⚠️ `ano` não significa a mesma coisa nas cinco bases

Esta é a armadilha mais fácil de cair e a mais difícil de perceber depois.

| base | `ano` é o ano da… | de onde sai |
|---|---|---|
| `emenda` | **emenda** (quando foi proposta) | coluna `ano_emenda` |
| `convenios` | **emenda** | `codigo_emenda[:4]` |
| `apoiamento` | **emenda** | prefixo do arquivo |
| `documento` | **execução** (data do documento) | prefixo do arquivo |
| `favorecido` | **execução** (mês do lançamento) | `ano_mes[:4]` |

Não é escolha do ETL: é como o portal publica. `emenda`, `convenios` e `apoiamento` são
organizadas pela emenda; `documento` e `favorecido`, pelo fluxo de caixa. Forçar
uniformidade aqui seria inventar informação que o dado não tem.

**Consequência: 2024 em bases diferentes não é o mesmo recorte.** Em
`favorecido_2024.parquet` estão os pagamentos feitos em 2024 — de emendas de 2024
(36.447 linhas), mas também de 2023 (23.301), 2022 (3.735), 2021 (3.079), 2020 (1.994) e
2019 (1.123). É dinheiro de emenda antiga saindo em 2024.

Somar `emenda_2024` com `favorecido_2024` mistura "emendas propostas em 2024" com
"pagamentos feitos em 2024". São perguntas diferentes.

Exemplo concreto da mesma emenda em dois arquivos — `202038950002`, do deputado Abilio
Santana, proposta em 2020:

| arquivo | o que aparece |
|---|---|
| `emenda/emenda_2020.parquet` | R$ 500.000 empenhados · R$ 0 pagos · R$ 500.000 de restos a pagar pagos |
| `documento/documento_2020.parquet` | 1 empenho + 1 liquidação |
| `documento/documento_2022.parquet` | 1 liquidação + 1 pagamento |

O pagamento saiu em 2022, mas em `emenda` ele está contabilizado na linha de 2020.

### O download único é acumulado, não anual

`emenda`, `favorecido` e `convenios` vêm de um único CSV sem filtro de ano
(`emendas-parlamentares/UNICO`). O ETL **reparte**, não filtra: nenhuma linha é descartada.
Ler a pasta inteira devolve o CSV original completo — 94.304 linhas de `emenda`,
distribuídas em 13 arquivos.

```python
# um ano: um arquivo
pd.read_parquet("processed/emenda/emenda_2024.parquet")     # 6.990 linhas

# a base inteira: a pasta
pd.read_parquet("processed/emenda")                         # 94.304, com coluna 'ano'

# um recorte de anos, sem ler o resto do disco
from pathlib import Path
anos = range(2018, 2027)
pd.concat(pd.read_parquet(f"processed/documento/documento_{a}.parquet") for a in anos)
```

Como o ano virou nome de arquivo, o recorte temporal é feito escolhendo arquivos — mais
direto que `filters=` e sem a pegadinha de tipo da partição Hive.

```python
# os 9.190 convênios sem código de emenda
pd.read_parquet("processed/convenios/convenios_0000.parquet")
```

Os **valores** de `emenda`, `favorecido` e `convenios` são acumulados até a data da
competência, não do ano do arquivo. A emenda do exemplo acima mostra restos a pagar pagos em
2022 dentro da linha de 2020. Para reconstruir como esses acumulados evoluíram, compare
competências:

```python
atual = pd.read_parquet("processed/emenda/emenda_2020.parquet",
                        columns=["codigo_emenda","valor_pago","competencia"])
antes = pd.read_parquet("processed/_historico/emenda/20260801/emenda_2020.parquet",
                        columns=["codigo_emenda","valor_pago"])
evolucao = atual.merge(antes, on="codigo_emenda", suffixes=("_novo","_antigo"))
evolucao["variacao"] = evolucao.valor_pago_novo - evolucao.valor_pago_antigo
```

## O que o ETL faz

**Códigos viram texto.** Tudo que casa com `^codigo`, `_ibge$`, `^numero`, `^empenho$`,
`^ano_mes$` fica `string`. Ler como número destrói a chave — o código da emenda tem 12
dígitos e o do favorecido é CPF/CNPJ.

**Valores viram `Float64`.** Decimal vírgula, **sem** ponto de milhar (verificado nos
arquivos: `-161998,38`, `1000000,00`). Negativos são cancelamentos e são preservados.

**Datas viram `datetime64`,** a partir de `DD/MM/AAAA`.

**Nomes de coluna viram snake_case sem acento,** e um mapa de sinônimos unifica os conceitos
que o portal nomeia de formas diferentes entre as bases:

| no portal | no parquet |
|---|---|
| `Nome Função` / `Função` | `funcao` |
| `Número da emenda` | `numero_emenda` |
| `Tipo de Emenda` / `Tipo da Emenda` | `tipo_emenda` |
| `Localidade de aplicação do recurso` | `localidade_aplicacao` |
| `Localidade do gasto` | `localidade_gasto` |
| `Código IBGE do município de aplicação do recurso` | `codigo_ibge_aplicacao` |

`localidade_aplicacao` e `localidade_gasto` **continuam separadas de propósito** — são
definições diferentes, não sinônimos (ver §3 do documento de estrutura das bases).

**`Sem informação`, string vazia e `-1` viram nulo.** São os sentinelas do portal.

**Leitura em blocos de 200 mil linhas,** para o pico de memória não acompanhar o tamanho do
arquivo (o CSV de 2024 tem 314 MB).

O ETL **não junta nem agrega** — só limpa e tipa. As regras de junção 1:N ficam para a
análise, onde precisam ser decididas caso a caso.

## Resultado da carga completa (18/08/2026)

4,1 GB de CSV → **127 MB de parquet**, em 2min20.

| base | linhas | arquivos | anos | sem `codigo_emenda` |
|---|---:|---:|---|---:|
| `emenda` | 94.304 | 13 | 2014–2026 | 17.810 (18,9%) |
| `documento` | 4.700.200 | 13 | 2014–2026 | 439.024 (9,3%) |
| `favorecido` | 816.789 | 13 | 2014–2026 | 66.631 (8,2%) |
| `convenios` | 84.877 | 13 | 2015–2026 + `0000` | 9.190 (10,8%) |
| `apoiamento` | 98.847 | 7 | 2020–2026 | 0 |
| **total** | **5.795.017** | **59** | | |

Na base `documento`, a ausência de código da emenda é fortemente concentrada no início da série —
**100% em 2014**, 57,8% em 2015, 47,8% em 2017, e cai para 0,0% em 2026. O campo passou a
ser preenchido de forma confiável a partir de 2018. Análises que dependam de junção com a
`emenda` devem começar em 2018, ou declarar a perda.

⚠️ **As linhas sem `codigo_emenda` são um achado, não um bug do ETL.** O portal grava
`"Sem informação"` no campo — 18,9% de `emenda`, concentradas nos anos iniciais. Essas linhas
não participam de nenhum join entre as bases. Qualquer análise que parta de uma junção
precisa reportar essa perda; qualquer total que use só uma base deve incluí-las.

## Lendo o resultado

```python
import pandas as pd

pd.read_parquet("processed/emenda")                       # base inteira
pd.read_parquet("processed/emenda/emenda_2024.parquet")   # um ano
```

Lembretes de análise, do documento de estrutura das bases:

- Na base `documento`, `valor_empenhado` e `valor_pago` são **mutuamente exclusivos por linha**.
  Filtre por `fase_despesa` antes de somar.
- Não some depois de um join 1:N. Agregue primeiro, junte depois.
- A base `emenda` é a única com restos a pagar — execução plurianual começa por ela.

### ⚠️ A armadilha do apoiamento: o valor repete por apoiador

Na base `apoiamento`, **o valor do empenho não é rateado entre os apoiadores** — ele
aparece integral em cada linha. Verificado: nos 46.609 empenhos distintos, todas as linhas
de um mesmo empenho têm exatamente o mesmo `valor_empenhado`. São 2,1 apoiadores por
empenho em média, chegando a **108** no extremo.

Somar a coluna direto infla o total de 1,3× a 2,5×, dependendo do ano:

| ano | linhas | empenhos | soma ingênua | correto | inflação |
|---|---:|---:|---:|---:|---:|
| 2020 | 7.123 | 3.880 | R$ 7,12 bi | R$ 3,72 bi | 1,9× |
| 2021 | 9.377 | 4.715 | R$ 8,77 bi | R$ 4,08 bi | 2,1× |
| 2022 | 2.670 | 1.309 | R$ 2,17 bi | R$ 1,04 bi | 2,1× |
| 2023 | 4.028 | 2.782 | R$ 7,96 bi | R$ 5,56 bi | 1,4× |
| 2024 | 14.521 | 12.642 | R$ 14,91 bi | R$ 11,28 bi | 1,3× |
| 2025 | 48.535 | 17.922 | R$ 27,66 bi | R$ 11,26 bi | 2,5× |
| 2026 | 12.593 | 3.359 | R$ 5,13 bi | R$ 2,28 bi | 2,3× |
| **total** | **98.847** | **46.609** | **R$ 73,72 bi** | **R$ 39,22 bi** | **1,9×** |

```python
# ERRADO — conta o mesmo empenho uma vez por apoiador
b5.valor_empenhado.sum()

# CERTO — total empenhado
b5.drop_duplicates("codigo_documento").valor_empenhado.sum()

# CERTO — quanto cada parlamentar apoiou (aí a repetição é o que se quer)
b5.groupby("apoiador").valor_empenhado.sum()
```

A terceira forma é legítima e não soma R$ 39,22 bi: um empenho apoiado por 10 parlamentares
conta inteiro para cada um. Isso é uma escolha de atribuição, não um erro — mas precisa ser
declarada, porque a soma das partes excede o todo.
