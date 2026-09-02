# Plugin Tool Gateway — Fase 11

**Contrato:** `platform/tools/registry.py` permite registrar nuevas tools sin tocar `tools/gateway.py`.

## Registro manual

```python
from procurement_platform.platform.tools.registry import register_tool

schema = {
  "type": "object",
  "properties": {"sku": {"type": "string"}, "on_hand": {"type": "number"}},
  "required": ["sku", "on_hand"]
}
def my_handler(payload):
    return {"shortage": payload["demand"] - payload["on_hand"]}

register_tool("calculate_shortage", schema, my_handler)
```

## Registro via entry_points

`pyproject.toml`:

```toml
[project.entry-points."procurement.tools"]
calculate_shortage = "procurement_platform.tools.builtin.calculate_shortage:handler"
hello_tool = "my_package.tools:handler"
```

El `ToolGateway` lee el registry al inicio:

```python
from procurement_platform.platform.tools.registry import get_tool_registry
registry = get_tool_registry()
# definitions.py -> TOOL_SCHEMAS = {k: v["schema"] for k,v in registry.items()}
```

## Tool contract (OpenAPI)

Cada tool debe exponer `schema` (JSONSchema) para validación entrada/salida y `handler` con firma `handler(payload: dict) -> dict`.

El gateway valida:
1. schema entrada
2. tenant/state allowlist (`TOOL_ALLOWLIST_BY_STATE`)
3. budgets/rate limits
4. idempotencia
5. dry-run support (`payload._dry_run` no ejecuta efecto externo)

## Ejemplo builtin

`src/procurement_platform/tools/builtin/calculate_shortage.py` es ejemplo mínimo. Ver `platform/tools/registry.py:list_tools()`.

## Añadir nueva tool sin modificar gateway

1. Crear `my_tool.py` con `schema` + `handler`.
2. Añadir entry_point en `pyproject.toml`.
3. `pip install -e .` y verificar `python -c "from procurement_platform.platform.tools.registry import list_tools; print(list_tools())"`.

No se requiere cambiar `tools/gateway.py` ni `tools/definitions.py` salvo allowlist.
