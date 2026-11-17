
import json, time, httpx
Q = "入职当天在哪里领取办公电脑"
docs = ["新员工在入职当天到信息服务台领取办公电脑。领取时需要出示员工编号。",
        "年假天数根据累计工龄计算：满一年不满十年为五天。",
        "出差住宿费按城市等级分档。"]
with httpx.Client(timeout=180.0) as c:
    for k in (1, 3):
        for i in range(3):
            t0 = time.perf_counter()
            r = c.post("http://127.0.0.1:56850/v1/rerank", json={"model": "bge-reranker-v2-m3", "query": Q, "documents": docs[:k]})
            print("k=", k, "iter", i, r.status_code, "ms=", round((time.perf_counter()-t0)*1000,1))
    # also send the exact payload shape the engine uses (12 docs) once
    t0 = time.perf_counter()
    r = c.post("http://127.0.0.1:56850/v1/rerank", json={"model": "bge-reranker-v2-m3", "query": Q, "documents": docs*4})
    print("k=12 status", r.status_code, "ms=", round((time.perf_counter()-t0)*1000,1))
