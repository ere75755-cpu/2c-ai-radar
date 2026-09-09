#!/usr/bin/env python3
import json, os, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

API_URL='https://api.producthunt.com/v2/api/graphql'
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
PRODUCTS=DATA/'products_raw.json'
METRICS=DATA/'product_metrics.json'
STATUS=DATA/'scan_status.json'
PAGE_SIZE=10
MAX_PAGES=20
FIRST_RUN_LOOKBACK_HOURS=24
OVERLAP_HOURS=2
MAX_WINDOW_HOURS=48
SLEEP=0.8

QUERY='''
query FetchPosts($after:String,$postedAfter:DateTime!,$postedBefore:DateTime!,$first:Int!){
  posts(first:$first,after:$after,postedAfter:$postedAfter,postedBefore:$postedBefore,order:NEWEST){
    edges{node{id name tagline description url website createdAt featuredAt votesCount commentsCount reviewsRating thumbnail{url} topics(first:5){edges{node{id name slug}}}}}
    pageInfo{hasNextPage endCursor}
  }
}
'''

def load(path, default):
    if not path.exists(): return default
    try: return json.loads(path.read_text(encoding='utf-8'))
    except: return default

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def parse_dt(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace('Z','+00:00'))
    except: return None

def window(now):
    st=load(STATUS,{})
    last=parse_dt(st.get('last_scan_at'))
    start=(last-timedelta(hours=OVERLAP_HOURS)) if last else (now-timedelta(hours=FIRST_RUN_LOOKBACK_HOURS))
    floor=now-timedelta(hours=MAX_WINDOW_HOURS)
    if start<floor: start=floor
    return start, now+timedelta(minutes=5)

def gql(vars_, token):
    headers={'Authorization':f'Bearer {token}','Content-Type':'application/json','User-Agent':'2c-ai-radar/1.0'}
    for attempt in range(3):
        r=requests.post(API_URL,headers=headers,json={'query':QUERY,'variables':vars_},timeout=30)
        try: payload=r.json()
        except: payload={}
        limited=(r.status_code==429 or any('rate_limit' in str(e).lower() or 'rate limit' in str(e).lower() for e in payload.get('errors',[])))
        if limited:
            reset=60
            try:
                errs=payload.get('errors') or []
                if errs and isinstance(errs[0],dict): reset=int((errs[0].get('details') or {}).get('reset_in',60))
            except: pass
            if attempt==2: raise RuntimeError(f'Product Hunt rate limit reached. Retry after about {reset} seconds.')
            wait=min(reset+5,180)
            print(f'Rate limited; waiting {wait}s before retry...')
            time.sleep(wait)
            continue
        if r.status_code>=400: raise RuntimeError(f'Product Hunt HTTP {r.status_code}: {r.text[:1000]}')
        if payload.get('errors'): raise RuntimeError('Product Hunt GraphQL error: '+json.dumps(payload['errors'],ensure_ascii=False))
        return payload['data']

def norm(n, ts):
    topics=[]
    for e in ((n.get('topics') or {}).get('edges') or []):
        t=e.get('node') or {}
        topics.append({'id':t.get('id'),'name':t.get('name'),'slug':t.get('slug')})
    return {
        'source':'Product Hunt','source_product_id':str(n.get('id') or ''),'name':n.get('name') or '',
        'tagline':n.get('tagline') or '','description':n.get('description') or '',
        'source_url':n.get('url') or '','website_url':n.get('website') or '',
        'thumbnail_url':(n.get('thumbnail') or {}).get('url') or '',
        'launch_date':n.get('featuredAt') or n.get('createdAt'),'created_at':n.get('createdAt'),'featured_at':n.get('featuredAt'),
        'votes_count':n.get('votesCount'),'comments_count':n.get('commentsCount'),'reviews_rating':n.get('reviewsRating'),
        'topics':topics,'last_updated_at':ts
    }

def main():
    token=os.environ.get('PRODUCTHUNT_TOKEN','').strip()
    if not token: raise SystemExit('Missing PRODUCTHUNT_TOKEN environment variable')
    now=datetime.now(timezone.utc); start,end=window(now); ts=now.isoformat()
    print('Scanning window:',start.isoformat(),'->',end.isoformat())
    existing=load(PRODUCTS,[]); byid={str(x.get('source_product_id')):x for x in existing if x.get('source_product_id')}
    metrics=load(METRICS,[]); m={(str(x.get('source_product_id')),x.get('date')):x for x in metrics}
    cursor=None; fetched=0; page=0; truncated=False
    while page<MAX_PAGES:
        page+=1
        data=gql({'after':cursor,'postedAfter':start.isoformat(),'postedBefore':end.isoformat(),'first':PAGE_SIZE},token)
        conn=data['posts']; edges=conn.get('edges') or []
        print(f'Fetched page {page}: {len(edges)} posts')
        for e in edges:
            n=e.get('node') or {}; pid=str(n.get('id') or '')
            if not pid: continue
            fresh=norm(n,ts); old=byid.get(pid,{})
            merged=dict(old); merged.update(fresh); merged['captured_at']=old.get('captured_at') or old.get('first_captured_at') or ts
            byid[pid]=merged
            m[(pid,now.date().isoformat())]={'source':'Product Hunt','source_product_id':pid,'date':now.date().isoformat(),'votes_count':n.get('votesCount'),'comments_count':n.get('commentsCount'),'reviews_rating':n.get('reviewsRating')}
            fetched+=1
        pi=conn.get('pageInfo') or {}
        if not pi.get('hasNextPage'): break
        cursor=pi.get('endCursor')
        if not cursor: break
        time.sleep(SLEEP)
    else:
        truncated=True
        print(f'Stopped at safety cap of {MAX_PAGES} pages')
    out=sorted(byid.values(),key=lambda x:(x.get('launch_date') or '',x.get('name') or ''),reverse=True)
    save(PRODUCTS,out); save(METRICS,list(m.values()))
    save(STATUS,{'source':'Product Hunt','last_scan_at':ts,'window_start':start.isoformat(),'window_end':end.isoformat(),'page_size':PAGE_SIZE,'pages_fetched':page,'max_pages':MAX_PAGES,'fetched_this_run':fetched,'total_products_raw':len(out),'truncated_by_safety_cap':truncated,'status':'success'})
    print(f'Done. pages={page}, fetched={fetched}, total={len(out)}, truncated={truncated}')

if __name__=='__main__': main()
