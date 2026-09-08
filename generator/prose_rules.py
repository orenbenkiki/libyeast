# SPDX-License-Identifier: MIT
"""
The words a sentence may not say, and the shapes it may not take.

The write-time hooks ask this of an edit. `check_prose` asks it of the tree. A single checker answers both. A rule
tightened here reaches the text already in the tree and the next edit alike.

A word fault is a claim. `every-claim-is-checked-before-it-is-written` refuses a universal and a superlative, and a
single counter-example refutes either. `a-count-is-answered-for` refuses the vague quantifier that takes the place of a
number. `use-the-words-this-project-uses` refuses a word the project turned down.
`a-refusal-may-write-the-words-it-bans` spares a hook refusal. Such a refusal ends on the name of the rule it enforces.

A shape fault is a construction. `short-plain-sentences` counts the commas, the words and the third-person pronouns.
That rule refuses a possessive with its noun dropped. It refuses a bare participle where the subject belongs.
`no-em-dash-and-no-colon` refuses those marks, the semicolon and the raised dot. `a-comment-describes-what` refuses a
clause giving WHY. `text-says-what-is` refuses a tense word. `a-sentence-names-its-actor` refuses a sentence opening on
a bare interrogative.

The rules read characters. A trailing absolute clause shows up as a comma and then a participle the sentence ends on. A
verbless clause elsewhere goes to the review. Deciding it wants a part-of-speech tagger this tree does not have. The
passive voice goes to the review.

`fragment_faults` reads a fragment whole, the way a hover or a symbol index shows it. It refuses a first sentence
pointing outside the fragment. It refuses a last sentence with no full stop.

The checker takes a markdown table row a cell at a time. A markdown list item ends where its line ends. The checker
drops a doxygen `@code` block and a doxygen command.

**Usage:** `import prose_rules`, then `prose_rules.word_faults(text)`, `prose_rules.shape_faults(text)` and
`prose_rules.fragment_faults(text)`.
"""

import re

# A claim that one counter-example refutes. English closes the determiners that make one, and the checker names the
# classes rather than the words a writer happened to reach for. A list of words is a game of whack-a-mole. A writer
# refused `the only` writes `the one door`, and a writer refused that writes `the sole door`.
#
# Totality, then uniqueness. A pattern opens on a word boundary. Without the boundary `all of` fired inside "a call of
# it".
#
# `at all` and `after all` are emphasis. `left alone` and `leaves it alone` mean untouched. `the one that` points back
# at a thing already named. A hyphen turns `the single` into a compound adjective, as in `the single-entry mapping`. A
# claim about a class is what stays.
_UNIVERSAL = re.compile(
    r"\bevery\b|\beach\b|(?<!at )(?<!after )(?<!not )\ball\b|\balways\b|\bnever\b|\beverything\b|\beverywhere\b"
    r"|\bwholly\b|\bentirely\b|\bfully\b|\bcompletely\b|\bthe whole of\b|\bthe lot\b"
    r"|\bthe only\b(?!-)|\bthe one (?!that\b|which\b|it\b|in\b|a\b|the\b)(?=[a-z])|\bthe single\b(?!-)"
    r"|\bthe sole\b(?!-)"
    r"|\bexactly one\b|(?<!left )(?<!leave )(?<!leaves )\balone\b"
    r"|\bnothing else\b|\bnothing more\b|\bnothing but\b|\bnothing beside\b|\bno other\b|\bnowhere\b"
    r"|\bthere is no (?:second|third|fourth|fifth|sixth)\b",
    re.IGNORECASE,
)

# A negation in subject position makes the same claim. `No quantity of input bounds it` claims about a class, where `the
# grammar has no say in it` denies a single property of a single thing. The subject tells the two apart, and a sentence
# opens on its subject.
#
# Case matters here. This must not fire on the lower-case `no` and `nothing` of an object.
_UNIVERSAL_SUBJECT = re.compile(r"^(?:No|Nothing|None|Not one|Not a single|Nowhere|Only)\b")

# A superlative. It names a top of a range nothing measured.
_SUPERLATIVE = re.compile(
    r"\bthe (?:hardest|simplest|largest|smallest|best|worst|fastest|slowest|last thing|one thing)\b"
    r"|\bmaximally\b|\bas \w+ as possible\b",
    re.IGNORECASE,
)

# A word written where a number belongs. Such a word names no member and states no count. `at most` and `the most` are
# superlatives rather than counts. The checker looks at the word in front of `most`.
_VAGUE = re.compile(
    r"\ba handful|\bnearly all|\bmost of (?:them|those|it)|\ba fraction of|\ba good (?:many|few)"
    r"|\bseveral of (?:them|those)|\bthe bulk of|\bseveral \w|\ba few \w|\bmultiple \w"
    r"|\bmostly\b|\blargely\b|\bbroadly\b|\broughly\b|\bgenerally\b|\ba small set of|\ba number of|\bsome \w+s\b"
    r"|(?<!\bat )(?<!\bthe )\bmost\b|\bso (?:many|few)\s+\w+s\b|\ba (?:couple|trio) of\b"
    r"|\b(?:dozens|hundreds|thousands|millions)\b",
    re.IGNORECASE,
)

# A clause hung off the end with no verb of its own. The reader gets a noun and a participle and has to decide whether
# it is a cause, a condition or an aside. `, being` and `, <noun> being` are the same shape written outright.
_AN_ABSOLUTE_CLAUSE = re.compile(r",\s+(?:\w+\s+)?being\b|,\s+(?:\w+\s+){0,3}\w+ing\b[^.!?]{0,60}[.!?]?$")

# The verb forms that make a clause. A tail holding one of these is a clause with a verb, whatever else it holds.
_A_FINITE_VERB = re.compile(
    r"\b(?:is|are|was|were|am|be|been|has|have|had|does|do|did|can|may|must|will|would|should|could|takes|holds"
    r"|reads|writes|says|gives|makes|stands|holds|names|answers)\b",
    re.IGNORECASE,
)

# The words that open a clause of their own. A tail led by one of these is a clause rather than a phrase hung off.
_A_SUBORDINATOR = re.compile(r"^(?:and|or|but|which|where|that|who|whose|when|while|if|so|as|than|then)\b", re.I)

# The tail after the last comma, up to the sentence end.
_A_TAIL = re.compile(r",\s*([^,]+?)[.!?]?$")

# The words a tail may run to. Past this the tail reads as a clause rather than a phrase hung off the end. The verb list
# below is a list rather than a checker, and a longer tail with a verb outside it reads as verbless.
_MOST_TAIL_WORDS = 4

# A tense word. A comment says what is, not what was or what is coming.
_A_TENSE_WORD = re.compile(
    r"\bnow\b|\bno longer\b|\bused to\b|\bpreviously\b|\bcurrently\b|\btoday\b|\bso far\b|\bas of\b|\bwill soon\b",
    re.IGNORECASE,
)

# A sentence ending on a verb with nothing after it.
_ENDS_ON_A_COPULA = re.compile(r"\b(?:is|are|was|were|be|been)\s*[.!?]?\s*$", re.IGNORECASE)

# A fragment opening on a word that points outside itself. A reader takes a fragment on its own, through an index or a
# hover.
_A_DEICTIC_OPENER = re.compile(r"^(?:It|They|This|That|These|Those|The same|Likewise|And )\b")

# The markup closing a sentence off. A full stop goes inside the emphasis rather than behind it.
_CLOSING_MARKUP = re.compile(r"[*_`\s]+$")

# A run up to a full stop, the last of a block with none. A stop between word characters belongs to a name such as
# `wire.py`, and a split there cuts a sentence into pieces too short to read.
_SENTENCE = re.compile(r"(?:[^.!?]|[.!?](?=\w))+[.!?]?")

# A name in backticks. The sentence writes it as code rather than as prose. A colon inside a code span is punctuation
# the writer did not choose.
_A_CODE_SPAN = re.compile(r"`[^`]*`")

# A hyphenated name in bold. `.claude/conventions.md` opens an entry with the rule's name written that way. A name is
# not a sentence.
_A_DEFINED_NAME = re.compile(r"\*\*[a-z0-9]+(?:-[a-z0-9]+)+\*\*")

# A sentence-ending mark inside a code span. The mark belongs to the name rather than to the sentence.
_A_STOP = re.compile(r"[.!?]")

# A web address. Its punctuation belongs to the address rather than to the sentence.
_A_URL = re.compile(r"https?://\S+")

# The label a message writes in front of what it says. A gate writes its own name there. `check_emitter` writes
# `Emitter.<field>` and `check_decoder` writes `U+<codepoint>`. A label holds no comma and no full stop between words.
_A_SPEAKER = re.compile(r"^\s*(?:[^.!?,;:\n]|[.!?](?=\S)){0,60}?(?<!\s): ")

# A hook closes a refusal by naming the rule. Such a text writes the words the rule bans.
_STATES_ITS_RULE = re.compile(r"Rule: [a-z0-9]+(?:-[a-z0-9]+)+\.\s*$")

# A place a literal names in front of a message. The literal opens on the file and a colon. The line follows that colon
# where a message cites one, and a second colon may close the place off. A markdown heading may open the literal. The
# colon belongs to the place rather than to the sentence. A comment names no place and gets no such licence.
_A_PLACE = re.compile(r"^\s*(?:#{1,6}\s+)?`[^`]*`:(?:`[^`]*`)?:?(?=\s)")

# A markdown autolink. The angle brackets hold a web address rather than a sentence.
_AN_AUTOLINK = re.compile(r"<https?://[^>]*>")

# A markdown image. The alt text names a badge, and the target is a web address.
_AN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# A markdown link. The text between the brackets is prose. The target after them is a web address.
_A_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

# A directive addressed to a tool rather than to a reader.
_A_DIRECTIVE = re.compile(
    r"\b(?:noqa|pylint|type|fmt|isort|mypy|yapf|nopep8)\s*:\s*\S*|\bfailure-is-reported:|\bnot-a-failure:|\bnot-prose:"
)

# A row of a markdown table. It has no full stop, and a whole table read as one sentence reports the width of the table
# rather than the shape of a sentence. The checker takes a cell on its own.
_A_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")

# A markdown list item. It ends where the line ends and has no full stop.
_AN_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")

# The marks that already end a line. A list lead closes on a colon, and the list under it is not a second clause.
_ENDS_A_LINE = (".", "!", "?", ":")

# A doxygen code block. It holds a program an author cannot reword.
_A_CODE_BLOCK = re.compile(r"@code\b.*?@endcode\b", re.DOTALL)

# A doxygen command. A command naming a thing keeps the name with it, and `@ref`, `@p` and `@param` do that. Any other
# command leads prose, and only the command itself goes. A name ends before the punctuation that closes the sentence
# around it.
_A_DOXYGEN_COMMAND = re.compile(r"@(?:ref|p|param)\s+\S*[^\s.,;:]|@[a-z]+")

# A clause giving WHY. Explaining WHAT the code does is what a comment is for, and this does not touch it.
_EXPLAINS_WHY = re.compile(
    r"\bbecause\b|,\s+so\b|\bso that\b|,\s+since\b|\bwhich is why\b|\bthe reason\b|\bwhat that buys\b"
    r"|\bfor the same reason\b|\bwhich is what\b",
    re.IGNORECASE,
)

# The length the term of a definition may run to. Past this the bold is a sentence rather than a term.
_MOST_TERM_CHARS = 48

# A term and its gloss. The form is `**term:** what it is`. The term leads the line and the item takes a line of its
# own. A list marker may open the line. A gloss is not a sentence.
_A_DEFINITION = re.compile(rf"^(?:[-*+]|\d+\.)?\s*\*\*[^*\n]{{1,{_MOST_TERM_CHARS}}}:\*\*\s+\S")

# The term of a definition, without the gloss under the term.
_A_DEFINITION_TERM = re.compile(rf"^(?:[-*+]|\d+\.)?\s*\*\*[^*\n]{{1,{_MOST_TERM_CHARS}}}:\*\*")

# A definition list item whose body opens on a participle. A noun belongs in that place.
_A_DEFINITION_GERUND = re.compile(rf"^(?:[-*+]|\d+\.)?\s*\*\*[^*\n]{{1,{_MOST_TERM_CHARS}}}:\*\*\s+\w+ing\b")

# The rule around a section divider. A divider is written `--- what the section is ---`. The rule is markup, and the
# words between the rules are the prose.
_A_DIVIDER_RULE = re.compile(r"(?m)^\s*-{3,}(?=\s|$)|(?<=\s)-{3,}\s*$")

# A colon ending a sentence. The text after the colon is the list rather than a second clause.
_A_LIST_LEAD = re.compile(r":\s*\.?\s*$")

# A possessive with the noun dropped, as `that holds for the generator's.` The reader supplies the noun from a
# neighbouring sentence. `own` is an adjective in `libyeast's own grammar`, and the noun follows it. The elision is
# there where `own` ends the phrase.
_AN_ELIDED_NOUN = re.compile(
    r"\b\w+'s\s*[,.]|\b\w+s'\s*[,.]|\b\w+'s\s+(?:the|a|an|to|from|for|of|in|on|by|with)\b|\b\w+'s\s+own\s*[,.]"
)

# A possessive with its noun dropped in front of a conjunction. This checker takes the prose as the author wrote it.
# `said_by` blanks a code span. A blanked span reads as a dropped noun.
_AN_ELIDED_NOUN_BEFORE_A_CONJUNCTION = re.compile(r"\b\w+'s\s+(?:and|or|alike)\b")

# A sentence opening on a bare past participle and joining a full clause. The first half drops its subject.
_AN_ELIDED_SUBJECT = re.compile(
    r"^[A-Z][a-z]+ed\b[^.]*,\s+and\b|^(?!Even\b|Often\b|Then\b|When\b)[A-Z][a-z]+(?:ed|en)\b\s"
)

# The passive voice with no agent named. A form of `be` takes a past participle, and no `by` follows to say who acted.
# `is decided here` and `a slice of a comment is read where it lands` name nobody. A passive that does name its agent
# passes, as in `counted by the way rather than by the item`.
#
# The words below are the irregular participles this tree writes. A regular participle ends in `ed`.
_A_PASSIVE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+(?:(?:not|never|only|also|then|now|still|already|\w+ly)\s+){0,2}"
    r"(?:\w+ed|read|written|held|made|given|put|left|kept|told|said|seen|taken|drawn|known|shown|run|cut|set|sent"
    r"|meant|built|found|lost|met|sold|spent|thrown|torn|worn|won|hidden|driven|broken)\b",
    re.IGNORECASE,
)

# A relative clause hung off the last comma. The sentence has finished, and a second idea rides in behind it. This
# belongs to the tail family. The checker refuses it at any length. `It records what a change did, which stays true as
# the code moves on` is a pair of sentences.
_A_WHICH_TAIL = re.compile(r",\s+which\b")

# The word that introduces the agent of a passive. It follows the participle, and a search starts there. `by default`
# and `by hand` name a manner rather than an actor.
_NAMES_AN_AGENT = re.compile(r"\bby\b(?!\s+(?:default|hand|then|now|contrast|turns|itself|design)\b)", re.IGNORECASE)

# A sentence opening on a bare interrogative. The writer put a nominalized clause where the actor belongs, as in "What
# libyeast adds cannot quietly become what libyeast changes". Naming the actor gives "an edit that meant to add
# something must not turn out to have changed an official production". `Whether` opens the docstring of a predicate and
# passes. `When` opens a fronted condition and passes.
_A_NOMINAL_SUBJECT = re.compile(r"^(?:(?:So|And|But|Yet|Then),?\s+)?(what|how|which|where)\b", re.IGNORECASE)

# A word listed here does not appear in this project's prose. A word arrives here after somebody wrote it and I turned
# the word down. This list is where the ruling lives.
#
# `meet` is banned outright. A reader opens a fragment. Say `intersected with` for the lattice sense. A thing satisfies
# a requirement.
#
# `answers to` is banned as a verb. A rule covers a text. A name names something in the tree. A script follows a rule.
# The noun passes. This spares `an answer to a question` and `the leaf's answer to them`.
#
# `carry` is banned outright. Say the verb the thing actually does. A file states its description. A list names its
# items. A parse continues at its tail call.
#
# `spell` is banned outright. A grammar writes a production. A source holds a name. Say which form a thing takes.
#
# `by itself` is banned outright. The phrase came in as a way past the ban on `alone`, and it says little. Say `only`
# where the point is that one thing takes part. Name the thing that is missing where the point is an absence. Delete the
# phrase where it does neither.
#
# `stand` is banned outright. `stands`, `standing`, `standings` and `stood` go with it. The word says nothing on its
# own. Say the verb the thing does. A rule passes. A comment holds a name. A line follows another. A file is in the
# tree.
#
# `the rest` is banned. `the one`, `the ones` and `the others` go with it. The phrase points at a noun instead of saying
# it. Name the noun. An `of` after the phrase names the noun. `the rest of the queue` passes.
_NOT_OUR_WORD = re.compile(
    r"\bmeets?\b|\bmet\b|\bmeeting\b"
    r"|\banswers\s+to\b|\banswering\s+to\b"
    r"|(?<!an )(?<!the )(?<!no )(?<!same )(?<!'s )\banswer\s+to\b"
    r"|\b(?:carry|carries|carried|carrying)\b"
    r"|\b(?:spell|spells|spelled|spelt|spelling|spellings|respelled)\b"
    r"|\bby\s+itself\b"
    r"|\b(?:stand|stands|standing|standings|stood)\b"
    r"|\bthe\s+(?:ones?|others|rest)\b(?!\s+of\b)",
    re.IGNORECASE,
)

# The third-person pronouns. A demonstrative stays out of this checker. `that` is a relative pronoun far more often than
# a demonstrative, and no character rule separates the two.
_A_PRONOUN = re.compile(r"\b(?:it|its|they|them|their)\b", re.IGNORECASE)

# A verb the sentence drops and the reader supplies. "that file would hold and this one does not" ends on the negation
# and leaves the verb behind.
_AN_ELIDED_VERB = re.compile(
    r"\b(?:does|do|did|is|are|was|were|has|have|had|can|will|would|should|could|may|must)\s+not\s*[.,!?]",
    re.IGNORECASE,
)

# The word that joins the last item of a list. `_items_of` reads the word at the head of the last segment or inside that
# segment.
_JOINS_A_LAST_ITEM = re.compile(r"\b(?:and|or)\b", re.IGNORECASE)

# The items a list in a single sentence may run to. Past this the reader is holding a table in their head.
_MOST_ITEMS = 3

# The commas a single sentence may hold. Past this the clauses are a pile.
_MOST_COMMAS = 2

# The words a single sentence may run to.
_MOST_WORDS = 26

# The third-person pronouns a single sentence may hold. A second pronoun makes the reader match a second referent.
# Naming the noun is shorter than pointing at it.
_MOST_PRONOUNS = 1

# The shortest sentence the shape rules read. A short label states a single idea whatever its punctuation.
_FEWEST_WORDS = 6


def _items_of(said: str) -> int:
    """
    The items the list in `said` runs to.

    The commas cut the sentence into segments. A last segment opening on `and` or `or` closes an Oxford list, and the
    segments are the items. A last segment with `and` or `or` inside it joins the final item without a comma, and that
    segment holds a pair of items.
    """
    segments = [one.strip() for one in said.split(",")]
    if len(segments) < 2:
        return 1
    if _JOINS_A_LAST_ITEM.match(segments[-1]):
        return len(segments)
    return len(segments) + 1 if _JOINS_A_LAST_ITEM.search(segments[-1]) else 1


def _celled(line: str) -> str:
    """`line` with a table row's cell boundaries said as full stops, and any other line unchanged."""
    return line.replace("|", ".") if _A_TABLE_ROW.match(line) else line


def _does_continue(below: str) -> bool:
    """Whether `below` continues the list item above it."""
    return bool(below.strip()) and below[:1].isspace() and not _AN_ITEM.match(below)


def _itemized(line: str, below: str) -> str:
    """
    `line` with a list item's end said as a full stop, and any other line unchanged.

    An item wraps at the column limit, and `below` is the line under it. A sentence therefore runs past the end of the
    line an item opens on. The full stop goes at the end of the item rather than at the end of that line.
    """
    ended = line.rstrip()
    if not _AN_ITEM.match(line) or not ended or ended.endswith(_ENDS_A_LINE) or _does_continue(below):
        return line
    return f"{ended}."


def said_by(prose: str) -> str:
    """`prose` with what it writes as code blanked. The answer is a single line."""
    named = _A_DEFINED_NAME.sub(" ", prose)
    without_code = _A_CODE_SPAN.sub(" ", _A_CODE_BLOCK.sub(" ", named))
    without_links = _A_LINK.sub(r"\1", _AN_IMAGE.sub(" ", _AN_AUTOLINK.sub(" ", without_code)))
    blanked = _A_DIRECTIVE.sub(" ", _A_URL.sub(" ", _A_DOXYGEN_COMMAND.sub(" ", without_links)))
    without_dividers = _A_DIVIDER_RULE.sub(" ", blanked)
    said = [_celled(line) for line in without_dividers.splitlines()]
    read = "\n".join(_itemized(line, said[at + 1] if at + 1 < len(said) else "") for at, line in enumerate(said))
    return " ".join(read.split())


def sentences(prose: str) -> list[str]:
    """
    The sentences `prose` writes, a line apiece, with the code spans intact.

    The split keeps a name in backticks. A pair of sentences differing only in the name they cite are about different
    things. A checker that blanks the name reports them as the same sentence written twice.

    A full stop inside a code span belongs to the name. The split skips it.
    """
    whole = " ".join(prose.split())
    unsplit = _A_CODE_SPAN.sub(lambda found: _A_STOP.sub("\x00", found.group(0)), whole)
    return [said for said in (whole[at.start() : at.end()].strip() for at in _SENTENCE.finditer(unsplit)) if said]


def words_found(prose: str) -> dict[str, list[str]]:
    """
    `{what the fault is called: the words that made it}`, over the sentences `prose` writes.

    Read a sentence at a time. `_UNIVERSAL_SUBJECT` wants a sentence to open on, and the whole prose flattened to a
    single line offers a single opening.
    """
    found: dict[str, set[str]] = {
        "universal": set(),
        "superlative": set(),
        "vague quantifier": set(),
        "word we do not use": set(),
    }
    for sentence in sentences(said_by(prose)):
        for what, pattern in (
            ("universal", _UNIVERSAL),
            ("universal", _UNIVERSAL_SUBJECT),
            ("superlative", _SUPERLATIVE),
            ("vague quantifier", _VAGUE),
            ("word we do not use", _NOT_OUR_WORD),
        ):
            found[what] |= {one.group(0).lower().strip() for one in pattern.finditer(sentence)}
    return {what: sorted(said) for what, said in found.items()}


def word_faults(prose: str) -> list[str]:
    """The words in `prose` that leave a sentence unfalsifiable, as `[what it is called] the word`."""
    return [f"[{what}] {one}" for what, said in sorted(words_found(prose).items()) for one in said]


def fragment_faults(prose: str) -> list[str]:
    """
    The faults `prose` has as a whole fragment rather than as a sentence.

    A reader opens a fragment on its own, through a symbol index or a hover. A first sentence opening on `It` or `The
    same` leans on a neighbour the index withholds. A last sentence with no full stop runs into whatever follows.
    Emphasis around that sentence is markup, and the full stop goes inside it.
    """
    said = [one for one in sentences(said_by(prose)) if _CLOSING_MARKUP.sub("", one)]
    if not said:
        return []
    found = []
    opener = _A_DEICTIC_OPENER.match(said[0])
    if opener:
        found.append(f"a fragment opening on a word pointing outside it: {opener.group(0).strip()!r}")
    if not _CLOSING_MARKUP.sub("", said[-1]).endswith((".", "!", "?")):
        found.append(f"a fragment whose last sentence has no full stop: {said[-1][-40:]!r}")
    return found


def _hung_tail(said: str) -> str | None:
    """
    The tail `said` hangs off its last comma with no verb, or None.

    A tail with a finite verb is a clause and passes. A tail opening on `and`, `which` or `that` is a clause too. A
    short tail with neither is a phrase the reader has to attach on their own. The appositive falls out of this. So does
    the trailing absolute, and so does the verb elided after a conjunction.
    """
    found = _A_TAIL.search(said)
    if not found:
        return None
    tail = found.group(1).strip()
    if not tail or len(tail.split()) > _MOST_TAIL_WORDS:
        return None
    if _A_FINITE_VERB.search(tail) or _A_SUBORDINATOR.match(tail):
        return None
    return tail


def _over(said: str, is_stating_why: bool = False) -> list[str]:
    """The shape faults of a single sentence. A text stating a reason gets the WHY clause skipped."""
    over = []
    why = None if is_stating_why else _EXPLAINS_WHY.search(said)
    if why:
        over.append(f"a clause giving WHY: {why.group(0).strip()!r}")
    if ";" in said:
        over.append("a semicolon joining two clauses")
    if ":" in _A_LIST_LEAD.sub("", _A_DEFINITION_TERM.sub("", said)):
        over.append("a colon")
    hung = _AN_ABSOLUTE_CLAUSE.search(said)
    if hung:
        over.append(f"a clause hung off the end with no verb: {hung.group(0).strip()!r}")
    tense = _A_TENSE_WORD.search(said)
    if tense:
        over.append(f"a word about when rather than what: {tense.group(0).strip()!r}")
    if _ENDS_ON_A_COPULA.search(said):
        over.append("a sentence ending on a verb with nothing after it")
    if _A_DEFINITION_GERUND.match(said):
        over.append("a definition whose body opens on a participle")
    hanging = _hung_tail(said)
    if hanging:
        over.append(f"a phrase hung off the last comma with no verb: {hanging!r}")
    if _A_WHICH_TAIL.search(said):
        over.append("a relative clause hung off the last comma")
    passive = _A_PASSIVE.search(said)
    if passive and not _NAMES_AN_AGENT.search(said, passive.end()):
        over.append(f"a passive naming no actor: {passive.group(0).strip()!r}")
    dropped = _AN_ELIDED_NOUN.search(said)
    if dropped:
        over.append(f"a possessive with its noun dropped: {dropped.group(0).strip()!r}")
    if _AN_ELIDED_SUBJECT.match(said):
        over.append("a bare participle where the subject belongs")
    if _AN_ELIDED_VERB.search(said):
        over.append("a verb the reader has to supply after the negation")
    pronouns = len(_A_PRONOUN.findall(said))
    if pronouns > _MOST_PRONOUNS:
        over.append(f"{pronouns} third-person pronouns")
    items = _items_of(said)
    if items > _MOST_ITEMS:
        over.append(f"a list of {items} items")
    if not _A_DEFINITION.match(said):
        if said.count(",") > _MOST_COMMAS:
            over.append(f"{said.count(',')} commas")
        if len(said.split()) > _MOST_WORDS:
            over.append(f"{len(said.split())} words")
    return over


def is_stating_its_rule(said: str) -> bool:
    """
    Whether `said` is a refusal that ends by naming the rule it enforces.

    A hook writes the words its rule bans. A hook quotes the wording it refuses. The word rules would refuse such a
    refusal.
    """
    return bool(_STATES_ITS_RULE.search(said))


def literal_faults(said: str) -> list[str]:
    """
    The faults a string literal holds.

    A literal may name what speaks before it speaks. A gate writes `conventions: what a gate can decide holds`, and the
    colon there is the form rather than an appositive. A literal may name a place the same way. A comment names no
    speaker and no place, and gets no such licence.

    `literal_shape_faults` holds the shape half. The `prose_rewrite` hook asks for that half, and the two then read a
    literal alike.
    """
    return word_faults(said) + literal_shape_faults(said)


def literal_shape_faults(said: str) -> list[str]:
    """The shape faults a string literal holds, with the label and the place it opens on blanked."""
    return shape_faults(_A_PLACE.sub(" ", _A_SPEAKER.sub(" ", said, count=1), count=1))


def refused_sentences(prose: str, is_stating_why: bool = False) -> list[str]:
    """
    The sentences of `prose` a shape rule refuses, with no fault named beside them.

    A caller quotes these at a writer that holds no checker of its own.
    """
    held = [said.partition("] ")[2] for said in shape_faults(prose, is_stating_why)]
    return [one for one in dict.fromkeys(held) if one]


def shape_faults(prose: str, is_stating_why: bool = False) -> list[str]:
    """
    The sentences of `prose` shaped past what a single pass reads.

    `_over` reads only a sentence running past `_FEWEST_WORDS`. A short sentence states a single idea whatever its
    punctuation. A nominalized subject counts at any length. `What the checker answers.` drops the actor and falls under
    `_FEWEST_WORDS`.

    `is_stating_why` passes a text whose whole job is a reason. A proposal's `why` field is one. The WHY clause goes
    unread there, and the rest of the shape rules apply.
    """
    whole = said_by(prose)
    found = [
        f"[a possessive with its noun dropped: {said.group(0).strip()!r}] {said.group(0).strip()}"
        for said in _AN_ELIDED_NOUN_BEFORE_A_CONJUNCTION.finditer(prose)
    ]
    for sentence in _SENTENCE.findall(whole):
        said = sentence.strip()
        over = _over(said, is_stating_why) if len(said.split()) >= _FEWEST_WORDS else []
        nominal = _A_NOMINAL_SUBJECT.match(said)
        if nominal:
            over.append(f"a nominalized clause opening on {nominal.group(1)!r} where the actor belongs")
        if over:
            found.append(f"[{'; '.join(over)}] {said}")
    return found
