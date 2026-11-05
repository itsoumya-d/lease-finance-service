from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CustomerRow(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200), default="")


class AssetRow(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_code: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(240))
    cost_cents: Mapped[int] = mapped_column(Integer)
    supplier: Mapped[str] = mapped_column(String(160), default="")


class LeaseRow(Base):
    __tablename__ = "leases"

    id: Mapped[int] = mapped_column(primary_key=True)
    lease_number: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    term_months: Mapped[int] = mapped_column(Integer)
    annual_rate_bps: Mapped[int] = mapped_column(Integer)
    monthly_payment_cents: Mapped[int] = mapped_column(Integer)
    residual_cents: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")
    end_of_term: Mapped[str] = mapped_column(String(20), default="")
    residual_paid: Mapped[bool] = mapped_column(default=False)
    schedules: Mapped[list["ScheduleRow"]] = relationship(back_populates="lease", cascade="all, delete-orphan")
    payments: Mapped[list["PaymentRow"]] = relationship(back_populates="lease", cascade="all, delete-orphan")


class ScheduleRow(Base):
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("lease_id", "sequence", name="uq_schedule_sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date)
    payment_cents: Mapped[int] = mapped_column(Integer)
    interest_cents: Mapped[int] = mapped_column(Integer)
    principal_cents: Mapped[int] = mapped_column(Integer)
    balance_cents: Mapped[int] = mapped_column(Integer)
    paid_cents: Mapped[int] = mapped_column(Integer, default=0)
    lease: Mapped[LeaseRow] = relationship(back_populates="schedules")

    @property
    def outstanding_cents(self) -> int:
        return max(self.payment_cents - self.paid_cents, 0)


class PaymentRow(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id", ondelete="CASCADE"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    paid_at: Mapped[date] = mapped_column(Date)
    external_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="unallocated")
    lease: Mapped[LeaseRow] = relationship(back_populates="payments")
    allocations: Mapped[list["AllocationRow"]] = relationship(back_populates="payment", cascade="all, delete-orphan")

    @property
    def allocated_cents(self) -> int:
        return sum(a.amount_cents for a in self.allocations)


class AllocationRow(Base):
    __tablename__ = "allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    payment: Mapped[PaymentRow] = relationship(back_populates="allocations")
    schedule: Mapped[ScheduleRow] = relationship()


class AuditRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="")
    reference: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
