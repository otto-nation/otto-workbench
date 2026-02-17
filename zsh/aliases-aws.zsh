# AWS Configuration

# ============================================================================
# Core AWS Functions
# ============================================================================

# Show current AWS identity
function aws-whoami() {
  if [[ -z "$AWS_ACCESS_KEY_ID" ]]; then
    echo "Not logged in to AWS"
    return 1
  fi
  
  local saved_profile=$AWS_PROFILE
  unset AWS_PROFILE
  
  local account=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
  local arn=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null)
  local region=${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}
  
  [[ -n "$saved_profile" ]] && export AWS_PROFILE=$saved_profile
  
  if [[ -n "$account" ]]; then
    echo "Account: $account"
    echo "Identity: $arn"
    echo "Region: $region"
  else
    echo "AWS credentials set but unable to retrieve identity"
  fi
}

# Logout from AWS (clear all credentials)
function aws-logout() {
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE
  echo "Logged out from AWS"
}

# Docker ECR login (replace with your account ID and region)
function aws-docker() {
  local account_id="${AWS_ACCOUNT_ID:-123456789012}"
  local region="${AWS_REGION:-us-east-1}"
  aws ecr get-login-password --region "$region" | \
    docker login --username AWS --password-stdin "$account_id.dkr.ecr.$region.amazonaws.com"
}
