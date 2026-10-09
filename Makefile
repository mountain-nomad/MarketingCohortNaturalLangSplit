# CohortSplit developer commands. Run `make help` for a summary.
#
# Values come from .env (copy .env.example). Secrets are passed to recipes as
# exported environment variables, never on the echoed command line.

SHELL := /bin/bash
UV ?= uv
NPM ?= npm
COMPOSE ?= docker compose

-include .env

APPDB_PORT ?= 55433
APPDB_NAME ?= cohortsplit
APPDB_USER ?= cohortsplit
WAREHOUSE_PORT ?= 55432

# Host-side settings for talking to the compose databases (integration tests, migrate).
HOST_ENV_TARGETS := test-integration migrate
$(HOST_ENV_TARGETS): export COHORTSPLIT_APPDB_HOST := 127.0.0.1
$(HOST_ENV_TARGETS): export COHORTSPLIT_APPDB_PORT := $(APPDB_PORT)
$(HOST_ENV_TARGETS): export COHORTSPLIT_APPDB_NAME := $(APPDB_NAME)
$(HOST_ENV_TARGETS): export COHORTSPLIT_APPDB_USER := $(APPDB_USER)
$(HOST_ENV_TARGETS): export COHORTSPLIT_APPDB_PASSWORD := $(APPDB_PASSWORD)
$(HOST_ENV_TARGETS): export COHORTSPLIT_WAREHOUSE_DSN := postgresql://cohortsplit_ro:$(WAREHOUSE_RO_PASSWORD)@127.0.0.1:$(WAREHOUSE_PORT)/ecommerce
test-integration: export COHORTSPLIT_REQUIRE_INTEGRATION := 1

.PHONY: help install lint typecheck test test-backend test-frontend test-integration up down migrate

help:
	@echo "install           install backend (uv) and frontend (npm) dependencies"
	@echo "lint              ruff + eslint"
	@echo "typecheck         mypy + tsc"
	@echo "test              backend unit tests + frontend tests"
	@echo "test-integration  backend integration tests against running compose DBs (make up)"
	@echo "up / down         start (and wait until healthy) / stop the docker compose stack"
	@echo "migrate           alembic upgrade head against the compose appdb"

install:
	cd backend && $(UV) sync --frozen
	cd frontend && $(NPM) ci --no-audit --no-fund

lint:
	cd backend && $(UV) run ruff check . && $(UV) run ruff format --check .
	cd frontend && $(NPM) run lint

typecheck:
	cd backend && $(UV) run mypy
	cd frontend && $(NPM) run typecheck

test: test-backend test-frontend

test-backend:
	cd backend && $(UV) run pytest -m "not integration"

test-frontend:
	cd frontend && $(NPM) test

test-integration:
	cd backend && $(UV) run pytest -m integration

up:
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

migrate:
	cd backend && $(UV) run alembic upgrade head
