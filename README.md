# Coach IA Pós-Sessão

Plataforma pós-sessão para upload e análise de gravações da PPPoker. Não oferece assistência durante partidas.

## Deploy na Abacus

1. Clone o repositório no SuperComputer.
2. Copie `.env.example` para `.env` e configure os segredos.
3. Execute `docker compose up -d --build`.
4. Verifique `http://localhost:3000/api/health` e `http://localhost:8000/health`.
5. Na Abacus, execute `sh scripts/install-nginx-abacus.sh` para publicar apenas o gateway web.

O deploy usa apenas Git e Docker; não requer o Agent da Abacus.

## Escopo desta versão

- upload manual de uma gravação encerrada;
- criação e consulta de sessões;
- fila assíncrona preparada para FFmpeg/OCR;
- dashboard inicial sem qualquer assistência durante partidas.
- acesso administrativo protegido por cookie HttpOnly;
- upload autenticado e limitado por tamanho.
- validação técnica assíncrona com `ffprobe` e manifesto por sessão.
- segmentação temporal a cada 2 segundos com miniaturas de baixo custo;
- política conservadora `unknown_until_evidenced` antes da classificação PPPoker.
- pontuação de mudança visual e agrupamento de segmentos por histograma.
- miniaturas autenticadas e acompanhamento do processamento no dashboard.
- classificador calibrado para o layout vertical PPPoker v220 com evidências por frame.
- detector pós-sessão de início/fim de mãos a 2 FPS, preservando interrupções de Lobby.
- clipes individuais com pré-roll de 3 segundos e quarentena de candidatos incompletos.

O pipeline de visão computacional ainda é um adaptador seguro: ele registra o trabalho,
mas não inventa mãos ou decisões até que os detectores validados sejam integrados.
