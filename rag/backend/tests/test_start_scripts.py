"""ローカル起動の工程と失敗処理を外部サービスなしで検証する。"""

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parents[3]


def _executable(path: Path, text: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -eu\n" + text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture(params=["rag", "nl2sql", "agent"])
def startup(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, dict[str, str]]:
    source = SUITE / request.param / "scripts"
    scripts = tmp_path / request.param / "scripts"
    scripts.mkdir(parents=True)
    (scripts.parent / "backend").mkdir()
    for name in ("start-all.sh", "start-backend.sh", "start-frontend.sh"):
        shutil.copy2(source / name, scripts / name)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    _executable(binaries / "lsof", "exit 1\n")
    _executable(
        binaries / "uv",
        """
if [ "$1" = sync ]; then
  echo sync-start >> "$TEST_EVENTS"
  touch "$TEST_DIR/preparing"
  sleep "${TEST_SYNC_DELAY:-0}"
  if [ "${TEST_SYNC_FAIL:-0}" != 0 ]; then exit 23; fi
  echo sync-end >> "$TEST_EVENTS"
  exit 0
fi
[ "$1" = run ] && [ "$2" = --no-sync ]
echo server >> "$TEST_EVENTS"
if [ "${TEST_SERVER_FAIL:-0}" != 0 ]; then exit 24; fi
touch "$TEST_DIR/serving"
exec sleep 60
""",
    )
    _executable(
        binaries / "curl",
        """
echo "health:$*" >> "$TEST_EVENTS"
# 環境変数のプロキシを使うと、ローカルの backend へ届かず失敗する（502。#781）。
direct=0
previous=""
for arg in "$@"; do
  if [ "$previous" = --noproxy ] && [ "$arg" = "*" ]; then direct=1; fi
  previous="$arg"
done
if [ -n "${http_proxy:-}${HTTP_PROXY:-}" ] && [ "$direct" = 0 ]; then exit 22; fi
# DB readiness は失敗しても、アプリの health が成功すれば画面は起動できる。
[[ "$*" = *"/api/health" ]] || exit 1
[ "${TEST_NOT_READY:-0}" = 0 ] && [ -f "$TEST_DIR/serving" ]
""",
    )
    _executable(scripts / "start-frontend.sh", 'echo frontend >> "$TEST_EVENTS"\n')
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("BACKEND_", "FRONTEND_", "TEST_"))
    }
    env.update(
        PATH=f"{binaries}:{env['PATH']}",
        TEST_DIR=str(tmp_path),
        TEST_SOURCE=str(source),
        TEST_EVENTS=str(tmp_path / "events"),
        BACKEND_READY_TIMEOUT_SECONDS="2",
    )
    return scripts, env


def _run(startup: tuple[Path, dict[str, str]]) -> subprocess.CompletedProcess[str]:
    scripts, env = startup
    return subprocess.run(
        ["bash", str(scripts / "start-all.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_slow_dependency_preparation_does_not_consume_health_timeout(
    startup: tuple[Path, dict[str, str]],
) -> None:
    _, env = startup
    env["TEST_SYNC_DELAY"] = "3"
    result = _run(startup)
    assert result.returncode == 0, result.stdout + result.stderr
    events = Path(env["TEST_EVENTS"]).read_text().splitlines()
    assert events[:2] == ["sync-start", "sync-end"]
    assert events.count("sync-start") == 1
    assert events.index("server") < events.index("frontend")
    assert all("/api/ready" not in event for event in events)


def test_readiness_does_not_use_the_proxy_from_the_environment(
    startup: tuple[Path, dict[str, str]],
) -> None:
    """NO_PROXY に 127.0.0.1 が無いプロキシの環境でも、ローカルの backend を確かめる（#781）。"""
    _, env = startup
    for key in ("no_proxy", "NO_PROXY"):
        env.pop(key, None)
    env.update(http_proxy="http://proxy.invalid:80", HTTP_PROXY="http://proxy.invalid:80")
    result = _run(startup)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "backend ready を確認しました" in result.stdout


@pytest.mark.parametrize("failure", ["TEST_SYNC_FAIL", "TEST_SERVER_FAIL", "TEST_NOT_READY"])
def test_failed_stage_does_not_start_frontend(
    startup: tuple[Path, dict[str, str]], failure: str
) -> None:
    _, env = startup
    env[failure] = "1"
    result = _run(startup)
    assert result.returncode != 0
    events = Path(env["TEST_EVENTS"]).read_text().splitlines()
    assert "frontend" not in events
    if failure == "TEST_SYNC_FAIL":
        assert result.returncode == 23
        assert "server" not in events
        assert "readiness" not in result.stdout


def test_cancellation_during_dependency_preparation(startup: tuple[Path, dict[str, str]]) -> None:
    scripts, env = startup
    env["TEST_SYNC_DELAY"] = "60"
    with subprocess.Popen(
        ["bash", str(scripts / "start-all.sh")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            deadline = time.monotonic() + 5
            while not (Path(env["TEST_DIR"]) / "preparing").exists():
                assert time.monotonic() < deadline
                time.sleep(0.05)
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=8)
            assert process.returncode == 143
        finally:
            if process.poll() is None:
                process.kill()
    assert Path(env["TEST_EVENTS"]).read_text().splitlines() == ["sync-start"]


@pytest.mark.parametrize("custom_ca", [False, True])
def test_frontend_uses_system_ca_and_preserves_explicit_ca(
    startup: tuple[Path, dict[str, str]], custom_ca: bool
) -> None:
    scripts, env = startup
    shutil.copy2(Path(env["TEST_SOURCE"]) / "start-frontend.sh", scripts / "start-frontend.sh")
    platform = scripts.parents[1] / "platform"
    ui = platform / "packages" / "ui"
    (ui / "dist").mkdir(parents=True)
    (platform / "node_modules").mkdir()
    for path in [platform / "package.json", ui / "package.json"]:
        path.write_text("{}")
    for name in ("index.js", "index.d.ts", "tokens.css"):
        (ui / "dist" / name).touch()
    (scripts.parent / "frontend").mkdir()
    expected = "/etc/ssl/certs/ca-certificates.crt"
    env.pop("NODE_EXTRA_CA_CERTS", None)
    if custom_ca:
        expected = str(platform / "custom-ca.pem")
        env["NODE_EXTRA_CA_CERTS"] = expected
    _executable(
        Path(env["TEST_DIR"]) / "bin" / "npm",
        'printf "%s\\n" "$NODE_EXTRA_CA_CERTS" >> "$TEST_EVENTS"\n',
    )
    result = subprocess.run(
        ["bash", str(scripts / "start-frontend.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # 共有 UI の成果物がソースより新しいので build を省き、npm install と npm run dev だけ（#816）。
    assert Path(env["TEST_EVENTS"]).read_text().splitlines() == [expected] * 2


def _frontend_with_shared_ui(startup: tuple[Path, dict[str, str]]) -> tuple[Path, Path]:
    scripts, env = startup
    shutil.copy2(Path(env["TEST_SOURCE"]) / "start-frontend.sh", scripts / "start-frontend.sh")
    platform = scripts.parents[1] / "platform"
    ui = platform / "packages" / "ui"
    (ui / "src").mkdir(parents=True)
    (ui / "dist").mkdir()
    (platform / "node_modules").mkdir()
    for path in [platform / "package.json", ui / "package.json", ui / "src" / "button.tsx"]:
        path.write_text("{}")
    for name in ("index.js", "index.d.ts", "tokens.css"):
        (ui / "dist" / name).touch()
    linked_ui = scripts.parent / "frontend" / "node_modules" / "@engchina" / "production-ready-ui"
    linked_ui.mkdir(parents=True)
    (linked_ui / "package.json").write_text("{}")
    _executable(Path(env["TEST_DIR"]) / "bin" / "npm", 'printf "%s\\n" "$*" >> "$TEST_EVENTS"\n')
    return scripts, ui


def _run_frontend(scripts: Path, env: dict[str, str]) -> list[str]:
    result = subprocess.run(
        ["bash", str(scripts / "start-frontend.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return Path(env["TEST_EVENTS"]).read_text().splitlines()


def test_frontend_builds_shared_ui_only_when_sources_changed(
    startup: tuple[Path, dict[str, str]],
) -> None:
    """共有 UI は成果物より新しいソースがあるときだけ build する。

    3 製品の同時起動で、起動中の別の製品が読む dist を消さない（#816）。
    """
    _, env = startup
    scripts, ui = _frontend_with_shared_ui(startup)
    events = Path(env["TEST_EVENTS"])

    assert not any(line.startswith("run build") for line in _run_frontend(scripts, env))

    # dist より新しいソースがあれば build する。
    later = time.time() + 5
    os.utime(ui / "src" / "button.tsx", (later, later))
    events.write_text("")
    assert "run build" in _run_frontend(scripts, env)

    # FORCE_SHARED_UI_BUILD=1 なら常に build する。
    for name in ("index.js", "index.d.ts", "tokens.css"):
        os.utime(ui / "dist" / name, (later + 5, later + 5))
    events.write_text("")
    assert "run build" in _run_frontend(scripts, {**env, "FORCE_SHARED_UI_BUILD": "1"})
