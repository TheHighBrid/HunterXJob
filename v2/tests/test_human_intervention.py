"""Unit tests for native human intervention and supervised browser bridge."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.human_intervention import (
    InterventionResult,
    InterventionStatus,
    InterventionType,
    remove_intervention_guidance,
    show_intervention_guidance,
    wait_for_intervention,
)


def test_wait_for_intervention_custom_condition_resolved():
    async def _run():
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=None)

        counter = {"checks": 0}

        async def custom_check(p):
            counter["checks"] += 1
            return counter["checks"] >= 2

        result = await wait_for_intervention(
            page,
            InterventionType.MFA,
            timeout_seconds=5.0,
            poll_interval=0.05,
            custom_condition=custom_check,
        )

        assert result.status is InterventionStatus.RESOLVED
        assert result.intervention_type is InterventionType.MFA
        assert counter["checks"] >= 2
        assert result.duration_seconds < 2.0

    asyncio.run(_run())


def test_wait_for_intervention_timeout():
    async def _run():
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=None)

        async def never_succeeds(p):
            return False

        result = await wait_for_intervention(
            page,
            InterventionType.LOGIN,
            timeout_seconds=0.15,
            poll_interval=0.05,
            custom_condition=never_succeeds,
        )

        assert result.status is InterventionStatus.TIMEOUT
        assert result.intervention_type is InterventionType.LOGIN
        assert "timed out" in result.message

    asyncio.run(_run())


def test_banner_injection_and_removal_safe():
    async def _run():
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=None)

        await show_intervention_guidance(page, "Test prompt")
        assert page.evaluate.await_count == 1

        await remove_intervention_guidance(page)
        assert page.evaluate.await_count == 2

    asyncio.run(_run())
