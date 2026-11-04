# risk_budgeting_spectrum: estado da calibração

- **Status:** Aprendizado
- **Atualizado em:** 2026-09-28 (migrado do `CLAUDE.md` da raiz em 2026-11-04,
  sem nova verificação no Fibery)

## Calibração vigente
Só existe "Calibração - 2026-05-04", em status Rascunho.

As tabelas de correlação Intra-Classe e Macro-Bucket foram apagadas do Fibery
porque o motor não as lia. A matriz completa está na Cross-Classe.

## Problemas conhecidos

### Long & Short: correlações simuladas
Os 12 pares de correlação de L&S são **simulados** (criados em 2026-09-27,
public-id 68–79 na Cross-Classe) e precisam ser substituídos por valores
estimados.

Max Weight continua em 100% (Multimercado tem 20%). O peso de L&S oscila entre
os perfis P1–P10: 11%, 5%, 0, 0, 0, 11%, 15%, 19%, 18%, 3%.

### Curvaturas diferentes por bucket
Com RF 0,5, Alt 1,2 e RV 1,2, a soma bruta dos RC alvo cai para 0,71–0,93 nos
perfis P2–P9. O motor renormaliza, então o γ efetivo difere do cadastrado.

### Max RC quase sem uso
Só está preenchido em Bitcoin (15%), e o limite nunca é atingido (RC máximo
~10%).

### Erro de RC não entra no critério de convergência
O RC realizado de RF fica ~10 p.p. abaixo do alvo nos perfis intermediários.
O motor só checa vol e limites para declarar convergência, não o erro de RC.
