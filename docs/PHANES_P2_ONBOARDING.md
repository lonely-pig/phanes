# Phanes P2 — Host Deployment and One-shot Onboarding (P2.1/P2.2)

P2.1 is a Host-side preparation increment on frozen P1 release
`v0.2.0-p1`, base commit `97c6f477fc587febd22ac12cc17c2ed532113434`.
Research novelty claim: **NONE**.

**P2.1 establishes deployment and a read-only declaration only.** P2.2 adds
explicit, one-shot private Host assembly and complete publication. Neither
import nor onboarding performs an Adapter action. P2.3 isolated end-to-end
acceptance is not implemented or claimed by this increment.

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
    allowed_capabilities: Iterable[str] = (),
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

The operation accepts only Self's state directory, existing P0 v1 config and
the separate authority/provider inputs. It accepts no live Adapter/Registry,
custom factory, module/code path, URL or caller-selected session ID. There is
no public Binding/PublishedSession wrapper, reset, rebind or grant API.

### Private assembly and sole publication point

1. Acquire a module-private nonblocking preparation guard; reject an already
   published process before any input inspection or dependency preparation.
2. Check only `callable(provider)` for the three Host providers.
3. Snapshot separate allowed names into a tuple, and copy the config. Reject
   nonempty config-sourced authority; do not merge, ignore or choose priority.
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

## Verification

```text
python -m unittest discover -s tests -p test_p2_onboarding.py -v
python -m unittest discover -s tests -p test_p2_onboarding_isolated.py -v
python -m unittest discover -s tests -v
```

The isolated tests create/export a real package-v2, copy it to B, restore Self
with the preinstalled trusted tool, and independently install Host source.
They compare Discovery bytes/hash, then remove A's original test path and run
a separate Python process with `-I -S -B` from B. Site packages are excluded;
even an inherited PYTHONPATH
pointing at the development repository/A is ignored.

`P2.1_DEPLOYMENT` evidence reports PID, effective sys.path, Runtime/Host roots,
module origins, installed Discovery hash, or the explicit missing-dependency
error. Python standard-library paths remain available. Tests assert no repo/A
import path, no package `phanes.discovery`, correct module type identity, and
unchanged Self/package bytes. Temporary evidence paths do not represent a
persistent session or migration format.

P2.2 extends the same four maintained files. The P2.1 absence-of-future-API
assertions now recognize the explicitly authorized function; import-inertness,
declaration boundaries and isolated deployment checks remain enforced.
The isolated missing-dependency probe additionally rejects explicit onboarding
without fallback, provider calls, composition or UUID generation. It does not
claim P2.3 isolated successful end-to-end acceptance.

New P2.2 unit/composition tests use fresh test-only module namespaces to isolate
permanent publication guards (not a supported production reset). A separate
fresh-process test verifies the real module's one-shot guard and that its first
Runtime remains operational. Counted trusted Adapters track construction,
descriptor/capability reads and invocation; successful preparation reports
1/1/1/0 respectively. The real P1 authorized request reports Registry.list /
Registry.invoke / original Adapter.invoke = 1/1/1; zero authority reports
1/0/0; missing context/provider errors stop before Registry action.

P0/P1 production, their tests, schemas, config formats and package-v2 semantics
are unchanged. No P2.3 completion or release acceptance is claimed.
