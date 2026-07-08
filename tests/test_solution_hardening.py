"""Unit coverage for the lower-severity hardening fixes.

1. ``solution_unique_name`` — placed in the MSCRM.SolutionUniqueName request
   header — now enforces the Dataverse identifier grammar, ruling out CR/LF
   header injection at the input boundary.
2. ``DATAVERSE_FILE_BASE_DIR`` — when set, solution export/import paths are
   confined to that directory, bounding the blast radius of an arbitrary-path
   file write/read.

Both are pure, network-free regression guards.
"""

import pytest
from pydantic import ValidationError

from dataverse_mcp.models import CreateSolutionInput
from dataverse_mcp.tools.solutions import _FILE_BASE_DIR_VAR, _confined_target

_URL = "https://yourorg.crm.dynamics.com"
_PUBLISHER_GUID = "00000000-0000-0000-0000-000000000001"


def _make_solution(unique_name: str) -> CreateSolutionInput:
    return CreateSolutionInput(
        dataverse_url=_URL,
        solution_unique_name=unique_name,
        display_name="Contoso Core",
        publisher_id=_PUBLISHER_GUID,
        version="1.0.0.0",
    )


def test_solution_unique_name_accepts_identifier():
    assert _make_solution("contoso_core").solution_unique_name == "contoso_core"


@pytest.mark.parametrize(
    "payload",
    [
        "foo\r\nX-Injected: bar",   # CRLF header-injection attempt
        "foo\nbar",                 # bare LF
        "has space",                # whitespace
        "foo-bar",                  # hyphen not permitted in unique names
        "1foo",                     # must start with letter/underscore
    ],
)
def test_solution_unique_name_rejects_non_identifier(payload):
    with pytest.raises(ValidationError) as exc:
        _make_solution(payload)
    assert "solution_unique_name" in str(exc.value)


def test_confined_target_unset_env_is_unconfined(monkeypatch, tmp_path):
    """With DATAVERSE_FILE_BASE_DIR unset, any resolvable path is returned."""
    monkeypatch.delenv(_FILE_BASE_DIR_VAR, raising=False)
    target = _confined_target(str(tmp_path / "sub" / "solution.zip"))
    assert target.name == "solution.zip"


def test_confined_target_allows_path_inside_base(monkeypatch, tmp_path):
    monkeypatch.setenv(_FILE_BASE_DIR_VAR, str(tmp_path))
    target = _confined_target(str(tmp_path / "nested" / "solution.zip"))
    assert target.is_relative_to(tmp_path)


def test_confined_target_allows_base_itself(monkeypatch, tmp_path):
    monkeypatch.setenv(_FILE_BASE_DIR_VAR, str(tmp_path))
    assert _confined_target(str(tmp_path)) == tmp_path.resolve()


def test_confined_target_rejects_path_outside_base(monkeypatch, tmp_path):
    base = tmp_path / "allowed"
    base.mkdir()
    outside = tmp_path / "elsewhere" / "evil.zip"
    monkeypatch.setenv(_FILE_BASE_DIR_VAR, str(base))
    with pytest.raises(ValueError, match=_FILE_BASE_DIR_VAR):
        _confined_target(str(outside))


def test_confined_target_rejects_traversal_escape(monkeypatch, tmp_path):
    base = tmp_path / "allowed"
    base.mkdir()
    monkeypatch.setenv(_FILE_BASE_DIR_VAR, str(base))
    with pytest.raises(ValueError, match=_FILE_BASE_DIR_VAR):
        _confined_target(str(base / ".." / ".." / "etc" / "passwd"))
