
import httpx
base = "https://flk.npc.gov.cn"
with httpx.Client(timeout=30.0, headers={"User-Agent": "Mozilla/5.0", "Referer": base + "/"}) as c:
    for path in ("/api/?type=flfg&searchType=title%3Bvague&sort=true&page=1&size=5",
                 "/api/search?type=flfg&page=1&size=5"):
        try:
            r = c.get(base + path)
            body = r.text.replace("\n", " ")[:200]
            print(path, r.status_code, len(r.text), body)
        except Exception as e:
            print(path, "ERR", e)
