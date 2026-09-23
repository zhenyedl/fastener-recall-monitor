"""Scan version-control inputs without printing any matched secret value."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

PATTERNS = (
    ("possible tenant/data identifier", re.compile(r"\b[0-9a-f]{24}\b", re.I)),
    ("real widget identifier", re.compile(r"_widget_\d{8,}")),
    ("GitHub credential", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("private home path", re.compile(r"(?:[A-Z]:[\\/]Users[\\/][^\\/\s]+|/Users/[\w.-]+)", re.I)),
    ("PEM private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
PRIVATE_DIRS = {".recall", "state", "data", "logs", "backups", "private", ".venv", "__pycache__"}
PRIVATE_SUFFIXES = {".xlsx", ".xlsm", ".csv", ".db", ".sqlite", ".log", ".pyc", ".pem", ".key"}


def inspect(name, content):
    path = Path(name)
    failures = []
    if set(path.parts) & PRIVATE_DIRS or path.suffix.lower() in PRIVATE_SUFFIXES:
        failures.append("private/generated file")
    if path.name.startswith(".env") and path.name != ".env.example":
        failures.append("environment file")
    if path.name.startswith("config") and path.suffix == ".json" and path.name != "config.example.json":
        failures.append("installation config")
    for label, pattern in PATTERNS:
        if pattern.search(content):
            failures.append(label)
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true", help="Scan index contents, not working files")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = ["git", "ls-files", "-z"] if args.staged else ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    names = subprocess.check_output(command, cwd=root).decode("utf-8").split("\0")
    found = 0
    for name in sorted(set(filter(None, names))):
        if args.staged:
            content = subprocess.check_output(["git", "show", ":" + name], cwd=root).decode("utf-8")
        else:
            content = (root / name).read_text(encoding="utf-8")
        failures = inspect(name, content)
        if failures:
            found += 1
            print(name + ": " + ", ".join(failures))
    print("Public-file scan: %s" % ("FAIL" if found else "PASS"))
    return int(bool(found))


if __name__ == "__main__":
    sys.exit(main())
