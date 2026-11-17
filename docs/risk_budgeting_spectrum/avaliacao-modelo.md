# RBS: avaliação do modelo com a calibração real (Onshore e Offshore)

- **Status:** Aprendizado (medição; nada do motor foi alterado)
- **Atualizado em:** 2026-11-17
- **Base:** "Calibração Onshore - 2026-10-14" (Rascunho), 13 classes, σ 1,9%–11,5%,
  10 perfis, peso mínimo 0,5% (seções 1 a 8); "Calibração Offshore - 2026-11-09"
  (Rascunho), 14 classes, σ 2%–14%, peso mínimo 2% (seção 9). Motor do PerseveraTools `main` @ `7f18df5`.
  Visão da arquitetura em [arquitetura.md](arquitetura.md).

## Método e limites

- Parâmetros, RC targets e as 78 correlações lidos do Fibery em 2026-11-17 (somente
  leitura) e montados numa `SpectrumConfig` à mão, porque o ambiente não tinha token
  do Fibery. A ordem das classes seguiu o catálogo por `fibery/rank`; o loader pode
  ordenar diferente, o que muda só sementes e pontos iniciais. Os 10 perfis
  convergiram e baterem os RC targets, mas **os pesos não foram conferidos contra o
  que a página do Hub mostra**.
- Sensibilidade: 3 cenários dirigidos e 6 perturbações aleatórias (vol de todas as
  classes × lognormal com σ = 15%; correlação fixa). Seis sorteios é pouco: as
  conclusões são de ordem de grandeza, não de intervalo de confiança.
- Retorno implícito: `μ = λ·Σw` com Sharpe da carteira fixado em 0,3 (arbitrário). O
  que importa é a ordem entre classes. Não há retorno esperado de referência no Hub
  (o CMA do Hub guarda só séries históricas), então não houve comparação com uma
  previsão.
- Sem backtest, sem retornos históricos, sem walk-forward.

## Resultado base

Todos os perfis convergem, em modo `hard`, com σ realizado = σ alvo e RC por bucket
exato. Faixa viável do universo: 1,38%–12,80%.

| Bucket (peso %) | P1 | P3 | P5 | P7 | P9 | P10 |
|---|---|---|---|---|---|---|
| Renda Fixa | 100 | 90 | 74 | 49 | 33 | 17 |
| Alternativos | 0 | 4 | 12 | 23 | 18 | 20 |
| Renda Variável | 0 | 6 | 14 | 28 | 49 | 63 |

## O que a medição mostrou

### 1. O perfil 1 não é degenerado (hipótese anterior refutada)
σ mínimo de 1,9% fica acima do piso viável (1,38%). O P1 tem 7 classes de RF: Crédito
DI 37%, Fundo Crédito GI 21%, Inflação Curta 19%, Crédito Inflação 8%, Fundo Crédito
HY 7%, Inflação Longa 4%, Pré Longa 4%. Mas 72% do P1 é crédito (DI, GI, HY, Crédito
Inflação) e 28% está em classes sem histórico.

### 2. Classes sem histórico pesam muito nos perfis conservadores
"RF Fundo Crédito GI" e "HY" têm vol e correlações de premissa (Fonte = Premissa).
Peso somado nelas por perfil: 28%, 32%, 39%, 33%, 21%, depois 0 (P1 a P5). Peso em
crédito no sentido amplo: 73%, 63%, 54%, 46%, 35%, 15%, ... 0 (P10).

### 3. Os pesos por bucket são robustos; os pesos por classe, não
Nos 9 cenários, a variação máxima do peso de um bucket (entre perfis) foi de 3 a 8
p.p. na maioria dos casos (até 11–16 p.p. no pior sorteio e, para RV, 12 p.p. com a vol de RV ×1,25), enquanto a de uma classe
chegou a 10–34 p.p. O orçamento de RC por bucket fixa o bucket; a composição
dentro dele fica a cargo de um objetivo secundário e muda muito com vol e correlação.

| Cenário | Convergiu | máx\|Δ\| bucket (RF/ALT/RV, p.p.) | máx\|Δ\| classe |
|---|---|---|---|
| Vol de crédito ×1,5 (DI, GI, HY, Créd. Inflação) | 10/10 | 5,4 / 5,6 / 0,3 | 33,6 (Inflação Curta) |
| Vol de RV ×1,25 | 10/10 | 4,9 / 8,0 / 12,3 | 10,3 |
| Correlação em direção a 1 (0,85ρ + 0,15) | 10/10 | 5,0 / 6,2 / 3,0 | 22,0 |
| 6 sorteios de vol (σ = 15%) | 8/10 a 10/10 perfis | 2–11 / 3–16 / 2–15 | 10–25 |

### 4. O perfil 10 está na borda da faixa viável
σ máximo de 11,5% é 90% do máximo viável (12,8%), com RV limitada a 35% por classe.
O P10 base tem só 4 posições (Inflação Longa 17%, Ouro 20%, RV Brasil 33%, RV EUA
30%), HHI 0,27 e número efetivo de apostas 2,2 (contra 4,7 a 5,3 nos perfis P1 a P4).

Nas 6 perturbações aleatórias de vol, 4 tiveram o P10 em modo `penalty` (e o P9 em
uma, onde o σ máximo passou do teto viável: 11,5% contra 11,06%). Nos outros casos o
teto viável era de 12,1% a 13,5%, ainda acima do σ máximo, e mesmo assim o P10 falhou
em 3 deles. Em todos os casos de falha o σ realizado bateu o alvo; o que falha é o RC
por bucket (ex.: sorteio 2: RF 4,0 / Alt 30,7 / RV 65,3, contra alvo 5 / 25 / 70).

**Teste de isolamento** (4 sorteios que falham, relaxando uma restrição por vez; P10
volta a `hard`?):

| Variante | Sorteio 2 | Sorteio 3 | Sorteio 5 | Sorteio 6 | Resolve |
|---|---|---|---|---|---|
| Base | não | não | não | não | 0/4 |
| σ máximo 11,0% | sim | não | sim | não | 2/4 |
| σ máximo 10,5% | sim | não | sim | sim | 3/4 |
| Max Weight de RV 50% (era 35%) | sim | não | sim | não | 2/4 |
| RC P10: RV 60% / Alt 35% | não | não | sim | não | 1/4 |
| RC P10: RV 60% / RF 15% | não | não | não | não | 0/4 |

Leitura: **o que importa é o teto de volatilidade**, não o orçamento de RC. Reduzir o σ
máximo ou afrouxar o teto por classe da RV resolve a maior parte; mexer no RC alvo
quase não ajuda (a variante com RF 15% deixa 6 perfis em `penalty`, contra 5 na base, somando os 4 sorteios). O sorteio 3 é inviável de fato (teto viável
11,06% < 11,5%) e continua falhando mesmo com σ máximo de 10,5%. Não distingo
inviabilidade real de falha numérica do SLSQP perto da borda: as duas aparecem como
`penalty`. Nenhuma regra simples do tipo "σ máximo ÷ teto viável" separa os casos
(sorteios 1 e 4, com razão 0,92 e 0,88, convergiram; 2, 5 e 6, com 0,85 a 0,95, não).

### 5. O "RC alvo" por classe que a página mostra não é uma restrição
O motor impõe RC só por bucket; a divisão uniforme dentro do bucket é objetivo
secundário e é abandonada com frequência. No P5, Crédito DI tem alvo 7,2% e
realizado 0,0%; no P10, Ouro tem alvo 6,25% (Alternativos 25% ÷ 4 classes) e
realizado 25%, com Multimercado, Long & Short e Bitcoin zerados. A coluna "Δ RC" do
Detalhe do perfil pode sugerir erro onde não há.

### 6. Concentração de risco por classe e blocos correlacionados
RV Brasil responde por 32% do risco no P8, 36% no P9 e 42% no P10 (com RV EUA, 70%
do bucket). Pré Longa, Inflação Curta, Inflação Longa e Crédito Inflação têm
correlações de 0,76–0,83 entre si e somam 35%–62% do peso nos perfis P1 a P6: são
em boa parte a mesma aposta de juros.

### 7. Rotatividade entre perfis vizinhos
Entre 13 e 24 p.p. por passo (soma de |Δw|/2), maior no P9→P10 (24 p.p.). Multimercado,
Long & Short e Bitcoin entram no P2–P3 e saem no P9–P10 (Bitcoin 8% no P9 → 0 no
P10). O padrão é regular, não errático; o salto maior vem de o P10 ser um canto.

### 8. Retorno implícito (Sharpe da carteira = 0,3)
Sharpe implícito por classe = 0,3 × correlação da classe com a carteira. No P5:
RV Brasil 0,24, Inflação Longa 0,23, Crédito Inflação 0,23, Pré Longa 0,22,
Multimercado 0,21, L&S 0,19, RV EUA 0,18, HY 0,11, Ouro 0,10, GI 0,08, Bitcoin 0,07,
Crédito DI 0,04. A carteira mantém 21% em GI e 21% em HY com Sharpe implícito 2 a 5
vezes menor que o de juros e bolsa; no P10 o implícito de Crédito DI é negativo
(−0,02). Isso é consequência do orçamento por bucket (a RF tem de absorver seu RC com
as classes que existem), não de uma visão sobre crédito. **Só vale como diagnóstico**:
sem retornos esperados de referência, não dá para dizer que está errado.

## Leitura

A parte robusta do modelo é a que o Comitê de fato escolhe: o orçamento de risco por
bucket e a curva de σ. A parte frágil é a composição dentro de cada bucket e as bordas
do espectro (P10), onde o resultado depende de vols e correlações que, para GI e HY,
são premissas.

## Opções para discutir com o Comitê (nada implementado)

1. **Composição dentro do bucket:** preencher `RC Intra-Bucket` (campo já existe no
   Fibery, vazio no Onshore) ou orçar por Conjunto.
2. **Classes sem histórico:** teto de peso para GI e HY, vol de estresse, ou proxy.
3. **Borda do espectro:** σ máximo menor (≈ 10,5%–11%) ou Max Weight de RV maior (teste de isolamento, seção 4). Mexer só no RC alvo do P10 não resolve.
4. **Página:** rotular o RC alvo por classe como indicativo (ou ocultar o Δ RC por
   classe) e mostrar o número efetivo de apostas.
5. **Retorno esperado:** se o Comitê tiver premissas de retorno por classe, comparar
   com o retorno implícito acima.

## 9. Offshore (calibração 2026-11-09)

Mesmo método. Dados lidos do Fibery em 2026-11-17: 14 classes, 91 correlações, RC
Intra-Bucket só em Ações (60/22/12/6), RC targets RF 100% → 5% (γ 0,5), Alt 0% → 15%
(γ 1,2), RV 0% → 80% (γ 1,2), σ 2%–14%, 10 perfis, peso mínimo 2%. **Validação:** o
espectro base reproduz a tabela "Reestimação" do [offshore.md](offshore.md) (RF/Alt/RV
no P10 de 13/18/69; Ações EUA 41%). Faixa viável 0,75%–17,35%: o σ máximo é 81% do teto
(Onshore: 90%). O caso base levou ~145 s, mais que o Onshore (~26 s).

Todos os 10 perfis convergem em modo `hard`.

| Bucket (peso %) | P1 | P3 | P5 | P7 | P9 | P10 |
|---|---|---|---|---|---|---|
| Renda Fixa | 100 | 88 | 75 | 59 | 33 | 13 |
| Alternativos | 0 | 7 | 10 | 11 | 15 | 18 |
| Renda Variável | 0 | 6 | 16 | 30 | 52 | 69 |

### O que aparece no Offshore

1. **O T-bill quase não entra.** "RF Pós-fixado" (vol 0,29%, Max Weight 40%) tem 12% no
   P1 e **0% de P2 em diante**. Como contribui quase nada ao risco, o orçamento de RC
   não o atrai; o P1 se apoia em Treasury Curta (39%), Inflação (21%), MBS (9%) e
   Treasury Longa (7%). A decisão registrada de "T-bills dentro do otimizador" tem
   efeito só no P1.
2. **O único RF do P10 é crédito HY.** O RF do P10 (13% do peso, 5% do risco) é só
   "RF Crédito HY" (12,7%), com correlação de 0,60–0,71 com as ações. De P7 a P9 o HY
   fica em 14,3%–15,0% do peso, junto ao teto de 15% (no P10, 12,7%). O bucket "Renda Fixa"
   deixa de ser diversificador nas pontas.
3. **Concentração maior que no Onshore.** Ações EUA respondem por 41% do risco no P8,
   44% no P9 e 47% no P10; o número efetivo de apostas é 2,3, 2,2 e 2,2 nesses perfis
   (Onshore: 3,2, 3,1 e 2,2). A primeira componente principal explica 68% da variância
   não ponderada (Onshore: 50%): o universo tem pouca diversificação de fato.
4. **Entra e sai.** Systematic Trend: 0 → 6,7 → 9,1 → 5,2 → 0 → 0 → 8,1 → 0 → 0
   (quatro trocas). Treasury Longa 18% no P7 e 0 no P8; Crédito IG 28% no P8 e 0 no
   P10. Rotatividade de 12 a 27 p.p. por passo (maior: P7→P8, 27,5). O piso de 2%
   amplifica os saltos (já registrado no `offshore.md`); EM e Imobiliário ficam zerados
   nos perfis baixos por ficarem abaixo dele, o que desfaz o RC Intra-Bucket de RV.
5. **Sensibilidade parecida com a do Onshore.** Bucket: variação máxima de 0,4 a 8,8 p.p.
   (até 13,8 em RV com a vol de RV ×1,25); classe: 10 a 28 p.p. Em 2 dos 6 sorteios, o
   P10 passou a `penalty`; em nenhum outro perfil.
6. **Retorno implícito (Sharpe da carteira 0,3):** no P5 o Sharpe implícito fica entre
   0,18 e 0,23 para Crédito (IG, HY), MBS e ações, e é baixo para diversificadores:
   Pós-fixado 0,01, Systematic Trend 0,04, Bitcoin 0,06, Treasury Curta 0,10, Ouro 0,12,
   Treasury Longa 0,13. No P10, Treasury Longa e Curta ficam negativas (−0,04 e −0,03),
   o modelo as zera. Bitcoin implícito de 8% de excesso a.a. no P10 (Sharpe 0,13, vol
   65%). Só diagnóstico, como no Onshore: sem retorno esperado de referência.

### Offshore sem T-bill (decisão de 2026-11-17)

Mesma rodada (10 cenários) com a classe "RF Pós-fixado" removida do universo: 13
classes, 78 correlações. Faixa viável 1,19%–17,35% (com a classe: 0,75%–17,35%), então
o σ mínimo de 2% continua válido.

- **Só o P1 muda.** Do P2 ao P10 os pesos são idênticos aos do caso base, porque a
  classe já estava zerada. O P1 passa a ter Treasury Curta 52,5% (antes 39,0%),
  Inflação 25,1% (20,7%), MBS 12,4% (9,3%), Treasury Longa 5,7% (7,3%) e Crédito IG
  4,3% (7,3%); HY zera (4,0%). Fica mais concentrado: HHI 0,36 (0,23) e Treasury
  Curta com 31% do risco.
- Sensibilidade e falhas iguais às do caso com T-bill: nos 6 sorteios, o P10 em
  `penalty` nos sorteios 3 e 4, como antes.
- O colchão de caixa é uma camada à parte (não implementada): com fração `c` em
  T-bill, o risco da carteira é aproximadamente `(1 − c)·σ` do perfil, mantendo a
  composição do perfil. Exemplo: `c` = 35% sobre o P7 (σ 7,3%) leva o risco a ~4,75%, o σ
  do P5, mas com a composição do P7, não a do P5.

### Onshore × Offshore

| | Onshore | Offshore |
|---|---|---|
| Perfis convergidos (base) | 10/10 | 10/10 |
| σ máximo ÷ teto viável | 0,90 | 0,81 |
| Maior classe em risco (P10) | RV Brasil 42% | Ações EUA 47% |
| Número efetivo de apostas (P10) | 2,2 | 2,2 |
| Classe sem histórico relevante | GI e HY (28–39% no P1–P4) | nenhuma (todas com índice de histórico longo; Bitcoin e Trend mais curtos) |
| Rotatividade entre perfis | 13–24 p.p. | 12–27 p.p. |
| Perfis em `penalty` nos 6 sorteios | 4 sorteios com P10 (+ P9 em 1) | 2 sorteios com P10 |
| Entra e sai | MM, L&S, Bitcoin | Systematic Trend (4 trocas) |

As mesmas fragilidades nos dois livros: composição dentro do bucket sensível, P10
no limite do teto, risco concentrado em uma classe de ações nas pontas. O problema
próprio do Onshore são as classes de premissa; o do Offshore é o RF das pontas ser
crédito HY e o T-bill ser irrelevante.

## 10. Decisões (2026-11-17)

- **P10:** fica como está na calibração vigente (Onshore σ máximo 11,5%; Offshore 14%).
  Ele converge; mexer sem necessidade mudaria o produto sem ganho comprovado, e subir o
  teto de peso das ações concentraria mais um perfil já concentrado.
- **Recalibração:** antes de aprovar, rodar `scripts/rbs_robustez.py`; se algum perfil for
  instável, reduzir o σ máximo até convergir (o teste de isolamento da seção 4 sugere
  começar por 10,5%). Regra no `CLAUDE.md` do módulo.
- **T-bills:** fora do otimizador no Offshore (seção 9).
- **Colchão de caixa:** camada fora do otimizador, escolhida pelo usuário, padrão de 5%,
  nos dois livros; implementada na página do Hub (PerseveraHub #46).
