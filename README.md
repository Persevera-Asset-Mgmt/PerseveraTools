# PerseveraTools

Pacote interno da Persevera Asset Management: carga de dados de mercado no
Postgres, renda fixa, pesquisa quant e integrações com Fibery e custodiantes.

## Instalação

```
pip install git+https://github.com/Persevera-Asset-Mgmt/PerseveraTools.git
```

Para desenvolver, num clone do repositório:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .[dev]
.venv\Scripts\python.exe -m pytest -q tests/
```

Os testes são offline e rodam no GitHub Actions em todo PR
(`.github/workflows/tests.yml`): com as versões do `uv.lock` e com as do PC
Bloomberg (Windows, Python 3.11, pandas 2.2.2, numpy 1.26.4).

## Módulos

| Módulo | Conteúdo |
|---|---|
| `data` | `FinancialDataService` e os providers (ANBIMA, ANBIMA Feed, B3, BCB, FRED, SIDRA, Mais Retorno, custodiantes, …); leitura de indicadores, fundos, composição de índices e `factor_zoo` |
| `fixed_income` | séries de crédito privado e títulos públicos, spread IPCA, métricas |
| `custom_series` | séries derivadas internamente (`persevera_*` em `indicadores`) |
| `quant_research.factor_investing` | backtest de fatores (long-only e long/short, universo por índice) |
| `quant_research.risk_budgeting_spectrum` | Motor RBS: espectro de perfis por risk budgeting, calibrado no Fibery |
| `quant_research.allocation_engine` | motor de alocação agnóstico a instrumento |
| `db` | conexão e upsert no Postgres; cliente Fibery |
| `open_finance` | integração Pluggy |
| `config`, `utils` | configuração por `.env`, logging, datas |

A tabela de destino de cada provider, e quais têm réplica em workflow do n8n,
está em [docs/data/providers-tabelas-n8n.md](docs/data/providers-tabelas-n8n.md).

## Configuração

Crie `.persevera/.env` na sua pasta de usuário
(`%USERPROFILE%\.persevera\.env` no Windows, `~/.persevera/.env` no Mac/Linux).
Só são necessárias as variáveis dos serviços que você usa.

```
# Caminhos
PERSEVERA_DATA_PATH=
PERSEVERA_AUTOMATION_PATH=

# Postgres
PERSEVERA_DB_USER=
PERSEVERA_DB_PASSWORD=
PERSEVERA_DB_HOST=
PERSEVERA_DB_PORT=
PERSEVERA_DB_NAME=

# Logging (opcional)
PERSEVERA_LOG_LEVEL=INFO
PERSEVERA_LOG_FORMAT=
PERSEVERA_LOG_DATE_FORMAT=
PERSEVERA_LOG_FILE=
PERSEVERA_LOG_FILE_ENABLED=
PERSEVERA_LOG_DIR=

# Dados de mercado
PERSEVERA_FRED_API_KEY=
PERSEVERA_ANBIMA_FEED_CLIENT_ID=
PERSEVERA_ANBIMA_FEED_CLIENT_SECRET=
PERSEVERA_ANBIMA_FEED_BASE_URL=
PERSEVERA_ANBIMA_FEED_SANDBOX=
PERSEVERA_COMDINHEIRO_USERNAME=
PERSEVERA_COMDINHEIRO_PASSWORD=

# Fibery, Make e Google Sheets
PERSEVERA_FIBERY_API_TOKEN=
PERSEVERA_FIBERY_DOMAIN=
PERSEVERA_MAKE_API_TOKEN=
PERSEVERA_MAKE_API_URL=
PERSEVERA_GOOGLESHEETS_CLIENT_ID=
PERSEVERA_GOOGLESHEETS_CLIENT_SECRET=
PERSEVERA_GOOGLESHEETS_PROJECT_ID=
PERSEVERA_GOOGLESHEETS_API_KEY=

# XP (Make proxy e XP Hub)
PERSEVERA_XPWS_MAKE_TOKEN_SCENARIO_ID=
PERSEVERA_XPWS_MAKE_PROXY_SCENARIO_ID=
PERSEVERA_XPHUB_BASE_URL=
PERSEVERA_XPHUB_BEARER_TOKEN=
PERSEVERA_XPHUB_SUBSCRIPTION_KEY=
PERSEVERA_XPHUB_CUSTOMER_CODE=
PERSEVERA_XPHUB_BRAND=
PERSEVERA_XPHUB_SEGMENT_CHANNEL=
PERSEVERA_XPHUB_SEGMENT_CODE=

# BTG
PERSEVERA_BTGWS_CLIENT_ID=
PERSEVERA_BTGWS_CLIENT_SECRET=
PERSEVERA_BTGWS_AUTH_BASE_URL=
PERSEVERA_BTGWS_ACCOUNTS_BASE_URL=
PERSEVERA_BTGWS_POSITIONS_BASE_URL=
PERSEVERA_BTGWS_TIMEOUT=
PERSEVERA_BTGWS_MAX_RETRIES=
PERSEVERA_BTGWS_RETRY_BACKOFF=
PERSEVERA_BTGWS_REQUEST_DELAY=
PERSEVERA_BTGWS_VERIFY_SSL=

# Warren One
PERSEVERA_WARRENONE_ENV=
PERSEVERA_WARRENONE_CLIENT_ID=
PERSEVERA_WARRENONE_SECRET_KEY=
PERSEVERA_WARRENONE_TENANT_ID=
PERSEVERA_WARRENONE_AUTH_BASE_URL=
PERSEVERA_WARRENONE_API_BASE_URL=
PERSEVERA_WARRENONE_TIMEOUT=
PERSEVERA_WARRENONE_MAX_RETRIES=
PERSEVERA_WARRENONE_RETRY_BACKOFF=
PERSEVERA_WARRENONE_REQUEST_DELAY=
PERSEVERA_WARRENONE_VERIFY_SSL=

# Interactive Brokers
PERSEVERA_IBKR_BASE_URL=
PERSEVERA_IBKR_CONSUMER_KEY=
PERSEVERA_IBKR_CONSUMER_SECRET=
PERSEVERA_IBKR_ACCESS_TOKEN=
PERSEVERA_IBKR_ACCESS_TOKEN_SECRET=
```

## Produção

Os jobs agendados (`run_factor_zoo_daily.bat`, `run_factor_zoo_independent.bat`,
`run_ihfa_subindices.bat`) rodam no PC Bloomberg, com Python 3.11, pandas 2.2.2
e numpy 1.26. Uma mudança no `main` só vale lá depois de o pacote ser
reinstalado nessa máquina.

## Documentação

Aprendizados, diagnósticos e propostas ficam em [docs/](docs/README.md).
