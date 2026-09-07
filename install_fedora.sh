#!/usr/bin/env bash
# GrokCAD — one-command installer for Fedora 41/42+ (GNOME or KDE, Wayland or X11).
# Usage:
#   chmod +x install_fedora.sh
#   ./install_fedora.sh
#   sudo ./install_fedora.sh --system-freecad   # also dnf-install FreeCAD if missing
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HOME}/.local/share/FreeCAD/Mod/GrokCAD"
DO_DNF=0
SKIP_PIP=0
DO_FLATPAK=0
FLATPAK_APP="org.freecad.FreeCAD"

for arg in "$@"; do
  case "$arg" in
    --system-freecad|--with-dnf) DO_DNF=1 ;;
    --skip-pip) SKIP_PIP=1 ;;
    --flatpak) DO_FLATPAK=1 ;;
    --flatpak=*)
      DO_FLATPAK=1
      FLATPAK_APP="${arg#--flatpak=}"
      ;;
    -h|--help)
      cat <<'EOF'
GrokCAD Fedora installer

  ./install_fedora.sh                 # dnf/host FreeCAD → ~/.local/share/FreeCAD/Mod
  ./install_fedora.sh --flatpak       # Flathub FreeCAD 1.1 → .../data/FreeCAD/v1-1/Mod
  sudo ./install_fedora.sh --system-freecad
  ./install_fedora.sh --skip-pip
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ "${DO_FLATPAK}" -eq 1 ]]; then
  # FreeCAD 1.1+ uses a versioned user dir (v1-1). Prefer the newest v* folder.
  BASE="${HOME}/.var/app/${FLATPAK_APP}/data/FreeCAD"
  if [[ -d "${BASE}/v1-1/Mod" ]]; then
    DEST="${BASE}/v1-1/Mod/GrokCAD"
  elif [[ -d "${BASE}" ]]; then
    newest="$(ls -d "${BASE}"/v*/Mod 2>/dev/null | sort -V | tail -n 1 || true)"
    if [[ -n "${newest}" ]]; then
      DEST="${newest}/GrokCAD"
    else
      DEST="${BASE}/Mod/GrokCAD"
    fi
  else
    DEST="${BASE}/v1-1/Mod/GrokCAD"
  fi
fi

echo "==> GrokCAD Fedora installer"
echo "    source : ${SCRIPT_DIR}"
echo "    dest   : ${DEST}"

have_freecad=0
if command -v freecad >/dev/null 2>&1 || command -v FreeCAD >/dev/null 2>&1; then
  have_freecad=1
fi

if [[ "${have_freecad}" -eq 0 ]]; then
  echo "!! FreeCAD is not on PATH."
  if [[ "${DO_DNF}" -eq 1 ]]; then
    if [[ "${EUID}" -ne 0 ]]; then
      echo "    re-run with sudo to install it:  sudo $0 --system-freecad"
      exit 1
    fi
    echo "==> dnf install -y freecad python3-pip python3-pillow"
    dnf install -y freecad python3-pip python3-pillow
  else
    echo "    Install it with:"
    echo "      sudo dnf install -y freecad python3-pip python3-pillow"
    echo "    then re-run $0"
    echo "    (or: sudo $0 --system-freecad)"
    exit 1
  fi
fi

# Python used by Fedora's FreeCAD is the system CPython.
PY="${PYTHON:-python3}"
if ! command -v "${PY}" >/dev/null 2>&1; then
  echo "!! python3 not found" >&2
  exit 1
fi

echo "==> Python: $("${PY}" -c 'import sys; print(sys.version)')"

if [[ "${SKIP_PIP}" -eq 0 ]]; then
  echo "==> pip install --user openai pillow requests"
  "${PY}" -m pip install --user --upgrade pip >/dev/null || true
  "${PY}" -m pip install --user --upgrade openai pillow requests
fi

echo "==> Installing workbench files"
mkdir -p "$(dirname "${DEST}")"
# Copy into a staging dir then swap, so a half-copy cannot leave a broken Mod.
STAGE="${DEST}.new.$$"
rm -rf "${STAGE}"
mkdir -p "${STAGE}"
# rsync if present, else cp.
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.new.*' \
    "${SCRIPT_DIR}/" "${STAGE}/"
else
  cp -a "${SCRIPT_DIR}/." "${STAGE}/"
  find "${STAGE}" -type d -name '__pycache__' -prune -exec rm -rf {} +
fi

# Make the installer executable in the installed copy too.
chmod +x "${STAGE}/install_fedora.sh" || true

if [[ -d "${DEST}" ]]; then
  BACKUP="${DEST}.bak.$(date +%Y%m%d%H%M%S)"
  echo "    existing install → ${BACKUP}"
  mv "${DEST}" "${BACKUP}"
fi
mv "${STAGE}" "${DEST}"

echo "==> Checking Python imports (user site)"
"${PY}" - <<'PY'
import site, sys
sys.path.append(site.getusersitepackages())
missing = []
for name in ("openai", "PIL"):
    try:
        __import__(name if name != "PIL" else "PIL")
    except Exception as exc:
        missing.append(f"{name}: {exc}")
if missing:
    print("WARN: missing packages:", "; ".join(missing))
    sys.exit(0)
print("    openai + Pillow OK")
PY

echo
echo "Installed."
echo
echo "Next:"
echo "  1. Start FreeCAD  (freecad)"
echo "  2. Switch workbench to  Grok CAD Agent"
echo "  3. Edit → Preferences → Grok CAD Agent  → paste your xAI API key"
echo "     (https://console.x.ai/)  or:  export XAI_API_KEY=xai-..."
echo "  4. Open the chat dock (toolbar: Open Grok Chat) and describe a part."
echo
echo "Optional FEM extras:  sudo dnf install -y CalculiX gmsh"
echo "Uninstall:            rm -rf \"${DEST}\""
