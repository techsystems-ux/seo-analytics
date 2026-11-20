"""Orbit AI Visibility Score: the formula on its own, no database."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collectors"))
os.environ.setdefault("DATABASE_URL", "postgresql://x@/x")
import ai_visibility as AV

top = {"mentioned": True, "rank": 1, "recommended": True, "sentiment": "positive", "cited": True}
assert AV.answer_value(top) == 1.0
assert abs(AV.position_score(2) - 0.6309) < 1e-4 and AV.position_score(3) == 0.5 and AV.position_score(None) == 0
mid = {"mentioned": True, "rank": 3, "recommended": False, "sentiment": "neutral", "cited": False}
assert abs(AV.answer_value(mid) - (0.5 * 0.5 + 0.15 * 0.5)) < 1e-9                       # .325
assert abs(AV.answer_value(mid, citing=False) - 0.325 / 0.85) < 1e-9                   # no web search: authority dropped, rest rescaled
miss = {"mentioned": False, "rank": None, "cited": False}
# thin evidence can't read as dominance: perfect on 5 answers = 71, on 50 = 96
assert round(AV.score_brand([top] * 5)["score"]) == 71 and round(AV.score_brand([top] * 50)["score"]) == 96
assert AV.score_brand([top] * 5)["raw"] == 100.0
# a prompt that names the brand counts a quarter
s1 = AV.score_brand([dict(top, weight=0.25), dict(miss, weight=1.0)])
s2 = AV.score_brand([dict(top, weight=1.0), dict(miss, weight=1.0)])
assert s1["score"] < s2["score"]
assert [AV.band(x) for x in (5, 20, 40, 70)] == ["Not shortlisted", "Emerging", "Competitive", "Leader"]
print("ALL SCORE FORMULA TESTS PASSED")
