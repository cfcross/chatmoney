from typing import Annotated, Literal
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Short = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
BodyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12000)
]
Pos = Annotated[int, Field(ge=1)]
Workflow = Literal["sales", "support"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(Model):
    pass


class KBRef(Model):
    kb_id: Short
    snapshot: Pos


class ModelParameters(Model):
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, gt=0, le=1)
    max_tokens: int | None = Field(None, ge=1, le=4000)


class Config(Model):
    model_provider: Literal["mock", "deepseek"] | None = None
    model_connection_id: Short | None = None
    model_name: Short | None = None
    model_params: ModelParameters | None = None
    persona: Short
    tone: Short
    goal: BodyText
    allowed: list[Short]
    forbidden: list[Short]
    company_ref: Short
    product_ref: Short
    knowledge: list[KBRef] = Field(min_length=1, max_length=16)
    # Declared interest only; real read access is granted per reviewed version and source.
    research_tools: list[Literal["company.profile_read"]] = Field(default_factory=list, max_length=1)
    tool_allowlist: list[Short]
    policy_ref: Short
    handoff_on: list[Short]


class TemplateSource(Model):
    id: Short
    version: Pos


class AgentCreate(Model):
    name: Short
    workflow: Workflow
    product_id: Short
    config: dict = Field(default_factory=dict)
    source_template: TemplateSource | None = None


class Changes(Model):
    name: Short | None = None
    config: dict | None = None


class AgentPatch(Model):
    expected_revision: Pos
    changes: Changes


class Publish(Model):
    expected_revision: Pos
    note: Short | None = None


class Reason(Model):
    reason: Short


class KBCreate(Model):
    name: Short
    product_id: Short


class DocumentRef(Model):
    document_id: Short
    revision: Pos


class KBPublish(Model):
    expected_snapshot: Pos | None
    documents: list[DocumentRef] = Field(min_length=1, max_length=100)


class Withdraw(DocumentRef):
    reason: Short


class Context(Model):
    locale: Short | None = None
    source_ref: Short | None = None


class SessionCreate(Model):
    agent_id: Short
    customer_id: Short
    conversation_id: Short
    task_id: Short | None = None
    context: Context = Field(default_factory=Context)


class Inbound(Model):
    external_message_id: Short
    text: BodyText
    occurred_at: AwareDatetime | None = None
    context: Context = Field(default_factory=Context)


class Correction(Reason):
    expected_revision: Pos
    key: Short
    value: str | int | float | bool | None
    source_ref: Short


class Action(Model):
    kind: Literal["opening", "follow_up"]
    source_ref: Short
    due_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_due(self):
        if self.kind == "follow_up" and not self.due_at:
            raise ValueError("follow_up requires due_at")
        return self


class TaskCreate(Model):
    name: Short | None = None
    note: str | None = Field(None, max_length=2000)
    agent_id: Short
    agent_version: Pos
    purpose: Literal["outreach", "support"]
    external_ref: Short | None = None


class Handoff(Reason):
    evidence_refs: list[Short] = Field(default_factory=list, max_length=30)


class Takeover(Model):
    handoff_id: Short
    expected_epoch: Pos


class HumanMessage(Model):
    text: BodyText
    expected_epoch: Pos


class Release(Takeover):
    resolution: Literal["resume", "close"]
    note: BodyText
