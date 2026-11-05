from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from .tables import AllocationRow, AssetRow, AuditRow, CustomerRow, LeaseRow, PaymentRow, ScheduleRow


class NotFoundError(Exception):
    pass


class ValidationError(Exception):
    pass


class ConflictError(Exception):
    pass


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def cents(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class LeaseService:
    def __init__(self, session: Session, actor: str = "system") -> None:
        self.session = session
        self.actor = actor

    def _audit(self, action: str, reference: str, detail: str = "") -> None:
        self.session.add(AuditRow(action=action, actor=self.actor, reference=reference, detail=detail))

    def create_customer(self, code: str, name: str, email: str = "") -> CustomerRow:
        if not code.strip() or not name.strip():
            raise ValidationError("customer code and name are required")
        if self.session.scalar(select(CustomerRow).where(CustomerRow.code == code)):
            raise ValidationError("customer already exists")
        row = CustomerRow(code=code, name=name, email=email)
        self.session.add(row)
        self.session.flush()
        self._audit("customer_created", code)
        self.session.commit()
        return row

    def create_asset(self, asset_code: str, description: str, cost_cents: int, supplier: str = "") -> AssetRow:
        if not asset_code.strip() or not description.strip() or cost_cents <= 0:
            raise ValidationError("asset code, description and positive cost are required")
        if self.session.scalar(select(AssetRow).where(AssetRow.asset_code == asset_code)):
            raise ValidationError("asset already exists")
        row = AssetRow(asset_code=asset_code, description=description, cost_cents=cost_cents, supplier=supplier)
        self.session.add(row)
        self.session.flush()
        self._audit("asset_created", asset_code)
        self.session.commit()
        return row

    def _payment(self, principal_cents: int, residual_cents: int, term_months: int, annual_rate_bps: int) -> int:
        monthly_rate = Decimal(annual_rate_bps) / Decimal(10000) / Decimal(12)
        n = Decimal(term_months)
        residual_pv = Decimal(residual_cents) / ((1 + monthly_rate) ** term_months)
        financed = Decimal(principal_cents) - residual_pv
        if monthly_rate == 0:
            payment = financed / n
        else:
            payment = financed * monthly_rate / (1 - (1 + monthly_rate) ** (-term_months))
        return cents(payment)

    def create_lease(self, lease_number: str, customer_id: int, asset_id: int, term_months: int, annual_rate_bps: int, residual_cents: int, start_date: date) -> LeaseRow:
        if not lease_number.strip():
            raise ValidationError("lease number is required")
        if term_months <= 0 or annual_rate_bps < 0 or residual_cents < 0:
            raise ValidationError("term, rate and residual must be valid")
        asset = self.session.get(AssetRow, asset_id)
        if self.session.get(CustomerRow, customer_id) is None or asset is None:
            raise NotFoundError("customer or asset not found")
        if residual_cents >= asset.cost_cents:
            raise ValidationError("residual must be below asset cost")
        if self.session.scalar(select(LeaseRow).where(LeaseRow.lease_number == lease_number)):
            raise ValidationError("lease already exists")
        payment = self._payment(asset.cost_cents, residual_cents, term_months, annual_rate_bps)
        lease = LeaseRow(lease_number=lease_number, customer_id=customer_id, asset_id=asset_id, term_months=term_months, annual_rate_bps=annual_rate_bps, monthly_payment_cents=payment, residual_cents=residual_cents, start_date=start_date)
        self.session.add(lease)
        self.session.flush()
        monthly_rate = Decimal(annual_rate_bps) / Decimal(10000) / Decimal(12)
        balance = asset.cost_cents
        for i in range(1, term_months + 1):
            interest = cents(Decimal(balance) * monthly_rate)
            principal = payment - interest
            if i == term_months:
                principal = balance - residual_cents
                payment_i = principal + interest
            else:
                payment_i = payment
            balance -= principal
            lease.schedules.append(ScheduleRow(
                sequence=i,
                due_date=add_months(start_date, i),
                payment_cents=payment_i,
                interest_cents=interest,
                principal_cents=principal,
                balance_cents=balance,
            ))
        self._audit("lease_created", lease_number, f"payment={payment}")
        self.session.commit()
        return lease

    def record_payment(self, lease_id: int, amount_cents: int, paid_at: date, external_id: str) -> PaymentRow:
        if amount_cents <= 0 or not external_id.strip():
            raise ValidationError("positive amount and external id are required")
        lease = self.session.get(LeaseRow, lease_id)
        if lease is None:
            raise NotFoundError("lease not found")
        existing = self.session.scalar(select(PaymentRow).where(PaymentRow.external_id == external_id))
        if existing is not None:
            if existing.lease_id == lease_id and existing.amount_cents == amount_cents and existing.paid_at == paid_at:
                return existing
            raise ConflictError("external id reused with different payment details")
        payment = PaymentRow(lease_id=lease_id, amount_cents=amount_cents, paid_at=paid_at, external_id=external_id)
        self.session.add(payment)
        self.session.flush()
        remaining = amount_cents
        for schedule in sorted(lease.schedules, key=lambda s: s.sequence):
            if remaining <= 0:
                break
            if schedule.due_date > paid_at:
                continue
            take = min(remaining, schedule.outstanding_cents)
            if take <= 0:
                continue
            allocation = AllocationRow(amount_cents=take)
            allocation.schedule = schedule
            payment.allocations.append(allocation)
            schedule.paid_cents += take
            remaining -= take
        touched = [a.schedule for a in payment.allocations]
        if payment.allocated_cents == amount_cents and touched and all(s.outstanding_cents == 0 for s in touched):
            payment.status = "allocated"
        elif payment.allocated_cents > 0:
            payment.status = "partially_allocated"
        else:
            payment.status = "unallocated"
        self._audit("payment_recorded", external_id, f"{amount_cents}")
        self.session.commit()
        return payment

    def pay_residual(self, lease_id: int, amount_cents: int, paid_at: date, external_id: str) -> LeaseRow:
        lease = self.session.get(LeaseRow, lease_id)
        if lease is None:
            raise NotFoundError("lease not found")
        if amount_cents != lease.residual_cents:
            raise ValidationError("residual payment must equal the residual")
        if lease.residual_paid:
            return lease
        payment = PaymentRow(lease_id=lease_id, amount_cents=amount_cents, paid_at=paid_at, external_id=external_id)
        self.session.add(payment)
        lease.residual_paid = True
        self._audit("residual_paid", lease.lease_number, str(amount_cents))
        self.session.commit()
        return lease

    def arrears(self, lease_id: int, as_of: date | None = None) -> int:
        lease = self.session.get(LeaseRow, lease_id)
        if lease is None:
            raise NotFoundError("lease not found")
        cutoff = as_of or date.today()
        return sum(s.outstanding_cents for s in lease.schedules if s.due_date <= cutoff)

    def end_of_term(self, lease_id: int, option: str) -> LeaseRow:
        lease = self.session.get(LeaseRow, lease_id)
        if lease is None:
            raise NotFoundError("lease not found")
        if option not in {"purchase", "return", "renew"}:
            raise ValidationError("option must be purchase, return or renew")
        if self.arrears(lease_id, date.max) > 0:
            raise ValidationError("lease has outstanding schedule payments")
        if option == "purchase" and not lease.residual_paid:
            raise ValidationError("residual must be paid before purchase")
        lease.status = "ended"
        lease.end_of_term = option
        self._audit("lease_ended", lease.lease_number, option)
        self.session.commit()
        return lease

    def summary(self) -> dict:
        leases = self.session.scalars(select(LeaseRow)).all()
        active = [l for l in leases if l.status == "active"]
        scheduled = sum(s.payment_cents for l in active for s in l.schedules)
        paid = sum(s.paid_cents for l in active for s in l.schedules)
        return {
            "leases": len(leases),
            "active": len(active),
            "scheduled_cents": scheduled,
            "paid_cents": paid,
            "outstanding_cents": scheduled - paid,
            "residual_cents": sum(l.residual_cents for l in active),
        }

    def audit(self) -> list[dict]:
        rows = self.session.scalars(select(AuditRow).order_by(AuditRow.id)).all()
        return [{"at": r.at.isoformat(), "action": r.action, "actor": r.actor, "reference": r.reference, "detail": r.detail} for r in rows]
