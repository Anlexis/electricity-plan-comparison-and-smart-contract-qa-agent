# Test Specification — ENE-C2-014 (ElectricityPlanQAAgent)

> Describes the tests this repository actually ships and their current results. Every row
> below corresponds to a test that exists; nothing is listed as planned.

## Test Strategy

- **Unit** — per node and per service module, driving `execute()` directly. Calling a node
  directly matters for the security cases: it proves the template refuses on its own
  rather than relying on a framework gate that may be configured off elsewhere.
- **Proof-of-Boundary** — the framework contract (invoke order, trust denial, import
  isolation, state serialization) and this agent's own boundaries.
- **End-to-end** — the real ASGI application through `/invoke` with bearer authentication.
  Several properties are only decidable here: whether caller data reaches the pipeline,
  whether declared runtime configuration is in force, and whether a refusal is legible to
  the client rather than an opaque internal error.

Corpus: the bundled in-code corpus backs all tests, with per-test fixtures where a case
needs a specific shape.

## Test Inventory

| File | Tests | Covers |
|------|-------|--------|
| `tests/unit/test_caller_context.py` | 70 | the caller-data contract: non-finite matrix per numeric field, bounds, inert alphabets, entry caps, closed field set, no value echoed |
| `tests/unit/test_query_classify.py` | 12 | question validation, intent tagging, segment classification, capacity-driven reclassification |
| `tests/unit/test_plan_retrieve.py` | 7 | retrieval, segment filtering, supplier threading, empty-corpus behaviour |
| `tests/unit/test_incentive_check.py` | 10 | eligibility rules per segment |
| `tests/unit/test_comparison_validate.py` | 9 | synthesis and the output gate's refusal cases |
| `tests/unit/test_output_gate_containment.py` | 12 | gate clearing, truthy notice, inventory guard, detector parity with the framework |
| `tests/unit/test_main_node.py` | 4 | the `main` slot orchestrator, including short-circuit on error |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | 2 | the framework's gates cannot be overridden |
| `tests/proof_of_boundary/test_pb_invoke_endpoint.py` | 25 | the real `/invoke`: auth, caller data doing real work, validation rejection, credential screen, runtime config in force, envelope shape |
| `tests/proof_of_boundary/test_plan_qa_boundary.py` | 10 | this agent's two guardrails through the real slot orchestrators and the compiled graph |
| `tests/proof_of_boundary/test_envelope_containment.py` | 3 | what the caller receives when a run does not succeed |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | 2 | invoke order and trust denial for every node under `src/nodes/` |
| `tests/proof_of_boundary/test_import_isolation.py` | 1 | no direct platform-SDK imports |
| `tests/proof_of_boundary/test_state_safety.py` | 1 | state holds primitives only |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | 2 | skipped — the agent has no interactive step |

## Framework Compliance

| ID | Test | Expected | Result |
|----|------|----------|--------|
| TC-01 | State is a flat TypedDict | no models or nested containers; msgpack-safe | PASS |
| TC-02 | Invalid caller input is refused | the contract refuses, naming the field, before retrieval | PASS |
| TC-03 | No credentials in state | no credential-bearing fields | PASS |
| TC-04 | Trust level is carried from the invocation context | preserved, never reset | PASS |
| TC-05 | Domain audit events are emitted | every boundary node emits one; payloads carry no personal data | PASS |
| TC-06 | The default input gate cannot be overridden | raises at class definition | PASS |
| TC-07 | The default output gate cannot be overridden | raises at class definition | PASS |
| TC-08 | `required_trust_level` is enforced | `VERIFIED_EXTERNAL`; an under-trusted caller is refused before `execute()` | PASS |

## Proof-of-Boundary

| ID | Boundary | Expected | Result |
|----|----------|----------|--------|
| PB-1 | Node → audit sink | trace events fire; no silent failures | PASS |
| PB-2 | State serialization | post-invoke state is primitives only | PASS |
| PB-3 | Retrieval interface | real chunks retrieved through the corpus interface | PASS |
| PB-4 | Import isolation | no direct platform-SDK imports | PASS |
| PB-5 | Checkpoint safety | no credentials or personal data in state | PASS |
| PB-6 | Invoke execution order | trust gate → node_start → input gate → `execute()` → output gate → node_complete, for every node | PASS |
| PB-7 | Interrupt propagation | not applicable — no interactive step | SKIP |

## Business Logic

| ID | Input | Expected | Result |
|----|-------|----------|--------|
| BL-01 | a household question | segment resolves to `residential` | PASS |
| BL-02 | an RE100 procurement question | segment resolves to `re100` | PASS |
| BL-03 | "従量電灯と時間帯別の違いは？" | plan-terms chunks returned with citations | PASS |
| BL-04 | household + 節電ポイント | eligibility populated for that programme | PASS |
| BL-05 | retrieved rules + eligibility | comparison carries a source citation per claim | PASS |
| BL-06 | no retrieval evidence | a stated refusal, never an empty success and never an invented comparison | PASS |
| BL-07 | `contract_kw` below and above the high-voltage threshold | the two answers differ, and the sub-threshold one says the plan is unavailable | PASS |
| BL-08 | `monthly_kwh` supplied | reported as a usage band, with no monetary figure anywhere in the answer | PASS |

## Security

| ID | Input | Expected | Result |
|----|-------|----------|--------|
| SC-01 | injected or unknown supplier selector | refused before retrieval; the value is not echoed back | PASS |
| SC-02 | `contract_kw` = NaN / Infinity / out of range | refused, naming the field | PASS |
| SC-03 | an undeclared context field | refused, not silently ignored | PASS |
| SC-04 | a credential-shaped context value | refused at the adapter with 400, naming the field, never the value | PASS |
| SC-05 | an answer containing an individualized bill estimate | withheld; every carrier of the draft cleared; a truthy notice returned | PASS |
| SC-06 | an answer containing a credential the framework recognises | withheld — the gate's detector is the framework's own, so the two sets cannot drift apart | PASS |
| SC-07 | a post-processing failure after the draft was written | the envelope carries no answer text, no traceback, no source paths | PASS |
| SC-08 | an unauthenticated caller | refused at the entry point; the response does not reveal whether the token was absent, malformed or wrong | PASS |
| SC-09 | an ordinary question containing security-adjacent wording | answered normally — the screens must not refuse real work | PASS |

## Execution Summary

- Verified against the framework wheel that continuous integration installs.
- 168 passed, 2 skipped.
- Skips are the interrupt-propagation cases, which do not apply to this agent.
