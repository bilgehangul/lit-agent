"""Build the plugin-managed venv at ~/.lit-agent/venv (spec section 4, /lit-setup phase A).

Run this with *any* Python 3.10+ interpreter; it creates the venv and installs
``requirements.txt`` into it. Everything else in lit-agent then runs under that venv, so
the user's system Python is never modified.

    python scripts/setup_env.py            # create or reuse, install core deps
    python scripts/setup_env.py --recreate # tear down and rebuild from scratch
    python scripts/setup_env.py --extra layout_text vector_index

Optional extras are deliberately *not* installed by default: spec section 4 requires the
core to stay pure-pip and cross-platform, and ADR-0001 keeps pymupdf4llm out of it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import venv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.console import setup as _setup_console

_setup_console()

from lib.paths import VENV_DIR, plugin_root, venv_python  # noqa: E402

MIN_PYTHON = (3, 10)

#: Extras keyed by the capability that needs them.
EXTRAS: dict[str, list[str]] = {
    "layout_text": ["pymupdf4llm>=0.0.17"],
    "vector_index": ["sqlite-vec>=0.1.6"],
}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def create_venv(recreate: bool = False) -> Path:
    if sys.version_info < MIN_PYTHON:
        raise SystemExit(
            f"lit-agent needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+, "
            f"but this is {sys.version.split()[0]}.")
    if recreate and VENV_DIR.exists():
        import shutil
        shutil.rmtree(VENV_DIR)
    if not venv_python().is_file():
        VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(VENV_DIR)
    return VENV_DIR


def install(packages: list[str], upgrade_pip: bool = False) -> subprocess.CompletedProcess:
    py = str(venv_python())
    if upgrade_pip:
        _run([py, "-m", "pip", "install", "--upgrade", "--quiet", "pip"])
    return _run([py, "-m", "pip", "install", "--quiet", *packages])


def installed_versions() -> dict[str, str]:
    proc = _run([str(venv_python()), "-m", "pip", "list", "--format=json"])
    if proc.returncode != 0:
        return {}
    try:
        return {p["name"].lower(): p["version"] for p in json.loads(proc.stdout)}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lit-setup-env")
    parser.add_argument("--recreate", action="store_true",
                        help="delete and rebuild the venv from scratch")
    parser.add_argument("--extra", nargs="*", default=[], choices=sorted(EXTRAS),
                        help="also install optional extras for these capabilities")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    req = plugin_root() / "requirements.txt"
    if not req.is_file():
        raise SystemExit(f"requirements.txt not found at {req}")

    create_venv(recreate=args.recreate)
    proc = install(["-r", str(req)], upgrade_pip=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-1500:]
        if args.json:
            print(json.dumps({"ok": False, "stage": "core", "error": detail}, indent=2))
        else:
            print("Installing core dependencies failed:\n", detail, file=sys.stderr)
            print("\nThis is a required capability. lit-agent cannot continue until it "
                  "installs cleanly.", file=sys.stderr)
        return 1

    extras_done, extras_failed = [], {}
    for cap in args.extra:
        result = install(EXTRAS[cap])
        if result.returncode == 0:
            extras_done.append(cap)
        else:
            # An extra that will not install is turned off with a reason, never left
            # half-configured (P1).
            extras_failed[cap] = (result.stderr or result.stdout).strip()[-400:]

    versions = installed_versions()
    payload = {
        "ok": True,
        "venv": str(VENV_DIR),
        "python": str(venv_python()),
        "core": {k: versions.get(k) for k in ("pymupdf", "httpx", "pyyaml", "bibtexparser")},
        "extras_installed": extras_done,
        "extras_failed": extras_failed,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"venv ready at {VENV_DIR}")
        for name, ver in payload["core"].items():
            print(f"  {name:14} {ver or 'MISSING'}")
        for cap in extras_done:
            print(f"  extra installed: {cap}")
        for cap, err in extras_failed.items():
            print(f"  extra FAILED (left off): {cap} -- {err.splitlines()[-1] if err else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
