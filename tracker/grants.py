"""Reconcile mutually exclusive volunteer reservations from trusted main-branch code."""
import json,os,re,sys,time,urllib.request,urllib.error
from tasks import load
from collections import defaultdict
repo=os.environ['GITHUB_REPOSITORY']
base='https://api.github.com/repos/'+repo
limits: dict = {'remaining': None, 'reset': None}
def api(route,method='GET',body=None,attempts=4):
 for attempt in range(attempts):
  request=urllib.request.Request(base+route,method=method,headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'replay-claims'},data=None if body is None else json.dumps(body).encode())
  try:
   with urllib.request.urlopen(request,timeout=30) as response:
    remaining=response.headers.get('X-RateLimit-Remaining')
    limits['remaining']=int(remaining) if remaining is not None else None
    limits['reset']=response.headers.get('X-RateLimit-Reset')
    return json.loads(response.read() or 'null')
  except urllib.error.HTTPError as error:
   if error.code not in (403,429) or attempt==attempts-1:raise
   # A busy ledger can exhaust the run token's hourly budget. Waiting for the
   # documented reset keeps the pass from failing outright and replaying the
   # same work on the next issue event.
   reset=error.headers.get('X-RateLimit-Reset')
   delay=min(120,max(5,int(reset)-int(time.time())+2)) if reset else 15*(attempt+1)
   print(f'HTTP {error.code} on {method} {route}; sleeping {delay}s (attempt {attempt+1}/{attempts})',flush=True)
   time.sleep(delay)
def main():
 rows=load();groups=defaultdict(set);reserved=set()
 for r in rows:
  groups[r['id']].add(r['id']);groups[r['packet']].add(r['id'])
  if r['state']!='open':reserved.add(r['id'])
 issues=[];page=1
 while True:
  batch=api(f'/issues?state=open&per_page=100&page={page}')
  issues.extend(i for i in batch if 'pull_request' not in i)
  if len(batch)<100:break
  page+=1
 planned=[];taken=set()
 # Preserve valid grants first, then use issue number as a deterministic tie-breaker.
 prior={}
 for i in issues:
  labels={l['name'] for l in i['labels']}
  if 'claim-granted' in labels:
   comments=api('/issues/'+str(i['number'])+'/comments?per_page=100')
   grants=[x['body'] for x in comments if x['user']['login']=='github-actions[bot]' and x['body'].startswith('Granted `')]
   if grants:prior[i['number']]=grants[-1].split('`')[1]
 for i in sorted(issues,key=lambda z:(prior.get(z['number'])!=z['title'].removeprefix('Claim '),z['number'])):
  labels={l['name'] for l in i['labels']};m=re.fullmatch(r'Claim (P\d{4}|M\d{6})',i['title'])
  if not m and not labels.intersection({'claim-granted','claim-conflict','claim'}):continue
  target=m[1] if m else '';mods=groups.get(target,set())
  win=bool(mods) and not mods.intersection(taken|reserved)
  if win:taken.update(mods)
  desired='claim-granted' if win else 'claim-conflict'
  planned.append((i['number'],labels,target,desired))
 writes=sum(1 for number,labels,target,desired in planned if desired not in labels or 'claim' not in labels)
 if limits['remaining'] is not None and limits['remaining']<writes+8:
  # Leave the queue intact for the next run rather than dying halfway through
  # the label pass with a partially applied ledger.
  print(f"stopping before the label pass: {limits['remaining']} requests left, {writes} needed (resets at {limits['reset']})",flush=True)
  return
 for number,labels,target,desired in planned:
  other='claim-conflict' if desired=='claim-granted' else 'claim-granted'
  if other in labels:api(f'/issues/{number}/labels/{other}','DELETE')
 for number,labels,target,desired in planned:
  if desired not in labels or 'claim' not in labels:api(f'/issues/{number}/labels','POST',{'labels':['claim',desired]})
  if desired=='claim-granted' and (desired not in labels or prior.get(number)!=target):
   api(f'/issues/{number}/comments','POST',{'body':f'Granted `{target}`. Begin this reservation. Attach your receipt and log to this issue, or close it to release the work.'})
  elif desired=='claim-conflict' and desired not in labels:
   api(f'/issues/{number}/comments','POST',{'body':'No grant. This selection is unavailable or overlaps existing work. Close this issue and choose a new open module or packet with `python3 tracker/tasks.py next`.'})
if __name__=='__main__':main()
