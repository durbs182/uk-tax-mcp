#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/Users/pauldurbin/github/uk-tax-mcp"

# Run the proxy using the venv python directly (avoid shell 'source' portability issues)
exec "$REPO_ROOT/.venv311/bin/python" "$REPO_ROOT/scripts/mcp_stdio_proxy.py" 2>> /tmp/hmrc-mcp-proxy.log
