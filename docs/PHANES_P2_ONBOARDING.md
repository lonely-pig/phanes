# Phanes P2.1 — Host Deployment Boundary & Read-only Declaration

P2.1 is a Host-side preparation increment on frozen P1 release
`v0.2.0-p1`, base commit `97c6f477fc587febd22ac12cc17c2ed532113434`.
Research novelty claim: **NONE**.

**P2.1 does not establish a usable Host session.** It does not implement
`onboard_host_session`, generate session IDs, bind a Registry, compose a P1
Runtime, apply authority, publish a session, or perform an action. Full
onboarding and publication belong to a later, separately authorized increment.

## Implemented public surface

Explicit module: `phanes_host.p2_bootstrap`.

- `HostSessionDescriptor`: a frozen, slotted dataclass with exactly
  `host_session_id: str`, `body: BodyDescriptor`,
  `offered_capabilities: tuple[CapabilitySpec, ...]`, and
  `context_source_kinds: tuple[str, ...]`.
- `OnboardingError`: an ordinary bootstrap exception type only, not a new
  stable P1 error code or an implemented failure-wrapping mechanism.

The descriptor directly reuses the existing BodyDescriptor and CapabilitySpec
types. Its supported constructor rejects mutable containers, live values in
the nested declaration fields, and context kinds outside the existing P1
names: `body_instance_id`, `environment_id`, `frame_id`, `evaluation_time`.
Context kinds may describe a subset; existence does not assert a known value.
There is no ontology or new TargetContext field.

These are declaration-shape checks, **not Registry compatibility validation**.
A descriptor is not evidence of successful binding, authority, context
readiness, physical identity/truth, applicability, or execution success.
Its supplied session string is opaque and is not normalized or generated.
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

`import phanes_host.p2_bootstrap` defines declarations and a private helper.
It does not even load `_p0_discovery` automatically. Import does not construct
an Adapter/Registry, discover a Body, bind, compose Runtime, call providers,
snapshot context, create a session ID, grant authority, or act.

P2.1 preserves `Import != Onboard`. Loading the separate Discovery dependency
only defines its existing fixed factory and functions; it does not call them.
No physical readiness or malicious-Host isolation guarantee is claimed.

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

The four maintained additions are this document, `p2_bootstrap.py`, and the
two new P2.1 test files. P0/P1 production, schemas, tests, config formats and
package-v2 semantics are unchanged. P2.2/P2.3 capabilities are not yet supported.
