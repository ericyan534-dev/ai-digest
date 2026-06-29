# ai-digest — developer workflow targets.
# Most targets run from the repo root. Python targets run inside backend/.

COMPOSE := docker compose -f infra/docker-compose.yml
PY      := python3
BACKEND := backend

.PHONY: help up down migrate ingest daily weekly nightly wiki smoke eval recall promptfoo tune api web test lint install fmt

help:
	@echo "ai-digest targets:"
	@echo "  make install   install backend deps (pip) + frontend deps (npm)"
	@echo "  make up         start Postgres+pgvector (docker compose)"
	@echo "  make down       stop Postgres+pgvector"
	@echo "  make migrate    apply db/schema.sql (idempotent)"
	@echo "  make ingest     pull all source adapters -> upsert items"
	@echo "  make daily      generate today's daily digest"
	@echo "  make weekly     generate this week's weekly digest"
	@echo "  make nightly    recompute interest vector (Loop 2) + grade latest daily"
	@echo "  make wiki       export latest digests as linked Obsidian notes"
	@echo "  make smoke      LIVE smoke test (real gemini-3.5-flash; needs GEMINI_API_KEY)"
	@echo "  make eval       LIVE golden-set eval gate (flexibility + editorial floor)"
	@echo "  make recall     LIVE did-I-miss-anything recall eval over latest daily"
	@echo "  make promptfoo  A/B the editorial judge prompt vs the golden set (needs Node)"
	@echo "  make tune       LIVE tier-threshold tuning harness (real ingest -> tier histogram)"
	@echo "  make preview    LIVE in-memory daily (NO DB); add ARGS='--deliver' to email+telegram"
	@echo "  make api        run the FastAPI backend (uvicorn :8000)"
	@echo "  make web        run the Next.js frontend (npm run dev :3000)"
	@echo "  make test       run pytest (unit + integration)"
	@echo "  make lint       ruff + mypy"

install:
	cd $(BACKEND) && $(PY) -m pip install -r requirements.txt
	cd frontend && npm install

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

migrate:
	cd $(BACKEND) && $(PY) -m scripts.migrate

ingest:
	cd $(BACKEND) && $(PY) -m scripts.ingest

daily:
	cd $(BACKEND) && $(PY) -m scripts.run_daily

weekly:
	cd $(BACKEND) && $(PY) -m scripts.run_weekly

nightly:
	cd $(BACKEND) && $(PY) -m scripts.run_nightly

wiki:
	cd $(BACKEND) && $(PY) -m scripts.wiki

smoke:
	cd $(BACKEND) && AIDIGEST_LLM_MOCK=0 $(PY) -m scripts.smoke

eval:
	cd $(BACKEND) && AIDIGEST_LLM_MOCK=0 $(PY) -m scripts.eval_gate

recall:
	cd $(BACKEND) && AIDIGEST_LLM_MOCK=0 $(PY) -m scripts.recall_eval

promptfoo:
	cd $(BACKEND) && AIDIGEST_LLM_MOCK=0 npx promptfoo@latest eval -c aidigest/eval/promptfoo.yaml

tune:
	cd $(BACKEND) && AIDIGEST_LLM_MOCK=0 $(PY) -m scripts.tune_tiers

# Live daily with no database (pre-DB convenience). `make preview ARGS=--deliver`.
preview:
	cd $(BACKEND) && AIDIGEST_LLM_MOCK=0 $(PY) -m scripts.preview_daily $(ARGS)

api:
	cd $(BACKEND) && $(PY) -m uvicorn aidigest.api.main:app --reload --port 8000

web:
	cd frontend && npm install && npm run dev

test:
	cd $(BACKEND) && AIDIGEST_LLM_MOCK=1 $(PY) -m pytest -q --cov=aidigest --cov-report=term-missing

lint:
	cd $(BACKEND) && $(PY) -m ruff check aidigest && $(PY) -m mypy aidigest

fmt:
	cd $(BACKEND) && $(PY) -m ruff format aidigest && $(PY) -m ruff check --fix aidigest
