import asyncio, logging, re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit, urlunsplit
import feedparser, httpx

log=logging.getLogger(__name__)
FEEDS=[
 "https://www.theguardian.com/film/rss","https://www.theguardian.com/books/rss",
 "https://www.theguardian.com/music/rss","https://pitchfork.com/rss/news/",
 "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
]
QUERIES=["кино премьера фильм режиссёр","сериал премьера сериал стриминг",
"книга роман писатель литературная премия","музыка альбом релиз музыкант",
"film movie cinema release","TV series streaming release","books publishing novel author","music album release artist"]

def clean_url(u):
 p=urlsplit(u.strip()); return urlunsplit((p.scheme,p.netloc.lower(),p.path.rstrip("/"),"",""))
def parse_date(e):
 v=e.get("published") or e.get("updated")
 if v:
  try:
   d=parsedate_to_datetime(v)
   return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
  except (ValueError,TypeError,OverflowError): pass
 p=e.get("published_parsed") or e.get("updated_parsed")
 return datetime(*p[:6],tzinfo=timezone.utc) if p else datetime.now(timezone.utc)
def strip(s): return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",s or "")).strip()
async def read_feed(client,url):
 r=await client.get(url); r.raise_for_status(); parsed=feedparser.parse(r.text); out=[]
 for e in parsed.entries[:35]:
  url=clean_url(e.get("link","")); title=strip(e.get("title",""))
  if not url or not title: continue
  dt=parse_date(e)
  if dt>datetime.now(timezone.utc)+timedelta(hours=3): continue
  out.append({"url":url,"title":title,"description":strip(e.get("summary","")),
   "source":e.get("source",{}).get("title","") or urlsplit(url).netloc,"published_at":dt.isoformat()})
 return out
async def collect():
 async with httpx.AsyncClient(timeout=httpx.Timeout(18,connect=8),follow_redirects=True,
   headers={"User-Agent":"ZenDigest/1.0 RSS reader"}) as client:
  tasks=[read_feed(client,u) for u in FEEDS]
  tasks += [read_feed(client,"https://news.google.com/rss/search?q="+quote(q)+"&hl=ru&gl=RU&ceid=RU:ru") for q in QUERIES]
  results=await asyncio.gather(*tasks,return_exceptions=True)
 unique={}
 for r in results:
  if isinstance(r,Exception): log.warning("Feed failed: %s",r); continue
  for x in r: unique.setdefault(x["url"],x)
 return list(unique.values())
