# Universo pelos membros de índices B3

- **Status:** Implementado (`BacktestConfig.universe_index`, PR #22; Hub:
  Persevera-Asset-Mgmt/PerseveraHub#17). Padrão do `BacktestConfig` desde o
  PR #30: `universe_index=("IBX100", "SMLL")`, `adtv_min=0`.
- **Atualizado em:** 2026-11-04

## Motivação

O filtro antigo (ADTV de 21 dias ≥ R$8 mi, valor fixo em reais nominais) deixa
entrar empresas em situação especial que giram muito, como GOLL54 e AZUL54
durante as reestruturações, e fica mais frouxo com a inflação (R$8 mi em 2010
não é R$8 mi em 2026). As metodologias dos índices B3 já filtram liquidez e
situação especial.

Com pesos com drift (PR #29) isso deixou de ser só uma distorção: vendido em
GOLL54 durante a alta de jun/2025, o Quality L/S beta-neutro no universo por
ADTV perde tudo (drawdown de −110%). Os preços estão corretos (confirmado em
2026-11-04), então a perda é real; a saída é não ter o papel no universo. Por
isso o universo por índice virou o padrão.

## Uso

```python
BacktestConfig(...)  # padrão: universe_index=("IBX100", "SMLL"), adtv_min=0
BacktestConfig(..., universe_index=None, adtv_min=8_000_000)  # pool por denominação
```

- Antes de 19/06/2008 (início do SMLL) o padrão é só o IBX100, e antes de
  03/01/2000 não há composição: o backtest falha.
- Com `universe_index=None`, defina `adtv_min`; com 0, entram papéis sem
  liquidez.

- `universe_index`: um campo da `b3_index_composition` (`IBOV`, `IBX100`,
  `IBX50`, `IBRA`, `SMLL`, `MLCX`) ou vários (união). Substitui o pool por
  denominação; com `codes`, faz a interseção.
- Em cada rebalanceamento o investível é quem é membro no pregão mais recente
  até a data (sem olhar para frente), cruzado com preço recente, ADTV
  (`adtv_min=0` desliga) e `one_class_per_issuer`.
- `index_max_age_days` (default 10): composição mais velha que isso faz o
  backtest falhar, em vez de congelar o universo em silêncio.
- Diagnóstico por rebalanceamento: `n_index_members`.
- Os códigos da tabela já são os canônicos do factor_zoo (a carga e o
  `aliases --apply` traduzem, inclusive o código antigo antes da vigência).

## Hub

Tela Factor Investing (PerseveraHub#17, persevera-tools na revisão
`07fa020`):

- Campo "Índices B3" (múltipla escolha; vazio = todas as ações BRL), que vira
  `universe_index` em ordem canônica, para a chave do cache não depender da
  ordem do clique.
- A API recusa índice inválido e início antes da primeira composição do
  índice: IBOV e IBX100 em 03/01/2000, IBX50 em 01/11/2002, SMLL e MLCX em
  19/06/2008, IBRA em 23/07/2012.
- O ADTV continua valendo junto com o índice; com índice escolhido e ADTV > 0
  a tela avisa "Soma com o índice; 0 = só o índice".
- O rótulo ganha ` · IBX100+SMLL` e a aba de rebalanceamentos mostra
  "Membros do índice" (`n_index_members`).
- Conferência: Value LO, IBX100 ∪ SMLL, ADTV 0, pelo código do Hub deu
  13,17% a.a. e 27,7 nomes em média, igual ao motor.

## Resultados (2010-01-01 a 2026-10-27)

Top 20%, rebalanceamento mensal, 10 bps de custo; L/S beta-neutro.
Universo IBX100 ∪ SMLL sem filtro de ADTV contra ADTV ≥ R$8 mi. Rodado em
2026-10-29, antes do limite de idade (PR #26) e do drift (PR #29); os números
com o engine atual estão em [diagnostico-engine.md](diagnostico-engine.md).

| Estilo | Livro | ADTV: ret / Sharpe / MDD | Índice: ret / Sharpe / MDD | Nomes (ADTV → índice) |
|---|---|---|---|---|
| Value | LO | 13,6% / 0,52 / −56% | 13,2% / 0,52 / −55% | 22 → 28 |
| Value | L/S | 8,2% / 0,53 / −52% | 11,4% / 0,82 / −38% | 44 → 55 |
| Quality | LO | 12,1% / 0,58 / −48% | 10,5% / 0,51 / −48% | 22 → 28 |
| Quality | L/S | 9,1% / 0,43 / −61% | 14,9% / 1,02 / −42% | 44 → 55 |
| Momentum | LO | 15,6% / 0,65 / −58% | 15,4% / 0,66 / −55% | 22 → 28 |
| Momentum | L/S | 18,5% / 0,91 / −44% | 20,4% / 1,09 / −51% | 44 → 56 |
| Risk | LO | 13,4% / 0,87 / −37% | 13,3% / 0,88 / −37% | 22 → 27 |
| Risk | L/S | 16,0% / 0,85 / −51% | 20,1% / 1,30 / −33% | 44 → 54 |

- As quebras dos papéis em reestruturação somem: em 12/06/2025 a Quality L/S
  passa de −57% no dia para +0,7%, a Risk L/S de −35% para +0,8%; em
  09/01/2026 a Momentum L/S de −7,8% para +0,4%.
- Long-only praticamente igual; giro menor (Quality 11% → 6% por mês).
- Universo médio de 147 membros (113 a 202).
- A comparação mistura dois efeitos: tirar os papéis em reestruturação e mudar
  o tamanho do universo. Momentum L/S teve drawdown maior (−51% contra −44%).
