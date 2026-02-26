.PHONY: all render-copy apply-mods validate show-plan test install help

PYTHON ?= python3

install:
	$(PYTHON) -m pip install -r requirements.txt

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

help:
	$(PYTHON) tools/render_sync.py help

test:
	$(PYTHON) -m pytest tests/ -v
