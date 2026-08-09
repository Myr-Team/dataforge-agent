from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field


class PriceMappingConflict(RuntimeError):
    pass


class DeploymentPriceMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_ref: str = Field(min_length=1, max_length=128)
    deployment: str = Field(min_length=1, max_length=160)
    official_price_key: str = Field(min_length=3, max_length=240)
    mapping_revision: int = Field(ge=1)
    updated_by_ref: str = Field(min_length=1, max_length=128)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class InMemoryPriceMappingRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._rows: dict[tuple[str, str], DeploymentPriceMapping] = {}

    def get(
        self, tenant_ref: str, deployment: str
    ) -> DeploymentPriceMapping | None:
        with self._lock:
            return self._rows.get((tenant_ref, deployment))

    def list(self, tenant_ref: str) -> tuple[DeploymentPriceMapping, ...]:
        with self._lock:
            rows = [
                value
                for (tenant, _), value in self._rows.items()
                if tenant == tenant_ref
            ]
        return tuple(sorted(rows, key=lambda item: item.deployment))

    def upsert(
        self,
        mapping: DeploymentPriceMapping,
        *,
        base_revision: int,
    ) -> DeploymentPriceMapping:
        key = (mapping.tenant_ref, mapping.deployment)
        with self._lock:
            current = self._rows.get(key)
            current_revision = current.mapping_revision if current else 0
            if current_revision != base_revision:
                raise PriceMappingConflict("price mapping revision conflict")
            if mapping.mapping_revision != base_revision + 1:
                raise ValueError("mapping_revision must advance by one")
            self._rows[key] = mapping
        return mapping

    def delete(
        self,
        tenant_ref: str,
        deployment: str,
        *,
        base_revision: int,
    ) -> bool:
        with self._lock:
            key = (tenant_ref, deployment)
            current = self._rows.get(key)
            if current is None:
                return False
            if current.mapping_revision != base_revision:
                raise PriceMappingConflict("price mapping revision conflict")
            self._rows.pop(key)
            return True


class SqlPriceMappingRepository:
    def __init__(self, *, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def get(
        self, tenant_ref: str, deployment: str
    ) -> DeploymentPriceMapping | None:
        connection = self._connection_factory()
        try:
            row = connection.cursor().execute(
                """/* finops:get-price-mapping */
                SELECT tenant_ref, deployment, official_price_key,
                       mapping_revision, updated_by_ref, updated_at
                FROM df_finops.official_price_mapping
                WHERE tenant_ref = ? AND deployment = ?""",
                tenant_ref,
                deployment,
            ).fetchone()
            return _mapping_from_row(row) if row is not None else None
        finally:
            connection.close()

    def list(self, tenant_ref: str) -> tuple[DeploymentPriceMapping, ...]:
        connection = self._connection_factory()
        try:
            rows = connection.cursor().execute(
                """/* finops:list-price-mappings */
                SELECT tenant_ref, deployment, official_price_key,
                       mapping_revision, updated_by_ref, updated_at
                FROM df_finops.official_price_mapping
                WHERE tenant_ref = ?
                ORDER BY deployment""",
                tenant_ref,
            ).fetchall()
            return tuple(_mapping_from_row(row) for row in rows)
        finally:
            connection.close()

    def upsert(
        self,
        mapping: DeploymentPriceMapping,
        *,
        base_revision: int,
    ) -> DeploymentPriceMapping:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """/* finops:upsert-price-mapping */
                MERGE df_finops.official_price_mapping WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS deployment) AS source
                ON target.tenant_ref = source.tenant_ref
                   AND target.deployment = source.deployment
                WHEN MATCHED AND target.mapping_revision = ? THEN UPDATE SET
                    official_price_key = ?, mapping_revision = ?,
                    updated_by_ref = ?, updated_at = SYSUTCDATETIME()
                WHEN NOT MATCHED AND ? = 0 THEN INSERT (
                    tenant_ref, deployment, official_price_key,
                    mapping_revision, updated_by_ref
                ) VALUES (?, ?, ?, ?, ?);""",
                mapping.tenant_ref,
                mapping.deployment,
                base_revision,
                mapping.official_price_key,
                mapping.mapping_revision,
                mapping.updated_by_ref,
                base_revision,
                mapping.tenant_ref,
                mapping.deployment,
                mapping.official_price_key,
                mapping.mapping_revision,
                mapping.updated_by_ref,
            )
            if getattr(cursor, "rowcount", 1) == 0:
                connection.rollback()
                raise PriceMappingConflict("price mapping revision conflict")
            connection.commit()
            return mapping
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def delete(
        self,
        tenant_ref: str,
        deployment: str,
        *,
        base_revision: int,
    ) -> bool:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """/* finops:delete-price-mapping */
                DELETE FROM df_finops.official_price_mapping
                WHERE tenant_ref = ? AND deployment = ?
                  AND mapping_revision = ?""",
                tenant_ref,
                deployment,
                base_revision,
            )
            deleted = int(getattr(cursor, "rowcount", 0) or 0) > 0
            if not deleted:
                exists = cursor.execute(
                    """/* finops:price-mapping-exists */
                    SELECT mapping_revision
                    FROM df_finops.official_price_mapping
                    WHERE tenant_ref = ? AND deployment = ?""",
                    tenant_ref,
                    deployment,
                ).fetchone()
                if exists is not None:
                    connection.rollback()
                    raise PriceMappingConflict("price mapping revision conflict")
            connection.commit()
            return deleted
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            connection.close()


def _mapping_from_row(row: Any) -> DeploymentPriceMapping:
    values = list(row)
    return DeploymentPriceMapping(
        tenant_ref=values[0],
        deployment=values[1],
        official_price_key=values[2],
        mapping_revision=values[3],
        updated_by_ref=values[4],
        updated_at=values[5],
    )


__all__ = [
    "DeploymentPriceMapping",
    "InMemoryPriceMappingRepository",
    "PriceMappingConflict",
    "SqlPriceMappingRepository",
]
