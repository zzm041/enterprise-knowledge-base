
import httpx, json
UA = {"User-Agent": "ekb-perf-corpus/1.0 (offline evaluation)"}
tests = [
    "https://zh.wikisource.org/w/api.php?action=query&format=json&list=search&srsearch=%E6%B3%95%E5%BE%8B&srlimit=3",
    "https://www.gov.cn/zhengce/zuixin/",
]
with httpx.Client(timeout=30.0, headers=UA, follow_redirects=True) as c:
    for u in tests:
        try:
            r = c.get(u)
            print(u[:60], r.status_code, len(r.text), repr(r.text[:160]))
        except Exception as e:
            print(u[:60], "ERR", type(e).__name__, e)
