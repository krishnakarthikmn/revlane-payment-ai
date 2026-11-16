#!/usr/bin/env bash
# Fallback for machines without Docker: prepare the development roles and
# databases on a Postgres server that is already running on localhost:5432.
# The password is the development default from .env.example, not a secret.
#
#   revlane       owns the schema and runs migrations. Not a superuser.
#   revlane_app   what tenant-scoped sessions switch to; row-level security applies.
#   revlane_chat  what Ask Revlane's queries run as: it can read the chat views and nothing else.
#   revlane, revlane_test, revlane_e2e   development, the unit tests, and the browser tests.
set -euo pipefail

if ! command -v psql >/dev/null 2>&1 || ! pg_isready -q -h localhost -p 5432; then
  echo "No Docker and no Postgres on localhost:5432." >&2
  echo "Install Docker, or start a local Postgres 16, then run 'make db' again." >&2
  exit 1
fi

run() { psql -h localhost -p 5432 -d postgres -v ON_ERROR_STOP=1 -qAt "$@"; }
has() { [ "$(run -c "$1")" = "1" ]; }

if ! has "SELECT 1 FROM pg_roles WHERE rolname = 'revlane'"; then
  run -c "CREATE ROLE revlane LOGIN PASSWORD 'revlane'"
  echo "Created role revlane."
fi
if ! has "SELECT 1 FROM pg_roles WHERE rolname = 'revlane_app'"; then
  run -c "CREATE ROLE revlane_app NOLOGIN"
  echo "Created role revlane_app."
fi
if ! has "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member WHERE r.rolname = 'revlane_app' AND u.rolname = 'revlane'"; then
  run -c "GRANT revlane_app TO revlane"
  echo "Let revlane switch to revlane_app."
fi
if ! has "SELECT 1 FROM pg_roles WHERE rolname = 'revlane_chat'"; then
  run -c "CREATE ROLE revlane_chat NOLOGIN"
  echo "Created role revlane_chat."
fi
if ! has "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member WHERE r.rolname = 'revlane_chat' AND u.rolname = 'revlane'"; then
  run -c "GRANT revlane_chat TO revlane"
  echo "Let revlane switch to revlane_chat."
fi
for db in revlane revlane_test revlane_e2e; do
  if ! has "SELECT 1 FROM pg_database WHERE datname = '$db'"; then
    run -c "CREATE DATABASE $db OWNER revlane"
    echo "Created database $db."
  fi
done

version="$(run -c "SHOW server_version" | cut -d. -f1)"
if [ "$version" != "16" ]; then
  echo "Note: local Postgres is version $version; the project targets 16 (CI and Docker use 16)." >&2
fi
echo "Database ready on localhost:5432 (local Postgres, not Docker)."
