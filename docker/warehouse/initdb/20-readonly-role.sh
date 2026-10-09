#!/bin/sh
# Create the read-only role `cohortsplit_ro` used by CohortSplit to query the demo
# warehouse. Read-only is enforced by privileges (SELECT only, no CREATE/TEMP, no
# ownership), with default_transaction_read_only=on as an additional layer.
# Runs once, on first container start, as the bootstrap superuser.
set -eu

if [ -z "${WAREHOUSE_RO_PASSWORD:-}" ]; then
  echo "WAREHOUSE_RO_PASSWORD must be set (see .env.example)" >&2
  exit 1
fi

# The password is read inside psql via \getenv so it never appears in argv.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv ro_password WAREHOUSE_RO_PASSWORD

CREATE ROLE cohortsplit_ro
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT
  PASSWORD :'ro_password';

ALTER ROLE cohortsplit_ro SET default_transaction_read_only = on;

-- Only `ecommerce` is reachable; PUBLIC loses CONNECT and TEMP everywhere.
REVOKE ALL ON DATABASE ecommerce FROM PUBLIC;
REVOKE ALL ON DATABASE postgres FROM PUBLIC;
REVOKE ALL ON DATABASE template1 FROM PUBLIC;
GRANT CONNECT ON DATABASE ecommerce TO cohortsplit_ro;

-- Schema: lookup only, no CREATE.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO cohortsplit_ro;

-- Tables: SELECT only, including tables created later by the bootstrap user.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cohortsplit_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO cohortsplit_ro;
SQL
