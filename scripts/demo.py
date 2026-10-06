"""Real localhost process lifecycle. All fixtures and logs stay in this project."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ekb.cli import initialize


def run():
    started = time.monotonic()
    run_dir = ROOT / "runtime" / ("http-demo-" + uuid4().hex[:8])
    config_path = initialize(run_dir)
    credentials = json.loads(
        (run_dir / "demo-credentials.json").read_text(encoding="utf8")
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "EKB_CONFIG": str(config_path), "PYTHONUNBUFFERED": "1"}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    logfile = (run_dir / "server.log").open("w", encoding="utf8")
    proc = None

    def start():
        p = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "ekb.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=logfile,
            stderr=logfile,
            creationflags=flags,
        )
        for _ in range(100):
            if p.poll() is not None:
                raise RuntimeError("Server exited; see runtime log")
            try:
                if client.get("/health").status_code == 200:
                    return p
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        p.terminate()
        p.wait(10)
        raise RuntimeError("Startup timed out")

    def stop(p):
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(5)

    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
    ) as client:
        try:
            proc = start()
            editor = {"Authorization": "Bearer " + credentials["editor"]}
            reader = {"Authorization": "Bearer " + credentials["reader"]}
            assert (
                client.get("/documents", params={"workspace": "handbook"}).status_code
                == 401
            )
            created = client.post(
                "/documents",
                headers=editor,
                json={
                    "workspace": "handbook",
                    "title": "Demo restoration",
                    "text": "Demo restoration checks require a checksum review.",
                },
            )
            created.raise_for_status()
            did = created.json()["id"]
            assert (
                client.post(
                    f"/documents/{did}/versions/1/ingest", headers=editor
                ).status_code
                == 200
            )
            answer = client.post(
                "/query",
                headers=reader,
                json={
                    "workspace": "handbook",
                    "question": "restoration checksum review",
                },
            ).json()
            assert (
                answer["status"] == "answered"
                and answer["citations"][0]["document_id"] == did
            )
            bad_case = client.post(
                "/query",
                headers=reader,
                json={
                    "workspace": "handbook",
                    "question": "I forgot my login secret; where can I regain access?",
                },
            ).json()
            assert (
                bad_case["status"] == "insufficient_knowledge"
                and bad_case["citations"] == []
            )
            cross_tenant = client.get(
                f"/documents/{did}",
                headers={"Authorization": "Bearer " + credentials["other-reader"]},
            )
            assert cross_tenant.status_code == 404
            assert (
                client.post(
                    f"/documents/{did}/archive",
                    headers=reader,
                    json={"expected_version": 1},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/documents",
                    headers=editor,
                    json={
                        "workspace": "handbook",
                        "title": "x",
                        "text": "x",
                        "filename": "x.pdf",
                    },
                ).status_code
                == 422
            )
            stop(proc)
            proc = None
            proc = start()
            persisted = client.get(f"/documents/{did}", headers=editor).json()
            assert persisted["current_version"] == 1
            assert (
                client.post(
                    f"/documents/{did}/archive",
                    headers=editor,
                    json={"expected_version": 1},
                ).status_code
                == 200
            )
            after = client.post(
                "/query",
                headers=reader,
                json={
                    "workspace": "handbook",
                    "question": "restoration checksum review",
                },
            ).json()
            assert after["status"] == "insufficient_knowledge"
            stop(proc)
            proc = None
            report = {
                "status": "PASS",
                "transport": "real localhost HTTP, separate Uvicorn process",
                "checks": [
                    "startup",
                    "anonymous denial",
                    "create/ingest",
                    "citation",
                    "cross-tenant read denial",
                    "reader write denial",
                    "known lexical paraphrase bad case",
                    "unsupported format",
                    "process restart persistence",
                    "archive exclusion",
                ],
                "answer": answer,
                "bad_case": {
                    "dataset_id": "paraphrase-hard",
                    "question": "I forgot my login secret; where can I regain access?",
                    "expected_source": "Account recovery",
                    "actual_status": bad_case["status"],
                    "limitation": "lexical retrieval misses a supported paraphrase",
                },
                "cross_tenant_read_status": cross_tenant.status_code,
                "after_archive": after,
                "database": str(run_dir / "knowledge.db"),
                "credentials": "omitted; synthetic runtime credentials ignored by Git",
                "server_stopped": True,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            (ROOT / "reports" / "http-demo.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
            )
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "checks": len(report["checks"]),
                        "report": "reports/http-demo.json",
                    }
                )
            )
        finally:
            if proc is not None:
                stop(proc)
            logfile.close()


if __name__ == "__main__":
    run()
