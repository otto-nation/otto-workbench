#!/usr/bin/env bash
set -euo pipefail

deploy_branch() {
    local branch=$1
    local dest="/var/www/app"
    git checkout $branch
    git pull origin $branch
    cp -r . $dest
    echo "Deployed $branch to $dest"
}

deploy_branch "$1"
