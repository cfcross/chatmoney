from typing import Literal
from pydantic import Field, model_validator
from app.contracts import Model, Short


class Source(Model):
    name: Short
    base_url: str = Field(min_length=10, max_length=1500)
    permission_reference: str = Field(min_length=8, max_length=600)
    purpose: Literal["sales_research", "acceptance_only"]
    allow_automation: bool
    allow_evidence_storage: bool
    allow_contact_page: bool = False
    allow_model_processing: bool = False
    valid_days: int = Field(ge=1, le=90)
    retention_days: int = Field(ge=1, le=30)


class GrantRequest(Model):
    source_id: Short


class Decision(Model):
    decision: Literal["approve", "reject", "revoke"]
    reason: str = Field(min_length=2, max_length=240)


class Criterion(Model):
    field: Literal[
        "industry",
        "main_products",
        "business_description",
        "country_or_region",
        "service_market",
        "company_size",
        "demand_signal",
    ]
    include: list[Short] = Field(min_length=1, max_length=8)
    exclude: list[Short] = Field(default_factory=list, max_length=8)
    weight: int = Field(ge=1, le=100)


class Start(Model):
    grant_id: Short
    company_url: str = Field(min_length=10, max_length=1500)
    research_goal: str = Field(min_length=2, max_length=350)
    criteria: list[Criterion] = Field(min_length=1, max_length=7)
    max_pages: int = Field(2, ge=1, le=3)
    max_depth: int = Field(1, ge=0, le=1)
    request_budget: int = Field(5, ge=2, le=6)
    timeout: int = Field(22, ge=4, le=22)
    accept_model_cost: bool = False

    @model_validator(mode="after")
    def unique_fields(self):
        if len({x.field for x in self.criteria}) != len(self.criteria):
            raise ValueError("匹配维度不可重复")
        return self


class Review(Model):
    decision: Literal["approved", "revise"]
    draft: str = Field(min_length=1, max_length=3000)


class FetchArgs(Model):
    company_url: str = Field(min_length=10, max_length=1500)


class ProfileResult(Model):
    pages: list[dict]
    requests: list[dict]
