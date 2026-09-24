#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Write the names a citation beside the code may name.

Building that set from the grammar runs the whole pipeline. A write-time hook cannot pay for a pipeline run per edit.
This writes the set once. The gate and the hooks then read the file.

`check_documents` compares the file with the live pipeline. A file behind the pipeline fails that gate.

**Usage:** `python3 generator/write_cited_names.py`. `make regen` runs it.
"""

import json
import os

import annotated2ir
import check_documents
import gate
import normalize


def main() -> None:
    """Write the names of the pipeline into the file the gate and the hooks read."""
    named = check_documents.named_by_the_pipeline(normalize.stages(annotated2ir.load()))
    path = os.path.join(gate.TREE, check_documents.CITED_NAMES)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sorted(named), handle, indent=2)
        handle.write("\n")
    print(f"{len(named)} name(s) written to {check_documents.CITED_NAMES}")


if __name__ == "__main__":
    main()
