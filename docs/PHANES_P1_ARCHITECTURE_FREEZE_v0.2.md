# PHANES P1 — Architecture Freeze v0.2

## 1. Status

**FROZEN. Architecture approved by Astra after delta review.**

This document is the normative architecture contract for P1 implementation. P1 is not yet implemented, engineering-accepted, tagged, or released. The immutable P0 release remains `v0.1.0-p0`.

Where implementation convenience conflicts with this document, implementation must yield. Changing a frozen rule requires a new architecture review; ordinary implementation decisions do not.

The evidence and reuse rationale are maintained separately in [PHANES_P1_PRIOR_ART_GATE.md](PHANES_P1_PRIOR_ART_GATE.md).

## 2. P1 Hypothesis

A Phanes Self can migrate immutable, provenance-bearing, context-dependent typed Experience from Host A to Host B. Using current context supplied independently by Host B and one fixed intended use, the P1 Runtime can classify an Experience as `APPLICABLE`, `INAPPLICABLE`, or `UNKNOWN` before any capability invocation. Migration carries no authority and performs no action.

The hypothesis is falsified if Experience identity, contents, provenance claims, or relations change in migration; if a known context mismatch, unknown result, or evaluation error reaches the supported Experience-driven action path; if import invokes an Adapter; or if Host A authority appears on Host B.

The governing distinctions are:

- **Experience continuity != applicability continuity.**
- **Identity continuity != permission continuity.**
- **Applicability != authority.**

## 3. Scope

P1 supports exactly two Experience kinds:

- `place_observation`
- `body_parameter_observation`

P1 supports exactly two intended uses:

- `historical_query`
- `navigation_target`

### 3.1 MUST HAVE

- A separately persisted `experiences.json` within migratable Self.
- Immutable records with strict structural and semantic validation.
- Explicit Body, environment, frame, and temporal dependency declarations.
- Provenance claims, `derived_from`, and one minimal correction relation, `supersedes`.
- Exact-identifier operational selection.
- Three-valued applicability with an independent failure channel.
- A request-scoped TargetContext snapshot supplied independently of migrated Self.
- A request-scoped `ResolvedNavigationTarget` before Core may consume coordinates.
- Independent applicability and authority gates.
- Package v2 validation and isolated A/B acceptance evidence.

### 3.2 MUST NOT HAVE

- Arbitrary Experience kinds or intended uses.
- `configuration_apply`, controller-parameter application, or automatic configuration migration.
- A general rule DSL, knowledge graph, W3C PROV runtime, OPA, Cedar, or XACML runtime.
- ROS, PX4, tf2, frame discovery, frame aliasing, or coordinate conversion.
- LLM interpretation, confidence scoring, semantic inference, or autonomous adaptation.
- Mutable Experience records, delete, tombstone, revocation, or an active flag.
- Current Host context, Body state, grants, credentials, or cached applicability inside Self.
- Automatic record selection by subject, time, insertion order, or supersession traversal.

## 4. Architecture and Boundaries

The logical Self is:

```text
Self
|-- Identity
`-- Memory
    |-- Legacy Place Memory
    `-- Experience
```

P1 keeps these physical files separate:

```text
identity.json
memory.json
experiences.json
```

Experience logically belongs to Memory, but P1 does not change Memory schema v1 and does not place Experience records in the legacy `places` map.

| Boundary | Owns | Must not own |
|---|---|---|
| Self | Identity, legacy Memory, immutable Experience records and historical provenance/dependency claims | Current TargetContext, grants, credentials, current Body state, applicability cache |
| Runtime | ExperienceStore, validators, ApplicabilityEvaluator, ExperienceUseGateway, Core, CapabilityRegistry, migration | Authority creation, hidden Host discovery, guessed context |
| Host | Adapter, current Body, host config, allowed capabilities, Body runtime state, current environment/frame contract | Migrated authority or modification of imported Experience |

An identifier for a historical Body, environment, or frame may occur in Experience. It is a claim or dependency reference, not current Host state and not authority.

## 5. Dependency Direction and Operational Path

The only supported Experience-driven operational dependency path is:

```text
ExperienceStore
-> ExperienceUseGateway.request(experience_id, navigation_target)
-> TargetContext snapshot
-> ApplicabilityEvaluator
-> ResolvedNavigationTarget
-> Core
-> CapabilityRegistry.list
-> CapabilityRegistry.invoke
-> Adapter.invoke
```

P1 operational Core does not receive `ExperienceStore` or a raw Experience record. It may consume coordinates only through a `ResolvedNavigationTarget` created by the gateway for the current request.

`MemoryStore.lookup_place()` remains legacy informational state and **must not enter the P1 operational action path**. P1 package v2 must expose no production bootstrap that routes legacy place coordinates directly to `move_to`.

Core continues not to discover a Body, read Host configuration, grant authority, or call an Adapter directly.

## 6. Experience Document and Record Contract

`experiences.json` is an exact-key document:

```text
ExperienceDocument
|-- schema_version = 1
|-- agent_id
`-- records[]
```

Each record has exactly these semantic fields:

| Field | Contract |
|---|---|
| `experience_id` | Required canonical UUID; unique and immutable within the document. |
| `agent_id` | Required; must equal document and Identity agent IDs. |
| `kind` | Required fixed enum selecting one payload and use contract. |
| `payload` | Required exact structure for the selected kind. |
| `provenance` | Required source-class and opaque source-reference claim. |
| `dependencies` | Required declarations for all four dependency dimensions. |
| `recorded_at` | Required UTC RFC 3339 timestamp produced by the recording Runtime. |
| `observed_at` | Required field containing UTC RFC 3339 or `null`; `null` means unknown. |
| `derived_from` | Required array of zero or more unique in-document Experience IDs. |
| `supersedes` | Required; one in-document Experience ID or `null`. |

Missing fields are invalid. Missing dependency metadata is never interpreted as independent, unknown, or default. `recorded_at` must not substitute for an unknown `observed_at` or a temporal-validity bound.

### 6.1 `place_observation`

Meaning: a named place was observed at a two-dimensional position in one explicit P1 mock environment and reference frame.

Payload:

```text
place_id: non-empty opaque identifier
position:
  x: finite JSON number
  y: finite JSON number
```

Allowed uses: `historical_query`, `navigation_target`.

Only `navigation_target + APPLICABLE` may yield a `ResolvedNavigationTarget`.

### 6.2 `body_parameter_observation`

Meaning: at `observed_at`, one exact Body instance was observed to have a numeric parameter. It is historical evidence, not a recommendation or current configuration.

Payload:

```text
parameter_name: non-empty fixed-format identifier
value: finite JSON number
unit: non-empty opaque unit identifier
```

Allowed use: `historical_query` only. P1 contains no configuration intended use or path. An applicable historical query cannot write configuration or invoke a capability.

## 7. Provenance Trust Boundary

The fixed source classes are:

- `user_statement`: created only by the explicit user-input recording path.
- `adapter_observation`: created only by Runtime after a successful, authorized, contract-valid Registry invocation.
- `test_fixture`: created only in explicit test mode.

Every provenance value contains `source_class` and a non-empty opaque `source_ref`.

Creation APIs must prevent callers from arbitrarily selecting a stronger source class. Nevertheless, package hashes prove integrity, not origin authenticity. After migration, provenance means only “the source assertion carried by this Self.” P1 does not cryptographically authenticate it, and provenance class does not grant authority or increase applicability.

## 8. Dependency Model

### 8.1 Global vocabulary

Body, environment, and frame modes:

```text
NONE
EXACT(identifier)
UNKNOWN
```

Temporal modes:

```text
NONE
INTERVAL(valid_from, valid_until)
UNKNOWN
```

### 8.2 Kind-specific authority table

This table is the **only rule** that determines whether a kind-specific dependency combination is valid:

| Kind | Body | Environment | Frame | Temporal |
|---|---|---|---|---|
| `place_observation` | `NONE` / `EXACT` | `EXACT` | `EXACT` | `NONE` / `INTERVAL` / `UNKNOWN` |
| `body_parameter_observation` | `EXACT` | `NONE` | `NONE` | `NONE` |

Any other combination is `INVALID_EXPERIENCE` during semantic validation and never enters the evaluator. In particular, a place Body, environment, or frame dependency may not be `UNKNOWN`. Temporal `UNKNOWN` is legal for a place and evaluates to `UNKNOWN` for `navigation_target`.

`NONE` is an explicit positive assertion that applicability for the supported use does not depend on that dimension. It is never inferred from absence.

### 8.3 Temporal semantics

- `recorded_at`: time the record entered Self.
- `observed_at`: time the described phenomenon occurred, or explicitly unknown.
- `valid_from` / `valid_until`: applicability interval only.
- `INTERVAL` requires both bounds and uses `valid_from <= evaluation_time < valid_until`.
- `valid_from >= valid_until` is invalid.
- Missing `valid_until` is invalid, not indefinite validity.
- “No known expiration” is `UNKNOWN`; explicit absence of temporal restriction is `NONE`.

## 9. TargetContext and Host Contracts

For each request, Runtime snapshots:

| Value | Independent provider | Match and missing behavior |
|---|---|---|
| `body_instance_id` | Current Registry Body descriptor, ultimately Host-provided | Exact equality; required but missing -> `UNKNOWN` |
| `environment_id` | Explicit Host/bootstrap configuration | Exact equality; required but missing -> `UNKNOWN` |
| `frame_id` | Explicit Host/bootstrap coordinate contract | Exact equality; required but missing -> `UNKNOWN` |
| `evaluation_time` | Injected UTC Runtime clock | Interval evaluation; required but missing -> `UNKNOWN` |
| `intended_use` | Fixed request handler | Kind-disallowed use -> `INAPPLICABLE` |

Experience cannot supply or override current context. P1 does not include `body_model_id`, current `location_id`, or grants in TargetContext.

### 9.1 Frame contract

P1 uses only the existing two-dimensional P0 mock coordinate and mock unit. It makes no meters, GPS, geodetic, ROS, or real-UAV interpretation.

Equal `frame_id` values are an operator-controlled Host assertion that the current Adapter's `move_to(x,y)` uses the same:

- origin semantics;
- positive X and Y axis orientation;
- mock-unit semantics; and
- overall coordinate interpretation.

If any of these semantics changes, the Host must use a different frame ID. If bootstrap cannot bind a known frame contract to the currently bound Adapter, frame is unavailable and navigation evaluates `UNKNOWN / FRAME_CONTEXT_MISSING`.

P1 performs no frame inference, aliasing, or conversion and does not change Adapter API v1. Equal identifiers are not proof that two real systems are physically identical.

### 9.2 Environment contract

Equal `environment_id` values are an operator-controlled assertion that two Hosts share the same P1 experiment environmental-context identity. P1 has no global registry, discovery mechanism, cloud namespace, cryptographic identity, or proof of real-world sameness.

## 10. Validation Layers

### 10.1 Structural validation

JSON Schema Draft 2020-12 is the normative structural specification. P1 Runtime uses a Python-standard-library strict validator rather than a third-party JSON Schema engine. The normative schema and Runtime validator must accept the same structural input set, demonstrated by shared positive and negative conformance fixtures.

Structural validation covers exact keys, required fields, primitive/container types, enums, payload alternatives, and format shapes.

### 10.2 Semantic validation

Runtime additionally checks:

- Identity/document/record agent consistency;
- the kind-specific dependency authority table;
- unique Experience IDs;
- reference existence and integrity;
- derivation and supersession cycles;
- supersession subject identity and non-branching;
- temporal ordering.

These additional semantic rejections are not structural-schema drift.

### 10.3 Applicability evaluation

Only completely validated documents and records may enter the evaluator. Malformation, unsupported schema, broken references, cycles, or internal evaluation failure use a separate failure channel and must not be converted into `UNKNOWN`.

## 11. Operational Selection

Operational selection is exclusively:

```text
navigation_target(experience_id)
```

P1 does not support operational selection by `place_id`, subject string, latest timestamp, first record, file order, or automatic successor lookup.

- Missing ID -> selection failure `EXPERIENCE_NOT_FOUND`; the evaluator is not called.
- Duplicate Experience ID -> invalid document `DUPLICATE_EXPERIENCE_ID`.
- Explicitly requested superseded record -> `INAPPLICABLE / RECORD_SUPERSEDED`.
- The gateway never redirects to or searches for a successor.
- Independent active records with the same `place_id` may coexist; the caller must explicitly select an Experience ID.

## 12. Applicability Contract

Input:

```text
Validated Experience x TargetContext snapshot x IntendedUse
```

Output is either:

```text
ApplicabilityResult(APPLICABLE | INAPPLICABLE | UNKNOWN, ordered reason codes)
```

or an independent `EvaluatorFailure`.

Rules:

1. A kind-disallowed intended use is `INAPPLICABLE`.
2. `historical_query` exposes a valid record as history; it does not assert current operational validity and may include superseded records.
3. `navigation_target` accepts only an unsuperseded `place_observation`.
4. Any known required mismatch is `INAPPLICABLE`.
5. With no known mismatch, missing required TargetContext or legal temporal `UNKNOWN` is `UNKNOWN`.
6. Only complete known matches are `APPLICABLE`.
7. If a known mismatch and unknown condition coexist, `INAPPLICABLE` wins because the mismatch is decisive.
8. ERROR always fails closed outside the three-value result.

Relevant stable reason codes are:

```text
ALL_REQUIRED_CONSTRAINTS_MATCH
HISTORICAL_QUERY_PERMITTED
INTENDED_USE_NOT_ALLOWED
RECORD_SUPERSEDED
BODY_MISMATCH
ENVIRONMENT_MISMATCH
FRAME_MISMATCH
TEMPORALLY_NOT_YET_VALID
TEMPORALLY_EXPIRED
BODY_CONTEXT_MISSING
ENVIRONMENT_CONTEXT_MISSING
FRAME_CONTEXT_MISSING
EVALUATION_TIME_MISSING
TEMPORAL_DEPENDENCY_UNKNOWN
```

Failure codes are separate, including:

```text
INVALID_EXPERIENCE
UNSUPPORTED_SCHEMA_VERSION
EXPERIENCE_NOT_FOUND
DUPLICATE_EXPERIENCE_ID
BROKEN_EXPERIENCE_REFERENCE
EXPERIENCE_REFERENCE_CYCLE
SUPERSESSION_SUBJECT_MISMATCH
EVALUATOR_INTERNAL_ERROR
```

Reason-code ordering is deterministic: lifecycle, intended use, Body, environment, frame, temporal. Human-readable text is diagnostic only and not a test contract.

## 13. Lifecycle

Records are immutable. A correction creates a new record; no field on the old record is modified.

P1 retains only:

- `derived_from`: epistemic derivation from zero, one, or many unique parents;
- `supersedes`: correction of zero or one prior record.

P1 does not add invalidation, revocation, deletion, tombstones, or a mutable active flag.

All referenced parents must exist in the same complete ExperienceDocument. Self-reference, cycles, missing parents, and package-external parents are invalid. P1 exports the complete document and does not support partial Experience export. A child has its own complete dependencies and never inherits parent applicability.

Supersession must preserve kind and subject identity:

| Kind | Frozen subject identity |
|---|---|
| `place_observation` | `place_id` |
| `body_parameter_observation` | `(body_instance_id, parameter_name, unit)` |

Each record may have at most one direct successor; branching is invalid. A unit change is a distinct parameter observation, not a correction. P1 performs no unit conversion.

## 14. Resolved Value Lifetime

`ResolvedNavigationTarget` is ephemeral and bound to one use request and its TargetContext snapshot. It is not persisted, cached, migrated, reused by another request, or reused after restart. Every navigation request reloads the exact record, snapshots current context, evaluates it, and resolves it anew.

P1 does not require token services, locks, or distributed transactions.

## 15. Applicability and Authority

The gates answer different questions:

- Applicability: is this Experience usable for this context and intended use?
- Authority: does the current Host permit this capability invocation?

| Applicability | Authority | Outcome |
|---|---|---|
| APPLICABLE | authorized | May proceed to capability argument validation; success is not guaranteed. |
| APPLICABLE | unauthorized | Reject; zero Adapter calls. |
| INAPPLICABLE | any | Reject before Registry invocation; zero Adapter calls. |
| UNKNOWN | any | Reject before Registry invocation; zero Adapter calls. |
| ERROR | any | Fail closed; zero action. |

## 16. Migration and Versioning

P1 package contains the complete validated Self and a fixed whitelist of generic Runtime source. It never contains Host configuration, current TargetContext, grants, credentials, Adapter implementation, Body state, cached evaluation, or pending action.

Export validates Identity, Memory, Experience, agent consistency, lifecycle graph, file whitelist, and hashes before committing the package.

Import uses a preinstalled trusted P1 migration tool, validates the package without executing package code, restores Self atomically into a nonexistent target, grants no authority, constructs no TargetContext, evaluates no Experience, registers no capability, and invokes no Adapter.

Frozen versions:

| Contract | Version |
|---|---:|
| Identity schema | 1 |
| Memory schema | 1 |
| Experience schema | 1 |
| Migration package | 2 |
| Core | 0.2 |
| Adapter API | 1 |
| Capability contract | 1 |
| Python | >=3.11 |

P1 rejects P0 package version 1 and performs no implicit upgrade or legacy import. Any future upgrade is a separately designed milestone. A P0 place cannot become a P1 place observation unless an operator explicitly supplies and confirms the required exact environment and frame contracts; otherwise it remains non-operational legacy Memory.

The `v0.1.0-p0` tag and P0 history remain unchanged.

## 17. Frozen Invariants

1. Experience logically belongs to Memory while remaining physically separate in `experiences.json` for P1.
2. Self contains Identity, legacy Memory, and Experience; it does not contain current Host context, authority, or Body state.
3. Current TargetContext never migrates and must be supplied independently for each use request.
4. Experience continuity does not imply applicability continuity.
5. Identity continuity does not imply permission continuity.
6. Applicability does not imply authority.
7. P1 supports only `place_observation` and `body_parameter_observation`.
8. P1 supports only `historical_query` and `navigation_target`; configuration application does not exist.
9. Every dependency dimension is explicit; absence never implies independence or universal applicability.
10. The kind-by-dependency authority table is the sole validity rule; disallowed combinations are validation errors and never reach the evaluator.
11. Operational selection uses exact `experience_id`, never place, time, first/latest, file order, or heuristic selection.
12. A missing selected ID is a selection failure, not `UNKNOWN`.
13. An explicitly selected superseded record is inapplicable and is never redirected to a successor.
14. Supersession preserves `place_id` for places and `(body_instance_id, parameter_name, unit)` for parameters.
15. Records are immutable; correction creates a new record linked by `supersedes`.
16. `derived_from` and `supersedes` references are complete, in-document, acyclic, and strictly validated.
17. Provenance records a source assertion, not cryptographic authenticity, truth, authority, or confidence.
18. P1 coordinates are two-dimensional P0 mock coordinates; no physical unit or real-navigation claim is made.
19. Equal frame IDs are Host assertions of identical origin, axes, mock unit, and Adapter coordinate interpretation.
20. Equal environment IDs are Host assertions of one experiment context, not proof of real-world identity.
21. If Host cannot bind frame semantics to the current Adapter, frame is unavailable and operation fails closed as `UNKNOWN`.
22. Runtime never guesses, aliases, or transforms frames, and Experience cannot provide current context.
23. Only fully structurally and semantically validated Experience enters applicability evaluation.
24. Applicability has exactly three results; malformed input and evaluator failure use an independent ERROR channel.
25. `INAPPLICABLE`, `UNKNOWN`, and ERROR never reach the supported Experience-driven operational path.
26. A `ResolvedNavigationTarget` is bound to one request/context snapshot and is never persisted, cached, migrated, or reused.
27. Legacy `MemoryStore.lookup_place()` cannot feed the P1 operational action path.
28. Every action still flows through CapabilityRegistry; Core does not bind, grant, discover, or call an Adapter directly.
29. Import restores Self only, grants zero authority, and causes zero applicability evaluation, Registry invocation, or Adapter action.
30. P1 freezes Experience schema 1, package 2, Core 0.2, Adapter API 1, Capability contract 1, Identity/Memory schemas 1, and Python >=3.11; package 1 is rejected.
31. `v0.1.0-p0` is permanent and unchanged; P1 claims require new engineering evidence and cannot retroactively enlarge P0 claims.

## 18. Architecture Acceptance Contract

The implementation must provide executable evidence for at least the following cases. Every action case starts with a fresh target Body at `(0,0)` unless stated otherwise.

| Case | Required evidence |
|---|---|
| Portable positive Experience | Body `NONE`, exact environment/frame, valid time, exact ID, authorized -> APPLICABLE, exactly one `move_to(10,20)`, final `(10,20)`. |
| Body mismatch | Exact source Body A evaluated on Body B -> INAPPLICABLE, zero Registry/Adapter invocation. |
| Environment mismatch | Known unequal IDs -> INAPPLICABLE, zero action. |
| Frame mismatch | Known unequal IDs -> INAPPLICABLE, zero action. |
| Frame unavailable | Host cannot declare current Adapter frame -> UNKNOWN, zero action. |
| Temporal expiry | Evaluation at or after `valid_until` -> INAPPLICABLE. |
| Temporal unknown | Legal place temporal `UNKNOWN` -> UNKNOWN. |
| Exact-ID selection | Two independent active records for one place remain separately addressable; only requested ID is evaluated. |
| No subject lookup | Operational request by place/latest/first/file order is unsupported. |
| Superseded explicit ID | Old exact ID -> INAPPLICABLE/RECORD_SUPERSEDED; no successor traversal. |
| Supersession subject mismatch | Place or parameter subject change -> semantic validation failure. |
| Duplicate IDs | Duplicate `experience_id` -> document validation failure. |
| Disallowed dependency mode | Place Body/environment/frame UNKNOWN, or non-authorized parameter modes -> validation failure. |
| Malformed Experience | Missing/extra/wrong-type fields -> structural failure, never empty-store fallback. |
| Broken derivation | Missing parent, self-reference, cycle, or external parent -> semantic failure. |
| Historical parameter | Historical query may succeed; navigation/configuration path is impossible; zero action. |
| APPLICABLE but unauthorized | Gate passes, Registry denies, Adapter call count zero. |
| INAPPLICABLE but authorized | Stops before Registry invocation, Adapter call count zero. |
| UNKNOWN but authorized | Stops before Registry invocation, Adapter call count zero. |
| Provenance preservation | Export/import preserves record IDs, contents, claims, dependencies, and relations exactly. |
| Import zero action | Valid import performs zero evaluator, Registry, and Adapter calls and restores zero authority. |
| Recompute | A later request or restart uses a fresh context snapshot and cannot reuse a prior result or resolved value. |
| Legacy bypass prevention | Architecture/import tests prove legacy place lookup cannot reach P1 operational Core. |
| Frame contract | Same ID/same declared semantics is eligible to match; same string/different declared semantics fails Host conformance evidence. |
| Experience cannot supply context | Record data cannot fill or override current frame/environment. |
| P0 regression | The immutable P0 143-test baseline remains green and P0 boundary claims are not weakened. |

## 19. Failure Criteria

P1 fails architecture acceptance if any invalid record is treated as empty or valid; any disallowed/missing dependency becomes independent; any ambiguous selection is resolved implicitly; any successor is followed automatically; any frame is guessed or converted; any historical parameter reaches a configuration/action path; any non-APPLICABLE or error outcome reaches Registry; import performs action or restores authority; context/evaluation is migrated or reused; or the legacy Memory lookup feeds the P1 action path.

## 20. Claims and Non-Claims

After all acceptance evidence passes, Phanes may claim only that, within a trusted local Runtime, two fixed Experience kinds, two fixed intended uses, complete dependency declarations, and Host-supplied Adapter-consistent context, it can migrate immutable Experience and provenance claims, recompute applicability for the current request, prevent `INAPPLICABLE`, `UNKNOWN`, and ERROR outcomes from entering the supported Experience-driven action path, keep applicability independent from authority, and avoid restoring historical Body state or configuration.

P1 does **not** prove or claim:

- Experience truth or quality;
- dependency completeness or truth;
- physical Body, frame, or environment identity;
- provenance authenticity;
- malicious-package security;
- general transfer learning or arbitrary robot knowledge reuse;
- semantic understanding or LLM reasoning;
- safe PID transfer or configuration migration;
- real UAV safety;
- isolation from malicious same-process code;
- online migration; or
- multi-process consistency.

## 21. Implementation Governance

Tests are part of every feature increment. Kimi may implement scoped milestones but owns no architecture decisions. Sol reviews every milestone for conformance, boundaries, regressions, and evidence. Astra is involved again only if implementation reveals that a frozen rule must change, or for final architecture acceptance.
