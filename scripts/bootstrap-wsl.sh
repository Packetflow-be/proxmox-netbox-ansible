#!/usr/bin/env bash
# bootstrap-wsl.sh: dev environment for proxmox-netbox-ansible on WSL (Ubuntu, amd64)
# Idempotent: safe to re-run. Clones this repo if missing, fast-forwards it when
# clean, never touches a checkout with local changes.
#
# Usage:   make setup            (from the checkout; same as: bash scripts/bootstrap-wsl.sh)
#          First run on a fresh machine: see docs/setup.md (apt gh, gh auth login, clone, make setup).
# Options: PF_ROOT=~/somewhere bash bootstrap-wsl.sh   (default: ~/packetflow)
set -euo pipefail

# ---------------------------------------------------------------- config ----
ROOT="${PF_ROOT:-$HOME/packetflow}"
GH_REPO="Packetflow-be/proxmox-netbox-ansible"
CHECKOUT="$ROOT/proxmox-netbox-ansible"            # single clone; also the collections root
COLL_DIR="$CHECKOUT/ansible_collections/packetflow"
DEPS_DIR="$ROOT/.deps"                             # third-party collections (ansible-galaxy)
VENV="$ROOT/.venv"
CFG="$ROOT/ansible.cfg"
BIN="$HOME/.local/bin"

# Pinned tools. Bump version + sha256 together; the hashes come from each
# release's published checksums file.
SOPS_VERSION=3.13.3
SOPS_SHA256=e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b   # sops-v3.13.3.linux.amd64
GH_VERSION=2.101.0
GH_SHA256=9bca2d1c16825f109907a23307628a2f0698fbf99662b73a5cf0b020293072b8     # gh_2.101.0_linux_amd64.tar.gz
COPIER_VERSION=9.18.2

# age comes from Ubuntu's archive.
APT_PKGS=(git curl ca-certificates make python3 python3-venv python3-pip openssh-client age)
# On top of .github/requirements-ci.txt (ansible-core, ansible-lint,
# yamllint, molecule, pytest, and proxmoxer/pynetbox/requests for the Proxmox and
# NetBox modules), so local tooling matches CI. ruff/shellcheck-py use the same
# pins as .github/workflows/scripts.yml (for `make check`).
PY_EXTRA=(pytz jmespath netaddr "copier==$COPIER_VERSION" ruff==0.16.8 shellcheck-py==0.11.0.1)

# --------------------------------------------------------------- helpers ----
log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# fetch URL SHA256 OUT: download over HTTPS and refuse anything that doesn't match.
fetch() {
  local url="$1" sum="$2" out="$3"
  curl -fsSL --retry 3 --proto '=https' --tlsv1.2 -o "$out" "$url" || die "download failed: $url"
  echo "$sum  $out" | sha256sum --check --status || die "checksum mismatch: $url"
}

# installed NAME EXPECTED: our copy in $BIN reports the expected version.
installed() {
  [[ -x "$BIN/$1" ]] && "$BIN/$1" --version 2>/dev/null | head -1 | grep -qF "$2"
}

sync_repo() {
  local repo="$1" dest="$2"
  local url="https://github.com/${repo}.git"

  if [[ -d "$dest/.git" ]]; then
    git -C "$dest" fetch --prune --quiet
    if [[ -n "$(git -C "$dest" status --porcelain)" ]]; then
      warn "${dest#"$ROOT"/}: local changes, pull skipped"
    elif ! git -C "$dest" pull --ff-only --quiet 2>/dev/null; then
      warn "${dest#"$ROOT"/}: not fast-forwardable (diverged or no upstream), pull skipped"
    fi
  elif [[ -e "$dest" ]]; then
    die "$dest exists but is not a git repo — move it away and re-run"
  else
    mkdir -p "$(dirname "$dest")"
    git clone --quiet "$url" "$dest"
  fi
  ok "$repo -> ${dest#"$ROOT"/} [$(git -C "$dest" rev-parse --abbrev-ref HEAD)@$(git -C "$dest" rev-parse --short HEAD)]"
}

# ------------------------------------------------------------ pre-flight ----
log "Pre-flight"
case "$ROOT" in
  /mnt/*) die "PF_ROOT is on the Windows filesystem ($ROOT). Use a Linux path (~) — /mnt/c breaks permissions and Ansible ignores world-writable ansible.cfg." ;;
esac
if grep -qi microsoft /proc/version 2>/dev/null; then ok "WSL detected"; else warn "not WSL — continuing anyway"; fi
[[ "$(uname -m)" == x86_64 ]] || die "only amd64 is supported (pinned downloads are linux_amd64)"
ok "root: $ROOT"

missing=()
for p in "${APT_PKGS[@]}"; do dpkg -s "$p" &>/dev/null || missing+=("$p"); done
if ((${#missing[@]})); then
  log "Installing apt packages: ${missing[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y -qq "${missing[@]}"
fi
ok "apt packages present"

# Commits (make site, site repos) need an identity; a fresh WSL has none.
if ! git config --global user.email >/dev/null || ! git config --global user.name >/dev/null; then
  die "git has no identity. Run:  git config --global user.name 'Your Name' && git config --global user.email you@example.com"
fi
ok "git identity: $(git config --global user.name) <$(git config --global user.email)>"

# ----------------------------------------------------------------- tools ----
log "Tools -> $BIN"
mkdir -p "$BIN"
case ":$PATH:" in *":$BIN:"*) ;; *) export PATH="$BIN:$PATH" ;; esac
if ! grep -q '# PF_LOCAL_BIN' "$HOME/.bashrc" 2>/dev/null; then
  # shellcheck disable=SC2016  # $PATH must stay literal in .bashrc
  printf '\n# PF_LOCAL_BIN\ncase ":$PATH:" in *":%s:"*) ;; *) export PATH="%s:$PATH" ;; esac\n' "$BIN" "$BIN" >> "$HOME/.bashrc"
fi

if installed sops "$SOPS_VERSION"; then ok "sops $SOPS_VERSION"; else
  fetch "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64" \
    "$SOPS_SHA256" "$TMP/sops"
  install -m 0755 "$TMP/sops" "$BIN/sops"
  ok "sops $SOPS_VERSION installed"
fi

if installed gh "$GH_VERSION"; then ok "gh $GH_VERSION"; else
  fetch "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
    "$GH_SHA256" "$TMP/gh.tar.gz"
  tar -xzf "$TMP/gh.tar.gz" -C "$TMP"
  install -m 0755 "$TMP/gh_${GH_VERSION}_linux_amd64/bin/gh" "$BIN/gh"
  ok "gh $GH_VERSION installed"
fi

# ---------------------------------------------------------------- github ----
log "GitHub"
if ! gh_out="$(gh auth status --hostname github.com 2>&1)"; then
  die "gh is not logged in. Run:  gh auth login --hostname github.com --git-protocol https --web
    then re-run this script. (Site repos are usually private; git over HTTPS uses gh's token.)
    gh said: $gh_out"
fi
ok "gh: $(gh api user --jq .login 2>/dev/null || echo authenticated)"
# git (and therefore ansible-galaxy git+https collections in site repos)
# authenticate to github.com through gh.
gh auth setup-git --hostname github.com
ok "git credential helper -> gh"

# --------------------------------------------------------------- checkout ---
log "checkout -> $CHECKOUT"
sync_repo "$GH_REPO" "$CHECKOUT"
[[ -d "$COLL_DIR" ]] || die "$COLL_DIR not found — is $CHECKOUT a proxmox-netbox-ansible checkout?"
collections=()
for galaxy in "$COLL_DIR"/*/galaxy.yml; do
  name="$(basename "$(dirname "$galaxy")")"
  g_ns="$(awk '/^namespace:/{print $2; exit}' "$galaxy")"
  g_name="$(awk '/^name:/{print $2; exit}' "$galaxy")"
  [[ "$g_ns" == "packetflow" && "$g_name" == "$name" ]] \
    || die "$galaxy says $g_ns.$g_name, expected packetflow.$name"
  collections+=("$name")
done
ok "collections: ${collections[*]}"

# -------------------------------------------------------------------- venv ---
log "Python venv -> $VENV"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --upgrade -r "$CHECKOUT/.github/requirements-ci.txt" "${PY_EXTRA[@]}"
ok "$("$VENV/bin/ansible" --version | head -1)"
ok "copier $("$VENV/bin/copier" --version | awk '{print $NF}')"

# -------------------------------------------------------------- ansible.cfg -
log "ansible.cfg"
cat > "$CFG" <<EOF
# Managed by scripts/bootstrap-wsl.sh (proxmox-netbox-ansible): re-running overwrites this file.
[defaults]
# 1st: the checkout (its ansible_collections/packetflow/* are live, no reinstall)
# 2nd: third-party deps installed by ansible-galaxy (always install with -p $DEPS_DIR)
collections_path = $CHECKOUT:$DEPS_DIR
callback_result_format = yaml
interpreter_python = auto_silent
retry_files_enabled = False
EOF
ok "$CFG"
export ANSIBLE_CONFIG="$CFG"   # for the rest of this script

ACT="$VENV/bin/activate"
if ! grep -q '# PF_ANSIBLE_ENV' "$ACT"; then
  printf '\n# PF_ANSIBLE_ENV\nexport ANSIBLE_CONFIG="%s"\n' "$CFG" >> "$ACT"
fi
ok "venv activate exports ANSIBLE_CONFIG (site repos override it in their Makefile)"

if ! grep -q '# PF_CHECKOUT_ALIAS' "$HOME/.bashrc" 2>/dev/null; then
  printf '\n# PF_CHECKOUT_ALIAS\nalias pfa='\''source "%s/bin/activate" && cd "%s"'\''\n' "$VENV" "$CHECKOUT" >> "$HOME/.bashrc"
fi
ok "alias 'pfa' in ~/.bashrc (activate venv + cd to the checkout)"

# --------------------------------------------------------- galaxy deps ------
log "Third-party collections (ansible-galaxy) -> $DEPS_DIR"
mkdir -p "$DEPS_DIR"
for name in "${collections[@]}"; do
  req="$COLL_DIR/$name/requirements.yml"
  if [[ -f "$req" ]]; then
    "$VENV/bin/ansible-galaxy" collection install -r "$req" -p "$DEPS_DIR" --upgrade >/dev/null
    ok "packetflow.$name requirements installed"
  else
    ok "packetflow.$name has no requirements.yml"
  fi
done

# ----------------------------------------------------------------- verify ---
log "Verify"
fail=0
check() { # label command...
  local label="$1"; shift
  if out="$("$@" 2>&1 | head -1)"; then ok "$label: $out"; else warn "$label: FAILED ($out)"; fail=1; fi
}
check sops    "$BIN/sops" --version
check gh      "$BIN/gh" --version
check age     age --version
check copier  "$VENV/bin/copier" --version
for v in "sops:$SOPS_VERSION" "gh:$GH_VERSION"; do
  installed "${v%%:*}" "${v##*:}" || { warn "${v%%:*} is not ${v##*:}"; fail=1; }
done
for name in "${collections[@]}"; do
  if "$VENV/bin/ansible-galaxy" collection list "packetflow.$name" 2>/dev/null | grep -q "^# $CHECKOUT"; then
    ok "packetflow.$name resolves to the checkout"
  else
    warn "packetflow.$name NOT resolved from $CHECKOUT"; fail=1
  fi
done
echo
"$VENV/bin/ansible-galaxy" collection list 2>/dev/null \
  | grep -E '^(packetflow|community|netbox|ansible)\.' | sed 's/^/    /' || true

((fail)) && die "verification failed — see warnings above"

log "Done"
cat <<EOF
    Open a new shell (or: source ~/.bashrc), then:  pfa
    From the checkout:  make check | make site NAME=<slug>   (docs/setup.md)
    Layout:
      $CHECKOUT   this repo (collections live from here)
      $DEPS_DIR              third-party collections
      $VENV                  Python venv (ansible, molecule, pynetbox, copier)
      $CFG                   ansible.cfg (exported by the venv)
      $BIN                   sops, gh
EOF
