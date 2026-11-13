.PHONY: install serve test eval gate docs demo shots lint

install:        ## editable install with dev tools
	pip install -e ".[dev]"
serve:          ## the cockpit at http://127.0.0.1:8765
	loopwright serve
test:           ## unit and end-to-end tests
	python -m pytest
eval:           ## the eval suite, written to results/
	loopwright eval --out results
gate:           ## evals plus the release gate (exit 1 on failure)
	loopwright eval --gate --out results
docs:           ## regenerate glossary, lessons, interview prep and events from the TOML
	python scripts/build_docs.py
demo:           ## re-record the static demo in docs/
	python scripts/build_demo.py
shots:          ## retake the README screenshots from the demo (needs playwright and Chrome)
	python -m http.server 8799 -d docs & sleep 1; python scripts/shoot.py; kill %1
lint:
	ruff check loopwright tests scripts
