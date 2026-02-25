.PHONY: all validate render-copy apply-mods show-plan test install

install:
	pip install -r requirements.txt

validate:
	python tools/render_sync.py validate-config

render-copy:
	python tools/render_sync.py render-copy

apply-mods:
	python tools/render_sync.py apply-mods

all:
	python tools/render_sync.py all

show-plan:
	python tools/render_sync.py show-plan

test:
	pytest tests/ -v
