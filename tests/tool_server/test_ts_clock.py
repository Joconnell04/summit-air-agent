"""SUMMIT_CLOCK_OFFSET_H: the eval and server-led worlds can play a daytime call at night; the live
world's clock never moves."""

from datetime import UTC, datetime, timedelta

import pytest

from app import clock
from servicetitan_mock import app as mock_app


@pytest.mark.parametrize("world", ["eval", "server-led"])
def test_the_offset_moves_the_clock_on_a_test_world(world):
    shifted = clock.shifted({"SUMMIT_CLOCK_OFFSET_H": "-10", "SUMMIT_WORLD": world})
    assert shifted is not None
    assert abs(shifted() - (datetime.now(UTC) - timedelta(hours=10))) < timedelta(seconds=5)


@pytest.mark.parametrize("world", ["", "live", "production"])
def test_the_offset_is_ignored_anywhere_else(world):
    assert clock.shifted({"SUMMIT_CLOCK_OFFSET_H": "-10", "SUMMIT_WORLD": world}) is None


def test_no_offset_is_the_real_clock():
    assert clock.shifted({"SUMMIT_WORLD": "eval"}) is None


def test_the_mock_reads_the_same_variables(monkeypatch):
    monkeypatch.setenv("SUMMIT_CLOCK_OFFSET_H", "3")
    monkeypatch.setenv("SUMMIT_WORLD", "eval")
    shifted = mock_app.shifted()
    assert shifted is not None and shifted() > datetime.now(UTC) + timedelta(hours=2, minutes=59)
    monkeypatch.setenv("SUMMIT_WORLD", "")
    assert mock_app.shifted() is None
