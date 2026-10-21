-- =====================================================================
-- db/init/01_schema.sql
-- Business Data Assistant - sample business database schema.
--
-- Runs automatically the FIRST time the postgres container starts with
-- an empty volume (docker-entrypoint-initdb.d). To re-run it:
--     docker compose down -v && docker compose up --build
--
-- Design notes
--   * Every table and column has a COMMENT. In Phase 2 these comments are
--     embedded into a vector store, so the LLM can find the right tables
--     ("schema RAG") instead of receiving the whole schema in every prompt.
--   * Money is NUMERIC(12,2) (never FLOAT) to avoid rounding errors.
--   * CHECK constraints encode business rules, so bad data cannot get in
--     even if an API bug tries to write it.
--   * Audit logs live in a separate "audit" schema that the read-only AI
--     role cannot see.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS audit;

-- ---------------------------------------------------------------------
-- Organisation
-- ---------------------------------------------------------------------
CREATE TABLE departments (
    department_id   SERIAL PRIMARY KEY,
    name            VARCHAR(80)  NOT NULL UNIQUE,
    cost_center     VARCHAR(20)  NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  departments IS 'Company departments such as Sales, Support, Engineering and Finance.';
COMMENT ON COLUMN departments.department_id IS 'Primary key of the department.';
COMMENT ON COLUMN departments.name          IS 'Department name, e.g. Sales, Finance, Engineering.';
COMMENT ON COLUMN departments.cost_center   IS 'Accounting cost-center code for the department, e.g. CC-100.';
COMMENT ON COLUMN departments.created_at    IS 'When the department record was created.';

CREATE TABLE employees (
    employee_id     SERIAL PRIMARY KEY,
    first_name      VARCHAR(60)  NOT NULL,
    last_name       VARCHAR(60)  NOT NULL,
    email           VARCHAR(120) NOT NULL UNIQUE,
    job_title       VARCHAR(80)  NOT NULL,
    department_id   INTEGER      NOT NULL REFERENCES departments(department_id),
    manager_id      INTEGER      REFERENCES employees(employee_id),
    hire_date       DATE         NOT NULL,
    salary          NUMERIC(12,2) NOT NULL CHECK (salary > 0),
    region          VARCHAR(20)  NOT NULL CHECK (region IN ('North America','Europe','Asia Pacific','Latin America')),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE  employees IS 'Company staff, including sales representatives who own customer accounts.';
COMMENT ON COLUMN employees.employee_id   IS 'Primary key of the employee.';
COMMENT ON COLUMN employees.first_name    IS 'Employee first name.';
COMMENT ON COLUMN employees.last_name     IS 'Employee last name.';
COMMENT ON COLUMN employees.email         IS 'Work email address of the employee.';
COMMENT ON COLUMN employees.job_title     IS 'Job title, e.g. Account Executive, Sales Manager, Support Engineer.';
COMMENT ON COLUMN employees.department_id IS 'Department the employee belongs to (departments.department_id).';
COMMENT ON COLUMN employees.manager_id    IS 'Employee''s direct manager (employees.employee_id). NULL for top-level leaders.';
COMMENT ON COLUMN employees.hire_date     IS 'Date the employee joined the company.';
COMMENT ON COLUMN employees.salary        IS 'Annual base salary in USD. Sensitive: restricted by RBAC in later phases.';
COMMENT ON COLUMN employees.region        IS 'Sales region the employee covers: North America, Europe, Asia Pacific or Latin America.';
COMMENT ON COLUMN employees.is_active     IS 'FALSE if the employee has left the company.';

-- ---------------------------------------------------------------------
-- Customers
-- ---------------------------------------------------------------------
CREATE TABLE customers (
    customer_id     SERIAL PRIMARY KEY,
    company_name    VARCHAR(120) NOT NULL,
    contact_name    VARCHAR(120) NOT NULL,
    email           VARCHAR(120) NOT NULL UNIQUE,
    phone           VARCHAR(40),
    industry        VARCHAR(40)  NOT NULL,
    segment         VARCHAR(20)  NOT NULL CHECK (segment IN ('Enterprise','Mid-Market','SMB')),
    country         VARCHAR(60)  NOT NULL,
    city            VARCHAR(80)  NOT NULL,
    region          VARCHAR(20)  NOT NULL CHECK (region IN ('North America','Europe','Asia Pacific','Latin America')),
    account_manager_id INTEGER   REFERENCES employees(employee_id),
    credit_limit    NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (credit_limit >= 0),
    status          VARCHAR(20)  NOT NULL DEFAULT 'active' CHECK (status IN ('active','churned','prospect')),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  customers IS 'Business customers (client companies) that buy products. Use for revenue by customer, segment, industry or region.';
COMMENT ON COLUMN customers.customer_id        IS 'Primary key of the customer.';
COMMENT ON COLUMN customers.company_name       IS 'Legal or trading name of the customer company.';
COMMENT ON COLUMN customers.contact_name       IS 'Main contact person at the customer.';
COMMENT ON COLUMN customers.email              IS 'Billing / contact email of the customer.';
COMMENT ON COLUMN customers.phone              IS 'Contact phone number.';
COMMENT ON COLUMN customers.industry           IS 'Customer industry, e.g. Retail, Healthcare, Manufacturing, Finance.';
COMMENT ON COLUMN customers.segment            IS 'Customer size segment: Enterprise, Mid-Market or SMB.';
COMMENT ON COLUMN customers.country            IS 'Country where the customer is based.';
COMMENT ON COLUMN customers.city               IS 'City where the customer is based.';
COMMENT ON COLUMN customers.region             IS 'Sales region: North America, Europe, Asia Pacific or Latin America.';
COMMENT ON COLUMN customers.account_manager_id IS 'Sales rep who owns the account (employees.employee_id).';
COMMENT ON COLUMN customers.credit_limit       IS 'Maximum outstanding unpaid invoice amount allowed, in USD.';
COMMENT ON COLUMN customers.status             IS 'Lifecycle status: active, churned or prospect.';
COMMENT ON COLUMN customers.created_at         IS 'Date the customer signed up / was created.';

-- ---------------------------------------------------------------------
-- Products
-- ---------------------------------------------------------------------
CREATE TABLE product_categories (
    category_id     SERIAL PRIMARY KEY,
    name            VARCHAR(80) NOT NULL UNIQUE,
    description     TEXT
);
COMMENT ON TABLE  product_categories IS 'Product categories such as Cloud Hosting, Software Licenses, Hardware, Professional Services.';
COMMENT ON COLUMN product_categories.category_id IS 'Primary key of the category.';
COMMENT ON COLUMN product_categories.name        IS 'Category name.';
COMMENT ON COLUMN product_categories.description IS 'Short description of what the category contains.';

CREATE TABLE products (
    product_id      SERIAL PRIMARY KEY,
    sku             VARCHAR(30)  NOT NULL UNIQUE,
    name            VARCHAR(120) NOT NULL,
    category_id     INTEGER      NOT NULL REFERENCES product_categories(category_id),
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
    unit_cost       NUMERIC(12,2) NOT NULL CHECK (unit_cost > 0),
    stock_quantity  INTEGER      NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT price_above_cost CHECK (unit_price >= unit_cost)
);
COMMENT ON TABLE  products IS 'Products and services the company sells, with list price, cost and stock.';
COMMENT ON COLUMN products.product_id     IS 'Primary key of the product.';
COMMENT ON COLUMN products.sku            IS 'Stock keeping unit code, unique per product.';
COMMENT ON COLUMN products.name           IS 'Product name.';
COMMENT ON COLUMN products.category_id    IS 'Category of the product (product_categories.category_id).';
COMMENT ON COLUMN products.unit_price     IS 'Current list price per unit in USD.';
COMMENT ON COLUMN products.unit_cost      IS 'Cost per unit in USD. Profit margin = unit_price - unit_cost.';
COMMENT ON COLUMN products.stock_quantity IS 'Units currently in stock (services have large virtual stock).';
COMMENT ON COLUMN products.is_active      IS 'FALSE if the product is discontinued.';
COMMENT ON COLUMN products.created_at     IS 'When the product was added to the catalog.';

-- ---------------------------------------------------------------------
-- Sales: orders and order lines
-- ---------------------------------------------------------------------
CREATE TABLE orders (
    order_id        SERIAL PRIMARY KEY,
    customer_id     INTEGER      NOT NULL REFERENCES customers(customer_id),
    sales_rep_id    INTEGER      REFERENCES employees(employee_id),
    order_date      TIMESTAMPTZ  NOT NULL,
    status          VARCHAR(20)  NOT NULL CHECK (status IN ('pending','processing','shipped','delivered','cancelled')),
    shipping_country VARCHAR(60) NOT NULL,
    subtotal        NUMERIC(12,2) NOT NULL CHECK (subtotal >= 0),
    discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    tax_amount      NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    total_amount    NUMERIC(12,2) NOT NULL CHECK (total_amount >= 0),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  orders IS 'Sales orders placed by customers. total_amount is the order revenue. Exclude status = ''cancelled'' when computing revenue.';
COMMENT ON COLUMN orders.order_id         IS 'Primary key of the order.';
COMMENT ON COLUMN orders.customer_id      IS 'Customer who placed the order (customers.customer_id).';
COMMENT ON COLUMN orders.sales_rep_id     IS 'Sales rep credited with the order (employees.employee_id).';
COMMENT ON COLUMN orders.order_date       IS 'Date and time the order was placed. Use for monthly / quarterly revenue.';
COMMENT ON COLUMN orders.status           IS 'Order status: pending, processing, shipped, delivered or cancelled.';
COMMENT ON COLUMN orders.shipping_country IS 'Country the order ships to.';
COMMENT ON COLUMN orders.subtotal         IS 'Sum of line totals before discount and tax, in USD.';
COMMENT ON COLUMN orders.discount_amount  IS 'Order-level discount in USD.';
COMMENT ON COLUMN orders.tax_amount       IS 'Tax charged in USD.';
COMMENT ON COLUMN orders.total_amount     IS 'Final order value in USD = subtotal - discount_amount + tax_amount. This is revenue.';
COMMENT ON COLUMN orders.updated_at       IS 'Last time the order (e.g. its status) changed.';

CREATE TABLE order_items (
    order_item_id   SERIAL PRIMARY KEY,
    order_id        INTEGER      NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id      INTEGER      NOT NULL REFERENCES products(product_id),
    quantity        INTEGER      NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
    line_total      NUMERIC(12,2) NOT NULL CHECK (line_total >= 0)
);
COMMENT ON TABLE  order_items IS 'Line items of each order: which products, how many, and at what price. Use for product-level sales and units sold.';
COMMENT ON COLUMN order_items.order_item_id IS 'Primary key of the order line.';
COMMENT ON COLUMN order_items.order_id      IS 'Order this line belongs to (orders.order_id).';
COMMENT ON COLUMN order_items.product_id    IS 'Product sold (products.product_id).';
COMMENT ON COLUMN order_items.quantity      IS 'Number of units sold on this line.';
COMMENT ON COLUMN order_items.unit_price    IS 'Price per unit charged on this order (may differ from current list price).';
COMMENT ON COLUMN order_items.line_total    IS 'quantity * unit_price for this line, in USD.';

-- ---------------------------------------------------------------------
-- Finance: invoices and payments
-- ---------------------------------------------------------------------
CREATE TABLE invoices (
    invoice_id      SERIAL PRIMARY KEY,
    invoice_number  VARCHAR(30)  NOT NULL UNIQUE,
    order_id        INTEGER      NOT NULL UNIQUE REFERENCES orders(order_id),
    customer_id     INTEGER      NOT NULL REFERENCES customers(customer_id),
    issue_date      DATE         NOT NULL,
    due_date        DATE         NOT NULL,
    amount          NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    status          VARCHAR(20)  NOT NULL CHECK (status IN ('draft','sent','paid','overdue','void')),
    paid_date       DATE,
    CONSTRAINT due_after_issue CHECK (due_date >= issue_date),
    CONSTRAINT paid_has_date CHECK ((status = 'paid') = (paid_date IS NOT NULL))
);
COMMENT ON TABLE  invoices IS 'Invoices billed to customers, one per non-cancelled order. Use for accounts receivable, overdue and unpaid amounts.';
COMMENT ON COLUMN invoices.invoice_id     IS 'Primary key of the invoice.';
COMMENT ON COLUMN invoices.invoice_number IS 'Human-readable invoice number, e.g. INV-2025-000123.';
COMMENT ON COLUMN invoices.order_id       IS 'Order being billed (orders.order_id).';
COMMENT ON COLUMN invoices.customer_id    IS 'Customer being billed (customers.customer_id).';
COMMENT ON COLUMN invoices.issue_date     IS 'Date the invoice was issued.';
COMMENT ON COLUMN invoices.due_date       IS 'Payment due date.';
COMMENT ON COLUMN invoices.amount         IS 'Invoice amount in USD (equals the order total_amount).';
COMMENT ON COLUMN invoices.status         IS 'Invoice status: draft, sent, paid, overdue or void. Unpaid = sent or overdue.';
COMMENT ON COLUMN invoices.paid_date      IS 'Date the invoice was fully paid; NULL if not paid.';

CREATE TABLE payments (
    payment_id      SERIAL PRIMARY KEY,
    invoice_id      INTEGER      NOT NULL REFERENCES invoices(invoice_id),
    payment_date    DATE         NOT NULL,
    amount          NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    method          VARCHAR(20)  NOT NULL CHECK (method IN ('bank_transfer','credit_card','ach','check','paypal')),
    reference       VARCHAR(60)  NOT NULL UNIQUE
);
COMMENT ON TABLE  payments IS 'Payments received against invoices. Use for cash collected and payment method analysis.';
COMMENT ON COLUMN payments.payment_id   IS 'Primary key of the payment.';
COMMENT ON COLUMN payments.invoice_id   IS 'Invoice being paid (invoices.invoice_id).';
COMMENT ON COLUMN payments.payment_date IS 'Date the payment was received.';
COMMENT ON COLUMN payments.amount       IS 'Amount received in USD.';
COMMENT ON COLUMN payments.method       IS 'Payment method: bank_transfer, credit_card, ach, check or paypal.';
COMMENT ON COLUMN payments.reference    IS 'Unique transaction reference from the payment provider.';

-- ---------------------------------------------------------------------
-- Audit log (written by the backend, never readable by the AI role)
-- ---------------------------------------------------------------------
CREATE TABLE audit.events (
    event_id        BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    request_id      UUID,
    user_id         VARCHAR(80),
    user_role       VARCHAR(40),
    event_type      VARCHAR(40)  NOT NULL,   -- e.g. nl_query, sql_blocked, api_call, confirmation
    question        TEXT,
    generated_sql   TEXT,
    tool_name       VARCHAR(80),
    tool_args       JSONB,
    outcome         VARCHAR(20)  NOT NULL CHECK (outcome IN ('success','blocked','error','pending_confirmation','cancelled')),
    reason          TEXT,
    row_count       INTEGER,
    latency_ms      INTEGER
);
COMMENT ON TABLE audit.events IS 'Append-only audit trail of every question, generated SQL, tool call and guardrail decision.';

CREATE TABLE audit.pending_actions (
    action_id       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ  NOT NULL,
    user_id         VARCHAR(80)  NOT NULL,
    user_role       VARCHAR(40)  NOT NULL,
    tool_name       VARCHAR(80)  NOT NULL,
    tool_args       JSONB        NOT NULL,
    summary         TEXT         NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','executed','cancelled','failed','expired')),
    result          JSONB,
    decided_at      TIMESTAMPTZ
);
COMMENT ON TABLE audit.pending_actions IS 'Data-changing actions proposed by the AI agent, waiting for (or decided by) explicit user confirmation.';

-- ---------------------------------------------------------------------
-- Indexes for the most common analytical access paths
-- ---------------------------------------------------------------------
CREATE INDEX idx_employees_department   ON employees(department_id);
CREATE INDEX idx_customers_region       ON customers(region);
CREATE INDEX idx_customers_account_mgr  ON customers(account_manager_id);
CREATE INDEX idx_products_category      ON products(category_id);
CREATE INDEX idx_orders_customer        ON orders(customer_id);
CREATE INDEX idx_orders_date            ON orders(order_date);
CREATE INDEX idx_orders_status          ON orders(status);
CREATE INDEX idx_orders_sales_rep       ON orders(sales_rep_id);
CREATE INDEX idx_order_items_order      ON order_items(order_id);
CREATE INDEX idx_order_items_product    ON order_items(product_id);
CREATE INDEX idx_invoices_customer      ON invoices(customer_id);
CREATE INDEX idx_invoices_status_due    ON invoices(status, due_date);
CREATE INDEX idx_payments_invoice       ON payments(invoice_id);
CREATE INDEX idx_audit_events_time      ON audit.events(occurred_at);
