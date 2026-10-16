"""Read-only source validation. No watcher, state, credentials, or notifications."""
import urllib.request, urllib.error, json, re, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
from html import unescape
TODAY = datetime.datetime.now(ZoneInfo('America/New_York')).date()
TOMORROW = TODAY + datetime.timedelta(days=1)
results=[]
def get(name, url, parse=False):
 result={'source':name,'requested':url,'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json,text/html;q=0.9'})
  with urllib.request.urlopen(request, timeout=25) as response:
   body=response.read().decode('utf-8',errors='replace')
   result.update(status=response.status,url=response.url,bytes=len(body),serverDate=response.headers.get('Date'),age=response.headers.get('Age'))
  if parse:
   data=json.loads(body); vm=data.get('viewModel',{})
   result.update(theatre=vm.get('theater',{}).get('details',{}).get('name'),date=vm.get('date'))
   if 'showtimeDates' in data:
    dates=data['showtimeDates'];result.update(calendarDates=dates)
   movies=[]
   for m in vm.get('movies',[]):
    groups=[]
    for v in m.get('variants',[]):
     for g in v.get('amenityGroups',[]):
      amenities=[a.get('name') for a in g.get('amenities',[])]
      shows=[{k:s.get(k) for k in ['id','ticketingDate','showtimeHashCode','ticketingJumpPageURL','isSoldOut','type','expired','filmFormat']} for s in g.get('showtimes',[])]
      groups.append({'formatHeader':v.get('filmFormatHeader'),'amenities':amenities,'count':len(shows),'samples':shows[:3]})
    movies.append({'id':m.get('id'),'title':m.get('title'),'groups':groups})
   result['movies']=movies
  else:
   title=re.search(r'<title[^>]*>(.*?)</title>',body,re.S|re.I)
   result['title']=unescape(title.group(1)) if title else None
   if name.startswith('bigscreen'):
    result['dateHeader']=re.findall(r'<h2>Showtimes.*?</h2>',body)
    sample=[]
    for row in re.findall(r'<tr\b[^>]*>.*?</tr>',body,re.S|re.I):
     if not re.search(r'IMAX 70mm|The Odyssey',row,re.I):continue
     movie=re.search(r'class="movieNameList">(.*?)</a>',row,re.S)
     comment=re.findall(r'class="showcomment">(.*?)</span>',row,re.S)
     links=[{'url':unescape(u),'label':re.sub('<[^>]+>','',label)} for u,label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*title="Buy tickets for this showtime from Fandango"[^>]*>(.*?)</a>',row,re.S)]
     if movie:sample.append({'movie':unescape(movie.group(1)),'attributes':comment,'links':links})
    result['samples']=sample
 except urllib.error.HTTPError as e:result.update(status=e.code,error=str(e))
 except Exception as e:result['error']=str(e)
 results.append(result)
 print(json.dumps(result),flush=True)
 return result
base='https://www.fandango.com/napi/'
get('fandango-calendar',base+'theaterCalendar/AABQI',True)
for day in [str(TODAY),str(TOMORROW),'2026-12-18']:
 get('fandango-'+day,base+'theaterMovieShowtimes/AABQI?startDate='+day+'&isdesktop=true',True)
 time.sleep(1)
for day in [str(TODAY),'2026-12-18']:
 get('bigscreen-'+day,'https://www.bigscreen.com/Marquee.php?theater=164&view=sched&showdate='+day)
get('atom','https://www.atomtickets.com/theaters/amc-lincoln-square-13/164')
get('tribute','https://www.tributemovies.com/cinema/New-York/New-York/AMC-Lincoln-Square-13/7511/')
# Read only the first redirects; never solve queues or submit ticketing forms.
from urllib.parse import urlparse, parse_qs
class TraceRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self, request, fp, code, msg, headers, newurl):
  q=parse_qs(urlparse(newurl).query)
  self.hops.append({'status':code,'host':urlparse(newurl).hostname,'path':urlparse(newurl).path,'selection':{k:q[k] for k in ['tid','mid','sdate'] if k in q}})
  if len(self.hops)>=2: return None
  return super().redirect_request(request,fp,code,msg,headers,newurl)
for r in list(results):
 if r['source']=='bigscreen-2026-12-18':
  links=[x['url'] for sample in r.get('samples',[]) for x in sample['links']]
  for url in links[:2]:
   trace=TraceRedirect();trace.hops=[]
   try: urllib.request.build_opener(trace).open(url,timeout=20).close()
   except Exception: pass
   result={'source':'bigscreen-ticket-redirect','url':url,'hops':trace.hops}
   results.append(result);print(json.dumps(result),flush=True)
# Follow one returned exact-screening URL, without selecting seats or buying.
for result in list(results):
 if result['source']=='fandango-2026-12-18':
  for movie in result.get('movies',[]):
   for group in movie['groups']:
    if any('70MM' in a.upper() for a in group['amenities']) and group['samples']:
     get('fandango-ticket-link',group['samples'][0]['ticketingJumpPageURL']);break
   else:continue
   break
Path('source-probe-results.json').write_text(json.dumps(results,indent=2))
