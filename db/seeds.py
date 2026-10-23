"""Startup seeding.

* `seed_essential()`  bureaus. Always runs, every environment. Idempotent.
* `_bootstrap_super_admin()`  production gets its first account ONLY from
  INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PIN. There is no automatic demo login.
* `seed_demo()`  demo accounts (all non-production starts) and, when enabled, the
  synthetic dataset from scripts/seed_aicte_demo.py. Never runs against a
  production database unless ALLOW_PROD_DEMO_SEED=1.

Everything here is SYNTHETIC. No real AICTE or personal data.
"""
import logging
import os

from config import Config
from db import bureaus, pool, users
from domain.constants import BUREAUS
from services.auth_service import hash_pin

log = logging.getLogger("samanvay.seeds")

DEMO_PIN = "1234"

# username, display name, role, bureau code, region
_DEMO_USERS = [
    ("superadmin",   "Dr. Meenakshi Rao",   "super_admin",   None,   None),
    ("pgrc.admin",   "Suresh Iyer",         "bureau_admin",  "PGRC", None),
    ("pgrc.officer", "Kavita Menon",        "officer",       "PGRC", None),
    ("pgrc.officer2", "Rohit Bansal",       "officer",       "PGRC", None),
    ("rb.admin",     "Anita Deshmukh",      "bureau_admin",  "RB",   None),
    ("rb.officer",   "Vikram Chauhan",      "officer",       "RB",   None),
    ("rb.officer2",  "Neha Kulkarni",       "officer",       "RB",   None),
    ("ab.admin",     "Harish Pillai",       "bureau_admin",  "AB",   None),
    ("ab.officer",   "Deepa Nair",          "officer",       "AB",   None),
    ("ac.admin",     "Prakash Joshi",       "bureau_admin",  "AC",   None),
    ("ac.officer",   "Farah Siddiqui",      "officer",       "AC",   None),
    ("vc.admin",     "Rajan Mehta",         "bureau_admin",  "VC",   None),
    ("vc.officer",   "Tanvi Shah",          "officer",       "VC",   None),
    ("ron.admin",    "Gurpreet Singh",      "bureau_admin",  "RO-N", "North"),
    ("ron.user",     "Manish Tiwari",       "regional_user", "RO-N", "North"),
    ("ros.admin",    "Lakshmi Narayanan",   "bureau_admin",  "RO-S", "South"),
    ("ros.user",     "Arvind Reddy",        "regional_user", "RO-S", "South"),
]

DEMO_ACCOUNTS = [
    {"username": u, "name": n, "role": r, "bureau": b, "pin": DEMO_PIN}
    for u, n, r, b, _reg in _DEMO_USERS
]


def seed_essential() -> None:
    """Bureaus every environment needs. Idempotent."""
    for code, name, region in BUREAUS:
        if not bureaus.get_by_code(code):
            bureaus.create(code, name, region)


def _bootstrap_super_admin() -> None:
    if users.list_all(role="super_admin"):
        return
    cfg_user, cfg_pin = Config.INITIAL_ADMIN_USERNAME, Config.INITIAL_ADMIN_PIN
    if cfg_user and cfg_pin:
        users.create(cfg_user, "Super Admin", "super_admin", hash_pin(cfg_pin),
                     created_by="bootstrap", must_change_pin=True)
        log.info("bootstrapped super admin %r from INITIAL_ADMIN_* env "
                 "(must change PIN on first login)", cfg_user)
        return
    if Config.is_production():
        raise RuntimeError(
            "SAMANVAY cannot start: no super admin exists and none is configured. "
            "Set INITIAL_ADMIN_USERNAME and INITIAL_ADMIN_PIN and redeploy.")


def _demo_allowed() -> bool:
    if not Config.is_production():
        return True
    if Config.ALLOW_PROD_DEMO_SEED:
        log.warning("ALLOW_PROD_DEMO_SEED=1: seeding DEMO data into a PRODUCTION database.")
        return True
    return False


def demo_loaded() -> bool:
    return bool(users.get_by_username("superadmin"))


def seed_demo_accounts() -> None:
    pin_hash = None
    for username, name, role, code, region in _DEMO_USERS:
        if users.get_by_username(username):
            continue
        pin_hash = pin_hash or hash_pin(DEMO_PIN)      # one hash for all demo accounts: fast boot
        b = bureaus.get_by_code(code) if code else None
        users.create(username, name, role, pin_hash, created_by="seed",
                     department=b["name"] if b else "AICTE Headquarters",
                     bureau_id=b["id"] if b else None, region=region)


def seed_demo_citizen() -> None:
    if not users.get_by_username("citizen.demo"):
        users.create("citizen.demo", "Aarav Sharma", "citizen", hash_pin("citizen123"),
                     contact="aarav.demo@example.invalid", created_by="seed")


def _dataset_wanted() -> bool:
    flag = os.environ.get("SEED_DEMO_DATA", "auto").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no", ""):
        return False
    return pool.STATE["mode"] == "memory"          # "auto": offline demo run


def seed_demo() -> None:
    if not _demo_allowed():
        log.info("production: demo seeding skipped")
        return
    seed_demo_accounts()
    seed_demo_citizen()
    if _dataset_wanted():
        try:
            from scripts.seed_aicte_demo import build
            log.info("synthetic demo dataset: %s", build())
        except ImportError:
            log.info("no demo dataset script present; accounts only")
        except Exception as e:  # noqa: BLE001 - seeding must never stop the app booting
            log.warning("demo dataset seed skipped: %s: %s", type(e).__name__, e)


def run() -> None:
    seed_essential()
    _bootstrap_super_admin()
    seed_demo()
    if pool.is_firestore():
        pool.flush_to_firestore()
