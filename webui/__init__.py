"""A local web console for the branch-free rule engine.

Everything in ``engine/`` is deliberately free of conditionals: the mesh decides
by asking an energy model. This package is the human-facing shell around that —
it picks a rule file, picks a backend, starts a run and streams the trace to the
browser. Ordinary ``if`` statements are fine here because none of this code ever
chooses a branch.
"""
