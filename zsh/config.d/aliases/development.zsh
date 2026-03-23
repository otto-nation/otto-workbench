# Development Tools Configuration

# ============================================================================
# NPM
# ============================================================================

alias nom='rm -rf node_modules/ && npm cache verify && npm install'

# ============================================================================
# Gradle
# ============================================================================

alias gw='./gradlew'
alias gw-build='./gradlew build'
alias gw-test='./gradlew test'
alias gw-clean='./gradlew clean'
alias gw-clean-build='./gradlew clean build'

# ============================================================================
# Utilities
# ============================================================================

alias serena='uvx --from git+https://github.com/oraios/serena serena'

# ============================================================================
# YAML/JSON Processing
# ============================================================================

# Validate YAML syntax
yaml_validate() { python -c 'import sys, yaml, json; yaml.safe_load(sys.stdin.read())'; }
# Convert YAML to JSON (compact)
yaml2json() { python -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(sys.stdin.read())))'; }
# Convert YAML to JSON (pretty-printed)
yaml2json_pretty() { python -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(sys.stdin.read()), indent=2, sort_keys=False))'; }
# Validate JSON syntax
json_validate() { python -c 'import sys, yaml, json; json.loads(sys.stdin.read())'; }
# Convert JSON to YAML
json2yaml() { python -c 'import sys, yaml, json; print(yaml.dump(json.loads(sys.stdin.read())))'; }

