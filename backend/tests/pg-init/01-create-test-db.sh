#!/bin/bash
# Runs on first Postgres container startup (docker-entrypoint-initdb.d).
# Creates the dedicated test database used by backend/tests (skipped by
# pytest when it doesn't exist).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE baleen_test;
EOSQL
