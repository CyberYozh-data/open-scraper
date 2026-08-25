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
# The image carries the runtime, not the dev tools — `python -m mypy` alone
# fails with "No module named mypy", so install the pinned set first rather than
# pin the version a second time here. Caches are redirected OUT of the mount:
# these containers run as root, and a root-owned .mypy_cache in a developer's
# tree is exactly the stale-cache-fakes-a-green trap this gate exists to close,
# except now it also needs sudo to clear.
typecheck:
	docker compose run --rm --no-deps -v "$(CURDIR)":/w -w /w \
		-e MYPY_CACHE_DIR=/tmp/mypy-cache web-scraper \
		bash -lc 'pip install -q -r requirements-dev.txt && python -m mypy'

# The crawler is not testable through docker compose: its image COPYs only
# src/ and requirements.txt, so tests/, pytest.ini and the tooling are all
# absent from it. These run what CI runs, on the interpreter CI uses.
crawler-test:
	docker run --rm -v "$(CURDIR)":/w -w /w/yozh-crawler \
		-e PYTHONPYCACHEPREFIX=/tmp/pycache python:3.12-slim \
		bash -lc 'pip install -q -r requirements-dev.txt && \
		pytest -q -m "not e2e" -p no:cacheprovider'

crawler-lint:
	docker run --rm -v "$(CURDIR)":/w -w /w/yozh-crawler \
		-e PYTHONPYCACHEPREFIX=/tmp/pycache python:3.12-slim \
		bash -lc 'pip install -q -r requirements-dev.txt && \
		python -m pylint --rcfile=../.pylintrc --fail-under=9.85 --fail-on=E src/'
