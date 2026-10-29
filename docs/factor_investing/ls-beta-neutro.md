# Long/short beta-neutro

- **Status:** Implementado (Persevera-Asset-Mgmt/PerseveraTools#14)
- **Atualizado em:** 2026-10-28

## Problema

O L/S dollar-neutral (1 comprado, 1 vendido) herda o beta das pontas. Em
estilos defensivos a ponta comprada tem beta baixo e a vendida alto, então o
livro fica vendido em mercado: β vs Ibov de −0,85 em Risk e −0,36 em Quality.

## Construção

`BacktestConfig(construction="long_short", beta_neutral=True)`:

- β por ativo de `beta_field` (default `beta`, Bloomberg `BETA_RAW_OVERRIDABLE`)
  na data do rebalanceamento; ativo sem β recebe a mediana dos pontuados.
- β de cada ponta = média ponderada dos β dos seus ativos.
- Pontas redimensionadas para `g_L·β_L = g_S·β_S` com `g_L + g_S = 2`, mantendo
  os pesos relativos dentro de cada ponta.
- β de uma ponta ≤ 0 → livro fica dollar-neutral e o evento
  `beta_neutral_skipped` é registrado.
- Diagnóstico por rebalanceamento: `beta_long`, `beta_short`, `gross_long`,
  `gross_short`, `net_beta`.

## Resultados (mesma configuração de [estilos-longo-prazo.md](estilos-longo-prazo.md))

| Estilo | Retorno a.a. | Sharpe | Max DD | β realizado |
|---|---|---|---|---|
| Value | 9,2% → 8,2% | 0,58 → 0,53 | −49% → −52% | 0,03 → −0,06 |
| Quality | 3,8% → 9,2% | 0,14 → 0,43 | −81% → −61% | −0,37 → −0,08 |
| Momentum | 18,2% → 18,5% | 0,76 → 0,91 | −43% → −44% | −0,33 → −0,13 |
| Risk | 0,3% → 16,1% | 0,01 → 0,85 | −93% → −51% | −0,85 → −0,02 |

Risk fica em média 1,45 comprado / 0,55 vendido (β_L 0,51, β_S 1,35).
Quality ainda inclui o dia de GOLL54; sem ele, Sharpe 0,96 e MDD −51%.

## Em aberto

- β bruto: ativos com β perto de zero (TAEE11 chegou a 0,02 em 2012) inflam a
  ponta comprada. Alternativa: β ajustado de Blume (0,67·bruto + 0,33).
- Mesmo neutra, a Risk L/S perde 37% em 2016.
