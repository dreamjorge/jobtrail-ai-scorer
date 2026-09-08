"""Tests for the ``JOB_ATS_BOARDS`` env parser and ``AtsBoardConfig`` dataclass.

PR-A covers plumbing only: parsing, validation, and the default ``ats_boards``
contract on :class:`AutomationConfig`. Concrete ``LeverSourceAdapter`` and
``GreenhouseSourceAdapter`` construction lands in PR-B and PR-C.

The parser uses a closed allowlist of keys, rejects non-object payloads,
rejects blank or duplicate board tokens, and bounds ``results_wanted`` to
the closed interval ``[1, 200]`` with a default of ``25``.
"""

from __future__ import annotations

import json

import pytest

from jobtrail_ai_scorer.automation import (
    AtsBoardConfig,
    AutomationConfig,
    parse_ats_boards,
)
from jobtrail_ai_scorer.sources import SourceAdapter, build_ats_adapters


# Happy paths


def test_parse_ats_boards_happy_path_returns_all_keys():
    raw = json.dumps(
        {
            "lever_boards": ["acme", "globex"],
            "greenhouse_boards": ["acmeco"],
            "results_wanted": 25,
        }
    )
    config = parse_ats_boards(raw)
    assert config.lever_boards == ("acme", "globex")
    assert config.greenhouse_boards == ("acmeco",)
    assert config.results_wanted == 25


def test_parse_ats_boards_default_results_wanted_when_omitted():
    config = parse_ats_boards(json.dumps({"lever_boards": ["acme"]}))
    assert config.results_wanted == 25


@pytest.mark.parametrize(
    "raw,expected_lever,expected_greenhouse",
    [
        (json.dumps({"lever_boards": [], "greenhouse_boards": []}), (), ()),
        (json.dumps({"lever_boards": ["acme"]}), ("acme",), ()),
        (json.dumps({"greenhouse_boards": ["acmeco"]}), (), ("acmeco",)),
    ],
    ids=["both-empty", "lever-only", "greenhouse-only"],
)
def test_parse_ats_boards_empty_or_single_provider_lists_are_valid(
    raw, expected_lever, expected_greenhouse
):
    config = parse_ats_boards(raw)
    assert config.lever_boards == expected_lever
    assert config.greenhouse_boards == expected_greenhouse


def test_parse_ats_boards_strips_whitespace_around_tokens():
    """Whitespace around a board token must be trimmed; surrounding tokens are
    preserved as unique entries when their trimmed forms differ."""
    config = parse_ats_boards(json.dumps({"lever_boards": ["  acme  ", " globex "]}))
    assert config.lever_boards == ("acme", "globex")


# Invalid JSON


@pytest.mark.parametrize(
    "raw", ["not-json", "{unclosed", "", "}{", "[1, 2, "],
    ids=["non-json", "unclosed-object", "empty", "garbage", "truncated-list"],
)
def test_parse_ats_boards_invalid_json_raises_value_error(raw):
    with pytest.raises(ValueError):
        parse_ats_boards(raw)


# Non-object payloads


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(["lever_boards"]),
        json.dumps("lever"),
        json.dumps(42),
        json.dumps(None),
    ],
    ids=["list", "string", "number", "null"],
)
def test_parse_ats_boards_non_object_raises_value_error(raw):
    with pytest.raises(ValueError):
        parse_ats_boards(raw)


# Unknown keys


def test_parse_ats_boards_unknown_key_raises_value_error():
    raw = json.dumps({"lever_boards": ["acme"], "unknown_key": True})
    with pytest.raises(ValueError):
        parse_ats_boards(raw)


# Blank / duplicate tokens


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"lever_boards": [""]}),
        json.dumps({"lever_boards": ["   "]}),
        json.dumps({"greenhouse_boards": [""]}),
        json.dumps({"greenhouse_boards": ["\t\n"]}),
    ],
    ids=["empty-lever", "whitespace-lever", "empty-greenhouse", "tabs-newlines-greenhouse"],
)
def test_parse_ats_boards_blank_token_raises_value_error(raw):
    with pytest.raises(ValueError):
        parse_ats_boards(raw)


def test_parse_ats_boards_non_string_token_raises_value_error():
    raw = json.dumps({"lever_boards": [123]})
    with pytest.raises(ValueError):
        parse_ats_boards(raw)


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"lever_boards": ["acme", "acme"]}),
        json.dumps({"greenhouse_boards": ["co", "co"]}),
    ],
    ids=["lever-duplicate", "greenhouse-duplicate"],
)
def test_parse_ats_boards_duplicate_token_raises_value_error(raw):
    with pytest.raises(ValueError):
        parse_ats_boards(raw)


def test_parse_ats_boards_duplicate_only_collapses_to_token_after_strip():
    """Whitespace differences must not allow the same board twice."""
    raw = json.dumps({"lever_boards": ["acme", "  acme  "]})
    with pytest.raises(ValueError):
        parse_ats_boards(raw)


# results_wanted validation


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"results_wanted": "5"}),
        json.dumps({"results_wanted": 5.5}),
        json.dumps({"results_wanted": 5.0}),
        json.dumps({"results_wanted": True}),
        json.dumps({"results_wanted": False}),
        json.dumps({"results_wanted": None}),
    ],
    ids=["string", "float", "whole-float", "true", "false", "null"],
)
def test_parse_ats_boards_non_int_results_wanted_raises_value_error(raw):
    with pytest.raises(ValueError):
        parse_ats_boards(raw)


@pytest.mark.parametrize(
    "value",
    [0, -1, 201, 500, 10_000],
    ids=["zero", "negative-one", "two-hundred-one", "five-hundred", "very-large"],
)
def test_parse_ats_boards_results_wanted_out_of_bounds_raises_value_error(value):
    """The design bounds ``results_wanted`` to the closed interval ``[1, 200]``."""
    with pytest.raises(ValueError):
        parse_ats_boards(json.dumps({"results_wanted": value}))


@pytest.mark.parametrize("value", [1, 25, 200], ids=["lower-bound", "default", "upper-bound"])
def test_parse_ats_boards_results_wanted_in_bounds(value):
    config = parse_ats_boards(json.dumps({"results_wanted": value}))
    assert config.results_wanted == value


# AutomationConfig wiring


def test_automation_config_parses_ats_boards_from_env():
    config = AutomationConfig.from_env(
        {"JOB_ATS_BOARDS": json.dumps({"lever_boards": ["acme"]})}
    )
    assert config.ats_boards == AtsBoardConfig(
        lever_boards=("acme",),
        greenhouse_boards=(),
        results_wanted=25,
    )


def test_automation_config_ats_boards_is_none_when_unset():
    config = AutomationConfig.from_env({})
    assert config.ats_boards is None


def test_automation_config_ats_boards_default_is_none_when_constructed_directly():
    """A direct ``AutomationConfig(...)`` call must default ``ats_boards`` to None."""
    config = AutomationConfig()
    assert config.ats_boards is None


def test_automation_config_accepts_explicit_ats_boards_dataclass():
    """``AutomationConfig(ats_boards=AtsBoardConfig(...))`` must round-trip the
    parsed config so callers that build the dataclass directly (without the
    env parser) get the same shape as :meth:`AutomationConfig.from_env`."""
    boards = AtsBoardConfig(
        lever_boards=("acme",),
        greenhouse_boards=("acmeco",),
        results_wanted=42,
    )
    config = AutomationConfig(ats_boards=boards)
    assert config.ats_boards is boards
    assert config.ats_boards.results_wanted == 42


# build_ats_adapters factory


def test_build_ats_adapters_returns_empty_tuple_when_ats_boards_is_none():
    """``build_ats_adapters(None)`` returns the empty tuple so the
    :class:`JobTrailAutomation` default matches the historical behaviour."""
    adapters = build_ats_adapters(None)
    assert adapters == ()
    assert isinstance(adapters, tuple)


def test_build_ats_adapters_returns_lever_adapter_when_lever_boards_configured():
    """Configured Lever boards construct a Lever adapter."""
    boards = AtsBoardConfig(lever_boards=("acme",), greenhouse_boards=())
    adapters = build_ats_adapters(boards)
    assert len(adapters) == 1
    assert adapters[0].name == "lever"


def test_automation_config_rejects_invalid_ats_boards_from_env():
    with pytest.raises(ValueError):
        AutomationConfig.from_env(
            {"JOB_ATS_BOARDS": json.dumps({"unknown_key": True})}
        )
