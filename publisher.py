import html, logging
from datetime import datetime
from sources import collect
log=logging.getLogger(__name__)

def _news_date(value):
 if not value:
  return "дата не указана"
 try:
  parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  return parsed.strftime("%d.%m.%Y")
 except (TypeError, ValueError):
  return str(value)[:10]

def render(items,date):
 parts=[f"🎬 <b>ZenDigest · {html.escape(date)}</b>",""]
 for i,x in enumerate(items,1):
  parts += [f"<b>{i}. {html.escape(x.get('title_ru') or x['title'])}</b>",f"<i>{html.escape(x['category'])} · Новость: {_news_date(x.get('published_at'))}</i>",
   html.escape(x['summary']),f"💡 {html.escape(x['why'])}",
   f"🔗 <a href=\"{html.escape(x['url'],quote=True)}\">Источник</a>",""]
 parts.append("<i>Кино · Сериалы · Книги · Музыка</i>")
 return "\n".join(parts)

async def run_digest(app,db,editor,settings):
 run_id=db.start()
 try:
  fresh=await collect(settings.fetch_hours); inserted=db.add(fresh); pending=db.pending(settings.fetch_hours)
  if not pending:
   db.finish(run_id,"empty",f"collected={len(fresh)} new={inserted}")
   return {"status":"empty","collected":len(fresh),"new":inserted,"published":0}
  selected=await editor.edit(pending,settings.max_items)
  if not selected:
   db.finish(run_id,"empty","No suitable stories selected by editor")
   return {"status":"empty","collected":len(fresh),"new":inserted,"published":0}
  db.annotate(selected); originals={x["url"]:x for x in pending}
  items=[{**originals[x["url"]],**x} for x in selected]
  text=render(items,datetime.now(settings.timezone).strftime("%d.%m.%Y"))
  while len(text)>3900 and len(items)>1:
   items.pop(); text=render(items,datetime.now(settings.timezone).strftime("%d.%m.%Y"))
  await app.bot.send_message(chat_id=settings.channel,text=text,parse_mode="HTML")
  db.mark([x["url"] for x in items])
  db.finish(run_id,"published",f"collected={len(fresh)} new={inserted} sent={len(items)}")
  return {"status":"published","collected":len(fresh),"new":inserted,"published":len(items)}
 except Exception as e:
  db.finish(run_id,"error",f"{type(e).__name__}: {e}"); log.exception("Digest failed"); raise
