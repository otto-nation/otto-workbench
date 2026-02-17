# Kubernetes Configuration

# ============================================================================
# Common kubectl Shortcuts
# ============================================================================

# Core command
alias k='kubectl'

# Resource viewing
alias k-pods='kubectl get pods'
alias k-svc='kubectl get services'
alias k-deploy='kubectl get deployments'
alias k-logs='kubectl logs'
alias k-desc='kubectl describe'
alias k-exec='kubectl exec -it'

# ============================================================================
# Namespace Switching
# ============================================================================

# Switch to namespace
alias k-ns='kubectl config set-context --current --namespace'

# Example namespace shortcuts (customize for your environment)
# alias k-ns-dev='kubectl config set-context --current --namespace=development'
# alias k-ns-prod='kubectl config set-context --current --namespace=production'
