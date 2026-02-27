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

alias k-ns='kubectl config set-context --current --namespace'
