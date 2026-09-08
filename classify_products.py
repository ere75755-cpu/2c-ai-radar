#!/usr/bin/env python3
"""Classify PH products into the existing 2C scene taxonomy.

Works without an LLM using conservative keyword rules. If OPENAI_API_KEY is set,
it uses the OpenAI Responses API for richer classification and summaries.
"""
from __future__ import annotations
import json, os, re, hashlib
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/"data"; CONFIG=ROOT/"config"
MODEL=os.environ.get("OPENAI_MODEL","gpt-5.6-luna")

def load(p, default):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except Exception:return default

def dump(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def stable_id(source_id):
    # deterministic JS-safe numeric id; keep far away from hand-maintained IDs (~1k)
    return 100_000_000 + int(hashlib.sha1(str(source_id).encode()).hexdigest()[:8],16) % 1_000_000_000

AI_WORDS=re.compile(r"\b(ai|artificial intelligence|llm|gpt|copilot|agent|agents|generative|machine learning|voice ai|image generation|video generation|chatbot)\b",re.I)
SCENE_HINTS={
    "health": ["health","medical","doctor","skin","fitness","nutrition","sleep","pregnancy","period","pet health"],
    "education": ["learn","study","education","tutor","language","exam"],
    "work": ["productivity","meeting","notes","research","spreadsheet","presentation","workflow","email","calendar"],
    "career": ["job","resume","interview","career","recruit"],
    "finance": ["finance","invest","stock","budget","tax","insurance"],
    "travel": ["travel","trip","hotel","flight","itinerary"],
    "shopping": ["shopping","commerce","product discovery","fashion","beauty"],
    "content": ["creator","video","image","music","writing","content","social media"],
}

def text_of(p):
    ts=" ".join(t.get("name","") for t in p.get("topics",[]))
    return f"{p.get('name','')} {p.get('tagline','')} {p.get('description','')} {ts}".lower()

def rule_classify(p, scenes):
    text=text_of(p); is_ai=bool(AI_WORDS.search(text)) or any('artificial intelligence' in t.get('name','').lower() for t in p.get('topics',[]))
    # token overlap with scene name/need. Conservative; LLM can replace when configured.
    best=None; best_score=0
    words=set(re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,}",text))
    for s in scenes:
        st=(s.get('domain','')+' '+s.get('scene','')+' '+s.get('need','')).lower()
        toks=set(re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,}",st))
        score=len(words & toks)
        # broad English hints into Chinese domains
        d=s.get('domain','')
        if any(k in text for k in SCENE_HINTS['health']) and ('健康' in d or '医疗' in d): score+=2
        if any(k in text for k in SCENE_HINTS['education']) and ('学习' in d or '教育' in d): score+=2
        if any(k in text for k in SCENE_HINTS['work']) and ('工作' in d or '生产力' in d): score+=2
        if any(k in text for k in SCENE_HINTS['career']) and ('求职' in d or '职业' in d): score+=2
        if any(k in text for k in SCENE_HINTS['finance']) and ('金融' in d or '财富' in d): score+=2
        if any(k in text for k in SCENE_HINTS['travel']) and ('旅行' in d or '旅游' in d): score+=2
        if any(k in text for k in SCENE_HINTS['shopping']) and ('商品' in d or '消费' in d or '购物' in d): score+=2
        if any(k in text for k in SCENE_HINTS['content']) and ('创作' in d or '内容' in d): score+=2
        if score>best_score: best,best_score=s,score
    if not best: best=scenes[0]
    confidence=min(.78,.30+.08*best_score) if best_score else .20
    votes=p.get('votesCount',0)
    priority=1 if not is_ai else 4 if votes>=500 else 3 if votes>=200 else 2
    return {"isAI":is_ai,"sceneId":best['sceneId'],"sceneConfidence":round(confidence,2),"productCore":p.get('tagline',''),
            "whyInteresting":"自动初筛：基于 Product Hunt 描述、Topics 与热度；建议人工复核。",
            "pattern":"待人工/LLM归纳","inspiration":"待人工/LLM归纳","autoResearchPriority":priority,
            "newSceneCandidate":False,"suggestedNewScene":"","newSceneReason":""}

def llm_classify(p, scenes):
    from openai import OpenAI
    client=OpenAI()
    compact=[{"sceneId":s['sceneId'],"domain":s['domain'],"scene":s['scene'],"need":s.get('need','')} for s in scenes]
    prompt=f"""你是2C AI产品研究员。判断 Product Hunt 产品是否与AI相关，并且只能从给定场景库中选择最匹配sceneId。
若现有场景明显无法覆盖且这是有价值的新2C需求，可把 newSceneCandidate 设为 true，但仍需给出最接近的 sceneId。
输出严格JSON，不要markdown。字段：isAI(boolean), sceneId(integer), sceneConfidence(0-1), productCore(中文1句话), whyInteresting(中文1句话), pattern(英文/中文短标签), inspiration(中文1句话), autoResearchPriority(1-5), newSceneCandidate(boolean), suggestedNewScene(string), newSceneReason(string)。
产品：{json.dumps(p,ensure_ascii=False)}
场景库：{json.dumps(compact,ensure_ascii=False)}"""
    r=client.responses.create(model=MODEL,input=prompt)
    txt=r.output_text.strip()
    txt=re.sub(r'^```(?:json)?\s*|\s*```$','',txt,flags=re.S)
    out=json.loads(txt)
    allowed={s['sceneId'] for s in scenes}
    if out.get('sceneId') not in allowed: raise ValueError('LLM returned unknown sceneId')
    return out

def to_dashboard(p,c):
    # Map into existing v24 product object schema.
    return {
        "id":stable_id(p['sourceProductId']),"sourceProductId":p['sourceProductId'],"source":"Product Hunt · Auto",
        "captureSource":"Product Hunt","name":p.get('name',''),"sceneId":c['sceneId'],"intro":c.get('productCore') or p.get('tagline',''),
        "biz":"待核实","website":p.get('producthuntUrl') or p.get('websiteUrl',''),"demo":p.get('websiteUrl') or p.get('producthuntUrl',''),
        "feedback":f"PH: {p.get('votesCount',0)} votes · {p.get('commentsCount',0)} comments · rating {p.get('reviewsRating',0)}",
        "reason":c.get('whyInteresting',''),"launchDate":(p.get('createdAt') or '')[:10],"pattern":c.get('pattern',''),"inspiration":c.get('inspiration',''),
        "signal":f"{p.get('votesCount',0)} votes · {p.get('commentsCount',0)} comments","captureDate":(p.get('lastCapturedAt') or '')[:10],"foundedDate":"待核实",
        "starred":False,"teamNote":"","researchPriority":c.get('autoResearchPriority',2),"researchWhy":c.get('whyInteresting',''),
        "autoResearchPriority":c.get('autoResearchPriority',2),"autoClassified":True,"isAI":c.get('isAI',False),"sceneConfidence":c.get('sceneConfidence',0),
        "newSceneCandidate":c.get('newSceneCandidate',False),"suggestedNewScene":c.get('suggestedNewScene',''),"newSceneReason":c.get('newSceneReason',''),
        "thumbnailUrl":p.get('thumbnailUrl',''),"phVotes":p.get('votesCount',0),"phComments":p.get('commentsCount',0),"phRating":p.get('reviewsRating',0)
    }

def main():
    raw=load(DATA/"products_raw.json",[]); taxonomy=load(CONFIG/"scenes.json",{}); scenes=taxonomy.get('scenes',[])
    old=load(DATA/"products_auto.json",[]); old_by={str(x.get('sourceProductId')):x for x in old}
    use_llm=bool(os.environ.get('OPENAI_API_KEY','').strip())
    out=[]; errors=[]
    for p in raw:
        try:
            c=llm_classify(p,scenes) if use_llm else rule_classify(p,scenes)
        except Exception as e:
            errors.append(f"{p.get('name')}: {e}"); c=rule_classify(p,scenes)
        item=to_dashboard(p,c)
        prev=old_by.get(str(p.get('sourceProductId')),{})
        # no manual fields should normally live here, but preserve if someone hand-edited JSON.
        for key in ('starred','teamNote','researchPriority','researchWhy'):
            if key in prev and prev.get(key) not in (None,'',False): item[key]=prev[key]
        out.append(item)
    out.sort(key=lambda x:(x.get('launchDate',''),x.get('phVotes',0)),reverse=True)
    dump(DATA/"products_auto.json",out)
    status=load(DATA/"scan_status.json",{}); status.update({"classifiedAt":datetime.now(timezone.utc).isoformat(),"classifier":"openai" if use_llm else "rules","totalAutoProducts":len(out),"classificationErrors":errors[:30]})
    dump(DATA/"scan_status.json",status)
    print(f"Classified {len(out)} products via {'OpenAI '+MODEL if use_llm else 'rules'}; {len(errors)} LLM fallbacks")

if __name__=='__main__':main()
