#!/usr/bin/env sh
# Back up the Reveal-X PostgreSQL database to a dated, custom-format dump.
#
#   DATABASE_URL=postgres://... ./scripts/backup_postgres.sh [output-file]
#
# Custom format (-Fc) rather than plain SQL because it is compressed and
# pg_restore can restore it into a database that already has tables
# (--clean --if-exists). The restore command is in docs/DEPLOYMENT.md.
#
# Why this exists: a free Render database expires 30 days after it is created
# and is deleted 14 days after that. A dump taken before then is the only way
# the accounts survive a move to a new database.
#
# pg_dump must be at least the server's major version; an older client
# refuses to dump a newer server. Render's external URL works from a laptop;
# the internal one only resolves inside Render.
set -eu

: "${DATABASE_URL:?set DATABASE_URL to the database to back up}"

out="${1:-revealx-$(date -u +%Y%m%d-%H%M%SZ).dump}"

# No --no-owner here: owner and grant information is kept in the dump and
# dropped at restore time instead, where the target's roles are known.
pg_dump -Fc --file="$out" "$DATABASE_URL"

echo "wrote $out ($(wc -c < "$out" | tr -d ' ') bytes)"
