# Security

Loopwright is a single-user program for your own machine. Its security model, including what it does not cover,
is in [docs/security.md](docs/security.md).

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting:
**Security → Report a vulnerability** on this repository. Include what you did, what happened and what you
expected. You should hear back within a week.

Especially interesting: a way to make a tainted turn perform an outside effect, a path that escapes the workspace
or the package folder, a request the local server accepts from another origin, or a secret that reaches a trace.
