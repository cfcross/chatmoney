"""Concrete public response shapes for Swagger. Success status variants mirror the handlers."""

from fastapi.openapi.utils import get_openapi
from app.contracts import Config as PublishedConfig

FIELDS = {
    "agent_id session_id task_id conversation_id run_id message_id send_id handoff_id document_id kb_id source_ref key reason name workflow product_id status owner summary action": "string",
    "draft_revision version revision agent_version epoch owner_epoch snapshot latest_snapshot seq": "integer",
    "auto_allowed": "boolean",
    "config handoff result error": "object",
    "documents facts items model_calls tool_executions pending unresolved_send_ids": "array",
    "next_cursor delivery note file_id": "string",
    "edit_revision": "integer",
}
TYPES = {field: typ for fields, typ in FIELDS.items() for field in fields.split()}
SHAPES = {
    "create_agent": "agent_id status draft_revision",
    "get_agent": "agent_id status name workflow product_id config version? draft_revision?",
    "patch_agent": "agent_id status draft_revision",
    "publish_agent": "agent_id status version draft_revision",
    "disable_agent": "agent_id status auto_allowed",
    "create_book": "kb_id product_id latest_snapshot~",
    "read_book": "kb_id name edit_revision latest_snapshot~ documents",
    "upload_document": "document_id run_id status",
    "publish_book": "kb_id snapshot status",
    "withdraw_document": "document_id revision status",
    "create_session": "session_id task_id agent_version status owner_epoch",
    "submit_message": "message_id run_id~ status",
    "session_state": "session_id task_id conversation_id status owner owner_epoch handoff_id~ handoff~ unresolved_send_ids",
    "get_messages": "items next_cursor~",
    "get_memory": "revision summary facts pending",
    "correct_memory": "revision key value source_ref",
    "schedule_action": "run_id status",
    "create_task": "task_id agent_version status",
    "get_task": "task_id name~ note~ edit_revision status reason~ agent_version",
    "start_task": "task_id status run_id",
    "resume_task": "task_id status run_id",
    "pause_task": "task_id status auto_allowed",
    "stop_task": "task_id status auto_allowed",
    "request_handoff": "handoff_id session_id status owner_epoch",
    "takeover": "handoff_id status owner owner_epoch",
    "human_message": "message_id send_id run_id status",
    "release_session": "handoff_id status owner owner_epoch",
    "read_run": "run_id status result~ error~ session_id~ task_id~ model_calls tool_executions",
}


TYPES.update(
    {k: "string" for k in ("text", "kind", "at", "execution_id", "tool", "title")}
)
TYPES.update({"corrected": "boolean", "source_refs": "array", "evidence_refs": "array"})


def object_of(fields):
    props = {}
    required = []
    for raw in fields.split():
        k = raw.rstrip("~?")
        typ = TYPES.get(k)
        if k == "value":
            props[k] = {}
        else:
            prop = {"type": typ}
            if typ == "array":
                prop["items"] = (
                    {"type": "string"}
                    if k in {"unresolved_send_ids", "evidence_refs", "source_refs"}
                    else {"type": "object", "additionalProperties": True}
                )
            if raw.endswith("~"):
                prop = {"anyOf": [prop, {"type": "null"}]}
            props[k] = prop
        if not raw.endswith("?"):
            required.append(k)
    return {"type": "object", "properties": props, "required": required}


def configure(app):
    def spec():
        if app.openapi_schema:
            return app.openapi_schema
        doc = get_openapi(title=app.title, version=app.version, routes=app.routes)
        common = doc["components"]["schemas"]
        full = PublishedConfig.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        common.update(full.pop("$defs", {}))
        common["PublishedConfig"] = full
        draft = {**full, "title": "草稿配置", "required": []}
        common["ProfileDraft"] = draft
        for name in ("AgentCreate", "Changes"):
            if name in common:
                common[name]["properties"]["config"] = {
                    "$ref": "#/components/schemas/ProfileDraft"
                }
        common["EngineError"] = {
            "type": "object",
            "required": ["request_id", "error"],
            "properties": {
                "request_id": {"type": "string"},
                "error": {
                    "type": "object",
                    "required": ["code", "message", "retryable"],
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "retryable": {"type": "boolean"},
                    },
                },
            },
        }
        for path, methods in doc["paths"].items():
            for method, op in methods.items():
                name = op["operationId"].split("_api_v1_")[0]
                responses = op["responses"]
                if name in SHAPES:
                    data = object_of(SHAPES[name])
                    props = data["properties"]
                    if "config" in props:
                        props["config"] = {"$ref": "#/components/schemas/ProfileDraft"}
                    if name == "read_run":
                        props["result"] = {
                            "anyOf": [
                                object_of(
                                    "action text? message_id? send_id? handoff_id? reason? delivery? source_refs? task_id? session_id? document_id? revision? evidence_refs?"
                                ),
                                {"type": "null"},
                            ]
                        }
                        props["error"] = {
                            "anyOf": [
                                common["EngineError"]["properties"]["error"],
                                {"type": "null"},
                            ]
                        }
                        props["tool_executions"]["items"] = object_of(
                            "execution_id tool status"
                        )
                    if name == "get_messages":
                        props["items"]["items"] = object_of(
                            "message_id kind text seq delivery at"
                        )
                    if name == "get_memory":
                        props["facts"]["items"] = object_of(
                            "key value source_ref corrected?"
                        )
                    if name == "read_book":
                        props["documents"]["items"] = object_of(
                            "document_id file_id title revision status"
                        )
                    body = {
                        "type": "object",
                        "required": ["request_id", "data"],
                        "properties": {"request_id": {"type": "string"}, "data": data},
                    }
                    success = next(k for k in responses if k.startswith("2"))
                    responses[success]["content"] = {
                        "application/json": {"schema": body}
                    }
                    if name in {"submit_message", "create_session", "request_handoff"}:
                        responses["200"] = {
                            "description": "已存在或仅保存来信",
                            "content": {"application/json": {"schema": body}},
                        }
                for code in (401, 403, 404, 409, 422, 500, 503):
                    responses[str(code)] = {
                        "description": "统一错误",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/EngineError"}
                            }
                        },
                    }
                if path.endswith("stream"):
                    responses["410"] = {
                        "description": "游标过期",
                        "content": responses["409"]["content"],
                    }
                if path.endswith("documents"):
                    for code in ("413", "415"):
                        responses[code] = {
                            "description": "文件大小或格式不受支持",
                            "content": responses["422"]["content"],
                        }
        app.openapi_schema = doc
        return doc

    app.openapi = spec
