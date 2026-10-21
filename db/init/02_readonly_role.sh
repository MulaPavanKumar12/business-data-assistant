#!/bin/bash
# =====================================================================
# db/init/02_readonly_role.sh
# Creates the least-privilege role the AI assistant uses to run SQL.
#
# Defence in depth: even if the SQL validator (Phase 3) has a bug, this
# role physically cannot change data:
#   * only SELECT on the business tables in "public"
#   * no access at all to the "audit" schema
#   * every transaction is forced read-only
#   * statement_timeout stops runaway queries (e.g. accidental cross joins)
# =====================================================================
set -euo pipefail

: "${READONLY_DB_USER:=bda_readonly}"
: "${READONLY_DB_PASSWORD:?READONLY_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v ro_user="$READONLY_DB_USER" \
     -v ro_pass="$READONLY_DB_PASSWORD" \
     -v db_name="$POSTGRES_DB" <<'EOSQL'
CREATE ROLE :"ro_user" WITH LOGIN PASSWORD :'ro_pass'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT CONNECTION LIMIT 20;

-- Nobody gets implicit rights through PUBLIC.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;

GRANT CONNECT ON DATABASE :"db_name" TO :"ro_user";
GRANT USAGE   ON SCHEMA public TO :"ro_user";
GRANT SELECT  ON ALL TABLES IN SCHEMA public TO :"ro_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :"ro_user";

-- Explicitly no access to the audit trail.
REVOKE ALL ON SCHEMA audit FROM :"ro_user";
REVOKE ALL ON ALL TABLES IN SCHEMA audit FROM :"ro_user";

-- Session-level safety nets enforced by the server, not by our code.
ALTER ROLE :"ro_user" SET default_transaction_read_only = on;
ALTER ROLE :"ro_user" SET statement_timeout = '5s';
ALTER ROLE :"ro_user" SET idle_in_transaction_session_timeout = '10s';
EOSQL

echo "Read-only role '$READONLY_DB_USER' created."
