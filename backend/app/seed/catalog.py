"""Static reference data used by the seed generator.

Keeping hand-curated lists here (instead of pure random strings) is what makes
the fake data *feel* real in a demo: a Crozent-style catalogue of cloud,
database, API and automation products, real-looking geographies, and an org
chart with sensible job titles.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

REGIONS: Final[tuple[str, ...]] = ("North America", "Europe", "Asia Pacific", "Latin America")

# region -> list of (country, faker_locale, cities, tax_rate)
GEOGRAPHY: Final[dict[str, list[tuple[str, str, tuple[str, ...], Decimal]]]] = {
    "North America": [
        (
            "United States",
            "en_US",
            ("New York", "Austin", "Chicago", "San Francisco", "Seattle", "Atlanta", "Denver", "Boston"),
            Decimal("0.07"),
        ),
        ("Canada", "en_CA", ("Toronto", "Vancouver", "Montreal", "Calgary"), Decimal("0.13")),
    ],
    "Europe": [
        ("United Kingdom", "en_GB", ("London", "Manchester", "Edinburgh", "Bristol"), Decimal("0.20")),
        ("Germany", "de_DE", ("Berlin", "Munich", "Hamburg", "Frankfurt"), Decimal("0.19")),
        ("France", "fr_FR", ("Paris", "Lyon", "Toulouse"), Decimal("0.20")),
        ("Netherlands", "nl_NL", ("Amsterdam", "Rotterdam", "Utrecht"), Decimal("0.21")),
    ],
    "Asia Pacific": [
        ("India", "en_IN", ("Bengaluru", "Hyderabad", "Mumbai", "Pune", "Chennai", "New Delhi"), Decimal("0.18")),
        ("Australia", "en_AU", ("Sydney", "Melbourne", "Brisbane"), Decimal("0.10")),
        ("Singapore", "en_US", ("Singapore",), Decimal("0.09")),
        ("Japan", "en_US", ("Tokyo", "Osaka"), Decimal("0.10")),
    ],
    "Latin America": [
        ("Brazil", "pt_BR", ("Sao Paulo", "Rio de Janeiro", "Curitiba"), Decimal("0.17")),
        ("Mexico", "es_MX", ("Mexico City", "Guadalajara", "Monterrey"), Decimal("0.16")),
        ("Chile", "es_ES", ("Santiago",), Decimal("0.19")),
    ],
}

# Relative share of customers per region.
REGION_WEIGHTS: Final[dict[str, float]] = {
    "North America": 0.42,
    "Europe": 0.28,
    "Asia Pacific": 0.20,
    "Latin America": 0.10,
}

INDUSTRIES: Final[tuple[str, ...]] = (
    "Retail",
    "Healthcare",
    "Manufacturing",
    "Finance",
    "Logistics",
    "Education",
    "Hospitality",
    "Real Estate",
    "Media",
    "Energy",
    "Professional Services",
    "Government",
)

# segment -> (share of customers, orders-per-customer weight, quantity multiplier,
#             max discount %, payment terms days, credit limit range)
SEGMENTS: Final[dict[str, dict[str, object]]] = {
    "Enterprise": {
        "share": 0.15,
        "order_weight": 4.0,
        "qty_mult": 4,
        "max_discount": 0.12,
        "terms_days": 60,
        "credit": (100_000, 500_000),
    },
    "Mid-Market": {
        "share": 0.35,
        "order_weight": 2.0,
        "qty_mult": 2,
        "max_discount": 0.07,
        "terms_days": 45,
        "credit": (20_000, 100_000),
    },
    "SMB": {
        "share": 0.50,
        "order_weight": 1.0,
        "qty_mult": 1,
        "max_discount": 0.03,
        "terms_days": 30,
        "credit": (2_000, 20_000),
    },
}

# name, cost center, share of headcount, job titles (first title = department head)
DEPARTMENTS: Final[list[tuple[str, str, float, tuple[str, ...]]]] = [
    (
        "Sales",
        "CC-100",
        0.40,
        (
            "VP of Sales",
            "Sales Manager",
            "Account Executive",
            "Account Executive",
            "Senior Account Executive",
            "Sales Development Rep",
        ),
    ),
    (
        "Customer Success",
        "CC-200",
        0.15,
        ("Head of Customer Success", "Customer Success Manager", "Support Engineer", "Support Engineer"),
    ),
    (
        "Engineering",
        "CC-300",
        0.20,
        (
            "CTO",
            "Engineering Manager",
            "Software Engineer",
            "Senior Software Engineer",
            "DevOps Engineer",
            "Data Engineer",
        ),
    ),
    ("Finance", "CC-400", 0.08, ("CFO", "Finance Manager", "Accountant", "Billing Specialist")),
    ("Marketing", "CC-500", 0.08, ("CMO", "Marketing Manager", "Content Marketer", "Growth Analyst")),
    ("Operations", "CC-600", 0.05, ("COO", "Operations Manager", "Operations Analyst")),
    ("Human Resources", "CC-700", 0.04, ("Head of People", "HR Business Partner", "Recruiter")),
]

# Salary band (USD) per seniority keyword found in the title.
SALARY_BANDS: Final[list[tuple[tuple[str, ...], tuple[int, int]]]] = [
    (("VP", "CTO", "CFO", "CMO", "COO", "Head of"), (180_000, 260_000)),
    (("Manager",), (110_000, 165_000)),
    (("Senior",), (105_000, 150_000)),
    (("Engineer",), (85_000, 135_000)),
    (("Account Executive",), (70_000, 110_000)),
]
DEFAULT_SALARY_BAND: Final[tuple[int, int]] = (50_000, 85_000)

# category -> (description, is_service, [(base product name, list price range, cost ratio range)])
PRODUCT_CATALOG: Final[dict[str, tuple[str, bool, list[tuple[str, tuple[int, int], tuple[float, float]]]]]] = {
    "Cloud Hosting": (
        "Virtual servers, managed Kubernetes and object storage billed monthly.",
        True,
        [
            ("Cloud VM", (60, 900), (0.35, 0.55)),
            ("Managed Kubernetes Cluster", (400, 4000), (0.40, 0.60)),
            ("Object Storage", (20, 600), (0.25, 0.45)),
            ("Load Balancer", (30, 250), (0.30, 0.50)),
        ],
    ),
    "Managed Databases": (
        "Fully managed PostgreSQL, MySQL and Redis instances with backups.",
        True,
        [
            ("Managed PostgreSQL", (120, 3500), (0.40, 0.60)),
            ("Managed MySQL", (100, 3000), (0.40, 0.60)),
            ("Managed Redis", (60, 1200), (0.35, 0.55)),
            ("Database Backup Vault", (40, 500), (0.30, 0.50)),
        ],
    ),
    "Software Licenses": (
        "Annual per-seat licenses for business platforms.",
        True,
        [
            ("CRM Platform License", (300, 1500), (0.10, 0.25)),
            ("ERP Suite License", (800, 4000), (0.10, 0.25)),
            ("Analytics Dashboard License", (200, 1200), (0.10, 0.25)),
            ("Helpdesk License", (150, 700), (0.10, 0.25)),
        ],
    ),
    "API & Integration": (
        "API gateway, integration connectors and workflow automation.",
        True,
        [
            ("API Gateway Plan", (150, 2500), (0.20, 0.40)),
            ("Integration Connector", (90, 900), (0.15, 0.35)),
            ("Workflow Automation Bot", (250, 3000), (0.20, 0.40)),
            ("Webhook Relay", (40, 300), (0.20, 0.40)),
        ],
    ),
    "Hardware": (
        "Physical servers, networking gear and laptops for client sites.",
        False,
        [
            ("Rack Server", (2500, 12000), (0.65, 0.80)),
            ("Network Switch", (400, 3500), (0.60, 0.78)),
            ("Business Laptop", (900, 2600), (0.70, 0.85)),
            ("Firewall Appliance", (800, 6000), (0.60, 0.78)),
        ],
    ),
    "Professional Services": (
        "Consulting, migrations and custom development billed per engagement.",
        True,
        [
            ("Cloud Migration Package", (5000, 40000), (0.45, 0.65)),
            ("Custom Web Platform Build", (8000, 60000), (0.50, 0.70)),
            ("Database Performance Audit", (2000, 9000), (0.40, 0.60)),
            ("API Integration Project", (3000, 25000), (0.45, 0.65)),
        ],
    ),
    "Support Plans": (
        "Monthly support and maintenance contracts with SLAs.",
        True,
        [
            ("Standard Support Plan", (200, 800), (0.30, 0.50)),
            ("Premium 24x7 Support", (1000, 5000), (0.35, 0.55)),
            ("Managed Maintenance Retainer", (1500, 8000), (0.40, 0.60)),
        ],
    ),
    "Security": (
        "Security monitoring, backups and compliance tooling.",
        True,
        [
            ("Endpoint Protection", (30, 120), (0.20, 0.40)),
            ("SIEM Monitoring", (600, 5000), (0.30, 0.50)),
            ("Penetration Test", (4000, 20000), (0.40, 0.60)),
            ("Compliance Toolkit", (300, 2500), (0.20, 0.40)),
        ],
    ),
}

PRODUCT_TIERS: Final[tuple[str, ...]] = ("Starter", "Standard", "Professional", "Business", "Enterprise", "Plus", "Max")

PAYMENT_METHODS: Final[tuple[str, ...]] = ("bank_transfer", "credit_card", "ach", "check", "paypal")
PAYMENT_METHOD_WEIGHTS: Final[tuple[float, ...]] = (0.40, 0.25, 0.20, 0.05, 0.10)
