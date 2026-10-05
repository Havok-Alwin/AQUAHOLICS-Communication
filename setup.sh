#!/usr/bin/env bash
# AQUAHOLICS: one-time setup on a fresh Ubuntu (22.04 / 24.04, x86_64) for the operator display
# (OCS_Application/MissionPlanner_dissected: vehicle backend + display + mission planner).
#
#   ./setup.sh            install everything, then build once
#   ./setup.sh --test     same, then run the backend and frontend tests
#
# What it does (each step is skipped when already done, so running it again is safe):
#   1. Ubuntu packages (sudo): curl, git, ca-certificates, libicu (needed by .NET), serial-port access
#      for your user (dialout group), and removes modemmanager/brltty, which grab the Pixhawk / telemetry
#      radio USB ports. Keep them with:  KEEP_MODEMMANAGER=1 ./setup.sh
#   2. Conda (Miniforge in ~/miniforge3) if no conda is installed.
#   3. Conda environment "aquaholics" with Node.js 22 (the display build).
#   4. .NET 10 SDK inside that environment (conda-forge has no .NET 10, so Microsoft's installer
#      puts it in the environment; activating the environment puts it on PATH).
#   5. Restores and builds the backend (NuGet) and the display (npm).
#
# Afterwards, every time:
#   conda activate aquaholics
#   cd OCS_Application/MissionPlanner_dissected && ./start.sh
set -euo pipefail

ENV_NAME=aquaholics
NODE_VERSION=22
DOTNET_CHANNEL=10.0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/OCS_Application/MissionPlanner_dissected"
RUN_TESTS=0
[[ "${1:-}" == "--test" ]] && RUN_TESTS=1

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ -d "$APP/backend/OcsBackend" && -f "$APP/frontend/package.json" ]] ||
  die "Run this from the AQUAHOLICS-Communication checkout (missing $APP)."
[[ -f "$APP/backend/MissionPlanner.ArduPilot.dll" ]] ||
  die "The Mission Planner DLLs are missing in $APP/backend (were they committed?)."
[[ "$(uname -m)" == "x86_64" ]] || warn "Tested on x86_64 only; the bundled SkiaSharp library is x64."
[[ $EUID -ne 0 ]] || die "Run as your normal user, not with sudo (the script asks for sudo itself)."

# ---- 1. Ubuntu packages and serial access -------------------------------------------------------
step "Ubuntu packages (asks for your password)"
sudo apt-get update
sudo apt-get install -y curl git ca-certificates libicu-dev

if ! id -nG "$USER" | grep -qw dialout; then
  sudo usermod -aG dialout "$USER"
  NEED_RELOGIN=1
  echo "Added $USER to the dialout group (serial ports)."
fi

if [[ "${KEEP_MODEMMANAGER:-0}" != "1" ]]; then
  for pkg in modemmanager brltty; do
    if dpkg -s "$pkg" >/dev/null 2>&1; then
      echo "Removing $pkg: it opens USB serial devices and blocks the Pixhawk / radio."
      sudo apt-get remove -y "$pkg"
    fi
  done
fi

# ---- 2. Conda ----------------------------------------------------------------------------------
step "Conda"
find_conda() {
  if command -v conda >/dev/null 2>&1; then
    CONDA_BASE="$(conda info --base)"
    return 0
  fi
  for d in "$HOME/miniforge3" "$HOME/mambaforge" "$HOME/miniconda3" "$HOME/anaconda3" /opt/conda; do
    if [[ -x "$d/bin/conda" ]]; then
      CONDA_BASE="$d"
      return 0
    fi
  done
  return 1
}
if ! find_conda; then
  echo "No conda found: installing Miniforge in $HOME/miniforge3"
  installer="$(mktemp --suffix=.sh)"
  curl -fsSL -o "$installer" "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-$(uname -m).sh"
  bash "$installer" -b -p "$HOME/miniforge3"
  rm -f "$installer"
  CONDA_BASE="$HOME/miniforge3"
  "$CONDA_BASE/bin/conda" init bash >/dev/null
  NEW_SHELL=1
fi
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"
echo "Using conda in $CONDA_BASE"

# ---- 3. Environment with Node.js ---------------------------------------------------------------
step "Conda environment '$ENV_NAME' (Node.js $NODE_VERSION)"
# conda-forge only: no Anaconda "defaults" channel, so no terms-of-service prompt.
if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  conda install -y -n "$ENV_NAME" --override-channels -c conda-forge "nodejs=$NODE_VERSION"
else
  conda create -y -n "$ENV_NAME" --override-channels -c conda-forge "nodejs=$NODE_VERSION"
fi
conda activate "$ENV_NAME"

# ---- 4. .NET 10 SDK inside the environment -----------------------------------------------------
step ".NET $DOTNET_CHANNEL SDK in the environment"
DOTNET_DIR="$CONDA_PREFIX/lib/dotnet"
if [[ -x "$DOTNET_DIR/dotnet" ]] && "$DOTNET_DIR/dotnet" --list-sdks | grep -q "^${DOTNET_CHANNEL%.0}\."; then
  echo "Already installed: $("$DOTNET_DIR/dotnet" --version)"
else
  installer="$(mktemp --suffix=.sh)"
  curl -fsSL -o "$installer" https://dot.net/v1/dotnet-install.sh
  bash "$installer" --channel "$DOTNET_CHANNEL" --install-dir "$DOTNET_DIR"
  rm -f "$installer"
fi
# Put dotnet on PATH whenever the environment is active, and take it off again on deactivate.
mkdir -p "$CONDA_PREFIX/etc/conda/activate.d" "$CONDA_PREFIX/etc/conda/deactivate.d"
cat >"$CONDA_PREFIX/etc/conda/activate.d/aquaholics-dotnet.sh" <<'EOF'
export AQUAHOLICS_OLD_PATH="$PATH"
export DOTNET_ROOT="$CONDA_PREFIX/lib/dotnet"
export PATH="$DOTNET_ROOT:$PATH"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1
EOF
cat >"$CONDA_PREFIX/etc/conda/deactivate.d/aquaholics-dotnet.sh" <<'EOF'
export PATH="${AQUAHOLICS_OLD_PATH:-$PATH}"
unset AQUAHOLICS_OLD_PATH DOTNET_ROOT DOTNET_CLI_TELEMETRY_OPTOUT DOTNET_NOLOGO
EOF
# Re-activate so this script uses the environment's dotnet.
conda deactivate
conda activate "$ENV_NAME"
echo "dotnet $(dotnet --version) at $(command -v dotnet)"
echo "node $(node --version) at $(command -v node)"

# ---- 5. Build once (downloads NuGet and npm packages) ------------------------------------------
step "Building the vehicle backend"
dotnet build "$APP/backend/OcsBackend" -c Debug

step "Building the display"
(cd "$APP/frontend" && npm ci && npm run build)

if ((RUN_TESTS)); then
  step "Tests"
  dotnet test "$APP/backend/OcsBackend.Tests"
  (cd "$APP/frontend" && npm test)
fi

chmod +x "$APP/start.sh"

# ---- Done -------------------------------------------------------------------------------------
step "Setup finished"
if [[ -n "${NEW_SHELL:-}" ]]; then
  echo "Conda was just installed: open a NEW terminal first."
fi
if [[ -n "${NEED_RELOGIN:-}" ]]; then
  echo "You were added to the 'dialout' group: log out and back in (or reboot) once, or the"
  echo "serial ports will say 'permission denied'."
fi
cat <<EOF

Run the display:
  conda activate $ENV_NAME
  cd "$APP"
  ./start.sh            # then pick the port on the vehicle card and press Connect
EOF
