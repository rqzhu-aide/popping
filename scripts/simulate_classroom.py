#!/usr/bin/env python3
"""Run a human-controlled classroom with simulated student sessions.

The instructor uses the normal browser interface. This process creates an
isolated temporary course and drives only student login, polling, team join,
thumb, hand, and rating requests. It never calls an instructor mutation route
and never points at the repository's real data or classes directories.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter, deque
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import httpx
    import psutil
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Simulator dependencies are missing. Run: "
        "python -m pip install -r requirements-load-test.txt"
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SLUG = "simulation"
INSTRUCTOR_USERNAME = "sim-instructor"
INSTRUCTOR_PIN = "9999"
STUDENT_PIN = "2468"
CONTROL_KEY = hashlib.sha256(str(PROJECT_ROOT).encode()).hexdigest()[:10]
CONTROL_DIR = Path(tempfile.gettempdir()) / f"popping-simulator-{CONTROL_KEY}"
CURRENT_FILE = CONTROL_DIR / "current.json"
STOP_FILE = CONTROL_DIR / "stop.request"
OWNER_FILE = CONTROL_DIR / "owner.lock"
ROOT_SENTINEL = ".popping-simulation-root"
STATIC_ASSETS = (
    "/static/css/style.css",
    "/static/vendor/atom-one-dark.min.css",
    "/static/vendor/marked.min.js",
    "/static/vendor/purify.min.js",
    "/static/vendor/highlight.min.js",
    "/static/js/app.js",
)
COLORS = (
    "#2563eb", "#dc2626", "#16a34a", "#9333ea",
    "#ea580c", "#0891b2", "#ca8a04", "#db2777",
    "#4f46e5", "#be123c", "#15803d", "#7e22ce",
)


class StopRequested(Exception):
    """Internal signal for a clean stop during server startup."""


def utc_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def process_alive(pid: Any, create_time: Any = None) -> bool:
    try:
        process = psutil.Process(int(pid))
        if not process.is_running():
            return False
        if create_time is not None:
            return abs(process.create_time() - float(create_time)) < 1
        return True
    except (TypeError, ValueError, psutil.Error):
        return False


def stable_fraction(seed: int, *parts: Any) -> float:
    source = ":".join(str(part) for part in (seed, *parts)).encode()
    number = int.from_bytes(hashlib.sha256(source).digest()[:8], "big")
    return number / float(2**64 - 1)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * pct / 100) - 1)
    return ordered[index]


def team_number_for(student_number: int, team_count: int) -> int:
    return ((student_number - 1) % team_count) + 1


def presentation_eligible(student_team: int, presenting_team: int | None) -> bool:
    return bool(presenting_team and student_team != presenting_team)


def challenge_eligible(
    student_team: int,
    presenting_team: int | None,
    challenger_team: int | None,
) -> bool:
    return bool(
        presenting_team
        and challenger_team
        and student_team not in (presenting_team, challenger_team)
    )


def apply_compact_poll_close(
    state: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Apply the timer-only close signal returned by the compact poll path."""
    if payload.get("changed") is False and payload.get("poll_closed"):
        state["poll_active"] = False
        state["challenge_ratings_open"] = False


@dataclass(frozen=True)
class Settings:
    students: int = 40
    teams: int = 8
    workers: int = 1
    port: int = 5100
    seed: int = 432
    speed: float = 1.0
    participation_rate: float = 1.0
    max_runtime: int = 3600
    keep_data: bool = False

    @property
    def max_members(self) -> int:
        return math.ceil(self.students / self.teams)

    def validate(self) -> None:
        if not 2 <= self.students <= 200:
            raise ValueError("students must be between 2 and 200")
        if not 2 <= self.teams <= min(20, self.students):
            raise ValueError("teams must be between 2 and the student count")
        if not 1 <= self.workers <= 8:
            raise ValueError("workers must be between 1 and 8")
        if not 0 <= self.port <= 65535 - self.workers:
            raise ValueError("port range is invalid")
        if self.speed <= 0:
            raise ValueError("speed must be positive")
        if not 0 <= self.participation_rate <= 1:
            raise ValueError("participation-rate must be between 0 and 1")
        if self.max_runtime < 0:
            raise ValueError("max-runtime cannot be negative")


@dataclass
class StudentBot:
    number: int
    student_id: str
    target_team_number: int
    target_team_id: int
    team_members: tuple[str, ...]
    base_url: str
    join_due: float
    rng: random.Random
    client: httpx.AsyncClient | None = None
    state_version: int | None = None
    last_state: dict[str, Any] = field(default_factory=dict)
    page_phase: str | None = None
    poll_failures: int = 0
    dashboard_failures: int = 0
    roster_version: int | None = None
    question_context: tuple[int, int] | None = None
    response_context: str | None = None
    phase_reload_pending: str | None = None
    visible_discussion_questions: bool = False
    available_teams: list[dict[str, Any]] = field(default_factory=list)
    due: dict[str, float] = field(default_factory=dict)
    done: set[str] = field(default_factory=set)
    submitted_presentations: set[str] = field(default_factory=set)
    submitted_challenges: set[str] = field(default_factory=set)


class ClassroomSimulator:
    def __init__(self, settings: Settings, root: Path | None = None) -> None:
        settings.validate()
        self.settings = settings
        self.owns_root = root is None
        self.root = root or Path(tempfile.mkdtemp(prefix="popping-simulation-"))
        resolved_root = self.root.resolve()
        resolved_project = PROJECT_ROOT.resolve()
        if (
            resolved_root == resolved_project
            or resolved_root in resolved_project.parents
            or resolved_project in resolved_root.parents
        ):
            raise ValueError("simulation root must not overlap the repository")
        self.data_dir = self.root / "data"
        self.classes_dir = self.root / "classes"
        self.db_path = self.data_dir / SLUG / "popping.db"
        self.server_log_path = self.root / "server.log"
        self.servers: list[subprocess.Popen[str]] = []
        self.server_log: Any = None
        self.base_urls: list[str] = []
        self.team_ids: dict[int, int] = {}
        self.team_members: dict[int, tuple[str, ...]] = {}
        self.student_rows: list[dict[str, Any]] = []
        self.students: list[StudentBot] = []
        self.tasks: list[asyncio.Task[Any]] = []
        self.student_tasks: list[asyncio.Task[Any]] = []
        self.stop_event = asyncio.Event()
        self.started_monotonic = time.monotonic()
        self.owner_lock: Any = None
        self.controller_create_time = psutil.Process(os.getpid()).create_time()
        self.failed_reason: str | None = None
        self.integrity: dict[str, Any] | None = None
        self.requests: deque[tuple[float, str, float, int | None]] = deque(
            maxlen=20000
        )
        self.action_counts: Counter[str] = Counter()
        self.error_counts: Counter[str] = Counter()
        self.total_requests = 0

    def seed(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ROOT_SENTINEL).write_text(
            "Disposable Popping classroom simulation data.\n", encoding="utf-8"
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.classes_dir.mkdir(parents=True, exist_ok=True)
        target_class = self.classes_dir / SLUG
        shutil.copytree(
            PROJECT_ROOT / "classes" / "demo",
            target_class,
            ignore=shutil.ignore_patterns("week-*-appendix.md"),
        )
        course_config = {
            "slug": SLUG,
            "name": f"Simulated Classroom ({self.settings.students} students)",
            "code": f"SIM {self.settings.students:03d}",
            "semester": "Local simulation",
            "active": True,
            "team_pool_size": self.settings.teams,
            "max_teams": self.settings.teams,
            "max_members_per_team": self.settings.max_members,
            "poll_duration": 40,
            "teams": [
                {"color": COLORS[index % len(COLORS)]}
                for index in range(self.settings.teams)
            ],
        }
        (target_class / "course.yaml").write_text(
            yaml.safe_dump(course_config, sort_keys=False), encoding="utf-8"
        )

        course_data = self.data_dir / SLUG
        course_data.mkdir(parents=True)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                (PROJECT_ROOT / "popping.sql").read_text(encoding="utf-8")
            )
            instructor_id = db.execute(
                "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
                (INSTRUCTOR_USERNAME, "Simulation Instructor", INSTRUCTOR_PIN),
            ).lastrowid
            course_id = db.execute(
                """INSERT INTO courses
                   (name, code, semester, slug, instructor_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    course_config["name"], course_config["code"],
                    course_config["semester"], SLUG, instructor_id,
                ),
            ).lastrowid
            for team_number in range(1, self.settings.teams + 1):
                team_id = db.execute(
                    "INSERT INTO teams (course_id, name, color) VALUES (?, ?, ?)",
                    (
                        course_id, f"Team {team_number}",
                        COLORS[(team_number - 1) % len(COLORS)],
                    ),
                ).lastrowid
                self.team_ids[team_number] = int(team_id)

            member_lists: dict[int, list[str]] = {
                number: [] for number in self.team_ids
            }
            for number in range(1, self.settings.students + 1):
                student_id = f"sim{number:03d}"
                team_number = team_number_for(number, self.settings.teams)
                db_id = db.execute(
                    """INSERT INTO students
                       (course_id, student_id, name, pin, team_id)
                       VALUES (?, ?, ?, ?, NULL)""",
                    (
                        course_id, student_id,
                        f"Simulated Student {number:03d}", STUDENT_PIN,
                    ),
                ).lastrowid
                member_lists[team_number].append(student_id)
                self.student_rows.append(
                    {
                        "number": number,
                        "student_id": student_id,
                        "db_id": int(db_id),
                        "team_number": team_number,
                        "team_id": self.team_ids[team_number],
                    }
                )
            self.team_members = {
                number: tuple(members)
                for number, members in member_lists.items()
            }
            db.execute(
                """INSERT INTO course_state
                   (course_id, phase, max_teams, max_members_per_team,
                    discussion_week, presentation_history, roster_version,
                    session_key, active_challenges_json)
                   VALUES (?, 'setup', ?, ?, 1, '[]', 0, 1, '[]')""",
                (
                    course_id, self.settings.teams,
                    self.settings.max_members,
                ),
            )
            db.commit()

    def acquire_owner_lock(self) -> None:
        CONTROL_DIR.mkdir(parents=True, exist_ok=True)
        handle = OWNER_FILE.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise RuntimeError(
                "Another simulated classroom is already starting or running"
            ) from exc
        owner = {
            "pid": os.getpid(),
            "create_time": self.controller_create_time,
            "acquired_at": utc_text(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(owner).encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
        self.owner_lock = handle

    def release_owner_lock(self) -> None:
        handle = self.owner_lock
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self.owner_lock = None

    @staticmethod
    def port_available(port: int) -> bool:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    def choose_ports(self) -> list[int]:
        if self.settings.port:
            ports = [
                self.settings.port + offset
                for offset in range(self.settings.workers)
            ]
            unavailable = [port for port in ports if not self.port_available(port)]
            if unavailable:
                raise RuntimeError(f"Local port is already in use: {unavailable[0]}")
            return ports
        for base in range(5100, 65000 - self.settings.workers):
            ports = [base + offset for offset in range(self.settings.workers)]
            if all(self.port_available(port) for port in ports):
                return ports
        raise RuntimeError("Could not find an available local port")

    async def start_servers(self) -> None:
        ports = self.choose_ports()
        self.base_urls = [f"http://127.0.0.1:{port}" for port in ports]
        environment = os.environ.copy()
        environment.update(
            {
                "POPPING_LOAD_TEST": "1",
                "DATA_DIR": str(self.data_dir),
                "LOAD_TEST_CLASSES_DIR": str(self.classes_dir),
                "SECRET_KEY": secrets.token_hex(32),
                "PYTHONUNBUFFERED": "1",
            }
        )
        environment.pop("RENDER", None)
        self.server_log = self.server_log_path.open("w", encoding="utf-8")
        for index, (port, base_url) in enumerate(
            zip(ports, self.base_urls, strict=True), 1
        ):
            if STOP_FILE.exists():
                raise StopRequested
            self.server_log.write(
                f"\n=== Simulation server {index}: {base_url} ===\n"
            )
            self.server_log.flush()
            command = [
                sys.executable, "-m", "uvicorn", "load_test_app:app",
                "--app-dir", "scripts", "--interface", "wsgi",
                "--host", "127.0.0.1", "--port", str(port),
                "--workers", "1", "--lifespan", "off", "--backlog", "512",
                "--timeout-keep-alive", "5", "--no-access-log",
            ]
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=self.server_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.servers.append(process)
            await self.wait_for_server(process, base_url)

    @staticmethod
    async def wait_for_server(
        process: subprocess.Popen[str], base_url: str
    ) -> None:
        deadline = time.monotonic() + 45
        async with httpx.AsyncClient(base_url=base_url) as client:
            while time.monotonic() < deadline:
                if STOP_FILE.exists():
                    raise StopRequested
                if process.poll() is not None:
                    break
                try:
                    response = await client.get(f"/login/{SLUG}", timeout=2)
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        raise RuntimeError(f"Simulation server did not start: {base_url}")

    def build_students(self) -> None:
        self.students = []
        for row in self.student_rows:
            number = int(row["number"])
            team_number = int(row["team_number"])
            self.students.append(
                StudentBot(
                    number=number,
                    student_id=str(row["student_id"]),
                    target_team_number=team_number,
                    target_team_id=int(row["team_id"]),
                    team_members=self.team_members[team_number],
                    base_url=self.base_urls[(number - 1) % len(self.base_urls)],
                    join_due=(
                        self.started_monotonic
                        + self.settings.speed * (0.4 + (number - 1) * 0.18)
                    ),
                    rng=random.Random(self.settings.seed * 1000 + number),
                )
            )

    async def request(
        self,
        student: StudentBot,
        operation: str,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response | None:
        if student.client is None:
            return None
        started = time.perf_counter()
        self.total_requests += 1
        try:
            response = await student.client.request(
                method, path, timeout=httpx.Timeout(15), **kwargs
            )
            elapsed = (time.perf_counter() - started) * 1000
            self.requests.append((time.time(), operation, elapsed, response.status_code))
            if response.status_code >= 500:
                self.error_counts[f"http_{response.status_code}"] += 1
            return response
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            elapsed = (time.perf_counter() - started) * 1000
            self.requests.append((time.time(), operation, elapsed, None))
            self.error_counts[type(exc).__name__] += 1
            return None

    async def safe_post(
        self, student: StudentBot, operation: str, path: str, body: dict[str, Any]
    ) -> str:
        for attempt in range(2):
            response = await self.request(
                student, operation, "POST", path, json=body
            )
            if response is not None and response.status_code == 200:
                self.action_counts[operation] += 1
                return "ok"
            if response is not None and response.status_code == 401:
                self.error_counts[f"{operation}_session_lost"] += 1
                raise RuntimeError(f"{operation} lost the student session")
            if response is not None and response.status_code in (403, 409):
                return "closed"
            if (
                response is not None
                and response.status_code == 404
                and operation == "student.challenge_rating"
            ):
                return "closed"
            if (
                response is not None
                and response.status_code == 400
                and operation == "student.join_team"
            ):
                self.error_counts["student.join_team_rejected"] += 1
                return "closed"
            if response is not None and 400 <= response.status_code < 500:
                self.error_counts[
                    f"{operation}_http_{response.status_code}"
                ] += 1
                raise RuntimeError(
                    f"{operation} returned unexpected HTTP "
                    f"{response.status_code}"
                )
            retryable = response is None or (
                response is not None and response.status_code == 503
            )
            if not retryable or attempt == 1:
                self.error_counts[f"{operation}_not_acknowledged"] += 1
                return "retry"
            delay = 2.0
            if response is not None:
                try:
                    delay = float(
                        response.headers.get("Retry-After")
                        or response.json().get("retry_after")
                        or 2
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    delay = 2.0
            await asyncio.sleep(min(5.0, max(0.5, delay)))
        return "retry"

    async def login(self, student: StudentBot) -> bool:
        if student.client is not None:
            await student.client.aclose()
            student.client = None
        await asyncio.sleep(self.settings.speed * (student.number - 1) * 0.04)
        student.client = httpx.AsyncClient(
            base_url=student.base_url,
            follow_redirects=False,
            headers={
                "Accept-Encoding": "gzip",
                "User-Agent": "Popping-Classroom-Simulator/1.0",
            },
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )
        await self.request(
            student, "student.login_page", "GET", f"/login/{SLUG}"
        )
        response = await self.request(
            student,
            "student.login",
            "POST",
            f"/login/{SLUG}",
            data={"student_id": student.student_id, "pin": STUDENT_PIN},
        )
        if response is None or response.status_code != 302:
            self.error_counts["login_failed"] += 1
            return False
        dashboard = await self.request(
            student, "student.dashboard", "GET", "/dashboard"
        )
        if dashboard is None or dashboard.status_code != 200:
            self.error_counts["dashboard_failed"] += 1
            return False
        assets = await asyncio.gather(
            *(
                self.request(student, "student.static", "GET", asset)
                for asset in STATIC_ASSETS
            )
        )
        if any(
            response is None or response.status_code != 200
            for response in assets
        ):
            self.error_counts["static_asset_failed"] += 1
            return False
        return True

    async def sync_secondary(self, student: StudentBot) -> None:
        state = student.last_state
        roster_version = int(state.get("roster_version") or 0)
        if student.roster_version != roster_version:
            response = await self.request(
                student, "student.teams", "GET", "/api/teams"
            )
            if response is not None and response.status_code == 200:
                payload = response.json()
                student.available_teams = (
                    payload if isinstance(payload, list) else []
                )
                my_team_id = (state.get("my_team") or {}).get("id")
                if my_team_id:
                    current_team = next(
                        (
                            team for team in student.available_teams
                            if team.get("id") == my_team_id
                        ),
                        None,
                    )
                    if current_team:
                        members = tuple(
                            str(member.get("student_id"))
                            for member in current_team.get("members") or []
                            if member.get("student_id")
                        )
                        if student.student_id in members:
                            student.team_members = members
                        student.target_team_id = int(my_team_id)
                student.roster_version = roster_version

        if state.get("phase") == "discussion":
            context = (
                int(state.get("discussion_week") or 1),
                int(state.get("discussion_questions_version") or 0),
            )
            if student.question_context != context:
                response = await self.request(
                    student,
                    "student.discussion_questions",
                    "GET",
                    "/api/discussion_questions",
                )
                if response is not None and response.status_code == 200:
                    payload = response.json()
                    student.visible_discussion_questions = bool(
                        payload.get("questions")
                    )
                    student.question_context = context
        else:
            student.visible_discussion_questions = False
            student.question_context = None

        presentation_key = str(state.get("poll_question_key") or "")
        challenge_keys = ",".join(
            sorted(
                str(item.get("challenge_key"))
                for item in state.get("active_challenges") or []
                if item.get("challenge_key")
            )
        )
        response_context = ""
        if state.get("phase") == "discussion":
            response_context = f"discussion:{state.get('discussion_week') or 1}"
        elif presentation_key:
            response_context = f"competition:{presentation_key}:{challenge_keys}"
        if response_context and response_context != student.response_context:
            response = await self.request(
                student, "student.my_responses", "GET", "/api/my_responses"
            )
            if response is not None and response.status_code == 200:
                student.response_context = response_context
        elif not response_context:
            student.response_context = None

    def participates(self, student: StudentBot, event: str) -> bool:
        return stable_fraction(
            self.settings.seed, student.number, event, "participates"
        ) < self.settings.participation_rate

    def delay(
        self, student: StudentBot, event: str, minimum: float, maximum: float
    ) -> float:
        fraction = stable_fraction(
            self.settings.seed, student.number, event, "delay"
        )
        return self.settings.speed * (minimum + (maximum - minimum) * fraction)

    def score(self, student: StudentBot, event: str) -> int:
        return 1 + int(stable_fraction(
            self.settings.seed, student.number, event, "score"
        ) * 5)

    async def react_setup(self, student: StudentBot, state: dict[str, Any]) -> None:
        if state.get("my_team"):
            student.done.add("joined")
            return
        student.done.discard("joined")
        if state.get("teams_locked") or time.monotonic() < student.join_due:
            return
        capacity = int(
            state.get("max_members") or self.settings.max_members
        )
        available = [
            team for team in student.available_teams
            if team.get("id") is not None
            and int(team.get("member_count") or 0) < capacity
        ]
        if not available:
            student.join_due = time.monotonic() + 3
            return
        target = next(
            (
                team for team in available
                if int(team["id"]) == student.target_team_id
            ),
            min(
                available,
                key=lambda team: (
                    int(team.get("member_count") or 0),
                    int(team["id"]),
                ),
            ),
        )
        student.target_team_id = int(target["id"])
        result = await self.safe_post(
            student,
            "student.join_team",
            "/api/join_team",
            {"team_id": student.target_team_id},
        )
        if result == "ok":
            student.done.add("joined")
            student.state_version = None
        else:
            student.join_due = time.monotonic() + 3

    async def react_discussion(
        self, student: StudentBot, state: dict[str, Any]
    ) -> None:
        if not state.get("my_team"):
            return
        session_key = int(state.get("session_key") or 0)
        action_key = f"thumb:{session_key}"
        if action_key in student.done:
            return
        if not self.participates(student, action_key):
            student.done.add(action_key)
            return
        due = student.due.setdefault(
            action_key,
            time.monotonic() + self.delay(student, action_key, 2, 18),
        )
        if time.monotonic() < due:
            return
        members = student.team_members
        if student.student_id not in members:
            return
        if len(members) < 2:
            student.done.add(action_key)
            return
        position = members.index(student.student_id)
        recipient = members[(position + 1) % len(members)]
        result = await self.safe_post(
            student,
            "student.thumb",
            "/api/grade_peer",
            {"recipient_id": recipient, "selected": True},
        )
        if result in ("ok", "closed"):
            student.done.add(action_key)
        else:
            student.due[action_key] = time.monotonic() + 3

    async def react_competition(
        self, student: StudentBot, state: dict[str, Any]
    ) -> None:
        presentation_key = str(state.get("poll_question_key") or "")
        active_team = state.get("active_team") or {}
        presenting_team = active_team.get("id")
        my_team = state.get("my_team") or {}
        my_team_id = my_team.get("id")
        if not presentation_key or not presenting_team or not my_team_id:
            return

        hand_key = f"hand:{presentation_key}"
        team_candidate = student.team_members[0] if student.team_members else None
        if (
            my_team_id != presenting_team
            and student.student_id == team_candidate
            and hand_key not in student.done
        ):
            due = student.due.setdefault(
                hand_key,
                time.monotonic() + self.delay(student, hand_key, 3, 14),
            )
            if time.monotonic() >= due:
                result = await self.safe_post(
                    student,
                    "student.raise_hand",
                    "/api/raise_hand",
                    {"presentation_key": presentation_key},
                )
                if result in ("ok", "closed"):
                    student.done.add(hand_key)
                else:
                    student.due[hand_key] = time.monotonic() + 3

        challenge_open = bool(state.get("challenge_ratings_open"))
        for challenge in state.get("active_challenges") or []:
            challenge_key = str(challenge.get("challenge_key") or "")
            challenger_team = challenge.get("challenger_team_id")
            if not challenge_key or not challenge_eligible(
                int(my_team_id), int(presenting_team),
                int(challenger_team) if challenger_team else None,
            ):
                continue
            event = f"challenge:{challenge_key}"
            if challenge_key in student.submitted_challenges:
                continue
            if not self.participates(student, event):
                student.done.add(event)
                continue
            if not challenge_open:
                student.due.pop(event, None)
                continue
            due = student.due.setdefault(
                event,
                time.monotonic() + self.delay(student, event, 2, 20),
            )
            if time.monotonic() < due:
                continue
            result = await self.safe_post(
                student,
                "student.challenge_rating",
                "/api/submit_challenge_rating",
                {"challenge_key": challenge_key, "score": self.score(student, event)},
            )
            if result == "ok":
                student.submitted_challenges.add(challenge_key)
                student.done.add(event)
            elif result == "closed":
                student.due.pop(event, None)
            else:
                student.due[event] = time.monotonic() + 3

        rating_event = f"presentation:{presentation_key}"
        if not presentation_eligible(int(my_team_id), int(presenting_team)):
            return
        if presentation_key in student.submitted_presentations:
            return
        if not self.participates(student, rating_event):
            student.done.add(rating_event)
            return
        if not state.get("poll_active"):
            student.due.pop(rating_event, None)
            return
        due = student.due.setdefault(
            rating_event,
            time.monotonic() + self.delay(student, rating_event, 3, 30),
        )
        if time.monotonic() < due:
            return
        result = await self.safe_post(
            student,
            "student.presentation_rating",
            "/api/submit_rating",
            {
                "presentation_key": presentation_key,
                "q1_developed": self.score(student, rating_event + ":q1"),
                "q2_easy": self.score(student, rating_event + ":q2"),
            },
        )
        if result == "ok":
            student.submitted_presentations.add(presentation_key)
            student.done.add(rating_event)
        elif result == "closed":
            student.due.pop(rating_event, None)
        else:
            student.due[rating_event] = time.monotonic() + 3

    async def react(self, student: StudentBot) -> None:
        state = student.last_state
        phase = state.get("phase")
        if phase == "setup":
            await self.react_setup(student, state)
        elif phase == "discussion":
            await self.react_discussion(student, state)
        elif phase == "competition":
            await self.react_competition(student, state)

    async def reload_dashboard(self, student: StudentBot, phase: str) -> bool:
        dashboard = await self.request(
            student, "student.phase_reload", "GET", "/dashboard"
        )
        if dashboard is not None and dashboard.status_code == 200:
            student.page_phase = phase
            student.phase_reload_pending = None
            student.dashboard_failures = 0
            student.state_version = None
            return True
        student.dashboard_failures += 1
        self.error_counts["phase_reload_failed"] += 1
        if student.dashboard_failures >= 5:
            raise RuntimeError(
                f"{student.student_id} could not reload the {phase} dashboard"
            )
        return False

    async def student_loop(self, student: StudentBot) -> None:
        for attempt in range(1, 6):
            if await self.login(student):
                break
            if self.stop_event.is_set():
                return
            await asyncio.sleep(min(10, attempt * 2))
        else:
            raise RuntimeError(
                f"{student.student_id} could not complete login and page load"
            )
        while not self.stop_event.is_set():
            params: dict[str, Any] = {}
            if student.state_version is not None:
                params["since"] = student.state_version
            active_question = student.last_state.get("active_question") or {}
            if active_question.get("id"):
                params["known_question_id"] = active_question["id"]
            if active_question.get("revision"):
                params["known_question_revision"] = active_question["revision"]
            response = await self.request(
                student, "student.poll", "GET", "/api/poll", params=params
            )
            if response is not None and response.status_code == 401:
                raise RuntimeError(
                    f"{student.student_id} lost the student session"
                )
            if response is None or response.status_code != 200:
                student.poll_failures = min(student.poll_failures + 1, 5)
                delay = min(10, 2**student.poll_failures) + student.rng.random()
                await asyncio.sleep(delay)
                continue
            student.poll_failures = 0
            payload = response.json()
            if payload.get("state_version") is not None:
                student.state_version = int(payload["state_version"])
            if payload.get("changed") is False:
                apply_compact_poll_close(student.last_state, payload)
            else:
                state = payload.get("state") or {}
                student.last_state = state
                phase = str(state.get("phase") or "setup")
                if student.page_phase != phase:
                    student.phase_reload_pending = phase
            if student.phase_reload_pending:
                reloaded = await self.reload_dashboard(
                    student, student.phase_reload_pending
                )
                if not reloaded:
                    await asyncio.sleep(1 + student.rng.random())
                    continue
            await self.sync_secondary(student)
            await self.react(student)
            interval = max(
                0.5, float(payload.get("poll_interval") or 1000) / 1000
            )
            await asyncio.sleep(interval * (1 + student.rng.random() * 0.2))

    def database_snapshot(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {}
        try:
            with closing(sqlite3.connect(self.db_path, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                state = db.execute("SELECT * FROM course_state LIMIT 1").fetchone()
                if not state:
                    return {}
                state = dict(state)
                course_id = state["course_id"]
                session_key = state.get("session_key") or 0
                presentation_key = state.get("poll_question_key") or ""
                students = db.execute(
                    """SELECT COUNT(*) AS total,
                              SUM(CASE WHEN last_login_at IS NOT NULL THEN 1 ELSE 0 END)
                                  AS logged_in,
                              SUM(CASE WHEN team_id IS NOT NULL THEN 1 ELSE 0 END)
                                  AS assigned
                       FROM students WHERE course_id = ? AND is_active = 1""",
                    (course_id,),
                ).fetchone()
                thumbs = db.execute(
                    """SELECT COUNT(*) AS votes,
                              COUNT(DISTINCT grader_id) AS participants
                       FROM teammate_thumbs
                       WHERE course_id = ? AND session_key = ?""",
                    (course_id, session_key),
                ).fetchone()
                ratings = db.execute(
                    """SELECT COUNT(*) FROM presentation_ratings
                       WHERE course_id = ? AND session_key = ?
                         AND question_key = ?""",
                    (course_id, session_key, presentation_key),
                ).fetchone()[0] if presentation_key else 0
                hands = db.execute(
                    """SELECT COUNT(*) FROM challenge_hands
                       WHERE course_id = ? AND presentation_key = ?""",
                    (course_id, presentation_key),
                ).fetchone()[0] if presentation_key else 0
                challenge_ratings = db.execute(
                    """SELECT COUNT(*) FROM challenge_ratings
                       WHERE course_id = ? AND session_key = ?
                         AND presentation_key = ?""",
                    (course_id, session_key, presentation_key),
                ).fetchone()[0] if presentation_key else 0
                return {
                    "phase": state.get("phase"),
                    "session_key": session_key,
                    "students_total": students["total"] or 0,
                    "students_logged_in": students["logged_in"] or 0,
                    "students_assigned": students["assigned"] or 0,
                    "thumb_participants": thumbs["participants"] or 0,
                    "thumb_votes": thumbs["votes"] or 0,
                    "presentation_key": presentation_key or None,
                    "presentation_ratings": ratings,
                    "raised_hands": hands,
                    "challenge_ratings": challenge_ratings,
                    "poll_active": bool(state.get("poll_active")),
                }
        except sqlite3.Error as exc:
            self.error_counts[type(exc).__name__] += 1
            return {"database_error": str(exc)}

    def status_payload(self, status: str = "running") -> dict[str, Any]:
        recent = [item for item in self.requests if item[0] >= time.time() - 5]
        latencies = [item[2] for item in recent]
        return {
            "status": status,
            "updated_at": utc_text(),
            "controller_pid": os.getpid(),
            "controller_create_time": self.controller_create_time,
            "runtime_root": str(self.root),
            "server_log": str(self.server_log_path),
            "instructor_url": (
                f"{self.base_urls[0]}/instructor_login/{SLUG}"
                if self.base_urls else None
            ),
            "instructor_username": INSTRUCTOR_USERNAME,
            "instructor_pin": INSTRUCTOR_PIN,
            "student_pin": STUDENT_PIN,
            "settings": asdict(self.settings),
            "observed": self.database_snapshot(),
            "traffic": {
                "requests_last_5s": len(recent),
                "p95_ms_last_5s": round(percentile(latencies, 95), 1),
                "total_requests": self.total_requests,
                "student_tasks_active": sum(
                    not task.done() for task in self.student_tasks
                ),
                "actions_acknowledged": dict(self.action_counts),
                "errors": dict(self.error_counts),
            },
            "failure": self.failed_reason,
            "integrity": self.integrity,
        }

    def write_status(self, status: str = "running") -> dict[str, Any]:
        payload = self.status_payload(status)
        atomic_json(CURRENT_FILE, payload)
        return payload

    @staticmethod
    def summary_line(payload: dict[str, Any]) -> str:
        observed = payload.get("observed") or {}
        traffic = payload.get("traffic") or {}
        return (
            f"[{time.strftime('%H:%M:%S')}] "
            f"{observed.get('phase') or 'starting'} | "
            f"login {observed.get('students_logged_in', 0)}/"
            f"{observed.get('students_total', 0)} | "
            f"teams {observed.get('students_assigned', 0)}/"
            f"{observed.get('students_total', 0)} | "
            f"thumbs {observed.get('thumb_votes', 0)} | "
            f"presentation {observed.get('presentation_ratings', 0)} | "
            f"challenge {observed.get('challenge_ratings', 0)} | "
            f"hands {observed.get('raised_hands', 0)} | "
            f"traffic {traffic.get('requests_last_5s', 0)}/5s | "
            f"p95 {traffic.get('p95_ms_last_5s', 0)}ms | "
            f"bots {traffic.get('student_tasks_active', 0)} | "
            f"errors {sum((traffic.get('errors') or {}).values())}"
        )

    async def supervise_task(
        self, label: str, awaitable: Any
    ) -> None:
        try:
            await awaitable
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error_counts["background_task_failed"] += 1
                self.failed_reason = (
                    f"{label} failed: {type(exc).__name__}: {exc}"
                )
                print(
                    f"Simulation task failed: {self.failed_reason}",
                    file=sys.stderr,
                    flush=True,
                )
                self.stop_event.set()
        else:
            if not self.stop_event.is_set():
                self.error_counts["background_task_stopped"] += 1
                self.failed_reason = f"{label} stopped unexpectedly"
                self.stop_event.set()

    async def status_loop(self) -> None:
        last_print = 0.0
        while not self.stop_event.is_set():
            payload = self.write_status("running")
            if time.monotonic() - last_print >= 5:
                print(self.summary_line(payload), flush=True)
                last_print = time.monotonic()
            await asyncio.sleep(2)

    async def safety_loop(self) -> None:
        high_cpu_samples = 0
        psutil.cpu_percent(interval=None)
        while not self.stop_event.is_set():
            if STOP_FILE.exists():
                self.stop_event.set()
                return
            exited = [
                {"pid": process.pid, "exit_code": process.poll()}
                for process in self.servers if process.poll() is not None
            ]
            if exited:
                self.failed_reason = f"Server process exited: {exited}"
                self.stop_event.set()
                return
            if (
                self.settings.max_runtime
                and time.monotonic() - self.started_monotonic
                >= self.settings.max_runtime
            ):
                print("Maximum simulation runtime reached.", flush=True)
                self.stop_event.set()
                return
            rss = 0
            try:
                controller = psutil.Process(os.getpid())
                processes = [controller] + controller.children(recursive=True)
                rss = sum(process.memory_info().rss for process in processes)
            except psutil.Error:
                pass
            cpu = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory().percent
            high_cpu_samples = high_cpu_samples + 1 if cpu >= 95 else 0
            if rss > 700 * 1024**2 or memory >= 90 or high_cpu_samples >= 10:
                self.failed_reason = (
                    f"Safety limit reached: rss={rss / 1024**2:.0f}MB, "
                    f"memory={memory:.1f}%, cpu={cpu:.1f}%"
                )
                self.stop_event.set()
                return
            await asyncio.sleep(1)

    async def stop_servers(self) -> None:
        tracked: dict[int, psutil.Process] = {}
        for process in self.servers:
            try:
                root = psutil.Process(process.pid)
                for child in root.children(recursive=True):
                    tracked[child.pid] = child
                tracked[root.pid] = root
            except psutil.Error:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
        processes = list(tracked.values())
        for process in processes:
            try:
                process.terminate()
            except psutil.Error:
                pass
        if processes:
            _, alive = psutil.wait_procs(processes, timeout=8)
            for process in alive:
                try:
                    process.kill()
                except psutil.Error:
                    pass
            if alive:
                psutil.wait_procs(alive, timeout=3)
        for process in self.servers:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self.server_log:
            self.server_log.close()
            self.server_log = None

    async def remove_owned_root(self) -> None:
        if not self.owns_root:
            return
        sentinel = self.root / ROOT_SENTINEL
        if not sentinel.is_file():
            raise RuntimeError(
                f"Refusing to remove simulation data without {ROOT_SENTINEL}"
            )
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                shutil.rmtree(self.root)
                return
            except OSError as exc:
                last_error = exc
                await asyncio.sleep(min(2.0, 0.25 * (2**attempt)))
        raise RuntimeError(
            f"Could not remove temporary simulation data: {last_error}"
        )

    def audit_database(self) -> dict[str, Any]:
        try:
            with closing(sqlite3.connect(self.db_path)) as db:
                integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
                foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
            return {
                "integrity_check": integrity,
                "foreign_key_violations": len(foreign_keys),
            }
        except sqlite3.Error as exc:
            return {"error": str(exc)}

    async def run(self) -> int:
        try:
            self.acquire_owner_lock()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            if self.owns_root:
                try:
                    self.root.rmdir()
                except OSError:
                    pass
            return 1
        clean = False
        try:
            STOP_FILE.unlink(missing_ok=True)
            self.write_status("starting")
            self.seed()
            await self.start_servers()
            self.started_monotonic = time.monotonic()
            self.build_students()
            ready = self.write_status("running")
            print("\nSimulated classroom is ready.", flush=True)
            print(f"Instructor URL: {ready['instructor_url']}", flush=True)
            print(f"Username: {INSTRUCTOR_USERNAME}", flush=True)
            print(f"PIN: {INSTRUCTOR_PIN}", flush=True)
            print(
                "Students join gradually. You control every instructor action.",
                flush=True,
            )
            print("Press Ctrl+C to stop.\n", flush=True)
            self.student_tasks = [
                asyncio.create_task(
                    self.supervise_task(
                        f"student {student.student_id}",
                        self.student_loop(student),
                    )
                )
                for student in self.students
            ]
            controller_tasks = [
                asyncio.create_task(
                    self.supervise_task("status monitor", self.status_loop())
                ),
                asyncio.create_task(
                    self.supervise_task("safety monitor", self.safety_loop())
                ),
            ]
            self.tasks = [*self.student_tasks, *controller_tasks]
            await self.stop_event.wait()
            clean = self.failed_reason is None
        except StopRequested:
            clean = True
            print("Stop requested during simulator startup.", flush=True)
        except asyncio.CancelledError:
            clean = self.failed_reason is None
            raise
        except Exception as exc:
            self.failed_reason = f"{type(exc).__name__}: {exc}"
            print(f"Simulation failed: {self.failed_reason}", file=sys.stderr)
        finally:
            self.stop_event.set()
            for task in self.tasks:
                task.cancel()
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)
            for student in self.students:
                if student.client is not None:
                    await student.client.aclose()
            await self.stop_servers()
            final_observed = self.database_snapshot()
            if self.db_path.exists():
                self.integrity = self.audit_database()
                clean = clean and self.integrity == {
                    "integrity_check": "ok",
                    "foreign_key_violations": 0,
                }
            if clean and not self.settings.keep_data and self.owns_root:
                try:
                    await self.remove_owned_root()
                except Exception as exc:
                    self.failed_reason = f"{type(exc).__name__}: {exc}"
                    clean = False
            if self.root.exists():
                print(f"Simulation data retained at: {self.root}", flush=True)
            status = "stopped" if clean else "failed"
            final_status = self.status_payload(status)
            final_status["observed"] = final_observed
            atomic_json(CURRENT_FILE, final_status)
            STOP_FILE.unlink(missing_ok=True)
            self.release_owner_lock()
        return 0 if clean else 1


def current_running() -> dict[str, Any] | None:
    current = read_json(CURRENT_FILE)
    if not current:
        return None
    if current.get("status") in ("starting", "running") and process_alive(
        current.get("controller_pid"),
        current.get("controller_create_time"),
    ):
        return current
    return None


def status_for_display(current: dict[str, Any]) -> dict[str, Any]:
    status = current.get("status")
    if status not in ("starting", "running"):
        return current
    alive = process_alive(
        current.get("controller_pid"),
        current.get("controller_create_time"),
    )
    stale_after = 60 if status == "starting" else 15
    updated_at = current.get("updated_at")
    fresh = False
    if updated_at:
        try:
            updated = datetime.fromisoformat(str(updated_at))
            fresh = (
                datetime.now(timezone.utc) - updated
            ).total_seconds() <= stale_after
        except (TypeError, ValueError):
            pass
    if alive and fresh:
        return current
    result = dict(current)
    result["status"] = "stale"
    result["failure"] = result.get("failure") or (
        "Simulator status is no longer updating"
        if alive else "Simulator controller is no longer running"
    )
    return result


def print_status(current: dict[str, Any]) -> None:
    print(f"Status: {current.get('status', 'unknown')}")
    if current.get("instructor_url"):
        print(f"Instructor URL: {current['instructor_url']}")
        print(f"Username: {current.get('instructor_username')}")
        print(f"PIN: {current.get('instructor_pin')}")
    observed = current.get("observed") or {}
    if observed:
        print(
            "Students: "
            f"{observed.get('students_logged_in', 0)} logged in, "
            f"{observed.get('students_assigned', 0)} assigned"
        )
        print(
            f"Phase: {observed.get('phase')} | "
            f"thumbs: {observed.get('thumb_votes', 0)} from "
            f"{observed.get('thumb_participants', 0)} students | "
            f"presentation ratings: {observed.get('presentation_ratings', 0)} | "
            f"challenge ratings: {observed.get('challenge_ratings', 0)} | "
            f"hands: {observed.get('raised_hands', 0)}"
        )
    traffic = current.get("traffic") or {}
    if traffic:
        print(
            f"Traffic: {traffic.get('requests_last_5s', 0)} requests/5s, "
            f"p95 {traffic.get('p95_ms_last_5s', 0)}ms, "
            f"errors {sum((traffic.get('errors') or {}).values())}"
        )
    if current.get("failure"):
        print(f"Failure: {current['failure']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run isolated simulated students while a human controls the "
            "normal Popping instructor page."
        )
    )
    parser.add_argument("--students", type=int, default=40)
    parser.add_argument("--teams", type=int, default=8)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--port", type=int, default=5100,
        help="First local port; use 0 to choose automatically",
    )
    parser.add_argument("--seed", type=int, default=432)
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="Scale student action delays; 0.5 is twice as fast",
    )
    parser.add_argument(
        "--participation-rate", type=float, default=1.0,
        help="Share of eligible students who vote, from 0 to 1",
    )
    parser.add_argument(
        "--max-runtime", type=int, default=3600,
        help="Safety stop in seconds; use 0 for no time limit",
    )
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--stop", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.status:
        while True:
            current = read_json(CURRENT_FILE)
            if not current:
                print("No simulated classroom has been started.")
                return 1
            current = status_for_display(current)
            print_status(current)
            if not args.watch or current.get("status") not in ("starting", "running"):
                return 0
            time.sleep(2)
            print()
    if args.stop:
        current = current_running()
        if not current:
            print("No simulated classroom is running.")
            return 0
        CONTROL_DIR.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text(utc_text(), encoding="utf-8")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process_alive(
            current.get("controller_pid"),
            current.get("controller_create_time"),
        ):
            time.sleep(0.25)
        if process_alive(
            current.get("controller_pid"),
            current.get("controller_create_time"),
        ):
            print("Stop requested. The simulator is still shutting down.")
            return 1
        print("Simulated classroom stopped.")
        return 0

    existing = current_running()
    if existing:
        print("A simulated classroom is already running.")
        print_status(existing)
        return 1
    settings = Settings(
        students=args.students,
        teams=args.teams,
        workers=args.workers,
        port=args.port,
        seed=args.seed,
        speed=args.speed,
        participation_rate=args.participation_rate,
        max_runtime=args.max_runtime,
        keep_data=args.keep_data,
    )
    try:
        settings.validate()
    except ValueError as exc:
        print(f"Invalid simulator settings: {exc}", file=sys.stderr)
        return 2
    simulator = ClassroomSimulator(settings)
    try:
        return asyncio.run(simulator.run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
