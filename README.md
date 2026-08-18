# Emendas Parlamentares — Portal da Transparência

Pipeline que baixa, valida e estrutura os dados abertos de Emendas Parlamentares da CGU.
**5.795.017 linhas** em cinco bases, 2014–2026 — 4,4 GB de CSV bruto em 135 MB de parquet.

```bash
cd script-scrap && source ../.venv/bin/activate
python3 emendas_transparencia.py      # baixa (abre Chromium)
python3 etl/etl_emendas.py            # só o novo vira parquet
```

| onde | o quê |
|---|---|
| **[`script-scrap/COMANDOS.md`](script-scrap/COMANDOS.md)** | os comandos — comece aqui ao retomar |
| **[`documents/PROJETO.md`](documents/PROJETO.md)** | visão geral, arquitetura, decisões e achados |
| [`script-scrap/etl/README.md`](script-scrap/etl/README.md) | armadilhas de análise — leia antes de somar qualquer coisa |
| [`script-scrap/README.md`](script-scrap/README.md) | como o scraper contorna o WAF |
| [`documents/`](documents/) | documentação detalhada |
| [`metadata/`](metadata/) | dicionários de dados |

```
raw/          como veio do portal, intocado
processed/    parquet tipado, um arquivo por ano
```

Três coisas que geram número errado **sem dar erro** — detalhadas em `documents/PROJETO.md` §5.4:

1. `ano` é o ano da **emenda** em `emenda`/`convenios`/`apoiamento`, e o da **execução** em
   `documento`/`favorecido`. Não são o mesmo recorte.
2. Em `apoiamento`, o valor repete por apoiador. Somar direto infla até 2,5×.
3. Em `documento`, filtre por `fase_despesa` antes de somar.
