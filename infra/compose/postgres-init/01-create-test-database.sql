-- Runs once, on first initialisation of an empty postgres volume (the
-- postgres image executes /docker-entrypoint-initdb.d/* only then).
--
-- Why this exists: the integration suite needs a database of its own, and
-- until M1.7 docs/TESTING.md §16 told every developer to create it by hand
-- with psql. That step was invisible until it bit — `scripts/smoke-test.sh`
-- ends in `docker compose down -v`, which destroys the volume and with it
-- `dcp_test`, so running the smoke test made the next `pytest -m integration`
-- fail with "database does not exist" for reasons that looked nothing like
-- the cause.
--
-- The suite drops and recreates the *schema* per session; it has never
-- created the database. CI does not need this file (its postgres service
-- container is started with POSTGRES_DB=dcp_test), and it is harmless there.
--
-- Not conditional, because this only ever runs against a brand new cluster
-- where nothing can exist yet. Postgres has no CREATE DATABASE IF NOT EXISTS.
CREATE DATABASE dcp_test OWNER dcp;

COMMENT ON DATABASE dcp_test IS
  'Integration-test database. Its schema is dropped and rebuilt per pytest session.';
