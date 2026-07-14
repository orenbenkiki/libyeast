# SPDX-License-Identifier: MIT
"""How a gate says what it found.

Every gate reports the same way, so that a failure reads the same wherever it came from — and so that reporting one
cannot be got subtly wrong in one place and right in the other four.
"""

import sys


def report(errors, noun, summary):
    """Print `errors` to standard error and exit non-zero, or print `summary` and return.

    `noun` names what an error is, for the count that follows the list of them.
    """
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"{len(errors)} {noun}", file=sys.stderr)
        sys.exit(1)
    print(summary)
