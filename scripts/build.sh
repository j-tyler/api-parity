#!/usr/bin/env bash
#
# Build script for api-parity
#
# Installs Python dependencies and builds the Go CEL evaluator binary.
# Run from the repository root: ./scripts/build.sh
#
set -euo pipefail

# Colors for output (disabled if not a terminal)
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    NC=''
fi

info() { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Ensure we're in the repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

info "Building api-parity from source..."
echo ""

# ============================================================================
# Check prerequisites
# ============================================================================

MISSING_DEPS=0

# Check Python
if command -v python3 &>/dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
    if [[ "$PYTHON_MAJOR" -gt 3 ]] || [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -ge 10 ]]; then
        success "Python $PYTHON_VERSION found"
    else
        error "Python 3.10+ required, found $PYTHON_VERSION"
        MISSING_DEPS=1
    fi
else
    error "Python 3 not found. Install Python 3.10 or later."
    MISSING_DEPS=1
fi

# Check Go
if command -v go &>/dev/null; then
    GO_VERSION=$(go version 2>&1 | awk '{print $3}' | sed 's/go//')
    GO_MAJOR=$(echo "$GO_VERSION" | cut -d. -f1)
    GO_MINOR=$(echo "$GO_VERSION" | cut -d. -f2)
    if [[ "$GO_MAJOR" -gt 1 ]] || [[ "$GO_MAJOR" -eq 1 && "$GO_MINOR" -ge 21 ]]; then
        success "Go $GO_VERSION found"
    else
        error "Go 1.21+ required, found $GO_VERSION"
        MISSING_DEPS=1
    fi
else
    error "Go not found. Install Go 1.21 or later: https://go.dev/dl/"
    MISSING_DEPS=1
fi

# Check pip
if command -v pip3 &>/dev/null || python3 -m pip --version &>/dev/null 2>&1; then
    success "pip found"
else
    error "pip not found. Install pip for Python 3."
    MISSING_DEPS=1
fi

if [[ "$MISSING_DEPS" -ne 0 ]]; then
    echo ""
    error "Missing prerequisites. Install them and re-run this script."
    exit 1
fi

echo ""

# ============================================================================
# Install Python package
# ============================================================================

info "Installing Python package in development mode..."

# Use pip from python3 -m to ensure correct pip
# Install with dev dependencies for running tests
if python3 -m pip install -e ".[dev]" --quiet; then
    success "Python package installed (with dev dependencies)"
else
    error "Failed to install Python package"
    exit 1
fi

# ============================================================================
# Build CEL evaluator
# ============================================================================

info "Building CEL evaluator (Go binary)..."

if [[ ! -d "cmd/cel-evaluator" ]]; then
    error "cmd/cel-evaluator directory not found. Are you in the repository root?"
    exit 1
fi

if go build -o cel-evaluator ./cmd/cel-evaluator; then
    success "CEL evaluator built: ./cel-evaluator"
else
    error "Failed to build CEL evaluator"
    exit 1
fi

echo ""

# ============================================================================
# Verify installation
# ============================================================================

info "Verifying installation..."

# Check CLI is available
if api-parity --help &>/dev/null; then
    success "api-parity CLI available"
else
    warn "api-parity CLI not in PATH. You may need to activate your virtualenv or add ~/.local/bin to PATH."
fi

# Check CEL binary exists and is executable
if [[ -x "./cel-evaluator" ]]; then
    success "CEL evaluator binary is executable"
else
    error "CEL evaluator binary not executable"
    exit 1
fi

echo ""
echo "=============================================="
success "Build complete!"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  1. Run tests:  python -m pytest tests/ -x -q --tb=short"
echo "  2. Try it:     api-parity --help"
echo ""
echo "See README.md for usage examples."
