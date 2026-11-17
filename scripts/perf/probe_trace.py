
import json, httpx
from pathlib import Path
tok = json.loads((Path(r"E:\enterprise-knowledge-base\runtime\console-live\demo-credentials.json")).read_text(encoding="utf8"))["reader"]
with httpx.Client(timeout=180.0, headers={"Authorization": f"Bearer {tok}"}) as c:
    r = c.post("http://127.0.0.1:53122/query", json={"workspace": "handbook", "question": "入职当天在哪里领取办公电脑", "mode": "bm25"})
    b = r.json()
    print("answer_mode:", b.get("answer_mode"))
    print("trace:", json.dumps(b.get("trace"), ensure_ascii=False))
    print("answer:", (b.get("answer") or "")[:80])
