# Security Policy

libyeast parses untrusted, potentially hostile input, and serves as a building block for sensitive software. The project
treats security as a first-class property and audits it continuously.

## Supported versions

The project has yet to reach `1.0`. Security fixes land on the latest `0.x` release, and the ABI stays unstable through
`0.x`.

| Version    | Supported |
| ---------- | --------- |
| latest 0.x | yes       |
| older      | no        |

## Reporting a vulnerability

**Do not open a public issue for security reports**.

- **Preferred:** GitHub's private vulnerability reporting (Security -> Report a vulnerability).
- **Otherwise:** email **libyeast-oren@ben-kiki.org** with details, and a reproducer where you have any.

Please allow coordinated disclosure. The maintainers acknowledge within a reasonable window, work on a fix, and agree a
disclosure date before anybody releases public details.

## Scope

**In scope:** memory-safety defects, such as a buffer overflow or a use-after-free. Resource exhaustion on untrusted
input counts too. That covers unbounded memory and pathological nesting depth. It also covers quadratic blow-up and
alias-expansion bombs.

**Out of scope:** crashes caused by the *calling program* misusing the API in ways the documentation explicitly forbids.
