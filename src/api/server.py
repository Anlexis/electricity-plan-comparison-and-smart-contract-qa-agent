"""Standalone HTTP entry point for the agent.

Entry points are adapters only — no business logic here. For platform-level routing the
gateway calls ``agent.invoke()`` directly and this module is not involved.
"""

import json
import os
import re
import secrets
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.security.credential_detector import detect_credentials_in_value
from pydantic import BaseModel
from shared.secrets import factory as secrets_factory

from src.graph.graph import ElectricityPlanQAAgent, load_runtime_config

app = FastAPI(title="Agent")

# Runtime parameters (config/config.yaml: max_retry, timeout_s) are passed to the
# constructor so the backbone actually consumes them — an agent built without config
# runs on framework defaults while the file appears to be in charge.
agent = ElectricityPlanQAAgent(config=load_runtime_config())
agent.compile()
# namespace/agent_name match the manifest (config/agent.yaml).
agent.provision_secrets(secrets_factory(namespace="ene", agent_name="ElectricityPlanQAAgent"))

# Coarse transport-level caps. The per-field contract (types, ranges, alphabets) belongs
# to src/services/caller_context.py, which the pre_process node applies.
_MAX_CONTEXT_KEYS = 16
_MAX_CONTEXT_BYTES = 256 * 1024

# A field name is caller data too, so it is only quoted back when it is itself inert.
_SAFE_FIELD_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""
    input_context: dict[str, Any] = {}


def _screen_context_for_credentials(context: dict[str, Any]) -> None:
    """Refuse a request whose context carries a credential-shaped value.

    The framework's initialize node returns ``input_context`` verbatim in its result, and
    the final output gate scans every value of every node result — so a credential-shaped
    string anywhere in the context makes the FIRST node fail with a traceback, before any
    of this template's code runs. The caller then gets an opaque internal error for what
    is really a rejected input.

    The request cannot succeed either way, so it is refused here instead, naming the
    field that caused it. The scan delegates to the framework's own detector, so the set
    refused here is exactly the set that would have detonated downstream — a local
    approximation could only drift from it. Iterating top-level fields is equivalent to
    scanning the whole mapping (the detector's dict case is the union over its values),
    which is what makes naming the field possible without changing what is refused.
    """
    for index, (name, value) in enumerate(context.items()):
        if detect_credentials_in_value(value):
            safe_name = name if _SAFE_FIELD_NAME.match(str(name)) else f"field #{index}"
            # The field, never the value, and never the detector's matched text.
            raise HTTPException(
                status_code=400,
                detail=f"input_context.{safe_name} looks like a credential and was refused.",
            )


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> "dict[str, Any]":
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    # Standalone caller auth: when INVOKE_AUTH_TOKEN is set on the server environment,
    # callers that no upstream middleware vouched for (still ANONYMOUS) must present it
    # as a Bearer token and then run at VERIFIED_EXTERNAL. Trust already established by
    # middleware is never demoted. This adapter is the entry-point auth boundary — a
    # deployment-level caller credential, not an agent secret.
    expected = os.environ.get("INVOKE_AUTH_TOKEN")
    if expected and trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str input (headers
        # decode as latin-1), which would 500 rather than return the generic 401.
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            # Generic body on purpose — do not reveal whether the token was absent,
            # malformed, or simply wrong.
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        trust = TrustLevel.VERIFIED_EXTERNAL

    if len(req.input_context) > _MAX_CONTEXT_KEYS:
        raise HTTPException(status_code=413, detail="input_context has too many keys.")
    if len(json.dumps(req.input_context, default=str)) > _MAX_CONTEXT_BYTES:
        raise HTTPException(status_code=413, detail="input_context is too large.")
    # 400 rather than 422: pydantic owns 422 and answers it with a list of error objects,
    # so reusing that status would make the response shape ambiguous for clients.
    _screen_context_for_credentials(req.input_context)

    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        result: dict[str, Any] = agent.invoke(req.input, ctx=ctx, input_context=req.input_context)
        return result


@app.get("/health")
def health() -> "dict[str, str]":
    return {"status": "ok", "agent": "ElectricityPlanQAAgent"}
