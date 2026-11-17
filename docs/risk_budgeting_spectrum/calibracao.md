# risk_budgeting_spectrum: estado da calibração

- **Status:** Aprendizado
- **Atualizado em:** 2026-11-17 (calibração vigente, Max RC e convergência
  reverificados no Fibery e no código; os pesos de Long & Short abaixo são da
  calibração antiga e não foram reconferidos). Visão geral em
  [arquitetura.md](arquitetura.md).

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
Só está preenchido em Bitcoin (20% no Onshore; Max Weight 8%). Na calibração
antiga era 15% e o limite nunca era atingido (RC máximo ~10%); não reconferido
com a calibração atual.

### Erro de RC por bucket (resolvido)
Antes de `d73ef48` a convergência checava só vol e limites, e o RC de RF ficava
~10 p.p. abaixo do alvo nos perfis intermediários. Hoje `_is_converged` exige o
RC por bucket dentro de 1e-3; perfis que só fecham por penalidade aparecem como
não convergidos (`rc_mode_per_profile = "penalty"`).
