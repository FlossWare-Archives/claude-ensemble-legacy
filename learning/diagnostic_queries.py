"""Diagnostic queries over the canonical Graph and Memory REST boundaries."""
from __future__ import annotations
import json, os, urllib.error, urllib.request
from typing import Any

def _post(base,path,payload):
    req=urllib.request.Request(base.rstrip("/") + path,data=json.dumps(payload).encode(),method="POST")
    req.add_header("Content-Type","application/json")
    try:
        with urllib.request.urlopen(req,timeout=5) as r: return json.loads(r.read())
    except urllib.error.URLError as exc: raise RuntimeError(f"service unavailable: {exc}") from exc

class DiagnosticQueries:
    def __init__(self,graph_service_url=None,memory_service_url=None):
        self.graph_service_url=graph_service_url or os.environ.get("ENSEMBLE_GRAPH_URL","http://127.0.0.1:8766")
        self.memory_service_url=memory_service_url or os.environ.get("ENSEMBLE_MEMORY_URL","http://127.0.0.1:8767")
    def semantic_search(self,query,limit=10):
        results=_post(self.memory_service_url,"/memory/search",{"query":query,"limit":limit}).get("results",[])
        return {"ok":True,"error":None,"data":{"query":query,"count":len(results),"results":results},"sample_count":len(results)}
    def graph_traversal(self,start_node,max_depth=3):
        result=_post(self.graph_service_url,"/graph/traverse",{"start":start_node,"max_depth":max_depth})
        return {"ok":True,"error":None,"data":{"start":start_node,"results":result.get("results",[])},"sample_count":len(result.get("results",[]))}
    def find_model_patterns(self,model):
        result=self.graph_traversal(f"model:{model}",2)
        results=result["data"]["results"]
        stats={}
        for item in results:
            node=item.get("node",{})
            if node.get("type")=="task_type": stats.setdefault(node.get("id",""),{"attempts":0})
        return {"ok":True,"error":None,"data":{"model":model,"tasks_attempted":len(stats),"task_stats":stats},"sample_count":len(stats)}
    def find_problematic_tasks(self, limit=10):
        # Keep this diagnostic summary bounded. Memory records may contain long
        # conversation excerpts, and returning 50 full records made the GET
        # response hundreds of kilobytes for a health-oriented diagnostic.
        limit = max(1, min(int(limit), 50))
        result=self.semantic_search("failed inconclusive low confidence",limit)
        return {"ok":True,"error":None,"data":{"problem_tasks":[],"records":result["data"]["results"]},"sample_count":result.get("sample_count",0)}
    def cost_outliers(self,percentile=0.9):
        result=self.semantic_search("arbitration cost expensive",100)
        return {"ok":True,"error":None,"data":{"percentile":percentile,"outliers":result["data"]["results"]},"sample_count":result.get("sample_count",0)}
    def trend_analysis(self,task_type,metric="cost"):
        result=self.semantic_search(f"{task_type} outcome",50)
        return {"ok":True,"error":None,"data":{"task_type":task_type,"metric":metric,"samples":result.get("sample_count",0)},"sample_count":result.get("sample_count",0)}
