"""CLI entry point:  ``python -m app.seed [--reset] [--if-empty]``.

Examples::

    python -m app.seed               # seed an empty database (fails if data exists)
    python -m app.seed --if-empty    # seed only if empty, otherwise exit 0 (used by docker compose)
    python -m app.seed --reset       # wipe business tables and re-seed
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from app.config import get_settings
from app.db import DatabaseUnavailableError, admin_connection
from app.logging_config import configure_logging
from app.seed.generator import SeedConfig, generate_dataset
from app.seed.loader import SeedError, database_has_data, load_dataset

logger = logging.getLogger("app.seed")

SUMMARY_SQL = """
SELECT
    (SELECT COUNT(*) FROM customers)                                        AS customers,
    (SELECT COUNT(*) FROM orders)                                           AS orders,
    (SELECT MIN(order_date)::date FROM orders)                              AS first_order,
    (SELECT MAX(order_date)::date FROM orders)                              AS last_order,
    (SELECT SUM(total_amount) FROM orders WHERE status <> 'cancelled')      AS total_revenue,
    (SELECT COUNT(*) FROM invoices WHERE status = 'overdue')                AS overdue_invoices
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the Business Data Assistant sample database.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reset", action="store_true", help="truncate existing business data first")
    group.add_argument("--if-empty", action="store_true", help="skip quietly if data already exists")
    parser.add_argument("--retries", type=int, default=10, help="connection attempts while the DB starts")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the seeder. Returns a process exit code."""
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    config = SeedConfig(
        seed=settings.seed_random_seed,
        num_customers=settings.seed_num_customers,
        num_employees=settings.seed_num_employees,
        num_products=settings.seed_num_products,
        num_orders=settings.seed_num_orders,
    )

    for attempt in range(1, args.retries + 1):
        try:
            with admin_connection(settings) as conn:
                if args.if_empty and database_has_data(conn):
                    logger.info("database already seeded; nothing to do (use --reset to re-seed)")
                    return 0
                dataset = generate_dataset(config)
                counts = load_dataset(conn, dataset, reset=args.reset)
                with conn.cursor() as cur:
                    cur.execute(SUMMARY_SQL)
                    summary = cur.fetchone()
            break
        except DatabaseUnavailableError:
            logger.warning("database not ready (attempt %d/%d), retrying in 2s", attempt, args.retries)
            time.sleep(2)
        except SeedError as exc:
            logger.error("%s", exc)
            return 1
    else:
        logger.error("giving up: database unreachable at %s:%s", settings.db_host, settings.db_port)
        return 2

    print("\n=== Seed summary ===")
    for table, n in counts.items():
        print(f"  {table:<20} {n:>8,} rows")
    if summary:
        print(f"  order date range     {summary['first_order']} -> {summary['last_order']}")
        print(f"  revenue (excl. cancelled)  ${summary['total_revenue']:,.2f}")
        print(f"  overdue invoices     {summary['overdue_invoices']:>8,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
