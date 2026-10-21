"""Verify a running combined server using a disposable test account.

Use only a test database. Add --live-design to exercise real distributor sourcing.
"""

import argparse
import json
import uuid
from collections import Counter

import httpx


def events(response):
    response.raise_for_status()
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:58000")
    parser.add_argument("--live-design", action="store_true")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=650, headers={"Origin": args.url}) as client:
        assert client.get("/api/ready").json()["persistence"] == "postgres"
        assert client.get("/projects").headers["content-type"].startswith("text/html")
        assert client.get("/assets/does-not-exist.js").status_code == 404
        assert client.get("/api/not-a-route").status_code == 404
        print("PASS: readiness, SPA deep links, asset/API 404s", flush=True)
        account = {"email": f"smoke-{uuid.uuid4().hex}@example.com", "password": uuid.uuid4().hex}
        response = client.post("/api/auth/register", json=account)
        response.raise_for_status()
        assert client.get("/api/auth/me").status_code == 200
        project = client.post("/api/projects", json={"name": "Deployment verification"}).json()
        assert client.get(f'/api/projects/{project["id"]}').status_code == 200
        blocked = client.post("/api/projects", json={"name": "Rejected"}, headers={"Origin": "https://untrusted.example"})
        assert blocked.status_code == 403
        print("PASS: session cookies, private project persistence, origin rejection", flush=True)
        frames = events(client.post("/api/design/demo", json={}))
        assert frames[-1]["type"] == "done"
        thread = frames[0]["thread_id"]
        assert client.get(f"/api/threads/{thread}/board").json()["status"] == "done"
        assert client.get(f"/api/export/{thread}.csv").status_code == 200
        print(json.dumps({"case": "recorded-walkthrough-restoration-export", "result": "PASS", "events": len(frames)}), flush=True)
        if args.live_design:
            mode = client.get("/api/runtime").json()["ai"]["mode"]
            frames = events(client.post("/api/design", json={"project_id": project["id"],
                "prompt": "USB temperature and humidity sensor", "request_id": uuid.uuid4().hex}))
            accepted = 0
            while frames and frames[-1]["type"] == "question" and accepted < 3:
                question = frames[-1]
                print(json.dumps({"case": "test-only-question-response", "question": question["text"],
                                  "answer": "Accept this and continue"}), flush=True)
                resumed = events(client.post("/api/resume", json={"thread_id": question["thread_id"],
                    "answer": "Accept this and continue"}))
                assert resumed and resumed[0]["seq"] > frames[-1]["seq"]
                frames.extend(resumed)
                accepted += 1
            counts = Counter(event["type"] for event in frames)
            print(json.dumps({"case": "live-parts-design", "planning": mode, "accepted_test_warnings": accepted, "events": counts,
                              "terminal": frames[-1] if frames else None}), flush=True)
            assert counts["plan"] and counts["selection"] and not counts["error"]
            assert frames[-1]["type"] == "done"
            thread = frames[-1]["thread_id"]
            assert client.get(f"/api/threads/{thread}/board").json()["status"] == "done"
            assert client.get(f"/api/export/{thread}.csv").status_code == 200
            print("PASS: real design completed; saved board and CSV are available (accepted warnings remain warnings)", flush=True)
        assert client.delete(f'/api/projects/{project["id"]}').status_code == 204
        assert client.get(f'/api/projects/{project["id"]}').status_code == 404
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401
        print("PASS: project deletion and session revocation", flush=True)


if __name__ == "__main__":
    main()
