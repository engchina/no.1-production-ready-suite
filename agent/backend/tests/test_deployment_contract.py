"""backend の Docker image の起動の契約（#165）。"""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_backend_image_runs_a_single_worker() -> None:
    """Runtime の状態は process の中にあるため、複数 worker にすると worker ごとに分かれる。"""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert '"--worker-class", "uvicorn.workers.UvicornWorker"' in dockerfile
    assert '"--workers", "1"' in dockerfile
    assert '"--workers", "2"' not in dockerfile
