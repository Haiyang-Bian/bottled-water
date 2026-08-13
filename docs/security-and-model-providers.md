# Security And Model Providers

## Authentication And Authorization

Open registration creates an active `member`; it never grants administrator access. Disabled users are rejected on every authenticated request, so previously issued JWTs stop working immediately. Duplicate email or username registration returns HTTP 409 without issuing a token.

Authorization is resolved from `UserRole → RolePermission → Permission` in the database. The built-in roles are `member`, `agent_provider`, `developer`, and `admin`. Only administrators may manage users, roles, and permissions. System roles cannot be deleted, and the API prevents self-demotion or removal of the final active administrator.

Create the first administrator interactively:

```powershell
cd backend
uv run python -m app.cli create-admin
```

For an unattended first Docker deployment, set `AGENTHUB_BOOTSTRAP_ADMIN_EMAIL`, `AGENTHUB_BOOTSTRAP_ADMIN_USERNAME`, and `AGENTHUB_BOOTSTRAP_ADMIN_PASSWORD`. Bootstrap runs only when no active administrator exists; remove the variables afterward.

## Production Configuration

Production startup rejects `DEBUG=true`, placeholder or short `SECRET_KEY` values, and sample database passwords. Start from `docker/env.example`. Unhandled API errors return a generic message plus `error_id`; detailed exceptions remain in server logs.

## Provider Credentials

Provider keys are transparently encrypted by the database `EncryptedText` type. API responses expose only `api_key_set`. Write or rotate a key through `PATCH /api/v1/model-providers/{id}/credential`; an empty frontend field preserves the existing key. Providers and model configurations are owner-scoped.

## DeepSeek

DeepSeek uses the official OpenAI-compatible API through the `openai` SDK:

- Base URL: `https://api.deepseek.com`
- Default: `deepseek-v4-flash`; optional `deepseek-v4-pro`
- Thinking: disabled by default; enable with effort `high` or `max`
- When thinking is enabled, temperature and top-p are omitted

Use “全局设置 → 模型 API” to add DeepSeek, save its key, refresh models, test connectivity, and activate a configuration. Real-service tests belong to the `providers:live` group and require `DEEPSEEK_API_KEY`.
