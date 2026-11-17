# Motor RBS – Espectro de Alocação (Risk Budgeting paramétrico)

## Premissa
A alocação em caixa/CDI curto é uma decisão de liquidez, não de investimento,
e por isso não entra no modelo, nos dois livros. Decisão de 2026-11-17: no
Offshore os T-bills também ficam fora do otimizador e servem só para balizar o
risco total (colchão de caixa, a desenhar). A classe "RF Pós-fixado" foi removida
do Fibery em 2026-11-17 (classe, parâmetro e 13 correlações): o Offshore tem 13
classes e 78 pares, como o Onshore.

## Livros
Onshore (BRL) e Offshore (USD) usam o mesmo motor e as mesmas tabelas do
Fibery, separados pelo campo **Livro** em Calibração de Mercado e Classes de
Ativo (ausente/vazio = Onshore). `load_from_fibery(livro=...)`, default
Onshore. Decisões do offshore em
[docs/risk_budgeting_spectrum/offshore.md](../../../docs/risk_budgeting_spectrum/offshore.md).

## Inputs: Fibery, space Inv-Rsrch-Quant
- **Calibração de Mercado:** σ min/max, N Profiles, Min Weight Threshold.
- **Classes de Ativo (13 onshore) + Parâmetros de Classe:** Default Vol, Max
  Weight, Max RC e RC Intra-Bucket (opcional) por classe.
- **RC Target por Bucket:** RC P1, RC P10 e Curvatura por bucket (RF, RV,
  Alternativos).
- **Correlações:** só Cross-Classe, com a matriz completa (78 pares, incluindo
  intra-bucket). Convenção: Classe A no bucket de menor índice (RF < Alt < RV).

## Estado da calibração
Problemas conhecidos e números da calibração atual ficam em
[docs/risk_budgeting_spectrum/calibracao.md](../../../docs/risk_budgeting_spectrum/calibracao.md).
Conferir a data de `Atualizado em` antes de confiar nos números.
