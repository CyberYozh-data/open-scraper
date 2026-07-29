"""A required field of nothing but nulls has not been extracted.

The row-alignment pattern anchors every field on a container that is present
even when the value is not, so the selector always matches and the field is
never empty — it is a list of Nones. `_missing_required` tested truthiness, so
that list satisfied `required` and self-heal never engaged, on precisely the
presets written to survive selector drift.

Reproduced on yandex_search: with `.Organic` intact but the inner classes
drifted, the pipeline returned titles=[None, None] in deterministic mode with
zero warnings and no heal attempt.
"""
from __future__ import annotations

from src.presets.models import ParsingInstructions
from src.presets.parser_pipeline import _missing_required


def _instructions(**field_kwargs) -> ParsingInstructions:
    return ParsingInstructions.model_validate(
        {
            "type": "css",
            "fields": {
                "titles": {"selector": ".x", "all": True, "required": True, **field_kwargs},
            },
        }
    )


class TestRequiredAllNull:
    def test_all_null_list_counts_as_missing(self):
        assert _missing_required(_instructions(), {"titles": [None, None]}) == ["titles"]

    def test_all_empty_string_list_counts_as_missing(self):
        assert _missing_required(_instructions(), {"titles": ["", ""]}) == ["titles"]

    def test_one_real_value_is_enough_to_satisfy_required(self):
        # Null-in-slot is the designed output for a row whose value is absent;
        # only a column with nothing in it at all is a drift signal.
        assert _missing_required(_instructions(), {"titles": [None, "A", None]}) == []

    def test_empty_list_still_counts_as_missing(self):
        assert _missing_required(_instructions(), {"titles": []}) == ["titles"]

    def test_populated_list_is_not_missing(self):
        assert _missing_required(_instructions(), {"titles": ["A", "B"]}) == []

    def test_scalar_behaviour_is_unchanged(self):
        instructions = ParsingInstructions.model_validate(
            {"type": "css", "fields": {"title": {"selector": ".x", "required": True}}}
        )
        assert _missing_required(instructions, {"title": None}) == ["title"]
        assert _missing_required(instructions, {"title": "A"}) == []
