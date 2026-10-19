"""The provider adapter layer (M4.1, ``OPEN-58`` -- ``OPEN-66``).

Everything that knows which cloud a server lives in lives under this package; nothing above
it may branch on a provider (``scripts/check_provider_branching.py``). The interface is in
:mod:`app.providers.base`, and it has no parameter a command, script, argv or document name
could travel in -- the signature is the control (docs/SECURITY.md rule 1).
"""
