# Bases de Emendas Parlamentares — Portal da Transparência (CGU)

Documentação da estrutura, dos relacionamentos e dos grupos de variáveis das bases de
Emendas Parlamentares publicadas pelo Portal da Transparência da Controladoria-Geral da
União. Baseada nos dicionários de dados oficiais (versão do portal 6.4.9, consulta em
06/08/2026).

Arquivo companheiro: `../metadata/dicionario_emendas_parlamentares.xlsx` — uma aba por dicionário.

---

## 1. Visão geral das bases

| # | Base | Granularidade (1 linha = ) | Nº de variáveis | Pergunta que responde |
|---|------|---------------------------|-----------------|------------------------|
| 1 | **Por Emenda Parlamentar** | emenda × classificação orçamentária × localidade de destino | 28 | Quanto foi empenhado/liquidado/pago em cada emenda, em qual função e para qual município? |
| 2 | **Por Documentos de Despesa** | documento SIAFI (empenho, liquidação ou pagamento) | 48 | Qual o rastro contábil completo de cada emenda, documento a documento? |
| 3 | **Por Favorecido** | emenda × favorecido × ano/mês | 13 | Quem recebeu o dinheiro da emenda e quanto? |
| 4 | **Convênios** | convênio | 12 | Quais convênios foram firmados a partir da emenda, com quem e para quê? |
| 5 | **Apoiamentos** | apoiador × empenho | 27 | Quais parlamentares apoiaram/solicitaram cada empenho? |

As bases 1–4 formam o **núcleo da execução da emenda**, do agregado (1) ao detalhe
contábil (2) e ao destino final do recurso (3 e 4). A base 5 é um **anexo de autoria
compartilhada**: ela existe porque emendas de bancada, de comissão e de relator podem ter
vários parlamentares "apoiando" um mesmo empenho — informação que não cabe no campo único
de autor das demais bases.

---

## 2. Como as bases se relacionam

```
                        ┌─────────────────────────────────────┐
                        │  1. POR EMENDA PARLAMENTAR          │
                        │  PK: Código da Emenda (12 díg.)     │
                        │  + classificação + localidade       │
                        └──────────────┬──────────────────────┘
                                       │ Código da Emenda
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
   ┌──────────▼──────────┐  ┌──────────▼──────────┐  ┌──────────▼──────────┐
   │ 2. POR DOCUMENTO    │  │ 3. POR FAVORECIDO   │  │ 4. CONVÊNIOS        │
   │    DE DESPESA       │  │ Código da Emenda +  │  │ PK: Número Convênio │
   │ PK: Código Documento│  │ Cód. Favorecido +   │  │                     │
   │ Fase: emp/liq/pgto  │  │ Ano/Mês             │  │                     │
   └──────────┬──────────┘  └─────────────────────┘  └─────────────────────┘
              │ Empenho (UG 6 + Gestão 5 + Empenho 11)
   ┌──────────▼──────────┐
   │ 5. APOIAMENTOS      │
   │ Empenho + Cód.      │
   │ Apoiador            │
   └─────────────────────┘
```

### Chaves de junção

| Ligação | Chave | Cardinalidade | Observações |
|---------|-------|---------------|-------------|
| 1 → 2 | `Código da Emenda` | 1:N | Uma emenda gera muitos documentos de despesa. Some por `Fase da Despesa` para reconciliar com os valores da base 1. |
| 1 → 3 | `Código da Emenda` | 1:N | A base 3 já vem agregada por favorecido e mês. |
| 1 → 4 | `Código da Emenda` | 1:N | Só existe linha quando a execução se deu via convênio. Cheque `Possui convênio?` na base 2. |
| 2 → 5 | `Empenho` (base 5) ≈ `Código Documento` (base 2, fase = empenho) | 1:N | O apoiamento só se aplica a empenhos, não a liquidações/pagamentos. Um mesmo empenho pode ter vários apoiadores. |
| 2 → 3 | `Código da Emenda` + `Código Favorecido` | N:N | Ligação indireta; a base 3 é uma agregação dos pagamentos da base 2. |
| 1/2/3 → autor | `Código do Autor da Emenda` | N:1 | Código SIAFI do parlamentar/bancada. |
| 5 → apoiador | `Código Apoiador` | N:1 | Mesmo domínio do código de autor (parlamentar no SIAFI). |

### Anatomia das chaves

- **Código da Emenda — 12 dígitos:** `AAAA` (ano) + `CCCC` (código do autor) + `NNNN`
  (número da emenda daquele autor). Ou seja, a chave já contém `Ano da Emenda`,
  `Código do Autor` e `Número da Emenda` — os três aparecem redundantemente como colunas.
  Trate sempre como **texto**: o zero à esquerda é significativo.
- **Empenho — 22 dígitos:** `UG` (6) + `Gestão` (5) + `Empenho` (11). O prefixo permite
  recuperar a Unidade Gestora mesmo sem juntar com a base 2.
- **Código do Favorecido:** CPF/CNPJ ou código SIAFI do ente. Também deve ser lido como
  texto.

### Armadilhas ao juntar as bases

1. **Não some valores após um join 1:N.** Juntar a base 1 (agregada) com a base 2
   (documento a documento) e somar `Valor Empenhado` duplica o total. Agregue primeiro,
   junte depois.
2. **`Valor Empenhado` e `Valor Pago` na base 2 são mutuamente exclusivos por linha:**
   o empenhado só é válido quando `Fase da Despesa = empenho`; o pago, quando
   `Fase da Despesa = pagamento`. Filtre pela fase antes de somar.
3. **Localidade tem duas definições diferentes.** A base 1 usa a *localidade do gasto*
   (regionalização do Plano de Trabalho); a base 2 usa a *localidade de aplicação do
   recurso*, que em certas modalidades de aplicação (30, 31, 32, 40, 41 combinadas com
   naturezas jurídicas específicas) passa a ser a **localidade do favorecido**. Não são
   intercambiáveis para análise territorial.
4. **Município/UF podem vir em branco** na base 1 quando a emenda é de abrangência
   nacional ou estadual.
5. **Restos a pagar só existem na base 1.** Se a análise for de execução plurianual, ela
   precisa começar por essa base.
6. **Nomes de campo variam entre as bases** para o mesmo conceito (`Nome Função` vs.
   `Função`, `Municipio Favorecido` sem acento na base 5). Padronize no ETL.

---

## 3. Grupos de variáveis

As colunas das cinco bases se distribuem em dez blocos temáticos. A tabela abaixo mostra
em quais bases cada bloco aparece.

| Grupo | Conteúdo | B1 | B2 | B3 | B4 | B5 |
|-------|----------|:--:|:--:|:--:|:--:|:--:|
| **Identificação da emenda** | Código da Emenda, Ano, Número, Tipo de Emenda | ✔ | ✔ | ✔ | ✔ | ✔ |
| **Autoria** | Código e Nome do Autor; Código e Nome do Apoiador | ✔ | ✔ | ✔ | — | ✔ |
| **Localidade** | Localidade do gasto / de aplicação, Município, UF, Região, códigos IBGE | ✔ | ✔ | — | ✔ | — |
| **Classificação orçamentária** | Função, Subfunção, Programa, Ação, Plano Orçamentário, Subtítulo, Grupo/Elemento/Modalidade de Despesa, Linguagem Cidadã | ✔ | ✔ | — | ✔ | ✔ |
| **Unidade executora** | UG, Unidade Orçamentária, Órgão, Órgão Superior (códigos SIAFI + nomes) | — | ✔ | — | — | ✔ |
| **Favorecido** | Código, Nome, Tipo, Natureza Jurídica, UF e Município do favorecido | — | ✔ | ✔ | — | ✔ |
| **Documento de despesa** | Código Documento / Empenho, Fase da Despesa | — | ✔ | — | — | ✔ |
| **Convênio** | Convenente, Objeto, Número, Data de publicação, "Possui convênio?" | — | ✔ | — | ✔ | — |
| **Valores** | Empenhado, Liquidado, Pago, Cancelado, Restos a pagar, Valor Recebido, Valor Convênio | ✔ | ✔ | ✔ | ✔ | ✔ |
| **Datas** | Data do Documento, Data do Apoio, Data de Retirada, Última movimentação, Ano/Mês | — | ✔ | ✔ | ✔ | ✔ |

### Detalhamento dos grupos

**Identificação da emenda.** Núcleo comum a todas as bases. `Tipo de Emenda` é a variável
de segmentação mais usada (individual, de bancada, de comissão, de relator) e determina
regras distintas de obrigatoriedade de execução.

**Autoria.** `Código do Autor da Emenda` é o identificador SIAFI do parlamentar ou da
bancada. Em emendas coletivas, o "autor" é a bancada/comissão — daí a existência da base
de Apoiamentos, que revela os parlamentares individuais por trás de cada empenho.

**Localidade.** Três recortes possíveis: destino do gasto (base 1), aplicação do recurso
(base 2) e sede do favorecido (bases 2, 3 e 5). Escolher o recorte errado é a fonte mais
comum de erro em análises municipais.

**Classificação orçamentária.** Hierarquia funcional-programática:
`Função → Subfunção → Programa → Ação → Plano Orçamentário → Subtítulo (Localizador)`.
A classificação da despesa em si (`Grupo → Modalidade de Aplicação → Elemento`) só está
completa na base 2, e é o que distingue investimento de custeio e execução direta de
transferência a entes subnacionais.

**Unidade executora.** Hierarquia administrativa:
`Órgão Superior → Órgão → Unidade Orçamentária → Unidade Gestora (UG)`.
Presente apenas nas bases de documento (2 e 5).

**Favorecido.** Quem efetivamente recebe. `Natureza Jurídica` (só na base 3) é o campo que
permite separar prefeituras, fundos públicos, OSCs e empresas.

**Valores.** Percurso da despesa: `Empenhado → Liquidado → Pago`, com o resíduo em
`Restos a Pagar Inscritos → Cancelados → Pagos`. Cada base expõe um subconjunto:

| Base | Empenhado | Liquidado | Pago | Cancelado | Restos a pagar | Outros |
|------|:---------:|:---------:|:----:|:---------:|:--------------:|--------|
| 1. Por Emenda | ✔ | ✔ | ✔ | — | ✔ (3 campos) | — |
| 2. Por Documento | ✔ (só fase empenho) | — | ✔ (só fase pagamento) | — | — | — |
| 3. Por Favorecido | — | — | — | — | — | Valor Recebido |
| 4. Convênios | — | — | — | — | — | Valor Convênio |
| 5. Apoiamentos | ✔ | — | ✔ | ✔ | — | — |

---

## 4. Escolhendo a base certa

| Se a pergunta é… | Use |
|------------------|-----|
| Quanto cada parlamentar destinou e executou por ano/função/município | Base 1 |
| Onde o dinheiro parou — CNPJ a CNPJ | Base 3 (agregado) ou Base 2 (documento a documento) |
| Rastrear o percurso contábil de um empenho específico | Base 2 |
| Investigar concentração de recursos em poucos favorecidos | Base 3 |
| Analisar transferências voluntárias e seus objetos | Base 4 |
| Descobrir a autoria real de emendas coletivas / "emendas de relator" | Base 5 cruzada com a Base 2 |
| Medir execução plurianual (restos a pagar) | Base 1 |

---

## 5. Notas de tratamento (ETL)

- Ler **todos os códigos como texto** (emenda, favorecido, IBGE, SIAFI, empenho) para
  preservar zeros à esquerda.
- Valores monetários vêm com **vírgula decimal** e possivelmente ponto de milhar nos CSVs
  do portal; converter explicitamente.
- Datas em formato `DD/MM/AAAA`; `Ano/Mês` na base 3 costuma vir como `AAAAMM`.
- Os arquivos são publicados **por ano**; concatene antes de analisar séries temporais e
  atente para mudanças de layout entre exercícios (ex.: a regra de `Data Documento` para
  empenhos mudou a partir de 2021).
- Verifique duplicidades de `Código Documento` ao concatenar anos — um empenho de um ano
  pode reaparecer em arquivos de anos seguintes por movimentações posteriores.

---

**Fonte:** Portal da Transparência — Controladoria-Geral da União.
Dicionários de Dados: *Emendas Parlamentares*, *Emendas Parlamentares por Documentos de
Despesa* e *Apoiamentos de Emendas Parlamentares*.
Definições de Função, Subfunção, Programa, Ação, Plano Orçamentário, Grupo e Elemento de
Despesa: Manual Técnico do Orçamento (SOF); definições de Órgão e Órgão Superior: Manual
do SIAFI.
