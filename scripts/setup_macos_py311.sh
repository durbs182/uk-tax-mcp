#!/usr/bin/env bash
# setup_macos_py311.sh
# macOS setup script for uk-tax-mcp development environment.
# Installs Homebrew (if missing prompt), pyenv via Homebrew, Python 3.11.6,
# creates a virtualenv (.venv311) in the repo, installs server extras and
# starts the MCP stdio dev server.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY_VERSION="3.11.6"
VENV_DIR=".venv311"

echo "Repository root: $REPO_ROOT"
cd "$REPO_ROOT"

command_exists() { command -v "$1" >/dev/null 2>&1; }

# 1) Homebrew
if command_exists brew; then
  echo "Homebrew found"
else
  echo "Homebrew not found. Please install Homebrew first:"
  echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  echo "Then re-run this script. Exiting."
  exit 1
fi

# 2) Install build dependencies via brew
echo "Installing pyenv and build deps via Homebrew..."
brew update
brew install pyenv openssl readline xz zlib || true

# 3) Install pyenv (brew should have installed it)
if ! command_exists pyenv; then
  echo "pyenv not found after brew install; aborting"
  exit 1
fi

# Ensure pyenv is available in this script's environment
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
# Initialize pyenv for this shell (do not modify shell rc automatically)
if pyenv init --no-rehash >/dev/null 2>&1; then
  eval "$(pyenv init -)"
fi

# 4) Install Python
if pyenv versions --bare | grep -qx "$PY_VERSION"; then
  echo "Python $PY_VERSION already installed via pyenv"
else
  echo "Installing Python $PY_VERSION (this may take several minutes)..."
  pyenv install "$PY_VERSION"
fi

# Use the repo-local pyenv version
pyenv local "$PY_VERSION"

# Determine the pyenv python binary (prefer pyenv-installed version)
PY_BIN=""
if [ -d "$PYENV_ROOT/versions/$PY_VERSION" ]; then
  PY_BIN="$PYENV_ROOT/versions/$PY_VERSION/bin/python"
fi
if [ -z "$PY_BIN" ]; then
  PY_BIN="$(pyenv which python 2>/dev/null || true)"
fi
if [ -z "$PY_BIN" ]; then
  PY_BIN="$(command -v python3 || true)"
fi
if [ -z "$PY_BIN" ]; then
  echo "No suitable python binary found (pyenv or system python3). Aborting."
  exit 1
fi

# 5) Create and activate virtualenv/venv using the chosen python
if [ -d "$VENV_DIR" ]; then
  echo "Virtualenv $VENV_DIR already exists"
  # Check the existing venv python version; if it doesn't match desired PY_VERSION major.minor, recreate
  if [ -x "$VENV_DIR/bin/python" ]; then
    EXISTING_PY_VER=$($VENV_DIR/bin/python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')")
    DESIRED_MAJOR_MINOR="${PY_VERSION%.*}"
    if [ "$EXISTING_PY_VER" != "$DESIRED_MAJOR_MINOR" ]; then
      echo "Existing venv Python $EXISTING_PY_VER does not match desired $DESIRED_MAJOR_MINOR; recreating venv with $PY_BIN"
      rm -rf "$VENV_DIR"
      "$PY_BIN" -m venv "$VENV_DIR"
    else
      echo "Existing venv Python $EXISTING_PY_VER matches desired $DESIRED_MAJOR_MINOR"
    fi
  else
    echo "Existing venv has no python binary; recreating"
    rm -rf "$VENV_DIR"
    "$PY_BIN" -m venv "$VENV_DIR"
  fi
else
  echo "Creating venv $VENV_DIR using $($PY_BIN -V)"
  "$PY_BIN" -m venv "$VENV_DIR"
fi

# Activate venv for the remainder of the script
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Ensure pip comes from the venv python
python -m pip install --upgrade pip

# If the venv python is older than 3.10, print a warning and optionally install
# eval_type_backport to help pydantic evaluate new typing syntax. This is a
# fallback only; the recommended approach is to use Python >=3.10.
VENV_PY="$VENV_DIR/bin/python"
PY_MAJOR_MINOR=$($VENV_PY -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')")
PY_MAJ=${PY_MAJOR_MINOR%%.*}
PY_MIN=${PY_MAJOR_MINOR#*.}
if [ "$PY_MAJ" -lt 3 ] || ( [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -lt 10 ] ); then
  echo "Warning: venv Python is $PY_MAJOR_MINOR (<3.10). Pydantic v2 expects Python >=3.10."
  echo "Attempting to install eval_type_backport into venv as a compatibility workaround..."
  python -m pip install eval_type_backport || echo "eval_type_backport install failed; you may need to use Python >=3.10"
fi
# 6) Install project with server extras (mcp, click, etc.)
echo "Installing project (server extras)..."
set +e
python -m pip install -e '.[server]' 2> /tmp/pip_server_install.err
RC=$?
set -e
if [ $RC -eq 0 ]; then
  echo "Installed project with [server] extras"
else
  echo "Failed to install with [server] extras. Inspect /tmp/pip_server_install.err for details"
  # If failure was due to 'mcp' package not being available for the current Python,
  # fall back to installing the core project and common server deps (excluding mcp).
  if grep -q "mcp" /tmp/pip_server_install.err || grep -q "Could not find a version" /tmp/pip_server_install.err; then
    echo "Falling back: install core package and known server deps (excluding mcp)."
    python -m pip install -e .
    python -m pip install click || true
    python -m pip install fastapi || true
    python -m pip install "uvicorn[standard]" || true
    echo "WARNING: 'mcp' could not be installed automatically.\nIf you need the MCP stdio server, install 'mcp' manually (e.g., pip install mcp or pip install git+https://github.com/your-org/mcp.git).\nOtherwise, run the HTTP dev server with uvicorn for testing."
  else
    echo "pip install failed for an unexpected reason. See /tmp/pip_server_install.err"
    exit 1
  fi
fi

# 7) (Optional) install uvicorn/fastapi for HTTP dev server
python -m pip install 'uvicorn[standard]' fastapi || true

# 8) Final notes and shell rc guidance
cat <<EOF
Setup complete. Next steps (recommended):

1) Add pyenv init to your shell configuration (if not already present):

   # Add to ~/.zshrc or ~/.bashrc
   export PYENV_ROOT="$HOME/.pyenv"
   export PATH="$PYENV_ROOT/bin:$PATH"
   eval "$(pyenv init -)"

2) Activate the venv and run the MCP stdio server:

   cd "$REPO_ROOT"
   source $VENV_DIR/bin/activate
   python3 -m hmrc_tax_mcp.server

   Note: the MCP stdio server expects an MCP-compatible client (stdio transport).

3) Alternatively, run the HTTP dev server (convenient for manual testing):

   source $VENV_DIR/bin/activate
   python3 -m uvicorn src.hmrc_tax_mcp.server:app --reload --port 8000

EOF

# 9) Optionally start the MCP server now (commented out by default)
if [ "${START_NOW:-no}" = "yes" ]; then
  echo "Starting MCP stdio server in background..."
  # Start in background; user can inspect logs if desired
  nohup python3 -m hmrc_tax_mcp.server > hmrc-server.log 2>&1 &
  echo "Server started; logs: $REPO_ROOT/hmrc-server.log"
fi

exit 0
