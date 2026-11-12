# OPEE — controle persistente de downloads

## Quantidade de novos projetos e paginação

Para concluir cinco novos projetos:

```bash
python scraper_opee.py --quantidade 5
```

Também aceita `-q 5`. Sem essa opção, utiliza `MAX_PROJETOS_POR_EXECUCAO` em `config.py` (padrão: 5). A quantidade deve ser um inteiro maior que zero.

Se os três primeiros projetos estiverem registrados, uma meta de cinco baixa do quarto ao oitavo, desde que todos sejam concluídos sem falhas. Downloads incompletos não entram na meta: o script continua procurando outros projetos.

Depois de percorrer uma página, clica no link com o número da seguinte, usando `get_by_role("link", name=str(numero), exact=True)`, conforme a gravação fornecida. Avança de 1 para 2, de 2 para 3, e assim por diante. Confirma a mudança pelos códigos dos projetos, evitando depender apenas de um tempo fixo de espera.

Encerra ao atingir a meta ou quando não encontra o próximo link numérico habilitado. Se não houver novos projetos suficientes, ou ocorrer falha na paginação, informa quantos conseguiu concluir e sai com código 1. Ao atingir a meta, sai com código 0.

A busca filtrada é considerada o início da página 1, como no fluxo gravado. Se o site alterar os links numéricos ou passar a ocultar o número da página seguinte, o seletor precisará ser ajustado.

## Instalação

1. Mantenha os quatro arquivos Python desta entrega na mesma pasta, substituindo `scraper_opee.py` e `config.py` pelas versões atualizadas. `teste_conexao.py` foi incluído sem alterações.
2. Confira `ONEDRIVE_BASE_DIR` em `config.py`. Foi preservado o caminho original `C:\automacao_opee\local`; altere-o caso seus downloads estejam em outro local.
3. Mantenha o ambiente Python com Playwright que já utiliza, abra o Edge com a depuração na porta 9222 e entre no OPEE como antes.
4. Execute `python scraper_opee.py`.

O novo módulo `controle_downloads.py` usa somente a biblioteca padrão do Python. Não exige uma instalação adicional.

## Funcionamento

O histórico `projetos_baixados.txt` é criado automaticamente dentro de `ONEDRIVE_BASE_DIR` quando houver registros. Uma linha identifica um projeto, por exemplo:

```text
PE-00404-0072/2016
```

Ao iniciar, o script lê o arquivo para um `set`. Antes de abrir um projeto, consulta esse conjunto. Depois de salvar todos os arquivos listados em “Relatório Final”, cria `_PRONTO.flag` e atualiza o TXT imediatamente. O histórico permanece entre execuções, mesmo que as pastas dos projetos sejam movidas posteriormente. Preserve esse arquivo junto com seus dados.

O identificador é o código do projeto. Siglas de distribuidora, status e a troca de `/` por `_` no nome de pasta não alteram a identificação. Não é necessário conhecer a label de cada arquivo para esse controle por projeto.

`MAX_PROJETOS_POR_EXECUCAO = 5`, em `config.py`, define a meta padrão. Projetos pulados ou com falha não consomem esse limite. O script continua nas páginas seguintes, quando necessário.

## Aproveitamento dos downloads anteriores

Na inicialização, são importadas as pastas com `_PRONTO.flag` em `01_Baixados_OPEE` e as pastas de projetos em `02_Em_Analise` e `03_Concluidos`, preservando a regra do script anterior. `04_Erros` não comprova conclusão e não é importada.

O código anterior podia criar a sentinela após cinco downloads, mesmo havendo mais arquivos listados e alguma falha. Confira as pastas antigas se esse cenário tiver ocorrido: a migração confia nas sentinelas e nos estados existentes, não verifica retroativamente a integridade dos documentos.

## Falhas e reprocessamento

- O projeto só entra no histórico após salvar todos os arquivos listados, com tamanho maior que zero. Isso confirma a transferência, não o conteúdo técnico ou a validade de PDFs/XMLs.
- `QTD_ARQUIVOS_ESPERADOS` continua como referência para aviso. Uma contagem diferente de cinco não impede concluir, desde que todos os arquivos que o site listou sejam salvos.
- Arquivos em transferência usam o sufixo `.part`. Em caso de falha, o projeto fica pendente; na próxima execução seus arquivos serão baixados novamente. Não há retomada individual de arquivos nesta versão.
- Se falhar a gravação do histórico, a execução é interrompida. A sentinela já criada permite importar o projeto na próxima inicialização.
- Para baixar um projeto novamente de propósito, com o scraper parado, remova sua linha do TXT e retire sua sentinela de `01_Baixados_OPEE`. Se houver pasta desse projeto em `02_Em_Analise` ou `03_Concluidos`, retire-a também desses diretórios, pois ela seria reimportada.
- Execute uma instância por vez, incluindo entre computadores sincronizados. A gravação é atômica, mas não há coordenação entre processos concorrentes.
- O histórico registra conclusão por projeto. Novas versões de documentos adicionadas depois no mesmo projeto não serão detectadas automaticamente.

## Validação

Foram verificados a sintaxe dos scripts e o histórico em testes locais: persistência após reiniciar, normalização dos códigos, importação de pastas, exclusão de projetos incompletos e preservação do TXT quando a gravação falha. Também foram exercitadas com objetos simulados a regra de conclusão dos downloads, a meta do quarto ao oitavo projeto, o avanço 1→2→3, o fim da listagem, páginas repetidas, projetos duplicados entre páginas, quantidades inválidas e clique sem mudança na listagem. A navegação no OPEE autenticado precisa ser validada no computador em que seu Edge está aberto.
