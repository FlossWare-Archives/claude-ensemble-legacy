#!/usr/bin/env python3
"""End-to-end tests for the canonical Ensemble REST gateway."""
from __future__ import annotations
import json, os, tempfile, threading, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT_REPO = Path(__file__).resolve().parents[1]
for import_path in (
    ROOT_REPO,
    ROOT_REPO / "graph-service",
    ROOT_REPO / "memory-service",
    ROOT_REPO / "learning",
):
    sys.path.insert(0, str(import_path))

def request(server, method, path, payload=None, headers=None):

    url=f"http://127.0.0.1:{server.server_port}{path}"
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(url,data=data,method=method)
    req.add_header("Content-Type","application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req,timeout=3) as r: return r.status,json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code,json.loads(e.read())

def request_with_headers(server, method, path, payload=None, headers=None):
    url=f"http://127.0.0.1:{server.server_port}{path}"
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(url,data=data,method=method)
    req.add_header("Content-Type","application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req,timeout=3) as r:
            return r.status, dict(r.headers.items()), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), json.loads(e.read())

def run():
    import sys, time

    class QueryCaptureHandler(BaseHTTPRequestHandler):
        seen_path = None
        seen_headers = None


        def do_GET(self):
            QueryCaptureHandler.seen_path = self.path
            QueryCaptureHandler.seen_headers = {k.lower(): v for k, v in self.headers.items()}
            body = b"{\"ok\": true, \"service\": \"claude-ensemble\"}\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    capture = ThreadingHTTPServer(("127.0.0.1", 0), QueryCaptureHandler)
    ct = threading.Thread(target=capture.serve_forever, daemon=True)
    ct.start()

    import server.ensemble_server as ensemble_module
    ensemble_module._health_cache.clear()
    original_urlopen = ensemble_module.urllib.request.urlopen
    probe_calls = {"count": 0}

    def counting_urlopen(*args, **kwargs):
        probe_calls["count"] += 1
        return original_urlopen(*args, **kwargs)
    ensemble_module.urllib.request.urlopen = counting_urlopen
    try:
        base = f"http://127.0.0.1:{capture.server_port}"
        assert ensemble_module._ensemble_target(base)
        assert ensemble_module._ensemble_target(base)
        assert probe_calls["count"] == 1
    finally:
        ensemble_module.urllib.request.urlopen = original_urlopen
        ensemble_module._health_cache.clear()
    root_repo=Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root_repo))
    sys.path.insert(0, str(root_repo/"graph-service"))
    sys.path.insert(0, str(root_repo/"memory-service"))
    sys.path.insert(0, str(root_repo/"learning"))
    from graph_service import create_server as graph_server
    from memory_service import MemoryService
    from server.ensemble_server import create_server as gateway_server, _forward
    from learning.arbitration_advisor import ArbitrationAdvisor
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        graph=graph_server("127.0.0.1",0,root/"graph.json")
        gt=threading.Thread(target=graph.serve_forever,daemon=True); gt.start()
        memory=MemoryService(root/"memory.sock",root/"memory")
        mt=threading.Thread(target=memory.start,daemon=True); mt.start()
        for _ in range(200):
            if memory.http_port: break
            time.sleep(.01)
        assert memory.http_port
        unavailable=gateway_server("127.0.0.1",0,graph_url="",memory_url="")
        ut=threading.Thread(target=unavailable.serve_forever,daemon=True); ut.start()
        try:
            status,body=request(unavailable,"GET","/api/v1/graph/health")
            assert status==503 and not body["ok"]
        finally:
            unavailable.shutdown(); unavailable.server_close(); ut.join(2)

        advisor=ArbitrationAdvisor()
        advisor.recommend_models=lambda *args, **kwargs: {
            "ok":True,"error":None,"error_code":None,
            "data":{"recommended":[{"model":"sonnet"}],"sample_count":1},
            "sample_count":1,
        }
        advisor.recommend_phases=lambda *args, **kwargs: {
            "ok":True,"error":None,"error_code":None,
            "data":{"recommendation":"2","failure_count":0},
            "sample_count":1,
        }
        advisor.estimate_cost=lambda *args, **kwargs: {
            "ok":False,"error":"No observed cost data","error_code":"no_data","data":None,
        }
        result=advisor.full_recommendation("code_review")
        assert not result["ok"] and result["error_code"]=="no_data"
        assert result["data"]["details"]["cost"]["error_code"]=="no_data"

        class CostAnalytics:
            def best_models_for(self, task_type, *args, **kwargs):
                return {
                    "ok":True,
                    "data":[
                        {"model":"cheap","avg_cost":0.10},
                        {"model":"expensive","avg_cost":0.20},
                    ],
                    "sample_count":2,
                }

        cost_advisor=ArbitrationAdvisor(CostAnalytics())
        previous=os.environ.get("ENSEMBLE_ARBITER_COST")
        os.environ["ENSEMBLE_ARBITER_COST"]="0.08"
        try:
            result=cost_advisor.estimate_cost(["cheap","expensive"],"code_review",phases=3)
        finally:
            if previous is None:
                os.environ.pop("ENSEMBLE_ARBITER_COST",None)
            else:
                os.environ["ENSEMBLE_ARBITER_COST"]=previous
        assert result["ok"]
        assert abs(result["data"]["worker_cost_per_phase"]-0.30)<1e-6
        assert abs(result["data"]["total_per_phase"]-0.38)<1e-6
        assert abs(result["data"]["total_all_phases"]-1.14)<1e-6

        query_gateway=gateway_server("127.0.0.1",0,
                                      graph_url=f"http://127.0.0.1:{capture.server_port}",
                                      memory_url="")
        qgt=threading.Thread(target=query_gateway.serve_forever,daemon=True)
        qgt.start()
        try:
            status,body=request(query_gateway,"GET","/api/v1/graph/capture?scope=remote&limit=2",headers={"Authorization":"Bearer secret","X-Request-ID":"req-169"})
            assert status==200 and body["ok"]
            assert QueryCaptureHandler.seen_path=="/api/v1/graph/capture?scope=remote&limit=2"
            assert QueryCaptureHandler.seen_headers["x-request-id"]=="req-169"
            assert "authorization" not in QueryCaptureHandler.seen_headers
        finally:
            query_gateway.shutdown(); query_gateway.server_close(); qgt.join(2)

        gateway=gateway_server("127.0.0.1",0,
                               graph_url=f"http://127.0.0.1:{graph.server_port}",
                               memory_url=f"http://127.0.0.1:{memory.http_port}")
        kt=threading.Thread(target=gateway.serve_forever,daemon=True); kt.start()
        try:
            # Collaboration authentication follows RFC 6750 semantics.
            original_token = os.environ.get("ENSEMBLE_COLLABORATION_AUTH_TOKEN")
            original_solvers = os.environ.get("ENSEMBLE_COLLABORATION_SOLVERS")
            os.environ.pop("ENSEMBLE_COLLABORATION_AUTH_TOKEN", None)
            try:
                status,body=request(gateway,"POST","/api/v1/collaboration/run",{"task":"auth test"})
                assert status==503 and body["error_code"]=="service_unavailable"
            finally:
                os.environ["ENSEMBLE_COLLABORATION_AUTH_TOKEN"]="test-collaboration-token"

            status,headers,body=request_with_headers(gateway,"POST","/api/v1/collaboration/run",{"task":"auth test"})
            assert status==401 and body["error_code"]=="unauthorized"
            assert next((value for name, value in headers.items() if name.lower() == "www-authenticate"), None) == "Bearer", headers

            status,headers,body=request_with_headers(
                gateway,"POST","/api/v1/collaboration/run",{"task":"auth test"},
                {"Authorization":"Bearer incorrect-token"},
            )
            assert status==401 and body["error_code"]=="unauthorized"
            assert next((value for name, value in headers.items() if name.lower() == "www-authenticate"), None) == "Bearer", headers

            os.environ["ENSEMBLE_COLLABORATION_SOLVERS"]="sonnet"
            status,body=request(
                gateway,"POST","/api/v1/collaboration/run",{"task":"auth test","solvers":["haiku"]},
                {"Authorization":"Bearer test-collaboration-token"},
            )
            assert status==403 and body["error_code"]=="forbidden"

            if original_solvers is None:
                os.environ.pop("ENSEMBLE_COLLABORATION_SOLVERS",None)
            else:
                os.environ["ENSEMBLE_COLLABORATION_SOLVERS"]=original_solvers
            if original_token is None:
                os.environ.pop("ENSEMBLE_COLLABORATION_AUTH_TOKEN",None)
            else:
                os.environ["ENSEMBLE_COLLABORATION_AUTH_TOKEN"]=original_token

            assert request(gateway,"GET","/api/v1/health")[0]==200
            for node in [
                {"id":"model:sonnet","type":"model","properties":{"name":"sonnet"}},
                {"id":"task:code_review","type":"task_type","properties":{"name":"code_review"}},
            ]:
                assert request(gateway,"POST","/api/v1/graph/add-node",node)[0]==200
            assert request(gateway,"POST","/api/v1/graph/add-edge",{
                "source":"model:sonnet","target":"task:code_review","type":"succeeded_on",
                "properties":{"cost":0.12}})[0]==200

            # Collaboration completion must automatically persist a safe Knowledge
            # event and a decision provenance record.
            import server.ensemble_server as gateway_module
            from collaboration import Candidate, CollaborationResult, CollaborationState, ReviewRecord

            class FakeCollaboration:
                def __init__(self, *args, **kwargs):
                    pass

                def run(self, *, context=""):
                    state = CollaborationState("test collaboration")
                    candidate = Candidate("r1-sonnet", "sonnet", 1, "workspace password=SUPER-SECRET")
                    state.candidates.append(candidate)
                    adjudication = {
                        "selected_candidate": candidate.candidate_id,
                        "decision": "Use API key SUPER-SECRET",
                        "rationale": "supported by review evidence",
                        "supporting_evidence": ["reviewer support"],
                        "rejected_alternatives": [],
                        "blocking_concerns": [],
                        "follow_ups": [],
                        "complete": True,
                        "human_decision_required": False,
                    }
                    state.adjudications.append(adjudication)
                    state.reviews.append(ReviewRecord(
                        candidate_id="r1-sonnet", reviewer="test-reviewer", status="complete",
                        verdict="approve", summary="found API_KEY=SUPER-SECRET",
                        findings=({"category": "credential", "detail": "SUPER-SECRET"},),
                        provider="test", model="reviewer-model", error="",
                    ))
                    return CollaborationResult("accepted", candidate, adjudication, state)

            memory_events = []
            provenance_records = []

            class FakeMemoryWriter:
                def write_event(self, **kwargs):
                    memory_events.append(kwargs)
                    return True

            class FakeProvenanceStore:
                def record(self, record):
                    provenance_records.append(record)
                    return record

            original_collaboration = gateway_module.CollaborationOrchestrator
            original_memory_writer = gateway.application.memory_writer
            original_provenance_store = gateway.application.provenance
            original_resolve = gateway.application.models.registry.resolve
            gateway_module.CollaborationOrchestrator = FakeCollaboration
            gateway.application.memory_writer = FakeMemoryWriter()
            gateway.application.provenance = FakeProvenanceStore()
            gateway.application.models.registry.resolve = lambda *_args, **_kwargs: object()
            os.environ["ENSEMBLE_COLLABORATION_AUTH_TOKEN"] = "test-collaboration-token"
            try:
                status, body = request(
                    gateway,
                    "POST",
                    "/api/v1/collaboration/run",
                    {"task": "test collaboration"},
                    {
                        "Authorization": "Bearer test-collaboration-token",
                        "X-Request-ID": "collab-test-1",
                    },
                )
                assert status == 200 and body["ok"]
                assert body["knowledge_persisted"] is True
                assert body["execution_id"] == "collab-test-1"
                assert len(memory_events) == 1
                assert memory_events[0]["event_type"] == "collaboration.result"
                assert memory_events[0]["event_id"] == "collab-test-1"
                assert memory_events[0]["payload"]["selected_candidate"]["candidate_id"] == "r1-sonnet"
                assert "proposal" not in memory_events[0]["payload"]["selected_candidate"]
                assert "summary" not in memory_events[0]["payload"]["reviews"][0]
                assert "findings" not in memory_events[0]["payload"]["reviews"][0]
                assert "SUPER-SECRET" not in json.dumps(memory_events[0]["payload"])
                assert len(provenance_records) == 1
                assert provenance_records[0].execution_id == "collab-test-1"
                assert provenance_records[0].selected == "r1-sonnet"

                class FailingMemoryWriter:
                    def write_event(self, **kwargs):
                        return False

                gateway.application.memory_writer = FailingMemoryWriter()
                status, body = request(
                    gateway,
                    "POST",
                    "/api/v1/collaboration/run",
                    {"task": "test collaboration"},
                    {
                        "Authorization": "Bearer test-collaboration-token",
                        "X-Request-ID": "collab-test-failure",
                    },
                )
                assert status == 200
                assert body["ok"] is True
                assert body["execution_id"] == "collab-test-failure"
                assert body["knowledge_persisted"] is False
                assert body["provenance_persisted"] is True
                assert len(provenance_records) == 2

                class FailingProvenanceStore:
                    def record(self, record):
                        raise RuntimeError("provenance unavailable")

                gateway.application.memory_writer = FakeMemoryWriter()
                gateway.application.provenance = FailingProvenanceStore()
                status, body = request(
                    gateway,
                    "POST",
                    "/api/v1/collaboration/run",
                    {"task": "test collaboration"},
                    {
                        "Authorization": "Bearer test-collaboration-token",
                        "X-Request-ID": "collab-test-provenance-failure",
                    },
                )
                assert status == 200
                assert body["ok"] is True
                assert body["knowledge_persisted"] is True
                assert body["provenance_persisted"] is False
            finally:
                gateway_module.CollaborationOrchestrator = original_collaboration
                gateway.application.memory_writer = original_memory_writer
                gateway.application.provenance = original_provenance_store
                gateway.application.models.registry.resolve = original_resolve
                if original_token is None:
                    os.environ.pop("ENSEMBLE_COLLABORATION_AUTH_TOKEN", None)
                else:
                    os.environ["ENSEMBLE_COLLABORATION_AUTH_TOKEN"] = original_token

            # Memory is independently reachable through the same public boundary.
            assert request(gateway,"POST","/api/v1/memory/write",
                           {"name":"rest-test","content":"gateway memory round trip"})[0]==200
            status,body=request(gateway,"POST","/api/v1/memory/read",{"name":"rest-test"})
            assert status==200 and body["ok"] and body["content"]=="gateway memory round trip",body

            status,body=request(gateway,"POST","/api/v1/decision/analytics/best-models",{"task_type":"code_review"})
            assert status==200 and body["ok"] and body["data"][0]["model"]=="sonnet",body
            status,body=request(gateway,"POST","/api/v1/decision/query/patterns",{"model":"sonnet"})
            assert status==200 and body["ok"] and body["sample_count"]==1,body
            status,body=request(gateway,"POST","/api/v1/decision/analytics/best-models",{})
            assert status==400 and not body["ok"] and body["error_code"]=="invalid_request"
            status,body=request(gateway,"POST","/api/v1/decision/query/semantic",{})
            assert status==400 and not body["ok"] and body["error_code"]=="invalid_request"

            # A declared oversized body is rejected before any read/allocation.
            status,body=request(gateway,"POST","/api/v1/graph/add-node",{"id":"oversized"},
                                 {"Content-Length":str(16*1024*1024+1)})
            assert status==400 and not body["ok"]

            # Gateway instances retain their own upstream routing.
            graph2=graph_server("127.0.0.1",0,root/"graph2.json")
            gt2=threading.Thread(target=graph2.serve_forever,daemon=True); gt2.start()
            gateway2=gateway_server("127.0.0.1",0,
                                    graph_url=f"http://127.0.0.1:{graph2.server_port}",
                                    memory_url=f"http://127.0.0.1:{memory.http_port}")
            kt2=threading.Thread(target=gateway2.serve_forever,daemon=True); kt2.start()
            try:
                assert request(gateway2,"POST","/api/v1/graph/add-node",
                               {"id":"model:other","type":"model","properties":{}})[0]==200
                assert request(gateway,"POST","/api/v1/graph/add-node",
                               {"id":"model:first","type":"model","properties":{}})[0]==200
                status,_=request(gateway2,"GET","/api/v1/graph/node/model:first")
                assert status==404
                status,_=request(gateway2,"GET","/api/v1/graph/node/model:other")
                assert status==200

                # A service URL may point at another Ensemble instance. The
                # caller still uses the same public path and the intermediate
                # gateway forwards /api/v1/graph/* transparently.
                federated=gateway_server(
                    "127.0.0.1",0,
                    graph_url=f"http://127.0.0.1:{gateway2.server_port}",
                    memory_url=f"http://127.0.0.1:{memory.http_port}",
                )
                ft=threading.Thread(target=federated.serve_forever,daemon=True); ft.start()
                try:
                    status,body=request(federated,"POST","/api/v1/graph/add-node",
                                         {"id":"model:remote","type":"model","properties":{}})
                    assert status==200 and body["ok"]
                    status,_=request(gateway2,"GET","/api/v1/graph/node/model:remote")
                    assert status==200
                finally:
                    federated.shutdown(); federated.server_close(); ft.join(2)

                # Two Ensemble gateways pointing at each other must terminate.
                cycle_a=gateway_server("127.0.0.1",0,graph_url="",memory_url="")
                cycle_b=gateway_server("127.0.0.1",0,graph_url="",memory_url="")
                cycle_a.application.service_urls["graph"] = "http://127.0.0.1:" + str(cycle_b.server_port)
                cycle_b.application.service_urls["graph"] = "http://127.0.0.1:" + str(cycle_a.server_port)
                at=threading.Thread(target=cycle_a.serve_forever,daemon=True); bt=threading.Thread(target=cycle_b.serve_forever,daemon=True)
                at.start(); bt.start()
                try:
                    status,body=request(cycle_a,"GET","/api/v1/graph/cycle")
                    assert status==503 and not body["ok"]
                finally:
                    cycle_a.shutdown(); cycle_a.server_close(); cycle_b.shutdown(); cycle_b.server_close()
                    at.join(2); bt.join(2)

                # A service URL that resolves to this gateway must not recurse.
                gateway.application.service_urls["graph"] = f"http://127.0.0.1:{gateway.server_port}"
                status,body=request(gateway,"GET","/api/v1/graph/health")
                assert status==503 and not body["ok"]
                gateway.application.service_urls["graph"] = f"http://127.0.0.1:{graph.server_port}"
            finally:
                gateway2.shutdown(); gateway2.server_close()
                graph2.shutdown(); graph2.server_close()
                kt2.join(2); gt2.join(2)
        finally:
            gateway.shutdown(); gateway.server_close()
            graph.shutdown(); graph.server_close()
            memory.stop()
            kt.join(2); gt.join(2); mt.join(2)
            capture.shutdown(); capture.server_close(); ct.join(2)

if __name__=="__main__":
    run(); print("ensemble REST gateway tests passed")
