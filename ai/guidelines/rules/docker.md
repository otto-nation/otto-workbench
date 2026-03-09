---
paths:
  - "**/Dockerfile"
  - "**/Dockerfile.*"
  - "**/compose.yml"
  - "**/compose.yaml"
  - "**/docker-compose.yml"
  - "**/docker-compose.yaml"
---

# Docker

## Compose Files
- The `version` field is deprecated — never include it
- Use `docker compose` (v2) not `docker-compose`

## Dockerfiles
- Use multi-stage builds when appropriate
- Minimize layers
- Use specific base image tags — never `latest`
- Run as non-root user when possible
