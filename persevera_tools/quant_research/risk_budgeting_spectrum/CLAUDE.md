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

## Recalibração: checagem de robustez antes de aprovar
Com vols um pouco diferentes o P10 pode cair em modo `penalty` (medido em
[avaliacao-modelo.md](../../../docs/risk_budgeting_spectrum/avaliacao-modelo.md), seção 4).
Antes de passar uma calibração a "Aprovada", rodar:

    python scripts/rbs_robustez.py --livro <Onshore|Offshore> --status <status> --jobs 4

Ele refaz o espectro com vols perturbadas (× exp(N(0; 0,15)), 6 sorteios) e lista os
perfis instáveis (não convergem no caso base ou em algum sorteio válido); sai com código
1 se houver algum. Sorteio válido: aquele em que o σ máximo da calibração ainda cabe no
teto de vol viável do universo perturbado; se o teto cai abaixo dele, é calibração
inválida nesse cenário e o sorteio é ignorado (`--incluir-inviaveis` o mantém). Regra: **perfil instável → reduzir o σ máximo até convergir antes de
aprovar** (`--sugerir-sigma-max` testa valores menores, em passos de 0,5 p.p.). Se nenhum
valor resolver, revisar os tetos de peso ou as vols; não aprovar com perfil instável sem
registrar a decisão. Subir o teto de peso das ações não é o caminho (concentra o P10).

Decisão de 2026-11-17: o P10 fica como está na calibração vigente (σ máximo 11,5% no
Onshore e 14% no Offshore); a regra acima vale a partir da próxima recalibração.
O código está em `risk_budgeting_spectrum/robustness.py`.

## Estado da calibração
Problemas conhecidos e números da calibração atual ficam em
[docs/risk_budgeting_spectrum/calibracao.md](../../../docs/risk_budgeting_spectrum/calibracao.md).
Conferir a data de `Atualizado em` antes de confiar nos números.
