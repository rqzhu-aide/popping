"""Focused safety and policy tests for the interactive classroom simulator."""

from __future__ import annotations

import asyncio
import importlib.util
import random
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import pytest
import yaml

from question_catalog import validate_question_catalog


pytest.importorskip("httpx")
pytest.importorskip("psutil")
pytest.importorskip("uvicorn")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "simulate_classroom.py"
SPEC = importlib.util.spec_from_file_location("simulate_classroom", SCRIPT_PATH)
assert SPEC and SPEC.loader
simulator_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = simulator_module
SPEC.loader.exec_module(simulator_module)


def test_default_shape_is_forty_students_in_eight_balanced_teams():
    settings = simulator_module.Settings()
    settings.validate()
    assert settings.students == 40
    assert settings.teams == 8
    assert settings.workers == 1
    assert settings.max_members == 5
    counts = Counter(
        simulator_module.team_number_for(number, settings.teams)
        for number in range(1, settings.students + 1)
    )
    assert counts == Counter({team: 5 for team in range(1, 9)})


def test_rating_eligibility_matches_server_rules():
    assert not simulator_module.presentation_eligible(1, 1)
    assert simulator_module.presentation_eligible(2, 1)

    assert not simulator_module.challenge_eligible(1, 1, 2)
    assert not simulator_module.challenge_eligible(2, 1, 2)
    assert simulator_module.challenge_eligible(3, 1, 2)
    assert not simulator_module.challenge_eligible(3, 1, None)


def test_compact_poll_close_closes_both_rating_scopes():
    state = {"poll_active": True, "challenge_ratings_open": True}
    simulator_module.apply_compact_poll_close(
        state, {"changed": False, "poll_closed": True}
    )
    assert state == {
        "poll_active": False,
        "challenge_ratings_open": False,
    }


def test_seed_is_isolated_and_has_expected_course_shape(tmp_path):
    runtime_root = tmp_path / "simulation-run"
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(port=0), root=runtime_root
    )
    simulator.seed()

    assert simulator.data_dir.is_relative_to(runtime_root)
    assert simulator.classes_dir.is_relative_to(runtime_root)
    assert simulator.data_dir.resolve() != (PROJECT_ROOT / "data").resolve()
    assert simulator.classes_dir.resolve() != (PROJECT_ROOT / "classes").resolve()

    course = yaml.safe_load(
        (simulator.classes_dir / "simulation" / "course.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert course["slug"] == "simulation"
    assert course["max_teams"] == 8
    assert course["max_members_per_team"] == 5
    assert course["poll_duration"] == 40
    assert (
        simulator.classes_dir
        / "simulation"
        / "week-1-questions.md"
    ).is_file()
    runtime_class = simulator.classes_dir / "simulation"
    assert not list(runtime_class.glob("week-*-appendix.md"))
    week = validate_question_catalog(runtime_class, weeks=[1]).get_week(1)
    assert week is not None
    assert week.ready
    assert week.discussion.count == 4
    assert week.presentation.count == 4
    assert (
        PROJECT_ROOT / "classes" / "demo" / "week-1-appendix.md"
    ).is_file()

    with sqlite3.connect(simulator.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM instructors").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 8
        assert db.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 40
        assert db.execute(
            "SELECT COUNT(*) FROM students WHERE team_id IS NOT NULL"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT phase FROM course_state"
        ).fetchone()[0] == "setup"
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    assert {len(members) for members in simulator.team_members.values()} == {5}


def test_behavior_is_reproducible_for_a_fixed_seed():
    first = [
        simulator_module.stable_fraction(432, number, "rating")
        for number in range(1, 41)
    ]
    second = [
        simulator_module.stable_fraction(432, number, "rating")
        for number in range(1, 41)
    ]
    different = [
        simulator_module.stable_fraction(433, number, "rating")
        for number in range(1, 41)
    ]
    assert first == second
    assert first != different
    assert all(0 <= value <= 1 for value in first)


def _student(number=1):
    return simulator_module.StudentBot(
        number=number,
        student_id=f"sim{number:03d}",
        target_team_number=1,
        target_team_id=11,
        team_members=("sim001", "sim002"),
        base_url="http://127.0.0.1:5100",
        join_due=0,
        rng=random.Random(number),
    )


def test_discussion_thumb_does_not_depend_on_a_visible_question(tmp_path):
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=tmp_path / "runtime",
    )
    student = _student()
    student.visible_discussion_questions = False
    student.due["thumb:1"] = 0
    calls = []

    async def fake_post(student_arg, operation, path, body):
        calls.append((student_arg.student_id, operation, path, body))
        return "ok"

    simulator.safe_post = fake_post
    asyncio.run(
        simulator.react_discussion(
            student,
            {"my_team": {"id": 11}, "session_key": 1},
        )
    )
    assert calls == [
        (
            "sim001",
            "student.thumb",
            "/api/grade_peer",
            {"recipient_id": "sim002", "selected": True},
        )
    ]


def test_cleared_challenger_is_rearmed_to_raise_again(tmp_path):
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=tmp_path / "runtime",
    )
    simulator.delay = lambda *args: 0
    student = _student()
    hand_key = "hand:pres-1"
    student.done.add(hand_key)
    calls = []

    async def fake_post(student_arg, operation, path, body):
        calls.append((operation, path, body))
        return "ok"

    simulator.safe_post = fake_post
    selected_state = {
        "poll_question_key": "pres-1",
        "active_team": {"id": 22},
        "my_team": {"id": 11},
        "is_active_challenger": True,
        "active_challenges": [],
    }
    asyncio.run(simulator.react_competition(student, selected_state))
    assert calls == []

    cleared_state = {**selected_state, "is_active_challenger": False}
    asyncio.run(simulator.react_competition(student, cleared_state))

    assert calls == [
        (
            "student.raise_hand",
            "/api/raise_hand",
            {"presentation_key": "pres-1"},
        )
    ]
    assert hand_key in student.done


def test_setup_rejoins_after_reset_and_uses_an_available_team(tmp_path):
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=4, teams=2),
        root=tmp_path / "runtime",
    )
    student = _student()
    student.done.add("joined")
    student.available_teams = [
        {"id": 11, "member_count": 2},
        {"id": 12, "member_count": 1},
    ]
    calls = []

    async def fake_post(student_arg, operation, path, body):
        calls.append((operation, path, body))
        return "ok"

    simulator.safe_post = fake_post
    asyncio.run(
        simulator.react_setup(
            student,
            {
                "my_team": None,
                "teams_locked": False,
                "max_members": 2,
            },
        )
    )
    assert calls == [
        ("student.join_team", "/api/join_team", {"team_id": 12})
    ]
    assert "joined" in student.done


def test_failed_secondary_fetch_retries_and_phase_exit_resets_context(tmp_path):
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=tmp_path / "runtime",
    )
    student = _student()
    student.roster_version = 1
    student.response_context = "discussion:1"
    student.last_state = {
        "phase": "discussion",
        "roster_version": 1,
        "discussion_week": 1,
        "discussion_questions_version": 4,
    }
    attempts = 0

    async def fake_request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return simulator_module.httpx.Response(503)
        return simulator_module.httpx.Response(
            200, json={"questions": [{"id": "q1"}]}
        )

    simulator.request = fake_request
    asyncio.run(simulator.sync_secondary(student))
    assert student.question_context is None
    asyncio.run(simulator.sync_secondary(student))
    assert student.question_context == (1, 4)
    assert student.visible_discussion_questions is True

    student.last_state = {"phase": "setup", "roster_version": 1}
    asyncio.run(simulator.sync_secondary(student))
    assert student.question_context is None
    assert student.visible_discussion_questions is False


def test_background_failure_stops_and_identifies_the_simulation(tmp_path):
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=tmp_path / "runtime",
    )

    async def fail():
        raise ValueError("bad payload")

    asyncio.run(simulator.supervise_task("student sim001", fail()))
    assert simulator.stop_event.is_set()
    assert "student sim001 failed: ValueError: bad payload" == (
        simulator.failed_reason
    )


def test_owned_root_cleanup_retries_a_transient_windows_lock(monkeypatch):
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2)
    )
    sentinel = simulator.root / simulator_module.ROOT_SENTINEL
    sentinel.write_text("simulation", encoding="utf-8")
    real_rmtree = simulator_module.shutil.rmtree
    attempts = 0

    def flaky_rmtree(path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("database file is still closing")
        real_rmtree(path)

    monkeypatch.setattr(simulator_module.shutil, "rmtree", flaky_rmtree)
    asyncio.run(simulator.remove_owned_root())
    assert attempts == 2
    assert not simulator.root.exists()


def test_status_marks_a_dead_controller_as_stale():
    current = {
        "status": "running",
        "updated_at": simulator_module.utc_text(),
        "controller_pid": 999_999_999,
        "controller_create_time": time.time(),
    }
    displayed = simulator_module.status_for_display(current)
    assert displayed["status"] == "stale"
    assert "no longer running" in displayed["failure"]


def test_owner_lock_allows_only_one_simulator(monkeypatch, tmp_path):
    control = tmp_path / "control"
    monkeypatch.setattr(simulator_module, "CONTROL_DIR", control)
    monkeypatch.setattr(simulator_module, "OWNER_FILE", control / "owner.lock")
    first = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=tmp_path / "first",
    )
    second = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=tmp_path / "second",
    )
    first.acquire_owner_lock()
    try:
        with pytest.raises(
            RuntimeError,
            match="already starting or running",
        ):
            second.acquire_owner_lock()
    finally:
        first.release_owner_lock()


def test_programmatic_root_is_never_removed_automatically(tmp_path):
    root = tmp_path / "caller-owned"
    root.mkdir()
    (root / simulator_module.ROOT_SENTINEL).write_text(
        "simulation", encoding="utf-8"
    )
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=root,
    )
    asyncio.run(simulator.remove_owned_root())
    assert root.is_dir()


def test_simulator_never_calls_instructor_mutation_routes():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = (
        "/api/set_phase",
        "/api/start_presentation",
        "/api/start_poll",
        "/api/stop_poll",
        "/api/next_presentation",
        "/api/cancel_presentation",
        "/api/select_challenger",
        "/api/clear_challenger",
    )
    for route in forbidden:
        assert route not in source
def _presentation_rating_state(poll_active):
    return {
        "phase": "competition",
        "poll_active": poll_active,
        "poll_question_key": "pres-1",
        "active_team": {"id": 22},
        "my_team": {"id": 11},
        "active_challenges": [],
        "challenge_ratings_open": False,
    }


def test_simulator_fails_an_action_rejected_while_last_state_was_eligible(
        tmp_path):
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=tmp_path / "runtime",
    )
    student = _student()
    student.last_state = _presentation_rating_state(True)

    async def rejected(*_args, **_kwargs):
        return simulator_module.httpx.Response(
            409, json={"error": "The presentation has changed"}
        )

    simulator.request = rejected
    with pytest.raises(RuntimeError, match="eligible in the last state"):
        asyncio.run(
            simulator.safe_post(
                student,
                "student.presentation_rating",
                "/api/submit_rating",
                {
                    "presentation_key": "pres-1",
                    "q1_developed": 4,
                    "q2_easy": 5,
                },
            )
        )

    assert simulator.rejection_summary() == {
        "student.presentation_rating": {
            "unexpected_while_eligible": {
                "409: The presentation has changed": 1,
            }
        }
    }
    assert simulator.error_counts[
        "student.presentation_rating_unexpected_rejection"
    ] == 1


def test_simulator_counts_a_rejection_as_expected_when_last_state_was_closed(
        tmp_path):
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=tmp_path / "runtime",
    )
    student = _student()
    student.last_state = _presentation_rating_state(False)

    async def rejected(*_args, **_kwargs):
        return simulator_module.httpx.Response(
            403, json={"error": "The rating poll is closed"}
        )

    simulator.request = rejected
    result = asyncio.run(
        simulator.safe_post(
            student,
            "student.presentation_rating",
            "/api/submit_rating",
            {
                "presentation_key": "pres-1",
                "q1_developed": 4,
                "q2_easy": 5,
            },
        )
    )

    assert result == "closed"
    assert simulator.rejection_summary() == {
        "student.presentation_rating": {
            "expected_closed": {
                "403: The rating poll is closed": 1,
            }
        }
    }
    assert not simulator.error_counts


def test_simulator_status_exposes_operation_reason_rejection_counters(tmp_path):
    simulator = simulator_module.ClassroomSimulator(
        simulator_module.Settings(students=2, teams=2),
        root=tmp_path / "runtime",
    )
    simulator.rejection_counts[
        (
            "student.thumb",
            "expected_closed",
            403,
            "Not in discussion phase",
        )
    ] = 2

    traffic = simulator.status_payload()["traffic"]

    assert traffic["rejections"] == {
        "student.thumb": {
            "expected_closed": {
                "403: Not in discussion phase": 2,
            }
        }
    }
