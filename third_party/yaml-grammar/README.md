# Vendored YAML grammar

`yaml-spec-1.2.yaml` is the machine-readable YAML 1.2 grammar — the ~211 productions expressed as operator-keyed YAML —
vendored from <https://github.com/yaml/yaml-grammar> (path `yaml-spec-1.2.yaml`).

It is not what libyeast is generated from. It says how to recognize YAML and nothing about what to emit, and it cannot
be made to: it writes the indicator characters inline, so it has nowhere to record that a quotation mark opens a scalar
as an indicator but is meta inside an escape, and it names no token at all. `grammar/yeast-spec-1.2.yaml` is derived
from it — restoring that structure, and annotating the productions with the tokens they emit.

What this file is, is the **standard libyeast's grammar is held to**. `make check-grammar` erases libyeast's token
actions, puts the indicator characters back where this file writes them inline, and checks that what remains is this
file, production for production. What libyeast adds therefore cannot quietly become what libyeast changes, and a
deliberate departure must be declared, with its reason, in `generator/check_vendor_spec.py`.
