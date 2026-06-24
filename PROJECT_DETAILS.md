# PivotBoss AI Project Details

## Overview
PivotBoss AI is a Python-based CPR trading system with a FastAPI backend, SQLite persistence, paper-trading simulation, and a lightweight dashboard. The project is being evolved into a more maintainable and production-friendly API.

## Current Status
Completed:
- CPR engine and signal generation
- Paper trading simulator and portfolio stats
- SQLite-backed persistence layer
- FastAPI API server with dashboard serving
- Scheduler integration for daily trading workflow
- Versioned API foundation with typed response models
- Router-based API structure for cleaner expansion
- Basic API key auth helper

In progress:
- Hardening the API for production use
- Adding stronger authentication and authorization
- Expanding versioned endpoints
- Improving error handling and observability

## Project Structure
- backend/: core trading engine, API, persistence, scheduler, connectors
- frontend/: dashboard UI
- config/: watchlist and environment config
- data/: runtime data and SQLite DB
- tests/: automated regression tests
- scripts/: helper scripts for login, backtests, and inspection

## Core Modules
- backend/api_server.py: FastAPI application entry point
- backend/api_routes.py: versioned API routes for v1 endpoints
- backend/api_schemas.py: Pydantic response models
- backend/auth.py: API key authentication helper
- backend/db.py: async SQLite persistence layer
- backend/cpr_engine.py: CPR calculations and signal generation
- backend/paper_trader.py: paper trading engine and portfolio statistics
- backend/trading_bot.py: main bot orchestration
- backend/scheduler_service.py: scheduler for recurring tasks
- backend/kotak_connector.py: Kotak Neo integration

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
- GET /api/v1/status
- GET /api/v1/portfolio
- GET /api/v1/watchlist
- GET /api/v1/ltp/{symbol}

## Authentication
A basic API key auth helper is available in backend/auth.py. It is intended to protect versioned endpoints when configured with PIVOTBOSS_API_KEY.

## Testing
The project currently includes API regression tests in tests/test_api_server.py.

## Next Milestones
1. Apply authentication to more versioned endpoints
2. Add health/readiness endpoints
3. Improve error handling and standard error responses
4. Add pagination and filtering for trade/signal endpoints
5. Expand coverage for more API routes
6. Prepare deployment and Docker support

## Development Notes
- The project is Python-first and uses FastAPI for API exposure.
- SQLite is currently the persistence layer.
- The dashboard is served by the API app for local use.
- The architecture is being evolved toward a cleaner service-oriented structure.
