---
paths:
  - "ansible/**"
  - "**/tasks/*.yml"
  - "**/roles/**"
  - "**/playbooks/**"
  - "**/*.j2"
---

# Ansible

## Code over commands
- All infrastructure changes are made via Ansible tasks — never suggest manual SSH,
  docker CLI, or one-off shell commands as the solution
- Use idempotent built-in modules (ansible.builtin.template, file, copy,
  community.docker.docker_compose_v2) over shell/command — if shell is unavoidable,
  add a comment explaining why no module equivalent exists

## Container assumptions
- Service containers may be minimal Alpine or distroless images without curl, wget,
  bash, or a package manager — never assume these tools are available
- Use Ansible's uri module for HTTP checks and wait_for for port/connection checks;
  never suggest docker exec + curl/wget patterns
- Before writing healthcheck tasks, check the base image to know what tools exist

## Variable scoping
- Version pins and port/container names have canonical locations in group_vars —
  read those files before writing tasks or templates that reference versions or ports
- Never duplicate a variable that already has a canonical location in group_vars
- Secrets come from vault.yml via {{ variable_name }} — never hardcode credentials

## Templates and handlers
- Edit Jinja2 templates in roles/<service>/templates/, not rendered files on the host
- Include notify: handlers on every task that changes service configuration —
  restarts are not automatic without them
