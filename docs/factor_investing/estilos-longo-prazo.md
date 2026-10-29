# Estilos de longo prazo: backtests e quebras de performance

- **Status:** Aprendizado
- **Atualizado em:** 2026-10-28

## Configuração

Estilos do Fibery (`Inv-Rsrch-Quant/Definições dos Fatores`, campo `Estilo`):

| Estilo | Componentes |
|---|---|
| Value | `earnings_yield_fwd`, `ebitda_yield_fwd`, `fcf_yield_fwd` |
| Quality | `roe`, `roic`, `gross_margin`, `ebitda_margin`, `net_margin`, `fcf_margin` |
| Momentum | `momentum_6m`, `momentum_12m` |
| Risk | `beta`, `volatility_1m`, `volatility_12m` (menor é melhor) |

Liquidity ficou de fora por ser de curto prazo.

`BacktestConfig`: 2010-01-01 a 2026-10-27, `quantile=0.2`, `rebalance_freq="BME"`,
`adtv_min=8_000_000`, `trading_cost_bps=10`, `one_class_per_issuer=True`,
`price_field="price_close"`.

## Resultados

| Estilo | LO ret / Sharpe / MDD | L/S ret / Sharpe / MDD | β L/S vs Ibov |
|---|---|---|---|
| Value | 13,6% / 0,52 / −56% | 9,2% / 0,58 / −49% | 0,03 |
| Quality | 12,1% / 0,58 / −48% | 3,8% / 0,14 / −81% | −0,36 |
| Momentum | 15,6% / 0,65 / −58% | 18,2% / 0,76 / −43% | −0,33 |
| Risk | 13,5% / 0,87 / −37% | 0,3% / 0,01 / −93% | −0,85 |

## Origem das quebras

1. **Dado errado no factor_zoo.** GOLL54 em 12/06/2025 (+1.847% num dia) e
   AZUL54 em 08–09/01/2026. Estavam na ponta vendida de Quality e Risk:
   −71% e −68% num único dia. Detalhes em
   [../factor_zoo/problemas-conhecidos.md](../factor_zoo/problemas-conhecidos.md).
   Ambos passavam no filtro de ADTV porque giravam muito durante a
   reestruturação.
2. **Beta das L/S.** Dollar-neutral não é beta-neutral: a L/S de Risk é, na
   prática, vendida em Ibovespa e quebra nos anos de alta (2016 −64%,
   2017 −32%, 2019 −13%, 2025). Resolvido com `beta_neutral=True`, ver
   [ls-beta-neutro.md](ls-beta-neutro.md).
3. **Eventos de mercado.** 18/05/2017 (Joesley), março de 2020 (Covid) e
   outubro de 2026 (05/10 alta e 26/10 queda generalizada de papéis
   domésticos, nos dias seguintes aos turnos da eleição; queda de 30–65% em
   dezenas de nomes, que se manteve nos dias seguintes — confirmar no
   Bloomberg). Afetam principalmente as long-only.

## Preço: `price_close` vs `price_close_split_adj`

`price_close` é ajustado por retorno total (dividendos); `price_close_split_adj`
só por desdobramentos. Com `split_adj` as long-only perdem 5–6 p.p. a.a.
(Value 13,6% → 7,3%) com a mesma vol e o mesmo drawdown. O default do motor
(`price_close`) é o correto. As quebras aparecem nas duas séries.
