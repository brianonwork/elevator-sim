"""Evaluation harness: traffic generation, the preset catalog, trial runs and reporting.

This package answers "which scheduler is better, and by how much?" It builds synthetic request
files, runs every scheduler on the same files, and compares the results. Separate from the simulator
proper. Nothing in ``elevator_sim`` imports this package; it imports the simulator to drive it. See
``docs/write-up/4-trial-design.md`` for the preset set and the run protocol these modules implement.
"""
