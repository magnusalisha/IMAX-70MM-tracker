"""Bounded read-only source observation; no alerts, secrets, or watcher state."""
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = 'https://www.atomtickets.com'
THEATRE = BASE + '/theaters/amc-lincoln-square-13/164'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
OUT = Path('atom-observations')
OUT.mkdir(exist_ok=True)

def utcnow(): return dt.datetime.now(dt.timezone.utc).isoformat()

class AtomPage(HTMLParser):
    def __init__(self):
        super().__init__(); self.shows=[]; self.dates=set(); self.preorders=[]; self.current=None; self.text=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag!='a': return
        if a.get('data-value'):
            try:
                date=json.loads(a['data-value']).get('serverFormat')
                if date and re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):self.dates.add(date)
            except (ValueError,AttributeError):pass
        if a.get('href','').startswith('/movies/377232/showtimes?'):self.preorders.append(a['href'])
        if a.get('href','').startswith('/checkout/') and a.get('data-vrs-showtime'):
            meta=json.loads(a['data-vrs-showtime'])
            if meta.get('venue_id')!='164':return
            product=json.loads(a.get('data-product-metadata','{}'))
            self.current={'id':a['href'].rsplit('/',1)[-1],'url':BASE+a['href'],'format':meta.get('format'),
                          'movie':product.get('name'),'timeLabel':'','disabled':'disabled' in a.get('class','')}
    def handle_data(self, value):
        self.text.append(value)
        if self.current:self.current['timeLabel']+=value
    def handle_endtag(self, tag):
        if tag=='a' and self.current:
            self.current['timeLabel']=self.current['timeLabel'].strip();self.shows.append(self.current);self.current=None

def fetch(name,url,kind='atom',expected_movie=None):
    result={'source':name,'url':url,'requestedAt':utcnow()}
    is_atom=urllib.parse.urlparse(url).hostname=='www.atomtickets.com'
    referer=THEATRE if is_atom else 'https://www.fandango.com/amc-lincoln-square-13-aabqi/theater-page'
    try:
        req=urllib.request.Request(url,headers={'User-Agent':UA,'Referer':referer,'Accept':'application/json,text/html;q=0.9'})
        with urllib.request.urlopen(req,timeout=20) as r:
            body=r.read().decode('utf-8',errors='replace')
            result.update(status=r.status,completedAt=utcnow(),headers={k:r.headers.get(k) for k in ['Date','Age','ETag','Last-Modified','Cache-Control']},sha256=hashlib.sha256(body.encode()).hexdigest())
        if kind=='atom':
            p=AtomPage();p.feed(body);text=' '.join(p.text)
            if 'AMC Lincoln Square 13' not in text or re.search(r'just a moment|security verification|access denied',text,re.I):
                raise ValueError('Unconfirmed theatre or bot challenge')
            result.update(availableDates=sorted(p.dates),odysseyPreorderPaths=p.preorders,showtimes=p.shows,
                          dateLabels=re.findall(r'Showtimes for [^<]+',body))
            for s in result['showtimes']:
                if expected_movie:s['movie']=expected_movie
            if not p.shows and not re.search(r'no (?:showtimes|movies)|not available|no longer available|no performances',text,re.I):
                # A missing modal/listing is unknown, not a trustworthy empty set.
                raise ValueError('No extractable listings and no explicit empty-result marker')
        elif kind=='fandango':
            vm=json.loads(body).get('viewModel',{})
            if vm.get('theater',{}).get('details',{}).get('name')!='AMC Lincoln Square 13':
                raise ValueError('No verified Fandango theatre payload')
            shows=[]
            for movie in vm.get('movies',[]):
                for variant in movie.get('variants',[]):
                    for group in variant.get('amenityGroups',[]):
                        for s in group.get('showtimes',[]):
                            shows.append({'movie':movie.get('title'),'ticketingDate':s.get('ticketingDate'),
                                          'filmFormat':s.get('filmFormat'),'url':s.get('ticketingJumpPageURL')})
            result.update(date=vm.get('date'),showtimes=shows)
        else:
            result['bodyIdentifiesTheatre']='AMC Lincoln Square 13' in body
        result['valid']=True
    except urllib.error.HTTPError as e:
        result.update(status=e.code,error=str(e),completedAt=utcnow(),valid=False)
    except Exception as e:
        result.update(error=str(e),completedAt=utcnow(),valid=False)
    return result

minutes=int(os.environ.get('OBSERVE_MINUTES','120'))
if not 1<=minutes<=120:raise ValueError('Observation must be between 1 and 120 minutes')
once='--once' in sys.argv
start=time.monotonic(); deadline=start+minutes*60
previous={}; snapshots=[]; events=[]; cycle=0
while True:
    stamp=utcnow();today=dt.datetime.now(ZoneInfo('America/New_York')).date();cycle+=1
    main=fetch('atom-theatre',THEATRE)
    probes=[main]
    # A defined sample of fourteen upcoming dates, plus any Odyssey advance-date
    # links explicitly advertised by the theatre page. This is not a full watcher.
    dates={today+dt.timedelta(days=i) for i in range(14)}
    for path in main.get('odysseyPreorderPaths',[]):
        value=urllib.parse.parse_qs(urllib.parse.urlparse(path).query).get('localDate',[''])[0][:10]
        try:dates.add(dt.date.fromisoformat(value))
        except ValueError:pass
    for date in sorted(dates):
        local=dt.datetime.combine(date,dt.time(2),ZoneInfo('America/New_York')).isoformat()
        url=BASE+'/movies/377232/showtimes?'+urllib.parse.urlencode({'localDate':local,'venueId':'164'})
        probes.append(fetch('atom-odyssey-'+str(date),url,expected_movie='The Odyssey'))
        time.sleep(1)
    probes.append(fetch('atom-imax-control',BASE+'/movies/359230/showtimes?localDate=2026-12-18T02%3A00%3A00-05%3A00&venueId=164',expected_movie='Dune: Part Three'))
    # These are comparison sources, never treated as empty when blocked.
    for date in [today,today+dt.timedelta(days=7)]:
        probes.append(fetch('fandango-'+str(date),'https://www.fandango.com/napi/theaterMovieShowtimes/AABQI?'+urllib.parse.urlencode({'startDate':str(date),'isdesktop':'true'}),kind='fandango'))
    probes.append(fetch('amc-reference','https://www.amctheatres.com/movie-theatres/new-york-city/amc-lincoln-square-13/showtimes?date='+str(today),kind='amc'))
    for result in probes:
        if not result['valid'] or not result['source'].startswith('atom'):continue
        current={s['id']:s for s in result.get('showtimes',[])}
        prior=previous.get(result['source'])
        if prior:
            for key in current.keys()-prior['ids']:
                events.append({'kind':'new-to-sample','source':result['source'],'showtime':current[key],
                               'lastAbsentAt':prior['at'],'firstPresentAt':result['completedAt'],
                               'sourcePublishedAt':None,'measuredFeedDelaySeconds':None})
        previous[result['source']]={'ids':set(current),'at':result['completedAt']}
    snapshot={'cycle':cycle,'startedAt':stamp,'completedAt':utcnow(),'probes':probes}
    snapshots.append(snapshot)
    (OUT/f'cycle-{cycle:03d}.json').write_text(json.dumps(snapshot,indent=2))
    summary={'startedAt':snapshots[0]['startedAt'],'latestAt':snapshot['completedAt'],'cycles':cycle,
             'elapsedMinutes':round((time.monotonic()-start)/60,2),'plannedMinutes':minutes,
             'atomSuccessfulProbes':sum(p['valid'] for snap in snapshots for p in snap['probes'] if p['source'].startswith('atom')),
             'atomFailedProbes':sum(not p['valid'] for snap in snapshots for p in snap['probes'] if p['source'].startswith('atom')),
             'events':events,'measuredFeedDelaySeconds':None,
             'limitation':'First observation is not source publication time. Actual feed delay requires a newly added matching showtime and an independent, unblocked publication reference. No unchanged run can establish update speed.',
             'scope':'Theatre current-day listings; Odyssey next 14 dates and advertised advance-date links; Dune IMAX 70mm positive control. No alerts or watcher changes.'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({'cycle':cycle,'at':utcnow(),'validAtomProbes':sum(p['valid'] for p in probes if p['source'].startswith('atom')),
                      'failedAtomProbes':sum(not p['valid'] for p in probes if p['source'].startswith('atom')),
                      'newToSampleEvents':len(events),'feedDelayMeasured':False}),flush=True)
    if once or time.monotonic()+600>deadline:break
    # Fixed ten-minute starts; a slow round does not create a request burst.
    next_start=start+cycle*600
    while time.monotonic()<min(next_start,deadline):time.sleep(min(30,min(next_start,deadline)-time.monotonic()))
