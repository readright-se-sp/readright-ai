#!/usr/bin/env bash
# Instance side: read the API key from SSM Parameter Store and start the production stack.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

MAI_API_KEY="$(aws ssm get-parameter \
  --name "${MAI_API_KEY_PARAMETER:-/readright/mai/api-key}" \
  --with-decryption --query Parameter.Value --output text)"
export MAI_API_KEY

docker compose -f compose.yaml up -d --build
