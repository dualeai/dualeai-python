# Package
name ?= dualeai
python_version ?= 3.10
version_full ?= $(shell $(MAKE) --silent version-full)
version_small ?= $(shell $(MAKE) --silent version)

version:
	@bash ./cicd/version.sh -g . -c

version-full:
	@bash ./cicd/version.sh -g . -c -m

version-pypi:
	@bash ./cicd/version.sh -g .

install:
	uv venv --python $(python_version) --allow-existing
	$(MAKE) install-deps

install-deps:
	uv sync --extra dev

upgrade:
	uv lock --upgrade --refresh

test:
	$(MAKE) test-static
	$(MAKE) test-func

# `uv lock --check` proves uv.lock still matches pyproject.toml. CI syncs with
# `uv sync --frozen`, which skips that check, so drift lands silently.
test-static:
	uv lock --check
	uv run ruff format --check .
	uv run ruff check .
	uv run ty check .
	uv run -m vulture src tests examples vulture_whitelist.py

test-func:
	$(MAKE) test-unit
	$(MAKE) test-int

test-unit:
	uv run pytest tests/ -v -n auto -m "unit" --ignore=tests/benchmarks

test-int:
	uv run pytest tests/ -v -n 0 -m "integration" --ignore=tests/benchmarks --no-cov

test-bench:
	uv run pytest tests/benchmarks/ --codspeed -v --no-cov -p no:xdist -o "addopts="

lint:
	uv run ruff format .
	uv run ruff check --fix .
	uv run ty check .
	uv run -m vulture src tests examples vulture_whitelist.py

dev:
	@echo "Dev runner not implemented yet."
