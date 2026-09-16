# Security And Model Providers

## Local CLI And Web Have Different Boundaries

The authentication, RBAC and administrator bootstrap sections below describe the **Web host**. The standalone CLI uses a machine/user-bound local environment and DPAPI or environment-variable credential references; it does not create a Web login or require Web administrator setup.

In 0.2.3, ordinary CLI tasks explicitly use `file_access_scope=user`, normal networking and the current user's OS access. Directory trust is disabled for that mode. Job cleanup, input validation and redaction remain, but are not a filesystem/network sandbox. Elevated hosts cannot run model tasks or tool management; there is no absolute anti-elevation guarantee for arbitrary scripts. LPAC is paused, and old restricted tasks never silently become native tasks. Model credentials are not deliberately injected into tool environments, but same-user scripts are not a strong secret-isolation boundary. See [CLI behavior](./cli.md) and [verified limits](./acceptance/native-user-experience-0.2.3.md).

Memory adoption, environment ownership and source validation remain separate from file access. Retained schema v6 permission records do not mean restricted execution is enabled. Normal installation/upgrade does not change business-directory ACLs or invoke UAC.

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
