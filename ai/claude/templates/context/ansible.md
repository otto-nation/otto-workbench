# Architecture Context

<!-- This file is committed and manually maintained. Fill it in with facts that
     Claude cannot derive from reading the code — especially software identity
     (which implementation is actually running) and known constraints. -->

## Service Stack

| Role | Container | Software | Port | Notes |
|------|-----------|----------|------|-------|
| <!-- service --> | <!-- container name --> | <!-- software + version --> | <!-- port --> | <!-- key notes --> |

## Known Constraints

**Container tool availability:**
- <!-- List which container images have/lack curl, wget, bash, etc. -->
- Use Ansible's uri module for HTTP checks, wait_for for port checks

**Software identity (common confusion points):**
- <!-- Example: matrix role uses Conduit, not Synapse -->
- <!-- Example: immich uses a custom Postgres image with pgvector, not standard postgres -->

## Conventions

- <!-- Add project-specific patterns Claude should know about -->
- <!-- Example: all config changes go through Ansible templates in roles/<service>/templates/ -->
