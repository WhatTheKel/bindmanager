-- =============================================================================
-- BindManager - Initial MySQL Setup
-- Run this ONCE on your MySQL server before the first `docker compose up`
-- (run as root/sudo, no -p — a fresh install authenticates local root by
-- OS user over the Unix socket, not by password):
--
--   sudo mysql -u root < docker/mysql/init.sql
-- =============================================================================

-- 1. Database
-- -----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS bindmanager
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

-- 2. Application user
-- -----------------------------------------------------------------------------
-- '%' allows connections from any host (covers Docker containers).
-- Change to '192.168.x.%' or '172.%.%.%' to restrict to your subnet.
CREATE USER IF NOT EXISTS 'bindmanager'@'%' IDENTIFIED BY 'bindmanager';
GRANT ALL PRIVILEGES ON bindmanager.* TO 'bindmanager'@'%';
FLUSH PRIVILEGES;

-- 3. Verify
-- -----------------------------------------------------------------------------
SELECT 'Database created:' AS '', schema_name AS name
    FROM information_schema.schemata
    WHERE schema_name = 'bindmanager';

SELECT 'User created:' AS '', user AS name, host
    FROM mysql.user
    WHERE user = 'bindmanager';
