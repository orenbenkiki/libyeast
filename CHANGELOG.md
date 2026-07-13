# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The ABI is unstable through `0.x`.

## [Unreleased]

### Added

- Project framework: CMake build (shared + static, hardened, symbol-visibility controlled), an incremental pre-commit
  `make` gate, tests (acutest), coverage with a `// UNTESTED` contract, Doxygen docs with a completeness gate, and a
  package-consumption test.
- Continuous integration: per-sub-gate GitHub Actions workflows (static quality, C tests, docs) with independent status
  badges and CodeQL analysis, and published API docs plus an HTML coverage report on GitHub Pages.
- Version API: `ys_version`, `ys_major`, `ys_minor`, `ys_patch`.
- Pull-parser API surface: `ys_new_string_parser` / `ys_new_stream_parser` / `ys_next_token` / `ys_free_parser`, with
  `ys_fd_reader`/`ys_fp_reader` reader adapters, a pluggable allocator, and the `ys_counting_allocator` leak counter.
  The parser core is not implemented yet, so `ys_next_token` returns a "not implemented" error.
- Character decoder: UTF-8 input is validated and classified against the grammar without a Unicode codepoint ever being
  assembled. Each character becomes a 32-bit key — the id of the character where the grammar names it, one bit per
  character set the grammar tests, and the bytes it consumed — so a test in the parser is a single comparison or a
  single AND. The tables are generated from the grammar and gated against drift.
- Annotated grammar: `grammar/yeast-spec-1.2.yaml` is libyeast's own grammar, and the source everything is generated
  from. It carries the YAML 1.2 rules together with the yeast tokens they emit, which the official grammar cannot
  express — it inlines the indicator characters, losing the structure the token layer hangs on, and names no token at
  all. It is also the only place the yeast token format is written down: the notation, the codes, and, rule by rule,
  what each emits and why. Four gates keep it honest. Erasing libyeast's additions recovers the official grammar
  exactly, so the grammar is hand-authored where it must be and machine-proved where it can be. Every character the
  parser consumes must lie within a token action, so a forgotten one fails the build rather than emitting an `unparsed`
  token years later. Every `begin-` marker must be closed by its own `end-`, on every path and for every context and
  chomping. And every rule that emits tokens must say which, checked against the grammar itself, so a note that is wrong
  fails as surely as one that is missing.
- Indentation detection, which the official grammar declares a "special rule" and never defines, and which it elsewhere
  leaves as an integer added to the string `"auto-detect"`. libyeast defines it, and its two departures from the
  official grammar are declared, with their reasons: `m` is an indentation now, and `s-l+block-indented` sets the `m` it
  had been reading and never setting.

_No release has been tagged yet; the YAML parser itself is not implemented._
