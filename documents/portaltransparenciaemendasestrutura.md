# Portal da Transparência — Emendas Parlamentares
## Documento de suporte técnico para scraping e download de dados

> **Uso pretendido:** arquivo de contexto para projeto no Claude Cowork.
> **Data do levantamento:** 13/08/2026
> **Método:** inspeção direta do DOM ao vivo (Chrome), leitura do JS de download e dos dicionários de dados oficiais da CGU.

---

## 0. Sumário executivo

| # | Dataset | URL da página | Modo | Parâmetro | Atualização |
|---|---------|---------------|------|-----------|-------------|
| 1 | Emendas Parlamentares | `/download-de-dados/emendas-parlamentares` | `UNICO` | nenhum (`UNICO`) | Diária |
| 2 | Emendas parlamentares por Documentos de Despesa | `/download-de-dados/emendas-parlamentares-documentos` | `ANO` | ano (2014–2026) | Diária |
| 3 | Apoiamento emendas parlamentares | `/download-de-dados/apoiamento-emendas-parlamentares-documentos` | `ANO` | ano (2020–2026) | Mensal¹ |

¹ O índice `/download-de-dados` informa **mensal**; o texto da própria página diz *"Esses dados serão atualizados semanalmente"*. Divergência da fonte — tratar como "no máximo semanal" e validar por hash/tamanho do arquivo.

**Host:** `https://portaldatransparencia.gov.br`
**Padrão de URL de download:** `https://portaldatransparencia.gov.br/download-de-dados/{modulo}/{parametro}`

```
/download-de-dados/emendas-parlamentares/UNICO
/download-de-dados/emendas-parlamentares-documentos/2026
/download-de-dados/apoiamento-emendas-parlamentares-documentos/2026
```

**⚠️ Ponto crítico de arquitetura:** o site está atrás de **AWS WAF** (bot control). Requisições `fetch`/`XHR`/`curl` diretas ao endpoint de download falham. Ver §5.

---

## 1. Template compartilhado das três páginas

As três páginas usam **exatamente o mesmo template** (`download-planilhas`). Só mudam: o texto, o `modoApresentacao` e o array `arquivos`.

### 1.1 Esqueleto do documento

```
html
└── body
    └── main#main
        └── div.template-base
            ├── nav.br-skiplink                       ← atalhos de acessibilidade (ignorar)
            ├── header#header.br-header               ← cabeçalho gov.br (ignorar)
            ├── div#main-navigation.br-menu           ← menu lateral (ignorar)
            ├── nav#box-identificacao-breadcrumbs.br-breadcrumb
            │   └── section.box-identificacao.mb-4
            │       ├── div.container.mb-4
            │       │   └── nav.br-breadcrumb
            │       │       └── ol.crumb-list
            │       │           ├── li.crumb.home  > a  "Início"
            │       │           ├── li.crumb       > a  "Dados Abertos"  [href=/download-de-dados]
            │       │           └── li.crumb       > span "Planilhas"
            │       └── div.container
            │           └── div.row
            │               ├── div.col-md-8.col-sm-12.col-xs-12
            │               │   └── h2            ← TÍTULO DO DATASET
            │               ├── div#divButtonHeader.col-md-2.col-sm-6.col-xs-6
            │               └── div.col-md-2.col-sm-6.col-xs-6
            └── div#main-content                      ← ÁREA ÚTIL
                ├── div#aconteudo.container
                │   └── div.row > div.col-xs-12 > div#alerts.floating-alerts
                └── div.container
                    └── div.row
                        └── div.col-sm-12.col-md-8
                            ├── p                      ← instrução ("Selecione o ano...")
                            ├── p > span > a           ← link "dicionário de dados"
                            ├── div#arquivo-unico.box-dados-ano
                            │   ├── p.download-title            "Dados disponíveis"
                            │   └── ul#link-unico
                            │       └── li > a.br-button.primary  ← LINK DE DOWNLOAD (modo UNICO)
                            ├── div#origens.box-dados-ano
                            │   ├── p.download-title            "Origens de dados disponíveis"
                            │   └── select#links-origens
                            ├── div#anos.box-dados-ano
                            │   ├── p.download-title            "Exercícios Disponíveis"
                            │   ├── select#links-anos           ← FILTRO DE ANO
                            │   └── a#link.a-no-css > button#btn.br-button.primary "Baixar"   (injetado por JS)
                            ├── div#meses.box-dados-ano
                            │   ├── p#titulo-meses.download-title "Meses Disponíveis em {ano}"
                            │   └── select#links-meses
                            ├── div#dias.box-dados-ano
                            │   ├── p#titulo-dias.download-title  "Dados Disponíveis em {mês}"
                            │   └── select#links-dias
                            └── div#origens-mes.box-dados-ano
                                ├── p#titulo-origens-mes.download-title "Tipos de planilhas disponíveis em {mês}"
                                └── select#links-origens-mes
```

### 1.2 Seletores CSS canônicos

| Alvo | Seletor | Observação |
|------|---------|------------|
| Título do dataset | `section.box-identificacao h2` | |
| Área de conteúdo | `#main-content` | |
| Texto descritivo | `#main-content .col-md-8 > p` | 1..N parágrafos |
| Link do dicionário de dados | `#main-content a[href*="dicionario-de-dados"]` | |
| Caixa "arquivo único" | `#arquivo-unico` | `display:none` se não aplicável |
| Link de download único | `#link-unico a.br-button.primary` | `href` já é a URL final |
| Select de origens | `select#links-origens` | |
| Select de anos | `select#links-anos` | `option[value]` = ano (4 díg.) |
| Select de meses | `select#links-meses` | `value` = `01`..`12`; texto = nome por extenso |
| Select de dias | `select#links-dias` | `value` = `01`..`31` |
| Select de origens/mês | `select#links-origens-mes` | |
| Botão "Baixar" (gerado por JS) | `a#link` / `button#btn` | ler o `href` de `a#link` |

**Regra de ouro:** as 6 caixas `.box-dados-ano` **sempre existem** no HTML. O que muda é `display` (`none`/`block`), controlado por `slideDown()`/`slideUp()` do jQuery. Detecte o modo por `getComputedStyle(el).display !== 'none'`, **não** pela presença do elemento.

### 1.3 Detecção do modo sem executar JS

O script inline no final da página (o único que contém `new DownloadPlanilhas`) carrega tudo:

```javascript
var arquivos = [];
arquivos.push({"ano":"2014","mes":"","dia":"","origem":"EmendasParlamentaresPorDocumento"});
/* ... */
var url = springUrl + "download-de-dados/emendas-parlamentares-documentos/";
var download = new DownloadPlanilhas("emendas-parlamentares-documentos", arquivos, "ANO", url);
download.criarLinksIniciais();
```

Regex úteis para parsear o HTML bruto (sem browser):

```python
RE_CTOR    = r'new DownloadPlanilhas\(\s*"([^"]+)"\s*,\s*arquivos\s*,\s*"([^"]+)"'   # -> (modulo, modo)
RE_URL     = r'var url\s*=\s*springUrl \+ "([^"]+)"'                                  # -> caminho base
RE_ARQUIVO = r'arquivos\.push\(\{\s*"ano"\s*:\s*"([^"]*)",\s*"mes"\s*:\s*"([^"]*)",\s*"dia"\s*:\s*"([^"]*)",\s*"origem"\s*:\s*"([^"]*)"\s*\}\)'
```

---

## 2. Lógica de construção das URLs (`download-planilhas.js`)

Arquivo: `https://portaldatransparencia.gov.br/static/js/portal/download-planilhas.js?v=6.4.9`
Classe: `DownloadPlanilhas(modulo, arquivos, modoApresentacao, url)`

### 2.1 Dispatcher

```
criarLinksIniciais():
  "DIA"                -> criarLinksIniciaisArquivoUnico()      -> url + ano+mes+dia
  "MES"                -> criarLinksIniciaisArquivoUnicoMensal()-> url + ano+mes
  "ANO"                -> criarLinksIniciaisAnos()              -> url + ano
  "ANO_MES"            -> criarLinksIniciaisAnos()              -> url + ano+mes
  "ANO_MES_DIA"        -> criarLinksIniciaisAnos()              -> url + ano+mes+dia
  "ANO_MES_ORIGEM"     -> criarLinksIniciaisAnos()              -> url + ano+mes+"_"+origem
  "ORIGEM_ANO_MES_DIA" -> criarLinksIniciaisOrigens()           -> filtra por origem, depois ano/mês/dia
  "UNICO"              -> criarBotaoBaixarArquivoUnico()        -> url + "UNICO"
```

### 2.2 Composição de href por modo

| Modo | href gerado |
|------|-------------|
| `UNICO` | `{url}UNICO` |
| `ANO` | `{url}{ano}` |
| `ANO_MES` | `{url}{ano}{mes}` — concatenação simples, ex.: `202603` |
| `ANO_MES_DIA` | `{url}{ano}{mes}{dia}` — ex.: `20260312` |
| `ANO_MES_ORIGEM` | `{url}{ano}{mes}_{origem}` |

### 2.3 Cascata de selects (importante para automação com browser)

```
#links-anos  --change-->  gerarLinksMeses()   (limpa e repovoa #links-meses, slideUp em #dias e #origens-mes)
#links-meses --change-->  gerarLinksDias()    ou gerarLinksOrigens()  (só p/ módulos servidores)
#links-dias  --change-->  atualiza href de a#link
```

O `<a id="link">` é **removido e recriado** (`$("#link").remove()`) a cada mudança de nível.
→ Ao automatizar via Playwright/Selenium, **re-selecione** `a#link` depois de cada `change` e dispare o evento `change` explicitamente (o site usa jQuery; `element.dispatchEvent(new Event('change'))` funciona).

Meses são exibidos por extenso via `obterMesPorExtenso()`, mas o `value` é sempre `"01".."12"`.

---

## 3. Ficha por dataset

### 3.1 Emendas Parlamentares

| Campo | Valor |
|-------|-------|
| Página | `https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares` |
| `<h2>` | `Emendas Parlamentares` |
| Módulo | `emendas-parlamentares` |
| `modoApresentacao` | `UNICO` |
| `arquivos` | `[]` (vazio) |
| Caixas visíveis | apenas `#arquivo-unico` |
| Link de download | `/download-de-dados/emendas-parlamentares/UNICO` |
| Rótulo do botão | `Baixar arquivo único` |
| Dicionário | `/dicionario-de-dados/emendas-parlamentares` (também acessível por `/pagina-interna/603482-dicionario-de-dados-emendas-parlamentares`) |
| Atualização | Diária |

Texto da página:
> Clique sobre o botão abaixo para acessar o arquivo com os dados disponíveis.
> O arquivo gerado apresenta informações de acordo com a estrutura descrita no seu respectivo dicionário de dados.

**Não há filtro de ano** — é sempre a base completa (snapshot atual).

HTML relevante renderizado:
```html
<div id="arquivo-unico" class="box-dados-ano">
  <p class="download-title">Dados disponíveis</p>
  <ul id="link-unico">
    <li><a type="button" class="br-button primary"
           href="/download-de-dados/emendas-parlamentares/UNICO">Baixar arquivo único</a></li>
  </ul>
</div>
```

---

### 3.2 Emendas parlamentares por Documentos de Despesa

| Campo | Valor |
|-------|-------|
| Página | `https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares-documentos` |
| `<h2>` | `Emendas parlamentares por Documentos de Despesa` |
| Módulo | `emendas-parlamentares-documentos` |
| `modoApresentacao` | `ANO` |
| `arquivos` | 13 entradas, `origem = "EmendasParlamentaresPorDocumento"`, `mes`/`dia` vazios |
| Anos disponíveis | `2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016, 2015, 2014` |
| Caixas visíveis | apenas `#anos` |
| Padrão de download | `/download-de-dados/emendas-parlamentares-documentos/{ano}` |
| Dicionário | `/dicionario-de-dados/emendas-parlamentares-por-documento` |
| Atualização | Diária |

Texto da página:
> Selecione o ano desejado e clique em Baixar.

Ordem no `<select>`: **decrescente** (o JS faz `d.reverse()`), primeiro option = ano mais recente = valor default do `href` inicial.

---

### 3.3 Apoiamento emendas parlamentares

| Campo | Valor |
|-------|-------|
| Página | `https://portaldatransparencia.gov.br/download-de-dados/apoiamento-emendas-parlamentares-documentos` |
| `<h2>` | `Apoiamento emendas parlamentares` |
| Módulo | `apoiamento-emendas-parlamentares-documentos` |
| `modoApresentacao` | `ANO` |
| `arquivos` | 7 entradas, `origem = "ApoiamentoEmendasParlamentares"`, `mes`/`dia` vazios |
| Anos disponíveis | `2026, 2025, 2024, 2023, 2022, 2021, 2020` |
| Caixas visíveis | apenas `#anos` |
| Padrão de download | `/download-de-dados/apoiamento-emendas-parlamentares-documentos/{ano}` |
| Dicionário | `/dicionario-de-dados/apoiamentos-emendas-parlamentares` |
| Atualização | Mensal (índice) / semanal (texto da página) |

Texto da página:
> Selecione o exercício e o mês desejados e clique em Baixar.
> Os dados sobre apoiadores de emendas parlamentares são enviados pelo Congresso Nacional e contemplam apoiamentos de emendas de comissão (2022 a 2024) e de relator (2020 a 2022). Esses dados serão atualizados semanalmente.
> A publicação desses dados decorre do plano de trabalho estabelecido entre os Poderes Executivo e Legislativo, homologado pelo Supremo Tribunal Federal (STF), em março, no âmbito da Arguição de Descumprimento de Preceito Fundamental (ADPF) nº 854.

**⚠️ Inconsistência do site:** o texto pede "exercício **e o mês**", mas o `modoApresentacao` é `ANO` e `#meses` está `display:none` com zero options. **Só há filtro de ano.** Texto residual de uma versão anterior — não tente montar URL com mês aqui.

Link adicional: `/emendas/adpf854` (contexto jurídico da publicação).

---

## 4. Layout dos dados (dicionários oficiais)

> Os ZIPs contêm CSV em **`;` (ponto e vírgula)**, encoding **ISO-8859-1 / Windows-1252** e valores monetários no formato brasileiro (`1234,56`) — padrão do Portal da Transparência. **Confirme na primeira execução** e ajuste `encoding=` / `decimal=","` / `thousands="."` no `pandas.read_csv`.

### 4.1 Dataset 1 — Emendas Parlamentares (3 tabelas no mesmo ZIP)

#### Tabela A: "Por Emenda Parlamentar"

| # | Coluna | Descrição |
|---|--------|-----------|
| 1 | Código da Emenda | Identificador de 12 dígitos: 4 (ano) + 4 (código do autor) + 4 (número da emenda) |
| 2 | Ano da Emenda | Ano em que a emenda foi proposta |
| 3 | Tipo da Emenda | Tipo de emenda parlamentar |
| 4 | Código do Autor da Emenda | Código do autor conforme SIAFI |
| 5 | Nome do Autor da Emenda | Nome do autor conforme SIAFI |
| 6 | Número da Emenda | Número conforme SIAFI |
| 7 | Localidade do Gasto | Região onde a despesa ocorre (atributo do Plano de Trabalho) |
| 8 | Código Município IBGE | Pode vir em branco |
| 9 | Município | Pode vir em branco |
| 10 | Código UF IBGE | Pode vir em branco |
| 11 | UF | Pode vir em branco |
| 12 | Região | Região de destinação do recurso |
| 13 | Código Função | Função orçamentária (MTO) |
| 14 | Nome Função | |
| 15 | Código Subfunção | |
| 16 | Nome Subfunção | |
| 17 | Código Programa | |
| 18 | Nome Programa | |
| 19 | Código Ação | |
| 20 | Nome Ação | |
| 21 | Código Plano Orçamentário | PO — gerencial, não consta da LOA |
| 22 | Nome Plano Orçamentário | |
| 23 | Valor Empenhado | |
| 24 | Valor Liquidado | |
| 25 | Valor Pago | |
| 26 | Valor Restos A Pagar Inscritos | |
| 27 | Valor Restos A Pagar Cancelados | |
| 28 | Valor Restos A Pagar Pagos | |

#### Tabela B: "Por Emendas Parlamentares - Convênios"

| # | Coluna | Descrição |
|---|--------|-----------|
| 1 | Código da Emenda | 12 dígitos |
| 2 | Código Função | |
| 3 | Nome Função | |
| 4 | Código Subfunção | |
| 5 | Nome Subfunção | |
| 6 | Localidade do Gasto | |
| 7 | Tipo de Emenda | |
| 8 | Data Publicação Convênio | |
| 9 | Convenente | Quem recebe os recursos federais |
| 10 | Objeto Convênio | |
| 11 | Número Convênio | |
| 12 | Valor Convênio | Inclui parcela `999` de rendimento de aplicação financeira, quando houver |

#### Tabela C: "Por Favorecido em Emendas Parlamentares"

| # | Coluna | Descrição |
|---|--------|-----------|
| 1 | Código da Emenda | 12 dígitos |
| 2 | Código do Autor da Emenda | |
| 3 | Nome do Autor da Emenda | |
| 4 | Número da Emenda | |
| 5 | Tipo de Emenda | |
| 6 | Ano/Mês | Ano e mês do lançamento |
| 7 | Código do Favorecido | CPF/CNPJ ou código |
| 8 | Favorecido | |
| 9 | Natureza Jurídica | |
| 10 | Tipo Favorecido | Pessoa Física / Pessoa Jurídica |
| 11 | UF Favorecido | |
| 12 | Município Favorecido | |
| 13 | Valor Recebido | |

**Chave de junção entre as três tabelas:** `Código da Emenda` (12 dígitos).

---

### 4.2 Dataset 2 — Emendas parlamentares por Documentos de Despesa (48 colunas)

| # | Coluna | Nota |
|---|--------|------|
| 1 | Código da Emenda | 12 dígitos |
| 2 | Ano da Emenda | |
| 3 | Código do Autor da Emenda | SIAFI |
| 4 | Nome do Autor da Emenda | SIAFI |
| 5 | Número da Emenda | SIAFI |
| 6 | Código Função | |
| 7 | Valor Empenhado | **Só preenchido em documento de empenho** |
| 8 | Valor Pago | **Só preenchido em documento de pagamento** |
| 9 | Tipo de Emenda | |
| 10 | Data Documento | Empenhos ≤2020, liquidações e pagamentos → data de emissão. Empenhos ≥2021 sujeitos a alteração → data da **última operação** |
| 11 | Código Documento | ID único do documento no SIAFI |
| 12 | Localidade de Aplicação do Recurso | Regra complexa (ver abaixo) |
| 13 | UF de Aplicação do Recurso | |
| 14 | Município de Aplicação do Recurso | |
| 15 | Código IBGE do Município de Aplicação do Recurso | |
| 16 | Fase da Despesa | `empenho` / `liquidação` / `pagamento` |
| 17 | Código Favorecido | |
| 18 | Favorecido | |
| 19 | Tipo Favorecido | Pessoa Física / Pessoa Jurídica / outros (ex.: exterior) |
| 20 | UF Favorecido | |
| 21 | Município Favorecido | |
| 22 | Código UG | Unidade Gestora |
| 23 | UG | |
| 24 | Código Unidade Orçamentária | |
| 25 | Unidade Orçamentária | |
| 26 | Código Órgão SIAFI | |
| 27 | Órgão | |
| 28 | Código Órgão Superior SIAFI | |
| 29 | Órgão Superior | |
| 30 | Código Grupo Despesa | `1`–`6` |
| 31 | Grupo Despesa | Pessoal e Encargos Sociais / Juros e Encargos da Dívida / Outras Despesas Correntes / Investimentos / Inversões financeiras / Amortização da Dívida |
| 32 | Código Elemento Despesa | |
| 33 | Elemento Despesa | |
| 34 | Código Modalidade Aplicação Despesa | 3º e 4º dígitos da natureza da despesa |
| 35 | Modalidade Aplicação Despesa | |
| 36 | Código Plano Orçamentário | |
| 37 | Plano Orçamentário | |
| 38 | Função | |
| 39 | Código Subfunção | |
| 40 | Subfunção | |
| 41 | Código Programa | |
| 42 | Programa | |
| 43 | Código Ação | |
| 44 | Ação | |
| 45 | Linguagem Cidadã | Nome intuitivo da ação (ex.: "Bolsa Família") |
| 46 | Código Subtítulo (Localizador) | |
| 47 | Subtítulo (Localizador) | |
| 48 | Possui convênio? | Sim/Não |

**Regra da "Localidade de Aplicação do Recurso"** (relevante para análises geográficas — implemente com cuidado):
usa a **localidade do favorecido** quando:
1. Modalidades `40`/`41` (Transf. a Municípios) e natureza jurídica do favorecido `1244` (Município Adm. Pública) ou `1201` (Fundo Público);
2. Modalidade `32` (Exec. Orç. Delegada a Estados/DF) e natureza `1236` (Estado/DF) ou `1023` (Órgão Público Exec. Estadual/DF);
3. Modalidade `31` (Transf. a Estados/DF Fundo a Fundo) e natureza `1201`;
4. Modalidade `30` (Transf. a Estados/DF) e natureza `1201`, `1236` ou `1112` (Autarquia Estadual/DF).

Caso contrário → usa a **Regionalização do Gasto** do Plano de Trabalho.

---

### 4.3 Dataset 3 — Apoiamento emendas parlamentares (27 colunas)

| # | Coluna | Descrição |
|---|--------|-----------|
| 1 | Código Apoiador | Código do parlamentar apoiador/solicitante do empenho |
| 2 | Apoiador | Nome do parlamentar apoiador/solicitante |
| 3 | Data do Apoio | |
| 4 | Data Retirada do Apoio | Vazio se o apoio não foi retirado |
| 5 | Empenho | **6 díg. UG + 5 díg. Gestão + 11 díg. Empenho** (22 caracteres) |
| 6 | Data última movimentação Empenho | |
| 7 | Código favorecido | Ex.: CNPJ |
| 8 | Favorecido | |
| 9 | Tipo Favorecido | Pessoa Física / Pessoa Jurídica |
| 10 | UF Favorecido | |
| 11 | Município Favorecido | |
| 12 | Código da Emenda | 12 dígitos |
| 13 | Tipo de Emenda | |
| 14 | Ano da Emenda | |
| 15 | Código UG | |
| 16 | UG | |
| 17 | Código Unidade Orçamentária | |
| 18 | Unidade Orçamentária | |
| 19 | Código Órgão SIAFI | |
| 20 | Órgão | |
| 21 | Código Órgão Superior SIAFI | |
| 22 | Órgão Superior | |
| 23 | Código Ação | |
| 24 | Ação | |
| 25 | Valor Empenhado | Não inclui valores cancelados |
| 26 | Valor Cancelado | |
| 27 | Valor Pago | |

> Contagem literal do dicionário: 27 campos. Confirmar contra o header real do CSV na primeira execução.
>
> **Verificado em 13/08/2026:** trocar `#links-anos` para `2019` e disparar `change` atualiza o `href` para `/download-de-dados/emendas-parlamentares-documentos/2019` — o padrão `{url}{ano}` está confirmado empiricamente.

**Este é o dataset-chave para análise política:** liga *parlamentar apoiador* → *empenho* → *favorecido*. Cobertura declarada: emendas de **comissão 2022–2024** e de **relator 2020–2022** (embora o select ofereça 2020–2026).

---

## 5. Bot protection — restrição de arquitetura (LEIA ANTES DE CODAR)

O portal usa **AWS WAF Bot Control**. Evidências coletadas:

- Script `challenge.js` + script ofuscado (`.../i6jm58tcv8`) carregados em toda página.
- Telemetria POST para `https://c41081116149.edge.sdk.awswaf.com/c41081116149/daffd43b3933/telemetry`.
- Cliente HTTP simples (`curl` com UA de navegador) → **`403` no CONNECT/handshake**.
- `fetch()` / `XMLHttpRequest` a partir do próprio contexto da página → **`Failed to fetch` / erro de rede**, mesmo com `credentials:'include'`.

**Consequência prática:** o download **não funciona** por HTTP client puro (`requests`, `httpx`, `curl`, `wget`, `axios`). Requer um **navegador real** que execute o desafio JS e obtenha o cookie `aws-waf-token`.

### Estratégias recomendadas, em ordem de preferência

**A) Playwright/Selenium com download nativo** — mais robusto
```python
from playwright.sync_api import sync_playwright

PAGES = {
    "emendas-parlamentares": ["UNICO"],
    "emendas-parlamentares-documentos": [str(a) for a in range(2014, 2027)],
    "apoiamento-emendas-parlamentares-documentos": [str(a) for a in range(2020, 2027)],
}
BASE = "https://portaldatransparencia.gov.br/download-de-dados"

with sync_playwright() as p:
    br = p.chromium.launch(headless=False)          # headless=True costuma ser barrado pelo WAF
    ctx = br.new_context(accept_downloads=True, locale="pt-BR")
    pg = ctx.new_page()
    # 1) visita a página HTML primeiro -> resolve o desafio e grava o cookie aws-waf-token
    pg.goto(f"{BASE}/emendas-parlamentares", wait_until="networkidle")
    pg.wait_for_timeout(5000)
    for modulo, params in PAGES.items():
        pg.goto(f"{BASE}/{modulo}", wait_until="networkidle")
        for prm in params:
            with pg.expect_download(timeout=600_000) as dl:
                pg.evaluate("u => location.href = u", f"{BASE}/{modulo}/{prm}")
            d = dl.value
            d.save_as(f"./raw/{modulo}_{prm}.zip")
```
Notas:
- **Reutilize o mesmo `context`** entre downloads — o token do WAF vive nos cookies.
- Prefira `headless=False` (ou `chromium` com `--headless=new` + UA realista); WAF costuma barrar headless clássico.
- `expect_download` com timeout longo: arquivos anuais podem ser grandes e o servidor gera o ZIP sob demanda.

**B) Navegar → extrair cookies → baixar com `requests`** — mais rápido, mais frágil
Faça `pg.goto()` na página HTML, exporte `ctx.cookies()` para uma `requests.Session`, replique `User-Agent`/`Accept-Language` **exatamente**, e baixe. Se falhar com 403/405, volte à estratégia A.

**C) API oficial `api-de-dados`** — sem WAF, mas paginada
Host: `https://api.portaldatransparencia.gov.br`
Autenticação: header **`chave-api-dados`** (cadastro gratuito no portal). Esquema declarado no OpenAPI como `Authorization` (header).

| Endpoint | Método | Parâmetros |
|----------|--------|-----------|
| `/api-de-dados/emendas` | GET | `codigoEmenda`, `numeroEmenda`, `nomeAutor`, `tipoEmenda`, `ano`, `codigoFuncao`, `codigoSubfuncao`, `pagina` |
| `/api-de-dados/emendas/documentos/{codigo}` | GET | `codigo` (path), `pagina` |

Spec: `https://api.portaldatransparencia.gov.br/v3/api-docs` · UI: `/swagger-ui/index.html`
Bom para: enriquecimento pontual, validação cruzada, atualizações incrementais.
Ruim para: carga histórica completa (rate limit + paginação).

> **Não implemente bypass de CAPTCHA/WAF.** A rota suportada é navegador real (A) ou a API oficial com chave (C).

---

## 6. Esqueleto sugerido do projeto

```
emendas-scraper/
├── config.yaml            # módulos, faixas de anos, destino
├── src/
│   ├── discover.py        # varre as 3 páginas, extrai modo + arquivos[] + anos
│   ├── download.py        # Playwright: baixa os ZIPs (estratégia A)
│   ├── extract.py         # descompacta, detecta encoding/sep, normaliza headers
│   ├── schema.py          # dataclasses/pandera com as colunas da §4
│   └── api_client.py      # cliente da api-de-dados (chave-api-dados)
├── raw/                   # ZIPs originais + manifest.json (url, sha256, bytes, ts)
├── staging/               # CSVs extraídos
└── warehouse/             # parquet particionado por ano
```

### `discover.py` — pseudo-código
```python
MODULOS = [
    "emendas-parlamentares",
    "emendas-parlamentares-documentos",
    "apoiamento-emendas-parlamentares-documentos",
]

def discover(page, modulo):
    page.goto(f"https://portaldatransparencia.gov.br/download-de-dados/{modulo}",
              wait_until="networkidle")
    titulo = page.inner_text("section.box-identificacao h2")
    modo = page.evaluate("""() => {
        const s = [...document.querySelectorAll('script:not([src])')]
                  .map(x => x.textContent).find(t => t.includes('new DownloadPlanilhas'));
        return s.match(/new DownloadPlanilhas\\(\\s*"[^"]+"\\s*,\\s*arquivos\\s*,\\s*"([^"]+)"/)[1];
    }""")
    anos = page.eval_on_selector_all("#links-anos option", "o => o.map(x => x.value)")
    unico = page.get_attribute("#link-unico a", "href")   # None se não houver
    return dict(modulo=modulo, titulo=titulo, modo=modo, anos=anos, link_unico=unico)
```

### Ideias de checks de integridade
- Gravar `sha256` + `content-length` de cada ZIP em `raw/manifest.json`; só reprocessar se mudou.
- Datasets 1 e 2 são **diários** → agendar 1×/dia de madrugada (BRT). Dataset 3, 1×/semana.
- Validar contagem de colunas contra a §4 e falhar alto se o layout mudar.
- Dataset 1 = snapshot completo (sem histórico versionado): **guarde os ZIPs datados** para permitir séries temporais.
- Reconciliação: `SUM(Valor Empenhado)` do dataset 2 agrupado por `Código da Emenda` deve se aproximar do `Valor Empenhado` do dataset 1 (mesmas emendas/anos). Divergências indicam recorte ou defasagem de atualização.

---

## 7. Referências

| Recurso | URL |
|---------|-----|
| Índice de dados abertos | `https://portaldatransparencia.gov.br/download-de-dados` |
| Dicionário — Emendas Parlamentares | `https://portaldatransparencia.gov.br/dicionario-de-dados/emendas-parlamentares` |
| Dicionário — Por Documentos de Despesa | `https://portaldatransparencia.gov.br/dicionario-de-dados/emendas-parlamentares-por-documento` |
| Dicionário — Apoiamentos | `https://portaldatransparencia.gov.br/dicionario-de-dados/apoiamentos-emendas-parlamentares` |
| Contexto ADPF 854 | `https://portaldatransparencia.gov.br/emendas/adpf854` |
| JS de download | `https://portaldatransparencia.gov.br/static/js/portal/download-planilhas.js` |
| OpenAPI da API de Dados | `https://api.portaldatransparencia.gov.br/v3/api-docs` |

---

## 8. Pendências a confirmar na primeira execução real

- [ ] Nome e convenção do arquivo baixado (`Content-Disposition`) — não verificável sem download.
- [ ] Encoding real do CSV (esperado ISO-8859-1) e separador (esperado `;`).
- [ ] Número de CSVs dentro do ZIP do dataset 1 (esperado 3, conforme as três tabelas do dicionário).
- [ ] Header literal do dataset 3 (dicionário lista 27 campos; validar).
- [ ] Tamanho dos ZIPs anuais do dataset 2 (dimensionar timeout e disco).
- [ ] Se `headless=True` passa pelo WAF (testar antes de agendar em CI).
