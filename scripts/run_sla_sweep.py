"""Create due-soon / overdue notifications. Idempotent: run it as often as you like.

    python -m scripts.run_sla_sweep

(The dashboard also runs the same sweep on every load.) In-memory mode starts
empty on each process, so this is mainly useful against PostgreSQL.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    from app import create_app
    from services import sla_service
    create_app()
    made = sla_service.run_sweep()
    print(f"created {made} notification(s); SLA summary: {sla_service.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
