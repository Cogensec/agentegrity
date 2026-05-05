"""External-dataset loaders for the detection benchmark.

Each loader knows the file format of one published prompt-injection /
jailbreak benchmark and adapts it to :class:`BenchmarkPrompt`. Loaders
return an empty list when the dataset path isn't set or the file
doesn't exist; the harness/test-suite skips those datasets gracefully.

Set the relevant env var to a local fixture path to enable a loader::

    AGENTEGRITY_BENCH_PINT=/data/pint.jsonl
    AGENTEGRITY_BENCH_AGENTDOJO=/data/agentdojo
    AGENTEGRITY_BENCH_INJECAGENT=/data/injecagent.jsonl

The loaders are deliberately small so a maintainer can read them in
one sitting before pointing them at production datasets.
"""

from tests.benchmarks.loaders.agentdojo import load_agentdojo
from tests.benchmarks.loaders.injecagent import load_injecagent
from tests.benchmarks.loaders.pint import load_pint

__all__ = ["load_agentdojo", "load_injecagent", "load_pint"]
