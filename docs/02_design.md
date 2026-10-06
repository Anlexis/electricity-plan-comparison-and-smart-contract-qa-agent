# Template Design Specification — ENE-C2-014 (ElectricityPlanQAAgent)

> The design of record for this template. It describes what the repository actually
> ships; where an earlier intent was not built, that is stated rather than implied.

## Position in the Architecture

| Aspect | Value |
|--------|-------|
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |
| Agent class | `ElectricityPlanQAAgent` (`src/graph/graph.py`) |
| Category | Cat 2 — a domain-specific multi-step workflow, not a single generic capability |
| `base_type` | `VectorRAGAgent` — a PATTERN LABEL in `config/agent.yaml`, never an inheritance target |
| Human-in-the-loop | Not enabled; the agent answers in one pass and has no interactive step |

`AutonomousBaseGraph` is deliberately not used: this is a fixed retrieval-augmented
question-answering pipeline, not a self-directed loop.

**Three-layer separation**

- **State** — a flat `TypedDict` extending the framework's `AgentState`. Primitives and
  JSON-serialized strings only. Checkpoints serialize with msgpack, and nested containers
  are not msgpack-safe; they corrupt silently instead of failing, so retrieval artifacts
  travel as JSON strings and are decoded at the node boundary.
- **Node** — each business step is a `FunctionNode` under `src/nodes/` overriding
  `execute(self, state: dict) -> dict` and returning ONLY the fields it changes.
- **Graph** — composition through `register_nodes()`, filling `pre_process` / `main` /
  `post_process`. `initialize` and `finalize` come from the base class.

## Architecture Overview

### Node configuration

Six business steps map onto the framework's fixed three-slot backbone. Each slot holds a
small inline orchestrator that runs its nodes in order and merges their partial results.

| Slot | Node(s) | Responsibility | Reads | Writes |
|------|---------|----------------|-------|--------|
| initialize | — | schema version, session id, caller trust level | user_input | session_id, caller_trust_level |
| pre_process | **QueryNormalize** → **CustomerTypeClassify** | Validate the question and the caller context against the contract; refuse anything outside it; tag a coarse intent; classify the customer segment | user_input, input_context | raw_query, validated_input, query_intent, validated_context, customer_type |
| main | **PlanRulesRetrieve** → **IncentiveProgramCheck** | Retrieve plan-terms and incentive chunks, apply the caller's capacity and plan-code filters, then evaluate incentive eligibility for the segment | validated_input, customer_type, validated_context | retrieved_plan_rules, retrieved_incentives, retrieval_scores, excluded_plan_ids, high_voltage_available, eligibility |
| post_process | **ComparisonGenerate** → **ResponseValidate** | Synthesize the cited aggregate comparison, then gate it | retrieved_*, eligibility, validated_context | answer, blocked, withheld_reason |
| finalize | — | response metadata | node_history, execution_time | response_metadata |

### Data flow

```
START -> initialize -> pre_process(QueryNormalize -> CustomerTypeClassify)
              -> main(PlanRulesRetrieve -> IncentiveProgramCheck) -> {route}
              -> post_process(ComparisonGenerate -> ResponseValidate)
              -> finalize -> END
```

`{route}` is the framework's own status routing: a success proceeds to `post_process`,
and any terminal status goes straight to `finalize`. Because the backbone always visits
every slot, each orchestrator passes through unchanged when the incoming state already
carries an error — a downstream slot must never overwrite an upstream refusal.

When retrieval yields no evidence, `ComparisonGenerate` records that as an explicit
withholding reason rather than emitting an empty answer, and the output gate turns it
into a stated refusal. An empty answer returned as a success reads as "there is nothing
to say" when the truth is "there was nothing to say it from", and those need different
follow-up from the caller.

### Caller-data contract

The agent accepts an optional `input_context` mapping alongside the question. The
accepted set is closed, and it is enforced in exactly one place
(`src/services/caller_context.py`), consumed by `QueryNormalize`:

| Field | Type | Bound | Effect on the answer |
|-------|------|-------|----------------------|
| `retailer` | inert identifier | must be a known supplier | filters plan terms to that supplier |
| `customer_type` | enum | residential / sme / corporate / re100 | selects the segment directly |
| `contract_kw` | finite number | 0 – 100,000 | rules high-voltage plans in or out, and can reclassify the segment |
| `monthly_kwh` | finite number | 0 – 10,000,000 | reported as a usage BAND |
| `plan_codes` | list of inert identifiers | ≤ 16 entries, `[a-z0-9_]{1,32}` | narrows the comparison to the named plans |

Three properties are deliberate:

- **An unknown field is refused, not ignored.** Ignoring is not stripping: an ignored
  field still travels on the request and still reaches the framework's first node, so a
  closed contract has to say no rather than look away.
- **Every number goes through a finite, bounded parser.** NaN and the infinities survive
  `float()` and arrive intact through raw JSON, and every comparison against NaN is
  false — so an unchecked NaN passes a threshold test instead of failing it, which fails
  OPEN on exactly the check the field exists for. Booleans are rejected explicitly
  because `isinstance(True, int)` is true in Python.
- **Refusals name the field and never the value.** An error log is an output channel;
  echoing a rejected value into it hands the caller a way to place arbitrary text there.

Absent context is not an error — the pipeline degrades to its baseline behaviour.

### State definition

Flat `TypedDict` extending `AgentState`. Inherited: `user_input`, `status`, `session_id`,
`node_history`, `error_log`, `input_context`. Template-specific:

| Field | Type | Purpose |
|-------|------|---------|
| raw_query | str | the original question |
| validated_input | str | the validated question that drives retrieval |
| query_intent | str | plan_comparison / incentive_eligibility / enrollment / general |
| validated_context | str \| None | JSON of the caller context AFTER validation |
| customer_type | str | residential / sme / corporate / re100 |
| retrieved_plan_rules | str \| None | JSON list of plan-terms chunks (id, text, source, citation) |
| retrieved_incentives | str \| None | JSON list of incentive-rule chunks |
| retrieval_scores | str \| None | JSON list of per-chunk scores |
| excluded_plan_ids | str \| None | JSON list of plans ruled out by the capacity floor |
| high_voltage_available | bool | whether the declared capacity reaches the high-voltage threshold |
| eligibility | str \| None | JSON incentive-eligibility determination by programme |
| answer | str | the final cited comparison, or the gate's refusal notice |
| blocked | bool | whether the answer was withheld |
| withheld_reason | str \| None | stable machine-readable reason; carries no answer text |
| error | str \| None | short-circuit / degradation flag |

No credentials, secrets, or personal data are held in state, and no individualized
billing amount is ever computed or persisted.

## Corpus Design

| Corpus | Source | Retrieval |
|--------|--------|-----------|
| 電気事業法 | public statute text | lexical overlap over the bundled corpus |
| Registered plan terms | retail supplier plan terms | segment + capacity + plan-code filtering |
| 節電ポイント rules | public programme rules | lexical overlap |
| RE100 / J-Credit eligibility | public scheme rules | lexical overlap |

The shipped corpus is a small in-code set behind the `PlanKnowledgeBase` interface. The
retrieval node depends only on that interface, so an embedding-backed store can replace
it without touching node logic. Scoring is deterministic lexical overlap, which is honest
about what ships: it is a stand-in for a similarity score, not a similarity score.

## Security Design

| Concern | Where it is enforced |
|---------|----------------------|
| Caller trust | `required_trust_level = VERIFIED_EXTERNAL` on every template node. The standalone entry point establishes that level from a bearer token; without one the caller stays anonymous and the pipeline refuses. |
| Caller input | `src/services/caller_context.py` — closed field set, inert identifier alphabets, finite bounded numbers, structural caps. Enforced in the node that owns the contract, so it does not depend on any framework gate being active. |
| Credential-shaped context | Screened at the adapter with the framework's own `detect_credentials_in_value`, refused with a 400 that names the field. |
| Output | `ResponseValidate` — refuses individualized billing or rate projections, and refuses anything the framework's own credential detector recognises. |
| Audit | Every node emits a domain trace event; no personal or account data appears in a trace payload. |

**Why the output gate delegates its credential scan.** A local pattern set narrower than
the framework's is not merely weaker, it is a bypass: a value the framework recognises and
the template misses passes this gate, then makes the framework raise inside post-processing
— and the wrapper discards the node's whole delta, including the clearing this gate
performs. Sharing the framework's detector makes the two sets equal by construction.

**Why refusal clears rather than blanks.** The graph resolves the caller-visible output by
falling through a chain of candidate fields, and a falsy value ACTIVATES that fallback
rather than suppressing it. A gate that sets the answer to an empty string therefore ships
exactly what it meant to withhold. On refusal the gate returns an error status, overwrites
every field carrying answer text or retrieved payload, and puts a TRUTHY notice in the
answer field. `get_output` additionally surfaces answer text only on a successful run or a
deliberate gate refusal — never after an error no gate attested.

### Output invariant

This template renders **no monetary aggregates**, so a numeric-precision grid does not
apply to it. The invariant it does enforce is the one it exists for: **aggregate plan
comparison only, never an individualized billing or rate projection.** Consumption is
therefore reported as a band rather than as the caller's own figure, and no cost is
computed anywhere in the pipeline.

## Framework Utilization

- [x] `InvocationContext` (correlation id, session id, caller trust level)
- [x] `emit_trace_event()` — domain audit events
- [x] `SecurityViolationError`
- [x] The framework's default input gate (personal-data masking, injection screening)
- [x] The framework's default output gate (credential scanning)
- [x] `detect_credentials` / `detect_credentials_in_value` — shared by the adapter screen
      and the output gate

### Composition

- **Pattern**: fixed three-slot backbone; `main` runs retrieval then eligibility in order.
- **Invoke chain**: the agent is entered through `.invoke()`.
- **Error propagation**: nodes return an error status plus an error log entry; the graph
  routes to finalization rather than raising to the caller.

## Import Isolation

- [x] The template does not import the platform SDK directly
- [x] Import targets are the `framework/` and `shared/` packages only

## Design Decision Record

| ID | Decision | Option A | Option B | Chosen | Rationale |
|----|----------|----------|----------|--------|-----------|
| DR-1 | Base class | `AgentBaseGraph` | `AutonomousBaseGraph` | **A** | Fixed retrieval-augmented pipeline; no autonomous loop |
| DR-2 | Pattern label | `VectorRAGAgent` | `ChatAgent` | **A** | The core is retrieval-augmented question answering with cited answers |
| DR-3 | Slot mapping | Six nodes grouped into three slots | `main` as a nested subgraph | **A** | Keeps the first release simple; B is a future refactor for per-node observability |
| DR-4 | Corpus scope | Bundled public corpora plus one supplier's own plan terms | Full multi-supplier plan terms | **A** | Large-scale plan-data collection is a separate effort; the interface makes it a drop-in |
| DR-5 | Caller context | Closed field set, refuse unknown keys | Accept and ignore unknown keys | **A** | An ignored key still reaches the framework's first node, so ignoring is not a boundary |
| DR-6 | Credential detection in the gate | Reuse the framework's detector | Keep a local pattern list | **A** | A narrower local set is a containment bypass, not a smaller safety margin |
