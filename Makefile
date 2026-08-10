devel: install install_dev

install:
	make pip_install
	echo "python -m playwright install chromium"

start:
	@echo "Start scriptrun-executor"
	python -m src.main

pip_install:
	pip3 install -r requirements.txt

install_dev:
	pip3 install -r requirements-dev.txt

normalize:
	ruff format ./src

lint:
	python -m pylint src/

test:
	pytest -m "not e2e" -v tests/*

test-all:
	pytest -v tests/*

test-unit:
	pytest -m "not e2e" -v tests/*

test-e2e:
	pytest -m e2e -v tests/*

test-cov:
	pytest -m "not e2e" --cov=src --cov-report=html --cov-report=term-missing -v

# Mounts THIS tree, so it checks the branch you have out rather than whatever
# the bind-mounted checkout happens to be on. Needs mypy in the image:
# `docker compose build web-scraper` after requirements-dev.txt changed.
typecheck:
	docker compose run --rm --no-deps -v "$(CURDIR)":/w -w /w web-scraper python -m mypy
