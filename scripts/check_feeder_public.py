"""Scan candidate additions, not unrelated upstream history, before publication."""

from ipaddress import ip_address, ip_network
from pathlib import Path
import re
import subprocess

BASE = "7f3be1999f581863ffa984e86ed359857f8bcab1"
ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
    r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}",
    r"op://",
    r"(?i)(?:local_key|client_secret|access_token|password)\s*[=:]\s*['\"][^'\"]{6,}['\"]",
    r"(?i)nyx|yoolax",
]
TEST_NET = ip_network("192.0.2.0/24")


def main():
    """Fail closed on added credentials, household names or non-fixture IPs."""
    diff = subprocess.check_output(
        ["git", "diff", "--no-ext-diff", "--unified=0", BASE, "--", "."],
        cwd=ROOT,
        text=True,
    )
    filename = ""
    checked = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            filename = line[6:]
            continue
        if not line.startswith("+") or filename == "scripts/check_feeder_public.py":
            continue
        checked += 1
        if any(re.search(pattern, line) for pattern in PATTERNS):
            raise SystemExit(f"{filename}: sensitive added-line pattern")
        for value in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line):
            address = ip_address(value)
            if not (filename.startswith("feeder_tests/") and address in TEST_NET):
                raise SystemExit(f"{filename}: non-fixture IPv4 address")
    if not checked:
        raise SystemExit("No candidate additions scanned")
    print(f"Public added-line gate passed ({checked} lines); manual provenance review required.")


if __name__ == "__main__":
    main()
