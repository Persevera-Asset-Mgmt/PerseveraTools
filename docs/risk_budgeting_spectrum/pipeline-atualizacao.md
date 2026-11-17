# RBS: pipeline de atualização da calibração (proposta)

- **Status:** Proposta (nada implementado além das peças listadas em "O que já existe")
- **Atualizado em:** 2026-11-17
- **Contexto:** a calibração do Fibery congela vols e correlações para o espectro ser
  reproduzível ([arquitetura.md](arquitetura.md)). Hoje usamos **correlação histórica de
  longo prazo** e **vols por EWMA**. Falta o processo que renova esses inputs.

## O que já existe

| Peça | O que faz | Lacuna |
|---|---|---|
| `estimation.py` + `scripts/rbs_offshore_calibracao.py` | Lê proxies de `indicadores`, estima vols e correlações (par a par, PSD de Higham), gera CSVs no formato do Fibery e compara com a calibração de referência | Vol é **desvio-padrão simples**, não EWMA; o script é só Offshore na prática e não roda a checagem de robustez |
| `scripts/rbs_robustez.py` | Perturba vols (e, com `--sigma-corr`, correlações) e acusa perfis instáveis; sugere σ máximo | Roda à mão |
| `loaders/fibery.py` | Lê a calibração (status, livro) e valida σ máx contra o teto viável | Só leitura |
| Hub | Usa a calibração mais avançada disponível | Cache de 1 h |

## Fluxo proposto

1. **Estimar** (por livro): vols EWMA e correlação de longo prazo a partir dos proxies.
   Mesmo código para os dois livros.
2. **Comparar com a calibração vigente**: variação de cada vol, maior variação de
   correlação, ajuste PSD, cobertura (início, fim, semanas) de cada proxy. Alertas:
   vol que muda mais de X% (ex.: 30%), proxy com dado velho, par com poucas semanas.
3. **Rodar o espectro** com os inputs novos e comparar com o vigente: giro por classe
   (p.p.) em P1, P5 e P10. Alerta se alguma classe mexer mais de Y p.p.
4. **Checagem de robustez** da candidata (vol + correlação) e sugestão de σ máximo.
   Perfil instável bloqueia a aprovação (regra do CLAUDE.md do módulo).
5. **Relatório** (um arquivo por livro) com os itens 2 a 4, para decisão humana.
6. **Gravar no Fibery** como nova Calibração em *Rascunho* (parâmetros + 78 correlações).
   Pela regra do repositório, escrita no Fibery exige permissão explícita por ação; um script
   que grave só entra por PR revisado. Até lá, o passo 6 gera os CSVs de importação.
7. **Aprovar** (muda o status no Fibery, manual). O Hub passa a usar a nova calibração
   quando o cache de 1 h expirar.

## Cadência sugerida

- **Vols (EWMA):** mensal, ou quando um alerta do item 2 disparar. Vol recente muda rápido,
  mas mexer na carteira todo mês gera giro; por isso o item 3 mede o giro antes de aprovar.
- **Correlações de longo prazo:** semestral. Mudam devagar; renovar com mais frequência
  só adiciona ruído.
- **Estrutura** (classes, proxies, tetos de peso, RC targets): sob demanda, com decisão.

## Pontos a decidir

1. ~~Parâmetros do EWMA~~ (resolvido): a página CMA do Hub usa `calculate_ewma_volatility(decay=0,995)`
   sobre preços diários (retorno simples, anualizado por √252; meia-vida de ~138 dias úteis) e
   correlação de retornos semanais simples (`resample("W")`) na janela de 15 anos, par a par,
   sem correção PSD. `estimation.ewma_vols` e `estimation.cma_correlation` reproduzem isso.
   O estimador antigo do `estimation.py` (desvio-padrão semanal de log-retorno, PSD de Higham)
   fica como alternativa e **não** é o que gerou os números atuais. Pendente: confirmar que a
   calibração atual foi gerada exatamente assim (`scripts/rbs_conferir_inputs.py`).
2. **Suavização da vol**: o EWMA já pesa o recente; a pergunta é se misturamos com a vol de
   longo prazo (σ = w·σ_EWMA + (1 − w)·σ_LP) para reduzir o giro entre calibrações. Dá para
   medir com o histórico (reestimar mês a mês e comparar o giro dos pesos), mas precisa do
   banco, que não está acessível a partir desta sessão.
3. **Limites de alerta** X e Y acima.
4. **Quem aprova** e onde fica o registro da decisão (campo de descrição da calibração).
5. **Inconsistência de input**: vol do Bitcoin 34,9% no Onshore contra 64,7% no Offshore
   (ver avaliacao-modelo.md, seção 12.4).
