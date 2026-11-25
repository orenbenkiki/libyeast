# SPDX-License-Identifier: MIT
"""
Break the project into fragments.

A fragment pairs prose with the code that prose documents. The code may be empty. A Python class is a fragment. So is a
C function, a Makefile target or a markdown paragraph. A comment block documenting no declaration is a fragment too.

A fragment's extent runs from the first line to the last. The fragment owns those lines, apart from the lines owned by
the fragments inside it. A docstring on an owned line is prose. So is a comment. A licence tag and an interpreter line
are not prose. Nor is a Python or C tool directive in a function body or at the end of a line. The remaining lines are
code, and the code takes the lines of the fragments inside it as well.

A key names the language, the file and the fragment. `Python:ir.py:Question` is a key. A fragment a declaration names
keeps its key across an edit. A fragment with no such declaration takes the digest of its prose. A rewrite of that prose
gives a new key. A fragment holds a prose digest and a code digest. `update_ledger_and_queue` keys the ledger on the
prose digest.

The prose of a fragment cites other fragments by name. `resolved` turns a citation into a key.

This module decides `a-file-says-what-it-is` and `a-file-description-comes-first`. `a-file-of-the-tree-has-a-reader`
rests on it too. A file fragment with no prose is a fault, apart from the file fragment of a `Conf` file. The block at
the top of a file describes that file. A lower block over a declaration describes that declaration. Any other lower
block outside the declarations and the function bodies is a fragment of its own. A lower block over no declaration
belongs to no fragment where `prose_rules.is_a_heading` reads it as a heading. `language_of` names the reader a file
has, and the `unread_files` checker refuses a file with none.

`prose_literals` reads a string literal that says something. `a-refusal-may-write-the-words-it-bans` keeps a hook
refusal out of that reading. `not_prose_lines` reads the `not-prose:` marker a writer puts on a literal holding a
layout.

`prose_rewrite.refusal_for` skips `CHANGELOG.md` for the shape rules. `is_about_the_tree` answers no for that file.

The write-time hooks call this module. `a-write-time-hook-reads-the-edit-in-place` is the rule. `written` builds the
file an edit would leave. A hook hands that file to `refusals_for_edit`. `refusals_for_edit` reads that file through
`fragments_touched` and `literals_touched`, and the `_PROSE` checkers read what those give. The `_ANY` checkers read the
whole file.

**Usage:** `python3 generator/collect_fragments.py`. The `--json` flag writes the fragments. The `--jsonl` flag writes a
fragment per line. `converge_prose` calls `fragments` itself.
"""

import ast
import bisect
import collections
import functools
import hashlib
import io
import json
import os
import re
import sys
import textwrap
import tokenize

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field, replace

import gate
import prose_rules

# The file that records what a change did rather than the tree's current shape. Its entries stay true as the code moves
# on.
_NOT_ABOUT_THE_TREE = ("CHANGELOG.md",)

# A markdown heading.
_A_HEADING = re.compile(r"^(#+)\s+(.*)$", re.M)

# A rule of `_THE_RULES`. The list item opens on the rule's name in bold, and the fragment takes that name.
_A_RULE = re.compile(r"^- \*\*([a-z0-9]+(?:-[a-z0-9]+)+)\*\* - ")

# The conventions. A list item there is a named rule.
_THE_RULES = os.path.join(".claude", "conventions.md")
_A_TOP_ITEM = re.compile(
    r"^([-*+]|\d+\.)\s+"
)  # A bulleted or numbered markdown list item. The pattern matches only an item with no indent.

# The fence that makes a markdown block code rather than prose.
_A_FENCE = re.compile(r"^\s*```")

# The fence around the YAML a document may open with. An agent definition such as `.claude/agents/prose-claims.md` names
# itself and its tools there. The YAML holds data rather than prose. `code_only` serves the YAML as the code of a file
# fragment.
_A_FRONT_MATTER = re.compile(r"^---\s*$")

# The lines a file opens with rather than prose. The binding holds the interpreter line and any SPDX tag once their
# marker is off.
_AN_OPENING_LINE = re.compile(r"^(!|SPDX-[A-Za-z-]+:)")

# The interpreter line and any SPDX tag line, with their marker still on. Those may come above a file's description.
_SITS_ABOVE_IT = re.compile(r"^\s*(#!|(//+|#)\s*SPDX-[A-Za-z-]+:)")

# The quotes a docstring opens with. Prefix letters may come in front of them.
_OPENS_A_STRING = re.compile("^[rbfuRBFU]*('''|\"\"\"|'|\")")
_CLOSES_A_STRING = re.compile(
    "('''|\"\"\"|'|\")$"
)  # A string closes on a run of quotes at the end of its last line, with no prefix in front.

# The shape a name takes where prose cites it. The name sits in backticks. A path may open on a dot, as
# `.claude/settings.json` does. A production of the grammar may hold a `+`, as `c-l+literal` does. `_A_CITED_C_NAME`
# matches the bare public name that C prose also cites.
_A_CITED_NAME = re.compile(r"`(\.?[A-Za-z_][A-Za-z0-9_./+-]*)`")
# A `Makefile` target as prose runs it, as in `make examine-claims` or `make examine-claims CLAIM_COUNT=400`. Such a
# citation names the target.
_A_CITED_TARGET = re.compile(r"`make ([A-Za-z0-9_.-]+)[^`]*`")
# a public name that C prose writes bare. The name opens with ys_ in either case.
_A_CITED_C_NAME = re.compile(r"\b((?:ys|YS)_[A-Za-z0-9_]+)\b")

# The language a file is written in, by the suffix or the name it has.
_LANGUAGES = (
    ((".py",), "Python"),
    ((".c", ".h"), "C"),
    ((".sh",), "Shell"),
    ((".yml", ".yaml"), "YAML"),
    ((".js",), "JS"),
    ((".md",), "Markdown"),
    ((".json",), "JSON"),
    (("Makefile",), "Make"),
    (("CMakeLists.txt", ".cmake"), "CMake"),
    (
        (
            ".clang-format",
            ".clang-format-version",
            ".clang-tidy",
            ".editorconfig",
            ".gitignore",
            "yeast.pc.in",
            "yeastConfig.cmake.in",
        ),
        "Conf",
    ),
)

# The line a markdown file stops holding its own prose at. A generator writes the text below such a line. That text
# copies prose the tree holds somewhere else. The collector reads no prose below such a line.
COPIED_BELOW = "<!-- `generator/write_agent_prompts.py` writes the text below this line. -->"

# The line a `_` key opens on, matched at the start of the line.
_A_JSON_NOTE = re.compile(r'^\s*"_"\s*:')

# A key a digest ends. The report below reads such a key against the prose of its fragment.
_A_DIGEST_NAME = re.compile(r"#[0-9a-f]{1,}$")

# The text a JSON string holds, with the escapes left as the file writes them.
_A_JSON_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')

# The marker a line comment opens with, by the language of the file. JSON writes no comment, and the empty marker says
# so. A `#` in a C file opens a preprocessor directive, as https://en.cppreference.com/w/c/preprocessor says. C and JS
# write a block comment too, and `_scanned` reads a block comment under the `//` marker.
#
# Public. `apply_prose`, `marker_lines` and `scripts/wrap_long_comments.py` read a comment's marker here.
MARKERS = {
    "C": "//",
    "JS": "//",
    "Python": "#",
    "Shell": "#",
    "YAML": "#",
    "Make": "#",
    "CMake": "#",
    "Conf": "#",
    "JSON": "",
}


@dataclass(frozen=True)
class _Text:
    """
    A half of a fragment, with the digest that says whether it moved. `lines` holds the file line of a line of `content`
    where `_code_text` built the half. The digest reads `content` and leaves `lines` out. A move of the code changes no
    digest.
    """

    content: str
    sha: str
    lines: tuple[int, ...] = ()


@dataclass(frozen=True)
class _Site:
    """
    A site of a fragment. A site is a run of prose lines in a file, with the prose those lines hold. A markdown
    paragraph makes a site too.

    `lines` holds offsets from the start of the fragment in the file of the site. `site_lines` turns those offsets into
    line numbers.

    A struct's own comment and the comment on a field count as sites apart. A C function declared in a header and
    defined in a source has sites in both files. `_joined` puts the sites of both files on a fragment. A rewrite comes
    back as a prose per site. `generator/apply_prose.py` writes the prose of a site back through the lines of that site.

    `prose` holds the text `flattened` gives. `as_wrapped` holds the prose as the file wraps it. `apply_prose.does_hold`
    reads `as_wrapped`.
    """

    path: str
    lines: tuple[int, ...]
    prose: str
    as_wrapped: str
    role: str = "comment"  # `docstring`, `comment` or `note`. A note is a comment inside a function body.
    code_below: str = ""  # the code lines a note sits on. A site of another role holds none.
    code_in_front: str = ""  # the code in front of a comment on the same line. A site of another kind holds none.


@dataclass
class Fragment:
    """
    A prose and the code it documents, keyed by place and name.

    `prose` holds the prose of the sites as `flattened` gives it. A site is a paragraph of its own.

    `starts` maps a file to the line the fragment starts on there, as the files read at collection. A fragment with
    sites in more than a single file holds a start per file. A caller outside this module reads a position through
    `site_lines`. A write moves the lines below it.

    `references` maps a key `cited_keys` finds in the prose to the code digest of that key.
    """

    key: str
    path: str
    kind: str
    starts: dict[str, int]
    prose: _Text
    code: _Text
    sites: tuple[_Site, ...] = ()  # the sites the prose comes from, in the order the prose reads.
    references: dict[str, str] = field(default_factory=dict)

    @property
    def start_line(self) -> int:
        """The line the fragment starts on in its own file, as that file read at collection."""
        return self.starts[self.path]


# The marker a production of the specification's BNF opens on. The marker writes the production's number and its name.
# The lines below a marker state that production, down to the next marker or the end of the text. Trailing comment lines
# go to the production below.
_A_SPEC_MARKER = re.compile(r"^:(\d+): ([a-zA-Z0-9][a-zA-Z0-9+-]*)$")


def _spec_productions(text: str) -> list[tuple[str, int, str]]:
    """
    `(the production's name, the line it opens on, the lines stating it)` for the specification's BNF `text`.

    The text of a production comes back rather than the IR that `annotated2ir` builds from it. A comment in the lines
    holds the specification's own `::=` statement of the production, as `s-double-escaped` shows. `annotated2ir.load`
    reads the file through `yaml.safe_load`, and the IR holds no comment.

    A production may end on comment lines holding the banner of the section below. Those comment lines open the
    production that follows them. A line opening on a spec number that `_A_SPEC_MARKER` does not match raises.
    """
    lines = text.split("\n")
    for at, one in enumerate(lines, start=1):
        if re.match(r":\d+: ", one) and not _A_SPEC_MARKER.match(one):
            raise ValueError(f"the spec marker on line {at} holds no production name: {one}")
    found = ((at, _A_SPEC_MARKER.match(one)) for at, one in enumerate(lines))
    opens = [(at, one.group(2)) for at, one in found if one]
    held = []
    banner: list[str] = []
    for (at, name), (next_at, _named) in zip(opens, opens[1:] + [(len(lines), "")]):
        said = lines[at:next_at]
        trailing: list[str] = []
        while said and (not said[-1].strip() or said[-1].startswith("#")):
            trailing.insert(0, said.pop())
        held.append((name, at + 1 - len(banner), "\n".join(banner + said)))
        while trailing and not trailing[0].strip():
            trailing.pop(0)
        banner = trailing
    return held


def _read_only() -> list[Fragment]:
    """
    The fragments of `gate.READ_ONLY`. Such a fragment holds code and no prose of ours.

    Prose of ours rests on the generated table `src/decoder_tables.h`, on the specification's BNF, and on the files
    under `third_party/`.

    The specification's BNF comes apart into a fragment per production, keyed by the production's name. Prose of ours
    names a production that way. This fragment names `nb-single-text` by its bare name. A C file comes apart the way
    `_c_fragments` takes one. A reader then fetches a declaration rather than the file around it. A declaration's own
    comment joins its code. The file fragment of a read-only C file holds the whole file. The comments of the file stay
    in it.

    `fragments` leaves such a fragment out.
    """
    held = []
    for path in gate.READ_ONLY:
        with open(os.path.join(gate.TREE, path), "rb") as handle:
            code = handle.read().decode("utf-8", "backslashreplace")
        if path.endswith(".txt"):
            key = _keyed("Text", path, "")
            held.append(Fragment(key, path, "read-only", {path: 1}, _text([]), _Text(code, _digest(code))))
            continue
        if path.endswith(".yaml"):
            file_key = _keyed("Grammar", path, "")
            held.append(Fragment(file_key, path, "read-only", {path: 1}, _text([]), _Text(code, _digest(code))))
            for name, first, said in _spec_productions(code):
                key = _keyed("Grammar", path, name)
                held.append(Fragment(key, path, "read-only", {path: first}, _text([]), _Text(said, _digest(said))))
            continue
        for one in _c_fragments(path, _line_commented(code)):
            said = (
                code
                if is_a_file(one.key)
                else "\n".join(part for part in (one.prose.content, one.code.content) if part)
            )
            held.append(Fragment(one.key, path, "read-only", one.starts, _text([]), _Text(said, _digest(said))))
    return held


def code_only() -> list[Fragment]:
    """
    The fragments holding code and no prose of ours. These are the fragments of `_read_only`, the front matter of a
    markdown document, and a JSON file as a file fragment. A file `gate.UNREAD` names is a file fragment too.

    A markdown file under `third_party/` is a file fragment as well. The key of such a fragment holds the file's path. A
    settings file whose comments hold no prose for the file is a file fragment too.

    Public. A reader of the fragments by key reads these beside `fragments`. `fragments` leaves them out.
    `_undescribed_fragments` refuses a fragment holding no prose.
    """
    held = _read_only()
    for path in gate.prose_files():
        named = str(path.relative_to(gate.TREE))
        language = language_of(named)
        if language == "Conf" and not any(is_a_file(one.key) for one in _fragments_of(named)):
            source = path.read_text(encoding="utf-8")
            held.append(
                Fragment(
                    _keyed(language, named, ""), named, "file", {named: 1}, _text([]), _Text(source, _digest(source))
                )
            )
            continue
        if language not in ("Markdown", "JSON"):
            continue
        source = path.read_text(encoding="utf-8")
        code = _front_matter(source) if language == "Markdown" else source
        if code or language == "Markdown":
            key = _keyed(language, named, "")
            held.append(Fragment(key, named, "file", {named: 1}, _text([]), _Text(code, _digest(code))))
    for named in gate.tracked_files():
        is_vendored_note = named.startswith("third_party/") and named.endswith(".md")
        if os.path.basename(named) not in gate.UNREAD and not is_vendored_note:
            continue
        with open(os.path.join(gate.TREE, named), encoding="utf-8") as handle:
            code = handle.read()
        key = f"Text:{named}" if is_vendored_note else _keyed("Text", named, "")
        held.append(Fragment(key, named, "file", {named: 1}, _text([]), _Text(code, _digest(code))))
    return held


def _front_matter(source: str) -> str:
    """
    The lines of `source` from its opening fence to the next fence. A source opening on no fence gives an empty string.
    """
    lines = source.split("\n")
    if not _A_FRONT_MATTER.match(lines[0]):
        return ""
    for at, line in enumerate(lines[1:], start=1):
        if _A_FRONT_MATTER.match(line):
            return "\n".join(lines[: at + 1])
    raise ValueError("front matter opens with no closing fence")


# The head of a read key asking what calls a name. Such a key names no fragment, as `is_a_question` tells. `claim_state`
# answers it by asking the question again through `answered_by`.
CALLERS = "callers:"

# The head of a read key asking what mentions a name.
MENTIONS = "mentions:"


# The environment name of the file taking a line per read of a claims reader. The fetch server appends to that file.
# `converge_claims` names the file per call. `prose_answer` reads the file and checks the citations of an answer.
CLAIM_RECORD = "YEAST_CLAIM_RECORD"

# The environment name of the file holding a draft a claims reader reads instead of the tree. The file maps a key to the
# prose per site. `converge_prose` names the file. `prose_answer` reads a claims answer against the draft.
CLAIM_DRAFT = "YEAST_CLAIM_DRAFT"

# The environment name holding the keys of a critic's batch. A line holds a key. `converge_prose` sets the variable for
# a critic call. `prose_answer` refuses a verdict outside the batch. It refuses an answer that leaves a key of the batch
# unanswered.
CRITIC_BATCH = "YEAST_CRITIC_BATCH"


# The languages whose fragments `bare_code` blanks whole. `callers_of` then finds no call in such a fragment.
_UNCALLED = ("Markdown", "Conf", None)

# The languages of `_QUOTES` whose strings hold text. `bare_code` keeps a string of the other languages. Such a string
# may hold an expansion. `CLANG_FORMAT_FOUND` in the `Makefile` expands `$(CLANG_FORMAT)` inside quotes.
_TEXT_STRINGS = ("C", "JS")

# The languages of `_QUOTES` whose comments hold code. A comment of `grammar/yeast-spec-1.2.yaml` holds a production of
# the YAML spec. `s-double-escaped` there calls `s-flow-line-prefix(n)` in its comment.
_CODE_COMMENTS = ("YAML",)

# The languages whose text `_code_digest` reads whole.
_READ_WHOLE = ("Markdown", "Conf", None)


def _prose_comments(comments: list[tuple[int, int]], language: str) -> list[tuple[int, int]]:
    """
    The spans of `comments` that hold prose. `comments` comes from `_scanned` over code in `language`. A comment of a
    `_CODE_COMMENTS` language holds code.
    """
    return [] if language in _CODE_COMMENTS else comments


@functools.cache
def bare_code(code: str, language: str | None) -> str:
    """
    Blank the string literals and the comments of `code`. A language of `_UNCALLED` goes blank whole. A blanked
    character turns into a space. A line break stays.

    An f-string keeps its interpolations. A JS template literal goes blank along with its interpolations. A string of a
    `_QUOTES` language outside `_TEXT_STRINGS` stays. A comment of `_CODE_COMMENTS` stays. A language outside
    `_UNCALLED`, `_QUOTES` and Python raises.

    Public. `check_documents` reads the names the C writes through here.
    """
    if language in _UNCALLED:
        spans = [(0, len(code))]
    elif language == "Python":
        starts = [0] + [at + 1 for at, char in enumerate(code) if char == "\n"]
        spans = [
            (starts[token.start[0] - 1] + token.start[1], starts[token.end[0] - 1] + token.end[1])
            for token in tokenize.generate_tokens(io.StringIO(code).readline)
            if token.type in (tokenize.STRING, tokenize.COMMENT, tokenize.FSTRING_MIDDLE)
        ]
    elif language in _QUOTES:
        strings, comments = _scanned(code, _QUOTES[language], MARKERS[language])
        spans = [(opens, ends) for _line, opens, ends, _said in strings if language in _TEXT_STRINGS]
        spans += _prose_comments(comments, language)
    else:
        raise ValueError(f"`bare_code` knows no literals of the language {language}")
    blanked = list(code)
    for opens, ends in spans:
        for at in range(opens, ends):
            if blanked[at] != "\n":
                blanked[at] = " "
    return "".join(blanked)


@functools.cache
def _identifiers(code: str) -> frozenset[str]:
    """The names `AN_IDENTIFIER` finds in `code`."""
    return frozenset(AN_IDENTIFIER.findall(code))


def _naming(
    held: list[Fragment],
    name: str,
    says: re.Pattern[str],
    declaring: frozenset[str] | None = None,
    is_blanking_text: bool = False,
    word: str = "",
) -> list[str]:
    """
    The sorted keys of the fragments whose code the pattern `says` matches. Under `is_blanking_text`, `says` matches the
    code `bare_code` gives. The fragment `name` declares stays out. `declaring` names the keys of that fragment. A
    caller that gives none has `name` match the last part of a key.

    A match of `says` holds the identifier `word`. `_naming` skips the regex search on a fragment lacking `word`. A
    `word` outside `AN_IDENTIFIER` skips no fragment.

    A file fragment holds the code of the other fragments in its file. `_gathered` gives it that code. A class holds the
    code of its methods. A match inside such an inner fragment belongs to the inner fragment. A file fragment or a class
    with no match outside those fragments stays out. A line of the fragment `name` declares counts as no match. A header
    holds such a line as a prototype. `_joined` adds the code of that prototype to the fragment.
    """
    if declaring is None:
        declaring = frozenset(one.key for one in held if one.key.rpartition(":")[2] == name)
    if AN_IDENTIFIER.fullmatch(word):
        held = [one for one in held if word in _identifiers(one.code.content)]
    lines = {
        one.key: collections.Counter(
            line.strip()
            for line in (
                bare_code(one.code.content, language_of(one.path)) if is_blanking_text else one.code.content
            ).split("\n")
            if says.search(line)
        )
        for one in held
    }
    declared = collections.Counter[str]()
    for one in held:
        if one.key in declaring:
            declared += lines[one.key]
    kept = []
    for one in held:
        if not lines[one.key] or one.key in declaring:
            continue
        outside = lines[one.key] - declared
        for other in held:
            if other.key.rpartition(":")[0] == one.key or other.key.startswith(one.key + "."):
                outside -= lines[other.key]
        if outside:
            kept.append(one.key)
    return sorted(kept)


def callers_of(held: list[Fragment], name: str, by_name: Mapping[str, set[str]] | None = None) -> list[str]:
    """
    The sorted keys of the fragments that call `name`. `by_name` comes from `keys_by_name` over `held`, and `callers_of`
    computes it for a caller that gives none.

    Public. `.claude/mcp/fetch_fragments.py` answers a question about the callers of a name this way. `claim_state` asks
    the same question again through `answered_by`, and a caller list that moved restales the fragment that read it.

    The fragment `name` declares stays out of the answer. A fragment of `gate.test_sources` stays out too.

    A call names a method through its object, as `emitter.give_back(` does. So a key or a dotted name matches by its
    last part. A method name shared by a pair of classes reaches the callers of both. The pattern matches the code
    `bare_code` gives.
    """
    tests = {os.path.relpath(path, gate.TREE) for path in gate.test_sources()}
    called = name.rpartition(":")[2].rpartition(".")[2]
    named = keys_by_name(held) if by_name is None else by_name
    declaring = frozenset(named.get(name, set()) | ({name} if name in {one.key for one in held} else set()))
    pattern = re.compile(rf"\b{re.escape(called)}\s*\(")
    return _naming([one for one in held if one.path not in tests], name, pattern, declaring, True, called)


# A name as code writes it. `named_by` and `claim_state.unpointed_reads` match a name against `AN_IDENTIFIER`.
AN_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def named_by(one: Fragment, by_name: Mapping[str, set[str]]) -> set[str]:
    """
    The keys of the fragments the code of `one` names, in the language of `one`, less the key of `one`. `by_name` comes
    from `keys_by_name`. The names come from the code `bare_code` gives. A call names its callee. A callback handed over
    as a value names its function too.

    Public. `claim_state.unpointed_reads` follows these names.
    """
    language = one.key.partition(":")[0]
    names = set(AN_IDENTIFIER.findall(bare_code(one.code.content, language)))
    return {
        key for name in names for key in by_name.get(name, ()) if key.partition(":")[0] == language and key != one.key
    }


def is_public(one: Fragment) -> bool:
    """
    Whether `one` declares a name code of another module may call. A C fragment is public where its own path or the path
    of a site starts with `include/`. A Python fragment is public where no part of its name opens with an underscore.
    Another language raises.

    Public. `claim_state.unpointed_reads` follows a name `named_by` gives into a fragment that is not public.
    """
    language = one.key.partition(":")[0]
    if language == "C":
        return any(path.startswith("include/") for path in [one.path, *(site.path for site in one.sites)])
    if language == "Python":
        return not any(part.startswith("_") for part in one.key.rpartition(":")[2].split("."))
    raise ValueError(f"no rule says whether a {language} fragment is public: {one.key}")


def mentions_of(held: list[Fragment], name: str, by_name: Mapping[str, set[str]] | None = None) -> list[str]:
    """
    The sorted keys of the fragments that mention `name`. `by_name` comes from `keys_by_name` over `held`, as for
    `callers_of`.

    Public. A struct has no callers. A reader asks this question about a struct instead.

    The match rests on the bare name. `.claude/mcp/fetch_fragments.py` refuses the name of a struct member. Structs may
    share a member name. A key or a dotted name matches by its last part, the way `callers_of` matches one. A method
    matches only where code reaches it as an attribute.
    """
    named = keys_by_name(held) if by_name is None else by_name
    declaring = frozenset(named.get(name, set()) | ({name} if name in {one.key for one in held} else set()))
    is_a_method = any("." in key.rpartition(":")[2] for key in declaring)
    word = name.rpartition(":")[2].rpartition(".")[2]
    mentioned = re.escape(word)
    pattern = re.compile(rf"\.{mentioned}\b" if is_a_method else rf"\b{mentioned}\b")
    return _naming(held, name, pattern, declaring, word=word)


# The call that answers a question, by the head of the key asking it.
_ASKED = {CALLERS: callers_of, MENTIONS: mentions_of}


def is_a_file(key: str) -> bool:
    """
    Whether `key` names a whole file. A file's key holds the language and the file's base name. A markdown file under
    `third_party/` keeps its path there. A declaration's key adds its name.

    Public. `converge_claims` hands a reader the declarations the prose names, and `is_a_file` keeps a file out of those
    declarations.
    """
    return not key.partition(":")[2].partition(":")[2]


def is_markdown(key: str) -> bool:
    """
    Whether `key` names a fragment of a markdown file. `is_a_document` tells the file from a paragraph.

    Public. `claim_state` and `prose_answer` ask this of a key.
    """
    return key.startswith("Markdown:")


def is_a_document(key: str) -> bool:
    """
    Whether `key` names the file fragment of a markdown document. A markdown key that is no document names a paragraph.

    Public. `claim_state`, `prose_answer` and `.claude/mcp/fetch_fragments.py` ask this of a key.
    """
    return is_markdown(key) and is_a_file(key)


def is_a_question(key: str) -> bool:
    """Whether the read key `key` asks a question of the tree rather than naming a fragment."""
    return key.partition(":")[0] + ":" in _ASKED


def _asks(asked: str) -> tuple[str, str]:
    """The head and the name of the question `asked`. A key that asks no question raises."""
    head, _, name = asked.partition(":")
    if head + ":" not in _ASKED:
        raise KeyError(f"`{asked}` asks no question of the tree")
    return head, name


def answered_by(held: list[Fragment], asked: str, by_name: Mapping[str, set[str]] | None = None) -> list[str]:
    """
    The keys of the fragments that answer the question `asked`. A key that asks no question raises. `by_name` goes to
    `callers_of` or `mentions_of`.
    """
    head, name = _asks(asked)
    return _ASKED[head + ":"](held, name, by_name)


def asked_in(asked: str) -> str:
    """The words a report says the question `asked` in. A key that asks no question raises."""
    head, name = _asks(asked)
    return f"the {head} of `{name}`"


def said_by(one: Fragment) -> str:
    """
    A fragment as a claims reader reads it. The prose comes first and the code it documents follows.

    Public. `converge_claims` writes a fragment into the reader's message this way. `.claude/mcp/fetch_fragments.py`
    answers a fetch this way unless it serves an outline.
    """
    said = f"### {one.key}\n\nin {one.path} at line {one.start_line}\n"
    if one.prose.content.rstrip():
        said += f"\n#### prose\n\n{one.prose.content.rstrip()}\n"
    if one.code.content.rstrip():
        said += f"\n#### code\n\n{_numbered_code(one)}\n"
    return said


def outline_of(held: list[Fragment], one: Fragment) -> str:
    """
    A long file fragment as a claims reader reads it. The answer holds the lines of code no declaration of the file
    holds. A line keeps the number `collect_fragments.said_by` gives it. The keys of the declarations follow, and a
    reader fetches a declaration under its key. A markdown document lists its headings among those keys, in the order of
    the file.

    Public. `.claude/mcp/fetch_fragments.py` answers a fetch of a long file this way.
    """
    inner = sorted(
        (other for other in held if other.key.startswith(one.key + ":")),
        key=lambda other: (other.start_line, other.key),
    )
    covered = collections.Counter(
        line.strip() for other in inner for line in other.code.content.split("\n") if line.strip()
    )
    kept = []
    for at, line in enumerate(one.code.content.rstrip().split("\n"), start=1):
        if not line.strip():
            continue
        if covered[line.strip()] > 0:
            covered[line.strip()] -= 1
            continue
        kept.append(f"{at:4d}  {line}")
    said = f"### {one.key}\n\nin {one.path} at line {one.start_line}\n"
    if one.prose.content.rstrip():
        said += f"\n#### prose\n\n{one.prose.content.rstrip()}\n"
    said += "\n#### code outside the declarations\n\n" + "\n".join(kept) + "\n"
    listed = [(other.start_line, f"- `{other.key}`") for other in inner]
    if language_of(one.path) == "Markdown":
        with open(os.path.join(gate.TREE, one.path), encoding="utf-8") as handle:
            source = handle.read()
        listed += [(at, line) for at, line in _document_lines(source) if _A_HEADING.match(line)]
    return said + "\n#### declarations\n\n" + "\n".join(line for _at, line in sorted(listed)) + "\n"


def owners_of(held: list[Fragment], one: Fragment, first: int, last: int) -> set[str]:
    """
    The keys of the declarations that hold the lines `first` to `last` of the code of the file fragment `one`. The count
    starts at 1, as `said_by` numbers the lines. The innermost declaration holding a line of the file owns it. A blank
    line needs no owner. The answer is empty where `one` holds no positions or the range falls outside the code. A line
    that is not blank and has no owner empties the answer as well.

    Public. `claim_state.refined` narrows a read of a file to the declarations a citation reaches.
    """
    said = one.code.content.split("\n")
    if not one.code.lines or not 1 <= first <= last <= len(one.code.lines):
        return set()
    inner = [(set(other.code.lines), other) for other in held if other.key.startswith(one.key + ":")]
    owners = set()
    for at in range(first - 1, last):
        if not said[at].strip():
            continue
        holding = [other for lines, other in inner if one.code.lines[at] in lines]
        if not holding:
            return set()
        owners.add(min(holding, key=lambda other: len(other.code.lines)).key)
    return owners


def _numbered_code(one: Fragment) -> str:
    """
    Write the number of a line of a fragment's code in front of that line.

    A claims reader cites a range of these numbers, and `prose_answer` reads the citation against the same numbering.
    The count starts at the fragment's own first line of code rather than at the line of the file.
    """
    lines = one.code.content.rstrip().split("\n")
    return "\n".join(f"{at:4d}  {line}" for at, line in enumerate(lines, start=1))


def located(fragment: Fragment) -> Fragment:
    """
    Read the files of `fragment` again, and answer with the fragment that holds its key. A write moves the lines below
    it. A line number read before that write points elsewhere. A key names a fragment durably.

    Public. `apply_prose` reads a fragment's sites through here. `converge_prose` reads a fragment's prose digest
    through here.

    The files are the fragment's own file and the files of its sites.
    """
    paths = sorted({fragment.path} | {site.path for site in fragment.sites})
    held = _joined([one for path in paths for one in _fragments_of(path)])
    found = [one for one in held if one.key == fragment.key]
    if len(found) != 1:
        raise ValueError(f"the tree holds {len(found)} fragment(s) under {fragment.key}")
    return found[0]


def site_lines(fragment: Fragment, site: _Site) -> tuple[int, ...]:
    """
    The line numbers `site` covers in its file. `located` reads the files again under the key of the fragment.

    Public. `apply_prose` reads those lines out of the file.
    """
    now = located(fragment)
    held = now.sites[fragment.sites.index(site)]
    return tuple(now.starts[held.path] + offset for offset in held.lines)


def prose_lines() -> dict[str, list[tuple[int, str]]]:
    """
    The prose runs of the files `gate.prose_files` names, as `{file: [(the line a run opens on, its prose)]}`. A run is
    a site of a fragment, or a literal `prose_literals` reads as prose.

    Public. `check_documents` reads the citations of the tree through here. The write-time hooks read the same sites and
    the same literals.
    """
    held = {}
    for path in gate.prose_files():
        named = str(path.relative_to(gate.TREE))
        said = [
            (one.starts[site.path] + site.lines[0], site.prose)
            for one in _fragments_of(named)
            for site in one.sites
            if site.path == named
        ]
        said += prose_literals(path.read_text(encoding="utf-8"), language_of(named) or "")
        held[named] = sorted(said)
    return held


def _digest(content: str) -> str:
    """The digest a half of a fragment is compared by."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _python_code(code: str) -> str:
    """
    The tokens of `code` with the comments, the docstrings and the non-logical line breaks left out. `code` holds
    Python. A docstring is a string token that makes up a whole statement. An indent counts as a token. The width of an
    indent does not count.
    """
    skipped = (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING)
    tokens = [token for token in tokenize.generate_tokens(io.StringIO(code).readline) if token.type not in skipped]
    opens = (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
    closes = (tokenize.NEWLINE, tokenize.ENDMARKER)
    kept = []
    for at, token in enumerate(tokens):
        before = tokens[at - 1].type if at else tokenize.NEWLINE
        after = tokens[at + 1].type if at + 1 < len(tokens) else tokenize.ENDMARKER
        if token.type == tokenize.STRING and before in opens and after in closes:
            continue
        kept.append(f"{token.type}:{'' if token.type == tokenize.INDENT else token.string}")
    return "\n".join(kept)


@functools.cache
def _code_digest(code: str, language: str | None) -> str:
    """
    The digest of `code` in `language`. `_python_code` reads Python. `_scanned` finds the comments of a `_QUOTES`
    language. `_prose_comments` names those that hold prose, and the digest leaves them out. For a `_QUOTES` language,
    the digest hashes the words without their layout. A language outside `_READ_WHOLE`, `_QUOTES` and Python raises.
    """
    if language in _READ_WHOLE:
        return _digest(code)
    if language == "Python":
        return _digest(_python_code(code))
    if language not in _QUOTES:
        raise ValueError(f"`_code_digest` knows no comments of the language {language}")
    _strings, comments = _scanned(code, _QUOTES[language], MARKERS[language])
    kept, at = [], 0
    for opens, ends in _prose_comments(comments, language):
        kept.append(code[at:opens])
        at = ends
    kept.append(code[at:])
    return _digest(" ".join(" ".join(kept).split()))


def code_digest(one: Fragment) -> str:
    """
    The digest a claim reading compares the code of `one` by. `_code_digest` says what the digest leaves out.

    Public. `claim_state`, `converge_claims` and `prose_answer` compare code this way. `fragments` gives `references`
    these digests.
    """
    return _code_digest(one.code.content, language_of(one.path))


def _named_by(prose: _Text) -> str:
    """
    The name a fragment takes where no declaration gives it one. That name is `#` followed by a prefix of the digest of
    the fragment's prose.
    """
    return f"#{prose.sha[:8]}"


def drafted(one: Fragment, prose_by_site: list[str]) -> Fragment:
    """
    `one` with `prose_by_site` as the prose of its sites. The key stays.

    Public. `converge_prose` and `converge_claims` build a rewritten fragment this way. `prose_answer` reads a claims
    answer against a draft this way.
    """
    sites = tuple(replace(site, prose=flattened(said), as_wrapped=said) for site, said in zip(one.sites, prose_by_site))
    return replace(one, prose=_a_prose(prose_by_site), sites=sites)


def key_for(key: str, digest: str) -> str:
    """
    The key a fragment takes once its prose digest is `digest`. A fragment named by its digest takes the new name. A
    fragment with a declared name keeps its key.

    Public. `converge_prose` and `converge_claims` name a fragment they rewrote.
    """
    return _A_DIGEST_NAME.sub(f"#{digest[:8]}", key)


def key_named(key: str) -> str:
    """
    `key` with no digest on its end. A digest is a number the tree decides, and prose names a fragment without it.

    Public. `record_run` writes the fragment a proposal saw this way.
    """
    return _A_DIGEST_NAME.sub("", key).rstrip()


def _text(lines: Iterable[str]) -> _Text:
    """The text `lines` write, with the empty lines around it trimmed."""
    content = "\n".join(lines).strip("\n")
    return _Text(content, _digest(content))


def _code_text(held: Iterable[tuple[int, str]]) -> _Text:
    """
    The text the pairs `(a file line, its text)` write, with the empty lines around it trimmed. `_text` trims the same
    lines, and the digest comes out the same. `lines` holds the file line of a kept pair, in the order of the pairs.
    """
    pairs = list(held)
    while pairs and not pairs[0][1]:
        pairs.pop(0)
    while pairs and not pairs[-1][1]:
        pairs.pop()
    content = "\n".join(said for _at, said in pairs)
    return _Text(content, _digest(content), tuple(at for at, _said in pairs))


# A run of blanks inside a line. Outside a fenced block, `flattened` reads such a run as a single space.
_A_RUN_OF_SPACE = re.compile(r"[ \t]+")


# A heading and a quote open a block from any position. `flattened` keeps the break of such a line.
_ALWAYS_OPENS = re.compile(r"^\s*(?:#{1,6}\s|>)")

# A bullet, a numbered item and a table row open a block. A line inside an open code span continues that span, and a
# wrapped `n - 1` then opens no block. `prose_rules` refuses a spaced hyphen, star or plus outside a code span.
_OPENS_A_BLOCK = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|\|)")


def does_open_a_block(line: str) -> bool:
    """
    Whether `line` opens on a bullet, a numbered item or a table row.

    Public. `check_hooks` indents the wrapped lines of a bullet or a numbered item two spaces past the indent of the
    first line.
    """
    return bool(_OPENS_A_BLOCK.match(line))


# The marker around a block a writer laid out by hand. `flattened` joins no line inside such a block.
_FENCE = "```"


def flattened(content: str) -> str:
    """
    `content` with the line wrapping taken out. A line continuing the line above joins it.

    `make reformat` rewraps a comment to the column limit. `make reformat-py` and `make reformat-md` do the rewrap. A
    digest of the flattened text holds still across that rewrap. A checker reads this text rather than the text as the
    file holds it.

    A blank line keeps its break. So does a line `_ALWAYS_OPENS` matches. So does a line `_OPENS_A_BLOCK` matches
    outside an open code span. A line inside a fenced block keeps the layout the writer gave it. The indentation comes
    off. Prose moved to another nesting level then keeps its digest. A bullet at depth `d` opens on `2 * d` spaces. A
    nested bullet keeps that depth across a rewrap that changes its indent.

    Public. `checkers` reads prose through this. `prose_digest` takes the digest of the flattened text.
    """
    held: list[str] = []
    is_fenced = False
    bullets: list[int] = []
    for line in content.split("\n"):
        if line.lstrip().startswith(_FENCE):
            is_fenced = not is_fenced
            held.append(line.rstrip())
            continue
        if is_fenced:
            held.append(line.rstrip())
            continue
        said = _A_RUN_OF_SPACE.sub(" ", line.strip())
        if not said:
            if held and held[-1]:
                held.append("")
            continue
        is_in_a_code_span = bool(held) and held[-1].count("`") % 2 == 1
        opens = _ALWAYS_OPENS.match(line) or (_OPENS_A_BLOCK.match(line) and not is_in_a_code_span)
        if opens or not held or not held[-1]:
            depth = 0
            if opens and _OPENS_A_BLOCK.match(line):
                indent = len(line) - len(line.lstrip())
                while bullets and bullets[-1] > indent:
                    bullets.pop()
                if not bullets or bullets[-1] < indent:
                    bullets.append(indent)
                depth = len(bullets) - 1
            else:
                bullets = []
            held.append("  " * depth + said)
            continue
        held[-1] = f"{held[-1]} {said}"
    return "\n".join(held).strip("\n")


def prose_digest(prose_by_site: Iterable[str]) -> str:
    """
    The digest of a fragment's prose, given its prose per site. `flattened` takes the wrapping out first.

    Public. The ledger and the settling queues in `generator/settling_state.py` key prose by this digest.
    `settling_state._A_DIGEST` matches it. `converge_prose` computes the digest of the prose it writes here.
    """
    return _digest(flattened("\n\n".join(prose_by_site)))


def _a_prose(prose_by_site: Iterable[str]) -> _Text:
    """The prose of the sites `prose_by_site`, as `flattened` gives it. A site is a paragraph of its own."""
    content = flattened("\n\n".join(prose_by_site))
    return _Text(content, _digest(content))


def language_of(path: str) -> str | None:
    """
    The language of `path`, or None where `_LANGUAGES` names no language for it. `_fragments_of_source` takes no
    fragment from a file of no language.
    """
    named = os.path.basename(path)
    for forms, language in _LANGUAGES:
        if path.endswith(forms) or named in forms:
            return language
    return None


def _keyed(language: str, path: str, name: str) -> str:
    """The key a fragment has. A file fragment writes no third part."""
    named = os.path.basename(path)
    return f"{language}:{named}" if not name else f"{language}:{named}:{name}"


def cited_by(prose: str, language: str) -> set[str]:
    """
    The names `prose` cites. Prose in any language cites a name in backticks. A `make` command in backticks cites its
    target. C prose also cites a public name bare.

    Public. `prose_answer` reads the names a claims reader cites under `looked_for`. `public_citations` reads the names
    the prose of a public header cites.
    """
    cited = set(_A_CITED_NAME.findall(prose)) | set(_A_CITED_TARGET.findall(prose))
    if language == "C":
        cited.update(_A_CITED_C_NAME.findall(prose))
    return cited


def _indent_of(line: str) -> str:
    """The whitespace `line` opens with."""
    return line[: len(line) - len(line.lstrip())]


def _undented(said: str) -> str:
    """
    The text a comment says with the space after its marker off.

    A deeper indent stays. A bullet indents the lines under it. The `(wrap)` bullet of `grammar/yeast-spec-1.2.yaml`
    does that. Flattening those lines would lose the bullet.
    """
    return said.removeprefix(" ").rstrip()


def _unmarked(said: str, marker: str) -> str:
    """
    The text a comment line says with its marker off.

    A doxygen C line comment opens on `///` or `//!`, and a member's comment on `//<`. Those go too. A `#` marker keeps
    what follows it. An interpreter line then still writes its `!`.
    """
    body = said[len(marker) :]
    return _undented(body.lstrip("/!<") if marker == "//" else body)


def _as_line_comments(found: re.Match[str]) -> str:
    """
    The answer holds the prose between the markers of the block comment `found` matched. A line of that prose loses its
    indent and a leading `*`. The line then opens with `//`.
    """
    return "\n".join("//" + line.lstrip().removeprefix("*") for line in found.group(1).split("\n"))


def _line_commented(source: str) -> str:
    """
    `source` with its block comments written as line comments.

    The answer holds the lines `source` holds. A fragment's line numbers stay put. `_read_only` reads a C file this way.
    """
    return _A_BLOCK_COMMENT.sub(_as_line_comments, source)


def _comments(source: str, language: str) -> dict[int, str]:
    """
    `{line: what the comment on it says}` for the lines `standalone_comments` names.

    A line continuing a trailing comment goes to `_marked_trailing` instead. Reading such a line here would say its
    words twice, and would report the tail of a sentence as a whole one.
    """
    marker = MARKERS[language]
    wrapped = _wrapped_trailing(source, language)
    lines = source.split("\n")
    return {
        at: _unmarked(lines[at - 1].strip(), marker)
        for at in standalone_comments(source, language)
        if at not in wrapped
    }


def python_comments(source: str) -> dict[int, tuple[str, str]]:
    """
    `{line: (the text in front of the comment, the comment with its marker)}`, over the comments of the Python `source`.
    The text in front of a comment on a line of its own is blank. A comment is a `tokenize.COMMENT` token, as
    https://docs.python.org/3/library/tokenize.html says.

    Public. `check_conventions` reads the comments of a Python file here. `scripts/wrap_long_comments.py` reaches them
    through `standalone_comments` and `trailing_comments`.
    """
    lines = source.split("\n")
    return {
        token.start[0]: (lines[token.start[0] - 1][: token.start[1]], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }


def standalone_comments(source: str, language: str | None) -> dict[int, int]:
    """
    `{line: the column its comment opens at}`, over the comments of `source` that take a line of their own.

    `python_comments` reads a Python file. A `Makefile` writes such a comment at the left margin. A recipe opens on a
    tab, and a `#` there belongs to the shell, as https://www.gnu.org/software/make/manual/html_node/Recipe-Syntax.html
    says. `_shell_comments` reads a shell script, and `_js_comments` reads a JS file. Another language of `MARKERS`
    opens a comment line on its marker. JSON writes no comment, and a JSON file raises.

    Public. `scripts/wrap_long_comments.py` reflows the comments this names.
    """
    lines = source.split("\n")
    if language == "Python":
        return {at: len(before) for at, (before, _said) in python_comments(source).items() if not before.strip()}
    if language == "Make":
        return {at: 0 for at, line in enumerate(lines, 1) if line.startswith("#")}
    if language == "Shell":
        return _shell_comments(lines)
    if language == "JS":
        return _js_comments(source)
    marker = MARKERS.get(language or "")
    if not marker:
        raise ValueError(f"`standalone_comments` knows no comments of the language {language}")
    return {at: len(line) - len(line.lstrip()) for at, line in enumerate(lines, 1) if line.lstrip().startswith(marker)}


def filled(text: str, first: str, then: str | None = None, width: int = gate.WIDEST) -> list[str]:
    """
    `text` filled to `width` by `textwrap.wrap`. The first line opens on `first`. A later line opens on `then`, or on
    `first` where the caller gives no `then`. A word longer than the width stays whole, and a hyphen breaks no word.

    Public. A module that wraps text to the width calls this.
    """
    return textwrap.wrap(
        text,
        width,
        initial_indent=first,
        subsequent_indent=first if then is None else then,
        break_long_words=False,
        break_on_hyphens=False,
    )


def comment_body(line: str, marker: str) -> str:
    """
    The text of the comment `line` after `marker` and an optional space. A reflow keeps an indent beyond that space.

    Public. `scripts/wrap_long_comments.py` reads a comment it hoists here.
    """
    after_marker = line.lstrip()[len(marker) :]
    return after_marker[1:] if after_marker.startswith(" ") else after_marker


# A comment line opening on a pair or more of marks from `-=*|+~^`, then whitespace or the end. `**bold**` opens no
# ruler.
_A_RULER = re.compile(r"^[-=*|+~^]{2,}(\s|$)")


def _is_laid_out(paragraph: list[str], marker: str) -> bool:
    """Whether a comment paragraph has a layout a greedy fill would wreck."""
    for line in paragraph:
        body = comment_body(line, marker)
        if body.startswith(" ") or _A_RULER.match(body.strip()):
            return True
        if body.strip().startswith(("- ", "* ")):
            return True
    return False


def comment_reflowed(block: list[str], indent: int, marker: str) -> list[str]:
    """
    The comment `block` reflowed. The prefix is the indent, `marker` and a space. `filled` fills a paragraph of prose to
    the width the prefix leaves.

    An empty comment line splits a block into paragraphs. A directive splits a block the same way, and the directive
    stays verbatim. A paragraph `_is_laid_out` names stays verbatim too.

    Public. `scripts/wrap_long_comments.py` reflows a comment block here, and `apply_prose` writes a comment the same
    way.
    """
    prefix = " " * indent + marker + " "
    out: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        if _is_laid_out(paragraph, marker):
            out.extend(paragraph)
        else:
            out.extend(filled(" ".join(comment_body(line, marker).strip() for line in paragraph), prefix))
        paragraph.clear()

    for line in block:
        body = comment_body(line, marker).strip()
        if body == "":
            flush()
            out.append(" " * indent + marker)
        elif gate.TOOL_DIRECTIVE.match(body):
            flush()
            out.append(line)
        else:
            paragraph.append(line)
    flush()
    return out


def trailing_comments(source: str, language: str | None) -> dict[int, int]:
    """
    `{line: the column its comment's marker opens at}`, over the comments of `source` that follow code on their line.

    `python_comments` reads a Python file. A `Makefile` line opens a comment at its first `#` that no backslash escapes,
    as https://www.gnu.org/software/make/manual/html_node/Makefile-Contents.html says. A recipe opens on a tab, and a
    `#` there belongs to the shell. `_scanned` reads another language, and a marker inside a string opens no comment. A
    language with no marker in `MARKERS` raises.

    Public. `scripts/wrap_long_comments.py` hoists such a comment from a Python or `Makefile` line past the width.
    `marker_lines` reads a mark in such a comment.
    """
    if language == "Python":
        return {at: len(before) for at, (before, _said) in python_comments(source).items() if before.strip()}
    if language == "Make":
        found = ((at, line, _AN_UNESCAPED_HASH.search(line)) for at, line in enumerate(source.split("\n"), 1))
        return {
            at: mark.start()
            for at, line, mark in found
            if mark is not None and not line.startswith("\t") and line[: mark.start()].strip()
        }
    marker = MARKERS.get(language or "")
    if not marker:
        raise ValueError(f"`trailing_comments` knows no comments of the language {language}")
    starts = [0] + [at + 1 for at, char in enumerate(source) if char == "\n"]
    lines = source.split("\n")
    held = {}
    quotes = _SETTING_QUOTES if language == "Conf" else _QUOTES[language or ""]
    for opens, _ends in _scanned(source, quotes, marker)[1]:
        at = bisect.bisect_right(starts, opens)
        column = opens - starts[at - 1]
        line = lines[at - 1]
        if source.startswith(marker, opens) and line[:column].strip():
            held[at] = column
    return held


def _shell_comments(lines: list[str]) -> dict[int, int]:
    """
    `{line: the column its comment opens at}`, over a shell script's comments on lines of their own.

    Such a comment opens on a `#` after its indentation. The shebang is no comment. A `#` in the body of a heredoc is
    text the heredoc hands over, and this skips the body.
    """
    found = {}
    ends = ""
    for at, line in enumerate(lines, 1):
        if ends:
            if line.strip() == ends:
                ends = ""
            continue
        opened = re.search(_A_HEREDOC_OPENER, line)
        if opened is not None:
            ends = opened.group(2)
        if line.lstrip().startswith("#") and not (at == 1 and line.startswith("#!")):
            found[at] = len(line) - len(line.lstrip())
    return found


def _js_comments(source: str) -> dict[int, int]:
    """
    `{line: the column its comment opens at}`, over a JS file's `//` comments on lines of their own.

    A scan follows the quoted strings, the template literals and the `${` expressions a template holds. A `//` inside a
    string or the literal text of a template opens no comment. A block comment takes no line here. The scan reads no
    regular expression literal.
    """
    found: dict[int, int] = {}
    modes = ["code"]  # The innermost mode sits last.
    braces: list[int] = []  # A scan counts the braces an expression of a template has open.
    row, line_start, at = 1, 0, 0
    while at < len(source):
        mark, mode = source[at], modes[-1]
        if mark == "\n":
            row, line_start = row + 1, at + 1
            if mode == "line":
                modes.pop()
            at += 1
        elif mode == "line":
            at += 1
        elif mode == "block":
            if source.startswith("*/", at):
                modes.pop()
                at += 1
            at += 1
        elif mode in ("'", '"', "`") and mark == "\\":
            at += 2
            if source[at - 1] == "\n":
                row, line_start = row + 1, at
        elif mode in ("'", '"'):
            if mark == mode:
                modes.pop()
            at += 1
        elif mode == "`":
            if mark == "`":
                modes.pop()
            elif source.startswith("${", at):
                modes.append("${")
                braces.append(0)
                at += 1
            at += 1
        elif source.startswith("//", at):
            if not source[line_start:at].strip():
                found[row] = at - line_start
            modes.append("line")
            at += 2
        elif source.startswith("/*", at):
            modes.append("block")
            at += 2
        else:
            if mark in ("'", '"', "`"):
                modes.append(mark)
            elif mode == "${" and mark == "{":
                braces[-1] += 1
            elif mode == "${" and mark == "}":
                if braces[-1]:
                    braces[-1] -= 1
                else:
                    modes.pop()
                    braces.pop()
            at += 1
    return found


def _python_comments(source: str) -> dict[int, str]:
    """`{line: what the comment on it says}`, over the Python comments on lines of their own."""
    return {
        at: _undented(said.lstrip("#")) for at, (before, said) in python_comments(source).items() if not before.strip()
    }


def _python_trailing(source: str) -> dict[int, tuple[str, str]]:
    """`{line: (its code, what the comment sharing it says)}`, over the Python comments beside code."""
    return {
        at: (before.rstrip(), _undented(said.lstrip("#")))
        for at, (before, said) in python_comments(source).items()
        if before.strip()
    }


def _wrapped_trailing(source: str, language: str) -> set[int]:
    """
    The lines that continue a comment sharing a line with code.

    Such a line holds a comment and no code. Its marker sits at the column of the marker above. clang-format writes this
    wrap, as https://clang.llvm.org/docs/ClangFormatStyleOptions.html#reflowcomments says.
    """
    trailing = trailing_comments(source, language)
    alone = standalone_comments(source, language)
    held: set[int] = set()
    column = None
    for at in range(1, source.count("\n") + 2):
        if at in trailing:
            column = trailing[at]
        elif at in alone and column is not None and alone[at] == column:
            held.add(at)
        else:
            column = None
    return held


def _marked_trailing(source: str, language: str) -> dict[int, tuple[str, str, int]]:
    """
    `{line: (its code, what the comment sharing it says, the line the comment ends on)}`, over the comments
    `trailing_comments` names.

    A wrapped line joins the comment above it. The C and make callers drop what sits on a branch line. A comment on an
    `#endif` names the branch that directive closes rather than saying anything about the branch. The `#endif` of
    `src/wire.h` names `YEAST_WIRE_H`.
    """
    marker = MARKERS[language]
    trailing = trailing_comments(source, language)
    wrapped = _wrapped_trailing(source, language)
    held: dict[int, tuple[str, str, int]] = {}
    opened = None
    for at, line in enumerate(source.split("\n"), start=1):
        if at in wrapped and opened is not None:
            code, was, _last = held[opened]
            held[opened] = (code, f"{was} {_unmarked(line.strip(), marker)}".strip(), at)
        elif at in trailing:
            column = trailing[at]
            held[at] = (line[:column].rstrip(), _unmarked(line[column:], marker), at)
            opened = at
        else:
            opened = None
    return held


def _blocks(alone: dict[int, str]) -> list[tuple[int, int, str]]:
    """
    The runs of consecutive comment lines, as `(the line it opens on, the line it ends on, its text)`.

    A comment line saying nothing adds no text. The next comment line joins the last run. A gap between a pair of
    comment lines starts a new run. A run ends at the interpreter line or at the licence tag.
    """
    held: list[tuple[int, int, str]] = []
    opens = -2
    for at in sorted(alone):
        if _AN_OPENING_LINE.match(alone[at]):
            opens = -2
            continue
        if not alone[at]:
            opens = at
            continue
        if held and at == opens + 1:
            held[-1] = (held[-1][0], at, held[-1][2] + "\n" + alone[at])
        else:
            held.append((at, at, alone[at]))
        opens = at
    return held


def _opens_above(blocks: list[tuple[int, int, str]], line: int, branches: set[int]) -> int:
    """
    The line a thing's prose opens on. That is where the comment block directly above it opens.

    A line in `branches` between the block and the thing does not part the block from the thing.
    """
    at = line - 1
    while at in branches:
        at -= 1
    for first, last, _said in blocks:
        if last == at:
            return first
    return line


def _first_line_of(split: "_Split") -> int:
    """The line a file's description opens on. The licence tag and the interpreter line come above that line."""
    at = 1
    while at <= len(split.lines) and _SITS_ABOVE_IT.match(split.lines[at - 1]):
        at += 1
    return at


def _branch_lines(source: str, marker: str) -> set[int]:
    """The lines choosing between branches, in C or in make."""
    return {
        at
        for at, line in enumerate(source.split("\n"), start=1)
        if _ONLY_A_BRANCH.match(line.partition(marker)[0].strip())
    }


# The most code lines a note's site holds.
_MOST_CODE_BELOW = 12


class _Split:
    """The text a file's lines say, and which of them are prose."""

    def __init__(self, source: str, is_prose: set[int]) -> None:
        self.lines = source.split("\n")
        self.is_prose = is_prose
        self.said: dict[int, str] = {}
        self.shared: dict[int, tuple[str, str]] = {}
        self.wrapped: dict[int, int] = {}  # the line a shared comment opens on, by a line that continues it.
        self.in_docstrings: set[int] = set()  # the docstring lines. A site holds docstring lines or comment lines.
        self.in_bodies: set[int] = set()  # the lines of the function bodies. A comment there is a note.

    def says(self, at: int, said: str) -> None:
        """Record that the prose on line `at` reads as `said` once its marker is off."""
        self.said[at] = said

    def shares(self, at: int, code: str, said: str, last: int | None = None) -> None:
        """
        Record that line `at` holds `code` and a comment saying `said`.

        A comment wrapping onto the lines below runs to `last`. `apply_prose._wrapped` writes the whole comment back
        through the run.
        """
        self.shared[at] = (code, said)
        for line in range(at + 1, (last or at) + 1):
            self.wrapped[line] = at

    def role(self, lines: tuple[int, ...]) -> str:
        """
        The role of the site on `lines`. A docstring line opens a `docstring` site. A comment inside a function body
        opens a `note`. Another comment opens a `comment`.
        """
        if lines[0] in self.in_docstrings:
            return "docstring"
        return "note" if lines[0] in self.in_bodies else "comment"

    def code_below(self, lines: tuple[int, ...]) -> str:
        """
        The code a note sits on. A note sharing a line with code sits on that code. The note on `lines` otherwise sits
        on the code under its last line. A blank line or a comment ends that code. `_MOST_CODE_BELOW` caps it.
        """
        if lines[0] in self.shared:
            return self.shared[lines[0]][0].strip()
        held: list[str] = []
        for line in self.lines[lines[-1] : lines[-1] + _MOST_CODE_BELOW]:
            if not line.strip() or line.strip().startswith(("#", "//")):
                break
            held.append(line)
        return "\n".join(held)

    def sites(self, owned: Iterable[int]) -> list[tuple[tuple[int, ...], str]]:
        """
        The sites the lines `owned` hold. A site reads as `(the lines, what they say)`.

        A gap in the line numbers ends a site. A docstring and a comment touching that docstring come back apart. A
        comment sharing a line with code becomes a site of its own. A struct's own comment and the comments on its
        fields come back apart.
        """
        held: list[tuple[list[int], list[str]]] = []
        opens = -2
        for at in sorted(owned):
            if at in self.wrapped:
                if held and held[-1][0][-1] == at - 1:
                    held[-1][0].append(at)
                opens = at
                continue
            if at in self.shared:
                held.append(([at], [self.shared[at][1]]))
            elif at in self.is_prose:
                said = self.said.get(at, self.lines[at - 1].strip())
                is_alike = (at in self.in_docstrings) == (at - 1 in self.in_docstrings)
                if held and at == opens + 1 and at - 1 not in self.shared and is_alike:
                    held[-1][0].append(at)
                    held[-1][1].append(said)
                else:
                    held.append(([at], [said]))
            else:
                continue
            opens = at
        return [(tuple(lines), "\n".join(said)) for lines, said in held]

    def code(self, owned: Iterable[int]) -> _Text:
        """The code of the lines `owned`, in the order the file writes them."""
        held = []
        for at in sorted(owned):
            if at in self.wrapped or (at in self.is_prose and at not in self.shared):
                continue
            held.append(self.shared[at][0] if at in self.shared else self.lines[at - 1])
        return _text(held)


# The kinds a registration links, as `(what is registered, what registers it)`.
_A_REGISTRATION = (("function", "binding"), ("target", "target"))

# The directives choosing between branches, in C and in make. A comment above such a directive documents what the
# branches declare.
_ONLY_A_BRANCH = re.compile(r"^(#\s*(if|ifdef|ifndef|else|elif|endif)\b|(ifeq|ifneq|ifdef|ifndef|else|endif)\b)")


def _has_prose(split: "_Split", first: int, last: int) -> bool:
    """Whether any line from `first` through `last` is prose or holds a comment beside code."""
    return any(at in split.is_prose or at in split.shared for at in range(first, last + 1))


def _grouped(
    extents: list[tuple[str, str, int, int]], split: "_Split", branches: set[int]
) -> list[tuple[str, str, int, int]]:
    """
    A binding that registers the function above it joins that function in a single extent. Both names name that extent.

    The grouping leaves a declaration with prose out of the extent above it.

    A line choosing between branches sits between a registration and its function without parting the pair.
    """
    held: list[tuple[str, str, int, int]] = []
    for one in sorted(extents, key=lambda held_one: held_one[2]):
        at = _closes_above(held, one[2])
        if at is not None and _does_absorb(held[at], one, split, branches):
            above = held[at]
            held[at] = (above[0], f"{above[1]}, {one[1]}", above[2], one[3])
        else:
            held.append(one)
    return held


def _closes_above(held: list[tuple[str, str, int, int]], first: int) -> int | None:
    """
    The place in `held` of the extent closing last before `first`.

    `_grouped` builds this list sorted by the line an extent opens on. A function holding a nested extent is followed by
    that nested extent, and the last extent appended is then the inner rather than the outer.
    """
    ended = [at for at, one in enumerate(held) if one[3] < first]
    return max(ended, key=lambda at: held[at][3]) if ended else None


def _does_sit_under(last: int, first: int, split: "_Split", branches: set[int]) -> bool:
    """
    Whether the declaration opening on `first` sits under the declaration closing on `last`, over blank lines and lines
    in `branches` as well.
    """
    return first > last and all(at in branches or not split.lines[at - 1].strip() for at in range(last + 1, first))


def _does_register(above: tuple[str, str, int, int], one: tuple[str, str, int, int], split: "_Split") -> bool:
    """
    Whether the declaration `one` registers the declaration `above` it.

    `_NO_CHOICE_OF_CHOICES = _Invariant("no-choice-of-choices", _no_choice_of_choices)` is such a binding. The binding
    names the function and writes no value of its own. A docstring on that function already says as much. A make target
    whose single prerequisite is the stamp rule over it is the same shape.

    The naming runs either way. A function whose body reads the binding under it is the wrapper over that binding. The
    docstring of that function documents the pair.
    """
    if (above[0], one[0]) not in _A_REGISTRATION:
        return False
    return _does_name(split, one, above[1]) or _does_name(split, above, one[1])


def _does_name(split: "_Split", extent: tuple[str, str, int, int], named: str) -> bool:
    """Whether the lines of `extent` write `named`. Looks for a dotted Python name by its last part."""
    said = re.escape(named if "/" in named else named.rpartition(".")[2])
    return re.search(rf"(?<![\w./-]){said}(?![\w./-])", "\n".join(split.lines[extent[2] - 1 : extent[3]])) is not None


def _does_absorb(
    above: tuple[str, str, int, int], one: tuple[str, str, int, int], split: "_Split", branches: set[int]
) -> bool:
    """Whether the extent `above` takes in the declaration `one` under it."""
    if _has_prose(split, one[2], one[3]):
        return False
    return _does_register(above, one, split) and _does_sit_under(above[3], one[2], split, branches)


def _own(first: int, last: int, extents: list[tuple[str, str, int, int]]) -> set[int]:
    """The lines an extent owns, less the lines of the extents inside it."""
    owned = set(range(first, last + 1))
    for _kind, _name, opens, closes in extents:
        if (opens, closes) != (first, last) and first <= opens and closes <= last:
            owned -= set(range(opens, closes + 1))
    return owned


def _a_fragment(
    language: str, path: str, kind: str, name: str | None, first: int, split: _Split, owned: Iterable[int]
) -> Fragment:
    """
    Build a fragment from the lines an extent owns. The fragment holds a pair of halves. A fragment with no name takes a
    name drawn from the digest of its prose.
    """
    owned = sorted(owned)
    sites = tuple(
        _Site(
            path,
            tuple(at - first for at in lines),
            flattened(said),
            said,
            split.role(lines),
            split.code_below(lines) if split.role(lines) == "note" else "",
            split.shared[lines[0]][0] if lines[0] in split.shared else "",
        )
        for lines, said in split.sites(owned)
    )
    prose, code = _a_prose([site.as_wrapped for site in sites]), split.code(owned)
    named = _named_by(prose) if name is None else name
    return Fragment(_keyed(language, path, named), path, kind, {path: first}, prose, code, sites)


def _documented(split: "_Split", owned: Iterable[int], extent: Iterable[int]) -> _Text:
    """
    The code a fragment's prose documents. The extent of the declaration comes back, less that fragment's own prose.

    A class holds the methods under it. Such an inner declaration comes back whole, prose and code alike. A line the
    fragment's prose shares with code keeps that code.
    """
    mine = set(owned)
    described = {at for at in mine if at in split.is_prose or at in split.wrapped}
    return _code_text(
        (at, split.shared[at][0] if at in mine and at in split.shared else split.lines[at - 1])
        for at in sorted(extent)
        if at not in described
    )


def _code_of(split: "_Split", extent: Iterable[int]) -> _Text:
    """
    The code of a file fragment. The lines of `extent` come back, less the prose in them. A line that prose shares with
    code keeps that code. An edit to the prose then moves no code digest of the file.
    """
    return _code_text(
        (at, split.shared[at][0] if at in split.shared else split.lines[at - 1])
        for at in sorted(extent)
        if at not in split.is_prose and at not in split.wrapped
    )


def _gathered(
    language: str,
    path: str,
    split: _Split,
    extents: list[tuple[str, str, int, int]],
    blocks: list[tuple[int, int, str]],
    branches: set[int],
    bodies: set[int],
) -> list[Fragment]:
    """
    Gather a fragment per name and a fragment per unattached comment of a file that declares things. The file itself
    makes a fragment too.

    An `#if` may declare a name under a branch and declare it again under the other. A single fragment holds both
    declarations. Python has no such branch. `_colliding_keys` reports a module binding a name twice.

    The comment block at the top of the file describes the file. `a-file-description-comes-first` says so. The rest of
    the file is the code that block documents. Lower down, a comment block that no extent holds documents no
    declaration. Such a block attaches to nothing, and it is a fragment with no code. An unattached comment is named by
    the digest of its prose. A rewrite of that prose gives a new name, and the old name leaves the tree.

    A block holding a section heading makes no fragment. `prose_rules.is_a_heading` decides that. The file fragment
    holds a heading in neither half.

    A block opening inside a function body makes no unattached comment, whether or not an extent encloses it.
    """
    extents = _grouped(extents, split, branches)
    taken = {at for _kind, _name, first, last in extents for at in range(first, last + 1)}
    loose = [one for one in blocks if one[0] not in taken and one[0] not in bodies]
    describes_the_file = [one for one in loose[:1] if one[0] == _first_line_of(split)]
    below = loose[len(describes_the_file) :]
    unattached = [one for one in below if not prose_rules.is_a_heading(one[2])]
    headings = [one for one in below if prose_rules.is_a_heading(one[2])]
    owned_by: dict[tuple[str, str, int], set[int]] = {}
    spanned: dict[tuple[str, str, int], set[int]] = {}
    for kind, name, first, last in extents:
        under = 0 if language in ("C", "Make") else first
        owned_by.setdefault((kind, name, under), set()).update(_own(first, last, extents))
        spanned.setdefault((kind, name, under), set()).update(range(first, last + 1))
    held = []
    for at, owned in owned_by.items():
        one = _a_fragment(language, path, at[0], at[1], min(owned), split, owned)
        one.code = _documented(split, owned, spanned[at])
        held.append(one)
    for first, last, _said in unattached:
        held.append(_a_fragment(language, path, "unattached", None, first, split, range(first, last + 1)))
    aside = {at for one in unattached + headings for at in range(one[0], one[1] + 1)}
    left = set(range(1, len(split.lines) + 1)) - taken - aside
    kind = "module" if language == "Python" else "file"
    describes = _a_fragment(language, path, kind, "", 1, split, left)
    describes.code = _code_of(split, range(1, len(split.lines) + 1))
    held.append(describes)
    return held


def _overridden(tree: ast.Module) -> set[str]:
    """
    Name the methods and nested classes that a class of this module declares with a docstring.

    A method of that name with no docstring is an override. The docstring on the overridden method is the override's
    contract. `pylint` reads an override the same way and asks the override for no docstring.
    https://github.com/pylint-dev/pylint/blob/main/pylint/checkers/base/docstring_checker.py passes `report_missing=not
    overridden` for such an override.
    """
    held: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if gate.is_a_definition(child) and ast.get_docstring(child):
                held.add(child.name)
    return held


def _python_extents(tree: ast.Module, blocks: list[tuple[int, int, str]]) -> list[tuple[str, str, int, int]]:
    """
    The declarations a Python module makes, as `(kind, dotted name, first line, last line)`.

    A definition's extent opens at the comment block above it. `__init__` is not a fragment of its own. Its lines stay
    with the class. A field's declaration and its comments then reach the class rather than the method.

    An override with no docstring is not a fragment either. The docstring on the overridden method is its contract. A
    bare `main` at module level is not a fragment, and the module's docstring describes it. `.pylintrc` writes `main`
    into its docstring pattern.

    A definition inside a function body is not a fragment. Its lines stay with the function.
    """
    held: list[tuple[str, str, int, int]] = []
    overrides = _overridden(tree)

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if not gate.is_a_definition(child) or child.name == "__init__":
                continue
            if not ast.get_docstring(child) and child.name == "main" and isinstance(node, ast.Module):
                continue
            if isinstance(node, ast.ClassDef) and child.name in overrides and not ast.get_docstring(child):
                continue
            name = prefix + child.name
            opens = min([child.lineno] + [one.lineno for one in child.decorator_list])
            kind = "class" if isinstance(child, ast.ClassDef) else "function"
            held.append((kind, name, _opens_above(blocks, opens, set()), child.end_lineno or child.lineno))
            if isinstance(child, ast.ClassDef):
                walk(child, name + ".")

    walk(tree, "")
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and gate.defined_names(node):
            name = ", ".join(gate.defined_names(node))
            held.append(("binding", name, _opens_above(blocks, node.lineno, set()), node.end_lineno or node.lineno))
    return held


def _python_bodies(tree: ast.Module) -> set[int]:
    """
    `_python_bodies` returns the numbers of the lines that the functions and methods of a module span. A comment block
    opening on such a line makes no unattached comment.
    """
    held: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            held.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return held


def _docstring_extents(tree: ast.Module) -> list[tuple[int, int]]:
    """The lines a docstring opens and closes on, over the module and what it declares."""
    held: list[tuple[int, int]] = []
    for node in [tree, *ast.walk(tree)]:
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            held.append((first.lineno, first.end_lineno or first.lineno))
    return held


def _python_fragments(path: str, source: str) -> list[Fragment]:
    """
    The fragments of a Python file. A module makes a fragment. A class, a function and a module-level binding make a
    fragment too, save the exceptions `_python_extents` lists.

    A loose comment block makes a fragment of its own. A block holding a section heading makes none. The block opening
    the file describes the module and makes none. A block inside a function body is prose of the fragment holding that
    body. `a-comment-inside-a-body-is-a-note` holds such a block to the length `MOST_COMMENT_LINES` gives.
    """
    tree = ast.parse(source)
    lines = source.split("\n")
    bodies = _python_bodies(tree)
    comments = {
        at: said
        for at, said in _python_comments(source).items()
        if at not in bodies or not gate.TOOL_DIRECTIVE.match(said)
    }
    blocks = _blocks(comments)
    strings = _docstring_extents(tree)
    is_prose = {at for opens, closes in strings for at in range(opens, closes + 1)} | {
        at for first, last, _said in blocks for at in range(first, last + 1)
    }
    split = _Split(source, is_prose)
    split.in_bodies.update(bodies)
    for at, said in comments.items():
        split.says(at, said)
    for at, (code, said) in _python_trailing(source).items():
        if not gate.TOOL_DIRECTIVE.match(said):
            split.shares(at, code, said)
    for opens, closes in strings:
        split.in_docstrings.update(range(opens, closes + 1))
        indent = _indent_of(lines[opens - 1])
        for at in range(opens, closes + 1):
            said = lines[at - 1].removeprefix(indent).rstrip()
            said = _OPENS_A_STRING.sub("", said, count=1) if at == opens else said
            split.says(at, _CLOSES_A_STRING.sub("", said) if at == closes else said)
    return _gathered("Python", path, split, _python_extents(tree, blocks), blocks, set(), bodies)


# The `struct`, `enum` or `union` keyword before a declaration's first brace. That keyword marks the brace as opening a
# type's body rather than a function body.
_A_TYPE_BODY = re.compile(r"\b(struct|enum|union)\b")
# A word followed by an opening bracket, a semicolon or an equals sign.
_A_DECLARED_NAME = re.compile(r"(\w+)\s*[({;=\[]")

# The name a member of a C type has, in the body that declares it.
_A_MEMBER_NAME = re.compile(r"\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*[;,=]")

# The close of a brace block, and the name a typedef writes after that close. A close that writes no name matches too,
# and its group then holds nothing.
_A_CLOSING_NAME = re.compile(r"^\}\s*(\w+)?")
# A preprocessor line other than a `#define` written with no space after its `#`. Includes, branches and `# define`
# match it. Such a line declares nothing.
_A_DIRECTIVE = re.compile(r"^#\s*(?!define\b)")

# The line opening an include guard. The `#define` under it repeats the name and declares nothing.
_AN_INCLUDE_GUARD = re.compile(r"^#\s*ifndef\s+(\w+)\s*$")


def _c_declared(said: str, closing: str) -> tuple[str, str]:
    """
    The kind and the name a C declaration writes. A word written after a closing brace names a type. Without that word,
    the function searches the declaration for a name.
    """
    if said.strip().startswith("#define"):
        return "define", said.split()[1].partition("(")[0]
    named = _A_CLOSING_NAME.match(closing)
    if _A_TYPE_BODY.search(said.partition("{")[0]) or "typedef" in said:
        if named and named.group(1):
            return "type", named.group(1)
        found = _A_DECLARED_NAME.search(said)
        return "type", found.group(1) if found else closing
    found = _A_DECLARED_NAME.search(said)
    if not found:
        return "binding", closing
    kind = "function" if f"{found.group(1)}(" in said.replace(" (", "(") else "binding"
    return kind, found.group(1)


def _c_extents(source: str, marker: str) -> list[tuple[str, str, int, int]]:
    """
    The declarations a C file makes at file scope, as `(kind, name, first line, last line)`.

    A brace block runs to the line where its braces balance, once that line opens with `}` or ends with `;`. A `#define`
    holding no brace runs to its last continued line. Anything else runs to the semicolon that ends it.
    """
    lines = _at_file_scope([line.partition(marker)[0] for line in source.split("\n")])
    held: list[tuple[str, str, int, int]] = []
    opens, said, depth = 0, "", 0
    is_continuing_a_directive = False
    for at, line in enumerate(lines, start=1):
        stripped = line.strip()
        if is_continuing_a_directive:
            is_continuing_a_directive = stripped.endswith("\\")
            continue
        if depth == 0 and not said and (not stripped or _A_DIRECTIVE.match(stripped)):
            is_continuing_a_directive = stripped.endswith("\\")
            continue
        if depth == 0 and not said:
            opens = at
        said += " " + stripped
        depth += line.count("{") - line.count("}")
        if depth > 0:
            continue
        if stripped.endswith("\\"):
            continue
        is_open = "{" in said
        if is_open and not stripped.endswith(";") and not stripped.startswith("}"):
            continue
        if not is_open and not stripped.endswith(";") and not said.lstrip().startswith("#define"):
            continue
        if not _is_an_include_guard(lines, at):
            held.append((*_c_declared(said, stripped), opens, at))
        said = ""
    return held


def _is_an_include_guard(lines: list[str], at: int) -> bool:
    """Whether the `#define` on line `at` repeats the name an `#ifndef` on the line above it guards."""
    guarded = _AN_INCLUDE_GUARD.match(lines[at - 2].strip()) if at > 1 else None
    return bool(guarded and lines[at - 1].strip() == f"#define {guarded.group(1)}")


def _at_file_scope(lines: list[str]) -> list[str]:
    """
    The lines with the opening line of an `extern "C"` blanked and its closing brace removed. A declaration inside the
    `extern "C"` block is at file scope.
    """
    held, depth = list(lines), 0
    for at, line in enumerate(lines):
        if 'extern "C"' in line and "{" in line:
            held[at] = ""
            continue
        depth += line.count("{") - line.count("}")
        if depth < 0:
            held[at] = held[at].replace("}", "", 1)
            depth += 1
    return held


def _c_bodies(source: str, marker: str, extents: list[tuple[str, str, int, int]]) -> set[int]:
    """The lines a C function's braces hold. A comment there belongs to the function's fragment."""
    held: set[int] = set()
    lines = source.split("\n")
    for kind, _name, first, last in extents:
        if kind != "function":
            continue
        for at in range(first, last + 1):
            if "{" in lines[at - 1].partition(marker)[0]:
                held.update(range(at, last + 1))
                break
    return held


def _c_fragments(path: str, source: str) -> list[Fragment]:
    """
    The fragments of a C file. The file makes a fragment. A type, a function and a `#define` make a fragment apiece. So
    does a variable at file scope.

    A member of a type is part of that type rather than a fragment of its own. A comment block inside a function body is
    prose of the function's fragment, unless it is a tool directive.
    """
    marker = "//"
    blocks = _blocks(_comments(source, "C"))
    branches = _branch_lines(source, marker)
    extents = [
        (kind, name, _opens_above(blocks, first, branches), last)
        for kind, name, first, last in _c_extents(source, marker)
    ]
    bodies = _c_bodies(source, marker, extents)
    comments = _comments(source, "C")
    directives = {at for at in bodies if at in comments and gate.TOOL_DIRECTIVE.match(comments[at])}
    is_prose = {at for first, last, _said in blocks for at in range(first, last + 1)} - directives
    split = _Split(source, is_prose)
    split.in_bodies.update(bodies)
    for at, said in comments.items():
        split.says(at, said)
    for at, (code, said, last) in _marked_trailing(source, "C").items():
        if at not in branches and not gate.TOOL_DIRECTIVE.match(said):
            split.shares(at, code, said, last)
    return _gathered("C", path, split, extents, blocks, branches, bodies)


# The declaration a Makefile line makes. A target opens at the line's start, and an assignment there names a variable.
_A_TARGET = re.compile(r"^([A-Za-z0-9_./%-]+)\s*:(?!=)")
_A_VARIABLE = re.compile(
    r"^([A-Za-z0-9_]+)\s*[:?+]?="
)  # A line assigning a Makefile variable. The operator there is `=` or `:=` or `?=` or `+=`.

# The lines `make` reads as an instruction to itself rather than as a target. `.PHONY` is one such line. `.stamps` is a
# target.
_A_MAKE_DIRECTIVE = re.compile(r"^\.[A-Z_]+$")


def _make_extents(source: str) -> list[tuple[str, str, int, int]]:
    """
    The declarations a Makefile makes, as `(kind, name, first line, last line)`. A target owns the recipe under it.
    """
    lines = source.split("\n")
    held: list[tuple[str, str, int, int]] = []
    at = 0
    while at < len(lines):
        found = _A_TARGET.match(lines[at]) or _A_VARIABLE.match(lines[at])
        if not found:
            at += 1
            continue
        kind = "target" if _A_TARGET.match(lines[at]) else "variable"
        last = at
        while last + 1 < len(lines) and (lines[last].endswith("\\") or lines[last + 1].startswith("\t")):
            last += 1
        if not _A_MAKE_DIRECTIVE.match(found.group(1)):
            held.append((kind, found.group(1), at + 1, last + 1))
        at = last + 1
    return held


def _make_fragments(path: str, source: str) -> list[Fragment]:
    """
    The fragments of a Makefile. A Makefile gives a fragment per target, per variable and per unattached comment. The
    file makes a fragment of its own.
    """
    blocks = _blocks(_comments(source, "Make"))
    branches = _branch_lines(source, "#")
    extents = [
        (kind, name, _opens_above(blocks, first, branches), last) for kind, name, first, last in _make_extents(source)
    ]
    split = _Split(source, {at for first, last, _said in blocks for at in range(first, last + 1)})
    for at, said in _comments(source, "Make").items():
        split.says(at, said)
    for at, (code, said, last) in _marked_trailing(source, "Make").items():
        if at not in branches:
            split.shares(at, code, said, last)
    return _gathered("Make", path, split, extents, blocks, branches, set())


def _said_by_note(strings: list[str]) -> str:
    """
    The prose a JSON note's value says.

    A note whose value is an array writes a string per line. An empty string there ends a paragraph. The lines of a
    paragraph join with a space, and a pair of paragraphs join with a blank line. `apply_prose` writes the strings back
    in that shape.
    """
    paragraphs: list[list[str]] = [[]]
    for one in strings:
        if one.strip():
            paragraphs[-1].append(one.strip())
        elif paragraphs[-1]:
            paragraphs.append([])
    return "\n\n".join(" ".join(one) for one in paragraphs if one)


def _json_fragments(path: str, source: str) -> list[Fragment]:
    """
    The fragments of a JSON file. A `_` value is the prose, and any other value is data.

    JSON writes no comment. The grammar at https://www.json.org/json-en.html holds none. A file of this tree says its
    own prose under `_`, at the top and inside an entry. A payload beside that prose is a fixture. A fixture may hold
    the very fault a hook must refuse. `tests/hooks.json` holds such fixtures.
    """
    held: list[Fragment] = []
    lines = source.split("\n")
    at = 0
    while at < len(lines):
        if not _A_JSON_NOTE.match(lines[at]):
            at += 1
            continue
        first, run = at, [lines[at]]
        depth = lines[at].count("[") - lines[at].count("]")
        while depth > 0 and at + 1 < len(lines):
            at += 1
            run.append(lines[at])
            depth += lines[at].count("[") - lines[at].count("]")
        at += 1
        quoted = _A_JSON_STRING.findall("\n".join(run))[1:]
        said = _said_by_note([json.loads(f'"{one}"') for one in quoted])
        if not said:
            continue
        sites = (_Site(path, tuple(range(len(run))), flattened(said), said),)
        prose = _a_prose([said])
        key = _keyed("JSON", path, _named_by(prose))
        held.append(Fragment(key, path, "note", {path: first + 1}, prose, _text([]), sites))
    return held


def _flat_fragments(language: str, path: str, source: str) -> list[Fragment]:
    """
    The fragments of a straight-line file. The fragments are the file itself and the comment blocks lower down that hold
    no section heading.

    The block at the top is the file's prose and the rest of the file is its code. Such a file declares nothing, and a
    block lower down documents no declaration.

    A settings file says its prose in a comment. Such a file with no comment at the top makes no fragment for the file
    itself.
    """
    blocks = _blocks(_comments(source, language))
    split = _Split(source, {at for first, last, _said in blocks for at in range(first, last + 1)})
    for at, said in _comments(source, language).items():
        split.says(at, said)
    for at, (code, said, last) in _marked_trailing(source, language).items():
        split.shares(at, code, said, last)
    held = _gathered(language, path, split, [], blocks, set(), set())
    return [one for one in held if one.prose.content] if language == "Conf" else held


def _document_lines(source: str) -> Iterator[tuple[int, str]]:
    """
    A markdown document's lines with their numbers. The front matter and a fenced block stay out. A `COPIED_BELOW` line
    ends the document.
    """
    is_front = source.startswith("---")
    is_fenced = False
    for at, line in enumerate(source.split("\n"), start=1):
        if line.strip() == COPIED_BELOW:
            return
        if is_front:
            is_front = at == 1 or not _A_FRONT_MATTER.match(line)
            continue
        if _A_FENCE.match(line):
            is_fenced = not is_fenced
            continue
        if not is_fenced:
            yield at, line


def _document_fragments(path: str, source: str) -> list[Fragment]:
    """
    The fragments of a markdown document. A paragraph makes a fragment. A top-level list item makes a fragment together
    with the items under it.

    A fenced block is code and makes no fragment. The heading above a paragraph and a digest of the paragraph's prose
    name that paragraph. A rule of `_THE_RULES` takes the rule's name instead.

    A `COPIED_BELOW` line ends the document. The prose under that line lives elsewhere in the tree, and a copy makes no
    fragment.
    """
    held: list[Fragment] = []
    heading = ""
    block: list[str] = []
    first = 1

    def close(opens: int) -> None:
        content = "\n".join(block).strip("\n")
        if not content:
            return
        prose = _a_prose([content])
        ruled = _A_RULE.match(content) if path == _THE_RULES else None
        if ruled:
            name = ruled.group(1)
        else:
            name = f"{heading} {_named_by(prose)}" if heading else _named_by(prose)
        sites = (_Site(path, tuple(range(len(block))), flattened(content), content),)
        held.append(Fragment(_keyed("Markdown", path, name), path, "paragraph", {path: opens}, prose, _text([]), sites))

    for at, line in _document_lines(source):
        found = _A_HEADING.match(line)
        # A top-level item closes the item before it. A nested item continues with the item it sits under.
        opens = bool(_A_TOP_ITEM.match(line)) and bool(block)
        if found or not line.strip() or opens:
            close(first)
            block, first = [], at
            if found:
                heading = found.group(2)
                continue
        if line.strip():
            if not block:
                first = at
            block.append(line)
    close(first)
    return held


def _fragments_of(path: str) -> list[Fragment]:
    """The fragments of a single file of the tree, by the language of that file."""
    if language_of(path) is None:
        return []
    with open(os.path.join(gate.TREE, path), encoding="utf-8") as handle:
        return _fragments_of_source(path, handle.read())


def _fragments_of_source(path: str, source: str) -> list[Fragment]:
    """
    The fragments of `path` as `source` writes it. The language of `path` decides which function splits `source` into
    fragments.

    A caller hands the text over, and this function reads no file.
    """
    language = language_of(path)
    if language is None:
        return []
    if language == "Markdown":
        return _document_fragments(path, source)
    if language == "Python":
        return _python_fragments(path, source)
    if language == "C":
        return _c_fragments(path, source)
    if language == "Make":
        return _make_fragments(path, source)
    if language == "JSON":
        return _json_fragments(path, source)
    return _flat_fragments(language, path, source)


def _joined(held: list[Fragment]) -> list[Fragment]:
    """
    A C function or type as a single fragment. The header declares the name and the source defines it.

    `_joined` matches a pair by name. The joined fragment keeps the source's key and the prose of both sites.
    """
    is_split = ("function", "type")
    bodies = {one.key.rpartition(":")[2]: one for one in held if one.kind in is_split and one.path.endswith(".c")}
    joined = []
    for one in held:
        named = one.key.rpartition(":")[2]
        if one.kind in is_split and one.path.endswith(".h") and named in bodies:
            body = bodies[named]
            code = "\n".join(part for part in (one.code.content, body.code.content) if part)
            body.prose = _a_prose([site.prose for site in one.sites + body.sites])
            body.code = _Text(code, _digest(code))
            body.sites = one.sites + body.sites
            body.starts = {**body.starts, **one.starts}
            continue
        joined.append(one)
    return joined


def _c_members(code: str) -> set[str]:
    """The names a C type's body declares. That is a struct's fields and an enum's constants."""
    return set(_A_MEMBER_NAME.findall(code.partition("{")[2]))


# A struct or a union, as a header declares one. The declaration opens a line.
_A_STRUCT = re.compile(r"^\s*(?:typedef\s+)?(?:struct|union)\b[^;{]*\{", re.MULTILINE)

# An enum, as a header declares one. The declaration opens a line.
_AN_ENUM = re.compile(r"^\s*(?:typedef\s+)?enum\b[^;{]*\{", re.MULTILINE)


def _named_in_bodies(held: list[Fragment], opens: re.Pattern[str]) -> set[str]:
    """
    The names a C type declares inside it, over the C fragments of `held` that `opens` matches.

    The body runs from the open brace to a close brace at the head of a line. The name a typedef writes after that close
    stays out.
    """
    found: set[str] = set()
    for one in held:
        if not one.key.startswith("C:"):
            continue
        opened = opens.search(one.code.content)
        if opened is None:
            continue
        found |= set(_A_MEMBER_NAME.findall(one.code.content[opened.end() :].partition("\n}")[0]))
    return found


def c_members(held: list[Fragment]) -> set[str]:
    """
    The field names the struct and union bodies of `held` declare.

    Public. `.claude/mcp/fetch_fragments.py` refuses such a name in a question about mentions. Structs may share a field
    name, and a search for that name crosses the types declaring it.
    """
    return _named_in_bodies(held, _A_STRUCT)


def c_constants(held: list[Fragment]) -> set[str]:
    """
    Answer the names of the enum constants that the C fragments in the list `held` declare.

    Public. `.claude/mcp/fetch_fragments.py` takes such a name. An enum constant sits in the namespace of its file
    rather than the namespace of a type, and the name reaches a single declaration.
    https://en.cppreference.com/w/c/language/name_space puts an enum constant among the ordinary identifiers.
    """
    return _named_in_bodies(held, _AN_ENUM)


# A step of the pipeline, as `normalize` writes one. The call names the step and then names the function that does the
# work. Prose cites the step under the name in that call.
_A_STEP = re.compile(r'_Step\(\s*"([a-z0-9][a-z0-9-]*)"\s*,\s*(_[A-Za-z0-9_]+)')

# An invariant of the pipeline, as `normalize` writes one. The call names the invariant. A constant holds that call, and
# a reader wants the fragment of that constant. `_absent` writes the invariant that a kind of node is gone.
_AN_INVARIANT = re.compile(r'_(?:Invariant|absent)\(\s*"([a-z0-9][a-z0-9-]*)"')

# A phase of the pipeline, as `normalize` writes one. The call names the phase, and a constant holds that call.
_A_PHASE = re.compile(r'_Phase\(\s*"([a-z0-9][a-z0-9-]*)"')


def _named_by_the_pipeline(held: list[Fragment], by_name: dict[str, set[str]]) -> None:
    """
    Put the names of the pipeline's steps, phases and invariants into `by_name`.

    A step names itself in its own call. A phase and an invariant do the same. Prose cites those names, and
    `named_by_the_pipeline` of `check_documents` accepts a citation to them. A step's name reaches the fragment of the
    function the step names. The name of an invariant reaches the fragment holding the call. So does the name of a
    phase, unless a step bears that name too. Such a name reaches the fragment of the step's function.

    A module's own fragment holds the code of the whole file, and that code holds these calls as well. Such a fragment
    takes none of these names.
    """
    steps = set()
    for one in held:
        for name, doing in _A_STEP.findall(one.code.content):
            steps.add(name)
            if by_name.get(doing):
                by_name.setdefault(name, set()).update(by_name[doing])
    for one in held:
        if one.key.count(":") < 2:
            continue
        for name in _AN_INVARIANT.findall(one.code.content):
            by_name.setdefault(name, set()).add(one.key)
        # A phase holding a single step may take that step's name, and the name then reaches the step.
        for name in _A_PHASE.findall(one.code.content):
            if name not in steps:
                by_name.setdefault(name, set()).add(one.key)


def keys_by_name(held: list[Fragment], is_taking_members: bool = True) -> dict[str, set[str]]:
    """
    `{a name prose may cite: the keys it names}`, over the fragments of `held`.

    A fragment takes its bare name, and the file and the name together. A fragment named by a run of declarations takes
    any of them. A file fragment takes its file. A Python module takes its file less the suffix. Prose cites a module
    under that form. A C type also takes any member it declares where `is_taking_members` holds. A member is part of a
    type rather than a fragment of its own.

    A production of the official grammar under `third_party/` takes the file and the name together, and no bare name.
    Prose of ours cites a bare production name, and that name reaches the production of libyeast's grammar.

    Public. `.claude/mcp/fetch_fragments.py` reads this map for the claim pass. The claim pass tells an ambiguous name
    from a name the tree lacks.
    """
    by_name: dict[str, set[str]] = {}
    for one in held:
        language, _, rest = one.key.partition(":")
        named, _, name = rest.partition(":")
        is_official = language == "Grammar" and one.path.startswith("third_party/")
        forms = {named, one.path} if not name else set() if is_official else {name}
        if not name and language == "Python":
            forms.add(os.path.splitext(named)[0])
        for part in name.split(", ") if name else []:
            forms |= {f"{named}.{part}"} if is_official else {part, f"{named}.{part}"}
            if language == "Python":
                forms |= {part.rpartition(".")[2], f"{os.path.splitext(named)[0]}.{part}"}
        if one.kind == "type" and is_taking_members:
            forms |= _c_members(one.code.content)
        for form in forms:
            by_name.setdefault(form, set()).add(one.key)
    _named_by_the_pipeline(held, by_name)
    return by_name


def reach_by_name(held: list[Fragment]) -> dict[str, set[str]]:
    """
    `{a name a citation may write: the keys of `held` it reaches}`. A key reaches the fragment it names. Another name
    reaches the keys `keys_by_name` gives it.

    Public. `prose_answer` refuses a citation through this map, and `claim_state` reads a citation through it.
    """
    reached = keys_by_name(held)
    reached.update({one.key: {one.key} for one in held})
    return reached


def resolved(held: list[Fragment]) -> dict[str, tuple[str, ...]]:
    """
    `{a name prose may cite: the sorted keys it names}`.

    Public. `prose_answer` resolves the citations of a draft with this.

    A name reaching a single fragment names that fragment. A C header may declare a typedef, and another C header may
    declare the struct under the same name. Such a name names the whole group, as `ys_token_source` does. Any other name
    a pair of fragments both take resolves to nothing. Prose citing such a name has to name the file. A bare member of a
    C type resolves to nothing as well.
    """
    found = {}
    for form, keys in keys_by_name(held, False).items():
        if len(keys) == 1 or _is_one_c_name(form, keys):
            found[form] = tuple(sorted(keys))
    return found


def _is_one_c_name(form: str, keys: set[str]) -> bool:
    """Whether `keys` are C header declarations of `form`."""
    return all(key.startswith("C:") and key.endswith(f".h:{form}") for key in keys)


def cited_keys(
    one: Fragment, prose: str, resolves: Mapping[str, tuple[str, ...]], by_key: Mapping[str, Fragment]
) -> list[str]:
    """
    The sorted keys `prose` cites, read as the prose of `one`. `resolves` comes from `resolved`. `by_key` maps a key of
    the tree to its fragment.

    Public. `fragments` takes the references of a fragment from this. `prose_answer` takes those of a draft.

    A citation reaches a declaration of a test only from prose in that test. A case `_registered_cases` finds is citable
    from any prose. So is the file of a test. The key of `one` stays out.
    """
    tests = {os.path.relpath(path, gate.TREE) for path in gate.test_sources()}
    cited = {key for name in cited_by(prose, one.key.partition(":")[0]) for key in resolves.get(name, ())}
    return sorted(
        key
        for key in cited - {one.key}
        if by_key[key].path not in tests
        or is_a_file(key)
        or by_key[key].path == one.path
        or key.rpartition(":")[2] in _registered_cases(by_key[key].path)
    )


# The acutest test list of a test file, up to the brace closing it.
_A_TEST_LIST = re.compile(r"TEST_LIST\s*=\s*\{(.*?)\n\};", re.DOTALL)

# A case the acutest test list registers, as `{"end_of_input", test_end_of_input},` writes it.
_A_REGISTERED_CASE = re.compile(r'\{\s*"[^"]*"\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}')


def _registered_cases(path: str) -> frozenset[str]:
    """The names of the functions the acutest test list of the test file `path` registers."""
    full = os.path.join(gate.TREE, path)
    return _cases_in(full, os.stat(full).st_mtime_ns)


@functools.cache
def _cases_in(full: str, _mtime: int) -> frozenset[str]:
    """
    Give back the names `_registered_cases` reads out of the file `full`. The cache takes the modification time of the
    file as a key. A write to the file changes that time, as
    https://docs.python.org/3/library/os.html#os.stat_result.st_mtime_ns says.

    A file naming no test list registers no case. `tests/pkg_consumer.c` is such a file. A file whose test list
    `_A_TEST_LIST` cannot read raises.
    """
    with open(full, encoding="utf-8") as handle:
        source = handle.read()
    listed = _A_TEST_LIST.search(source)
    if listed is None:
        if "TEST_LIST" in source:
            raise ValueError(f"{full} names a test list that `_A_TEST_LIST` cannot read")
        return frozenset()
    return frozenset(_A_REGISTERED_CASE.findall(listed.group(1)))


def fragments() -> list[Fragment]:
    """
    The fragments the project holds. A citation in a fragment's prose resolves to the fragment it names.

    A citation naming a field, a parameter or a local names no fragment, and this drops it. A citation reaches a
    declaration of a test only from prose in that test. Any prose may cite a case `_registered_cases` finds. Any prose
    may also cite the file of a test. `every-cited-name-exists` holds a citation to the names the tree writes.

    This applies no `is_about_the_tree` skip. `prose_rewrite.refusal_for` applies that skip for the shape rules. The
    word rules reach the file the skip leaves out. `prose_rules.word_refusal` applies no such skip.
    """
    held: list[Fragment] = []
    for path in gate.prose_files():
        held.extend(_fragments_of(str(path.relative_to(gate.TREE))))
    held = _joined(held)
    reachable = held + code_only()
    resolves = resolved(reachable)
    by_key = {one.key: one for one in reachable}
    for one in held:
        one.references = {key: code_digest(by_key[key]) for key in cited_keys(one, one.prose.content, resolves, by_key)}
    return sorted(held, key=lambda one: (one.path, one.start_line))


@dataclass(frozen=True)
class _Edit:
    """
    A single edit a checker is deciding. The class holds the file the edit names, as the file is and as the edit would
    leave it.
    """

    path: str
    was: str
    now: str


# The words a literal says before it reads as prose. A shorter literal names something rather than saying it.
_FEWEST_WORDS_SAID = 2

# A literal the tree writes source into. `grammar2decoder` writes C declarations that way. `pre-commit-review.js` builds
# its prompts with arrow functions inside template literals. The `Makefile` and `scripts/check-deps.sh` hand a Python
# program to `python3 -c`.
_HOLDS_SOURCE = re.compile(r"(?:\b(?:const |import |print\()|=>|#!/)")

# A pattern a shell hands to `grep`. `.claude/hooks/no-shell-file-writes.sh` hands such a pattern to `grep -Eq`. A
# character class and a class shorthand belong to a regular expression rather than to a sentence.
_HOLDS_A_PATTERN = re.compile(r"\[[0-9a-zA-Z]-[0-9a-zA-Z]\]|\\[bwsdWSD]")

# A shell command line, and a template literal holding an interpolation. `$NAME` and `${NAME}` mark both.
_HOLDS_AN_EXPANSION = re.compile(r"\$\{?[A-Za-z_]")

# A literal holding C source. An include guard or an `#include` opens such a literal. So does a `typedef`. A closing
# brace before a name and a semicolon opens such a literal as well. A cast to any `_t` type marks C source anywhere in
# the literal.
_HOLDS_C_SOURCE = re.compile(r"^\s*(?:#(?:ifndef|define|endif|include)\b|typedef\b|\}\s*\w+;)|\(\w+_t\)")

# The marker `not_prose_lines` reads, in a `#` or `//` comment. The reason follows the colon.
_A_NOT_PROSE_MARKER = re.compile(rf"(?:#|//).*\b{gate.NOT_PROSE}:\s*(\S.*)$")

# A block comment, with the prose between the markers. C and JS write one.
_A_BLOCK_COMMENT = re.compile(r"/\*(.*?)\*/", re.S)

# The opener of a heredoc in a shell script. The word after `<<` names the line that closes the body. A word opens on a
# letter or an underscore, and `$((1<<2))` opens no heredoc.
_A_HEREDOC_OPENER = r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1"

# A heredoc, with the prose between the opener's line and the line holding the closing word.
_A_HEREDOC = re.compile(_A_HEREDOC_OPENER + r"[^\n]*\n(.*?)\n\s*\2$", re.S | re.M)

# An `echo` or a `printf` whose argument opens with no quote. A shell and a Makefile write one.
_AN_ECHOED_WORD = re.compile(r"^[ \t]*@?(?:echo|printf)[ \t]+(?![\"'])(\S.*)$", re.M)

# A setting, with the prose after the key. A comment line opens on its marker and matches nothing here.
_A_SETTING = re.compile(r"^[ \t]*[\w.\-]+[ \t]*[:=][ \t]*(\S.*)$", re.M)

# The file whose strings are hook payloads rather than prose. `tests/hooks.json` holds pairs of payloads. A hook must
# refuse the edit under `answers` and pass the edit under `passes`. The fault may sit in the payload under `answers`. A
# pair may also give a `file`, the text the edit lands in. That text may hold a fault already. The file's own prose sits
# under `_`, and `_json_fragments` reads that.
_HOOK_PAYLOADS = "tests/hooks.json"

# The quotes a language opens a literal with. C writes a double quote. C also writes a single quote around a character,
# as https://en.cppreference.com/w/c/language/character_constant says. JS writes a double quote, a single quote and a
# backtick, and the backtick form interpolates.
# https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Template_literals describes that interpolation.
_QUOTES = {"C": "\"'", "JS": "\"'`", "Shell": "\"'", "Make": "\"'", "CMake": '"', "YAML": "\"'", "JSON": '"'}

# The `#` opening a comment on a `Makefile` line. A backslash before a `#` escapes it.
_AN_UNESCAPED_HASH = re.compile(r"(?<!\\)#")

# The quotes a settings file of the tree opens a value with. `trailing_comments` reads a settings file with these.
# `_QUOTES` leaves a settings file out, and `prose_literals` reads no literal there.
_SETTING_QUOTES = "\"'"

# A conversion a C format string writes. The value it takes the place of is a name to a reader.
_A_CONVERSION = re.compile(r"%[-+ #0]*[0-9*]*(?:\.[0-9*]+)?(?:hh|h|ll|l|z|j|t|L)?[diouxXeEfFgGaAcspn%]")

# An interpolation a JS template literal writes. Its braces hold an expression rather than a sentence.
_AN_INTERPOLATION = re.compile(r"\$\{[^{}]*\}")

# A parameter, a command substitution or a bare name behind a dollar. A workflow's `${{ }}` holds a brace inside its
# braces, and this matches none.
_AN_EXPANSION = re.compile(r"\$\$?\{[^{}]*\}|\$\$?\([^()]*\)|\$[A-Za-z_@*#?!$0-9][A-Za-z_0-9]*")

# An escape a quoted run writes. A break, a carriage return and a tab read as a space, and any other escape reads as its
# character.
_AN_ESCAPE = re.compile(r"\\(.)")

# A line break a C string writes before its last character. C has no multi-line string. A writer who wants such a string
# escapes the break. C keeps a bare newline out of a literal, and
# https://en.cppreference.com/w/c/language/string_literal says so. Such a string is a record. A writer of prose in C
# gives a line to a literal, and the compiler joins the lines.
# https://en.cppreference.com/w/c/language/translation_phases lists that join among the translation phases.
_A_BROKEN_LINE = re.compile(r"\\n(?!$)")


def _said_by_literal(node: ast.expr) -> str | None:
    """
    The text a literal says. This answers None where the node holds no string. An interpolation reads as the fixed word
    `name` in backticks.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "`name`"
            for part in node.values
        )
    return None


def _past_interpolation(source: str, at: int) -> int:
    """
    `_past_interpolation` answers the offset just past the interpolation that opens at `at`.

    An expression can hold braces. An expression can hold a template literal. The scan counts a brace inside a template
    literal. The scan ignores backticks.
    """
    depth, walk, length = 0, at + 1, len(source)
    while walk < length:
        if source[walk] == "{":
            depth += 1
        elif source[walk] == "}":
            depth -= 1
            if depth == 0:
                return walk + 1
        walk += 1
    return length


def _quoted_runs(source: str, quotes: str, marker: str) -> list[tuple[int, int, int, str]]:
    """`(the line a string opens on, where it opens, where it ends, the text between its quotes)` over `source`."""
    return _scanned(source, quotes, marker)[0]


def _scanned(source: str, quotes: str, marker: str) -> tuple[list[tuple[int, int, int, str]], list[tuple[int, int]]]:
    """
    A pair of lists over `source`. The first holds a tuple per string, as `_quoted_runs` writes it. The second holds
    `(where it opens, where it ends)` per comment.

    A comment holds no literal. A `#` marker opens a comment at a word start, and `$#` inside a word opens none. An
    empty marker says the language writes no comment.

    A backslash takes the character after it. A single quote takes no escape outside a `//` language. Such a run ends at
    the next quote. A backtick run skips its interpolations. A backtick inside an interpolation belongs to the
    expression.
    """
    held, comments, at, line, length = [], [], 0, 1, len(source)
    while at < length:
        char = source[at]
        if char == "\n":
            line, at = line + 1, at + 1
        elif marker and source.startswith(marker, at) and (marker != "#" or at == 0 or source[at - 1] in " \t\n"):
            found = source.find("\n", at)
            comments.append((at, length if found < 0 else found))
            at = length if found < 0 else found
        elif marker == "//" and source.startswith("/*", at):
            found = source.find("*/", at + 2)
            shut = length if found < 0 else found + 2
            comments.append((at, shut))
            line, at = line + source.count("\n", at, shut), shut
        elif char in quotes:
            escapes = char != "'" or marker == "//"
            opens, opened, walk = at, line, at + 1
            while walk < length and source[walk] != char:
                if char == "`" and source.startswith("${", walk):
                    walk = _past_interpolation(source, walk)
                else:
                    walk += 2 if escapes and source[walk] == "\\" else 1
            line += source.count("\n", opens, min(walk, length))
            held.append((opened, opens, min(walk + 1, length), source[opens + 1 : walk]))
            at = min(walk + 1, length)
        else:
            at += 1
    return held, comments


def _unwrapped_runs(runs: list[tuple[int, int, int, str]]) -> list[tuple[int, int, int, str]]:
    """
    `runs` with a program unwrapped, as the same tuples.

    A shell single quote holds a program where the text inside it holds a double-quoted string. The prose there is the
    double-quoted strings, and the program around them is code. A single-quoted run holding no such string is prose
    itself. A run `_HOLDS_SOURCE` matches stays wrapped. Its inner strings are source as well.
    """
    held = []
    for line, opens, shuts, said in runs:
        inside = _quoted_runs(said, '"', "#") if '"' in said and not _HOLDS_SOURCE.search(said) else []
        if not inside:
            held.append((line, opens, shuts, said))
            continue
        held += [
            (line + said.count("\n", 0, at), opens + at + 1, opens + shut + 1, text) for _line, at, shut, text in inside
        ]
    return held


def _joined_runs(source: str, runs: list[tuple[int, int, int, str]]) -> list[tuple[int, str]]:
    """
    Give back `(the line it opens on, what it says)` for a string. A string a writer wraps over lines counts as a single
    string.

    C joins a pair of strings that only whitespace separates, as https://en.cppreference.com/w/c/language/string_literal
    says. JS joins a pair with a `+` between them, as
    https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Addition says. A message wrapped over a
    pair of lines reads as a single message, and a sentence may cross the wrap.
    """
    held: list[tuple[int, str]] = []
    ends: list[int] = []
    for line, opens, shuts, said in runs:
        if held and not source[ends[-1] : opens].strip(" \t\r\n+"):
            held[-1] = (held[-1][0], held[-1][1] + said)
            ends[-1] = shuts
        else:
            held.append((line, said))
            ends.append(shuts)
    return held


def _named_out(said: str, pattern: re.Pattern[str]) -> str:
    """
    `said` with the runs `pattern` matches reading as a name in backticks. An inner run goes first, and the outer run
    then matches in turn.
    """
    while True:
        marked = pattern.sub("`name`", said)
        if marked == said:
            return marked
        said = marked


def _said_by_run(said: str, language: str) -> str:
    """
    The text a quoted run says. An interpolation in the run reads as a name in backticks.

    A C conversion, a JS interpolation and a dollar expansion take a value's place. An expansion nested in another
    expansion goes with the outer expansion. An escaped break, carriage return or tab reads as a space.
    """
    marked = _A_CONVERSION.sub("`name`", said) if language == "C" else said
    marked = _named_out(marked, _AN_INTERPOLATION) if language == "JS" else marked
    marked = _named_out(marked, _AN_EXPANSION) if language in ("Shell", "Make", "CMake", "YAML") else marked
    return _AN_ESCAPE.sub(lambda found: " " if found.group(1) in "ntr" else found.group(1), marked)


def _does_read_as_prose(said: str) -> bool:
    """
    Whether `said` reads as prose rather than as data.

    A string whose `prose_rules.said_by` holds fewer than `_FEWEST_WORDS_SAID` words names something rather than saying
    it. Source counts as data. A pattern counts as data. A shell variable such as `$NAME` marks a command line, and C
    source is data. A string opening on `usage:` is data. A hook refusal ends on the name of the rule it enforces. This
    function rejects such a refusal.
    """
    if _HOLDS_SOURCE.search(said) or _HOLDS_A_PATTERN.search(said):
        return False
    if _HOLDS_AN_EXPANSION.search(said) or _HOLDS_C_SOURCE.search(said):
        return False
    if said.lstrip().lower().startswith("usage:"):
        return False
    if prose_rules.is_stating_its_rule(said):
        return False
    return len(prose_rules.said_by(said).split()) >= _FEWEST_WORDS_SAID


def not_prose_lines(source: str) -> dict[int, str]:
    """
    `{the line a `not-prose:` marker is on: the reason it states}`.

    A literal may hold a record layout or a fixture line rather than a sentence. The marker covers the line it is on and
    the line under that. A writer trails the literal's own line with the marker, or writes the marker above.
    `check_prose` holds the marker in both directions.
    """
    return {
        at: found.group(1).strip()
        for at, line in enumerate(source.splitlines(), start=1)
        if (found := _A_NOT_PROSE_MARKER.search(line))
    }


def _unquoted_prose(source: str, language: str) -> list[tuple[int, str]]:
    """
    The prose of `source` that no quote holds, as `(the line it opens on, what it says)`.

    Prose sits outside a quote as well. A block comment holds prose between `/*` and `*/`. A heredoc holds prose between
    the opener and the word that closes it. An `echo` or a `printf` takes prose with no quote around it. A settings file
    holds prose after the key. A markdown heading names the section under it.

    `_quoted_runs` reaches none of those.
    """
    held: list[tuple[int, str]] = []

    def says(at: int, said: str) -> None:
        held.append((source[:at].count("\n") + 1, " ".join(said.split())))

    if language in ("C", "JS"):
        for found in _A_BLOCK_COMMENT.finditer(source):
            says(found.start(), found.group(1).replace("*", " "))
    if language == "Shell":
        for found in _A_HEREDOC.finditer(source):
            says(found.start(), found.group(3))
    for pattern, language_of_it in ((_AN_ECHOED_WORD, ("Shell", "Make")), (_A_SETTING, ("Conf",))):
        if language in language_of_it:
            for found in pattern.finditer(source):
                says(found.start(), found.group(1))
    if language == "Markdown":
        for found in _A_HEADING.finditer(source):
            says(found.start(), found.group(2))
    return held


def prose_literals(source: str, language: str = "Python") -> list[tuple[int, str]]:
    """
    The string literals of `source` that read as prose, as `(the line it opens on, what it says)`.

    A language `_LANGUAGES` names has a reader here. `_unquoted_prose` reads what no quote holds.

    `_does_read_as_prose` holds the tests a reader shares.

    A docstring is a fragment's prose and belongs elsewhere. An f-string reads whole, and an interpolation inside it
    comes back as the fixed word `name` in backticks. A literal of fewer than `_FEWEST_WORDS_SAID` words is data. That
    leaves out a rule name and a grammar operator. A literal holding source counts as data. A pattern handed to `re` and
    a line opening on `usage:` count as data as well. A literal a caller splits into words is a word list. A hook
    refusal ends on the name of the rule it enforces. `prose_literals` leaves such a refusal out too.
    """
    bare = [(at, said) for at, said in _unquoted_prose(source, language) if _does_read_as_prose(said)]
    if language in _QUOTES:
        scanned = _quoted_runs(source, _QUOTES[language], MARKERS[language])
        if language in ("Shell", "Make", "YAML"):
            scanned = _unwrapped_runs(scanned)
        runs = _joined_runs(source, scanned)
        if language == "C":
            runs = [(at, one) for at, one in runs if not _A_BROKEN_LINE.search(one)]
        return sorted(
            set(bare)
            | {
                (at, said)
                for at, said in ((at, _said_by_run(one, language)) for at, one in runs)
                if _does_read_as_prose(said)
            }
        )
    if language != "Python":
        return sorted(set(bare))
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    # An f-string counts as a single literal. The pieces of an f-string are parts of that literal.
    pieces = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
        for inner in ast.walk(part)
    }
    # A pattern passed to `re.compile` is data.
    patterns = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("compile", "match", "search", "fullmatch", "sub", "findall", "finditer", "split")
        for arg in node.args
        for inner in ast.walk(arg)
    }
    # A literal a caller splits into words is data.
    word_lists = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "split"
        for inner in ast.walk(node.func.value)
    }
    held = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        if id(node) in docstrings or id(node) in pieces or id(node) in patterns or id(node) in word_lists:
            continue
        said = _said_by_literal(node)
        if said is not None and _does_read_as_prose(said):
            held.append((node.lineno, said))
    return sorted(set(held))


def written(tool_input: Mapping[str, object]) -> _Edit | None:
    """
    The edit `tool_input` describes, read against the file on disk.

    The old text of an Edit decides nothing where the file lacks it. The tool refuses such an edit before anything
    lands. https://code.claude.com/docs/en/tools-reference says `old_string` must appear in the file exactly as written.

    An Edit with an empty `new_string` deletes the line break right after `old_string` as well. The tool takes that line
    break only where `old_string` does not end on a line break. `edit_landed` refuses an edit whose landed file differs
    from this reading.
    """
    path = tool_input.get("file_path")
    if not isinstance(path, str):
        return None
    was = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            was = handle.read()
    content = tool_input.get("content")
    if isinstance(content, str):
        return _Edit(path, was, content)
    old, new = tool_input.get("old_string"), tool_input.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str) or old not in was:
        return None
    taken = re.escape(old) + ("\n?" if not new and not old.endswith("\n") else "")
    now = re.sub(taken, lambda _found: new, was, count=0 if tool_input.get("replace_all") else 1)
    return _Edit(path, was, now)


def whole(path: str) -> _Edit:
    """
    The edit that writes the file `path` names from nothing. The checkers read the whole prose of the file in such an
    edit. A byte outside UTF-8 reads as a replacement character, and `ascii_only` refuses that character.

    Public. `check_prose` reads the tree this way.
    """
    with open(path, encoding="utf-8", errors="replace") as handle:
        return _Edit(path, "", handle.read())


def is_about_the_tree(path: str) -> bool:
    """
    Whether the prose of `path` describes the tree. `CHANGELOG.md` records what a change did, and the shape rules skip
    that file.

    A hook may hand over a path relative to the tree. `is_about_the_tree` reads the base name. `is_prose_of_the_project`
    reads it the same way.
    """
    return os.path.basename(path) not in _NOT_ABOUT_THE_TREE


def _fragments_left(one: _Edit) -> list[Fragment]:
    """
    The fragments the file would hold once `one` lands. `fragments_touched` asks about these, and it keeps those whose
    prose the edit changes.

    This function applies no `is_about_the_tree` skip. A caller wanting that skip calls `is_about_the_tree` itself, as
    `prose_rewrite.refusal_for` does. `prose_rules.word_refusal` and `no_tree_numbers.refusal_for` apply no such skip
    either.
    """
    return _fragments_of_source(one.path, one.now)


def fragments_touched(one: _Edit) -> list[Fragment]:
    """
    The fragments whose prose the edit `one` changes. A write-time hook runs its `_PROSE` checkers on the prose of
    these. Its `_ANY` checkers read the whole file.

    A fragment comes back where the edit mints its key. A fragment comes back where its prose digest moves. A fragment
    whose code moved and whose prose held still has no prose to judge.

    A hook reading the whole file refuses an edit for a fault the edit did not write. A file holding many faults then
    takes no edit at all. `check_prose` reads a file git tracks as a `whole` edit. `make verify-prose` runs it under
    `verify`, and `make pc` runs `verify`. `check_prose` hands such a fault to `report`, and `report` fails `make pc`
    over a fault the hook here passes over.
    """
    before = {said.key: said.prose.sha for said in _fragments_of_source(one.path, one.was)}
    return [said for said in _fragments_left(one) if before.get(said.key) != said.prose.sha]


def does_hold_hook_payloads(path: str) -> bool:
    """Whether the strings of `path` are hook payloads rather than prose. `_HOOK_PAYLOADS` names that file."""
    return os.path.basename(path) == os.path.basename(_HOOK_PAYLOADS)


def literals_touched(one: _Edit) -> list[tuple[int, str]]:
    """
    The prose literals `one` writes, as `(the line it opens on, what it says)`.

    `literals_touched` returns a literal whose text the edit writes. A `not-prose:` marker on that line or the line
    above leaves the literal out. An edit above a literal shifts that literal to a new line. Such a literal does not
    come back.

    `does_hold_hook_payloads` tells whether `one.path` is the file whose strings are payloads. `literals_touched` reads
    no string of that file.
    """
    if does_hold_hook_payloads(one.path):
        return []
    language = language_of(one.path) or ""
    before = {said for _at, said in prose_literals(one.was, language)}
    marked = not_prose_lines(one.now)
    return [
        (at, said)
        for at, said in prose_literals(one.now, language)
        if said not in before and at not in marked and at - 1 not in marked
    ]


def _undescribed_fragments(held: list[Fragment]) -> list[str]:
    """The messages naming the fragments that hold no prose."""
    return [f"{one.path}:{one.start_line}: {one.key} holds no prose" for one in held if not one.prose.content]


def _colliding_keys(held: list[Fragment]) -> list[str]:
    """The keys that a fragment shares with some other fragment. A key names a single fragment."""
    seen: dict[str, int] = {}
    for one in held:
        seen[one.key] = seen.get(one.key, 0) + 1
    return sorted(key for key, count in seen.items() if count > 1)


def _keys_not_naming_their_prose(held: list[Fragment]) -> list[str]:
    """
    Messages naming the keys that end on a digest other than the digest of the fragment's prose.

    A fragment with no declaration to name it takes `_named_by`.
    """
    return [
        f"{one.path}:{one.start_line}: {one.key} ends on a name the prose does not give it"
        for one in held
        if _A_DIGEST_NAME.search(one.key) and not one.key.endswith(_named_by(one.prose))
    ]


def main() -> None:
    held = fragments()
    gate.report([f"{key}: fragments share this key" for key in _colliding_keys(held)], "colliding fragment key(s)", "")
    gate.report(_undescribed_fragments(held), "fragment(s) holding no prose", "")
    gate.report(_keys_not_naming_their_prose(held), "fragment key(s) the prose does not name", "")
    if "--json" in sys.argv:
        json.dump([asdict(one) for one in held], sys.stdout, indent=2)
        return
    if "--jsonl" in sys.argv:
        for one in held:
            print(json.dumps(asdict(one)))
        return
    by_kind: dict[str, int] = {}
    for one in held:
        by_kind[one.kind] = by_kind.get(one.kind, 0) + 1
    cited = sum(len(one.references) for one in held)
    print(f"prose: {len(held)} fragment(s) over {len({one.path for one in held})} file(s) holding {cited} reference(s)")
    for kind, count in sorted(by_kind.items()):
        print(f"    {count} {kind}")


if __name__ == "__main__":
    main()
