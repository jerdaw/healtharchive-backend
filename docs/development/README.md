# Development docs

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
