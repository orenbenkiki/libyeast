# libyeast Design

libyeast is an efficient streaming YAML 1.2 parser in C, generated from the formal grammar. It is a *token* parser. It
produces a yeast token stream, a lossless representation of the document's structure.

Higher level layers use this stream. They compose the tokens into a node graph and link anchors and aliases. They
resolve tags, deal with duplicate keys, and construct native data structures. The stream also preserves the presentation
details of the input document, and that covers indentation and comments. That makes it a basis for YAML re-formatters
and pretty printers.

The overall architecture of the project follows. It gives the big picture perspective. The source files document the
actual details, and this document does not repeat them.

## Main Parts

1. The C parser and its public facing API (in `include/yeast.h`) is the goal of the whole thing. This ships on its own,
   and you can consider this repository to be a self-contained C library. CMake builds the library, and Conan can
   consume it. It will work fine on these terms. A user of the library can look at the generated
   [Doxygen documentation](https://orenbenkiki.github.io/libyeast/) and go no further.

1. Python code that generates the C parser tables, based on the YAML specification BNF. The build commits the generated
   tables into the repository. Building and installing the C parser library does not re-run the Python code. A pipeline
   of mechanical semantics-preserving transformations takes the specification's backtracking PEG syntax toward a
   deterministic LL(1) grammar. `normalize.OWED` names the invariants the pipeline has yet to settle. A
   semantics-preserving transformation keeps the result true to the YAML specification.

1. An extensive test framework based on the fixtures based on the
   [Haskell YamlReference](https://github.com/orenbenkiki/yamlreference) and
   [YAML Test Suite](https://github.com/yaml/yaml-test-suite) projects test cases. The framework runs the grammar and
   the result of a transformation step against the canonical interpretation of YAML documents. The C parser has no
   automaton yet, and the C tests cover the library's interface and the error a read answers with.

1. Development is coordinated by the attached [Makefile](Makefile) which orchestrates running the Python code to
   generate the tables and test the tree. We built this project from the ground up using Claude, as an experiment in how
   far a Claude-built project can go. It therefore includes automated verification that documentation and comments are
   clear of the awful Claude style, are complete, and consistent with the code. Shell and python scripts support that,
   and so do Claude settings that force it to be as well behaved as possible.

## C Parser Overview

The [Doxygen documentation](https://orenbenkiki.github.io/libyeast/) already includes an overview of the code from the
user's point of view. It includes the relevant details in the documentation of the public API. Therefore this document
does not repeat them.

The architecture of the implementation comes in broad strokes here. The source code comments give the details, and this
document does not repeat them either.

The parts around the automaton work. The generator does not build the automaton yet.

At the bottom, the *decoder* turns input bytes into something a parse can branch on. It does not assemble Unicode
codepoints. It classifies a character straight into a key. The key holds a bit per character set the grammar tests, with
the grammar's unions and subtractions already evaluated into the bits. A decision is then a single comparison, and a
token is a span of the original bytes. The generator writes the tables it reads, and those tables sit in the tree.

The *parser* holds the runtime state such an automaton needs. There is a window over the input. There is a queue of
tokens already built. That queue can hold a run back until a later character settles the codes of that run. There is a
stack of the productions the parse is inside. A frame holds where to return and the indentation in force. The C call
stack holds no part of that state. That is what lets a caller pull a token and resume mid-production on the next call.

The step between states is missing. `ys_read_token` on a parser has no transition to take. It hands back a single error
token reading "not implemented".

Above that, a *token source* is a single handle over tokens whatever made them. It is YAML parsed from memory, YAML
parsed from a stream, or a yeast wire replayed. The wire arm is complete. A token stream written earlier reads back
through the same call a parse would use. A *token sink* is the mirror. It writes tokens out as a yeast wire or back as
YAML.

Beside these sits the plumbing. That is the buffered read from a byte source, the allocation through the pluggable
allocator, and the table of static message strings.

## Grammar Transformations Overview

The YAML specification grammar is a backtracking PEG (Parsing Expression Grammar). This makes it easy to directly
express complex notions in the grammar. It is relatively straightforward to implement this directly (e.g.
[Haskell YamlReference](https://github.com/orenbenkiki/yamlreference) and the
[YAML Reference Parser](https://github.com/yaml/yaml-reference-parser)). We even have such a
[parser](generator/interpreter.py) implemented here, and we test the grammar through its transformations with that
parser. Such parsers are correct by construction, but are inefficient.

In contrast an ideal deterministic LL(1) grammar only requires looking at the next character. A parser for such a
grammar streams the input a character at a time. It is a finite state machine with a stack for pushing and popping state
data. This allows for an efficient implementation.

The approach here is to convert the PEG grammar to a deterministic LL(1) grammar. The YAML specification allows for such
a transformation in general. There are rough edges we work around. We get close enough for practical purposes.
Specifically, we have cases where we collect provisional tokens until a later character resolves them, and only then
emit those tokens.

The YAML spec specifically restricts the amount of data in such provisional tokens to `1024` characters for plain keys.
For automatic detection of indentation of block scalars, the story is more nuanced. A true parser could make do with
counting the initial empty lines and the maximal number of spaces in a line. We emit tokens that cover the input. We
must therefore keep such leading empty lines as provisional tokens. Here the YAML spec does not restrict the amount of
data we need to keep. To compensate, we restrict the total memory the parser uses before it rejects the input. That is a
good idea in general. This isn't a problem in practice. Block scalars do not start with many empty lines, certainly not
enough to cause excessive memory consumption.

We start with a slightly tweaked version of the [YAML specification BNF rules](grammar/yeast-spec-1.2.yaml). We check
that version against [the original](third_party/yaml-grammar/yaml-spec-1.2.yaml). We then use a series of
semantics-preserving steps to transform the backtracking PEG grammar, step by step, to a deterministic LL(1) grammar
([`generator/normalize.py`](generator/normalize.py)). From the result we generate the C tables
([`generator/grammar2decoder.py`](generator/grammar2decoder.py)). The generator writes the character tables. `PLAN.md`
owes the parser's state tables.

## Ensuring correctness

We try to ensure the correctness of the result in a pair of ways. First, we try and keep the grammar transformation
steps small and plain. A reviewer can then read a step directly and see that it preserves the semantics. Second, we test
against a suite of test cases. That covers the original grammar and the result of a step. That suite tries to show the
semantics survive. The C parser has no automaton to run against it yet.

Our test suites come in the groups below.

1. Test fixtures in [`tests/spec/`](tests/spec) we based on
   [Haskell YamlReference](https://github.com/orenbenkiki/yamlreference). These are low level tests. They ensure the
   parser produces tokens as the YAML specification says, and that the tokens keep the presentation details. A fixture
   is a pair of files named `<production>[.n=N][.p=N][.c=C][.t=T][.r=R][.i=I].<case>`. The first holds an `.input` YAML
   fragment. The second holds the `.output` token stream that production must emit for it. The name says which rule to
   run and with which parameters. A fixture therefore tests a rule rather than a document.

   An input the parser must reject is a fixture like any other. Its name states the `invalid` case. Its `.output` pins
   the `!` error token and the position, and the markers that close behind follow. That is what lets a test decide a
   rejection rather than merely observe it.

   If you need to generate the output of a new fixture, [`generator/regen_fixture.py`](generator/regen_fixture.py) runs
   the interpreter over a named input and freezes what it emits.

   We don't reuse the Haskell YamlReference test suite unchanged. We have changed things and added more tests. We read
   UTF-8 and stop there, and its UTF-16 and UTF-32 cases stay out. Our `bom` token holds the mark itself as opposed to
   the LE/BE output from the Haskell YamlReference. We also ensure that no token contains a line break, other than the
   line break tokens themselves. This is especially relevant for the `unparsed` tokens we emit following an error.

1. Test cases in [`third_party/yaml-test-suite/`](third_party/yaml-test-suite) we copy as-is from the
   [YAML Test Suite](https://github.com/yaml/yaml-test-suite). These are high level tests. They ensure the parser
   produces the essential YAML events as the YAML specification says. Such a test discards many presentation details
   rather than the whole set. We pin the commit that [YAMLStar](https://github.com/yaml/yamlstar) itself tests against.

   In general we treat this as "golden" and attempt to produce exactly the same results as the test suite reference. We
   call a disagreement out explicitly in [`check_star.DIVERGENCES`](generator/check_star.py). It names the case and
   justifies the difference. An example is `JEF9/02`, an empty kept block scalar whose input ends in no line break. The
   test suite adds the line break automatically. Our implementation correctly parses the input without an implicit final
   line break.

A bug we find comes with a test case that demonstrates it. That test must trigger before the fix goes in.

## Vetting the project

We vet the project along the axes below. The list names them.

1. **Functional correctness:** the test suites described above cover this.

1. **Memory safety of the C parser:** The Debug build compiles with AddressSanitizer and UndefinedBehaviorSanitizer. The
   tests run under both. Leak detection differs by platform. On Linux LeakSanitizer flags a leak as a test exits. Apple
   clang has no LeakSanitizer. On MacOS the `leaks` tool reads the Release binary instead. The portable test is using
   `ys_counting_allocator` which verifies that allocations and deallocations match.

1. **Code style:** standard tools do this. `clang-format` formats the C, and `clang-tidy` and `cppcheck` lint it.
   `black`, `ruff` and `pylint` do the same for the Python. `mdformat` handles the markdown. `gersemi` handles the
   CMake, and `shfmt` handles the shell. A pair of scripts of our own verify additional style issues.
   `scripts/check_comments.py` checks the comment style, and `scripts/wrap_long_comments.py` reflows a comment block to
   the column limit.

1. **Prose style:** this is difficult. Claude writes the prose, and Claude is bad at it. We fight this with a pair of
   layers of defense. We run mechanised tests for known Claude anti patterns directly from the Makefile. We provide
   [Claude settings](.claude/settings.json) to prevent known Claude anti patterns from manifesting in newly created
   prose. Those settings run write-time hooks that refuse an edit rather than reporting it afterwards.

1. **Prose correctness:** this is even harder. We run a [workflow](.claude/workflows/pre-commit-review.js) with small
   set of focused Claude agents to review the final results for higher level properties.

Running `make pc` will verify what we managed to automate. Running the `pre-commit-review` workflow in Claude tries to
verify the higher level prose correctness properties. Setting this up has been challenging to say the least.

Once pushed, github will run [workflows](.github/workflows) to re-verify things. These perform almost exactly the same
set of tests. Mercifully, the Claude workflow stays out. CodeQL verification comes in. In addition, these build the
github pages containing the [Doxygen documentation of the C parser](https://orenbenkiki.github.io/libyeast/). That is
what users come for.
