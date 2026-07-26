"""Optional session exporters for agentegrity.

Exporters implement the :class:`~agentegrity.adapters.base.SessionExporter`
protocol and are attached to any adapter via ``register_exporter``. The
OpenTelemetry exporter requires the ``[otel]`` extra.
"""

from agentegrity.exporters.otel import OTelSessionExporter

__all__ = ["OTelSessionExporter"]
