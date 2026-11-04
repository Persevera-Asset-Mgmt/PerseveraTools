# PerseveraTools

Pacote interno da Persevera: leitura e carga de dados (providers → Postgres),
renda fixa, pesquisa quant (factor investing, risk budgeting) e integrações
(Fibery, custodiantes). Visão dos módulos no [README](README.md).

## Regras de trabalho
- Antes de alterar código, explicar o diagnóstico e propor a mudança.
- Não escrever nem apagar nada no Fibery; exclusões de campos são feitas manualmente.
- Commit e push só quando pedido; mudanças vão por branch + PR, nunca direto no `main`.

## Ambiente
- Testes e scripts: `.venv/Scripts/python.exe` (Python 3.12). O `python` do PATH
  é outro (3.15) e não tem numpy/pandas/pytest.
- Testes: `.venv/Scripts/python.exe -m pytest -q tests/`
- Produção: os jobs agendados (`run_*.bat`) rodam no PC Bloomberg, com
  Python 3.11, **pandas 2.2.2 e numpy 1.26**. Código com datas ou tipos numpy
  deve funcionar também nessas versões. Uma mudança só chega lá depois de o
  pacote ser reinstalado nessa máquina.

## Banco e n8n
- Vários providers de `persevera_tools/data/providers` têm uma réplica em
  workflow do n8n que grava nas mesmas tabelas. Renomear tabela, mudar campo ou
  chave exige atualizar código **e** workflow. Mapa em
  [docs/data/providers-tabelas-n8n.md](docs/data/providers-tabelas-n8n.md).
- `renda_fixa_historico` (antes `credito_privado_historico`) é compartilhada por
  vários providers; a chave do upsert inclui `source`.

## Documentação
- `docs/` guarda o *porquê*: aprendizados, diagnósticos e propostas, com
  `Status` e `Atualizado em`. Convenções em [docs/README.md](docs/README.md).
- Contexto específico de um módulo fica num `CLAUDE.md` dentro da pasta dele
  (ex.: `persevera_tools/quant_research/risk_budgeting_spectrum/CLAUDE.md`).
