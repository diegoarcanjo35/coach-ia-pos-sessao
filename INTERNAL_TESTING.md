# Roteiro de testes internos — v3.0.0

## Preparação

- confirme que o sistema informa `3.0.0-internal` nos health checks;
- entre com a conta administrativa já configurada na Abacus;
- nunca compartilhe o `.env` ou a senha administrativa;
- crie uma conta individual para cada testador.

## Administração de usuários

1. Abra **Usuários**.
2. Crie um jogador com senha temporária de pelo menos 8 caracteres.
3. Saia e valide o login do jogador.
4. Confirme que ele não visualiza sessões de outras contas.
5. Redefina a senha pelo administrador.
6. Desative a conta e confirme que o acesso deixa de funcionar.
7. Reative a conta e teste novamente.

## Upload e processamento

1. Envie uma gravação vertical encerrada da PPPoker.
2. Confirme que o nome do arquivo e do torneio são limpos após o envio.
3. Acompanhe todas as etapas até **Pronto para revisão**.
4. Confirme mesa, Lobby, mãos completas e quarentena.
5. Verifique se uma falha apresenta mensagem e botão de reprocessamento.

## Revisão

1. Reproduza os clipes.
2. Aprove ou descarte cada mão.
3. Adicione tag, dificuldade e observação.
4. Confira e corrija os dados do Lobby.
5. Confirme ou descarte evidências de Coelho.
6. Salve observações da sessão.
7. Atualize a página e confirme a persistência.
8. Finalize a revisão somente quando não houver decisões pendentes.

## Gestão e exportação

- teste busca, período, ordenação, favoritos e arquivamento;
- edite o nome do torneio e salve tags da sessão;
- confira horas, mãos, quarentena e percentual de revisão;
- exporte a revisão individual e o consolidado de sessões;
- confirme que os arquivos exportados não contêm senhas.

## Registro de problemas

Para cada problema, anote:

- usuário e sessão afetada;
- horário aproximado;
- ação realizada;
- resultado esperado e resultado observado;
- print da tela;
- trecho dos logs de `api`, `worker` ou `web`, sem segredos.

## Fora do escopo desta edição

- valores e planos;
- cobrança, checkout e pagamentos;
- suporte em tempo real durante partidas;
- recomendações estratégicas sem extração validada das ações e cartas.
