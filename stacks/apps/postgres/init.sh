#!/bin/bash
# Runs once, on an empty data directory: one database and user per app.
set -euo pipefail

create() {
  local db=$1 pass=$2
  psql -v ON_ERROR_STOP=1 --username postgres <<SQL
CREATE USER $db WITH PASSWORD '$pass';
CREATE DATABASE $db OWNER $db;
SQL
}

create nextcloud "$NEXTCLOUD_DB_PASSWORD"
create paperless "$PAPERLESS_DB_PASSWORD"
create n8n "$N8N_DB_PASSWORD"
create huginn "$HUGINN_DB_PASSWORD"
create ai "$AI_DB_PASSWORD"
psql -v ON_ERROR_STOP=1 --username postgres --dbname ai -c "CREATE EXTENSION IF NOT EXISTS vector;"
