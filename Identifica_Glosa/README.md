# Identifica Glosa

O programa percorre a pasta principal com as avaliações de projetos. Em cada
subpasta, lê o arquivo `.json` da avaliação e o campo **`recomenda`**.

Se a recomendação for **Reprovado** ou **Aprovado parcialmente**, a subpasta é
renomeada com o sufixo **` - Glosado`**:

```
Avaliacoes/
├── PE-001-2024/                 recomenda: Aprovado              (sem alteração)
├── PE-002-2024 - Glosado/       recomenda: Reprovado
└── PE-003-2024 - Glosado/       recomenda: Aprovado parcialmente
```

## Como usar

Não precisa instalar nada além do Python.

- **Duplo clique** em `Identifica Glosa.bat`. Uma janela abre para você escolher a pasta principal.
- Ou pelo terminal:

```bash
python identifica_glosa.py "C:\caminho\Avaliacoes"
```

Opções:

- `--simular`: mostra o que seria renomeado, **sem renomear nada**. É bom usar antes da primeira vez.
- `--recursivo`: procura o `.json` também em pastas dentro das subpastas.
  Por padrão, procura só no primeiro nível de cada subpasta.

## Regras

- Aceita o campo `recomenda` ou numerado (`recomenda_1`, `recomenda_2`…), como nos
  JSONs consolidados do GeraDocs, em qualquer nível do arquivo.
- Maiúsculas, acentos e espaços extras são ignorados. Por exemplo,
  `APROVADO  PARCIALMENTE` também é reconhecido.
- Se a subpasta tiver mais de um projeto ou mais de um JSON, basta **um** com glosa
  para a pasta ser marcada.
- Pastas que já terminam com ` - Glosado` não são renomeadas de novo. O programa
  pode ser rodado várias vezes sem problema.
- O programa nunca apaga nem altera os arquivos, só renomeia as pastas.

## Relatório

Ao final, o programa mostra um resumo e salva o arquivo **`relatorio_glosas.csv`** na
pasta principal. O relatório abre direto no Excel e lista cada subpasta com uma
destas situações:

| Situação     | Significado                                                  |
|--------------|--------------------------------------------------------------|
| GLOSADO      | Pasta renomeada nesta execução                               |
| JÁ MARCADO   | Pasta já tinha o sufixo ` - Glosado`                         |
| SEM GLOSA    | Recomendação sem glosa (ex.: Aprovado)                       |
| SEM JSON     | Nenhum arquivo `.json` na subpasta                           |
| SEM CAMPO    | JSON sem o campo `recomenda`                                 |
| ERRO         | JSON corrompido, nome já existente ou pasta em uso           |

Se uma pasta não puder ser renomeada, verifique se algum arquivo dela está aberto
(Word, PDF, Explorador de Arquivos) e rode de novo.
