# Phanes P2 Engineering Acceptance Report

**GLM Implementation Evidence — Sol Final Engineering Review Accepted**

The implementation evidence below was produced by GLM; its milestone entries
retain their historical candidate context. Sol independently reviewed the
final code and cumulative diff, then tested documentation-corrected candidate
`dcf302c770c68282a8dcdd9a15a24a26dd0a9270` from a clean pinned worktree:
454 tests, zero failures/errors/skips, compileall and diff checks PASS.
Verdict: **PASS FOR P2 RELEASE**. Master was fast-forwarded to that exact
commit and independently retested (454/454 PASS) before creating annotated
tag `v0.3.0-p2`; a final documentation-only checkpoint completed the release
commit that the tag identifies. The tag and master are published to GitHub
together with this report; no GitHub Release object is created (the repository
has none and uses annotated tags). Release scope: **Embodiment Onboarding —
Explicit Host Session Discovery and Safe Binding**.

## SOL P2.2 REVIEW FINDINGS

Codex / Sol 6.1 performed an independent read-only review of P2.2 commit
`9330761` with verdict `REQUEST CHANGES`. Disposition in this candidate:

| Finding | Status | Disposition |
|---|---|---|
| **B1** — `allowed_capabilities: Iterable[str]` allowed an executable generator to be iterated during onboarding, indirectly calling a provider (reproduced by Sol: provider calls 1/0/0, publication True) | **CLOSED** | Public authority input narrowed to passive data: exact `tuple` of exact `str`, shape-checked **before** Discovery; generators/iterators/lists/custom containers rejected without iteration (commit `0f30983`). Mandatory regressions added: executable generator (zero provider calls, zero generator-body executions, zero Discovery, no publication), custom `__iter__` object (zero `__iter__` calls), passive tuple success, invalid tuple semantics still delegated to the existing Registry. The "providers not evaluated during onboarding" invariant is marked PASS only through this negative evidence. |
| **N1** — traceback frame locals may expose preparation objects under Python introspection | **documented** | No scrubber/sandbox built. Claim limited to: partial objects are not published through supported return values, module state, or exception attributes; ordinary introspection is not a supported channel and is not defended against (module and API docstrings; §5, P2 doc). |
| **N2** — `deepcopy(body_config)` can execute custom `__deepcopy__` on malicious objects | **documented** | No generic safe-object framework built. Documented that P2 config assumes trusted, passive local configuration data; deepcopy side effects out of scope (docstrings; P2 doc). |
| **N3** — `typing.get_type_hints(onboard_host_session)` raises NameError (TYPE_CHECKING-only Runtime import) | **documented** | LOW tooling issue left as-is to keep `import phanes_host.p2_bootstrap` lazy; documented with the workaround (`inspect.signature`) in the P2 doc. |

## 1. Baseline

| Item | Value |
|---|---|
| Repository | lonely-pig/phanes |
| P1 release base | `97c6f477fc587febd22ac12cc17c2ed532113434` (`v0.2.0-p1`) |
| P2.1 commit (accepted) | `acb19e14e996e0df6f4236dbf261ac8d353b3e54` |
| P2.2 commit (accepted, PASS 404/404) | `93307611763ff694ba591dd0b4003fef29b83283` |
| Working branch | `p2-glm-p2.3-p2.7` (created from exactly the P2.2 SHA) |
| Baseline verification | base SHA verified, clean tree, full suite 404/404 PASS, master/tags recorded before any change |

## 2. Commits (P2.3–P2.7)

| Milestone | Commit | Message |
|---|---|---|
| P2.3 | `2a87341` | test: add P2.3 isolated onboarding acceptance |
| P2.4 | `e96d61b` | test: add P2.4 embodiment replacement matrix |
| P2.5 | `defb0ad` | test: harden P2 onboarding failure boundaries |
| P2.6 | `7942b6a` | docs: prepare P2 embodiment onboarding release candidate |
| P2.6 finalize | `b1f2e9e` | chore: finalize P2 engineering acceptance candidate (report draft) |
| P2.7 / B1 fix | `0f30983` | fix: require passive tuple authority input (Sol P2.2 review B1) |
| P2.7 final | (this commit) | docs: close Sol P2.2 review findings and bind acceptance report to candidate SHA |

**Candidate code SHA for review: `0f309833dd1ed6dfc5adb0f06b3c35835e76e5f6`.**
The commits after it add documentation only (this report and doc updates);
the full suite was re-run green at the final HEAD and from a clean worktree.

## 3. Architecture boundary (implemented)

Ownership is unchanged from the P2 freeze:

- **SELF** (migrates): `identity.json`, `memory.json`, `experiences.json`.
- **GENERIC RUNTIME** (migrates inside package-v2): fixed whitelist of 13
  modules incl. Registry, applicability, Gateway, P1 composition; plus the
  migration tooling itself outside the package.
- **CURRENT HOST / EPHEMERAL STATE** (never migrates, never persists):
  `host_session_id`, Adapter, Registry live state, authority, context
  providers, current context, Host configuration, current binding.
- **EXTERNAL WORLD**: actual position/environment/hardware — untouched.

P2.3–P2.6 primarily added validation, tests, and documentation. P2.7
additionally applied the Sol B1 public-input boundary fix to
`phanes_host/p2_bootstrap.py`. Frozen P0/P1 production semantics:
**no changes** (verified by diff against
`97c6f47` — `phanes/` is untouched; see §12).

## 4. Implemented public API

`phanes_host.p2_bootstrap` exports exactly:

- `HostSessionDescriptor` — frozen, slotted, read-only declaration
  (`host_session_id`, `body`, `offered_capabilities`, `context_source_kinds`);
  no persistence, no authority, no live values.
- `OnboardingError` — ordinary bootstrap exception; preparation failures keep
  `__cause__`; not a P1 stable failure code.
- `onboard_host_session(state_dir, body_config, *, allowed_capabilities: tuple[str, ...] = (),
  get_environment_id=None, get_asserted_frame_id=None, get_evaluation_time=None)`
  — explicit one-shot onboarding; returns `(descriptor, p1_runtime)` or `None`
  for a valid no-body config. The authority input is passive data (exact tuple
  of exact strings, shape-checked before Discovery, never executed); duplicate/
  unknown/not-offered semantics stay Registry validation.

No public reset/grant/rebind/SessionManager/HostSessionBinding/
CapturedAdapterView/Registry accessor exists; private helpers stay private;
star-import exports exactly the three names (P2.5.14 tests).

## 5. Implemented behavior summary

- Providers are checked callable and never invoked during onboarding —
  including for executable authority inputs: a generator passed as
  `allowed_capabilities` is rejected before Discovery with zero provider
  calls and zero generator-body executions (Sol B1 negative evidence).
  Current authority enters only via the separate passive `allowed_capabilities`
  tuple; a zero-authority session is a complete published session.
- Non-empty embedded config authority is rejected before Discovery.
- The Adapter declaration snapshot is captured exactly once; the descriptor
  and the Registry see the same snapshot; the Registry remains the sole
  validator; the captured view forwards each authorized invoke to the original
  Adapter exactly once.
- Preparation failures are retryable; one successful publication per process
  is permanent and every later call — including no-body inputs — fails before
  any Host work; the first Runtime remains usable.
- UUID/session ID is generated only after successful composition, is
  ephemeral, and persists nowhere; `body=None` returns None and stays UNBOUND.
- No repository fallback, no dynamic loading, no post-bind grant, no hot
  rebind; a new Body/session requires a fresh process.

## 6. Test counts

| Suite stage | Total | Failures | Errors | Skips |
|---|---|---|---|---|
| P2.2 accepted baseline | 404 | 0 | 0 | 0 |
| P2.6 candidate draft | 447 | 0 | 0 | 0 |
| Candidate (final, incl. Sol B1 regressions; clean worktree re-run) | 454 | 0 | 0 | 0 |

New tests: P2.3 = 6, P2.4 = 9, P2.5 = 26, P2.6 audit = 2, P2.7 Sol-B1/oracle
additions = 7 (executable generator, custom iterable, passive tuple success,
Registry-delegated tuple semantics, per-provider None semantics, per-provider
exception semantics, canonical-module failed→corrected retry). No test was
deleted, weakened, renamed away, or skipped to achieve green; the P2.2
authority test was updated to the corrected passive-data contract (its
mutation premise no longer exists under B1) while keeping its no-grant-API
assertions. Validation was repeated from a clean `git worktree` checkout of
the final candidate HEAD: 454/454 PASS, 0 skips. Milestone gates additionally
ran `compileall` (phanes, phanes_host, tests) and `git diff --check` at every
commit.

## 7. Isolated deployment evidence (P2.3)

`tests/test_p2_isolated_onboarding_acceptance.py` builds the real A → B
topology (A: Self + trusted producer; B: copied-package-v2 + trusted_host with
`__init__.py`, `mock_uav.py`, `p2_bootstrap.py`, `_p0_discovery.py` +
restored_state), using real `export_package_v2` / `import_package_v2`. All
scenarios run fresh `python -I -S -B` children with hostile
`PYTHONPATH=(repo, A)`; A is renamed away before any child runs.

Measured results (child JSON evidence, asserted by the parent):

- Isolation: no effective `sys.path` entry resolves under the repo or A;
  runtime modules originate in the copied package; Host modules and
  `_p0_discovery` originate in the trusted Host tree; `phanes.discovery`
  cannot be imported; A's path does not exist.
- Import-only child: no published session and `_p0_discovery` never loaded.
  Its hooks are installed after the first bootstrap import, so its zero
  counters are not evidence about that first import. First-import zero
  construction/discovery/registry/bind/compose/UUID/action evidence comes
  from `TestBootstrapImport`, which installs hooks before importing bootstrap.
- Zero authority: onboarding succeeds; offered = (get_position, move_to);
  Registry registers nothing; matching Experience applicable yet request ends
  `CAPABILITY_UNAVAILABLE`; Adapter.invoke = 0.
- Explicit authority: full real P1 path exercised once — Registry.invoke = 1,
  original Adapter.invoke = 1, move executed by the original MockUAV adapter;
  provider calls exactly 1/1/1.
- Missing frame: onboarding succeeds; request = UNKNOWN / FRAME_CONTEXT_MISSING
  (fixture matched so only frame was missing); Registry.invoke = 0.
- Provider exception: original RuntimeError propagates (not OnboardingError);
  Adapter.invoke = 0; snapshot order stops at the raising provider.
- Immutability: Self hashes and full package digest identical before/after in
  every scenario; parent-side byte comparison and `validate_package_v2` pass.

## 8. Embodiment replacement matrix (P2.4)

`tests/test_p2_replacement_matrix.py` — every case = fresh `-I -S -B`
process(es); one migrated Self carries Experiences recorded on two bodies.
`P2.4_MATRIX` rows capture case/PID/agent/body/session/authority/context/
result/invocation counts/Self hashes.

| Case | Body | Authority | Context | Result (per fresh process) |
|---|---|---|---|---|
| A (x2 processes) | uav-1 | move_to | frame-alpha | new session IDs S1 ≠ S2; invoke exactly once each |
| B | uav-2 (same type) | move_to | frame-alpha | uav-1 Experience INAPPLICABLE / BODY_MISMATCH; 0 invokes |
| C | uav-2 | get_position only | frame-beta | uav-2 Experience applicable → CAPABILITY_UNAVAILABLE; 0 invokes |
| D | uav-1 | move_to | frame-beta / None | INAPPLICABLE / FRAME_MISMATCH; UNKNOWN / FRAME_CONTEXT_MISSING; 0 invokes |
| E | uav-2 | zero | frame-beta | applicable Experience → CAPABILITY_UNAVAILABLE; 0 invokes |
| F | uav-2 | move_to | frame-beta | invoke exactly once |

Additional established evidence: authority non-continuity (authorized A, then
zero-authority B cannot act on the same Experience); body replacement via real
package migration keeps agent_id/memory/Experience (hash-identical) while
body/authority/providers/context/Adapter of A are absent (old Experience →
BODY_MISMATCH + FRAME_MISMATCH); session ID is non-semantic (identical inputs,
different IDs, identical outcomes; ID absent from TargetContext, dependency
matching, and reason codes). Uniqueness of session IDs is observed, never
claimed cryptographically.

## 9. Failure injection summary (P2.5)

All 26 hardening tests pass with no production change required — every
injection behaved as the P2.2 design intends:

- Malformed inputs (config types/fields, body declarations, hostile adapter
  identifiers, authority shapes/content, providers) rejected with preserved
  causes; retryable.
- Unstable/exception-throwing Adapter declarations: captured exactly once;
  exceptions retryable with no publication.
- Registry stays the sole validator (invalid declarations fail as
  RegistryError inside bind; the P2 descriptor deliberately does not
  duplicate the rules).
- Compose failures (missing/corrupt Self, agent mismatch) fail before UUID;
  retryable after repair.
- uuid4 failure/garbage (empty, whitespace, failing `__str__`) publishes
  nothing; retryable.
- Second onboarding: all measured deltas zero (discovery, construction,
  descriptor/capability reads, Registry construction, bind, providers, compose,
  UUID); old Runtime usable; no-body input follows the same published rule.
- Reentrancy rejected without consuming the outer attempt; deterministic
  overlap test; 8-thread race → exactly 1 session/1 UUID/1 bind/1 construction,
  7 rejections.
- Dynamic loading attacks (paths, URLs, module names, shell fragments) cannot
  load anything; hostile body_ids stay opaque data.
- Repository fallback attacks with the repo genuinely first or last on
  `sys.path`: onboarding fails closed; the importable `phanes.discovery`
  fallback target is never called (mocked counter = 0).
- Persistence leak search: no session ID, `host_session_id`, context
  sentinels, deployment paths, `p2_bootstrap`, or authority names in Self or
  exported package bytes; no extra state files.
- Public API audit: exactly three exported names; no forbidden public names;
  private helpers stay private; Host package does not re-export.

## 10. Migration audit (P2.6)

`tests/test_p2_release_audit.py` pins the package-v2 whitelist literally
(`SELF_FILES_V2`, 13 `RUNTIME_FILES_V2` names) and byte-scans an exported
package built **after** a real onboarding plus navigation: no
`host_session_id`, `HostSessionDescriptor`, `OnboardingError`,
`onboard_host_session`, `p2_bootstrap` bytes, and no session-ID bytes in any
migrated file; the tree contains exactly whitelisted files + manifest.
Combined with P2.5.13 (Self/package scan for session ID, context sentinels,
deployment paths, authority names) the migration exclusions hold: host session
ID, descriptor instances, Adapter, providers, current context, Registry,
authority, current allowed set, pending actions, and execution state do not
migrate. package-v2 itself is unchanged.

## 11. Final invariant matrix

| Invariant | Status | Evidence |
|---|---|---|
| Import != Onboard | PASS | P2.2 TestBootstrapImport; P2.3 import_only child |
| Discovery != Authority | PASS | matrix C/E; P2.2 zero-authority tests |
| Authority != Applicability | PASS | matrix C/E; P2.2 authority-does-not-override test |
| Host declaration != Physical truth | PASS | descriptor shape/ephemerality tests; P2.5.13 |
| Self continuity without Host continuity | PASS | P2.4 body-replacement persistence test |
| Zero-authority onboarding | PASS | P2.3.5; matrix E; P2.2 |
| No inherited authority | PASS | P2.4 authority non-continuity; matrix C |
| No inherited binding | PASS | P2.4 body replacement (old Registry/Adapter absent) |
| Providers not read during onboarding | PASS | provider counters in all isolated children; P2.2; Sol B1 executable-generator negative test (zero provider calls, zero generator-body executions, zero Discovery) |
| Current context Host-local | PASS | matrix D; P2.2 context tests |
| Experience cannot populate current context | PASS | P1 gateway boundary tests; matrix D |
| Declaration snapshot consistency | PASS | P2.5.2 unstable-declaration tests |
| Registry remains sole invocation boundary | PASS | P2.5.4; counted Registry.invoke in children |
| Adapter invoke zero during onboarding | PASS | every onboarding counter assertion |
| Exactly one Adapter invoke for authorized valid request | PASS | P2.3.6; matrix A/F |
| Failed first onboarding retryable | PASS | P2.5.1/253/255/256/257 |
| One successful session per process | PASS | P2.5.8/259 incl. race storm |
| body=None remains UNBOUND | PASS | P2.5.10; P2.2 no-body tests |
| Same Body / new process / new session | PASS | matrix A; session non-semantic test |
| Different Body / new onboarding | PASS | matrix B/C/E/F; body-replacement test |
| Session ID non-persistent | PASS | P2.5.13; P2.2 package byte comparison |
| No hot rebind | PASS | public API audit; second-onboarding deltas |
| No post-bind grant | PASS | public API audit; P2.2 authority snapshot test |
| No dynamic loading | PASS | P2.5.11; static import inspection (no `__import__`/`importlib`/`eval(`/`exec(` in P2 production or shipped runtime) |
| No repository fallback | PASS | P2.5.12; P2.1/P2.3 isolation |
| package-v2 unchanged | PASS | P2.6 whitelist pinning; diff audit |
| P1 semantics unchanged | PASS | full P0/P1 suites green (362-test P1 baseline subset) |
| Frozen P0/P1 production semantics unchanged | PASS | diff vs `97c6f47` touches no `phanes/` file |

## 12. Exact changed files vs P1 release base `97c6f47`

| File | Status | Classification |
|---|---|---|
| `docs/PHANES_P2_ONBOARDING.md` | added, extended in P2.6 and P2.7 | P2 docs |
| `phanes_host/p2_bootstrap.py` | added (P2.2); modified only by Sol-B1 fix `0f30983` (passive authority input + N1/N2 docstrings) | P2 production |
| `tests/test_p2_onboarding_isolated.py` | added (P2.1) | P2 tests |
| `tests/test_p2_onboarding.py` | added (P2.2); authority test updated to the corrected passive-data contract (`0f30983`) | P2 tests |
| `tests/test_p2_isolated_onboarding_acceptance.py` | added (P2.3); Self file-set check added (`0f30983`) | P2 tests |
| `tests/test_p2_replacement_matrix.py` | added (P2.4) | P2 tests |
| `tests/test_p2_onboarding_hardening.py` | added (P2.5); Sol-B1/oracle additions (`0f30983`) | P2 tests |
| `tests/test_p2_release_audit.py` | added (P2.6) | P2 tests |
| `README.md` | modified (P2.6/P2.7 status/counts/map only) | P2 docs |
| `docs/P2_ENGINEERING_ACCEPTANCE.md` | added (P2.7, this file) | P2 docs |

Frozen P0/P1 production semantic changes: **NONE**. Unrelated files changed:
**NONE**.

## 13. Limitations (explicit non-proofs)

P2 does **not** prove or provide: physical truth (declarations, frame
assertions and provenance remain unattested Host claims); malicious-Host or
malicious-package safety; cryptographic identity or session uniqueness; real
UAV/hardware safety; hardware or physical resource rollback; online body swap
or hot rebind; dynamic or deferred authority; semantic capability equivalence
or automatic adaptation; LLM interpretation; tf2/frame transforms;
cross-platform performance; multi-process consistency. P2 adds no new research
mechanism.

## 14. Known risks

- This candidate was validated on Windows / Python 3.13.7 only; POSIX
  behavior of the subprocess-isolation tests was not exercised here.
- The isolation tests depend on `-I -S -B` interpreter semantics of the local
  toolchain; other Python implementations may resolve paths differently.
- The one-session guard is process-local and single-interpreter by design;
  deliberate module reload or private-state tampering is unsupported
  (documented, tested only as observation).
- Thread-scheduling order is not deterministic, but the asserted race-storm
  outcome (exactly one published session/UUID) follows from the lock+flag
  guard rather than timing.
- Discovery byte/SHA comparison checks copy integrity, not source
  authenticity; installation remains a trusted out-of-band step.
- In-process fresh-module test namespaces isolate publication guards for
  testing; they are not a production reset API.
- Sol N1: ordinary Python traceback/frame introspection may observe
  preparation objects; only supported publication channels are defended.
- Sol N2: `body_config` deepcopy assumes trusted, passive local configuration
  data; malicious `__deepcopy__` side effects are out of scope.
- Sol N3: `typing.get_type_hints(onboard_host_session)` raises NameError
  (TYPE_CHECKING-only Runtime import); use `inspect.signature`.

## 15. Handoff

Sol P2.2 review blocker B1 is closed in this candidate (commit `0f30983`);
N1/N2/N3 are documented as scoped limitations above and in the P2 doc.

Sol final Engineering / Release Review and integration/tagging are complete
as recorded above. Master and the annotated tag `v0.3.0-p2` are published to
GitHub with this release commit. Do not start P3.
