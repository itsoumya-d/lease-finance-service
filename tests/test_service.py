from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from leasefinance.api import create_app
from leasefinance.db import Base
from leasefinance.service import ConflictError, LeaseService, NotFoundError, ValidationError

API_KEYS = {"admin-key": "admin", "officer-key": "officer", "viewer-key": "viewer"}
START = date(2026, 1, 15)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    yield db
    db.close()


@pytest.fixture()
def client(session):
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    return TestClient(create_app(session_factory=factory, api_keys=API_KEYS, init_database=False))


def seed(service: LeaseService):
    customer = service.create_customer("ACME", "Acme Ltd")
    asset = service.create_asset("EXC-1", "Excavator", 12000000, "Cat")
    lease = service.create_lease("L-1", customer.id, asset.id, 24, 750, 2000000, START)
    return customer, asset, lease


def pay_all(service, lease):
    for schedule in lease.schedules:
        service.record_payment(lease.id, schedule.payment_cents, schedule.due_date, f"PAY-{schedule.sequence}")


def test_create_customer(session):
    row = LeaseService(session).create_customer("ACME", "Acme")
    assert row.code == "ACME"


def test_create_asset(session):
    row = LeaseService(session).create_asset("EXC-1", "Excavator", 12000000)
    assert row.cost_cents == 12000000


def test_lease_schedule(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    assert len(lease.schedules) == 24
    assert lease.schedules[0].due_date == date(2026, 2, 15)


def test_schedule_ends_at_residual(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    assert lease.schedules[-1].balance_cents == lease.residual_cents


def test_schedule_principal_totals(session):
    service = LeaseService(session)
    _, asset, lease = seed(service)
    principal = sum(s.principal_cents for s in lease.schedules)
    assert principal == asset.cost_cents - lease.residual_cents


def test_payment_allocates_oldest(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    first = lease.schedules[0]
    payment = service.record_payment(lease.id, first.payment_cents, first.due_date, "PAY-1")
    assert payment.status == "allocated"
    assert first.paid_cents == first.payment_cents


def test_partial_payment(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    first = lease.schedules[0]
    payment = service.record_payment(lease.id, 1000, first.due_date, "PAY-1")
    assert payment.status == "partially_allocated"


def test_arrears(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    assert service.arrears(lease.id, lease.schedules[0].due_date) == lease.schedules[0].payment_cents


def test_payment_idempotency(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    first = lease.schedules[0]
    a = service.record_payment(lease.id, first.payment_cents, first.due_date, "PAY-1")
    b = service.record_payment(lease.id, first.payment_cents, first.due_date, "PAY-1")
    assert a.id == b.id


def test_payment_conflict(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    first = lease.schedules[0]
    service.record_payment(lease.id, first.payment_cents, first.due_date, "PAY-1")
    with pytest.raises(ConflictError):
        service.record_payment(lease.id, 1, first.due_date, "PAY-1")


def test_residual_payment(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    row = service.pay_residual(lease.id, lease.residual_cents, date(2028, 1, 15), "RES-1")
    assert row.residual_paid is True


def test_purchase_requires_residual(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    pay_all(service, lease)
    with pytest.raises(ValidationError):
        service.end_of_term(lease.id, "purchase")


def test_purchase_success(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    pay_all(service, lease)
    service.pay_residual(lease.id, lease.residual_cents, date(2028, 1, 15), "RES-1")
    row = service.end_of_term(lease.id, "purchase")
    assert row.status == "ended"
    assert row.end_of_term == "purchase"


def test_return_requires_paid_schedule(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    with pytest.raises(ValidationError):
        service.end_of_term(lease.id, "return")


def test_return_success(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    pay_all(service, lease)
    row = service.end_of_term(lease.id, "return")
    assert row.end_of_term == "return"


def test_summary(session):
    service = LeaseService(session)
    _, _, lease = seed(service)
    first = lease.schedules[0]
    service.record_payment(lease.id, first.payment_cents, first.due_date, "PAY-1")
    summary = service.summary()
    assert summary["active"] == 1
    assert summary["paid_cents"] == first.payment_cents


def test_audit(session):
    service = LeaseService(session)
    seed(service)
    actions = [e["action"] for e in service.audit()]
    assert "customer_created" in actions
    assert "lease_created" in actions


def test_missing_lease(session):
    with pytest.raises(NotFoundError):
        LeaseService(session).arrears(999)


def test_api_auth_and_workflow(client):
    assert client.post("/customers", json={}).status_code == 401
    headers = {"X-API-Key": "officer-key"}
    customer = client.post("/customers", json={"code": "ACME", "name": "Acme"}, headers=headers).json()
    asset = client.post("/assets", json={"asset_code": "EXC-1", "description": "Excavator", "cost_cents": 12000000}, headers=headers).json()
    lease = client.post("/leases", json={
        "lease_number": "L-1", "customer_id": customer["id"], "asset_id": asset["id"],
        "term_months": 12, "annual_rate_bps": 750, "residual_cents": 2000000, "start_date": "2026-01-15",
    }, headers=headers).json()
    assert lease["monthly_payment_cents"] > 0
    summary = client.get("/summary", headers={"X-API-Key": "viewer-key"}).json()
    assert summary["leases"] == 1
