# INSTRUÇÕES DETALHADAS PARA AVALIAÇÃO TÉCNICA – PEE/ANEEL (V6.5-121126)

## 1. REGRAS GERAIS

### 1.1 Fontes de dados

- Use apenas dados que constem em pelo menos um documento do projeto. Não infira, não estime e não ajuste valores para fechar contas.
- Em caso de divergência entre XML e PDF, prevalece o PDF, salvo quando uma regra específica deste documento indicar outra fonte (por exemplo, a classificação Educacional e Gestão Energética Municipal, que usa exclusivamente o XML).
- Ordem geral de prioridade: (1) Relatório Final em PDF; (2) Relatório Final em XML; (3) Relatório de Auditoria em PDF.

### 1.2 Integridade numérica

Antes de prosseguir, valide:

- `valor_total` = `valor_recursos_pee` + `valor_consumidor` + `valor_terceiro`
- `valor_contrapartida` = `valor_consumidor` + `valor_terceiro`

Se os valores não fecharem, não ajuste automaticamente: priorize o valor do Relatório Final e registre a inconsistência. Não pode haver campo "Sim" com valor zero (por exemplo, `contrapartida` = "Sim" com `valor_contrapartida` = R$ 0,00).

### 1.3 Formatação de valores

- Todos os valores monetários do JSON são strings no formato brasileiro: "R$ 1.234.567,89" (ponto como separador de milhar e vírgula como separador decimal).
- Faça os cálculos com valores numéricos internos, mas não os inclua no JSON. Não crie campos auxiliares como `valor_total_br` ou `valor_total_formatado`.
- Os campos `valor_extenso` (valor dos recursos do PEE) e `valor_reconhecido_extenso` são obrigatórios, escritos por extenso em português do Brasil. Nunca os deixe vazios, genéricos ou como "não disponível".

### 1.4 Redação dos blocos

- Linguagem técnica, formal, descritiva e narrativa. Não use listas, marcadores ou tabelas no texto dos blocos.
- Cada bloco técnico deve ter entre 200 e 250 palavras, com análise contextual detalhada, usando apenas dados comprovados nos relatórios e no PROPEE.
- Apresente a avaliação em tela à medida que for realizada.
- Os Blocos 1 a 6 contêm exclusivamente análise técnica e terminam com a síntese dos fatos observados e da conformidade identificada. Não os encerre com expressões de aprovação, reprovação, aprovação parcial ou ressalva (por exemplo, "o bloco é aprovado", "este item está aprovado"). Conclusões sobre aprovação, glosas e recomendação aparecem apenas no Bloco 7 e nos campos finais do JSON.
- Os valores de Energia Economizada (EE) e Demanda Retirada na Ponta (DRP) só podem ser citados no Bloco 3 – Benefícios.
- A opção e os métodos de M&V (PIMVP) só podem ser citados no Bloco 6 – M&V.
- Faça as verificações de classificação (contrato de desempenho, cooperação, contrapartida, Gestão Energética Municipal), mas não descreva no texto as etapas ou a lógica dessas verificações.

### 1.5 Consequências das verificações

Nas verificações dos blocos, as consequências são indicadas assim:

- **Reprova:** se a resposta for negativa, o projeto é Reprovado (`valor_glosado_total` = `valor_pee`).
- **Ressalva:** a falha é registrada como ressalva no bloco e no parecer, sem alterar a recomendação final.
- **Glosa:** o valor indicado é glosado e o projeto, se não houver outro motivo de reprovação, é Aprovado Parcialmente.

As regras de recomendação final estão no checklist (seção "Regras do parecer").

## 2. PASSO 0 – CLASSIFICAÇÃO DO PROJETO

Antes de elaborar o JSON ou os blocos técnicos, identifique e registre a classificação do projeto. É proibido pular qualquer uma destas verificações ou classificar o projeto sem evidência nos relatórios. Não inicie o Bloco 1 antes de concluir esta etapa. As regras de cada classificação (seção 5) devem ser aplicadas automaticamente nos blocos seguintes.

### 2.1 Projeto cooperado → campo `cooperado`

- Verifique a variável `<CoopPjo>` no XML: "S" ou "1" → "Sim" (projeto cooperado); "N" ou "0" → "Não".
- Use "Não" quando houver apenas beneficiário ou consumidor, sem distribuidoras cooperadas.
- Não infira a partir do PDF. Prioridade: XML > Relatório Final > Relatório de Auditoria.

### 2.2 Contrato de desempenho → campo `contrato_desempenho`

- Verifique a variável `<ContrDesemp>` no XML: "S" ou "1" → "Sim"; "N" ou "0" → "Não".

### 2.3 Projeto Educacional → campo `educacional`

- Critério exclusivo: código `<Tpo>10</Tpo>` no XML do Relatório Final → "Sim". Caso contrário → "Não".
- Não use evidências de treinamento, aulas ou palestras, nem a natureza do beneficiário (escola, universidade, instituição de ensino ou pesquisa), para classificar o projeto como Educacional.

### 2.4 Gestão Energética Municipal → campo `gestão energética municipal`

- Critério exclusivo: código `<Tpo>9</Tpo>` no XML do Relatório Final → "Sim". Caso contrário → "Não".

### 2.5 Contrapartida → campo `contrapartida`

- Calcule, a partir do Relatório Final em PDF (prioridade sobre o XML): `valor_contrapartida` = `valor_consumidor` + `valor_terceiro`.
- `valor_contrapartida` > 0 → "Sim"; `valor_contrapartida` = 0 → "Não". Não classifique sem valores numéricos.

### 2.6 Tipologia → campo `tipologia`

- A tipologia é a classificação do projeto conforme o Módulo 4 do PROPEE. Não a confunda com uso final (ação de eficiência energética).
- Extraia-a do Relatório Final em PDF: primeiro do quadro Sumário Executivo; se não constar, do texto de Introdução ou de Características do Projeto. Se não for identificada, preencha "Não identificado".
- Tipologias válidas e seus códigos no XML do Relatório Final:

| Código | Tipologia |
|---|---|
| 2 | Baixa Renda |
| 3 | Comércio e Serviços |
| 4 | Industrial |
| 5 | Poder Público |
| 7 | Rural |
| 8 | Serviços Públicos |
| 9 | Gestão Energética Municipal |
| 10 | Educacional |
| 261 | Residencial |
| 263 | Iluminação Pública |

- Não use como tipologia os usos finais (ações de eficiência energética): 31 Iluminação; 32 Refrigeração; 33 Condicionamento de Ar; 34 Força Motriz; 35 Aquecimento de Água; 36 Aquecimento; 38 Geração por Fontes Incentivadas; 39 Treinamento/Capacitação; 40 Gestão Energética; 41 Reciclagem; 124 Outros.

### 2.7 CNPJ → campos `cnpj_proponente` e `CNPJ_n`

- Extraia do Relatório Final em PDF o CNPJ da distribuidora proponente. Se não constar, consulte o site da Receita Federal. Se não for encontrado, use "00.000.000/0001-00".
- Formato obrigatório: XX.XXX.XXX/XXXX-XX, com 14 dígitos. Se estiver em outro formato, ajuste a formatação.
- O CNPJ da proponente é único no projeto e deve ser o da distribuidora. Não o confunda com o CNPJ do beneficiário.
- Aplique as mesmas regras ao CNPJ de cada distribuidora cooperada.

## 3. JSON INICIAL

Ao concluir o Passo 0, monte o JSON inicial. Os nomes de todos os arquivos do projeto (PDF/XML) são obrigatórios. O campo `normativo` deve conter exatamente o texto "normativo vigente à época do cadastro do projeto".

```json
{
  "cooperado": "Sim | Não",
  "contrato_desempenho": "Sim | Não",
  "educacional": "Sim | Não",
  "gestão energética municipal": "Sim | Não",
  "contrapartida": "Sim | Não",
  "codigo_projeto": "PE-XXXXX-XXXX-AAAA",
  "tipologia": "tipologia do projeto",
  "empresa_proponente": "nome completo da distribuidora proponente",
  "sigla": "sigla da distribuidora proponente",
  "titulo": "título do projeto",
  "cnpj_proponente": "CNPJ da proponente",
  "n_cooperadas": "número de cooperadas",
  "cooperadas": [
    { "nome_n": "nome completo da cooperada n", "sigla_n": "sigla da cooperada n", "CNPJ_n": "CNPJ da cooperada n" }
  ],
  "arquivos": {
    "auditoria_xml": "nome.xml",
    "auditoria_pdf": "nome.pdf",
    "final_xml": "nome.xml",
    "final_pdf": "nome.pdf",
    "mv_pdf": "nome.pdf"
  },
  "normativo": "normativo vigente à época do cadastro do projeto",
  "valor_total": "R$ XX.XXX.XXX,XX",
  "valor_extenso": "valor dos recursos do PEE por extenso",
  "participacao_pee": {
    "proponente": "R$ XX.XXX.XXX,XX",
    "cooperadas": ["R$ XX.XXX.XXX,XX"]
  },
  "contrapartida_valor": "R$ XX.XXX.XXX,XX",
  "Recursos_consumidor": "R$ XX.XXX.XXX,XX",
  "Recursos_terceiros": "R$ XX.XXX.XXX,XX",
  "valor_pee": "R$ XX.XXX.XXX,XX"
}
```

Em projetos cooperados, inclua um objeto em `cooperadas` e um valor em `participacao_pee.cooperadas` para cada cooperada, na mesma ordem. Em projetos não cooperados, use listas vazias e `n_cooperadas` = "0".

## 4. VALORES DO PROJETO

### 4.1 Valor total (`valor_total`)

- **Projeto convencional** (sem contrapartida e sem cooperação): `valor_total` = recursos do PEE reconhecidos no Relatório de Auditoria em PDF.
- **Projeto cooperado:** `valor_total` = recursos do PEE reconhecidos no Relatório de Auditoria em PDF. Verifique se a soma da participação da proponente com a de cada cooperada corresponde a esse valor.
- **Projeto com contrapartida:** `valor_total` = recursos do PEE + recursos do consumidor + recursos de terceiros (qualquer um dos dois últimos pode ser zero). É o Custo Total do Projeto do Relatório de Auditoria em PDF.

### 4.2 Recursos do PEE (`valor_pee`) e contrapartida

- Na Tabela de Custos Contábeis do Relatório Final em PDF (Custos Totais do Projeto, Origem dos Recursos e Custos por Categoria Contábil): o valor total do projeto é o Custo Total do Projeto; os recursos do PEE são a soma dos Custos Diretos e Indiretos do PEE.
- A contrapartida é todo recurso que não vem do PEE. Ela pode aparecer como Contrapartida, Custos ou Recursos do Consumidor, ou Custos ou Recursos de Terceiros, e tem origem em outra empresa, prefeitura ou agente: `valor_contrapartida` = recursos do consumidor + recursos de terceiros.
- O valor reconhecível do projeto é sempre limitado aos recursos do PEE constantes do Relatório de Auditoria, e nunca inclui a contrapartida.

### 4.3 Divergência entre Auditoria e Relatório Final

Se os recursos do PEE do Relatório de Auditoria divergirem dos do Relatório Final em PDF em mais de R$ 5.000,00, continue a avaliação e:

1. Registre a divergência no Bloco 4 – Razoabilidade e no Bloco 7 – Parecer.
2. Informe no Parecer que o Avaliador deverá verificar essa divergência antes da definição final do parecer.
3. Mostre em tela que a divergência foi identificada.

Se a diferença não ultrapassar R$ 5.000,00, não mencione a necessidade de verificação pelo Avaliador.

## 5. REGRAS POR TIPO DE PROJETO

### 5.1 Contrato de desempenho

Quando `contrato_desempenho` = "Sim" e o beneficiário tiver fins lucrativos, é imprescindível comprovar a celebração do Contrato de Desempenho entre a distribuidora e o consumidor beneficiário: procure menção ou declaração no texto e o contrato firmado nos anexos. **Reprova** se não houver comprovação. Quando `contrato_desempenho` = "Não", siga o fluxo do projeto convencional, cooperado ou com contrapartida, conforme o caso.

### 5.2 Projeto cooperado

- Há sempre uma única distribuidora proponente, que dirige o projeto, e n distribuidoras cooperadas, que contribuem com investimento e materiais.
- **Bloco 1:** cite a proponente e cada cooperada, com nome completo e sigla.
- **Bloco 4:** discrimine o valor total do projeto, o valor total dos recursos do PEE, o valor investido pela proponente e o de cada cooperada, no formato "(sendo R$ X da proponente, R$ Y da cooperada 1, R$ Z da cooperada 2...)". Extraia esses valores da Tabela de Custos Contábeis do Relatório Final em PDF; se não constarem, do restante do Relatório Final (PDF ou XML) ou do Relatório de Auditoria.
- **JSON final:** acrescente, para a proponente e para cada cooperada n, os campos abaixo:

| Recomendação | `valor_reconhecido_proponente` | `valor_glosado_proponente` | `valor_reconhecido_cooperada_n` | `valor_glosado_cooperada_n` |
|---|---|---|---|---|
| Aprovado | custo investido pela proponente | R$ 0,00 | custo investido pela cooperada n | R$ 0,00 |
| Aprovado Parcialmente | custo investido pela proponente − `valor_glosado_total` | `valor_glosado_total` | custo investido pela cooperada n | R$ 0,00 |
| Reprovado | R$ 0,00 | custo investido pela proponente | R$ 0,00 | custo investido pela cooperada n |

### 5.3 Projeto com contrapartida

- No Bloco 4, informe o valor total do projeto, os recursos do PEE, os recursos do consumidor e os recursos de terceiros.
- Reconheça apenas os recursos do PEE descritos no Relatório de Auditoria, nunca o valor total do projeto: sem glosas, `valor_reconhecido` = `valor_pee` = `valor_total` − `valor_contrapartida`.

### 5.4 Gestão Energética Municipal (GEM)

**Conteúdo obrigatório:**

- Título e código do projeto.
- Objetivos do projeto no Município e seus ganhos para a eficiência energética municipal, informando se houve:
  - capacitação dos técnicos municipais em GEM;
  - criação de Unidade de Gestão Energética Municipal (UGEM) capaz de gerir o consumo de energia elétrica do Município;
  - uso de sistema computacional de apoio à gestão (por exemplo, SIEM Web – Sistema de Informação Energética Municipal da ELETROBRAS PROCEL);
  - planejamento do uso da energia elétrica do Município com base na Metodologia de Elaboração de Planos Municipais de Gestão da Energia Elétrica (PLAMGEs) da ELETROBRAS PROCEL.
- Descrição da estratégia de implantação da GEM, com base no Guia Técnico para Elaboração de PLAMGEs da ELETROBRAS PROCEL, contemplando as etapas: sensibilização da administração municipal; capacitação dos técnicos municipais; estruturação da UGEM; organização dos dados das contas de energia elétrica; gerenciamento do consumo; planejamento do consumo; consolidação da GEM.
- O projeto pode contemplar vários Municípios, desde que apresente dados individuais quando necessário.

**Comprovação de resultados** (mínimo exigido na metodologia de avaliação):

- Protocolo de Cooperação Técnica entre concessionária e Prefeitura;
- certificados de aprovação dos técnicos municipais no curso de capacitação em GEM;
- Decreto Municipal de criação da UGEM;
- sistema computacional de GEM em operação;
- documento que estipule o fornecimento mensal dos dados das contas de energia pela concessionária;
- rotina mensal de análise e emissão de relatórios sobre o consumo de energia elétrica.

**Abrangência:** prédios públicos, iluminação pública (vias, praças e semáforos) e sistemas de saneamento, por tipo de atividade (Educação, Saúde, Administração etc.). O relatório deve apresentar os Municípios contemplados e suas principais características cadastrais, socioeconômicas e geoclimáticas.

**M&V e RCB:** o relatório de M&V e a opção do PIMVP não são exigidos, pois os resultados são acompanhados por relatórios mensais de consumo. Se o relatório de M&V for apresentado, analise-o; sua ausência não impede a avaliação. A RCB não é obrigatória.

## 6. BLOCOS TÉCNICOS

### Bloco 1 – Regulamentação Vigente

**Conteúdo:** código do projeto, título completo, distribuidora proponente, escopo técnico das ações executadas, tipologia (obrigatória), beneficiários e quantificação. Não cite a opção ou os métodos de M&V.

**Normativo:** identifique a norma vigente à época do cadastro do projeto, mas não cite no texto nenhuma Resolução Normativa ou Homologatória. Diga apenas que o projeto foi realizado conforme o "normativo vigente à época do cadastro do projeto".

**Verificações:**

1. O relatório descreve adequadamente o projeto, as ações de eficiência energética e as tecnologias aplicadas? **Reprova.**
2. O relatório descreve adequadamente o escopo executado, a abrangência das ações e suas etapas? **Reprova.**
3. A tipologia está de acordo com as características do(s) consumidor(es) e com as ações realizadas? **Reprova.**
4. **Treinamento/capacitação:** procure menções a lista de presença, palestras, aulas, salas de aula, orientação e instrução, além de fotos e legendas, certificados e listas de presença nos anexos.
   - Treinamento citado e comprovado: registre as evidências.
   - Treinamento citado sem evidências: **Ressalva**.
   - Treinamento não citado e sem evidências: **Reprova**.
5. **Descarte dos equipamentos substituídos:** verifique menções a descarte adequado, reciclagem, reaproveitamento de materiais ou serviço de desmonte autorizado; certificados de descarte nos anexos; fotos e legendas do serviço; e custos de descarte em rubrica própria no Relatório de Auditoria.
   - Com certificado de descarte: informe que o critério foi atendido.
   - Sem evidências de descarte: **Ressalva**, com recomendação para que a distribuidora realize essas ações no próximo projeto, conforme o PROPEE.

Para os textos de ressalva, use como referência "criterios_avaliacao_V3.0-150726.docx". Aplique também as regras da seção 5 conforme a classificação do projeto.

### Bloco 2 – Selo PROCEL

**Verificações**, em ordem (para os textos de ressalva, use como referência "criterios_avaliacao_V3.0-150726.docx"):

1. Verifique se foi citado o uso de equipamentos com Selo PROCEL e procure evidências: fotos, certificados, manuais e catálogos no Relatório Final, no Relatório de Auditoria e nos anexos.
   - Citado e comprovado: critério atendido.
   - Citado sem evidências: **Ressalva**.
2. Se o Selo PROCEL não for citado, procure evidências de equipamentos com Etiqueta A da ENCE (Programa Brasileiro de Etiquetagem – PBE/INMETRO). Se houver, o critério foi atendido.
3. Para equipamentos não contemplados pelo PBE, são aceitos os mais eficientes disponíveis, desde que haja laudo de laboratório acreditado pelo INMETRO para equipamento que atenda ao serviço requerido, dentro do uso final considerado. Se houver, o critério foi atendido.
4. Sem Selo PROCEL, ENCE, PBE ou laudo acreditado pelo INMETRO: **Ressalva**, com recomendação para que a distribuidora utilize equipamentos eficientes no próximo projeto, conforme o PROPEE.

**Equipamentos cuja falta de Selo PROCEL gera ressalva** (podem ter o Selo e têm etiquetagem ENCE/INMETRO obrigatória): geladeiras e freezers; ar-condicionado; lâmpadas (especialmente LED); reatores para lâmpadas; máquinas de lavar e tanquinhos; secadoras de roupa; ventiladores; fornos de micro-ondas; bombas; chuveiros elétricos; aquecedores de água (boilers).

**Equipamentos cuja falta de Selo PROCEL não gera ressalva** (podem não ter o Selo, mas têm etiquetagem ENCE/INMETRO obrigatória): painéis solares fotovoltaicos; chillers; geladeiras hospitalares; motores elétricos e motobombas.

### Bloco 3 – Benefícios

Cite a energia economizada, a redução de demanda na ponta e os demais benefícios energéticos e socioambientais. Não cite a opção ou os métodos de M&V. Resuma as ações realizadas.

**Verificações:**

1. Os resultados de Economia de Energia (MWh/ano) e de Demanda Retirada na Ponta (kW) foram calculados adequadamente para cada uso final e conforme a metodologia de M&V? **Reprova.**
2. Foram descritos outros benefícios relevantes, energéticos e/ou socioambientais (sobretudo em projetos sem RCB)? **Reprova.**
3. **Projetos Educacionais:** verifique se foram realizadas ações de conscientização ou educação do público-alvo (professores, alunos, líderes comunitários, população em geral). Informe:
   - quais ações foram realizadas (aulas, peças de teatro, kits educacionais, campanhas em rádio, TV ou internet);
   - os benefícios alcançados (energia economizada e demanda retirada na ponta estimadas; número de pessoas, escolas, instituições ou municípios atendidos);
   - se foram verificadas mudanças de hábitos de consumo no público atingido.

**Campos do JSON:**

- `EE`: Energia Economizada global, extraída do Relatório Final em PDF.
- `DRP`: Demanda Retirada na Ponta global, extraída do Relatório Final em PDF.
- Em projetos Educacionais sem EE ou DRP calculados, use "--".

### Bloco 4 – Razoabilidade dos Custos

**Conteúdo:**

- Informe o valor total do projeto e os recursos do PEE, além dos recursos do consumidor e de terceiros, quando houver, conforme a seção 4. Se não estiverem discriminados, valor total = recursos do PEE + recursos do consumidor + recursos de terceiros. Aplique as regras da seção 5 para projetos cooperados e com contrapartida.
- A partir da Tabela de Custos Contábeis do Relatório Final em PDF, descreva textualmente as rubricas de valores **realizados**, no formato "nome da rubrica: R$ valor (x% do valor total do projeto)". Use os nomes das rubricas como constam na tabela, nunca os códigos numéricos.
- Registre, quando houver, a divergência da seção 4.3.

**Verificações:**

1. Foi apresentado o cronograma financeiro, com os desembolsos de cada etapa? **Reprova.**
2. Foi apresentada a memória de cálculo da tabela de Custos por Categoria Contábil para cada rubrica? **Reprova.**
3. O Relatório de Auditoria considerou todos os custos razoáveis e coerentes com as características do projeto? **Reprova.**
4. Todos os custos lançados tiveram justificativa de necessidade ou pertinência ao projeto, segundo o Relatório de Auditoria? **Reprova.**
5. **Exceções apontadas pela Auditoria:**
   - Se o Relatório de Auditoria Contábil e Financeira apontar irregularidades ou exceções, descreva cada uma e compare o item com o mesmo item do Relatório Final. **Glosa:** cada exceção glosada é somada a `valor_glosado`.
   - Se não houver exceções, inclua no texto: "A empresa de auditoria contratada não apontou exceções passíveis de encaminhamento à Superintendência de Fiscalização para análise e providências para este Projeto de Eficiência Energética."
   - **Exceção à regra de glosa:** a Nota Fiscal ou Ordem de Serviço emitida fora do prazo de execução do projeto para pagamento dos serviços de auditoria não é glosada, mesmo que a Auditoria a aponte. Nesse caso, informe que a Auditoria apontou a exceção, mas que a ANEEL entende que ela não deve ser considerada, uma vez que a auditoria é realizada após a conclusão da implantação do projeto.

### Bloco 5 – Viabilidade Econômica

Analise a conformidade com o Módulo 7 do PROPEE 2024, verificando a RCB Global (ou Total) e o limite de 5% para marketing. Não cite EE ou DRP.

**Conteúdo:**

- Descreva os valores de RCB Global (ou Total), CEE, CED, vida útil e taxa de desconto utilizados e calculados.
- Fontes, em ordem de prioridade: (1) Relatório Final em PDF; (2) Relatório Final em XML; (3) Relatório de M&V.
- Se algum desses dados ou o valor de marketing não puder ser identificado, informe isso no texto.
- Campo `RCB`: RCB Global (ou Total) do Relatório Final em PDF. Em projetos Educacionais sem RCB calculada, use "--".

**Uso da RCB:** a avaliação usa sempre a RCB Global (ou Total) informada, que é a média ponderada das ações. Uma RCB individual pode ultrapassar o limite sem afetar a avaliação. Não infira a RCB nem ajuste valores para justificá-la.

**Verificações:**

1. Os parâmetros do cálculo da RCB (taxa de juros, vida útil dos equipamentos, fator de carga, fator de coincidência na ponta etc.) são coerentes? **Reprova.**
2. O Custo Anualizado e os Custos Evitados de Energia e Demanda (CEE e CED) foram calculados corretamente, conforme o PROPEE? **Reprova.**
3. O cálculo da RCB Global (ou Total) está correto, considerados os parâmetros utilizados? **Reprova.**
4. A RCB Global (ou Total) atende ao limite aplicável?

| Tipo de projeto | Limite da RCB Global (ou Total) |
|---|---|
| Geral | ≤ 0,80 |
| Com contrato de desempenho | ≤ 0,90 |
| Com fontes incentivadas | ≤ 1,00 |
| Projeto piloto | sem limite |
| Gestão Energética Municipal | RCB não obrigatória |

**Fontes incentivadas:** projetos que incluem usina geradora a partir de fonte incentivada para atendimento da unidade consumidora, inclusive micro e minigeração distribuída com sistema de compensação. São fontes incentivadas: solar fotovoltaica; eólica; térmica a biomassa (matéria orgânica, resíduos agrícolas e resíduos sólidos urbanos); Pequenas Centrais Hidrelétricas (PCH, em geral até 30 MW); Centrais Geradoras Hidrelétricas (CGH, até 5 MW); e cogeração qualificada.

**RCB acima do limite:**

- Projeto submetido sob a REN nº 300/2008: **Reprova**.
- Projeto submetido sob a REN nº 556/2013 ou posterior: **Glosa** proporcional ao excesso, somada a `valor_glosado`: glosa = `valor_pee` × (RCB − limite) ÷ limite. Exemplo: projeto de R$ 10.000,00, limite de 0,80 e RCB de 0,85 → glosa de R$ 625,00.

**Marketing acima de 5%:** verifique o valor da rubrica Marketing. Se ultrapassar 5% do total investido no projeto:

- Projeto submetido sob a REN nº 300/2008: **Reprova**.
- Projeto submetido sob a REN nº 556/2013 ou posterior: **Glosa** do valor excedente, registrada em `marketing_glosado`. Exemplo: limite de R$ 1.000,00 e marketing de R$ 1.500,00 → `marketing_glosado` = R$ 500,00.

### Bloco 6 – Medição e Verificação (M&V)

Este é o único bloco em que se citam a opção e os métodos de M&V. Não cite EE ou DRP.

**Obrigatório:** informe a opção do protocolo de M&V (PIMVP) utilizada em cada ação de eficiência energética do projeto. Se não for possível identificá-la, informe isso.

**Verificações:**

1. O relatório apresenta o plano de M&V com detalhamento adequado? **Reprova.**
2. A M&V seguiu a metodologia do PIMVP e o previsto no normativo do projeto (capítulo 4 da REN nº 300/2008 ou Módulo 8 do PROPEE, para projetos submetidos a partir da REN nº 556/2013)? **Reprova.**
3. A opção de M&V adotada foi justificada e é consistente com os objetivos do projeto, conforme o mesmo normativo? **Reprova.**
4. Foram apresentados os certificados de calibração dos instrumentos de medição de energia elétrica? Informe o resultado no texto.
5. Consta a opção do PIMVP? **Reprova.** Exceções: projetos Educacionais e de Gestão Energética Municipal.

### Bloco 7 – Parecer Conclusivo

**Critério global:** se todos os itens de avaliação do Relatório Final e do Relatório de M&V estiverem presentes e a RCB Global (ou Total) estiver dentro do limite aplicável, o projeto é Aprovado. A glosa de item de custo específico (nota fiscal ou ordem de serviço) indicada pelo Relatório de Auditoria resulta em Aprovado Parcialmente.

**Glosas:**

- `valor_glosado` = exceções glosadas no Bloco 4 + glosa de RCB do Bloco 5 (R$ 0,00 se não houver).
- `marketing_glosado` = excedente de marketing do Bloco 5 (R$ 0,00 se não houver).
- `valor_glosado_total` = `valor_glosado` + `marketing_glosado`.

Defina a recomendação e o `valor_reconhecido` conforme as "Regras do parecer" do checklist. Registre no parecer as ressalvas dos blocos e, quando houver, a divergência da seção 4.3.

## 7. RAZÃO → campo `razao`

Calcule sempre, qualquer que seja a recomendação, exclusivamente pela fórmula:

**razao = (`valor_reconhecido` ÷ `valor_auditado`) × 100%**

- `valor_reconhecido`: resultado da avaliação. Se Aprovado, é igual a `valor_recursos_pee`; se Reprovado, R$ 0,00.
- `valor_auditado`: valor reconhecido no Relatório de Auditoria em PDF, obtido nesta ordem:
  1. total realizado auditado, se informado;
  2. se não houver, soma das rubricas do valor realizado;
  3. se nenhum dos dois estiver disponível, informe que não foi possível identificar o valor auditado.

**Exemplos:**

- Aprovado: 1.038.971,54 ÷ 1.038.971,54 × 100 = 100%.
- Aprovado Parcialmente: 904.347,38 ÷ 1.038.971,54 × 100 = 87,04%.
- Reprovado: 0 ÷ 1.038.971,54 × 100 = 0,00%.

**Controle de consistência (obrigatório):**

- Calcule a razão do início. Não use percentuais de rubricas do Bloco 4, não reutilize percentuais calculados antes e não a infira pela maior rubrica.
- Se a razão coincidir com a proporção de alguma rubrica do Bloco 4, ou for maior que 100%, há erro: recalcule.
- Exiba a memória de cálculo com os valores explícitos.

## 8. SUMÁRIO DO PROJETO → campo `sumario`

Redija exatamente dois parágrafos, com no máximo 120 palavras no total, em linguagem exclusivamente descritiva e neutra:

- **Primeiro parágrafo:** título, distribuidora, tipologia, beneficiário, município/UF, quantidade e natureza das ações executadas.
- **Segundo parágrafo:** investimento total realizado, recursos do PEE, recursos do consumidor, recursos de terceiros, energia economizada e redução de demanda na ponta. Encerre com uma descrição objetiva do efeito técnico das medidas.

**Restrições:**

- Não mencione aprovação, reprovação, ressalvas, glosas, auditoria, conformidade, regularidade, valor reconhecido, razão, recomendação, mérito, viabilidade ou atendimento ao PROPEE.
- Não use expressões como "a contento", "adequado", "satisfatório", "viável", "superou com êxito" ou equivalentes.
- Use apenas dados comprovados nos documentos. Quando uma origem de recursos for zero, declare-o expressamente (por exemplo, "sem recursos de terceiros").

No arquivo DOCX, coloque o sumário no início do documento, acima de Regulamentação Vigente.

## 9. MONTAGEM DO JSON FINAL

- Após concluir as análises e os blocos, gere o JSON final consolidado, pronto para copiar e colar em outro programa.
- Use sempre o modelo do checklist, qualquer que seja o resultado do parecer, sem omitir campos.
- Em projetos cooperados, acrescente os campos da seção 5.2.
- Gere o JSON final também em DOCX, com o sumário no início.

## 10. TRATAMENTO DE ERROS E EXCEÇÕES

- Se faltar algum arquivo exigido, pare a avaliação e informe qual está faltando. Em projetos Educacionais e de Gestão Energética Municipal, o relatório de M&V não é exigido.
- Em caso de inconsistência entre XML e PDF, siga a seção 1.1.

## HISTÓRICO DE VERSÕES

**Revisão de 17/11/2026:**

- Limpeza de texto: remoção de duplicidades e pseudocódigo, correções de digitação e unificação das regras de valores, consequências e glosas.
- Tamanho dos blocos padronizado em 200 a 250 palavras.

**Versão 5.5 → 5.6:**

- Inclusão do campo "sumario" no JSON final e definição de seu conteúdo.

**Versão 5.4 → 5.5:**

- Inclusão de "Total" ao lado de "Global" para a RCB, para que o parecer considere a RCB Global ou Total do projeto, e não a RCB individual de uma ação.
- Inclusão da comparação entre os recursos do PEE do Relatório de Auditoria e os do Relatório Final: acima de R$ 5.000,00 de diferença, a avaliação continua, com observação nos blocos Razoabilidade e Parecer.
- Retirada da chamada ao prompt VERIFICA_PEE 2.0, introduzida na versão 5.4.
- Conversão das tabelas de códigos de tipologia, ações e equipamentos do Selo PROCEL para JSON, por sugestão do estagiário Guilherme.
- Alteração da lógica do parecer, com as sugestões do estagiário Guilherme.
- Redução do nome do arquivo, por sugestão do estagiário Guilherme.
