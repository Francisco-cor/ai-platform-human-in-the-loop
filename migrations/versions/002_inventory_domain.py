"""Fase 2 — dominio inventario, demanda, proveedores y órdenes

Revision ID: 002_inventory_domain
Revises: 001_initial
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_inventory_domain"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("location_id", sa.String(length=64), nullable=False),
        sa.Column("on_hand", sa.Float(), nullable=False),
        sa.Column("reserved", sa.Float(), nullable=False),
        sa.Column("in_transit", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_tenant_sku_loc", "inventory_items", ["tenant_id", "sku", "location_id"], unique=True)
    op.create_index(op.f("ix_inventory_items_location_id"), "inventory_items", ["location_id"], unique=False)
    op.create_index(op.f("ix_inventory_items_sku"), "inventory_items", ["sku"], unique=False)
    op.create_index(op.f("ix_inventory_items_tenant_id"), "inventory_items", ["tenant_id"], unique=False)

    op.create_table(
        "demand_forecasts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("location_id", sa.String(length=64), nullable=False),
        sa.Column("daily_demand", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_demand_tenant_sku_loc", "demand_forecasts", ["tenant_id", "sku", "location_id"], unique=True)
    op.create_index(op.f("ix_demand_forecasts_tenant_id"), "demand_forecasts", ["tenant_id"], unique=False)

    op.create_table(
        "suppliers",
        sa.Column("supplier_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("min_order_qty", sa.Float(), nullable=False),
        sa.Column("max_order_qty", sa.Float(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("allowed_tenants", sa.JSON(), nullable=True),
        sa.Column("allowed_locations", sa.JSON(), nullable=True),
        sa.Column("unit_price_overrides", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("supplier_id"),
    )
    op.create_index(op.f("ix_suppliers_tenant_id"), "suppliers", ["tenant_id"], unique=False)

    op.create_table(
        "purchase_orders",
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("location_id", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expected_arrival_days", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index(op.f("ix_purchase_orders_location_id"), "purchase_orders", ["location_id"], unique=False)
    op.create_index(op.f("ix_purchase_orders_sku"), "purchase_orders", ["sku"], unique=False)
    op.create_index(op.f("ix_purchase_orders_supplier_id"), "purchase_orders", ["supplier_id"], unique=False)
    op.create_index(op.f("ix_purchase_orders_tenant_id"), "purchase_orders", ["tenant_id"], unique=False)

    op.create_table(
        "purchase_order_lines",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["purchase_orders.order_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_purchase_order_lines_order_id"), "purchase_order_lines", ["order_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_purchase_order_lines_order_id"), table_name="purchase_order_lines")
    op.drop_table("purchase_order_lines")
    op.drop_index(op.f("ix_purchase_orders_tenant_id"), table_name="purchase_orders")
    op.drop_index(op.f("ix_purchase_orders_supplier_id"), table_name="purchase_orders")
    op.drop_index(op.f("ix_purchase_orders_sku"), table_name="purchase_orders")
    op.drop_index(op.f("ix_purchase_orders_location_id"), table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_index(op.f("ix_suppliers_tenant_id"), table_name="suppliers")
    op.drop_table("suppliers")
    op.drop_index(op.f("ix_demand_forecasts_tenant_id"), table_name="demand_forecasts")
    op.drop_index("ix_demand_tenant_sku_loc", table_name="demand_forecasts")
    op.drop_table("demand_forecasts")
    op.drop_index(op.f("ix_inventory_items_tenant_id"), table_name="inventory_items")
    op.drop_index(op.f("ix_inventory_items_sku"), table_name="inventory_items")
    op.drop_index(op.f("ix_inventory_items_location_id"), table_name="inventory_items")
    op.drop_index("ix_inventory_tenant_sku_loc", table_name="inventory_items")
    op.drop_table("inventory_items")
