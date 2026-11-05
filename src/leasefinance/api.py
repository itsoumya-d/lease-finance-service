from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from .auth import load_api_keys, require_role
from .db import SessionLocal, init_db
from .service import ConflictError, LeaseService, NotFoundError, ValidationError


class CustomerIn(BaseModel):
    code: str
    name: str
    email: str = ""


class AssetIn(BaseModel):
    asset_code: str
    description: str
    cost_cents: int
    supplier: str = ""


class LeaseIn(BaseModel):
    lease_number: str
    customer_id: int
    asset_id: int
    term_months: int
    annual_rate_bps: int
    residual_cents: int
    start_date: date


class PaymentIn(BaseModel):
    lease_id: int
    amount_cents: int
    paid_at: date
    external_id: str


class ResidualIn(BaseModel):
    amount_cents: int
    paid_at: date
    external_id: str


class EndIn(BaseModel):
    option: str


def create_app(session_factory: sessionmaker = SessionLocal, api_keys: dict[str, str] | None = None, init_database: bool = True) -> FastAPI:
    if init_database:
        init_db()
    keys = api_keys or load_api_keys()
    app = FastAPI(title="Lease finance service", version="1.0.0")

    def db_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    viewer = require_role("viewer", keys)
    officer = require_role("officer", keys)

    def handle(exc: Exception):
        if isinstance(exc, NotFoundError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, ConflictError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/customers")
    def customer(payload: CustomerIn, session: Session = Depends(db_session), actor: str = Depends(officer)) -> dict[str, Any]:
        try:
            row = LeaseService(session, actor).create_customer(payload.code, payload.name, payload.email)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "code": row.code}

    @app.post("/assets")
    def asset(payload: AssetIn, session: Session = Depends(db_session), actor: str = Depends(officer)) -> dict[str, Any]:
        try:
            row = LeaseService(session, actor).create_asset(payload.asset_code, payload.description, payload.cost_cents, payload.supplier)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "asset_code": row.asset_code}

    @app.post("/leases")
    def lease(payload: LeaseIn, session: Session = Depends(db_session), actor: str = Depends(officer)) -> dict[str, Any]:
        try:
            row = LeaseService(session, actor).create_lease(payload.lease_number, payload.customer_id, payload.asset_id, payload.term_months, payload.annual_rate_bps, payload.residual_cents, payload.start_date)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "monthly_payment_cents": row.monthly_payment_cents}

    @app.post("/payments")
    def payment(payload: PaymentIn, session: Session = Depends(db_session), actor: str = Depends(officer)) -> dict[str, Any]:
        try:
            row = LeaseService(session, actor).record_payment(payload.lease_id, payload.amount_cents, payload.paid_at, payload.external_id)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "status": row.status, "allocated_cents": row.allocated_cents}

    @app.post("/leases/{lease_id}/residual")
    def residual(lease_id: int, payload: ResidualIn, session: Session = Depends(db_session), actor: str = Depends(officer)) -> dict[str, Any]:
        try:
            row = LeaseService(session, actor).pay_residual(lease_id, payload.amount_cents, payload.paid_at, payload.external_id)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "residual_paid": row.residual_paid}

    @app.post("/leases/{lease_id}/end")
    def end(lease_id: int, payload: EndIn, session: Session = Depends(db_session), actor: str = Depends(officer)) -> dict[str, Any]:
        try:
            row = LeaseService(session, actor).end_of_term(lease_id, payload.option)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "status": row.status, "end_of_term": row.end_of_term}

    @app.get("/leases/{lease_id}/arrears")
    def arrears(lease_id: int, as_of: date | None = None, session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        try:
            return {"lease_id": lease_id, "arrears_cents": LeaseService(session).arrears(lease_id, as_of)}
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/summary")
    def summary(session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        return LeaseService(session).summary()

    @app.get("/audit")
    def audit(session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        return {"audit": LeaseService(session).audit()}

    return app


app = create_app()
