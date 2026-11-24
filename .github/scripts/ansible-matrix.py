#!/usr/bin/env python3
"""Compute the CI matrix for ansible.yml.

Prints two GitHub Actions outputs:
  collections=["base", ...]                        collections to lint + build
  roles=[{"collection": "zabbix", "role": "agent"}]  roles with a molecule/ dir (none today)

A collection is selected when any file under it changed. Everything is
selected on workflow_dispatch, when the diff base is unknown, or when the
CI plumbing itself changed.

Usage: ansible-matrix.py [BASE_REF]   (no BASE_REF -> select everything)
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
COLLECTIONS_DIR = ROOT / "ansible_collections" / "packetflow"
PREFIX = "ansible_collections/packetflow/"
CI_FILES = (".github/workflows/ansible.yml", ".github/scripts/ansible-matrix.py",
            ".github/requirements-ci.txt")


def changed_files(base: str) -> list[str] | None:
    if not base or set(base) == {"0"}:
        return None
    try:
        out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                             cwd=ROOT, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError:
        return None  # base not in history (force push, shallow clone): test everything
    return out.splitlines()


def main() -> None:
    every = sorted(p.parent.name for p in COLLECTIONS_DIR.glob("*/galaxy.yml"))
    files = changed_files(sys.argv[1] if len(sys.argv) > 1 else "")

    if files is None or any(f in CI_FILES for f in files):
        selected = every
    else:
        touched = {f[len(PREFIX):].split("/", 1)[0] for f in files if f.startswith(PREFIX)}
        selected = [c for c in every if c in touched]

    roles = [
        {"collection": c, "role": scenario.parents[2].name}
        for c in selected
        for scenario in sorted(COLLECTIONS_DIR.glob(f"{c}/roles/*/molecule/default/molecule.yml"))
    ]

    print(f"collections={json.dumps(selected)}")
    print(f"roles={json.dumps(roles)}")


if __name__ == "__main__":
    main()
