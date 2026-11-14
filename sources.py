import asyncio, logging, re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode
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

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
def clean_url(u):
 p=urlsplit(u.strip())
 query=urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                  if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS])
 return urlunsplit((p.scheme,p.netloc.lower(),p.path.rstrip("/"),query,""))

async def resolve_article_url(client, url):
 """Resolve Google News RSS redirects; never return a Google News wrapper as source."""
 host=(urlsplit(url).hostname or "").lower()
 if host not in {"news.google.com", "www.news.google.com"}:
  return clean_url(url)
 try:
  response=await client.get(url,follow_redirects=True,timeout=10)
  final=clean_url(str(response.url))
  final_host=(urlsplit(final).hostname or "").lower()
  if final_host in {"news.google.com","www.news.google.com"}:
   log.warning("Google News link did not resolve to publisher: %s",url[:180])
   return None
  if response.status_code >= 400:
   log.warning("Article redirect returned HTTP %s: %s",response.status_code,url[:180])
   return None
  return final
 except (httpx.HTTPError,ValueError) as exc:
  log.warning("Could not resolve Google News article link: %s",exc)
  return None
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
 is_google_news=(urlsplit(url).hostname or "").lower()=="news.google.com"
 semaphore=asyncio.Semaphore(8)
 async def resolve(link):
  async with semaphore:
   return await resolve_article_url(client,link)
 entries=parsed.entries[:35]
 raw_links=[e.get("link","") for e in entries]
 resolved=await asyncio.gather(*(resolve(link) if is_google_news else asyncio.sleep(0,result=clean_url(link))
                                 for link in raw_links),return_exceptions=True)
 for e,resolved_url in zip(entries,resolved):
  if isinstance(resolved_url,Exception) or not resolved_url: continue
  url=resolved_url; title=strip(e.get("title",""))
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
