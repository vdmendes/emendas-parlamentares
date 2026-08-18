# Comandos

```bash
cd /datalake/financas_publicas/emendas-parlamentares/database_portal
source .venv/bin/activate
cd script-scrap
```

## Rotina normal

```bash
python3 emendas_transparencia.py --simular            # o que falta baixar
python3 emendas_transparencia.py                      # baixa (abre Chromium)
python3 etl/etl_emendas.py                            # só o novo vira parquet
```

Baixar abre uma janela do Chromium. Não mexa nela — o cookie do WAF vive ali.

## Atualizar

```bash
python3 emendas_transparencia.py --datasets emendas --forcar    # nova competência
python3 emendas_transparencia.py --anos 2025:2026 --forcar      # anos recentes
python3 etl/etl_emendas.py
```

## Baixar pedaços

```bash
python3 emendas_transparencia.py --datasets emendas                    # snapshot
python3 emendas_transparencia.py --datasets documentos --anos 2014:2026
python3 emendas_transparencia.py --datasets apoiamento --anos 2020:2026
python3 emendas_transparencia.py --anos 2024 -v                        # log detalhado
python3 emendas_transparencia.py --espera-desafio 10                   # se der erro (WAF)
```

## ETL

```bash
python3 etl/etl_emendas.py --listar            # o que seria processado
python3 etl/etl_emendas.py --bases documento   # uma base só
python3 etl/etl_emendas.py --forcar            # reprocessa tudo (~2min20)
```

Bases: `emenda`, `documento`, `favorecido`, `convenios`, `apoiamento`

## Testes

```bash
python3 test_emendas.py                        # 63 checagens, sem rede
```

## Ler os dados

```python
import pandas as pd

pd.read_parquet("processed/emenda")                        # base inteira
pd.read_parquet("processed/documento/documento_2024.parquet")   # um ano
```

## Onde está o quê

```
database_portal/
├── raw/                     ZIPs e CSVs do portal, como vieram
├── processed/               parquet, um arquivo por ano
│   └── _historico/          competências anteriores do download único
├── script-scrap/            os scripts
│   ├── COMANDOS.md          este arquivo
│   ├── emendas_transparencia.py   baixa      (+ README.md)
│   ├── test_emendas.py
│   ├── build_dic.py         gera o dicionário .xlsx
│   └── etl/etl_emendas.py   transforma (+ README.md)
├── documents/               documentação
│   ├── CARGA.md                                 roteiro da carga completa
│   ├── portaltransparenciaemendasestrutura.md   engenharia reversa do portal
│   ├── estrutura_bases_emendas_parlamentares.md modelo relacional das 5 bases
│   ├── formato-arquivos-portal.md               encoding, separador, formatos
│   └── prints/                                  dicionários oficiais em imagem
└── metadata/                dicionários e metadados
    └── dicionario_emendas_parlamentares.xlsx    uma aba por base
```

Os scripts acham `raw/` e `processed/` sozinhos, subindo a árvore — não importa de onde
você os chame.

## Virada de ano

Não precisa mexer em nada. O período padrão vai até o ano corrente, então em 2027 o teto
vira 2027 sozinho. Os anos reais vêm do `<select>` da página do portal, e se algum dia ele
publicar um exercício adiantado o log avisa:

```
WARNING Portal oferece alem do periodo pedido: 2027 -- use --anos para incluir
```

## Três coisas para não esquecer

1. **`ano` não é a mesma coisa em todas as bases.** Em `emenda`, `convenios` e
   `apoiamento` é o ano da emenda; em `documento` e `favorecido` é o ano da execução.
   Somar `emenda_2024` com `favorecido_2024` mistura perguntas diferentes.

2. **Na base `apoiamento`, o valor repete por apoiador.** Somar direto infla até 2,5×.
   Use `drop_duplicates("codigo_documento")` para totais.

3. **Na base `documento`, filtre por `fase_despesa` antes de somar** — `valor_empenhado` e
   `valor_pago` são mutuamente exclusivos por linha.

Detalhes em `etl/README.md` e em `../documents/`. Dicionários em `../metadata/`.
