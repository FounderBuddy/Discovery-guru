.PHONY: install web dev test schemas schemas-check compose deploy

install:
	pip install -e libs/sdp_common
	for s in services/*/; do pip install -r $$s/requirements.txt; done

web:
	cd web && npm ci && npm run build

dev:
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
