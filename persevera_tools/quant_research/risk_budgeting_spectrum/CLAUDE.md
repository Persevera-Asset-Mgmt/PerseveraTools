# Motor RBS – Espectro de Alocação (Risk Budgeting paramétrico)

## Premissa
A alocação em caixa/CDI curto é uma decisão de liquidez, não de investimento,
e por isso não entra no modelo. Exceção: no livro Offshore, T-bills entram
como classe "Pós-fixado", com Max Weight.

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
