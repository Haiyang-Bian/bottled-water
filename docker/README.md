# Docker Deployment

Run the full stack from the repository root:

```powershell
docker compose -f docker/docker-compose.yml up --build
```

To customize passwords, ports, or public URLs:

```powershell
Copy-Item docker/env.example docker/.env
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build
```

Open the app at:

```text
http://localhost:8080
```

The compose stack starts:

- `nginx`: frontend static files and reverse proxy
- `backend`: FastAPI API server on the internal port `8888`
- `postgres`: PostgreSQL 15 with a persistent volume
- `redis`: Redis 7 with a persistent volume

The backend container runs `alembic upgrade head` before starting the API, so a fresh database is initialized automatically.

Useful commands:

```powershell
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs -f backend
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml down -v
```

Use `down -v` only when you want to remove the PostgreSQL and Redis volumes as well.

For production, override at least these values before deploying publicly:

- `SECRET_KEY`
- `POSTGRES_PASSWORD`
- `PUBLIC_BASE_URL`
- model provider keys configured through the app or environment
