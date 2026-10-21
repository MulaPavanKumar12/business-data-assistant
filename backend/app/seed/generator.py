"""Pure, deterministic generator for the sample business dataset.

The generator never touches the database. It returns plain Python rows, which
makes it fast to unit-test (referential integrity, money math, date ordering)
and keeps the loader trivial.

Realism features
----------------
* Org chart with department heads, managers and sales reps per region.
* Customers split into Enterprise / Mid-Market / SMB with different order
  frequency, basket size, discounts, payment terms and credit limits.
* Order volume grows over time and peaks in Q4 (seasonality), so trend and
  "last quarter" questions return interesting answers.
* Popular products (long-tail distribution) dominate sales.
* Order status depends on order age; invoices/payments follow from status;
  some invoices are overdue, some are paid in two instalments.
"""

from __future__ import annotations

import logging
import random
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from faker import Faker

from app.seed import catalog

logger = logging.getLogger(__name__)

Row = dict[str, Any]
CENT = Decimal("0.01")

# Column order per table. The loader uses these for COPY, so they are the
# single source of truth for "what the generator produces".
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "departments": ("department_id", "name", "cost_center", "created_at"),
    "employees": (
        "employee_id",
        "first_name",
        "last_name",
        "email",
        "job_title",
        "department_id",
        "manager_id",
        "hire_date",
        "salary",
        "region",
        "is_active",
    ),
    "customers": (
        "customer_id",
        "company_name",
        "contact_name",
        "email",
        "phone",
        "industry",
        "segment",
        "country",
        "city",
        "region",
        "account_manager_id",
        "credit_limit",
        "status",
        "created_at",
    ),
    "product_categories": ("category_id", "name", "description"),
    "products": (
        "product_id",
        "sku",
        "name",
        "category_id",
        "unit_price",
        "unit_cost",
        "stock_quantity",
        "is_active",
        "created_at",
    ),
    "orders": (
        "order_id",
        "customer_id",
        "sales_rep_id",
        "order_date",
        "status",
        "shipping_country",
        "subtotal",
        "discount_amount",
        "tax_amount",
        "total_amount",
        "updated_at",
    ),
    "order_items": ("order_item_id", "order_id", "product_id", "quantity", "unit_price", "line_total"),
    "invoices": (
        "invoice_id",
        "invoice_number",
        "order_id",
        "customer_id",
        "issue_date",
        "due_date",
        "amount",
        "status",
        "paid_date",
    ),
    "payments": ("payment_id", "invoice_id", "payment_date", "amount", "method", "reference"),
}

# Insert order that respects foreign keys.
LOAD_ORDER: tuple[str, ...] = tuple(TABLE_COLUMNS)


@dataclass(frozen=True)
class SeedConfig:
    """Knobs for the generator. Same config => byte-identical dataset."""

    seed: int = 42
    num_customers: int = 500
    num_employees: int = 60  # target; tiny values are rounded up so every department is staffed
    num_products: int = 120
    num_orders: int = 5000
    end_date: date = field(default_factory=lambda: datetime.now(UTC).date())
    history_years: int = 3

    def __post_init__(self) -> None:
        if self.num_customers < 10:
            raise ValueError("num_customers must be >= 10")
        if self.num_employees < 15:
            raise ValueError("num_employees must be >= 15 (one head + staff per department)")
        if self.num_products < 10:
            raise ValueError("num_products must be >= 10")
        if self.num_orders < 10:
            raise ValueError("num_orders must be >= 10")
        if self.history_years < 1:
            raise ValueError("history_years must be >= 1")

    @property
    def start_date(self) -> date:
        return self.end_date - timedelta(days=365 * self.history_years)


@dataclass
class Dataset:
    """Generated rows keyed by table name."""

    tables: dict[str, list[Row]]

    def counts(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self.tables.items()}

    def __getitem__(self, table: str) -> list[Row]:
        return self.tables[table]


def money(value: float | Decimal) -> Decimal:
    """Round a number to cents using banker-safe HALF_UP rounding."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def slugify(text: str) -> str:
    """ASCII, lowercase, dot-free slug suitable for an email local part / domain."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower()) or "company"


class BusinessDataGenerator:
    """Builds every table of the sample database from a :class:`SeedConfig`."""

    def __init__(self, config: SeedConfig) -> None:
        self.cfg = config
        self.rng = random.Random(config.seed)
        self._fakers: dict[str, Faker] = {}
        self._used_emails: set[str] = set()

    # ------------------------------------------------------------------ utils
    def _faker(self, locale: str) -> Faker:
        if locale not in self._fakers:
            fk = Faker(locale)
            fk.seed_instance(self.cfg.seed + len(self._fakers))
            self._fakers[locale] = fk
        return self._fakers[locale]

    def _unique_email(self, local: str, domain: str) -> str:
        base = f"{local}@{domain}"
        email, n = base, 1
        while email in self._used_emails:
            n += 1
            email = f"{local}{n}@{domain}"
        self._used_emails.add(email)
        return email

    def _random_date(self, start: date, end: date) -> date:
        span = max((end - start).days, 0)
        return start + timedelta(days=self.rng.randint(0, span))

    def _as_datetime(self, d: date) -> datetime:
        """Business-hours timestamp on a given day (UTC)."""
        t = time(hour=self.rng.randint(8, 19), minute=self.rng.randint(0, 59), second=self.rng.randint(0, 59))
        return datetime.combine(d, t, tzinfo=UTC)

    # ------------------------------------------------------------- generation
    def generate(self) -> Dataset:
        """Generate the full dataset in foreign-key order."""
        logger.info("generating dataset seed=%s end_date=%s", self.cfg.seed, self.cfg.end_date)
        departments = self._departments()
        employees = self._employees(departments)
        categories, products = self._products()
        customers = self._customers(employees)
        orders, items = self._orders(customers, employees, products)
        invoices, payments = self._invoices_and_payments(orders, customers)
        dataset = Dataset(
            tables={
                "departments": departments,
                "employees": employees,
                "customers": customers,
                "product_categories": categories,
                "products": products,
                "orders": orders,
                "order_items": items,
                "invoices": invoices,
                "payments": payments,
            }
        )
        logger.info("generated rows %s", dataset.counts())
        return dataset

    def _departments(self) -> list[Row]:
        created = datetime.combine(self.cfg.start_date - timedelta(days=365 * 2), time(9), tzinfo=UTC)
        return [
            {"department_id": i, "name": name, "cost_center": cc, "created_at": created}
            for i, (name, cc, _share, _titles) in enumerate(catalog.DEPARTMENTS, start=1)
        ]

    def _salary_for(self, title: str) -> Decimal:
        low, high = catalog.DEFAULT_SALARY_BAND
        for keywords, band in catalog.SALARY_BANDS:
            if any(k in title for k in keywords):
                low, high = band
                break
        return money(round(self.rng.uniform(low, high), -2))

    def _employees(self, departments: list[Row]) -> list[Row]:
        n_total = self.cfg.num_employees
        fk = self._faker("en_US")
        rows: list[Row] = []

        # Headcount per department, at least 2 (head + one staff member).
        dept_sizes = [max(2, round(share * n_total)) for _, _, share, _ in catalog.DEPARTMENTS]
        # Fix rounding so the total matches exactly; Sales absorbs the difference.
        dept_sizes[0] += n_total - sum(dept_sizes)
        dept_sizes[0] = max(dept_sizes[0], 2 + len(catalog.REGIONS))

        emp_id = 0
        for dept, size, (_, _, _, titles) in zip(departments, dept_sizes, catalog.DEPARTMENTS, strict=True):
            head_id: int | None = None
            for k in range(size):
                emp_id += 1
                first, last = fk.first_name(), fk.last_name()
                if k == 0:
                    title, manager = titles[0], None
                    head_id = emp_id
                else:
                    title, manager = self.rng.choice(titles[1:]), head_id
                # Sales reps are spread round-robin across regions so every region has coverage.
                if dept["name"] == "Sales" and k > 0:
                    region = catalog.REGIONS[(k - 1) % len(catalog.REGIONS)]
                else:
                    region = self.rng.choices(catalog.REGIONS, weights=list(catalog.REGION_WEIGHTS.values()))[0]
                rows.append(
                    {
                        "employee_id": emp_id,
                        "first_name": first,
                        "last_name": last,
                        "email": self._unique_email(f"{slugify(first)}.{slugify(last)}", "crozent-demo.com"),
                        "job_title": title,
                        "department_id": dept["department_id"],
                        "manager_id": manager,
                        "hire_date": self._random_date(
                            self.cfg.start_date - timedelta(days=365 * 2), self.cfg.end_date - timedelta(days=30)
                        ),
                        "salary": self._salary_for(title),
                        "region": region,
                        "is_active": self.rng.random() > 0.06 or k == 0,
                    }
                )
        return rows

    def _products(self) -> tuple[list[Row], list[Row]]:
        categories: list[Row] = []
        templates: list[tuple[int, str, bool, tuple[int, int], tuple[float, float]]] = []
        for cat_id, (name, (desc, is_service, items)) in enumerate(catalog.PRODUCT_CATALOG.items(), start=1):
            categories.append({"category_id": cat_id, "name": name, "description": desc})
            for base, price_rng, cost_rng in items:
                templates.append((cat_id, base, is_service, price_rng, cost_rng))

        products: list[Row] = []
        used_names: set[str] = set()
        for pid in range(1, self.cfg.num_products + 1):
            cat_id, base, is_service, (p_lo, p_hi), (c_lo, c_hi) = templates[(pid - 1) % len(templates)]
            tier_idx = ((pid - 1) // len(templates)) % len(catalog.PRODUCT_TIERS)
            name = f"{base} {catalog.PRODUCT_TIERS[tier_idx]}"
            if name in used_names:  # more products than template x tier combos
                name = f"{name} v{pid}"
            used_names.add(name)
            # Higher tiers cost more: scale within the template's range.
            tier_factor = (tier_idx + 1) / len(catalog.PRODUCT_TIERS)
            price = p_lo + (p_hi - p_lo) * tier_factor * self.rng.uniform(0.7, 1.0)
            price = money(round(price, 0) - 0.01 if price > 50 else round(price, 2))  # e.g. 499.99
            cost = money(price * Decimal(str(self.rng.uniform(c_lo, c_hi))))
            products.append(
                {
                    "product_id": pid,
                    "sku": f"{slugify(base)[:6].upper()}-{cat_id:02d}{pid:04d}",
                    "name": name,
                    "category_id": cat_id,
                    "unit_price": price,
                    "unit_cost": min(cost, price),
                    "stock_quantity": 100_000 if is_service else self.rng.randint(0, 400),
                    "is_active": self.rng.random() > 0.05,
                    "created_at": self._as_datetime(
                        self._random_date(self.cfg.start_date - timedelta(days=200), self.cfg.start_date)
                    ),
                }
            )
        return categories, products

    def _customers(self, employees: list[Row]) -> list[Row]:
        reps_by_region: dict[str, list[int]] = {r: [] for r in catalog.REGIONS}
        for e in employees:
            if e["department_id"] == 1 and e["manager_id"] is not None and e["is_active"]:
                reps_by_region[e["region"]].append(e["employee_id"])
        all_reps = [rep for reps in reps_by_region.values() for rep in reps]

        segments = list(catalog.SEGMENTS)
        seg_weights = [float(catalog.SEGMENTS[s]["share"]) for s in segments]  # type: ignore[arg-type]
        regions = list(catalog.REGION_WEIGHTS)
        reg_weights = list(catalog.REGION_WEIGHTS.values())
        used_names: set[str] = set()
        rows: list[Row] = []

        for cid in range(1, self.cfg.num_customers + 1):
            region = self.rng.choices(regions, weights=reg_weights)[0]
            country, locale, cities, _tax = self.rng.choice(catalog.GEOGRAPHY[region])
            fk = self._faker(locale)
            segment = self.rng.choices(segments, weights=seg_weights)[0]
            seg = catalog.SEGMENTS[segment]

            company = fk.company()
            while company in used_names:
                company = f"{fk.company()} {self.rng.choice(['Group', 'Holdings', 'Labs', 'Systems'])}"
            used_names.add(company)
            contact_first, contact_last = fk.first_name(), fk.last_name()
            domain = f"{slugify(company)[:24]}.example.com"

            status = self.rng.choices(["active", "churned", "prospect"], weights=[0.80, 0.12, 0.08])[0]
            credit_lo, credit_hi = seg["credit"]  # type: ignore[misc]
            reps = reps_by_region[region] or all_reps
            rows.append(
                {
                    "customer_id": cid,
                    "company_name": company,
                    "contact_name": f"{contact_first} {contact_last}",
                    "email": self._unique_email(slugify(contact_first)[:20] or "contact", domain),
                    "phone": fk.phone_number(),
                    "industry": self.rng.choice(catalog.INDUSTRIES),
                    "segment": segment,
                    "country": country,
                    "city": self.rng.choice(cities),
                    "region": region,
                    "account_manager_id": self.rng.choice(reps) if reps else None,
                    "credit_limit": money(round(self.rng.uniform(credit_lo, credit_hi), -3)),
                    "status": status,
                    # Leave the last 60 days for brand-new customers only.
                    "created_at": self._as_datetime(
                        self._random_date(self.cfg.start_date, self.cfg.end_date - timedelta(days=60))
                    ),
                }
            )
        return rows

    def _order_date(self, earliest: date, latest: date) -> date:
        """Sample an order date with an upward growth trend and Q4 seasonality."""
        total_span = (self.cfg.end_date - self.cfg.start_date).days or 1
        season = {
            1: 0.85,
            2: 0.85,
            3: 1.0,
            4: 0.95,
            5: 0.95,
            6: 1.0,
            7: 0.9,
            8: 0.85,
            9: 1.05,
            10: 1.15,
            11: 1.3,
            12: 1.4,
        }
        for _ in range(50):  # rejection sampling; acceptance rate is high
            d = self._random_date(earliest, latest)
            progress = (d - self.cfg.start_date).days / total_span  # 0 .. 1
            weight = (0.6 + 0.8 * progress) * season[d.month]  # max ~= 1.96
            if self.rng.random() * 2.0 <= weight:
                return d
        return self._random_date(earliest, latest)

    def _order_status(self, order_day: date) -> str:
        age = (self.cfg.end_date - order_day).days
        if self.rng.random() < 0.04:
            return "cancelled"
        if age > 21:
            return "delivered"
        if age > 7:
            return self.rng.choice(["shipped", "delivered"])
        if age > 2:
            return self.rng.choice(["processing", "shipped"])
        return self.rng.choice(["pending", "processing"])

    def _orders(self, customers: list[Row], employees: list[Row], products: list[Row]) -> tuple[list[Row], list[Row]]:
        buyers = [c for c in customers if c["status"] != "prospect"]
        buyer_weights = [float(catalog.SEGMENTS[c["segment"]]["order_weight"]) for c in buyers]  # type: ignore[arg-type]
        active_products = [p for p in products if p["is_active"]] or products
        # Long-tail popularity: a few best sellers, many slow movers.
        product_weights = [1.0 / (rank + 1) ** 0.8 for rank in range(len(active_products))]
        self.rng.shuffle(product_weights)
        tax_by_country = {c: tax for geo in catalog.GEOGRAPHY.values() for c, _l, _ci, tax in geo}
        sales_reps = [e["employee_id"] for e in employees if e["department_id"] == 1 and e["manager_id"]]

        orders: list[Row] = []
        items: list[Row] = []
        item_id = 0
        for oid in range(1, self.cfg.num_orders + 1):
            cust = self.rng.choices(buyers, weights=buyer_weights)[0]
            seg = catalog.SEGMENTS[cust["segment"]]
            earliest = cust["created_at"].date()
            latest = self.cfg.end_date
            if cust["status"] == "churned":  # churned customers stopped buying ~6 months ago
                latest = max(earliest, self.cfg.end_date - timedelta(days=180))
            order_day = self._order_date(earliest, latest)
            order_ts = self._as_datetime(order_day)
            status = self._order_status(order_day)

            subtotal = Decimal("0.00")
            n_lines = self.rng.choices([1, 2, 3, 4, 5], weights=[0.35, 0.30, 0.18, 0.11, 0.06])[0]
            chosen = {p["product_id"]: p for p in self.rng.choices(active_products, weights=product_weights, k=n_lines)}
            for product in chosen.values():
                item_id += 1
                qty = self.rng.randint(1, 3) * int(seg["qty_mult"])  # type: ignore[call-overload]
                if product["unit_price"] > 5000:  # big-ticket services are bought one at a time
                    qty = 1
                # Negotiated price: list price minus up to 5% for volume.
                unit_price = money(product["unit_price"] * Decimal(str(1 - self.rng.uniform(0, 0.05))))
                line_total = money(unit_price * qty)
                subtotal += line_total
                items.append(
                    {
                        "order_item_id": item_id,
                        "order_id": oid,
                        "product_id": product["product_id"],
                        "quantity": qty,
                        "unit_price": unit_price,
                        "line_total": line_total,
                    }
                )

            discount = money(subtotal * Decimal(str(self.rng.uniform(0, float(seg["max_discount"])))))  # type: ignore[arg-type]
            tax = money((subtotal - discount) * tax_by_country[cust["country"]])
            total = subtotal - discount + tax
            rep = cust["account_manager_id"] if self.rng.random() < 0.85 else self.rng.choice(sales_reps)
            updated = order_ts + timedelta(days=min((self.cfg.end_date - order_day).days, self.rng.randint(0, 14)))
            orders.append(
                {
                    "order_id": oid,
                    "customer_id": cust["customer_id"],
                    "sales_rep_id": rep,
                    "order_date": order_ts,
                    "status": status,
                    "shipping_country": cust["country"],
                    "subtotal": subtotal,
                    "discount_amount": discount,
                    "tax_amount": tax,
                    "total_amount": total,
                    "updated_at": updated,
                }
            )
        # Sort chronologically so order_id increases with order_date (like a real system).
        return self._renumber_orders(orders, items)

    @staticmethod
    def _renumber_orders(orders: list[Row], items: list[Row]) -> tuple[list[Row], list[Row]]:
        orders.sort(key=lambda o: (o["order_date"], o["order_id"]))
        mapping = {o["order_id"]: new_id for new_id, o in enumerate(orders, start=1)}
        for o in orders:
            o["order_id"] = mapping[o["order_id"]]
        for it in items:
            it["order_id"] = mapping[it["order_id"]]
        items.sort(key=lambda i: (i["order_id"], i["order_item_id"]))
        for new_id, it in enumerate(items, start=1):
            it["order_item_id"] = new_id
        return orders, items

    def _invoices_and_payments(self, orders: list[Row], customers: list[Row]) -> tuple[list[Row], list[Row]]:
        cust_by_id = {c["customer_id"]: c for c in customers}
        today = self.cfg.end_date
        invoices: list[Row] = []
        payments: list[Row] = []
        pay_id = 0
        for order in orders:
            if order["status"] not in ("shipped", "delivered"):
                continue
            cust = cust_by_id[order["customer_id"]]
            terms = int(catalog.SEGMENTS[cust["segment"]]["terms_days"])  # type: ignore[call-overload]
            issue = min(order["order_date"].date() + timedelta(days=self.rng.randint(0, 3)), today)
            due = issue + timedelta(days=terms)

            roll = self.rng.random()
            paid_date: date | None = None
            if roll < 0.01:
                status = "void"
            elif due < today:
                # Past due date: most are paid (some late), a few are overdue.
                if roll < 0.93:
                    status = "paid"
                    paid_date = min(issue + timedelta(days=self.rng.randint(5, terms + 20)), today)
                else:
                    status = "overdue"
            else:
                if roll < 0.35 and issue < today:
                    status = "paid"
                    paid_date = self._random_date(issue + timedelta(days=1), today)
                else:
                    status = "sent"

            inv_id = len(invoices) + 1
            invoices.append(
                {
                    "invoice_id": inv_id,
                    "invoice_number": f"INV-{issue.year}-{inv_id:06d}",
                    "order_id": order["order_id"],
                    "customer_id": order["customer_id"],
                    "issue_date": issue,
                    "due_date": due,
                    "amount": order["total_amount"],
                    "status": status,
                    "paid_date": paid_date,
                }
            )

            if status == "paid" and paid_date is not None:
                method = self.rng.choices(catalog.PAYMENT_METHODS, weights=catalog.PAYMENT_METHOD_WEIGHTS)[0]
                amount: Decimal = order["total_amount"]
                splits: Sequence[tuple[date, Decimal]]
                if amount > Decimal("1000") and self.rng.random() < 0.15 and paid_date > issue:
                    first = money(amount * Decimal(str(self.rng.uniform(0.3, 0.7))))
                    splits = [(self._random_date(issue, paid_date), first), (paid_date, amount - first)]
                else:
                    splits = [(paid_date, amount)]
                for pay_date, pay_amount in splits:
                    if pay_amount <= 0:
                        continue
                    pay_id += 1
                    payments.append(
                        {
                            "payment_id": pay_id,
                            "invoice_id": inv_id,
                            "payment_date": pay_date,
                            "amount": pay_amount,
                            "method": method,
                            "reference": f"PAY-{pay_date:%Y%m%d}-{pay_id:07d}",
                        }
                    )
        return invoices, payments


def generate_dataset(config: SeedConfig | None = None) -> Dataset:
    """Convenience wrapper: ``generate_dataset(SeedConfig(seed=1))``."""
    return BusinessDataGenerator(config or SeedConfig()).generate()
