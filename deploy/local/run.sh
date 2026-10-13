#!/bin/zsh
# Runs an auto-collection module with the environment from .env; the Mac won't sleep while the process is alive.
#   deploy/local/run.sh leadgen.ops.bot
cd "$(dirname "$0")/../.." || exit 1
set -a; . ./.env; set +a
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PG_DUMP="$PWD/.venv/lib/python3.9/site-packages/pgserver/pginstall/bin/pg_dump"
exec /usr/bin/caffeinate -is .venv/bin/python -m "$@"
