"""ORM roundtrip tests for the four channel-registry fields wired up in
Sprint 5.2.1 Mini-Run 3b (channel_role, quality_tier, acquisition_strategy,
monitoring_enabled).

Scope: exercise the SQLModel <-> SQLite serialization path. Postgres-native
ENUM behavior (CREATE TYPE, native_enum=True) is NOT exercised here — under
SQLite ``_enum_column`` falls back to VARCHAR (see entities.py:153-165) and
values come back as plain strings, so we compare via ``.value`` / equality.
The migration roundtrip is covered separately in
test_migration_extend_channel_registry.py.

Note on enum coverage: we deliberately do a single representative roundtrip
per enum (``test_enum_value_roundtrip_stichprobe``) instead of a full
parametrize over every member. The ORM mapping is the same for every
member of an enum, so iterating each value adds no additional fault
coverage — only test runtime.
"""
from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import (
    AcquisitionStrategy,
    Channel,
    ChannelRole,
    Market,
    QualityTier,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _persist(session: Session, channel: Channel) -> Channel:
    session.add(channel)
    session.commit()
    channel_id = channel.id
    session.expire_all()
    return session.exec(select(Channel).where(Channel.id == channel_id)).one()


def test_channel_with_all_new_fields_roundtrip(session: Session) -> None:
    """Set explicit non-default values for all four new fields, persist,
    re-fetch, assert each comes back as written."""
    channel = Channel(
        name="Marvel Studios",
        url="https://instagram.com/marvel",
        handle="marvel",
        market=Market.US,
        channel_role=ChannelRole.STUDIO_DISTRIBUTOR,
        quality_tier=QualityTier.P0,
        acquisition_strategy=AcquisitionStrategy.YOUTUBE_API,
        monitoring_enabled=False,
    )
    fetched = _persist(session, channel)

    assert fetched.channel_role == ChannelRole.STUDIO_DISTRIBUTOR.value
    assert fetched.quality_tier == QualityTier.P0.value
    assert fetched.acquisition_strategy == AcquisitionStrategy.YOUTUBE_API.value
    assert fetched.monitoring_enabled is False


def test_channel_role_nullable_roundtrip(session: Session) -> None:
    """channel_role is the only one of the four that's nullable. An explicit
    None must come back as None (not coerced to empty-string or default)."""
    channel = Channel(
        name="Generic Channel",
        url="https://instagram.com/generic",
        channel_role=None,
        quality_tier=QualityTier.P2,
        acquisition_strategy=AcquisitionStrategy.MANUAL,
    )
    fetched = _persist(session, channel)

    assert fetched.channel_role is None
    assert fetched.quality_tier == QualityTier.P2.value
    assert fetched.acquisition_strategy == AcquisitionStrategy.MANUAL.value


def test_channel_defaults_apply_when_omitted(session: Session) -> None:
    """Omit all four new fields on creation. SQLModel-side defaults
    (P1 / apify / monitoring_enabled=True / channel_role=None) must
    apply. Asserts the default contract from entities.py:185-200.
    """
    channel = Channel(
        name="Defaults Channel",
        url="https://instagram.com/defaults",
    )
    fetched = _persist(session, channel)

    assert fetched.channel_role is None
    assert fetched.quality_tier == QualityTier.P1.value
    assert fetched.acquisition_strategy == AcquisitionStrategy.APIFY.value
    assert fetched.monitoring_enabled is True


def test_enum_value_roundtrip_stichprobe(session: Session) -> None:
    """One representative member per enum, one channel per enum. The mapping
    is shared across members (see _enum_column), so a single Stichprobe is
    enough to catch wrong-direction serialization (e.g. NAME instead of
    value). Full enum coverage would only re-test the same code path."""
    role_channel = _persist(
        session,
        Channel(
            name="Role Probe",
            url="https://instagram.com/role",
            channel_role=ChannelRole.FRANCHISE,
        ),
    )
    tier_channel = _persist(
        session,
        Channel(
            name="Tier Probe",
            url="https://instagram.com/tier",
            quality_tier=QualityTier.P2,
        ),
    )
    strategy_channel = _persist(
        session,
        Channel(
            name="Strategy Probe",
            url="https://instagram.com/strategy",
            acquisition_strategy=AcquisitionStrategy.MANUAL,
        ),
    )

    assert role_channel.channel_role == ChannelRole.FRANCHISE.value
    assert tier_channel.quality_tier == QualityTier.P2.value
    assert strategy_channel.acquisition_strategy == AcquisitionStrategy.MANUAL.value


def test_monitoring_enabled_false_persisted(session: Session) -> None:
    """The boolean field has server_default=true and SQLModel-default True.
    Verify that an explicit False survives the roundtrip and is not silently
    overwritten by the default — would mask a regression where the column
    starts ignoring the model value in favor of the server_default."""
    channel = Channel(
        name="Disabled Monitor",
        url="https://instagram.com/disabled",
        monitoring_enabled=False,
    )
    fetched = _persist(session, channel)

    assert fetched.monitoring_enabled is False
