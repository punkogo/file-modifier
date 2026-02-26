.PHONY: all render-copy apply-mods validate show-plan test install

PYTHON ?= python3

install:
	pip install -r requirements.txt

validate:
	$(PYTHON) tools/render_sync.py validate-config

render-copy:
	$(PYTHON) tools/render_sync.py render-copy

apply-mods:
	$(PYTHON) tools/render_sync.py apply-mods

all:
	$(PYTHON) tools/render_sync.py all

show-plan:
	$(PYTHON) tools/render_sync.py show-plan

test:
	pytest tests/ -v
