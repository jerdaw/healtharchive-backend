# Development docs

## Start Here

**New developer?**
- **Setup:** [Dev Environment Setup](dev-environment-setup.md) — Local setup guide
- **Test:** [Live Testing](live-testing.md) — Local testing workflows
- **Contribute:** [Testing Guidelines](testing-guidelines.md) — Test conventions
- **Architecture:** [Architecture](../architecture.md) — How the code works

**Quick reference:**
| Task | Documentation |
|------|---------------|
| Run backend locally | [Live Testing](live-testing.md) |
| Run tests | [Testing Guidelines](testing-guidelines.md) |
| Understand architecture | [Architecture](../architecture.md) |
| Deploy changes | [Change to Production](playbooks/change-to-production.md) |

## All Development Documentation

- Local testing flows (recommended): `live-testing.md`
- Local + VPS setup (recommended): `dev-environment-setup.md`
- Backend testing conventions: `testing-guidelines.md`
- Development playbooks (task workflows): `playbooks/README.md`

## Code Annotations (Demo)
This project uses MkDocs Material code annotations to provide inline context for complex configurations:

```yaml
# Example docker-compose.yml
services:
  api:
    image: healtharchive-backend:latest
    ports:
      - "8001:8001" # (1)
    environment:
      - HEALTHARCHIVE_DATABASE_URL=sqlite:///data.db # (2)
```

1. Standard FastAPI port for local development.
2. Default SQLite path inside the container.
