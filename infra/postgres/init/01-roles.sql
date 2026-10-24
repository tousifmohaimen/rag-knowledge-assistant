-- Runs once, the first time the local Postgres container starts with an empty volume.
-- Local development only: production roles and passwords are created by hand or by
-- the hosting provider, never from a file in the repo.

-- The API connects as this role. It is not the owner of the tables, so Postgres
-- row-level security policies always apply to it.
CREATE ROLE rka_app LOGIN PASSWORD 'rka_app';

-- Separate database for the test suite, so running tests never touches dev data.
CREATE DATABASE rka_test OWNER rka;
