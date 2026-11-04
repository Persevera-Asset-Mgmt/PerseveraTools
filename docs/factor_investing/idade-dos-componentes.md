# Limite de idade dos componentes

- **Status:** Implementado (`BacktestConfig.max_age_by_frequency` /
  `component_max_age_days`, PR a abrir)
- **Atualizado em:** 2026-11-04

## Motivação

`snapshot_components` repetia o último valor de cada componente sem limite de
idade. Quando a fonte para de publicar, o valor velho continua entrando no score
para sempre. O `factor_zoo` grava só as datas com valor, então "a cobertura
acabou" não se distingue de "não mudou".

Caso que motivou: o consenso Bloomberg de FCF (`free_cash_flow_fwd`, `1BF`) de
BRAP4 e GOAU4 parou em 20/10/2021. Em jun/2025 o `fcf_yield_fwd` de −25% da
BRAP4, de 1.349 dias antes, ainda entrava no score Value. A AMER3 usava em 2025
o ROE de nov/2022.

## Regra

Um valor mais velho que o limite do componente (em dias corridos, contados da
última data com valor até o rebalanceamento) vira ausente.

| `Frequência` | Limite | Base empírica (intervalos entre registros consecutivos, 2010+) |
|---|---|---|
| Diário | 10 | p50 1 d, p99 4–12 d; ~1% acima de 10 d (papéis que quase não negociam) |
| Consenso | 30 | p99 3–4 d, p99,9 10–23 d |
| Trimestral | 200 | p50 91 d, p90 133 d (3º tri → anual); 1,5–2,5% acima de 200 d |

De onde vem a frequência de cada componente, em ordem:

1. `component_max_age_days` do config.
2. `Frequência` do Fibery (`Inv-Rsrch-Quant/Definições dos Fatores`). Hoje só os
   campos brutos têm o campo preenchido; os 85 fatores derivados estão vazios.
3. Regra por nome para os derivados: `*_fwd` → Consenso; `momentum_*`,
   `delta_*`, `median_*`, `volatility_*`, `rsi_*`, `beta*`, `price_range` →
   Diário; margens, crescimento, variabilidade, accruals, ROE/ROIC/ROA/ROCE →
   Trimestral.
4. Trimestral, com aviso no log. Em 2026-11-04 caem aqui 12 fatores ativos,
   nenhum componente de estilo (`*_yield_ltm`, `*_percentile_10y`,
   `short_interest_pct`, `days_to_cover`, `capex_to_sales`, `book_yield_ltm`,
   `ANNOUNCEMENT_DT`).

Com `beta_neutral`, o beta que dimensiona as pernas segue o mesmo limite do
`beta_field` (Diário, 10 dias). Um beta velho vira ausente e o
`beta_neutralize` usa a mediana; o diagnóstico conta esses nomes em
`n_stale_beta`. Assim, o mesmo beta não fica descartado no score (estilo Risk) e
em uso no tamanho das pernas.

Os limites ficam entre 0 e 365 dias (`COMPONENT_LOOKBACK_DAYS`), que é o
histórico carregado antes de `start_date`. Antes, o primeiro rebalanceamento não
via valores com mais de 1 ano e os seguintes viam.

## O que os testes mostraram

Protótipo com os 17 componentes de estilo, 2015-01 a 2026-10, rebalanceamento
mensal (`BME`), universo líquido (ADTV 21d ≥ R$8 mi):

- Sem limite, o snapshot novo é idêntico ao antigo nas 141 datas, em metade do
  tempo (32 s contra 59 s).
- Descartes no universo líquido: `fcf_yield_fwd` 11%, demais de consenso ~4%,
  trimestrais 1–3%, diários ~0 (4 valores de `momentum_12m` em 11 anos).
- O descarte alto do `fcf_yield_fwd` em 2021 não é buraco de carga (a cobertura
  mensal fica em ~140 papéis): é cobertura que acabou de vez (230 papéis desde
  2010). As pausas de 31–45 dias que depois voltam são ~10% das pausas acima de
  30 dias; subir o limite de Consenso para 45 quase não recuperaria valores.

Backtest Value, long-only, quintil 20%, `BME`, 2016-01-01 a 2025-12-31,
`adtv_min` R$8 mi, sem custos:

| | Limites padrão | Folgado (365 d em tudo) |
|---|---|---|
| Retorno anual | 28,0% | 27,2% |
| Sharpe | 1,10 | 1,06 |
| Max drawdown | −54,2% | −54,9% |
| Nomes médios | 24,4 | 24,8 |
| Valores descartados | 2.717 | 1.170 |

100 das 120 carteiras mudam, quase sempre em poucos nomes.

Risk, long/short beta-neutro, `universe_index="IBX100"`, `adtv_min=0`, quintil
20%, `BME`, 2020-01-01 a 2025-12-31, sem custos: 72 rebalanceamentos, beta
líquido 0 em todos e nenhum beta descartado por idade (os betas do IBX100 são
diários).

## Em aberto

- A BRAP4 passou a ficar **mais** tempo na carteira (71 meses contra 64): sem o
  FCF velho e muito negativo, o score dela usa só 2 dos 3 componentes. A média
  do score composto ignora componentes ausentes (`skipna`), então falta exigir
  cobertura mínima de componentes.
- Preencher `Frequência` no Fibery para os fatores derivados, para não depender
  da regra por nome.
- O filtro de ADTV (`snapshot_series(max_age=...)`) ainda conta a idade em
  linhas do painel, não em dias corridos.
