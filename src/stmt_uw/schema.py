"""What every system returns for one applicant file (money in dollars, as people write it)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class StatementRevenue(BaseModel):
    period_end: str = Field(description="Statement closing date, YYYY-MM-DD")
    revenue: float = Field(description="Real revenue in this statement period, dollars")


class Debt(BaseModel):
    provider: str = Field(description="Lender or funder name as it appears on the statement")
    kind: Literal["mca", "term_loan", "equipment_loan"]
    frequency: Literal["daily", "weekly", "monthly"]
    payment: float = Field(description="Amount of each payment, dollars")


class Assessment(BaseModel):
    revenue_by_statement: list[StatementRevenue]
    avg_monthly_revenue: float = Field(description="Mean of the per-statement revenue, dollars")
    avg_daily_balance: float = Field(description="Mean end-of-day balance, every calendar day")
    negative_days: int = Field(description="Calendar days ending with a balance below zero")
    nsf_count: int = Field(description="Returned-item / non-sufficient-funds fee events")
    overdraft_count: int = Field(description="Overdraft fee events")
    existing_debt: list[Debt]
    verdict: Literal["ready", "needs_review"]
    reason: str = Field(description="Why the file needs review, or a one-line confirmation")
