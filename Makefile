.PHONY: install run test seed demo-flow docker-up docker-down package clean

install:
	python -m pip install -r requirements-dev.txt

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

seed:
	python scripts/seed_demo.py

demo-flow:
	python scripts/demo_flow.py

docker-up:
	docker compose up --build

docker-down:
	docker compose down

package:
	bash scripts/package_zip.sh

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache htmlcov .coverage
