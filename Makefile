.PHONY: install web dev dev-fresh test schemas schemas-check db-check db-revision compose deploy

install:
	pip install -e libs/sdp_common
	for s in services/*/; do pip install -r $$s/requirements.txt; done

web:
	cd web && npm ci && npm run build

# Keeps your data between runs. Uses Postgres if LEDGER_DB_URL is set (it is, in Codespaces), else SQLite.
dev:
	python3 scripts/dev.py

# Wipes all data first (and the Postgres schema).
dev-fresh:
	python3 scripts/dev.py --fresh

test:
	pytest -q tests/e2e_test.py

schemas:
	python3 scripts/gen_schemas.py

schemas-check:
	python3 scripts/gen_schemas.py --check

compose:
	docker compose up --build

deploy:
	./deploy/cloudrun/deploy.sh

# Check the ledger models and its migrations agree (run before committing a model change).
db-check:
	cd services/ledger && alembic check

# After changing services/ledger/app/db.py:  make db-revision m="add widget table"
db-revision:
	cd services/ledger && alembic revision --autogenerate -m "$(m)"
