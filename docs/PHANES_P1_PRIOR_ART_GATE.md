# PHANES P1 — Prior Art Gate: Experience Provenance & Applicability

## Status

Gate completed before P1 Architecture Freeze v0.2. This document is rationale and evidence, not the normative implementation contract. The normative contract is [PHANES_P1_ARCHITECTURE_FREEZE_v0.2.md](PHANES_P1_ARCHITECTURE_FREEZE_v0.2.md).

Research snapshot: 2026-09-27.

## Gate Conclusion

Mature standards provide useful provenance, observation-time, coordinate-frame, immutable-history, validation, and deterministic-evaluation patterns. No existing framework directly defines Phanes's combination of Experience migration, target-context applicability, replaceable Body, and authority non-persistence. Phanes therefore reuses infrastructure and established concepts while owning its minimal Experience and applicability semantics.

## Sources Investigated

### Provenance and observation

- W3C PROV-DM, W3C Recommendation (2013): entities, activities, agents, attribution, generation, derivation.
- W3C PROV-O, W3C Recommendation (2013): OWL2 representation of PROV.
- OGC SensorThings API Part 1: Sensing 1.1 / OGC Observations and Measurements: phenomenon, result, and valid-time distinctions; Sensor, Thing, and FeatureOfInterest context.
- Python `prov` 3.2.2 (MIT) and RDFLib 7.6.0 (BSD-3-Clause).

### Structural validation

- JSON Schema Draft 2020-12.
- Python `jsonschema` 4.26.0 (MIT, Python >=3.10).

### Robotics context and historical/current separation

- ROS REP-103: units and coordinate conventions.
- ROS REP-105: coordinate frames for mobile platforms and frame authority.
- ROS 2 tf2: time-aware transform trees.
- rosbag2 (Apache-2.0): recorded message history and playback.
- PX4 v1.17 Parameters and ULog (PX4 BSD-3-Clause): current configuration versus historical flight/parameter records.
- KnowRob (BSD-3-Clause) and OpenEASE: robot knowledge, episodes, ontologies, and semantic queries.

### Applicability and multi-valued decisions

- Open Policy Agent/Rego 1.21.0 (Apache-2.0): deterministic input evaluation and undefined values.
- Cedar 4.13.0 (Apache-2.0): typed context, pure evaluation, diagnostics, and separate errors.
- OASIS XACML 3.0: `Permit`, `Deny`, `NotApplicable`, and `Indeterminate` distinctions.

### Immutable history and agent memory

- Event Sourcing pattern as documented by Martin Fowler and Azure Architecture Center.
- CloudEvents 1.0.2 (Apache-2.0).
- Letta 0.16.8 (Apache-2.0): persistent agent memory blocks, messages, and passages.

## Decision Matrix

| Candidate | Relevant problem | Decision | P1 rationale |
|---|---|---|---|
| W3C PROV core concepts | Provenance and derivation | LEARN FROM | Use source/derivation distinctions without adopting its graph model or claiming compatibility. |
| PROV-O / RDF / RDFLib | Graph provenance | REJECT | Ontology, namespaces, graph query, and runtime dependency are unnecessary for two fixed kinds. |
| Python `prov` | Provenance runtime | REJECT | Does not solve applicability and couples P1 to full PROV. |
| OGC O&M / SensorThings | Observation and time semantics | ADAPT | Separate recorded, observed, and valid time in the smaller Phanes contract. |
| JSON Schema Draft 2020-12 | Structural contract | DIRECTLY ADOPT | Language-neutral normative format suited to migration validation. |
| Python `jsonschema` | Runtime validation | REJECT FOR P1 | Two fixed kinds do not justify weakening the stdlib-only offline migration property; reconsider only if schema breadth grows. |
| ROS frame semantics | Spatial context | ADAPT | Use opaque Host-bound frame identity and exact matching. |
| tf2 runtime | Transform resolution | REJECT FOR P1 | Frame transforms and ROS coupling are outside the proof. |
| rosbag2 | Historical message storage | LEARN FROM | Preserve history/current-state separation; do not use playback as an action mechanism. |
| PX4 Parameters and ULog | History versus current configuration | LEARN FROM | A historical parameter is not current authorized configuration. |
| Full Event Sourcing | Immutable history | REJECT | Experience is not the authoritative event stream from which application state is replayed. |
| Append-only correction records | Immutable lifecycle | ADAPT | Corrections create immutable records connected by a minimal relation. |
| CloudEvents | Event envelope | REJECT | Transport events are not Experience applicability records. |
| OPA/Rego runtime | General rule evaluation | REJECT | General DSL and policy distribution exceed the fixed P1 experiment. |
| Cedar runtime | Authorization evaluation | REJECT | Authorization semantics must not define Experience applicability. |
| Cedar evaluation pattern | Pure evaluation and diagnostics | LEARN FROM | Separate deterministic results, reasons, and evaluation errors. |
| XACML decision distinctions | Not-applicable versus indeterminate | LEARN FROM | Supports a non-Boolean result model without importing XACML authorization. |
| KnowRob/OpenEASE | Robot knowledge reuse | LEARN FROM | Confirms context and episode concerns but brings an oversized robotics/ontology stack. |
| Letta/MemGPT-style memory | Persistent agent memory | REJECT | Does not provide embodiment/environment applicability or Phanes authority separation. |
| Phanes applicability semantics | Experience portability | BUILD OURSELVES | These semantics define Phanes's Self/Runtime/Host safety boundary. |

## Decisions Carried into Freeze

- Provenance is a source claim, not authenticated truth.
- `recorded_at`, `observed_at`, and temporal applicability are distinct.
- Spatial operational data requires an explicit Host-bound frame contract.
- Historical Experience, current Body state, and current authorized configuration are separate.
- Immutable correction is useful; full Event Sourcing is not.
- Applicability is three-valued, while malformed data and evaluator faults use a separate failure channel.
- Applicability and authorization are different decisions and remain separate gates.
- JSON Schema 2020-12 is normative for structure; P1 uses a strict stdlib Runtime validator plus semantic validation.

## Reuse Boundary

Phanes reuses stable formats and conceptual distinctions. It does not delegate Identity, Self membership, Body replacement, authority, migration semantics, Experience kinds, intended uses, or applicability truth rules to an external framework.

## References

- https://www.w3.org/TR/prov-dm/
- https://www.w3.org/TR/prov-o/
- https://docs.ogc.org/is/18-088/18-088.html
- https://json-schema.org/draft/2020-12/json-schema-core.html
- https://www.ros.org/reps/rep-0103.html
- https://www.ros.org/reps/rep-0105.html
- https://docs.ros.org/en/jazzy/Concepts/Intermediate/About-Tf2.html
- https://github.com/ros2/rosbag2
- https://docs.px4.io/v1.17/en/advanced_config/parameters.html
- https://docs.px4.io/v1.17/en/dev_log/ulog_file_format.html
- https://www.openpolicyagent.org/docs/policy-language
- https://docs.cedarpolicy.com/auth/authorization.html
- https://docs.oasis-open.org/xacml/3.0/xacml-3.0-core-spec-os-en.html
- https://martinfowler.com/eaaDev/EventSourcing.html
- https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing
- https://cloudevents.io/
- https://knowrob.org/
- https://openease.org/
- https://docs.letta.com/guides/agents/memory
