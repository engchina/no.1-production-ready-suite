"""backend の起動の契約（#165）。本番は init_script.sh が作る systemd の unit（#286 / #356）。"""

import re
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[2]
INIT_SCRIPT = AGENT_ROOT / "init_script.sh"


def test_backend_unit_runs_a_single_worker() -> None:
    """Runtime の状態は process の中にあるため、複数 worker にすると worker ごとに分かれる。"""
    init_script = INIT_SCRIPT.read_text(encoding="utf-8")

    assert 'BACKEND_WORKERS="1"' in init_script
    exec_start = re.search(r"(?m)^ExecStart=.*gunicorn app\.main:app.*$", init_script)
    assert exec_start is not None, "backend の unit の ExecStart が見つからない"
    assert "--worker-class uvicorn.workers.UvicornWorker" in exec_start.group(0)
    assert "--workers ${BACKEND_WORKERS}" in exec_start.group(0)


def test_own_code_has_no_container_image() -> None:
    """自前のコードは Docker イメージを作らない。compose は第三者の Runtime だけを持つ（#356）。"""
    ignored = {"node_modules", ".venv", "dist"}
    dockerfiles = [
        path
        for path in AGENT_ROOT.rglob("Dockerfile*")
        if not ignored.intersection(path.relative_to(AGENT_ROOT).parts)
    ]
    assert dockerfiles == []
    compose = (AGENT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "build:" not in compose
    services_block = compose.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    services = re.findall(r"(?m)^  ([a-z0-9-]+):\n", services_block)
    assert set(services) == {"runtime-openclaw", "runtime-hermes", "runtime-deerflow"}
    images = re.findall(r"(?m)^    image: (\S+)$", compose)
    assert len(images) == 3
    assert all("@sha256:" in image for image in images)
