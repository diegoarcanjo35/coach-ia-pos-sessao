# Coach IA Pós-Sessão

Plataforma pós-sessão para upload e análise de gravações da PPPoker. Não oferece assistência durante partidas.

## Deploy na Abacus

1. Clone o repositório no SuperComputer.
2. Copie `.env.example` para `.env` e configure os segredos.
3. Execute `docker compose up -d --build`.
4. Verifique `http://localhost:3000/api/health` e `http://localhost:8000/health`.

O deploy usa apenas Git e Docker; não requer o Agent da Abacus.
