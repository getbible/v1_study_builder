.PHONY: install dev engine test lint format check clean

install:
	python -m pip install -e .

dev:
	python -m pip install -e '.[dev]'

engine:
	study-builder engine install

test:
	python -m pytest

lint:
	python -m ruff check src tests scripts

format:
	python -m ruff format src tests scripts

check: lint test

clean:
	python -m study_builder clean --work-dir .work --dist-dir dist
