# Phanes P2 — Host Deployment, One-shot Onboarding, and Replacement Evidence (P2.1–P2.5)

P2 is a Host-side engineering increment on frozen P1 release `v0.2.0-p1`,
base commit `97c6f477fc587febd22ac12cc17c2ed532113434`. Research novelty
claim: **NONE**.

**P2.1 establishes deployment and a read-only declaration only.** P2.2 adds
explicit, one-shot private Host assembly and complete publication. P2.3 adds
isolated end-to-end acceptance evidence outside the development repository.
P2.4 adds the embodiment/session replacement matrix. P2.5 adds failure
injection and boundary hardening. Neither import nor onboarding performs an
Adapter action. P2 is an engineering release candidate, **not released**: no
merge to master, no tag, no GitHub release has been made for P2.

## Implemented public surface

Explicit module: `phanes_host.p2_bootstrap`.

- `HostSessionDescriptor`: a frozen, slotted dataclass with exactly
  `host_session_id: str`, `body: BodyDescriptor`,
  `offered_capabilities: tuple[CapabilitySpec, ...]`, and
  `context_source_kinds: tuple[str, ...]`.
- `OnboardingError`: an ordinary bootstrap exception, not a stable P1 error
  code. Preparation failures preserve the original exception as `__cause__`.
- `onboard_host_session`: explicit one-shot onboarding, described below.

The descriptor directly reuses the existing BodyDescriptor and CapabilitySpec
types. Its supported constructor rejects mutable containers, live values in
the nested declaration fields, and context kinds outside the existing P1
names: `body_instance_id`, `environment_id`, `frame_id`, `evaluation_time`.
Context kinds may describe a subset; existence does not assert a known value.
There is no ontology or new TargetContext field.

These are declaration-shape checks, **not Registry compatibility validation**.
A descriptor is not evidence of successful binding, authority, context
readiness, physical identity/truth, applicability, or execution success.
Its constructor's supplied session string is opaque and is not normalized.
Constructing a declaration alone does not publish a session. Only successful
explicit onboarding generates the published session's ID.
It contains no authority, context values, physical state, Adapter, Registry,
provider callable, module/path/URL, or execution state.

The descriptor is ephemeral and has no persistence/serialization API. It
must not be stored in Self, Experience, or a migration package. Python's
general introspection/serialization facilities are not a security sandbox;
there is no supported Phanes persistence path for this object.

## Independent target-Host deployment

Package-v2 remains unchanged: it does **not** contain `discovery.py`, P2
bootstrap, Host code, or Host state. Do not add files to its Runtime whitelist
or use the P0 legacy CLI to operate a P1 Self.

Prepare this layout from trusted, independently installed release sources:

```text
B/
  copied-package-v2/
    runtime/phanes/                 # original package-v2 Runtime only
    self/                          # original package Self
    manifest.json
  trusted_host/
    phanes_host/
      __init__.py
      mock_uav.py
      p2_bootstrap.py
      _p0_discovery.py              # installation artifact only
  state/                           # Self restored by trusted v2 import tool
```

`_p0_discovery.py` is a byte-for-byte copy of frozen
`phanes/discovery.py`, installed under a fixed Host module name. It is not a
fifth maintained source file in the repository. Installation must compare
the bytes and SHA-256 with the trusted source; the hash checks copy integrity,
not source authenticity. Never obtain this dependency from package metadata,
an arbitrary module path, a URL, or Body A's filesystem.

The private `_load_host_discovery()` helper performs only a fixed ordinary
import of `phanes_host._p0_discovery`. It neither calls `discover_body` nor
creates a Body/session. Missing installation raises ImportError. It never
falls back to repository `phanes.discovery`, scans plugins, executes source
strings, or loads an arbitrary path. It is not a public onboarding API.

The Host copy continues to use `phanes.contracts` from the copied P1 Runtime
and the fixed `phanes_host.mock_uav` factory from the independent Host install.
Do not install another full `phanes` package alongside the Host to hide missing
dependencies. Use only explicit B Runtime/Host deployment roots plus normal
Python standard-library paths; exclude the development repository and A.

## Import is inert

`import phanes_host.p2_bootstrap` defines declarations, functions and an empty
private publication guard.
It does not even load `_p0_discovery` automatically. Import does not construct
an Adapter/Registry, discover a Body, bind, compose Runtime, call providers,
snapshot context, create a session ID, grant authority, or act.

P2.1 preserves `Import != Onboard`. Loading the separate Discovery dependency
only defines its existing fixed factory and functions; it does not call them.
No physical readiness or malicious-Host isolation guarantee is claimed.

## P2.2 explicit onboarding

```python
onboard_host_session(
    state_dir: Path | str,
    body_config: dict,
    *,
    allowed_capabilities: tuple[str, ...] = (),
    get_environment_id: Callable[[], str | None] | None = None,
    get_asserted_frame_id: Callable[[], str | None] | None = None,
    get_evaluation_time: Callable[[], str | None] | None = None,
) -> tuple[HostSessionDescriptor, existing_P1_runtime] | None
```

The providers' default None represents a missing callable, **not** a default
current value. Missing, None or non-callable providers raise OnboardingError
with a TypeError cause. All three callables are required for success. They
receive no Experience and are never invoked during onboarding. At request
time, existing P1 snapshots them anew: a callable returning None is valid
and reaches existing missing-context/UNKNOWN behavior; a provider exception
propagates unchanged, not as OnboardingError or a fabricated None.
Body context still comes from `registry.current_body`, not another provider.

`allowed_capabilities` is **passive data** (Sol P2.2 review B1): it must be
exactly a `tuple` whose members are exactly `str`. The exact-type shape check
runs before Discovery, so executable generators, iterators, lists, named
tuples and custom containers are rejected without being iterated or executed;
a negative test proves a generator whose body would call a provider triggers
zero provider calls, zero generator-body executions, zero Discovery and no
publication. Reading a real tuple of real strings cannot run user code.
Duplicate, unknown, not-offered and contract-mismatch semantics are **not**
duplicated here — they remain existing Registry validation and still reject
invalid tuples. `body_config` is assumed to be trusted, passive local
configuration data; its `deepcopy` must not carry malicious-object side
effects (`__deepcopy__` hooks are out of P2 scope). Python traceback/frame
introspection may observe preparation objects; the claim is only that partial
objects are not published through supported return values, module state, or
exception attributes. `typing.get_type_hints(onboard_host_session)` raises
NameError because the Runtime return type is imported under TYPE_CHECKING;
use `inspect.signature` instead — a documented tooling limitation.

The operation accepts only Self's state directory, existing P0 v1 config and
the separate authority/provider inputs. It accepts no live Adapter/Registry,
custom factory, module/code path, URL or caller-selected session ID. There is
no public Binding/PublishedSession wrapper, reset, rebind or grant API.

### Private assembly and sole publication point

1. Acquire a module-private nonblocking preparation guard; reject an already
   published process before any input inspection or dependency preparation.
2. Check only `callable(provider)` for the three Host providers.
3. Shape-check the passive `allowed_capabilities` input (exact tuple of exact
   strings; live iterables are rejected without iteration) and copy the
   config. Reject nonempty config-sourced authority; do not merge, ignore or
   choose priority.
4. Load only the fixed independently installed Host Discovery and reuse its
   `discover_body(config)` validation/factory unchanged.
5. Read the original trusted Adapter's descriptor and capabilities exactly
   once into a private BodyAdapter-compatible declaration view.
6. Create a function-local CapabilityRegistry and bind that view exactly once
   with the allowed snapshot. Registry remains the sole capability/authority
   contract validator, including unknown/duplicate names and wrong versions.
7. Directly compose the existing P1 Runtime with the bound Registry and the
   untouched provider callables, loading/validating Self using existing APIs.
8. Only after successful composition, generate `str(uuid.uuid4())`, construct
   the read-only declaration, and prepare the ordinary `(descriptor, runtime)`
   return tuple.
9. Commit the private published boolean, then return the complete tuple.

Descriptor and Registry consume the **same** captured Body/capability values;
Registry reads the private view rather than inspecting the original Adapter
again. The view performs no contract validation and delegates each authorized
invoke to the original Adapter exactly once. It is private and non-migrating.

No preparation object is cached in module state or attached to an error.
Any preparation failure leaves publication uncommitted and releases the
guard, allowing a later corrected attempt with a new private Registry.
UUID generation/declaration construction failures also remain retryable.
Exceptions retain normal Python causes/tracebacks: this is not an
introspection sandbox or external hardware-resource rollback guarantee.

One successful publication permanently consumes this module's process
lifecycle. Every subsequent call fails before Discovery, construction,
provider reads, bind, compose or UUID generation; the earlier Runtime remains
usable. Overlapping/reentrant preparation is rejected without consuming the
outer attempt. The guard is local orchestration, not a Registry lock or
multi-process design. Deliberate Python module reload/private-state tampering
is not a supported lifecycle API. New Body/session requires a fresh process.

### No Body and zero authority are different

A valid explicit no-body config (`body=None`, config allowed list empty)
returns **None**, remaining UNBOUND, with no Registry, composition, UUID or
descriptor. Supplying nonempty separate authority with no Body is an error,
not silently ignored. A later corrected Body attempt can still succeed.

A valid Body with separate `allowed_capabilities=()` instead publishes a
complete **zero-authority session**, using `registry.bind(view, ())`.
The descriptor still lists all offered declarations; the Registry authorizes
only the separately supplied allowed subset. Offered is never copied into
allowed. Authority is established only at the single bind, never afterward.

### Ephemeral declaration, no readiness claim

The session ID is generated once per successful assembly, is opaque, is not
derived from Body/agent identity, and is not authentication or authority.
It is not inserted into TargetContext, Experience, Self or package-v2.
Tests compare all three Self files and complete before/after exported package
bytes. A published session does not imply authorized capability, known
context, applicable Experience, physical readiness/truth or execution success.
The existing P1 applicability, Gateway, Core and Registry retain their roles.

Trusted Host constructors, declaration reads and capabilities inspection
must be non-actuating. P2 ensures it calls no `adapter.invoke()` during
preparation; it does not sandbox malicious Python code hiding side effects.

## Minimal usage example (trusted target Host)

The example shows the only supported onboarding path. It assumes the B
deployment layout above: the copied package-v2 Runtime root and the
independent trusted Host tree are on `sys.path`, Self was restored by a
trusted v2 import, and `_p0_discovery.py` is installed under its fixed name.
The Host explicitly chooses providers and authority; nothing is discovered,
auto-authorized, dynamically loaded, or rebound.

```python
from pathlib import Path

from phanes_host.p2_bootstrap import onboard_host_session

state_dir = Path("B/state")  # Self restored by the trusted package-v2 import

body_config = {
    "schema_version": 1,
    "body": {"adapter": "mock_uav", "body_id": "uav-001"},
    "allowed_capabilities": [],  # config authority stays empty by design
}

# Providers are Host-supplied current-context functions. They are checked
# callable during onboarding and called only at request time.
providers = dict(
    get_environment_id=lambda: "env-alpha",
    get_asserted_frame_id=lambda: "frame-alpha",  # Host-asserted coordinate contract
    get_evaluation_time=lambda: None,             # temporal mode NONE needs no time
)

# Zero-authority onboarding: a complete, published, but non-acting session.
descriptor, runtime = onboard_host_session(
    state_dir, body_config, allowed_capabilities=(), **providers)
descriptor.offered_capabilities      # declarations only, never authority
runtime.navigate_experience(...)     # CAPABILITY_UNAVAILABLE, zero Adapter invokes

# Explicit-authority onboarding (fresh process; one session per process).
descriptor, runtime = onboard_host_session(
    state_dir, body_config, allowed_capabilities=("move_to",), **providers)
runtime.navigate_experience(experience_id)  # existing P1 path, at most one invoke
```

The example must not be read to suggest automatic hardware discovery, dynamic
loading, automatic authorization, post-bind grants, or hot rebind: none exist.

## P2.3 isolated deployment evidence

`tests/test_p2_isolated_onboarding_acceptance.py` constructs the real A → B
deployment: A creates a Self with an Experience, exports a real package-v2
(`export_package_v2`), B copies it, restores Self with `import_package_v2`,
and independently installs the Host tree. A is renamed away before any child
runs. Every scenario is a fresh `python -I -S -B` subprocess with a hostile
inherited `PYTHONPATH` pointing at the development repository and the vanished
A path. Child evidence reports PID, effective `sys.path`, module origins,
counted Adapter/Registry/bind/compose/UUID/discovery work, provider call
counts, the session declaration, request outcomes, and Self/package digests.

- Isolation: no sys.path entry resolves under the repository or A; `phanes`
  originates from the copied runtime, `phanes_host`/`p2_bootstrap`/`mock_uav`
  and `_p0_discovery` from the trusted Host tree; `import phanes.discovery`
  fails; A's path is unavailable.
- Import-only child: no session is published and `_p0_discovery` is never
  loaded. Its hooks are installed after the first bootstrap import, so its
  counters do not observe that first import. `TestBootstrapImport` separately
  installs hooks before the first import and proves zero onboarding work,
  session-ID generation, and action calls.
- Zero authority: `allowed_capabilities=()` publishes a complete session whose
  Registry registers nothing; a matching Experience evaluates applicable but
  the request still ends `CAPABILITY_UNAVAILABLE` with zero Adapter invokes.
- Explicit authority: `allowed_capabilities=("move_to",)` exercises the real
  P1 path (Experience → Gateway → TargetContext → Applicability → Authority →
  Registry → captured view → original MockUAV Adapter) exactly once
  (Registry.invoke = Adapter.invoke = 1) and the original adapter's log line
  appears on stderr.
- Missing frame context: frame provider returns None → `UNKNOWN` /
  `FRAME_CONTEXT_MISSING` with zero invocations (fixture matches body and
  environment so frame is the only missing condition).
- Provider exception: a request-time RuntimeError propagates unchanged (not
  OnboardingError) with zero invocations; the snapshot order stops at the
  raising provider (environment 1, frame 1, time 0).
- Immutability: Self file hashes, the complete package digest, parent-side
  byte comparisons, and `validate_package_v2` are unchanged by onboarding and
  requests in every scenario.

## P2.4 embodiment/session replacement evidence

`tests/test_p2_replacement_matrix.py` migrates one Self carrying Experiences
recorded on two bodies (uav-1/frame-alpha and uav-2/frame-beta) into an
independent B deployment. Every case runs fresh `-I -S -B` processes; this is
not hot rebind. `P2.4_MATRIX` evidence rows report case, PID, agent_id,
body_id, session_id, authority, current context, applicability result,
Registry/Adapter invoke counts, and Self hashes. Observed cases:

| Case | Self | Body | Authority | Current context | Result |
|---|---|---|---|---|---|
| A | same | uav-1 (same) | move_to | frame-alpha | new session, invoke exactly once |
| B | same | uav-2 (same type) | move_to | frame-alpha | uav-1 Experience `INAPPLICABLE`/`BODY_MISMATCH` |
| C | same | uav-2 | get_position only | frame-beta | applicable uav-2 Experience → `CAPABILITY_UNAVAILABLE` |
| D | same | uav-1 | move_to | frame-beta / missing | `INAPPLICABLE`/`FRAME_MISMATCH`; `UNKNOWN`/`FRAME_CONTEXT_MISSING` |
| E | same | uav-2 | zero | frame-beta | applicable Experience → `CAPABILITY_UNAVAILABLE` |
| F | same | uav-2 | move_to | frame-beta | invoke exactly once |

Established distinctions (observation-based; no cryptographic claims):

- Same body, new process → new session ID, new Registry, new providers, new
  authority input; no object reuse across PIDs.
- Body replacement keeps agent_id, memory and Experience history (hashes
  equal), while authority, Registry, providers, context and Adapter of the
  old body are absent — the historical record yields
  `BODY_MISMATCH` + `FRAME_MISMATCH` in the new session.
- Authority non-continuity: after an authorized successful navigation, a zero
  authority session with the same Experience still cannot act.
- Context non-continuity: B's frame provider alone decides current context; A's
  frame is never reused because the Experience came from A.
- Session-ID non-semantics: identical inputs in two processes produce
  identical applicability outcomes with different session IDs;
  `host_session_id` never appears in TargetContext, dependency matching, or
  applicability reasons.

## P2.5 failure-injection and boundary evidence

`tests/test_p2_onboarding_hardening.py` (in-process fresh module namespaces
plus subprocess attacks) establishes:

- Input failures: malformed body_config types/fields/schema versions/body
  declarations, unknown or hostile adapter identifiers, malformed
  `allowed_capabilities` content, missing/non-callable providers, and
  non-empty embedded config authority are all rejected with preserved causes
  and leave publication retryable. Adapter construction may already have
  happened when Registry rejects authority content, but nothing is ever
  invoked.
- Passive authority input (Sol P2.2 review B1): executable generators are
  rejected with zero provider calls, zero generator-body executions, zero
  Discovery and no publication; custom iterables are rejected with zero
  `__iter__` calls; a passive tuple succeeds normally; invalid tuple contents
  (duplicate/unknown/not-offered) remain Registry rejections, not duplicated
  P2 rules. Per-provider request semantics are pinned for environment, frame
  and time: each provider returning None yields the existing P1 outcome
  (context-missing UNKNOWN codes; time unused for NONE temporal mode;
  EVALUATION_TIME_MISSING for INTERVAL), and each provider raising propagates
  the original exception with zero invocations while the session stays usable.
  A canonical-module subprocess proves a real failed attempt (corrupt Self)
  recovers via a corrected retry and leaves exactly the three Self files.
- Unstable declarations: a second descriptor/capabilities read would return
  different values, yet exactly one read per onboarding occurs; the
  descriptor and Registry see the same snapshot; later instability is not
  observed.
- Declaration exceptions: descriptor/capabilities failures yield
  OnboardingError with the original cause, no bind/compose/publication, and a
  clean retry.
- Registry remains the sole validator: unknown/duplicate/wrong-version
  capability declarations and wrong adapter API versions fail as
  `RegistryError` inside bind; the P2 descriptor deliberately does not
  duplicate these rules (it accepts shapes the Registry rejects).
- Compose failures: missing or corrupt Self files and an
  identity/history agent mismatch fail before UUID and publication and
  remain retryable after repair.
- UUID/descriptor construction failures: a failing or garbage-producing
  uuid4 (empty, whitespace, or an object whose `__str__` raises) publishes
  nothing and remains retryable; descriptor validation is not weakened.
- Second onboarding: after publication every subsequent call — including
  no-body configs — fails before any Host work (all measured deltas zero)
  and the earlier Runtime stays usable.
- Reentrancy/concurrency: overlapping preparation is rejected without
  consuming the outer attempt; a deterministic pause proves the guard; an
  8-thread race yields exactly one published session, one UUID, one bind,
  one construction, and seven rejected callers.
- No-body edge: repeated valid `body=None` returns None, performs zero work,
  and never consumes the publication slot; after real publication the same
  input follows the published-session rule.
- Dynamic loading attacks: adapter identifiers resembling paths, URLs,
  modules or shell fragments cannot trigger loading (fixed factory table
  only); unusual body_id values remain opaque data that reach the Body
  without loading or writing anything.
- Repository fallback attacks: with the development repository genuinely
  first or last on `sys.path` and the fixed dependency absent, onboarding
  still fails closed; the importable `phanes.discovery` fallback target is
  never called (zero calls on its mocked `discover_body`).
- Persistence leak search: after session creation, Self and exported package
  bytes contain no session ID, no `host_session_id` marker, no Host context
  sentinels, no deployment paths, no `p2_bootstrap` reference, and no
  authority names; the state directory gains no extra files.
- Public API audit: the supported P2 surface is exactly
  `HostSessionDescriptor`, `OnboardingError`, `onboard_host_session`;
  star-import exports exactly those; private helpers stay private; the Host
  package does not re-export the API; no reset/grant/rebind/binding/
  Registry-accessor names exist.

## Frozen invariants and where the tests prove them

| P2 invariant | Evidence (tests) |
|---|---|
| Import != Onboard | TestBootstrapImport; import_only isolated child |
| Discovery != Authority | zero-authority matrix cases; test_zero_authority_* |
| Host declaration != Physical truth | descriptor shape/ephemerality tests; P2.5.13 leak search |
| Authority != Applicability | matrix C/E; missing-context scenarios |
| Applicability != Execution success | matrix C/E; zero-authority applicable request |
| Old Host session != New Host session | matrix A; same-body/new-process tests |
| Same Body != Same Host session | matrix A; session_id non-semantic test |
| Self continuity != Host-state continuity | body-replacement persistence test |
| Identity continuity != permission continuity | authority non-continuity test |
| Experience continuity != applicability continuity | matrix B/D |
| Migration != action / != authorization | P2.1/P2.3 import-only children; zero-authority cases |
| Providers not read during onboarding | provider call counters in every isolated child; B1 executable-generator negative test (zero provider calls, zero generator-body executions) |
| Declaration snapshot consistency | TestP252UnstableDeclarations |
| Registry remains sole invocation boundary | TestP254; counted Registry.invoke in children |
| Adapter invoke zero during onboarding | all isolated/child counters |
| Exactly one Adapter invoke for authorized request | P2.3 explicit-authority; matrix A/F |
| Failed first onboarding retryable | TestP251/253/255/256/257 |
| One successful session per process | TestP258/259; race storm |
| body=None remains UNBOUND | TestP2510; P2.2 no-body tests |
| Session ID non-persistent/ephemeral | P2.5.13 leak search; P2.2 byte comparisons |
| No hot rebind / no post-bind grant | public API audit; authority snapshot test |
| No dynamic loading / no repository fallback | TestP2511; TestP2512; P2.1/P2.3 isolation |
| package-v2 unchanged; P1 semantics unchanged | P2.6 migration audit test; full P0/P1 suites |

## Verification

```text
python -m unittest discover -s tests -p test_p2_onboarding.py -v
python -m unittest discover -s tests -p test_p2_onboarding_isolated.py -v
python -m unittest discover -s tests -p test_p2_isolated_onboarding_acceptance.py -v
python -m unittest discover -s tests -p test_p2_replacement_matrix.py -v
python -m unittest discover -s tests -p test_p2_onboarding_hardening.py -v
python -m unittest discover -s tests -v
```

The isolated tests create/export a real package-v2, copy it to B, restore Self
with the preinstalled trusted tool, and independently install Host source.
They compare Discovery bytes/hash, then remove A's original test path and run
a separate Python process with `-I -S -B` from B. Site packages are excluded;
even an inherited PYTHONPATH pointing at the development repository/A is
ignored.

`P2.1_DEPLOYMENT` evidence reports PID, effective sys.path, Runtime/Host roots,
module origins, installed Discovery hash, or the explicit missing-dependency
error. `P2.3_EVIDENCE` adds the complete onboarding/request record for the
isolated authority scenario. `P2.4_MATRIX` prints one evidence row per
replacement case. Python standard-library paths remain available. Tests assert
no repo/A import path, no package `phanes.discovery`, correct module type
identity, and unchanged Self/package bytes. Temporary evidence paths do not
represent a persistent session or migration format.

P2.2 unit/composition tests use fresh test-only module namespaces to isolate
permanent publication guards (not a supported production reset). A separate
fresh-process test verifies the real module's one-shot guard and that its first
Runtime remains operational. Counted trusted Adapters track construction,
descriptor/capability reads and invocation; successful preparation reports
1/1/1/0 respectively. The real P1 authorized request reports Registry.list /
Registry.invoke / original Adapter.invoke = 1/1/1; zero authority reports
1/0/0; missing context/provider errors stop before Registry action.

P0/P1 production, their tests, schemas, config formats and package-v2 semantics
are unchanged. P2 is an engineering release candidate awaiting independent
review; it is not merged, tagged, or released.

## Migration exclusions (unchanged, re-audited)

Package-v2 still carries exactly the three Self files and the fixed generic
Runtime whitelist. It contains and migrates no `host_session_id`,
`HostSessionDescriptor`, onboarding code, Adapter instances, provider
callables, current context, Registry, authority, current allowed-capability
values, pending actions, or execution state. `tests/test_p2_release_audit.py`
pins the package whitelist and byte-scans an exported package for P2 session
markers after a real onboarding.

## Limitations and non-goals

P2 does **not** prove or provide: physical truth (declarations and frame
assertions remain unattested Host claims), malicious-Host or malicious-package
safety, cryptographic identity or session uniqueness, real UAV/hardware
safety, hardware or physical resource rollback, online body swap or hot
rebind, dynamic/deferred authority, semantic capability equivalence or
automatic capability adaptation, LLM interpretation, tf2/frame transforms,
cross-platform performance, or multi-process consistency. It adds no new
research mechanism; P2.3–P2.7 are engineering completion, validation, and
evidence only.
