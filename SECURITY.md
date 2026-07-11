# Security Policy

libyeast parses untrusted, potentially hostile input and is intended as a building block for sensitive software.
Security is treated as a first-class, continuously-audited property.

## Supported versions

The project is pre-1.0. Only the latest `0.x` release receives security fixes, and the ABI is unstable through `0.x`.

| Version    | Supported |
| ---------- | --------- |
| latest 0.x | ✅        |
| older      | ❌        |

## Reporting a vulnerability

**Do not open a public issue for security reports.**

- Preferred: GitHub's private vulnerability reporting (Security → Report a vulnerability).
- Otherwise: email **libyeast-oren@ben-kiki.org** with details and, ideally, a reproducer.

Please allow coordinated disclosure: the maintainers will acknowledge within a reasonable window, work on a fix, and
agree a disclosure date before public details are released.

## Scope

In scope: memory-safety defects (buffer overflows, use-after-free) and resource-exhaustion / denial-of-service on
untrusted input (unbounded memory, pathological nesting depth, quadratic blow-up, alias-expansion bombs).

Out of scope: crashes caused by the *calling program* misusing the API in ways the documentation explicitly forbids.
