# Lease Finance Service

Production-oriented equipment lease finance backend: customers, assets,
amortising payment schedules, idempotent payments, allocation, arrears, residual
settlement, end-of-term options and audit.

## Why this exists

Asset finance is decided by the schedule. A lease needs a defensible monthly
payment, a principal/interest split, a residual that reconciles at maturity,
and clear treatment of partial payments and arrears. This service models that
engine explicitly.

## Features

- Customers and financed assets
- Amortising leases with term, annual rate, monthly payment and residual
- Monthly schedules with interest, principal and closing balance
- Final balance reconciles to the residual
- Idempotent external payment IDs
- Oldest-due allocation, partial payments and unallocated credits
- Arrears as of a date
- Residual settlement
- End-of-term: purchase (residual required), return or renew (all schedules paid)
- Portfolio summary and append-only audit
- API-key RBAC: viewer, officer, admin
- PostgreSQL or SQLite, SQLAlchemy 2, Alembic, Docker, GitHub Actions

## Run locally

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
DATABASE_URL=sqlite:///./lease.db .venv/bin/alembic upgrade head
API_KEYS='{"dev-admin":"admin","dev-officer":"officer","dev-viewer":"viewer"}' \
  .venv/bin/uvicorn leasefinance.api:app --reload
```

## Test

```bash
.venv/bin/pytest
```

## Honest scope

This is a real, tested backend service, not a production lending platform. It
does not do credit approval, KYC, tax, IFRS 16 accounting, GL postings, direct
debit, or collections. The README does not claim production usage.
