"""
Unit tests for server/coaching/plan.py

Covers:
  - get_plan_phase: phase boundaries at 0%, 25%, 55%, 80%, 92%, 100%
  - _recommend_methodology: polarized vs pyramidal decision logic
  - create_plan_from_args: plan dict shape, blocked_days filtering, methodology override
  - format_plan_context / format_plan_status: string smoke tests (no crash, key substrings present)

All tests are fully offline — PLAN_FILE is redirected to tmp_path, no network.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import coaching.plan as plan_mod


# ─────────────────────────────────────────────────────────────────────────────
# get_plan_phase — boundary values
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPlanPhase:
    def test_unknown_for_zero_total_weeks(self):
        assert plan_mod.get_plan_phase(1, 0) == "Unknown"

    def test_unknown_for_zero_current_week(self):
        assert plan_mod.get_plan_phase(0, 12) == "Unknown"

    # Base: pct <= 0.25
    def test_base_at_week_1_of_16(self):
        assert plan_mod.get_plan_phase(1, 16) == "Base"

    def test_base_at_boundary_25pct(self):
        # week 4 of 16 = exactly 0.25
        assert plan_mod.get_plan_phase(4, 16) == "Base"

    # Development: 0.25 < pct <= 0.55
    def test_development_just_above_25pct(self):
        # week 5 of 16 = 0.3125
        assert plan_mod.get_plan_phase(5, 16) == "Development"

    def test_development_at_55pct_boundary(self):
        # 8.8/16 — use 8 of 16 (0.5) and 9 of 16 (0.5625)
        # 0.55 boundary: week = round(0.55 * 16) = 8.8 → week 8 = 0.5 → Development
        #                                                  week 9 = 0.5625 → Specific
        assert plan_mod.get_plan_phase(8, 16) == "Development"
        assert plan_mod.get_plan_phase(9, 16) == "Specific"

    # Specific: 0.55 < pct <= 0.80
    def test_specific_midpoint(self):
        # week 11 of 16 = 0.6875
        assert plan_mod.get_plan_phase(11, 16) == "Specific"

    def test_specific_at_80pct_boundary(self):
        # week 12 of 16 = 0.75 → Specific; week 13 of 16 = 0.8125 → Taper
        assert plan_mod.get_plan_phase(12, 16) == "Specific"
        assert plan_mod.get_plan_phase(13, 16) == "Taper"

    # Taper: 0.80 < pct <= 0.92
    def test_taper_midpoint(self):
        # week 14 of 16 = 0.875
        assert plan_mod.get_plan_phase(14, 16) == "Taper"

    # Race Week: pct > 0.92
    def test_race_week_final_week(self):
        assert plan_mod.get_plan_phase(16, 16) == "Race Week"

    def test_race_week_just_above_92pct(self):
        # week 15 of 16 = 0.9375 > 0.92
        assert plan_mod.get_plan_phase(15, 16) == "Race Week"

    def test_single_week_plan(self):
        # 1/1 = 1.0 > 0.92 → Race Week
        assert plan_mod.get_plan_phase(1, 1) == "Race Week"

    def test_phases_cover_all_weeks_of_12_week_plan(self):
        phases = [plan_mod.get_plan_phase(w, 12) for w in range(1, 13)]
        valid = {"Base", "Development", "Specific", "Taper", "Race Week"}
        assert all(p in valid for p in phases)


# ─────────────────────────────────────────────────────────────────────────────
# _recommend_methodology
# ─────────────────────────────────────────────────────────────────────────────

class TestRecommendMethodology:
    def test_starting_fresh_gives_polarized(self):
        method, _ = plan_mod._recommend_methodology(
            run_days=5, planned_weekly_km=40, volume_change="starting_fresh"
        )
        assert method == "polarized"

    def test_low_volume_gives_polarized(self):
        method, _ = plan_mod._recommend_methodology(
            run_days=5, planned_weekly_km=15, volume_change=None
        )
        assert method == "polarized"

    def test_three_or_fewer_run_days_gives_polarized(self):
        method, _ = plan_mod._recommend_methodology(
            run_days=3, planned_weekly_km=40, volume_change=None
        )
        assert method == "polarized"

    def test_four_run_days_high_volume_gives_pyramidal(self):
        method, _ = plan_mod._recommend_methodology(
            run_days=4, planned_weekly_km=50, volume_change=None
        )
        assert method == "pyramidal"

    def test_five_run_days_no_km_target_gives_pyramidal(self):
        method, _ = plan_mod._recommend_methodology(
            run_days=5, planned_weekly_km=None, volume_change=None
        )
        assert method == "pyramidal"

    def test_four_days_exactly_30km_gives_pyramidal(self):
        # boundary: >= 30km and >= 4 run_days → pyramidal
        method, _ = plan_mod._recommend_methodology(
            run_days=4, planned_weekly_km=30, volume_change=None
        )
        assert method == "pyramidal"

    def test_rationale_is_non_empty_string(self):
        _, rationale = plan_mod._recommend_methodology(
            run_days=3, planned_weekly_km=25, volume_change=None
        )
        assert isinstance(rationale, str) and len(rationale) > 0


# ─────────────────────────────────────────────────────────────────────────────
# create_plan_from_args
# ─────────────────────────────────────────────────────────────────────────────

class TestCreatePlanFromArgs:
    """create_plan_from_args must not touch the filesystem (save_plan is separate)."""

    def _preds(self):
        return {
            "vo2max": 52,
            "hm": "1:48:00",
            "10k": "50:00",
            "5k": "24:00",
            "source": "garmin_race_predictor",
        }

    def _args(self, **overrides):
        base = {
            "race_name":             "Test Half",
            "race_date":             (date.today() + timedelta(weeks=16)).isoformat(),
            "race_distance":         "half_marathon",
            "training_days_per_week": 5,
            "include_strength":      True,
            "strength_days_per_week": 2,
        }
        base.update(overrides)
        return base

    def test_plan_dict_has_required_keys(self):
        plan, _ = plan_mod.create_plan_from_args(self._args(), self._preds())
        for key in ("race_name", "race_date", "race_distance", "total_weeks",
                    "methodology", "run_days_per_week", "training_days_per_week"):
            assert key in plan

    def test_total_weeks_computed_from_race_date(self):
        args = self._args(race_date=(date.today() + timedelta(weeks=12)).isoformat())
        plan, _ = plan_mod.create_plan_from_args(args, self._preds())
        assert plan["total_weeks"] == 12

    def test_run_days_equals_training_minus_strength(self):
        args = self._args(training_days_per_week=5, strength_days_per_week=2)
        plan, _ = plan_mod.create_plan_from_args(args, self._preds())
        assert plan["run_days_per_week"] == 3

    def test_methodology_override_respected(self):
        args = self._args(methodology="pyramidal")
        plan, _ = plan_mod.create_plan_from_args(args, self._preds())
        assert plan["methodology"] == "pyramidal"

    def test_blocked_days_filtered_to_valid_day_names(self):
        args = self._args(blocked_days=["monday", "WEDNESDAY", "notaday", ""])
        plan, _ = plan_mod.create_plan_from_args(args, self._preds())
        assert set(plan["blocked_days"]) == {"Monday", "Wednesday"}

    def test_blocked_days_empty_when_none(self):
        plan, _ = plan_mod.create_plan_from_args(self._args(), self._preds())
        assert plan["blocked_days"] == []

    def test_target_time_from_args_preferred_over_prediction(self):
        args = self._args(target_time="1:45:00")
        plan, _ = plan_mod.create_plan_from_args(args, self._preds())
        assert plan["target_time"] == "1:45:00"

    def test_target_time_falls_back_to_prediction_hm(self):
        args = self._args()  # no target_time key
        plan, _ = plan_mod.create_plan_from_args(args, self._preds())
        assert plan["target_time"] == "1:48:00"

    def test_summary_string_contains_race_name(self):
        _, summary = plan_mod.create_plan_from_args(self._args(), self._preds())
        assert "Test Half" in summary

    def test_summary_string_contains_phase_breakdown_header(self):
        _, summary = plan_mod.create_plan_from_args(self._args(), self._preds())
        assert "PHASE BREAKDOWN" in summary

    def test_b_race_written_to_plan(self):
        args = self._args(
            b_race_name="Tune-up 10k",
            b_race_date=(date.today() + timedelta(weeks=8)).isoformat(),
        )
        plan, _ = plan_mod.create_plan_from_args(args, self._preds())
        assert plan["b_race_name"] == "Tune-up 10k"

    def test_include_strength_false_gives_zero_strength_days(self):
        args = self._args(include_strength=False, strength_days_per_week=2)
        plan, _ = plan_mod.create_plan_from_args(args, self._preds())
        assert plan["strength_days_per_week"] == 0

    def test_planned_weekly_km_stored_as_float(self):
        args = self._args(planned_weekly_km="50")  # passed as string (common from tool args)
        plan, _ = plan_mod.create_plan_from_args(args, self._preds())
        assert isinstance(plan["planned_weekly_km"], float)
        assert plan["planned_weekly_km"] == 50.0


# ─────────────────────────────────────────────────────────────────────────────
# load_plan / save_plan — filesystem I/O via tmp_path
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadSavePlan:
    def test_load_plan_returns_none_when_no_file(self, tmp_path, monkeypatch):
        fake_path = tmp_path / "no-plan.json"
        monkeypatch.setattr(plan_mod, "PLAN_FILE", fake_path)
        assert plan_mod.load_plan() is None

    def test_save_then_load_round_trips(self, tmp_path, monkeypatch):
        fake_path = tmp_path / "test-plan.json"
        monkeypatch.setattr(plan_mod, "PLAN_FILE", fake_path)
        data = {"race_name": "Test", "race_date": "2026-12-01"}
        plan_mod.save_plan(data)
        loaded = plan_mod.load_plan()
        assert loaded == data

    def test_load_plan_returns_none_on_corrupt_json(self, tmp_path, monkeypatch):
        fake_path = tmp_path / "bad-plan.json"
        fake_path.write_text("{not valid json")
        monkeypatch.setattr(plan_mod, "PLAN_FILE", fake_path)
        assert plan_mod.load_plan() is None


# ─────────────────────────────────────────────────────────────────────────────
# format_plan_context — string output smoke tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatPlanContext:
    def _make_plan(self, weeks_from_now=12, weeks_elapsed=4):
        today = date.today()
        return {
            "race_name":     "City Half",
            "race_date":     (today + timedelta(weeks=weeks_from_now)).isoformat(),
            "race_distance": "half_marathon",
            "created_on":    (today - timedelta(weeks=weeks_elapsed)).isoformat(),
            "total_weeks":   weeks_from_now + weeks_elapsed,
            "target_time":   "1:50:00",
            "predicted_hm_time": "1:52:00",
        }

    def test_output_contains_race_name(self):
        out = plan_mod.format_plan_context(self._make_plan())
        assert "City Half" in out

    def test_output_contains_plan_context_header(self):
        out = plan_mod.format_plan_context(self._make_plan())
        assert "PLAN CONTEXT" in out

    def test_race_in_past_shows_days_ago(self):
        today = date.today()
        plan = {
            "race_name":     "Old Race",
            "race_date":     (today - timedelta(days=5)).isoformat(),
            "race_distance": "10k",
            "created_on":    (today - timedelta(weeks=14)).isoformat(),
            "total_weeks":   12,
            "target_time":   None,
        }
        out = plan_mod.format_plan_context(plan)
        assert "ago" in out

    def test_race_today_shows_today(self):
        today = date.today()
        plan = {
            "race_name":     "Today Race",
            "race_date":     today.isoformat(),
            "race_distance": "5k",
            "created_on":    (today - timedelta(weeks=8)).isoformat(),
            "total_weeks":   8,
            "target_time":   None,
        }
        out = plan_mod.format_plan_context(plan)
        assert "TODAY" in out

    def test_b_race_shown_when_present_and_future(self):
        today = date.today()
        plan = self._make_plan()
        plan["b_race_name"] = "B Race Tune-up"
        plan["b_race_date"] = (today + timedelta(weeks=6)).isoformat()
        out = plan_mod.format_plan_context(plan)
        assert "B Race" in out
        assert "B Race Tune-up" in out
