PYTHON ?= python

.PHONY: help install install-dev lint format type-check test secret-scan pre-push

help:
	@echo "Available targets:"
	@echo "  install        Install runtime dependencies"
	@echo "  install-dev    Install runtime + dev dependencies"
	@echo "  lint           Run ruff linter"
	@echo "  format         Auto-format with black"
	@echo "  type-check     Run mypy type checker"
	@echo "  test           Run full test suite"
	@echo "  secret-scan    Run gitleaks secret scanner (requires gitleaks installed)"
	@echo "  pre-push       Full local gate (mirrors CI exactly) — run before every push"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements.txt
	pip install ruff black mypy pytest pytest-cov

lint:
	ruff check .

format:
	black .

type-check:
	mypy . --ignore-missing-imports --exclude 'tests/'

test:
	pytest tests/ -m "not market"

secret-scan:
	gitleaks detect --redact --no-git -s .

pre-push: lint
	black --check .
	mypy . --ignore-missing-imports --exclude 'tests/'
	pytest tests/ -m "not market"
	gitleaks detect --redact --no-git -s .
