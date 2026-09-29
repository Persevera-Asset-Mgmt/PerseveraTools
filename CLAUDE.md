# Motor RBS – Espectro de Alocação (Risk Budgeting paramétrico)

## Fonte dos inputs: Fibery, space Inv-Rsrch-Quant
- O modelo baseia-se no fato de que a decisão de alocação em caixa/CDI curto é uma decisão de liquidez, não investimento. Por isso não entra no modelo.
- Calibração de Mercado: σ min/max, N Profiles, Min Weight Threshold. Hoje só existe  "Calibração - 2026-05-04" (Rascunho).
- Classes de Ativo (13) + Parâmetros de Classe: Default Vol, Max Weight, Max RC por classe.
- RC Target por Bucket: RC P1, RC P10 e Curvatura por bucket (RF, RV, Alternativos).
- Correlações: somente Cross-Classe, com a matriz completa (78 pares, incl. intra-bucket).
  Convenção: Classe A no bucket de menor índice (RF < Alt < RV). Intra-Classe e
  Macro-Bucket foram apagadas (não eram lidas pelo motor).

## Problemas conhecidos na calibração
- Long & Short: os 12 pares de correlação são SIMULADOS (criados em 2026-09-27,
  public-id 68–79 na Cross-Classe), a substituir por valores estimados.
  Max Weight continua 100% (MM tem 20%); o peso de L&S oscila entre perfis
  (11%, 5%, 0, 0, 0, 11%, 15%, 19%, 18%, 3%).
- Curvaturas diferentes por bucket (RF 0,5 / Alt 1,2 / RV 1,2): a soma bruta cai
  para 0,71–0,93 nos perfis P2–P9 e o motor renormaliza, então o γ efetivo difere
  do cadastrado.
- Max RC só preenchido em Bitcoin (15%) e nunca atinge o limite (RC máx ~10%).
- RC realizado de RF fica ~10 p.p. abaixo do alvo nos perfis intermediários; o motor
  só checa vol e limites para dizer que convergiu, não o erro de RC.

## Regras de trabalho
- Não escrever nem apagar nada no Fibery; exclusões de campos eu faço manualmente.
- Antes de alterar código, explicar o diagnóstico e propor a mudança.
