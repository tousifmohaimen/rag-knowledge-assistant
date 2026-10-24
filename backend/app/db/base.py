"""Declarative base and column mixins shared by every table."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Predictable constraint names keep migrations stable across environments.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantOwned:
    """Adds ``tenant_id``. Deleting a tenant deletes everything it owns."""

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
