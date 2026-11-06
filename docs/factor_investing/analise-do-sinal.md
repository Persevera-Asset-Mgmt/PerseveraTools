# Análise do sinal, Sharpe sobre CDI e cobertura mínima de componentes

- **Status:** Implementado (PR #31)
- **Atualizado em:** 2026-11-05

## O que mudou no motor

1. **Sharpe e benchmark.** Antes o Sharpe usava `risk_free_rate=0`. No Brasil, o
   Sharpe da long-only com rf=0 é quase só CDI. Agora:
   - `risk_free_series="br_cdi_index"` (default): o retorno diário do CDI nas
     datas do NAV entra no Sharpe da **long-only**. A **long/short** é
     autofinanciada; o retorno dela já é excesso, então o Sharpe não desconta rf.
   - `benchmark_series="br_ibovespa"` (default): β, alfa anualizado com t-stat
     Newey-West e correlação. Na long-only também retorno ativo, tracking error e IR.
   - `risk_free_rate` constante continua existindo, mas exige
     `risk_free_series=None`. Passar os dois é erro. **Hub:** ao subir o pin, o
     backtest que passa `rf` precisa escolher um dos dois.
2. **Cobertura mínima de componentes.** O score é a média dos z-scores
   disponíveis; um ativo com 1 de 6 componentes tinha score mais disperso e caía
   mais nas pontas. `min_component_coverage=0.5` (default) exige
   `ceil(0,5·K)` componentes, com `K` = componentes com algum dado na data.
   `n_low_coverage` por rebalanceamento registra quantos saíram.
3. **`analyze_signal(result)`** (`signal_analysis.py`), sem depender da carteira:
   IC e rank IC (média, ICIR, hit rate, t Newey-West), decaimento do IC por
   horizonte, retorno por quintil e spread Q5−Q1, teste de monotonicidade de
   Patton & Timmermann (2010), Fama-MacBeth univariado e com controles
   (tamanho = log market cap, β, book yield, momentum 12-1; um controle que é
   componente do sinal é omitido), IC por componente, correlação entre
   componentes e autocorrelação do score. O `BacktestResult` passou a guardar
   `prices` e `component_scores` para isso.

## Configuração dos números abaixo

Padrão de pesquisa ([../README.md](../README.md)): universo
IBOV ∪ IBX100 ∪ SMLL com `adtv_min=0`, 2010-01-01 a 2026-10-27,
`quantile=0.2`, `rebalance_freq="BME"`, `trading_cost_bps=10`,
`one_class_per_issuer=True`. L/S com `beta_neutral=True`. CDI no período:
10,1% a.a.; Ibovespa: 6,2% a.a.

> **Pesos fixos.** As carteiras abaixo foram rodadas antes do PR #29, com o
> engine que volta ao peso alvo todo dia (`weight_drift=False` reproduz). Com
> drift, o padrão atual, os L/S perdem 0,9–2,2 p.p. ao ano
> ([diagnostico-engine.md](diagnostico-engine.md)). As estatísticas do sinal
> (IC, quintis, Fama-MacBeth) não dependem disso: usam o score e o preço.

## Carteiras: antes e depois

"Antes" = mesmo universo com rf = 0 e sem cobertura mínima.

| Estilo | LO antes ret / Sharpe | LO agora ret / Sharpe | LO α vs Ibov (t) | L/S β-neutro ret / Sharpe / MDD | L/S α (t) |
|---|---|---|---|---|---|
| Value | 13,2% / 0,55 | 11,7% / 0,07 | 5,8% (1,8) | 12,0% / 0,86 / −42% | 12,3% (3,5) |
| Quality | 10,6% / 0,54 | 10,5% / 0,02 | 3,3% (1,3) | 15,0% / 1,04 / −42% | 15,0% (3,9) |
| Momentum | 15,5% / 0,70 | 15,5% / 0,25 | 8,3% (2,6) | 20,4% / 1,13 / −51% | 20,2% (4,2) |
| Risk | 13,4% / 0,92 | 13,4% / 0,23 | 4,6% (2,1) | 20,1% / 1,33 / −34% | 19,5% (5,0) |

- Descontado o CDI, o excesso das long-only fica entre 0,4% (Quality) e
  5,5% (Momentum) a.a., e nenhum alfa da LO passa de t = 3. As L/S
  beta-neutras têm alfa com t entre 3,5 e 5,0 nos quatro estilos.
- A cobertura mínima só mexe em Value: em média 12,3 nomes por
  rebalanceamento tinham 1 dos 3 yields. Sem eles a LO cai de 13,2% para
  11,7%. Em Quality sai 0,5 nome por mês; Momentum e Risk não mudam.

## Sinal (rank IC mensal, 200 meses, ~129–141 nomes por data)

| Estilo | Rank IC | t (NW) | ICIR a.a. | Spread Q5−Q1 a.a. (t) | Monotônico (p; degraus) | FM t uni / c/ controles | Autocorr. score |
|---|---|---|---|---|---|---|---|
| Value | 0,024 | 2,2 | 0,65 | 12,3% (2,9) | 0,17; 2/4 | 2,9 / 4,1 | 0,96 |
| Quality | 0,069 | 5,5 | 1,51 | 11,5% (2,2) | 0,34; 3/4 | 2,6 / 2,9 | 0,98 |
| Momentum | 0,080 | 6,0 | 1,45 | 21,4% (3,7) | 0,0005; 4/4 | 3,2 / 1,4 | 0,89 |
| Risk | 0,080 | 4,4 | 1,13 | 12,2% (1,6) | 0,09; 4/4 | 1,6 / 1,5 | 0,95 |

Leitura:

- **Momentum** passa em tudo, inclusive o t > 3 de Harvey, Liu & Zhu (2016)
  no spread. Com controles o t cai para 1,4 porque o controle
  `momentum_12m1` é quase o mesmo sinal; não quer dizer sinal fraco. O t do
  IC fica perto de 6 até 6 meses.
- **Quality** tem o IC mais estável (ICIR 1,5, 70% dos meses positivos) e
  todos os componentes significativos, mas Q5 rende menos que Q4 e o
  quintil de baixo rende ~0. O sinal está mais em evitar as piores do que
  em escolher as melhores.
- **Risk** ordena bem (IC t 4,4, quintis crescentes) e a L/S beta-neutra é a
  melhor carteira (Sharpe 1,33). O spread de quintis pesos iguais e o
  Fama-MacBeth não são significativos porque não neutralizam o β: comprado
  em baixo β e vendido em alto β perde nos anos de alta. O prêmio aparece
  depois de ajustar o β.
- **Value** é o mais fraco no IC (t 2,2) e não é monotônico (Q2 > Q3 > Q4),
  mas tem spread t 2,9 e Fama-MacBeth t 4,1 com controles.
  `earnings_yield_fwd` lidera (t 3,1), `fcf_yield_fwd` t 1,9 e
  `ebitda_yield_fwd` t 1,1. No universo por ADTV a ordem era outra (FCF
  liderava, earnings t 1,3): a contribuição dos componentes não é estável.
- **Controles:** em todos os estilos o momentum 12-1 é o controle com maior
  prêmio (t ≈ 4,5–5,4). Tamanho, β e book yield não aparecem.
- **Componentes de Quality** redundantes: `net_margin` × `ebitda_margin`
  0,72 e `roe` × `roic` 0,78.
- **Autocorrelação** de 0,89–0,98 entre meses, com IC acumulado crescendo
  até 6–12 meses: rebalancear mensalmente pode ser mais frequente do que o
  sinal pede.

### Universo por índice contra universo por ADTV (≥ R$8 mi)

Mesmo motor, só troca o universo:

| Estilo | Rank IC t (ADTV → índice) | Spread t | L/S β-neutro Sharpe |
|---|---|---|---|
| Value | 1,1 → 2,2 | 1,6 → 2,9 | 0,42 → 0,86 |
| Quality | 4,0 → 5,5 | 1,6 → 2,2 | 0,35 → 1,04 |
| Momentum | 4,7 → 6,0 | 3,3 → 3,7 | 0,92 → 1,13 |
| Risk | 3,8 → 4,4 | 1,2 → 1,6 | 0,81 → 1,33 |

O universo por índice é ~25% maior (129–141 contra 103–112 nomes) e não tem
os dias de dado errado de GOLL54/AZUL54; os dois efeitos estão misturados.

## Em aberto

- Value: rever a composição (tirar `ebitda_yield_fwd`?) com o log de
  tentativas, para não inflar o resultado escolhendo componentes pelo
  backtest.
- Quality: testar uma versão que só evita o quintil de baixo.
- Risk: spread de quintis e Fama-MacBeth com retorno ajustado ao β.
- Momentum: trocar para `momentum_12m1` e comparar.
- Testar rebalanceamento trimestral nos estilos com autocorrelação > 0,95.
- Próximas etapas da proposta: atribuição aos fatores NEFIN, Deflated Sharpe
  e log de tentativas, grade de robustez.
