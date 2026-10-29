"""Evaluation harness: agent vs modeled manual baseline, plus ablations of each safeguard.

Trusted-side tooling. Nothing under sourceloop.agents may import this package. Core modules are never
edited: ablations are applied from outside by patching module attributes inside a context manager.
"""
