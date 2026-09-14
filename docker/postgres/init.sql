-- =============================================================================
-- BindManager - Initial PostgreSQL Setup
-- Run this ONCE as the postgres superuser before the first `docker compose up`
-- (run as the `postgres` OS user — Postgres uses local peer auth, so
-- `psql -U postgres` run as any other user fails with "Peer authentication
-- failed"):
--
--   sudo -u postgres psql -f docker/postgres/init.sql
-- =============================================================================

-- 1. Role (user)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bindmanager') THEN
    CREATE ROLE bindmanager WITH LOGIN PASSWORD 'bindmanager';
  END IF;
END
$$;

-- 2. Database
-- -----------------------------------------------------------------------------
-- Must be run outside a transaction block; psql handles this automatically.
-- If the database already exists this will print a notice and skip creation.
SELECT 'CREATE DATABASE bindmanager OWNER bindmanager ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'bindmanager')\gexec

-- 3. Privileges
-- -----------------------------------------------------------------------------
-- Connect to the new database to grant schema-level privileges.
\connect bindmanager

GRANT ALL PRIVILEGES ON DATABASE bindmanager TO bindmanager;
GRANT ALL ON SCHEMA public TO bindmanager;

-- 4. Verify
-- -----------------------------------------------------------------------------
SELECT 'Database created:' AS "", datname AS name, pg_catalog.pg_get_userbyid(datdba) AS owner
    FROM pg_database
    WHERE datname = 'bindmanager';

SELECT 'Role created:' AS "", rolname AS name
    FROM pg_roles
    WHERE rolname = 'bindmanager';
