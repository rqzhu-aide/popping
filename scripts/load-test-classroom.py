#!/usr/bin/env python3
"""Run an isolated, real-HTTP classroom load test.

The script creates a disposable 80-student course, starts independent local
server processes, runs the main classroom workflow, and verifies the resulting
database. It never uses the repository's normal data or classes directories.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx
import psutil
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SLUG = "loadtest"
INSTRUCTOR_USERNAME = "load-instructor"
INSTRUCTOR_PIN = "9999"
STUDENT_PIN = "2468"
TEAM_COUNT = 8
STUDENTS_PER_TEAM = 10
STUDENT_COUNT = TEAM_COUNT * STUDENTS_PER_TEAM
STATIC_ASSETS = (
    "/static/css/style.css",
    "/static/vendor/atom-one-dark.min.css",
    "/static/vendor/marked.min.js",
    "/static/vendor/purify.min.js",
    "/static/vendor/highlight.min.js",
    "/static/js/app.js",
)


def percentile(values: Iterable[float], pct: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(0, math.ceil((pct / 100) * len(ordered)) - 1)
    return ordered[rank]


@dataclass
class Sample:
    operation: str
    method: str
    path: str
    started: float
    elapsed_ms: float
    status: int | None
    wire_bytes: int
    decoded_bytes: int
    error: str | None = None


class Metrics:
    def __init__(self) -> None:
        self.samples: list[Sample] = []
        self.unexpected: list[str] = []

    async def request(
        self,
        client: httpx.AsyncClient,
        operation: str,
        method: str,
        path: str,
        *,
        expected: tuple[int, ...] = (200,),
        timeout: float = 15.0,
        allow_transport_error: bool = False,
        **kwargs: Any,
    ) -> httpx.Response | None:
        started_wall = time.time()
        started = time.perf_counter()
        try:
            response = await client.request(
                method, path, timeout=httpx.Timeout(timeout), **kwargs
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            try:
                wire_bytes = int(response.headers.get("content-length", ""))
            except ValueError:
                wire_bytes = len(response.content)
            sample = Sample(
                operation=operation,
                method=method,
                path=path.split("?", 1)[0],
                started=started_wall,
                elapsed_ms=elapsed_ms,
                status=response.status_code,
                wire_bytes=wire_bytes,
                decoded_bytes=len(response.content),
            )
            self.samples.append(sample)
            response.extensions["load_test_sample"] = sample
            if response.status_code not in expected:
                sample.error = f"unexpected_status_{response.status_code}"
                body = response.text[:240].replace("\n", " ")
                self.unexpected.append(
                    f"{operation}: HTTP {response.status_code}: {body}"
                )
            if path.startswith("/api/") and response.status_code != 204:
                content_type = response.headers.get("content-type", "")
                if "application/json" not in content_type:
                    sample.error = sample.error or "non_json_api_response"
                    self.unexpected.append(
                        f"{operation}: API returned {content_type or 'no content type'}"
                    )
            return response
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.samples.append(
                Sample(
                    operation=operation,
                    method=method,
                    path=path.split("?", 1)[0],
                    started=started_wall,
                    elapsed_ms=elapsed_ms,
                    status=None,
                    wire_bytes=0,
                    decoded_bytes=0,
                    error=type(exc).__name__,
                )
            )
            if not allow_transport_error:
                self.unexpected.append(f"{operation}: {type(exc).__name__}")
            return None

    def summary(self, samples: Iterable[Sample] | None = None) -> dict[str, Any]:
        grouped: dict[str, list[Sample]] = defaultdict(list)
        selected = self.samples if samples is None else samples
        for sample in selected:
            grouped[sample.operation].append(sample)
        result: dict[str, Any] = {}
        for operation, samples in sorted(grouped.items()):
            latencies = [sample.elapsed_ms for sample in samples]
            statuses = Counter(
                "transport_error" if sample.status is None else str(sample.status)
                for sample in samples
            )
            result[operation] = {
                "count": len(samples),
                "status": dict(sorted(statuses.items())),
                "p50_ms": round(percentile(latencies, 50) or 0, 2),
                "p95_ms": round(percentile(latencies, 95) or 0, 2),
                "p99_ms": round(percentile(latencies, 99) or 0, 2),
                "max_ms": round(max(latencies, default=0), 2),
                "wire_bytes": sum(sample.wire_bytes for sample in samples),
                "decoded_bytes": sum(sample.decoded_bytes for sample in samples),
            }
        return result


@dataclass
class Student:
    number: int
    student_id: str
    db_id: int
    team_number: int
    team_id: int
    client: httpx.AsyncClient
    page_phase: str = "setup"
    page_team_id: int | None = None
    state_version: int | None = None
    known_question_id: int | None = None
    known_question_revision: str | None = None
    last_state: dict[str, Any] | None = None
    failures: int = 0
    pause_until: float = 0
    last_success_at: float = 0
    phase_seen_at: dict[str, float] = field(default_factory=dict)
    presentation_seen_at: dict[str, float] = field(default_factory=dict)
    poll_open_seen_at: dict[str, float] = field(default_factory=dict)
    roster_synced_version: int | None = None
    roster_inflight: asyncio.Task[Any] | None = None
    questions_synced_context: tuple[int, int] | None = None
    questions_inflight: asyncio.Task[Any] | None = None
    questions_seen_at: dict[int, float] = field(default_factory=dict)
    responses_synced_context: str | None = None
    responses_inflight: asyncio.Task[Any] | None = None
    rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.number)


class ClassroomLoadTest:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = Path(tempfile.mkdtemp(prefix="popping-loadtest-"))
        self.data_dir = self.root / "data"
        self.classes_dir = self.root / "classes"
        self.db_path = self.data_dir / SLUG / "popping.db"
        self.log_path = self.root / "server.log"
        self.report_path = self.root / "report.json"
        self.metrics = Metrics()
        self.checks: list[dict[str, Any]] = []
        self.diagnostics: dict[str, Any] = {}
        self.students: list[Student] = []
        self.team_ids: list[int] = []
        self.servers: list[subprocess.Popen[str]] = []
        self.server_log: Any = None
        self.base_url = ""
        self.base_urls: list[str] = []
        self.instructor: httpx.AsyncClient | None = None
        self.instructor_poll_client: httpx.AsyncClient | None = None
        self.latest_instructor_state: dict[str, Any] | None = None
        self.stop_event = asyncio.Event()
        self.safety_abort = asyncio.Event()
        self.poll_tasks: list[asyncio.Task[Any]] = []
        self.background_task_errors: list[str] = []
        self.monitor_samples: list[dict[str, float]] = []
        self.server_pids: set[int] = set()
        self.question_change_pending = False
        self.expected_questions: dict[tuple[int, int], tuple[str, str]] = {}
        self.fault_test_started_at: float | None = None

    def check(self, name: str, passed: bool, detail: Any = None) -> None:
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})
        label = "PASS" if passed else "FAIL"
        print(f"[{label}] {name}: {detail}", flush=True)

    def seed(self) -> None:
        self.data_dir.mkdir(parents=True)
        self.classes_dir.mkdir(parents=True)
        source_class = PROJECT_ROOT / "classes" / "demo"
        target_class = self.classes_dir / SLUG
        shutil.copytree(source_class, target_class)
        colors = (
            "#2563eb", "#dc2626", "#16a34a", "#9333ea",
            "#ea580c", "#0891b2", "#ca8a04", "#db2777",
        )
        config_data = {
            "slug": SLUG,
            "name": "Isolated Classroom Load Test",
            "code": "LOAD 080",
            "semester": "Synthetic",
            "active": True,
            "team_pool_size": TEAM_COUNT,
            "max_teams": TEAM_COUNT,
            "max_members_per_team": STUDENTS_PER_TEAM,
            "poll_duration": 300,
            "teams": [{"color": color} for color in colors],
        }
        (target_class / "course.yaml").write_text(
            yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8"
        )

        course_data = self.data_dir / SLUG
        course_data.mkdir(parents=True)
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript((PROJECT_ROOT / "popping.sql").read_text(encoding="utf-8"))
        instructor_id = db.execute(
            "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
            (INSTRUCTOR_USERNAME, "Load Test Instructor", INSTRUCTOR_PIN),
        ).lastrowid
        course_id = db.execute(
            """INSERT INTO courses (name, code, semester, slug, instructor_id)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "Isolated Classroom Load Test", "LOAD 080", "Synthetic",
                SLUG, instructor_id,
            ),
        ).lastrowid
        for number, color in enumerate(colors, 1):
            team_id = db.execute(
                "INSERT INTO teams (course_id, name, color) VALUES (?, ?, ?)",
                (course_id, f"Team {number}", color),
            ).lastrowid
            self.team_ids.append(int(team_id))
        self.student_seed: list[dict[str, int | str]] = []
        for number in range(1, STUDENT_COUNT + 1):
            student_id = f"load{number:03d}"
            db_id = db.execute(
                """INSERT INTO students
                   (course_id, student_id, name, pin, team_id)
                   VALUES (?, ?, ?, ?, NULL)""",
                (course_id, student_id, f"Student {number:03d}", STUDENT_PIN),
            ).lastrowid
            team_number = (number - 1) // STUDENTS_PER_TEAM + 1
            self.student_seed.append(
                {
                    "number": number,
                    "student_id": student_id,
                    "db_id": int(db_id),
                    "team_number": team_number,
                    "team_id": self.team_ids[team_number - 1],
                }
            )
        db.execute(
            """INSERT INTO course_state
               (course_id, phase, max_teams, max_members_per_team,
                discussion_week, presentation_history, roster_version,
                session_key, active_challenges_json)
               VALUES (?, 'setup', ?, ?, 1, '[]', 0, 1, '[]')""",
            (course_id, TEAM_COUNT, STUDENTS_PER_TEAM),
        )
        db.commit()
        db.close()

    @staticmethod
    def free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    async def start_server(self) -> None:
        if self.args.port:
            ports = [
                self.args.port + offset for offset in range(self.args.workers)
            ]
        else:
            ports = []
            while len(ports) < self.args.workers:
                port = self.free_port()
                if port not in ports:
                    ports.append(port)
        self.base_urls = [f"http://127.0.0.1:{port}" for port in ports]
        self.base_url = self.base_urls[0]
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
        self.server_log = self.log_path.open("w", encoding="utf-8")
        for index, (port, base_url) in enumerate(
            zip(ports, self.base_urls, strict=True), 1
        ):
            self.server_log.write(
                f"\n=== Server {index}/{self.args.workers}: {base_url} ===\n"
            )
            self.server_log.flush()
            command = [
                sys.executable, "-m", "uvicorn", "load_test_app:app",
                "--app-dir", "scripts", "--interface", "wsgi",
                "--host", "127.0.0.1", "--port", str(port),
                "--workers", "1", "--lifespan", "off", "--backlog", "512",
                "--timeout-keep-alive", "5", "--no-access-log",
            ]
            server = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=self.server_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.servers.append(server)
            await self.wait_for_server(server, base_url)
            print(
                f"Server {index}/{self.args.workers} ready at {base_url}",
                flush=True,
            )

    @staticmethod
    async def wait_for_server(
        server: subprocess.Popen[str], base_url: str
    ) -> None:
        deadline = time.monotonic() + 60
        async with httpx.AsyncClient(base_url=base_url) as client:
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    break
                try:
                    response = await client.get(f"/login/{SLUG}", timeout=2)
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        raise RuntimeError(f"Load-test server at {base_url} did not start")

    async def stop_server(self) -> None:
        processes_by_pid: dict[int, psutil.Process] = {}
        for server in self.servers:
            try:
                master = psutil.Process(server.pid)
                for process in master.children(recursive=True) + [master]:
                    processes_by_pid[process.pid] = process
            except psutil.Error:
                pass
        processes = list(processes_by_pid.values())
        for process in reversed(processes):
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
        for server in self.servers:
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)
        if self.server_log:
            self.server_log.close()

    async def resource_monitor(self) -> None:
        high_cpu_samples = 0
        while not self.stop_event.is_set():
            rss = 0
            exited = [
                {"pid": server.pid, "exit_code": server.poll()}
                for server in self.servers
                if server.poll() is not None
            ]
            if exited:
                self.diagnostics["server_process_exit"] = exited
                self.safety_abort.set()
                self.stop_event.set()
                print(f"Server process exited unexpectedly: {exited}", flush=True)
                return
            processes_by_pid: dict[int, psutil.Process] = {}
            for server in self.servers:
                try:
                    master = psutil.Process(server.pid)
                    for process in [master] + master.children(recursive=True):
                        processes_by_pid[process.pid] = process
                except psutil.Error:
                    pass
            processes = list(processes_by_pid.values())
            self.server_pids.update(process.pid for process in processes)
            for process in processes:
                try:
                    rss += process.memory_info().rss
                except psutil.Error:
                    pass
            cpu = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory().percent
            self.monitor_samples.append(
                {
                    "time": time.time(),
                    "system_cpu_pct": float(cpu),
                    "system_memory_pct": float(memory),
                    "server_rss_mb": round(rss / (1024 * 1024), 2),
                }
            )
            high_cpu_samples = high_cpu_samples + 1 if cpu >= 95 else 0
            if rss > 1.5 * 1024**3 or memory >= 92 or high_cpu_samples >= 15:
                self.safety_abort.set()
                self.stop_event.set()
                print("Safety limit reached. Stopping the load run.", flush=True)
                return
            await asyncio.sleep(1)

    def new_client(self, base_url: str | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url or self.base_url,
            follow_redirects=False,
            headers={
                "Accept-Encoding": "gzip",
                "User-Agent": "Popping-Isolated-Load-Test/1.0",
            },
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )

    async def login_instructor(self) -> None:
        self.instructor = self.new_client()
        await self.metrics.request(
            self.instructor, "instructor.login_page", "GET",
            f"/instructor_login/{SLUG}",
        )
        response = await self.metrics.request(
            self.instructor,
            "instructor.login",
            "POST",
            f"/instructor_login/{SLUG}",
            expected=(302,),
            data={"username": INSTRUCTOR_USERNAME, "pin": INSTRUCTOR_PIN},
        )
        self.check(
            "instructor login",
            bool(response and response.status_code == 302),
            response.status_code if response else "transport error",
        )
        await self.metrics.request(
            self.instructor, "instructor.dashboard", "GET", f"/instructor/{SLUG}"
        )
        self.instructor_poll_client = self.new_client()
        self.instructor_poll_client.cookies.update(self.instructor.cookies)

    async def login_student(self, seed: dict[str, int | str]) -> Student:
        number = int(seed["number"])
        base_url = self.base_urls[(number - 1) % len(self.base_urls)]
        client = self.new_client(base_url)
        student = Student(
            number=number,
            student_id=str(seed["student_id"]),
            db_id=int(seed["db_id"]),
            team_number=int(seed["team_number"]),
            team_id=int(seed["team_id"]),
            client=client,
        )
        await self.metrics.request(
            client, "student.login_page", "GET", f"/login/{SLUG}"
        )
        response = await self.metrics.request(
            client,
            "student.login",
            "POST",
            f"/login/{SLUG}",
            expected=(302,),
            data={"student_id": student.student_id, "pin": STUDENT_PIN},
        )
        if not response or response.status_code != 302:
            return student
        dashboard = await self.metrics.request(
            client, "student.dashboard", "GET", "/dashboard"
        )
        if dashboard and dashboard.status_code == 200:
            await asyncio.gather(
                *(
                    self.metrics.request(
                        client, "student.static", "GET", asset
                    )
                    for asset in STATIC_ASSETS
                )
            )
        return student

    async def login_students(self) -> None:
        self.students = list(
            await asyncio.gather(
                *(self.login_student(seed) for seed in self.student_seed)
            )
        )
        successes = sum(
            1 for sample in self.metrics.samples
            if sample.operation == "student.login" and sample.status == 302
        )
        self.check("80-student login burst", successes == STUDENT_COUNT, successes)

    async def join_teams(self) -> None:
        team_responses = await asyncio.gather(
            *(
                self.metrics.request(
                    student.client, "student.teams_before_join", "GET", "/api/teams"
                )
                for student in self.students
            )
        )
        valid_lists = 0
        for response in team_responses:
            if response and response.status_code == 200:
                data = response.json()
                teams = data if isinstance(data, list) else data.get("teams", [])
                if len(teams) == TEAM_COUNT:
                    valid_lists += 1
        self.check("team list visible to every student", valid_lists == STUDENT_COUNT, valid_lists)
        joins = await asyncio.gather(
            *(
                self.metrics.request(
                    student.client,
                    "student.join_team",
                    "POST",
                    "/api/join_team",
                    json={"team_id": student.team_id},
                )
                for student in self.students
            )
        )
        successes = sum(
            bool(response and response.status_code == 200) for response in joins
        )
        self.check("80 simultaneous team joins", successes == STUDENT_COUNT, successes)
        await asyncio.gather(
            *(
                self.metrics.request(
                    student.client, "student.dashboard_after_join", "GET", "/dashboard"
                )
                for student in self.students
            )
        )
        for student in self.students:
            student.page_team_id = student.team_id

    async def sync_roster(self, student: Student, version: int) -> None:
        try:
            response = await self.metrics.request(
                student.client, "student.sync_roster", "GET", "/api/teams"
            )
            if response and response.status_code == 200:
                student.roster_synced_version = version
        except Exception as exc:
            self.background_task_errors.append(
                f"{student.student_id} roster sync: {type(exc).__name__}: {exc}"
            )
        finally:
            student.roster_inflight = None

    async def sync_questions(
        self, student: Student, context: tuple[int, int]
    ) -> None:
        try:
            response = await self.metrics.request(
                student.client,
                "student.sync_discussion_questions",
                "GET",
                "/api/discussion_questions",
            )
            if response and response.status_code == 200:
                payload = response.json()
                questions = payload.get("questions")
                try:
                    response_context = (
                        int(payload.get("current_week")),
                        int(payload.get("version")),
                    )
                except (TypeError, ValueError):
                    response_context = None
                expected = self.expected_questions.get(context)
                expected_present = expected is None or (
                    isinstance(questions, list)
                    and any(
                        isinstance(question, dict)
                        and question.get("title") == expected[0]
                        and question.get("content") == expected[1]
                        for question in questions
                    )
                )
                if (
                    not self.question_change_pending
                    and response_context == context
                    and isinstance(questions, list)
                    and expected_present
                ):
                    student.questions_synced_context = context
                    student.questions_seen_at[context[1]] = time.time()
        except Exception as exc:
            self.background_task_errors.append(
                f"{student.student_id} question sync: {type(exc).__name__}: {exc}"
            )
        finally:
            student.questions_inflight = None

    async def sync_responses(self, student: Student, context: str) -> None:
        try:
            response = await self.metrics.request(
                student.client,
                "student.sync_my_responses",
                "GET",
                "/api/my_responses",
            )
            if response and response.status_code == 200:
                student.responses_synced_context = context
        except Exception as exc:
            self.background_task_errors.append(
                f"{student.student_id} response sync: {type(exc).__name__}: {exc}"
            )
        finally:
            student.responses_inflight = None
    def schedule_secondary(self, student: Student) -> None:
        state = student.last_state
        if not state:
            return
        roster_version = int(state.get("roster_version") or 0)
        if (
            student.roster_synced_version != roster_version
            and student.roster_inflight is None
        ):
            student.roster_inflight = asyncio.create_task(
                self.sync_roster(student, roster_version)
            )
        if state.get("phase") == "discussion":
            question_context = (
                int(state.get("discussion_week") or 1),
                int(state.get("discussion_questions_version") or 0),
            )
            if (
                student.questions_synced_context != question_context
                and student.questions_inflight is None
            ):
                student.questions_inflight = asyncio.create_task(
                    self.sync_questions(student, question_context)
                )
            response_context = f"discussion:{question_context[0]}"
        elif state.get("phase") == "competition" and state.get("poll_question_key"):
            response_context = f"competition:{state['poll_question_key']}"
        else:
            response_context = ""
        if (
            response_context
            and student.responses_synced_context != response_context
            and student.responses_inflight is None
        ):
            student.responses_inflight = asyncio.create_task(
                self.sync_responses(student, response_context)
            )

    async def student_poll_loop(self, student: Student) -> None:
        while not self.stop_event.is_set():
            if student.pause_until > time.monotonic():
                await asyncio.sleep(min(0.25, student.pause_until - time.monotonic()))
                continue
            params: dict[str, Any] = {}
            if student.state_version is not None:
                params["since"] = student.state_version
            if student.known_question_id is not None:
                params["known_question_id"] = student.known_question_id
            if student.known_question_revision:
                params["known_question_revision"] = student.known_question_revision
            response = await self.metrics.request(
                student.client,
                "student.poll",
                "GET",
                "/api/poll",
                params=params,
            )
            if not response or response.status_code != 200:
                student.failures = min(student.failures + 1, 5)
                await asyncio.sleep(min(10, 2**student.failures) + student.rng.random())
                continue
            student.failures = 0
            student.last_success_at = time.time()
            data = response.json()
            sample = response.extensions.get("load_test_sample")
            if sample:
                sample.operation = (
                    "student.poll.compact"
                    if data.get("changed") is False
                    else "student.poll.full"
                )
            if data.get("state_version") is not None:
                student.state_version = int(data["state_version"])
            if data.get("changed") is not False:
                state = data.get("state") or {}
                student.last_state = state
                phase = str(state.get("phase") or "setup")
                presentation_key = str(state.get("poll_question_key") or "")
                if presentation_key:
                    student.presentation_seen_at.setdefault(
                        presentation_key, time.time()
                    )
                    if state.get("poll_active"):
                        student.poll_open_seen_at.setdefault(
                            presentation_key, time.time()
                        )
                active_question = state.get("active_question") or {}
                if active_question and not active_question.get("content_unchanged"):
                    student.known_question_id = active_question.get("id")
                    student.known_question_revision = active_question.get("revision")
                current_team = (state.get("my_team") or {}).get("id")
                reload_needed = (
                    phase != student.page_phase
                    or current_team != student.page_team_id
                )
                if reload_needed:
                    reload_response = await self.metrics.request(
                        student.client,
                        "student.phase_reload",
                        "GET",
                        "/dashboard",
                    )
                    if reload_response is not None and reload_response.status_code == 200:
                        student.page_phase = phase
                        student.page_team_id = current_team
                        student.phase_seen_at.setdefault(phase, time.time())
                        student.state_version = None
                        student.known_question_id = None
                        student.known_question_revision = None
                        student.roster_synced_version = None
                        student.questions_synced_context = None
                        student.responses_synced_context = None
                    else:
                        # Force a full state response so the failed reload is retried.
                        student.state_version = None
                else:
                    student.phase_seen_at.setdefault(phase, time.time())
                    self.schedule_secondary(student)
            else:
                self.schedule_secondary(student)
            interval = max(0.2, float(data.get("poll_interval") or 1000) / 1000)
            await asyncio.sleep(interval * (1 + student.rng.random() * 0.2))

    async def instructor_poll_loop(self) -> None:
        assert self.instructor_poll_client is not None
        while not self.stop_event.is_set():
            response = await self.metrics.request(
                self.instructor_poll_client,
                "instructor.poll",
                "GET",
                "/api/poll",
            )
            if response and response.status_code == 200:
                self.latest_instructor_state = response.json().get("state")
            await asyncio.sleep(1)

    async def instructor_state(self) -> dict[str, Any]:
        assert self.instructor is not None
        response = await self.metrics.request(
            self.instructor,
            "instructor.control_state",
            "GET",
            "/api/poll",
        )
        if not response or response.status_code != 200:
            raise RuntimeError("Instructor state request failed")
        return response.json()["state"]

    @staticmethod
    def state_guard(state: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {
            "expected_phase": state.get("phase"),
            "expected_session_key": int(state.get("session_key") or 0),
            "expected_roster_version": int(state.get("roster_version") or 0),
            "presentation_key": state.get("poll_question_key") or "",
            **extra,
        }

    async def wait_for(self, description: str, predicate: Any, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            if self.safety_abort.is_set():
                raise RuntimeError("Resource safety limit reached")
            await asyncio.sleep(0.1)
        raise RuntimeError(f"Timed out waiting for {description}")

    def propagation(
        self, timestamps: list[float], started_at: float
    ) -> dict[str, float | int | None]:
        latencies = [max(0, value - started_at) for value in timestamps]
        return {
            "count": len(latencies),
            "p50_s": round(percentile(latencies, 50) or 0, 3),
            "p95_s": round(percentile(latencies, 95) or 0, 3),
            "max_s": round(max(latencies), 3) if latencies else None,
        }

    async def set_phase(self, phase: str) -> tuple[float, dict[str, Any]]:
        assert self.instructor is not None
        state = await self.instructor_state()
        payload = self.state_guard(state, phase=phase)
        response = await self.metrics.request(
            self.instructor,
            f"instructor.set_phase.{phase}",
            "POST",
            "/api/set_phase",
            json=payload,
        )
        if not response or response.status_code != 200:
            raise RuntimeError(f"Could not enter {phase}")
        return time.time(), response.json()

    @staticmethod
    def varied_text(size: int) -> str:
        blocks: list[str] = []
        number = 0
        while sum(len(block) + 2 for block in blocks) < size:
            digest = hashlib.sha256(f"load-question-{number}".encode()).hexdigest()
            blocks.append(
                f"Evidence block {number}: compare {digest[:24]} with "
                f"{digest[24:48]} and explain the uncertainty in {digest[48:]}."
            )
            number += 1
        return "\n\n".join(blocks)[:size]

    async def add_question(self, title: str, content: str) -> tuple[int, int, float]:
        assert self.instructor is not None
        self.question_change_pending = True
        try:
            state = await self.instructor_state()
            week = int(state.get("discussion_week") or 1)
            response = await self.metrics.request(
                self.instructor,
                "instructor.add_question",
                "POST",
                "/api/questions",
                json=self.state_guard(
                    state,
                    week=week,
                    title=title,
                    content=content,
                ),
            )
            if not response or response.status_code != 200:
                raise RuntimeError("Could not add appendix question")
            completed_at = time.time()
            payload = response.json()
            label = str(payload.get("label") or "")
            expected_title = f"{label}: {title}"
            if payload.get("title") != expected_title:
                raise RuntimeError("Appendix question title did not match the request")
            question_id = int(payload["question_id"])
            new_state = await self.instructor_state()
            version = int(new_state.get("discussion_questions_version") or 0)
            self.expected_questions[(week, version)] = (expected_title, content)
            return question_id, version, completed_at
        finally:
            self.question_change_pending = False
    async def run_workflow(self) -> None:
        await self.login_instructor()
        await self.login_students()
        await self.join_teams()

        self.poll_tasks.extend(
            asyncio.create_task(self.student_poll_loop(student))
            for student in self.students
        )
        self.poll_tasks.append(asyncio.create_task(self.instructor_poll_loop()))
        await self.wait_for(
            "initial student polls",
            lambda: all(student.last_state is not None for student in self.students),
            15,
        )

        discussion_at, _ = await self.set_phase("discussion")
        await self.wait_for(
            "discussion page visibility",
            lambda: all("discussion" in student.phase_seen_at for student in self.students),
            12,
        )
        discussion_times = [
            student.phase_seen_at["discussion"] for student in self.students
        ]
        discussion_prop = self.propagation(discussion_times, discussion_at)
        self.diagnostics["discussion_phase_propagation"] = discussion_prop
        self.check(
            "discussion reaches every student within 5 seconds",
            bool(discussion_prop["max_s"] is not None and discussion_prop["max_s"] <= 5),
            discussion_prop,
        )

        question_id, question_version, question_at = await self.add_question(
            "Large fanout question", self.varied_text(32 * 1024)
        )
        await self.wait_for(
            "large question fanout",
            lambda: all(
                student.questions_synced_context == (1, question_version)
                for student in self.students
            ),
            15,
        )
        question_times = [
            student.questions_seen_at[question_version] for student in self.students
        ]
        question_prop = self.propagation(question_times, question_at)
        self.diagnostics["large_question_fanout"] = question_prop
        self.check(
            "32 KiB question reaches every student within 5 seconds",
            bool(question_prop["max_s"] is not None and question_prop["max_s"] <= 5),
            question_prop,
        )

        thumb_responses = await asyncio.gather(
            *(
                self.metrics.request(
                    student.client,
                    "student.thumb",
                    "POST",
                    "/api/grade_peer",
                    json={
                        "recipient_id": self.students[
                            ((student.number - 1) // STUDENTS_PER_TEAM)
                            * STUDENTS_PER_TEAM
                            + (student.number % STUDENTS_PER_TEAM)
                        ].student_id,
                        "selected": True,
                    },
                )
                for student in self.students
            )
        )
        self.check(
            "80 simultaneous teammate thumbs",
            all(response and response.status_code == 200 for response in thumb_responses),
            sum(bool(response and response.status_code == 200) for response in thumb_responses),
        )
        thumb_replays = await asyncio.gather(
            *(
                self.metrics.request(
                    student.client,
                    "student.thumb_replay",
                    "POST",
                    "/api/grade_peer",
                    json={
                        "recipient_id": self.students[
                            ((student.number - 1) // STUDENTS_PER_TEAM)
                            * STUDENTS_PER_TEAM
                            + (student.number % STUDENTS_PER_TEAM)
                        ].student_id,
                        "selected": True,
                    },
                )
                for student in self.students
            )
        )
        self.check(
            "duplicate teammate thumbs are idempotent",
            all(response and response.status_code == 200 for response in thumb_replays),
            sum(bool(response and response.status_code == 200) for response in thumb_replays),
        )

        paused = self.students[:10]
        pause_started = time.monotonic()
        for student in paused:
            student.pause_until = pause_started + 8
        await asyncio.sleep(1.5)
        _, reconnect_version, _ = await self.add_question(
            "Reconnect marker", "A short question added while ten clients are offline."
        )
        await self.wait_for(
            "connected clients receive reconnect marker",
            lambda: all(
                student.questions_synced_context == (1, reconnect_version)
                for student in self.students[10:]
            ),
            8,
        )
        resume_at = time.time() + max(0, pause_started + 8 - time.monotonic())
        await self.wait_for(
            "paused clients recover",
            lambda: all(
                student.questions_synced_context == (1, reconnect_version)
                for student in paused
            ),
            15,
        )
        reconnect_prop = self.propagation(
            [student.questions_seen_at[reconnect_version] for student in paused],
            resume_at,
        )
        self.diagnostics["reconnect_propagation"] = reconnect_prop
        self.check(
            "paused sessions recover within 5 seconds without login",
            bool(reconnect_prop["max_s"] is not None and reconnect_prop["max_s"] <= 5),
            reconnect_prop,
        )

        competition_at, _ = await self.set_phase("competition")
        await self.wait_for(
            "competition page visibility",
            lambda: all("competition" in student.phase_seen_at for student in self.students),
            12,
        )
        competition_prop = self.propagation(
            [student.phase_seen_at["competition"] for student in self.students],
            competition_at,
        )
        self.diagnostics["competition_phase_propagation"] = competition_prop
        self.check(
            "competition reaches every student within 5 seconds",
            bool(competition_prop["max_s"] is not None and competition_prop["max_s"] <= 5),
            competition_prop,
        )

        state = await self.instructor_state()
        assert self.instructor is not None
        start_presentation = await self.metrics.request(
            self.instructor,
            "instructor.start_presentation",
            "POST",
            "/api/start_presentation",
            json=self.state_guard(
                state, team_id=self.team_ids[0], question_id=question_id, time_cap=300
            ),
        )
        if not start_presentation or start_presentation.status_code != 200:
            raise RuntimeError("Could not start presentation")
        presentation_at = time.time()
        presentation_key = str(start_presentation.json()["presentation_key"])
        await self.wait_for(
            "presentation propagation",
            lambda: all(
                student.last_state
                and student.last_state.get("poll_question_key") == presentation_key
                for student in self.students
            ),
            40,
        )
        presentation_prop = self.propagation(
            [student.presentation_seen_at[presentation_key] for student in self.students],
            presentation_at,
        )
        self.diagnostics["presentation_propagation"] = presentation_prop
        self.check(
            "presentation reaches every student within 5 seconds",
            bool(presentation_prop["max_s"] is not None and presentation_prop["max_s"] <= 5),
            presentation_prop,
        )

        hand_students = self.students[10:30]
        hand_responses = await asyncio.gather(
            *(
                self.metrics.request(
                    student.client,
                    "student.raise_hand",
                    "POST",
                    "/api/raise_hand",
                    json={"presentation_key": presentation_key},
                )
                for student in hand_students
            )
        )
        self.check(
            "20 simultaneous challenge hands",
            all(response and response.status_code == 200 for response in hand_responses),
            sum(bool(response and response.status_code == 200) for response in hand_responses),
        )
        await self.wait_for(
            "instructor sees 20 challenge hands",
            lambda: bool(
                self.latest_instructor_state
                and len(self.latest_instructor_state.get("challenge_hands", [])) == 20
            ),
            8,
        )
        state = await self.instructor_state()
        select = await self.metrics.request(
            self.instructor,
            "instructor.select_challenger",
            "POST",
            "/api/select_challenger",
            json=self.state_guard(state, student_id=hand_students[0].db_id),
        )
        if not select or select.status_code != 200:
            raise RuntimeError("Could not select challenger")
        challenge_key = str(select.json()["challenge_key"])

        rating_students = self.students[20:80]
        challenge_ratings = await asyncio.gather(
            *(
                self.metrics.request(
                    student.client,
                    "student.challenge_rating",
                    "POST",
                    "/api/submit_challenge_rating",
                    json={"challenge_key": challenge_key, "score": 1 + student.number % 5},
                )
                for student in rating_students
            )
        )
        self.check(
            "60 simultaneous challenge ratings",
            all(response and response.status_code == 200 for response in challenge_ratings),
            sum(bool(response and response.status_code == 200) for response in challenge_ratings),
        )
        challenge_replays = await asyncio.gather(
            *(
                self.metrics.request(
                    student.client,
                    "student.challenge_rating_replay",
                    "POST",
                    "/api/submit_challenge_rating",
                    json={"challenge_key": challenge_key, "score": 1 + student.number % 5},
                )
                for student in rating_students
            )
        )
        self.check(
            "duplicate challenge ratings are idempotent",
            all(response and response.status_code == 200 for response in challenge_replays),
            sum(bool(response and response.status_code == 200) for response in challenge_replays),
        )
        state = await self.instructor_state()
        self.check(
            "instructor challenge count is 60",
            state.get("challenge_rating_counts", {}).get(challenge_key) == 60,
            state.get("challenge_rating_counts", {}).get(challenge_key),
        )

        start_poll = await self.metrics.request(
            self.instructor,
            "instructor.start_poll",
            "POST",
            "/api/start_poll",
            json=self.state_guard(state),
        )
        if not start_poll or start_poll.status_code != 200:
            raise RuntimeError("Could not start rating poll")
        await self.wait_for(
            "open rating poll propagation",
            lambda: all(
                student.last_state and student.last_state.get("poll_active")
                for student in self.students
            ),
            12,
        )
        await asyncio.sleep(5)
        presentation_ratings = await asyncio.gather(
            *(
                self.metrics.request(
                    student.client,
                    "student.presentation_rating",
                    "POST",
                    "/api/submit_rating",
                    json={
                        "presentation_key": presentation_key,
                        "q1_developed": 4,
                        "q2_easy": 5,
                    },
                )
                for student in rating_students
            )
        )
        self.check(
            "60 simultaneous presentation ratings",
            all(response and response.status_code == 200 for response in presentation_ratings),
            sum(bool(response and response.status_code == 200) for response in presentation_ratings),
        )
        presentation_replays = await asyncio.gather(
            *(
                self.metrics.request(
                    student.client,
                    "student.presentation_rating_replay",
                    "POST",
                    "/api/submit_rating",
                    json={
                        "presentation_key": presentation_key,
                        "q1_developed": 4,
                        "q2_easy": 5,
                    },
                )
                for student in rating_students
            )
        )
        self.check(
            "60 presentation rating replays are acknowledged",
            all(response and response.status_code == 200 for response in presentation_replays),
            sum(bool(response and response.status_code == 200) for response in presentation_replays),
        )
        with sqlite3.connect(self.db_path) as db:
            count_before_locks = db.execute(
                "SELECT COUNT(*) FROM presentation_ratings"
            ).fetchone()[0]
        self.check("presentation rating replay storage remains exactly 60 rows", count_before_locks == 60, count_before_locks)

        remaining = self.args.soak_seconds
        while remaining > 0 and not self.stop_event.is_set():
            step = min(30, remaining)
            await asyncio.sleep(step)
            remaining -= step
            poll_count = sum(
                sample.operation.startswith("student.poll")
                for sample in self.metrics.samples
            )
            print(
                f"Soak progress: {self.args.soak_seconds - remaining}/{self.args.soak_seconds}s, "
                f"student polls={poll_count}",
                flush=True,
            )

        if not self.args.skip_lock_tests:
            self.fault_test_started_at = time.time()
            await self.short_lock_probe(presentation_key)
            await self.long_lock_probe(presentation_key)
        else:
            self.diagnostics["lock_probes"] = "skipped"

    async def short_lock_probe(self, presentation_key: str) -> None:
        lock = sqlite3.connect(self.db_path, timeout=1, isolation_level=None)
        lock.execute("BEGIN IMMEDIATE")
        started = time.time()
        candidates = self.students[10:20]
        tasks = [
            asyncio.create_task(
                self.metrics.request(
                    student.client,
                    "student.rating_short_lock",
                    "POST",
                    "/api/submit_rating",
                    json={
                        "presentation_key": presentation_key,
                        "q1_developed": 3,
                        "q2_easy": 3,
                    },
                )
            )
            for student in candidates
        ]
        await asyncio.sleep(3)
        lock.execute("ROLLBACK")
        lock.close()
        responses = await asyncio.gather(*tasks)
        ended = time.time()
        reads = [
            sample for sample in self.metrics.samples
            if sample.operation.startswith("student.poll")
            and sample.started >= started
            and sample.started <= ended
            and sample.started + sample.elapsed_ms / 1000 >= started
        ]
        read_p99 = percentile(
            [sample.elapsed_ms for sample in reads if sample.status == 200], 99
        )
        detail = {
            "writes_ok": sum(bool(response and response.status_code == 200) for response in responses),
            "polls_completed": sum(sample.status == 200 for sample in reads),
            "poll_p99_ms": round(read_p99 or 0, 2),
        }
        self.diagnostics["three_second_sqlite_lock"] = detail
        self.check(
            "3-second SQLite lock preserves polling and all writes",
            detail["writes_ok"] == len(candidates)
            and detail["polls_completed"] > 0
            and (read_p99 or 0) <= 3000,
            detail,
        )

    async def long_lock_probe(self, presentation_key: str) -> None:
        student = self.students[10]
        query = """SELECT q1_developed, q2_easy FROM presentation_ratings r
                   JOIN students s ON s.id = r.student_id
                   WHERE s.student_id = ? AND r.question_key = ?"""
        count_query = """SELECT COUNT(*) FROM presentation_ratings r
                         JOIN students s ON s.id = r.student_id
                         WHERE s.student_id = ? AND r.question_key = ?"""
        params = (student.student_id, presentation_key)
        with sqlite3.connect(self.db_path) as db:
            row_before = db.execute(query, params).fetchone()
            count_before = db.execute(count_query, params).fetchone()[0]

        lock = sqlite3.connect(self.db_path, timeout=1, isolation_level=None)
        lock.execute("BEGIN IMMEDIATE")
        request_task = asyncio.create_task(
            self.metrics.request(
                student.client,
                "student.rating_long_lock_diagnostic",
                "POST",
                "/api/submit_rating",
                json={
                    "presentation_key": presentation_key,
                    "q1_developed": 2,
                    "q2_easy": 2,
                },
                expected=(503,),
                timeout=15,
                allow_transport_error=True,
            )
        )
        await asyncio.sleep(17)
        lock.execute("ROLLBACK")
        lock.close()
        response = await request_task
        await asyncio.sleep(3)

        response_json: Any = None
        if response is not None:
            try:
                response_json = response.json()
            except (ValueError, json.JSONDecodeError):
                response_json = None
        retry_after = response.headers.get("Retry-After", "") if response is not None else ""
        recoverable_503 = (
            response is not None
            and response.status_code == 503
            and "application/json" in response.headers.get("content-type", "").lower()
            and isinstance(response_json, dict)
            and bool(response_json.get("error"))
            and retry_after.strip().isdigit()
            and int(retry_after) >= 1
        )

        with sqlite3.connect(self.db_path) as db:
            row_after_release = db.execute(query, params).fetchone()
            count_after_release = db.execute(count_query, params).fetchone()[0]
        no_late_commit = (
            count_after_release == count_before
            and row_after_release == row_before
        )

        retry = await self.metrics.request(
            student.client,
            "student.rating_long_lock_retry",
            "POST",
            "/api/submit_rating",
            json={
                "presentation_key": presentation_key,
                "q1_developed": 2,
                "q2_easy": 2,
            },
        )
        with sqlite3.connect(self.db_path) as db:
            final_row = db.execute(query, params).fetchone()
            final_count = db.execute(count_query, params).fetchone()[0]

        detail = {
            "client_result": response.status_code if response is not None else "timeout",
            "response_json": response_json,
            "retry_after": retry_after or None,
            "row_before": list(row_before) if row_before else None,
            "row_after_release": list(row_after_release) if row_after_release else None,
            "count_after_release": count_after_release,
            "retry_result": retry.status_code if retry is not None else "transport_error",
            "final_row": list(final_row) if final_row else None,
            "final_count": final_count,
            "current_client_timeout_seconds": 15,
            "current_sqlite_busy_timeout_seconds": 8,
        }
        self.diagnostics["seventeen_second_sqlite_lock"] = detail
        self.check(
            "17-second lock returns recoverable JSON 503",
            recoverable_503,
            detail,
        )
        self.check(
            "timed-out lock request makes no late commit",
            no_late_commit,
            detail,
        )
        self.check(
            "rating retry succeeds after lock release",
            retry is not None and retry.status_code == 200,
            detail,
        )
        self.check(
            "post-lock retry leaves one final rating row",
            final_count == 1 and final_row == (2, 2),
            detail,
        )
    def database_integrity(self) -> None:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        team_counts = [
            row["c"] for row in db.execute(
                """SELECT COUNT(s.id) AS c FROM teams t
                   LEFT JOIN students s ON s.team_id = t.id AND s.is_active = 1
                   GROUP BY t.id ORDER BY t.id"""
            ).fetchall()
        ]
        thumbs = db.execute(
            """SELECT grader_id, recipient_id, grader_team_id, recipient_team_id
               FROM teammate_thumbs"""
        ).fetchall()
        challenge_rows = db.execute(
            "SELECT * FROM challenge_ratings"
        ).fetchall()
        presentation_rows = db.execute(
            "SELECT * FROM presentation_ratings"
        ).fetchall()
        logged_in = db.execute(
            "SELECT COUNT(*) FROM students WHERE last_login_at IS NOT NULL"
        ).fetchone()[0]
        unique_challenge = len(
            {(row["challenge_key"], row["rater_id"]) for row in challenge_rows}
        )
        unique_presentation = len(
            {(row["question_key"], row["student_id"]) for row in presentation_rows}
        )
        db.close()
        self.check("SQLite integrity_check", integrity == "ok", integrity)
        self.check("SQLite foreign_key_check", not foreign_keys, len(foreign_keys))
        self.check(
            "all teams contain exactly 10 students",
            team_counts == [STUDENTS_PER_TEAM] * TEAM_COUNT,
            team_counts,
        )
        self.check("all student logins were recorded", logged_in == STUDENT_COUNT, logged_in)
        self.check(
            "thumb records are exact and team-local",
            len(thumbs) == STUDENT_COUNT
            and all(
                row["grader_id"] != row["recipient_id"]
                and row["grader_team_id"] == row["recipient_team_id"]
                for row in thumbs
            ),
            len(thumbs),
        )
        self.check(
            "challenge records are unique and exact",
            len(challenge_rows) == 60 and unique_challenge == 60,
            {"rows": len(challenge_rows), "unique": unique_challenge},
        )
        expected_presentation_rows = 60 if self.args.skip_lock_tests else 70
        self.check(
            "presentation records are unique after lock probes",
            len(presentation_rows) == expected_presentation_rows
            and unique_presentation == expected_presentation_rows,
            {"expected": expected_presentation_rows, "rows": len(presentation_rows),
             "unique": unique_presentation},
        )

    def performance_checks(self) -> None:
        normal_samples = [
            sample for sample in self.metrics.samples
            if self.fault_test_started_at is None
            or sample.started < self.fault_test_started_at
        ]
        summaries = self.metrics.summary(normal_samples)
        self.diagnostics["normal_metrics"] = summaries
        compact = summaries.get("student.poll.compact", {})
        full = summaries.get("student.poll.full", {})
        write_operations = {
            "student.join_team", "student.thumb", "student.thumb_replay",
            "student.raise_hand", "student.challenge_rating",
            "student.challenge_rating_replay", "student.presentation_rating",
            "student.presentation_rating_replay", "student.rating_short_lock",
        }
        writes = [
            sample.elapsed_ms for sample in normal_samples
            if sample.operation in write_operations and sample.status == 200
        ]
        compact_samples = [
            sample for sample in normal_samples
            if sample.operation == "student.poll.compact" and sample.status == 200
        ]
        compact_wire_p99 = percentile(
            [sample.wire_bytes for sample in compact_samples], 99
        )
        self.check(
            "compact poll p95 is at most 500 ms",
            bool(compact and compact.get("p95_ms", float("inf")) <= 500),
            compact.get("p95_ms"),
        )
        self.check(
            "compact poll p99 is at most 1.5 seconds",
            bool(compact and compact.get("p99_ms", float("inf")) <= 1500),
            compact.get("p99_ms"),
        )
        self.check(
            "compact poll p99 wire size is below 300 bytes",
            bool(compact_wire_p99 is not None and compact_wire_p99 < 300),
            compact_wire_p99,
        )
        self.check(
            "full poll p99 is at most 3 seconds",
            bool(full and full.get("p99_ms", float("inf")) <= 3000),
            full.get("p99_ms"),
        )
        write_p95 = percentile(writes, 95)
        write_p99 = percentile(writes, 99)
        self.check(
            "write p95 is at most 2 seconds",
            bool(write_p95 is not None and write_p95 <= 2000),
            round(write_p95 or 0, 2),
        )
        self.check(
            "write p99 is at most 5 seconds",
            bool(write_p99 is not None and write_p99 <= 5000),
            round(write_p99 or 0, 2),
        )
        unexpected_core = [
            f"{sample.operation}: {sample.error}"
            for sample in normal_samples if sample.error
        ]
        self.check(
            "no unexpected HTTP or transport errors",
            not unexpected_core,
            unexpected_core[:10],
        )
        poll_attempts = sum(
            sample.operation.startswith("student.poll")
            for sample in normal_samples
        )
        if self.args.soak_seconds >= 600:
            self.check(
                "at least 40,000 student polls in the 10-minute run",
                poll_attempts >= 40000,
                poll_attempts,
            )
        else:
            self.diagnostics["student_poll_attempts"] = poll_attempts

    async def close_clients(self) -> None:
        clients = [student.client for student in self.students]
        if self.instructor:
            clients.append(self.instructor)
        if self.instructor_poll_client:
            clients.append(self.instructor_poll_client)
        await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)

    def write_report(self) -> None:
        summaries = self.metrics.summary()
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "environment": {
                "server": "independent single-worker Uvicorn WSGI processes",
                "workers": self.args.workers,
                "students": STUDENT_COUNT,
                "teams": TEAM_COUNT,
                "soak_seconds": self.args.soak_seconds,
                "python": sys.version,
                "base_url": self.base_url,
                "base_urls": self.base_urls,
                "disposable_root": str(self.root),
                "server_pids": sorted(self.server_pids),
            },
            "checks": self.checks,
            "diagnostics": self.diagnostics,
            "metrics": summaries,
            "unexpected": self.metrics.unexpected,
            "resources": {
                "max_system_cpu_pct": max(
                    (sample["system_cpu_pct"] for sample in self.monitor_samples),
                    default=0,
                ),
                "max_system_memory_pct": max(
                    (sample["system_memory_pct"] for sample in self.monitor_samples),
                    default=0,
                ),
                "max_server_rss_mb": max(
                    (sample["server_rss_mb"] for sample in self.monitor_samples),
                    default=0,
                ),
            },
        }
        self.report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"Report: {self.report_path}", flush=True)
        print(f"Server log: {self.log_path}", flush=True)

    async def run(self) -> int:
        print(f"Disposable test root: {self.root}", flush=True)
        try:
            self.seed()
            await self.start_server()
            self.poll_tasks.append(asyncio.create_task(self.resource_monitor()))
            await self.run_workflow()
        except Exception as exc:
            self.check("load scenario completed", False, str(exc))
            traceback.print_exc()
        finally:
            self.stop_event.set()
            if self.poll_tasks:
                task_results = await asyncio.gather(
                    *self.poll_tasks, return_exceptions=True
                )
                for result in task_results:
                    if isinstance(result, BaseException):
                        self.background_task_errors.append(
                            f"poll task: {type(result).__name__}: {result}"
                        )
            await self.close_clients()
            await self.stop_server()
        self.check(
            "resource safety limits were not triggered",
            not self.safety_abort.is_set(),
            self.diagnostics.get("server_process_exit") or "within limits",
        )
        self.check(
            "background polling and sync tasks completed cleanly",
            not self.background_task_errors,
            self.background_task_errors[:10],
        )
        self.database_integrity()
        self.performance_checks()
        if self.log_path.exists():
            server_log = self.log_path.read_text(encoding="utf-8", errors="replace")
            locked_errors = len(re.findall(r"database is locked", server_log, re.I))
            tracebacks = server_log.count("Traceback (most recent call last)")
            app_tracebacks = len(re.findall(r"Unhandled error on|ERROR in app", server_log))
            self.diagnostics["server_log"] = {
                "database_locked_messages": locked_errors,
                "tracebacks": tracebacks,
                "application_tracebacks": app_tracebacks,
            }
            self.check("server log has no SQLite lock errors", locked_errors == 0, locked_errors)
            self.check(
                "server log has no application tracebacks",
                app_tracebacks == 0,
                {"application": app_tracebacks, "runtime": tracebacks},
            )
        self.write_report()
        failed = [check for check in self.checks if not check["passed"]]
        print(
            f"Completed with {len(failed)} failed check(s) out of {len(self.checks)}.",
            flush=True,
        )
        return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--soak-seconds",
        type=int,
        default=120,
        help="Sustained polling duration after workflow bursts (default: 120)",
    )
    parser.add_argument(
        "--skip-lock-tests",
        action="store_true",
        help="Skip the destructive lock-pressure diagnostics",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="Independent single-worker server processes (default: 3)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="First server port; later servers use consecutive ports (default: auto)",
    )
    args = parser.parse_args()
    if args.soak_seconds < 0:
        parser.error("--soak-seconds must be nonnegative")
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be between 1 and 8")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.port and args.port + args.workers - 1 > 65535:
        parser.error("--port plus --workers exceeds port 65535")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(ClassroomLoadTest(parse_args()).run()))
