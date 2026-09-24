# SPDX-License-Identifier: MIT
"""
The words a sentence may not say, and the shapes it may not take.

The write-time hooks ask this of an edit. `check_prose` asks it of the tree. A single checker answers both. A rule
tightened here reaches the text already in the tree and the next edit alike.

A word fault is a claim. `a-claim-writes-no-universal` refuses a universal and a superlative, and a single
counter-example refutes either. `a-count-is-answered-for` refuses the vague quantifier that takes the place of a number.
`use-the-words-this-project-uses` refuses a word the project turned down. `a-refusal-may-write-the-words-it-bans` spares
a hook refusal. Such a refusal ends on the name of the rule it enforces. `a-named-hedge-is-refused` refuses the hedges
the word list names. `a-reference-parser-has-one-name` refuses a loose name for either upstream parser.
`no-discovery-narration` refuses `turns out` and `turned out`. `a-plan-step-is-not-named-by-a-numeral` refuses a step
named by a numeral. `a-so-clause-is-a-why` refuses a purpose clause riding on `so`. `a-dotted-name-sits-in-a-code-span`
refuses a bare path.

`a-whose-clause-is-a-hung-tail` refuses a relative clause hung off a comma on `whose`. `a-possessive-owns-a-noun`
refuses a sentence ending on a possessive. `a-sentence-does-not-define-by-itself` refuses a word repeated across `what`.
`a-fragment-opens-on-its-own-referent` refuses `such a` in a fragment's opening sentence. `a-code-span-hides-no-passive`
reads a code span as a word, and a passive behind such a span names its actor.

`a-free-choice-word-is-a-universal` refuses `whatever`. `a-purpose-clause-says-no-action` refuses `exists for` and
`exists to`. `a-pointer-names-its-referent` refuses the runs `That last` and `This last`.
`a-name-of-the-tree-sits-in-a-code-span` refuses a name written outside the backticks. `a-question-word-is-no-noun`
refuses a free relative behind a preposition. `a-sentence-states-no-plan` refuses a sentence ending on `yet`.
`a-marked-up-word-is-read-as-a-word` takes the emphasis off a word before the rules above read it.

A shape fault is a construction. `a-sentence-stays-within-the-limits` counts the commas, the words and the third-person
pronouns. That rule refuses a possessive with its noun dropped. It refuses a bare participle where the subject belongs.
`no-em-dash-and-no-colon` refuses those marks, the semicolon and the raised dot. `a-comment-writes-no-why-clause`
refuses a clause giving WHY. `text-says-what-is` refuses a tense word. `a-sentence-opens-on-a-subject` refuses a
sentence opening on a bare interrogative. `a-sentence-hangs-a-single-tail` refuses a modifier stacked behind another.

The rules read characters. A trailing absolute clause shows up as a comma and then a participle the sentence ends on. A
verbless clause elsewhere goes to the review. Deciding it wants a part-of-speech tagger this tree does not have. The
passive voice goes to the review.

`prose_faults` reads a fragment whole, the way a hover or a symbol index shows it. It refuses a first sentence pointing
outside the fragment. It refuses a last sentence with no full stop. `a-run-of-pronouns-reaches-back-to-a-noun` refuses a
run of pronoun openers that reaches back to a demonstrative. `a-paragraph-opens-on-a-noun` refuses a paragraph opening
on a pronoun.

The checker takes a markdown table row a cell at a time. A markdown list item ends where its line ends. The checker
drops a doxygen `@code` block and a doxygen command.

**Usage:** `import prose_rules`. Then call `prose_rules.word_refusal(text, path)` and `prose_rules.prose_faults(text)`.
"""

import os
import re

# A claim that one counter-example refutes. English has a closed set of determiners that make such a claim. The checker
# names the classes of those determiners rather than the words a writer happened to reach for. A list of words is a game
# of whack-a-mole. A writer refused `the only` writes `the one door`, and a writer refused that writes `the sole door`.
#
# The patterns take totality first and uniqueness second. A pattern opens on a word boundary. Without the boundary `all
# of` fired inside "a call of it".
#
# `at all` and `after all` are emphasis. `left alone` and `leaves it alone` mean untouched. `the one that` points back
# at a thing already named. A hyphen turns `the single` into a compound adjective, as in `the single-entry mapping`. The
# checker still refuses a claim about a class.
#
# `whatever` is the free-choice word, and `a-free-choice-word-is-a-universal` refuses it. A free relative naming a thing
# takes the noun instead, as `whatever is on top` says `the entry on top`.
_UNIVERSAL = re.compile(
    r"\bwhatever\b|\bwhichever\b"
    r"|\bevery\b|\beach\b|(?<!at )(?<!after )(?<!not )\ball\b|\balways\b|\bnever\b|\beverything\b|\beverywhere\b"
    r"|\bwholly\b|\bentirely\b|\bfully\b|\bcompletely\b|\bthe whole of\b|\bthe lot\b"
    r"|\bthe only\b(?!-)|\bthe one (?!that\b|which\b|it\b|in\b|a\b|the\b)(?=[a-z])|\bthe single\b(?!-)"
    r"|\bthe sole\b(?!-)"
    r"|\bexactly one\b|(?<!left )(?<!leave )(?<!leaves )\balone\b"
    r"|\bnothing else\b|\bnothing more\b|\bnothing but\b|\bnothing beside\b|\bno other\b|\bnowhere\b"
    r"|\bthere is no (?:second|third|fourth|fifth|sixth)\b",
    re.IGNORECASE,
)

# A negation in subject position makes a claim about a class. `No quantity of input bounds it` is such a claim, where
# `the grammar has no say in it` denies a single property of a single thing. The subject tells a claim about a class
# from a claim about a thing, and a sentence opens on its subject.
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
    r"|\b(?:dozens|hundreds|thousands|millions)\b"
    r"|\bthe (?:two|three|four)\b",
    re.IGNORECASE,
)

# A clause hung off the end with no verb of its own. The reader gets a noun and a participle and has to decide whether
# it is a cause, a condition or an aside. `, being` and `, <noun> being` are the same shape written outright.
_AN_ABSOLUTE_CLAUSE = re.compile(r",\s+(?:\w+\s+)?being\b|,\s+(?:\w+\s+){0,3}\w+ing\b[^.!?]{0,60}[.!?]?$")

# The verb forms that make a clause. A tail holding one of these is a clause with a verb, and the words beside it make
# no difference.
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

# A fragment opening on a word that points outside itself. A reader takes a fragment out of an index or a hover.
_A_DEICTIC_OPENER = re.compile(r"^(?:It|They|This|That|These|Those|The same|Likewise|And )\b")

# A sentence opening on a third-person pronoun. A run of these reads as a list under a subject the prose named earlier.
_A_PRONOUN_OPENER = re.compile(r"^(?:It|Its|They|Their)\b")

# A sentence opening on a word that points back rather than naming. Such a sentence anchors no run of pronoun openers.
# `Such a node` names the noun and passes.
_A_BACK_POINTER = re.compile(r"^(?:It|Its|They|Their|That|Those|This|These)\b")

# The pronoun openers in a row before the run wants a named subject behind it.
_FEWEST_IN_A_RUN = 2

# The markup closing a sentence off. A full stop goes inside the emphasis rather than behind it.
_CLOSING_MARKUP = re.compile(r"[*_`\s]+$")

# A run up to a full stop, or up to the end of a block that holds no stop. A stop between word characters belongs to a
# name such as `wire.py`, and the split skips it. A stop in front of another stop opens an ellipsis or a range, and the
# split skips it as well.
_SENTENCE = re.compile(r"(?:[^.!?]|[.!?](?=[\w.]))+[.!?]?")

# A name in backticks. The sentence writes it as code rather than as prose. A colon inside a code span is punctuation
# the writer did not choose.
_A_CODE_SPAN = re.compile(r"`[^`]*`")

# A hyphenated name in bold. `.claude/conventions.md` opens an entry with the rule's name written that way. A name is
# not a sentence.
_A_DEFINED_NAME = re.compile(r"\*\*[a-z0-9]+(?:-[a-z0-9]+)+\*\*")

# The word a code span leaves behind. A shape rule then reads a noun in place of the name. A gap would lose the subject
# of a sentence.
_A_NAME = "name"

# A sentence-ending mark inside a code span. The mark belongs to the name rather than to the sentence.
_A_STOP = re.compile(r"[.!?]")

# A web address. Its punctuation belongs to the address rather than to the sentence.
_A_URL = re.compile(r"https?://\S+")

# The label a message writes in front of its text. A gate writes its own name there. `check_emitter` writes
# `Emitter.<field>` and `check_decoder` writes `U+<codepoint>`. A label holds no comma and no full stop between words.
_A_SPEAKER = re.compile(r"^\s*(?:[^.!?,;:\n]|[.!?](?=\S)){0,60}?(?<!\s): ")

# A hook closes a refusal by naming the rule. Such a text writes the words the rule bans.
_STATES_ITS_RULE = re.compile(r"Rule: [a-z0-9]+(?:-[a-z0-9]+)+\.\s*$")

# A place a literal names in front of a message. The literal opens on the file and a colon. The line follows that colon
# where a message cites one, and a second colon may close the place off. A markdown heading may open the literal. The
# colon belongs to the place rather than to the sentence. A comment names no place, and a colon in a comment still
# counts against its sentence.
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
# rather than the shape of a sentence. The checker takes a cell.
_A_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")

# A markdown list item. It ends where the line ends and has no full stop.
_AN_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")

# The marks that already end a line. A list lead closes on a colon, and the list under it is not a second clause.
_ENDS_A_LINE = (".", "!", "?", ":")

# A doxygen code block. It holds a program an author cannot reword.
_A_CODE_BLOCK = re.compile(r"@code\b.*?@endcode\b", re.DOTALL)

# A doxygen command. A command naming a thing keeps the name with it. `@ref`, `@p` and `@param` do that, and `@file`
# names the source file it documents. Any other command leads prose, and the pattern drops the command and keeps the
# prose. A name ends before the punctuation that closes the sentence around it.
_A_DOXYGEN_COMMAND = re.compile(r"@(?:ref|p|param|file)\s+\S*[^\s.,;:]|@[a-z]+")

# A clause giving WHY. A comment explains WHAT the code does, and this pattern does not touch such a comment.
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
    rf"(?:{_A_NAME}\s+)?"
    r"(?:\w+ed|read|written|held|made|given|put|left|kept|told|said|seen|taken|drawn|known|shown|run|cut|set|sent"
    r"|meant|built|found|lost|met|sold|spent|thrown|torn|worn|won|hidden|driven|broken|got|gotten)\b",
    re.IGNORECASE,
)

# A relative clause hung off the last comma. The sentence has finished, and a second idea rides in behind it. This
# belongs to the tail family. The checker refuses it at any length. `It records what a change did, which stays true as
# the code moves on` is a pair of sentences.
_A_WHICH_TAIL = re.compile(r",\s+which\b")

# A relative clause hung off a comma on `whose`. The reader walks back to find the noun the clause is about. This
# belongs to the tail family beside `_A_WHICH_TAIL`.
_A_WHOSE_TAIL = re.compile(r",\s+whose\b")

# A sentence ending on a possessive. The noun the thing owns is gone, and an edit ate it. `A lookahead's.` is such a
# docstring.
_ENDS_ON_A_POSSESSIVE = re.compile(r"\w's\s*[.!?]\s*$")

# A pointer opening a fragment. A reader who opens the fragment cold finds no earlier sentence naming what the pointer
# points at.
_A_SUCH_POINTER = re.compile(r"\bsuch an?\b", re.IGNORECASE)

# A step named by a numeral. The number moves when somebody adds a step, and the prose then points at another step.
_NAMES_A_STEP_BY_A_NUMERAL = re.compile(r"\b(?:phase|step|stage)\s+\d", re.IGNORECASE)

# A purpose clause riding on `so`. The clause gives the reader WHY an action happens where WHAT happens next belongs.
# `do so` and `says so` put `so` in place of a verb, and the other forms of those verbs do the same. `so that` opens a
# clause of its own.
_A_SO_CLAUSE = re.compile(
    r"(?<!\bdo)(?<!\bdoes)(?<!\bdid)(?<!\bdoing)(?<!\bsay)(?<!\bsays)(?<!\bsaid)(?<!\bsaying)\s+so\s+(?!that\b)",
    re.IGNORECASE,
)

# A path, a scope and a dotted name outside a code span. `said_by` blanks a URL and takes a link's target off. This
# pattern reads the prose left after that. `check_documents` verifies a backticked name, and a bare path gets past that
# gate. A slash right after an opening angle bracket closes a markup tag rather than dividing a path.
_A_BARE_PATH = re.compile(
    rf"(?<![\w./:-])(?!e\.g|i\.e)(?!(?:{_A_NAME}[/.])+{_A_NAME}\b)"
    r"[\w-]*(?:(?<!<)/[\w.-]*\w[\w./-]*|::\w+|\.[a-zA-Z]\w*)"
)

# A word listed here says nothing when it repeats across a `what`. A content word repeated there defines a thing in its
# own terms.
_A_FUNCTION_WORD = frozenset(
    "a an the of to in on for and or is are was were it its that this these those what who how when where they them "
    "he she we you as at by be been being with from not no but if then than so such each any all one two more most "
    "own same other another there here which whose will would can could may might must shall should do does did done "
    "have has had".split()
)


def _defined_in_its_own_terms(said: str) -> str:
    """
    The content word `said` repeats on both sides of a `what`. The answer is empty where no word repeats there.

    A sentence defining a word by repeating it tells the reader nothing. `_A_FUNCTION_WORD` names the words a repeat
    says nothing about.
    """
    for found in re.finditer(r"\b(\w{4,})\b[^.]{0,35}?\bwhat\b[^.]{0,35}?\b(\w{4,})\b", said, re.IGNORECASE):
        ahead, behind = found.group(1).lower(), found.group(2).lower()
        if ahead == behind and ahead not in _A_FUNCTION_WORD:
            return ahead
    return ""


# A modifier hung off a comma. `_hung_tail` reads the tail after the last comma and stops there. A sentence stacking a
# modifier behind another passes that checker. A reader tracks such a modifier to the word it lands on. A stacked
# modifier sends the reader back through the clause. `a-sentence-hangs-a-single-tail` is the rule.
_A_STACKED_TAIL = re.compile(r",\s+(with|behind|without)\b", re.IGNORECASE)

# The modifiers a single sentence may hang off its commas.
_MOST_TAILS = 1

# The word that introduces the agent of a passive. It follows the participle, and a search starts there. `by default`
# and `by hand` name a manner rather than an actor.
_NAMES_AN_AGENT = re.compile(r"\bby\b(?!\s+(?:default|hand|then|now|contrast|turns|itself|design)\b)", re.IGNORECASE)

# A sentence opening on a bare interrogative. The writer put a nominalized clause where the actor belongs, as in "What
# libyeast adds cannot quietly become what libyeast changes". Naming the actor gives "an edit that meant to add
# something must not turn out to have changed an official production". `Whether` opens the docstring of a predicate and
# passes. `When` opens a fronted condition and passes.
_A_NOMINAL_SUBJECT = re.compile(r"^(?:(?:So|And|But|Yet|Then),?\s+)?(what|how|which|where)\b", re.IGNORECASE)

# A word listed here does not appear in this project's prose. A word arrives here after somebody wrote it and the author
# turned the word down. This list is where the ruling lives.
#
# This list bans `meet` outright. A reader opens a fragment. Say `intersected with` for the lattice sense. A thing
# satisfies a requirement.
#
# This list bans `answer to` outright, and it bans `answer for` the same way. A rule covers a text. A name names
# something in the tree. A script follows a rule. A default supplies the parameter a caller omits. A handler reports its
# own failure.
#
# This list bans `hold` followed straight by `to`. A text satisfies a convention. A header matches what the grammar
# produces. A case agrees with what the suite says. `holds this module to the roster` names its object, and the ban
# passes that wording.
#
# This list bans `carry` outright. Say the verb the thing actually does. A file states its description. A list names its
# items. A parse continues at its tail call.
#
# This list bans `spell` outright. A grammar writes a production. A source holds a name. Say which form a thing takes.
#
# This list bans `by itself` outright. `on its own`, `on their own` and `of its own accord` go with it. A phrase here
# came in as a way past the ban in front of it, and the chain runs back to `alone`. The phrase says little. Say `only`
# where the point is that one thing takes part. Name the thing that is missing where the point is an absence. Delete the
# phrase where it does neither. A possessive wants another form. `on a line of its own` says what `on its own line`
# said.
#
# This list bans `if desired` outright. `as needed`, `as appropriate` and `where appropriate` go with it. A hedge reads
# as a condition and states none. A hedge names nobody who decides. Say who chooses and on what, or say the claim
# without the hedge.
#
# This list bans a bare `YamlReference`. `the YAML reference parser` goes with it. A pair of upstream projects go by
# both forms. Write `Haskell YamlReference` for the vendored Haskell parser. Write `YAML Reference Parser` for the
# spec-generated one. `a-reference-parser-has-one-name` is the rule.
#
# This list bans `stand` outright. `stands`, `standing` and `standings` go with it. So does `stood`. The word says
# nothing. Say the verb the thing does. A rule passes. A comment holds a name. A line follows another. A file is in the
# tree.
#
# This list bans `the rest`. `the one`, `the ones` and `the others` go with it. The phrase points at a noun instead of
# saying it. Name the noun. An `of` after the phrase names the noun. `the rest of the queue` passes.
_NOT_OUR_WORD = re.compile(
    r"\bmeets?\b|\bmet\b|\bmeeting\b"
    r"|\banswer(?:s|ed|ing)?\s+(?:to|for)\b"
    r"|\b(?:hold|holds|holding|held)\s+to\b"
    r"|\b(?:carry|carries|carried|carrying)\b"
    r"|\b(?:spell|spells|spelled|spelt|spelling|spellings|respelled)\b"
    r"|\bby\s+itself\b|\bon\s+(?:its|their)\s+own\b|\bof\s+(?:its|their)\s+own\s+accord\b"
    r"|\bif\s+desired\b|\bas\s+needed\b|\b(?:as|where)\s+appropriate\b|\bin\s+general\b"
    r"|(?<!Haskell )\bYamlReference\b|(?-i:\bthe\s+YAML\s+reference\s+parser\b)"
    r"|\b(?:stand|stands|standing|standings|stood)\b"
    r"|\bturn(?:s|ed)\s+out\b"
    r"|\bthe\s+(?:ones?|others|rest)\b(?!\s+of\b)",
    re.IGNORECASE,
)

# A free relative after a preposition. The sentence points at a thing rather than naming it. `to what the tree bears
# out` points, and `to the entries the tree bears out` names.
_A_POINTING_RELATIVE = re.compile(
    r"\b(?:to|of|for|about|through)\s+what\b|\b(?:is|are|was|were)\s+what\b", re.IGNORECASE
)

# A clause ending on `yet`. Such a clause points at work the project still owes, and `PLAN.md` holds that work. A
# trailing `yet` sits before a comma as often as before a full stop. A conjunction `yet` opens a clause and sits before
# neither.
_POINTS_AT_A_PLAN = re.compile(r"\byet\s*[.!?,]")

# A clause saying what a thing is there for. It gives WHY where the question asked WHAT.
_A_PURPOSE_CLAUSE = re.compile(r"\bexists\s+(?:for|to)\b", re.IGNORECASE)

# A pointer at the thing named last. The prose names the rule, the file or the option instead.
_POINTS_AT_A_LAST_THING = re.compile(r"\b(?:that|this) last\b", re.IGNORECASE)

# A name of the tree written outside a code span. `said_by` blanks the code spans, the links and the URLs. An underscore
# between letters remains.
_AN_UNMARKED_NAME = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*[A-Za-z0-9]\b")

# The emphasis markers around a word. A rule below matches the word without them.
_EMPHASIS = re.compile(r"\*+")

# A paragraph break. The referent of a pronoun opening the paragraph sits across that break.
_A_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

# The third-person pronouns. A demonstrative stays out of this checker. `that` is a relative pronoun far more often than
# a demonstrative, and no character rule separates a relative pronoun from a demonstrative.
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

# The shortest sentence the shape rules read. A short label states a single idea under any punctuation.
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
    """`prose` with what it writes as code blanked. The answer is a single line. A code span leaves `_A_NAME` behind."""
    named = _A_DEFINED_NAME.sub(" ", prose)
    without_code = _A_CODE_SPAN.sub(_A_NAME, _A_CODE_BLOCK.sub(" ", named))
    without_links = _A_LINK.sub(r"\1", _AN_IMAGE.sub(" ", _AN_AUTOLINK.sub(" ", without_code)))
    blanked = _A_DIRECTIVE.sub(" ", _A_URL.sub(" ", _A_DOXYGEN_COMMAND.sub(" ", without_links)))
    without_dividers = _A_DIVIDER_RULE.sub(" ", blanked)
    said = [_celled(line) for line in without_dividers.splitlines()]
    read = "\n".join(_itemized(line, said[at + 1] if at + 1 < len(said) else "") for at, line in enumerate(said))
    return " ".join(read.split())


def _sentences(prose: str) -> list[str]:
    """
    The sentences `prose` writes, a line apiece, with the code spans intact.

    A full stop inside a code span belongs to the name. The split skips it.
    """
    whole = " ".join(prose.split())
    unsplit = _A_CODE_SPAN.sub(lambda found: _A_STOP.sub("\x00", found.group(0)), whole)
    return [said for said in (whole[at.start() : at.end()].strip() for at in _SENTENCE.finditer(unsplit)) if said]


# The word rules, by the name a fault goes under. A class names the patterns that find the fault. A class names the
# refusal a write-time hook gives too. A pattern therefore lives beside its refusal. `words_found` reads the patterns.
# `word_refusal` reads the wording. A class added here reaches both readers.
_A_WORD_RULE: dict[str, tuple[tuple[re.Pattern[str], ...], str]] = {
    "vague quantifier": (
        (_VAGUE,),
        "This edit writes a vague quantifier into {path}: {said}. It is unfalsifiable by construction. It neither "
        'states a count nor says which ones. Say WHICH ONES instead: not "falls to a fraction of what it read" but '
        '"takes every ungated way that begins with a set, leaving the ways that begin with a call". Where no '
        "characterisation can be found, the sentence was reporting a measurement and belongs to whatever measures "
        "it. Rule: a-count-is-answered-for.",
    ),
    "universal": (
        (_UNIVERSAL, _UNIVERSAL_SUBJECT),
        "This edit writes a universal into {path}: {said}. Each is refuted by one counter-example. Go and find it "
        "before this stands: grep the tree, read the callee, expand the glob, check the signature. Then say out loud "
        "what you looked at and what it said, and write the edit again. Keep the word where the tree bears it out, "
        "and say WHICH ONES where it does not. Intent is free and state costs a grep, and what gets written is the "
        "state. Rule: a-claim-writes-no-universal.",
    ),
    "superlative": (
        (_SUPERLATIVE,),
        "This edit writes a superlative into {path}: {said}. Each is refuted by one counter-example. Go and find it "
        "before this stands: grep the tree, read the callee, expand the glob, check the signature. Then say out loud "
        "what you looked at and what it said, and write the edit again. Keep the word where the tree bears it out, "
        "and say WHICH ONES where it does not. Rule: a-claim-writes-no-universal.",
    ),
    "sentence pointing at a plan": (
        (_POINTS_AT_A_PLAN,),
        "This edit ends a sentence on `yet`, into {path}: {said}. Such a sentence points at work the project still "
        "owes, and the prose goes stale the day that work lands. `PLAN.md` holds the work the project owes. Say what "
        "is: not `the parser core does not exist yet` but `the tree holds no parser core`. Rule: "
        "a-sentence-states-no-plan.",
    ),
    "question word for a noun": (
        (_A_POINTING_RELATIVE,),
        "This edit writes a question word where a noun belongs, into {path}: {said}. The sentence points at a thing "
        "rather than naming it. Name the noun: not `reduce the ledger to what the tree bears out` but `reduce the "
        "ledger to the entries the tree bears out`. A sentence with no noun to reach for wants saying again rather "
        "than a swap. Rule: a-question-word-is-no-noun.",
    ),
    "word we do not use": (
        (_NOT_OUR_WORD,),
        "This edit writes a word this project turned down, into {path}: {said}. The ban is a ruling, and it sits "
        "beside the pattern in `prose_rules`. Read the reason there and use the word it names instead. Rule: "
        "use-the-words-this-project-uses.",
    ),
    "purpose clause": (
        (_A_PURPOSE_CLAUSE,),
        "This edit writes a purpose clause into {path}: {said}. Such a clause gives WHY where the question was WHAT. "
        "Name the action instead. Rule: a-purpose-clause-says-no-action.",
    ),
    "pointer at a last thing": (
        (_POINTS_AT_A_LAST_THING,),
        "This edit writes a pointer at a last thing into {path}: {said}. An edit inserting a sentence leaves such a "
        "pointer aimed at something else. Name the rule, the file or the option instead. Rule: "
        "a-pointer-names-its-referent.",
    ),
    "step named by a numeral": (
        (_NAMES_A_STEP_BY_A_NUMERAL,),
        "This edit names a step by a numeral, into {path}: {said}. The number moves when somebody adds a step, and the "
        "prose then points at another step. Name the step: not `Phase 10 leaves the bodies unlowered` but `Lowering "
        "leaves the bodies unlowered`. Rule: a-plan-step-is-not-named-by-a-numeral.",
    ),
    "purpose clause on so": (
        (_A_SO_CLAUSE,),
        "This edit writes a `so` clause into {path}: {said}. The clause gives the reader WHY an action happens where "
        "WHAT happens next belongs. End the sentence and say what follows: not `The paths come back so a site can say "
        "which it wants` but `The paths come back. A site then says which it wants`. Rule: a-so-clause-is-a-why.",
    ),
    "path outside a code span": (
        (_A_BARE_PATH,),
        "This edit writes a bare path into {path}: {said}. A word holding a slash, a scope or a dot sits inside "
        "backticks. `check_documents` verifies a backticked name against the tree, and a bare path gets past that "
        "gate. A rename then leaves the prose naming a file the tree dropped. Rule: "
        "a-dotted-name-sits-in-a-code-span.",
    ),
    "name outside a code span": (
        (_AN_UNMARKED_NAME,),
        "This edit writes a bare name into {path}: {said}. A name the tree binds sits inside backticks. "
        "`check_documents` verifies a backticked name against the tree, and a bare name gets past that gate. Rule: "
        "a-name-of-the-tree-sits-in-a-code-span.",
    ),
}


def words_found(prose: str) -> dict[str, list[str]]:
    """
    `{what the fault is called: the words that made it}`, over the sentences `prose` writes.

    Read a sentence at a time. `_UNIVERSAL_SUBJECT` wants a sentence to open on, and the whole prose flattened to a
    single line offers a single opening. `words_found` strips the bold markers from a sentence first. The word rules
    then read a bolded claim as a bare one.
    """
    found: dict[str, set[str]] = {what: set() for what in _A_WORD_RULE}
    for sentence in _sentences(said_by(prose)):
        bare = _EMPHASIS.sub("", sentence).strip()
        for what, (patterns, _) in _A_WORD_RULE.items():
            for pattern in patterns:
                found[what] |= {one.group(0).lower().strip() for one in pattern.finditer(bare)}
    return {what: sorted(said) for what, said in found.items()}


# The files whose text states a rule. A rule wording holds a universal. A checker reads `refuses every sentence ending
# on a verb` as the rule it enforces, and the universal rule passes such a wording here. `.claude/proposals-pending.md`
# takes a proposal the critic raised, and `prose_answer` names that path when it reads one. `write_agent_prompts` copies
# the conventions and the rejections into the agent definitions, and those hold the same wordings.
_A_RULE_IS_STATED_IN = (
    "conventions.md",
    "rejected.md",
    "proposals-pending.md",
    "prose-critic.md",
    "prose-compare.md",
    "prose-condense.md",
)

# The word classes the rule files may hold. A rule quotes the words it refuses.
_A_RULE_MAY_SAY = ("universal", "vague quantifier", "question word for a noun")

# The class a plan document may write. `PLAN.md` states the work the project owes, and a word about when belongs there.
_A_PLAN_MAY_SAY = "sentence pointing at a plan"

# The document stating the work the project owes.
_THE_PLAN = "PLAN.md"


def _does_state_a_rule(path: str) -> bool:
    """Whether the text of `path` states a rule. The tuple above names the files that do."""
    return os.path.basename(path) in _A_RULE_IS_STATED_IN


def word_refusal(prose: str, path: str) -> str | None:
    """
    The refusal the word rules give for `prose` at `path`, or None where the words pass.

    `_A_WORD_RULE` decides both the classes and the wording, and the `prose_words` hook calls here.

    The universal rule passes a file that `_A_RULE_IS_STATED_IN` names.
    """
    found = words_found(prose)
    for what, (_, wording) in _A_WORD_RULE.items():
        if what in _A_RULE_MAY_SAY and _does_state_a_rule(path):
            continue
        if what == _A_PLAN_MAY_SAY and os.path.basename(path) == _THE_PLAN:
            continue
        if found[what]:
            return wording.format(path=path, said="; ".join(found[what]))
    return None


def prose_faults(prose: str, is_stating_why: bool = False) -> list[str]:
    """
    The shape faults of `prose`, as a sentence and as a whole fragment.

    Public. The `prose_rewrite` hook asks here.
    """
    return _shape_faults(prose, is_stating_why) + _fragment_faults(prose)


def _fragment_faults(prose: str) -> list[str]:
    """
    The faults `prose` has as a whole fragment rather than as a sentence.

    A reader opens a fragment through a symbol index or a hover. A first sentence opening on `It` or `The same` leans on
    a neighbour the index withholds. A last sentence with no full stop runs into the text after it. Emphasis around that
    sentence is markup, and the full stop goes inside it. A paragraph opening on a pronoun sends the reader back over
    the blank line to find the noun.
    """
    said = [one for one in _sentences(said_by(prose)) if _CLOSING_MARKUP.sub("", one)]
    if not said:
        return []
    found = []
    opener = _A_DEICTIC_OPENER.match(said[0])
    if opener:
        found.append(
            f"[a fragment opening on a word pointing outside it: {opener.group(0).strip()!r}] {said[0].strip()}"
        )
    pointer = _A_SUCH_POINTER.search(said[0])
    if pointer:
        found.append(f"[a fragment opening on a pointer at its own referent: {pointer.group(0)!r}] {said[0].strip()}")
    if not _CLOSING_MARKUP.sub("", said[-1]).endswith((".", "!", "?")):
        found.append(f"[a fragment whose last sentence has no full stop] {said[-1].strip()}")
    found += _unanchored_runs(said)
    found += _pronoun_openers(prose, bool(opener))
    return found


def _pronoun_openers(prose: str, is_opener_reported: bool) -> list[str]:
    """
    The paragraphs of `prose` whose opening sentence begins with a third-person pronoun.

    The first paragraph opens the fragment. `_A_DEICTIC_OPENER` reads that opening, and `is_opener_reported` says
    whether it reported one.
    """
    found = []
    for at, paragraph in enumerate(_A_PARAGRAPH_BREAK.split(prose)):
        if at == 0 and is_opener_reported:
            continue
        said = _sentences(said_by(paragraph))
        pronoun = _A_PRONOUN_OPENER.match(_EMPHASIS.sub("", said[0]).strip()) if said else None
        if pronoun:
            found.append(f"[a paragraph opening on a pronoun: {pronoun.group(0).strip()!r}] {said[0].strip()}")
    return found


def _unanchored_runs(said: list[str]) -> list[str]:
    """
    The runs of pronoun-opening sentences in `said` that reach back to no named subject.

    A run of such openers reads as a list under a subject the prose named. The sentence in front of the run is where
    that subject goes. A demonstrative there names nothing, and the reader walks back further to find the noun.
    """
    found, at = [], 0
    while at < len(said):
        if not _A_PRONOUN_OPENER.match(said[at].strip()):
            at += 1
            continue
        start = at
        while at < len(said) and _A_PRONOUN_OPENER.match(said[at].strip()):
            at += 1
        if at - start < _FEWEST_IN_A_RUN:
            continue
        behind = said[start - 1].strip() if start else ""
        if not behind or _A_BACK_POINTER.match(behind):
            found.append(f"[a run of pronoun openers reaching back to no named subject] {said[start].strip()}")
    return found


def _hung_tail(said: str) -> str | None:
    """
    The tail `said` hangs off its last comma with no verb, or None.

    A tail passes where it holds a finite verb or opens on `and`, `which` or `that`. A tail holding `and` or `or` ends a
    list, and `_MOST_ITEMS` governs the list. The function refuses a short tail of another shape. An appositive has that
    shape. A trailing absolute has that shape. A verb elided after a conjunction leaves a tail of that shape.
    """
    found = _A_TAIL.search(said)
    if not found:
        return None
    tail = found.group(1).strip()
    if not tail or len(tail.split()) > _MOST_TAIL_WORDS:
        return None
    if _A_FINITE_VERB.search(tail) or _A_SUBORDINATOR.match(tail):
        return None
    if _JOINS_A_LAST_ITEM.search(tail):
        return None
    return tail


def _over(said: str, is_stating_why: bool = False) -> list[str]:
    """The shape faults of a single sentence. The check skips the WHY clause on a text stating a reason."""
    over = []
    why = None if is_stating_why else _EXPLAINS_WHY.search(said)
    if why:
        over.append(f"a clause giving WHY: {why.group(0).strip()!r}")
    if ";" in said:
        over.append("a semicolon joining a pair of clauses")
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
    if _A_WHOSE_TAIL.search(said):
        over.append("a relative clause hung off a comma on `whose`")
    circular = _defined_in_its_own_terms(said)
    if circular:
        over.append(f"a word repeated across `what`: {circular!r}")
    stacked = _A_STACKED_TAIL.findall(said)
    if len(stacked) > _MOST_TAILS:
        over.append(f"a modifier stacked behind another: {', '.join(one.strip() for one in stacked)}")
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

    A hook's refusal quotes the words its rule bans. The word rules would refuse such a refusal.
    """
    return bool(_STATES_ITS_RULE.search(said))


def literal_shape_faults(said: str) -> list[str]:
    """
    The shape faults a string literal holds, with the label and the place it opens on blanked.

    A literal may name what speaks before it speaks. A gate writes `conventions: what a gate can decide holds`, and the
    colon there is the form rather than an appositive. A literal may name a place the same way. A comment names no
    speaker and no place, and gets no such licence.
    """
    return _shape_faults(_A_PLACE.sub(" ", _A_SPEAKER.sub(" ", said, count=1), count=1))


def refused_sentences(prose: str, is_stating_why: bool = False) -> list[str]:
    """
    The sentences of `prose` a shape rule refuses, with no fault named beside them.

    A caller quotes these at a writer that holds no checker of its own.
    """
    held = [said.partition("] ")[2] for said in prose_faults(prose, is_stating_why)]
    return [one for one in dict.fromkeys(held) if one]


def _shape_faults(prose: str, is_stating_why: bool = False) -> list[str]:
    """
    The sentences of `prose` shaped past what a single pass reads.

    `_over` reads only a sentence running past `_FEWEST_WORDS`. A short sentence states a single idea under any
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
        # A stub falls under `_FEWEST_WORDS`, and the truncation is the fault. This reads such a sentence too.
        if _ENDS_ON_A_POSSESSIVE.search(said):
            over.append("a possessive the sentence ends on, with the noun it owns dropped")
        if over:
            found.append(f"[{'; '.join(over)}] {said}")
    return found
