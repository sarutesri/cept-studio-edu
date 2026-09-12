"""CEPT Power Studio — multi-engine power system study core.

A declarative, agent-drivable framework for power system test studies
(OpenDSS and DIgSILENT PowerFactory). The public contract is the case
schema in ``cept.schema``; everything below it (engine adapters, studies,
reporting) is replaceable without changing how a case is described.
"""

__version__ = "0.2.0"

# Small stable data surface used by the public education package.  The engine
# adapters remain opt-in and are deliberately not imported here.
from cept.schema import Case, StudyResult

__all__ = ["Case", "StudyResult", "__version__"]
