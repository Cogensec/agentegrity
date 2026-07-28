"""Exporters — network sinks that stream live session data off the agent.

Exporters implement the :class:`~agentegrity.adapters.base.SessionExporter`
protocol and are attached to any adapter via ``register_exporter``. The SDK
evaluates locally by default and sends nothing anywhere; registering an
exporter is what turns on streaming. Two ship in-tree:

* :class:`~agentegrity.exporters.http.HTTPExporter` — POSTs sessions to an
  agentegrity-pro backend. Stdlib-only, no extra required, and auto-attached
  when ``AGENTEGRITY_TOKEN`` / ``AGENTEGRITY_EXPORTER_URL`` are set.
* :class:`~agentegrity.exporters.otel.OTelSessionExporter` — emits
  OpenTelemetry traces and metrics to any OTLP backend. Requires the ``[otel]``
  extra; the module imports without it, and the constructor is what raises.
"""

from agentegrity.exporters.http import HTTPExporter, from_env
from agentegrity.exporters.otel import OTelSessionExporter

__all__ = ["HTTPExporter", "from_env", "OTelSessionExporter"]
