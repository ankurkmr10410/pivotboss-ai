# AGENTS.md

## Project overview
PivotBoss AI is a Python-based trading system focused on CPR-based analysis, paper trading, and a FastAPI backend.

## Safety rules
- This project is for analysis and paper trading first.
- Do not introduce real-money execution or unsafe live-trading behavior without explicit safeguards and review.
- Keep the architecture modular, testable, and versioned.
- Preserve existing behavior while improving API quality, security, observability, and maintainability.

## Repository map
- backend/api_server.py: FastAPI app entry point
- backend/api_routes.py: versioned API routes
- backend/api_schemas.py: shared Pydantic models
- backend/auth.py: API key auth helper
- backend/db.py: SQLite persistence layer
- backend/cpr_engine.py: CPR logic and signal generation
- backend/paper_trader.py: paper trading logic
- backend/trading_bot.py: main bot orchestration
- backend/scheduler_service.py: scheduler
- backend/kotak_connector.py: broker integration

## Working expectations
- Prefer small, test-backed changes.
- Update tests when behavior changes.
- Keep the project understandable for future contributors and tools.
- Update PROJECT_DETAILS.md when major architecture or workflow changes are made.
