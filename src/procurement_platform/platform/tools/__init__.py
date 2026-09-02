"""
Platform tools — generic registry and gateway (Fase 11).

Tool plugin registry via entry_points:
  [project.entry-points."procurement.tools"]
  calculate_shortage = procurement_platform.tools.builtin.calculate_shortage:handler
"""

from procurement_platform.platform.tools.registry import get_tool_registry, register_tool

__all__ = ["get_tool_registry", "register_tool"]
