"""Manual production-safety smoke check (no real database needed).

Each scenario runs in a fresh Python subprocess and asserts SAMANVAY either
boots cleanly or FAILS LOUDLY — never silently degrades to in-memory.

Run:  python scripts/check_production_boot.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_BOOT = (
    "import sys; "
    "from app import create_app; "
    "from db import pool; "
    "create_app(); "
    "print('BOOT_OK mode=' + pool.STATE['mode'])"
)

_GOOD = "x" * 45

SCENARIOS = [
    ("prod / no secrets / no db",
     {"APP_ENV": "production"}, "raise"),
    ("prod / good secrets / no DATABASE_URL",
     {"APP_ENV": "production", "SECRET_KEY": _GOOD, "JWT_SECRET": _GOOD}, "raise"),
    ("prod / good secrets / unreachable DATABASE_URL / no firestore",
     {"APP_ENV": "production", "SECRET_KEY": _GOOD, "JWT_SECRET": _GOOD,
      "DATABASE_URL": "postgresql://127.0.0.1:1/nope",
      "INITIAL_ADMIN_USERNAME": "sir", "INITIAL_ADMIN_PIN": "246810"}, "raise"),
    ("dev / nothing configured",
     {"APP_ENV": "development"}, "memory"),
]

_CLEAR = ("APP_ENV", "SECRET_KEY", "JWT_SECRET", "DATABASE_URL",
          "INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PIN",
          "FIREBASE_KEY_JSON", "FIREBASE_CREDENTIALS_B64",
          "FIREBASE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS",
          "ALLOW_FIRESTORE_IN_DEV", "SEED_DEMO")

failures = 0
for name, overrides, expect in SCENARIOS:
    env = {k: v for k, v in os.environ.items() if k not in _CLEAR}
    for k in _CLEAR:
        env[k] = ""          # block .env from filling it back in
    env.update(overrides)
    env["PYTHONPATH"] = HERE

    p = subprocess.run([sys.executable, "-c", _BOOT], cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=120)
    out = (p.stdout + p.stderr).strip().replace("\n", " | ")

    if expect == "raise":
        ok = p.returncode != 0 and "BOOT_OK" not in p.stdout
        verdict = "exited non-zero (fails loudly)" if ok else "DID NOT FAIL"
    else:  # expect a clean in-memory dev boot
        ok = p.returncode == 0 and "BOOT_OK mode=memory" in p.stdout
        verdict = "booted in-memory (dev)" if ok else "unexpected result"

    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {verdict}")
    if not ok:
        failures += 1
        print(f"        rc={p.returncode}  out={out[:300]}")

print()
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print(f"all {len(SCENARIOS)} production-boot checks passed")
