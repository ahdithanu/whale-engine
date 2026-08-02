"""`python -m pipeline.resolve` -- delegates to the full-universe resolver.

The anchor-scoped proving run (Boeing/Oshkosh/Caterpillar only) lives at
pipeline.resolve.anchors / pipeline.resolve.report and is still the right
tool for validating a resolver change in isolation before trusting it
against the full ~51K-record universe.
"""

from pipeline.resolve.universe import main

if __name__ == "__main__":
    main()
