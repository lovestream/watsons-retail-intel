"""Validate WTD JSONL against DATA_SCHEMA R3: required fields, enums, score recomputation, ID format."""
import json, re, sys
WEIGHTS={"strategic":0.18,"relevance":0.24,"impact":0.12,"source_credibility":0.12,"data_richness":0.10,"actionability":0.14,"timeliness":0.06,"novelty":0.04}
REQUIRED=["event_id","fact","event_date","published_date","first_seen","region","topic","channels","subjects","primary_url","evidence","source_type","source_tier","verified","verification_level","priority","status","last_updated","relation","score_components","subject","action","object","business_reason"]
RELATIONS={"SAME_OCCURRENCE","SAME_STORY","BACKGROUND","UNRELATED","NEW"}
VERIF={"V1","V2","V3"}
TIER={"T1","T2","T3","T4"}
errors=[]
path=sys.argv[1] if len(sys.argv)>1 else "events/WTD-2026-10-19-24.jsonl"
rows=[json.loads(x) for x in open(path,encoding="utf-8") if x.strip()]
seen=set()
for i,r in enumerate(rows,1):
    for f in REQUIRED:
        if f not in r or r[f] in (None,"",[]):
            # score_components may be null for ARCHIVE, evidence etc handled below
            if f=="score_components" and r.get("priority")=="ARCHIVE":
                continue
            errors.append(f"row {i} {r.get('event_id')} missing/empty {f}")
    if not re.match(r"^EV-\d{8}-\d{2}$", r.get("event_id","")):
        errors.append(f"row {i} bad event_id {r.get('event_id')}")
    if r.get("event_id") in seen:
        errors.append(f"duplicate event_id {r['event_id']}")
    seen.add(r.get("event_id"))
    if r.get("relation") not in RELATIONS:
        errors.append(f"row {i} bad relation {r.get('relation')}")
    if r.get("verification_level") not in VERIF:
        errors.append(f"row {i} bad verification_level {r.get('verification_level')}")
    if r.get("source_tier") not in TIER:
        errors.append(f"row {i} bad source_tier {r.get('source_tier')}")
    sc=r.get("score_components")
    if sc:
        total=sum(sc[k]*WEIGHTS[k] for k in WEIGHTS)
        if abs(round(total,2)-sc.get("total",-1))>0.005:
            errors.append(f"row {i} score mismatch recomputed {round(total,2)} vs {sc.get('total')}")
    if r.get("status")=="superseded" and "superseded_by" not in r:
        errors.append(f"row {i} superseded without superseded_by")
print(f"records: {len(rows)}")
print(f"errors: {len(errors)}")
for e in errors: print(" -",e)
sys.exit(1 if errors else 0)
