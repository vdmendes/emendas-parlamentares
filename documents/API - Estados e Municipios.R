# =============================================================================
# Extrator SICONFI - Matriz de Saldos Contábeis (MSC)
# =============================================================================

# 1. Configuração do Diretório e Pacotes
DIRETORIO_BASE <- "D:/aeae"
if (!dir.exists(DIRETORIO_BASE)) dir.create(DIRETORIO_BASE, recursive = TRUE)
setwd(DIRETORIO_BASE)

pacotes <- c("httr2", "dplyr", "arrow", "stringr")
for (p in pacotes) {
  if (!requireNamespace(p, quietly = TRUE)) install.packages(p)
  library(p, character.only = TRUE)
}

# 2. Dicionários e Funções Auxiliares (IBGE)
ESTADOS_IBGE <- c(
  "RO" = 11, "AC" = 12, "AM" = 13, "RR" = 14, "PA" = 15, "AP" = 16, "TO" = 17,
  "MA" = 21, "PI" = 22, "CE" = 23, "RN" = 24, "PB" = 25, "PE" = 26, "AL" = 27,
  "SE" = 28, "BA" = 29, "MG" = 31, "ES" = 32, "RJ" = 33, "SP" = 35, "PR" = 41,
  "SC" = 42, "RS" = 43, "MS" = 50, "MT" = 51, "GO" = 52, "DF" = 53
)

obter_municipios_ibge <- function() {
  cat("Buscando lista de municípios no IBGE...\n")
  url <- "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
  req <- request(url) |> req_retry(max_tries = 3)
  resp <- req_perform(req)
  dados <- resp_body_json(resp, simplifyVector = TRUE)
  
  uf_siglas <- dados$microrregiao$mesorregiao$UF$sigla
  nomes_limpos <- str_replace_all(dados$nome, '[\\\\/:*?"<>|]', "")
  
  chaves <- paste0(nomes_limpos, "_", uf_siglas)
  mapa <- setNames(dados$id, chaves)
  
  return(mapa)
}

# 3. Função de Requisição
baixar_msc <- function(id_ente, an_referencia) {
  url_base <- "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/msc_orcamentaria"
  lista_lotes <- list()
  offset <- 0
  limit <- 5000
  
  repeat {
    req <- request(url_base) |>
      req_url_query(
        id_ente = id_ente,
        an_referencia = an_referencia,
        me_referencia = 12,           
        co_tipo_matriz = "MSCC",      
        classe_conta = 6,             
        id_tv = "ending_balance",     
        offset = offset,
        limit = limit
      ) |>
      req_retry(max_tries = 3) |>
      req_timeout(60)
    
    resp <- tryCatch(req_perform(req), error = function(e) NULL)
    if (is.null(resp) || resp_status(resp) != 200) break
    
    dados_json <- resp_body_json(resp, simplifyVector = TRUE)
    if (is.null(dados_json$items) || length(dados_json$items) == 0) break
    
    lista_lotes[[length(lista_lotes) + 1]] <- as.data.frame(dados_json$items)
    
    if (isTRUE(dados_json$hasMore)) {
      offset <- offset + limit
      Sys.sleep(0.5) 
    } else {
      break
    }
  }
  
  if (length(lista_lotes) > 0) {
    return(bind_rows(lista_lotes))
  } else {
    return(data.frame()) 
  }
}

# 4. Loops de Extração
extrair_estados <- function(anos = 2019:2024) { 
  pasta_saida <- file.path(DIRETORIO_BASE, "dados_estados")
  if (!dir.exists(pasta_saida)) dir.create(pasta_saida)
  
  cat("\n=== Iniciando Download SICONFI (Estados) ===\n")
  for (sigla in names(ESTADOS_IBGE)) {
    cod_ibge <- ESTADOS_IBGE[[sigla]]
    for (ano in anos) {
      arquivo_parquet <- file.path(pasta_saida, sprintf("msc_%s_%d.parquet", sigla, ano))
      
      if (file.exists(arquivo_parquet)) {
        cat(sprintf("[PULO] %s %d - Já extraído\n", sigla, ano))
        next
      }
      
      cat(sprintf("[BUSCA] %s %d... ", sigla, ano))
      df_estado <- baixar_msc(cod_ibge, ano)
      
      if (nrow(df_estado) == 0) {
        cat("VAZIO\n")
        write_parquet(data.frame(aviso = "Sem dados", cod_ibge = cod_ibge, ano = ano), arquivo_parquet)
      } else {
        cat(sprintf("OK! %d linhas.\n", nrow(df_estado)))
        df_estado$uf <- sigla
        df_estado$ano_extracao <- ano
        write_parquet(df_estado, arquivo_parquet)
      }
    }
  }
}

extrair_municipios <- function(anos = 2019:2024) {
  pasta_saida <- file.path(DIRETORIO_BASE, "dados_municipios")
  if (!dir.exists(pasta_saida)) dir.create(pasta_saida)
  
  mapa_mun <- obter_municipios_ibge()
  total_mun <- length(mapa_mun)
  contador <- 0
  
  cat("\n=== Iniciando Download SICONFI (Municípios) ===\n")
  for (nome_uf in names(mapa_mun)) {
    contador <- contador + 1
    cod_ibge <- mapa_mun[[nome_uf]]
    
    for (ano in anos) {
      arquivo_parquet <- file.path(pasta_saida, sprintf("msc_%s_%d.parquet", nome_uf, ano))
      
      if (file.exists(arquivo_parquet)) {
        cat(sprintf("[%d/%d] [PULO] %s %d - Já extraído\n", contador, total_mun, nome_uf, ano))
        next
      }
      
      cat(sprintf("[%d/%d] [BUSCA] %s %d... ", contador, total_mun, nome_uf, ano))
      df_mun <- baixar_msc(cod_ibge, ano)
      
      if (nrow(df_mun) == 0) {
        cat("VAZIO\n")
        write_parquet(data.frame(aviso = "Sem dados", cod_ibge = cod_ibge, ano = ano), arquivo_parquet)
      } else {
        cat(sprintf("OK! %d linhas.\n", nrow(df_mun)))
        df_mun$municipio <- nome_uf
        df_mun$ano_extracao <- ano
        write_parquet(df_mun, arquivo_parquet)
      }
    }
  }
}

# 5. Executar
# extrair_estados(anos = 2019:2024)
#extrair_municipios(anos = 2019:2024)
