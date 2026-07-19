# Coach IA Pós-Sessão — v3.2.1 AI Progress

SaaS de estudo técnico para gravações encerradas da PPPoker. O sistema não oferece assistência durante partidas e não deve ser utilizado como ferramenta em tempo real.

Esta edição é destinada a testes internos. Não contém preços, planos comerciais, assinaturas, checkout, cobrança ou integração de pagamentos.

## Estado atual

- upload manual e autenticado de gravações encerradas;
- processamento assíncrono com PostgreSQL, Redis, FFmpeg e worker dedicado;
- classificação conservadora de mesa, Lobby, transição e tela desconhecida;
- detecção de candidatos a mãos e geração de clipes individuais;
- quarentena automática para mãos incompletas;
- dashboard amplo, responsivo e organizado por abas;
- histórico persistente das sessões;
- OCR conservador para contexto do Lobby;
- detecção de “pagou o Coelho” somente com evidência textual;
- validação humana persistente de mãos, Lobby e Coelho;
- observações salvas por sessão.
- busca, filtros e badges no histórico;
- progresso e finalização formal da revisão;
- tags, dificuldade e observações individuais por mão;
- correção manual do contexto do Lobby;
- exportação do relatório de revisão em JSON.
- KPIs consolidados de volume e revisão;
- favoritos, arquivamento e tags por sessão;
- edição do nome do torneio e filtros por período;
- detalhes técnicos do arquivo e reprocessamento controlado de falhas;
- exportação consolidada das sessões.
- autenticação multiusuário com papéis de administrador e jogador;
- criação, ativação, desativação e redefinição de senha pelo painel interno;
- isolamento das sessões por proprietário;
- senhas armazenadas com PBKDF2-SHA256 e salt individual.
- seleção local de frames relevantes por mão completa;
- extração multimodal estruturada de cartas, board, streets, pot, stack e ações visíveis;
- cartas renderizadas visualmente no dashboard;
- evidência e confiança associadas a cada extração;
- falhas isoladas por mão e revisão obrigatória para campos duvidosos.

## Regras de segurança do produto

- análise exclusivamente após o encerramento da sessão;
- nenhuma recomendação estratégica durante o jogo;
- `unknown_until_evidenced`: dados sem evidência permanecem desconhecidos;
- OCR do Lobby é marcado como não verificado até confirmação humana;
- Coelho somente é confirmado automaticamente quando o texto contém `PAGOU` e `COELHO`;
- cartas abertas pelo Coelho não são tratadas como streets com ação;
- mãos parciais não entram automaticamente na futura análise técnica.

## Arquitetura

- `apps/web`: Next.js, React e TypeScript;
- `apps/api`: FastAPI, autenticação e persistência das sessões;
- `apps/worker`: FFmpeg, classificação visual, OCR e geração de clipes;
- PostgreSQL: sessões e usuários;
- Redis: fila de processamento;
- Nginx: gateway público para Web e API;
- Docker Compose: execução integrada dos serviços.

## Deploy na Abacus

Após atualizar o repositório:

```bash
cd ~/coach-ia-pos-sessao
git pull --ff-only
docker compose up -d --build api worker web
docker compose ps
docker compose logs --tail=80 api worker web
```

Verificações locais:

```bash
curl http://localhost:8000/health
curl http://localhost:3000/api/health
```

O arquivo `.env` deve permanecer somente na Abacus e nunca deve ser enviado ao GitHub.

No primeiro boot, `ADMIN_EMAIL` e `ADMIN_PASSWORD` criam a conta administrativa inicial. Depois disso, novos acessos são criados em **Usuários** dentro do dashboard.

## Testes internos e usuários

1. Entre com o administrador configurado no `.env`.
2. Abra **Usuários** no topo do dashboard.
3. Informe e-mail, senha inicial e papel.
4. Entregue as credenciais ao testador por um canal seguro.
5. O jogador verá e administrará somente as próprias sessões.
6. O administrador pode redefinir a senha ou desativar o acesso.

O roteiro completo de validação está em `INTERNAL_TESTING.md`.

## Fluxo atual

1. O jogador encerra a sessão.
2. Envia a gravação vertical da PPPoker.
3. O worker valida e segmenta o vídeo.
4. As telas são classificadas.
5. As mãos candidatas e os clipes são gerados.
6. Lobby e Coelho são registrados como contexto separado.
7. O jogador revisa, aprova ou descarta as evidências.
8. As decisões e observações permanecem salvas após atualizar a página.

## Evolução recente

### v3.2.1 AI Progress

- progresso persistido por mão durante a extração visual;
- fases de preparação, espera da OpenAI, validação e conclusão;
- percentual, contadores de sucesso/erro e estimativa restante;
- barra animada durante chamadas demoradas;
- polling do dashboard sem necessidade de atualizar a página.

### v3.2.0 AI Calibration

- seleção adaptativa de até 9 frames por mão, configurável até 12;
- frames de fronteira, quartis e maiores mudanças visuais;
- validação de cartas duplicadas e colisões Hero/board;
- validação de contagem e progressão do board;
- verificação das referências de evidência;
- score determinístico de completude;
- redução automática de confiança quando há inconsistências;
- relatório de calibração no dashboard;
- contabilização de tokens de entrada e saída;
- metadados do critério de seleção em cada frame.

### v3.1.0 AI Alpha

- integração pós-sessão com OpenAI Responses API;
- Structured Outputs validados por Pydantic;
- até cinco frames cronológicos enviados por mão completa;
- respostas da API configuradas com `store=false`;
- política explícita de `unknown` e proibição de conselho estratégico;
- Coelho separado de streets e ações;
- visualização de cartas, board, ações, confiança, alertas e frames originais.

### v3.0.0 Internal Beta

- pacote único cumulativo para testes internos;
- autenticação multiusuário persistida no PostgreSQL;
- papéis de administrador e jogador;
- painel de criação e manutenção de acessos;
- isolamento de dados por usuário;
- nenhuma funcionalidade de valores, planos ou pagamentos.

### v2.4.0

- lote de 15 melhorias para gestão operacional das sessões;
- KPIs de horas, mãos, quarentena e revisões finalizadas;
- favoritos, arquivamento, tags, ordenação e filtro por período;
- edição do torneio e detalhamento do arquivo processado;
- mensagens de falha e reprocessamento somente para sessões com erro;
- exportação consolidada autenticada das sessões.

### v2.3.0

- lote de 10 melhorias para organização e revisão;
- busca e filtro de sessões com estados visuais;
- progresso das decisões e bloqueio de finalização incompleta;
- tags, dificuldade e notas por mão;
- edição manual dos campos extraídos do Lobby;
- exportação autenticada do relatório de revisão em JSON.

### v2.2.1

- README sincronizado com a versão real do produto.

### v2.2.0

- aprovação e descarte de mãos;
- confirmação ou rejeição de contexto do Lobby e eventos de Coelho;
- observações persistentes por sessão;
- correção da leitura do manifesto no histórico.

### v2.1.0

- navegação por abas;
- lista visual de mãos completas;
- clipes sincronizados com intervalos e confiança;
- área separada de quarentena;
- candidatos estruturados de jogadores, stack médio e premiação.

### v2.0.1

- cartões de OCR com alturas independentes;
- texto bruto recolhido;
- remoção de falsos cartões de Coelho em sessões novas e antigas.

### v2.0.0

- dashboard ampliado;
- progresso detalhado do processamento;
- galeria de evidências;
- OCR inicial de Lobby e Coelho.

## Limitações atuais

O sistema ainda não extrai todas as ações, sizings, posições e cartas de cada mão. Até que os detectores sejam validados com evidências reais suficientes, nenhuma análise estratégica deve ser inventada ou apresentada como definitiva.
