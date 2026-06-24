# PivotBoss AI Project Details

## Overview
PivotBoss AI is a Python-based trading assistant focused on CPR-based technical analysis. The project currently combines:
- a FastAPI backend for analytics and dashboard access
- a SQLite persistence layer for market data and paper-trading records
- a paper-trading engine for simulation and risk tracking
- a scheduler and bot layer for automated trading workflows

The system is being evolved into a more maintainable, versioned, and production-ready API.

## Project Goal
Build a trading tool that can:
1. ingest market data
2. compute CPR-based trading signals
3. simulate trades safely in paper mode
4. expose data through a clean API
5. later support controlled and audited live deployment

## Important Guardrails
This project is currently being developed as a research and paper-trading system.
- Do not deploy with real money until the system has:
  - strong risk controls
  - kill switches
  - audit logging
  - broker execution safeguards
  - compliance review
- Default behavior should remain safe and conservative.
- Any future real-money deployment must be treated as a high-risk financial system.

## Current Status
Completed:
- CPR engine and signal generation
- Paper trading simulator and portfolio statistics
- SQLite-backed persistence layer
- FastAPI API server and dashboard serving
- Scheduler integration for daily trading workflow
- Versioned API foundation with typed response models
- Router-based API structure for cleaner expansion
- Health and readiness endpoints
- Basic API key authentication helper
- Pagination and filtering for versioned trade/signal endpoints

In progress:
- Hardening the API for production use
- Improving authentication and authorization flow
- Adding stronger error handling and observability
- Expanding API coverage and documentation
- Preparing safer deployment architecture

## Repository Structure
- backend/: core engine, API, persistence, scheduler, connectors
- frontend/: dashboard UI
- config/: watchlist and environment configuration
- data/: runtime data and SQLite database files
- tests/: automated regression tests
- scripts/: helper utilities for bot execution and login flows

## Core Modules
- backend/api_server.py: FastAPI app entry point and legacy endpoint compatibility
- backend/api_routes.py: versioned API routes for v1 endpoints
- backend/api_schemas.py: Pydantic response and pagination models
- backend/auth.py: API key authentication helper
- backend/db.py: async SQLite persistence layer
- backend/cpr_engine.py: CPR calculations and signal generation
- backend/paper_trader.py: paper trading engine and portfolio statistics
- backend/trading_bot.py: main bot orchestration
- backend/scheduler_service.py: scheduler for recurring tasks
- backend/kotak_connector.py: Kotak Neo integration layer

## Data Flow
1. Market data is loaded from Yahoo or broker connectors.
2. The system computes CPR levels and signal outputs.
3. Signals are stored in SQLite.
4. Paper trades are tracked for PnL and portfolio statistics.
5. The FastAPI layer exposes data for dashboards and downstream tools.

## Current API Surface
### Existing endpoints
- GET /api/status
- GET /api/portfolio
- GET /api/trades
- GET /api/signals
- GET /api/cpr/{symbol}
- GET /api/watchlist
- GET /api/candles/{symbol}
- GET /api/equity
- GET /api/scheduler
- GET /api/ltp/{symbol}
- GET /api/quotes
- GET /api/bot

### Versioned endpoints
- GET /api/v1/health
- GET /api/v1/ready
- GET /api/v1/status
- GET /api/v1/portfolio
- GET /api/v1/watchlist
- GET /api/v1/trades
- GET /api/v1/signals
- GET /api/v1/ltp/{symbol}

## Authentication
Versioned endpoints can be protected with an API key using the X-API-Key header and the PIVOTBOSS_API_KEY environment variable.

## Testing
The project includes API regression tests under the tests folder. Current coverage includes:
- core API endpoints
- versioned health/readiness routes
- pagination and filtering behavior

## Next Milestones
1. Add stronger role-based or token-based protection
2. Create a unified error-handling contract across all endpoints
3. Improve observability and logging
4. Add deployment configuration and container support
5. Add historical backtesting and replay support
6. Introduce safer live-trading controls and audit trails

## Development Notes
- The project is Python-first and uses FastAPI for API exposure.
- SQLite is the current persistence layer for local and early-stage deployment.
- The dashboard is served through the API app for local use.
- The architecture should remain modular so future agents or tools can extend it safely.

## Agent / Tool Handoff Prompt
Use the following prompt when giving this repository to another AI agent, coding tool, or automation system:

Prompt:
You are working on the PivotBoss AI trading project. This is a Python-based CPR trading system with a FastAPI backend, SQLite persistence, paper-trading simulation, and a dashboard. The repository is currently in an API-hardening and modularization phase.

Important context:
- The project is for analysis and paper trading first.
- Do not introduce real-money execution or unsafe live-trading behavior without explicit safeguards and review.
- The current architecture should remain modular, testable, and versioned.
- Preserve existing behavior while improving API quality, security, observability, and maintainability.

Project summary:
- Main backend entry point: backend/api_server.py
- Versioned API routes: backend/api_routes.py
- Shared API models: backend/api_schemas.py
- Auth helper: backend/auth.py
- Persistence layer: backend/db.py
- CPR logic: backend/cpr_engine.py
- Paper trading logic: backend/paper_trader.py
- Bot orchestration: backend/trading_bot.py
- Scheduler: backend/scheduler_service.py
- Connector integration: backend/kotak_connector.py

Current priorities:
- keep the API stable and versioned
- preserve tests
- improve security and error handling
- add documentation and deployment readiness
- avoid unsafe live-trading changes unless explicitly requested

When making changes:
- prefer small, test-backed updates
- update tests when behavior changes
- keep the project understandable for future contributors and tools
- update PROJECT_DETAILS.md whenever major architecture or workflow changes are made
