# proxmox-netbox-ansible: dev entry points. Full walkthrough: docs/setup.md
#
#   make setup            install/update the WSL dev environment (idempotent)
#   make check            run CI's lint + tests locally
#   make site NAME=acme   new site repo at ~/packetflow/site-acme from site-template/
#   make site NAME=acme ANSWERS=~/packetflow/answers/acme.yml   same, answers from a file (no questions)
#   make test-template NODE=pve01 [DISTRO=debian12] [METHOD=ssh HOST=<ip>] [FORCE=1]   test template (docs/templates.md)
#
# Tools come from ~/packetflow/.venv and ~/.local/bin (both created by `make setup`);
# the venv doesn't need to be activated.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PF_ROOT ?= $(HOME)/packetflow
VENV    ?= $(PF_ROOT)/.venv
export PATH := $(VENV)/bin:$(HOME)/.local/bin:$(PATH)
export ANSIBLE_CONFIG := $(PF_ROOT)/ansible.cfg

COLLECTIONS := $(wildcard ansible_collections/packetflow/*)
# The site template is this repo (copier.yml + site-template/). Copier takes the
# latest tag; TEMPLATE=$(CURDIR) REF=HEAD renders this checkout.
TEMPLATE    ?= gh:Packetflow-be/proxmox-netbox-ansible

.PHONY: help setup check check-ansible check-tests check-scripts site test-template

help:
	@sed -n 's/^#   make /  make /p' $(MAKEFILE_LIST)

setup:
	PF_ROOT=$(PF_ROOT) bash scripts/bootstrap-wsl.sh

check: check-ansible check-tests check-scripts
	@echo "all checks passed"

# Lint this checkout + third-party deps.
check-ansible:
	@for c in $(COLLECTIONS); do \
	  echo "== $$c"; \
	  (cd $$c && yamllint --strict . \
	    && ANSIBLE_COLLECTIONS_PATH=$(CURDIR):$(PF_ROOT)/.deps ansible-lint -q); \
	done

# Collection unit tests (pytest) and integration tests (tests/integration/*/run.sh).
check-tests:
	@for c in $(COLLECTIONS); do \
	  if [ -d $$c/tests/unit ]; then echo "== $$c unit"; (cd $$c && pytest -q tests/unit); fi; \
	  for t in $$c/tests/integration/*/run.sh; do \
	    [ -x "$$t" ] || continue; \
	    echo "== $$t"; ANSIBLE_COLLECTIONS_PATH=$(CURDIR):$(PF_ROOT)/.deps "$$t"; \
	  done; \
	done

check-scripts:
	shellcheck scripts/*.sh
	ruff check scripts/
	pytest -q scripts/tests

# REF=<tag|branch> pins the template version (default: its latest tag).
# ANSWERS=<file> takes every answer from a YAML file and asks nothing (Copier
# --defaults --data-file); its site_slug must match NAME.
# COPIER_ARGS passes extra flags to Copier.
site:
	@[[ "$(NAME)" =~ ^[a-z0-9_]+$$ ]] || { echo "usage: make site NAME=<site_slug>  (a-z, 0-9, _)" >&2; exit 2; }
	@[ ! -e "$(PF_ROOT)/site-$(NAME)" ] || { echo "$(PF_ROOT)/site-$(NAME) already exists" >&2; exit 1; }
	@[ -z "$(ANSWERS)" ] || [ -f "$(ANSWERS)" ] || { echo "ANSWERS: $(ANSWERS) not found" >&2; exit 2; }
	@[ -z "$(ANSWERS)" ] || [ "$$(sed -n 's/^site_slug:[[:space:]]*//p' "$(ANSWERS)" | tr -d "\"'")" = "$(NAME)" ] \
	  || { echo "ANSWERS: site_slug in $(ANSWERS) isn't $(NAME)" >&2; exit 2; }
	copier copy $(if $(REF),--vcs-ref $(REF)) $(if $(ANSWERS),--defaults --data-file $(ANSWERS)) $(COPIER_ARGS) --data site_slug=$(NAME) $(TEMPLATE) $(PF_ROOT)/site-$(NAME)
	cd $(PF_ROOT)/site-$(NAME) && git init -q -b main && git add -A && git commit -q -m "Initial site from site-template"
	@echo
	@echo "site-$(NAME) is a git repo with a first commit. Continue with the steps above,"
	@echo "starting with: cd $(PF_ROOT)/site-$(NAME) && make secrets   (docs/setup.md, steps 4-5)"

# Test template tpl-<distro>-test with a password login. Every distro has its own
# test ID. METHOD=api (default): through the Proxmox API, the vendor's image as
# is; credentials PROXMOX_* from the environment, else from ./.env (rights in
# docs/templates.md). METHOD=ssh: as root over SSH on HOST (the node's address),
# the image customized: guest agent and SSH password login.
METHOD        ?= api
DISTRO        ?= ubuntu2404
TEST_USER     ?= debian
TEST_PASSWORD ?= debian
TEST_SSH_KEYS ?= $(wildcard $(HOME)/.ssh/authorized_keys)
# METHOD=ssh only: service account baked into the image, key login + sudo.
SERVICE_USER  ?= automation
SERVICE_KEYS  ?= $(wildcard $(HOME)/.ssh/id_ed25519.pub)
TEST_VMIDS    := ubuntu2404=9900 ubuntu2204=9901 debian12=9902 debian13=9903 ubuntu2604=9904
COMMA         := ,
TEST_TARGET    = $(if $(filter ssh,$(METHOD)),-i '$(HOST)$(COMMA)' -u root -e template_method=ssh,-i 'localhost$(COMMA)' -c local -e template_method=api -e template_node=$(NODE))
test-template:
	@[ -n "$(if $(filter ssh,$(METHOD)),$(HOST),$(NODE))" ] || { echo "usage: make test-template NODE=<node name> [DISTRO=ubuntu2404] [VMID=99xx] [FORCE=1]" >&2; \
	  echo "       make test-template METHOD=ssh HOST=<node address> [DISTRO=...] [VMID=99xx] [FORCE=1]" >&2; exit 2; }
	@if [ "$(METHOD)" = api ] && [ -z "$${PROXMOX_TOKEN_SECRET:-}" ] && [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	vmid="$(VMID)"; [ -n "$$vmid" ] || vmid="$$(printf '%s\n' $(TEST_VMIDS) | sed -n 's/^$(DISTRO)=//p')"; \
	[ -n "$$vmid" ] || { echo "no test VM ID for $(DISTRO): pass VMID=99xx" >&2; exit 2; }; \
	ansible-playbook $(TEST_TARGET) packetflow.proxmox.template \
	  -e template_distro=$(DISTRO) -e template_vmid=$$vmid -e template_name=tpl-$(DISTRO)-test \
	  -e template_zabbix_agent=false \
	  -e template_test_user=$(TEST_USER) -e template_test_password=$(TEST_PASSWORD) \
	  $(if $(TEST_SSH_KEYS),-e template_test_ssh_keys_file=$(TEST_SSH_KEYS)) \
	  $(if $(and $(filter ssh,$(METHOD)),$(SERVICE_USER),$(SERVICE_KEYS)),-e template_service_user=$(SERVICE_USER) -e template_service_ssh_keys_file=$(SERVICE_KEYS)) \
	  $(if $(FORCE),-e template_force=true)
