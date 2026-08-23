from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TRACKED = (
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"\.(db|db-shm|db-wal)$", re.IGNORECASE),
    re.compile(r"(^|/)(logs|data_cache|audit_exports|output|tmp|dist|node_modules)/"),
)
SECRET_ASSIGNMENT = re.compile(
    rb"(?im)^\s*(OKX_(?:API_KEY|SECRET_KEY|PASSPHRASE)|api[_-]?key|secret|passphrase)\s*[:=]\s*['\"]?([^\s'\"#]{8,})"
)
PLACEHOLDERS = {b"your_api_key", b"your_secret_key", b"your_passphrase", b"changeme", b"placeholder"}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def main() -> int:
    failures: list[str] = []
    for relative in tracked_files():
        if relative == ".env.example":
            continue
        if any(pattern.search(relative) for pattern in FORBIDDEN_TRACKED):
            failures.append(f"forbidden tracked artifact: {relative}")
            continue
        path = ROOT / relative
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if b"\0" in content:
            continue
        for match in SECRET_ASSIGNMENT.finditer(content):
            value = match.group(2).lower()
            if value not in PLACEHOLDERS and not value.startswith((b"example", b"test-", b"dummy")):
                failures.append(f"possible secret assignment: {relative}")
                break
    if failures:
        print("REPOSITORY_SAFETY_CHECK_FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("REPOSITORY_SAFETY_CHECK_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
