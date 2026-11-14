import logging
from datetime import time
from telegram import Update
from telegram.ext import Application,CommandHandler,ContextTypes
from publisher import run_digest
log=logging.getLogger(__name__)

def admin(update,settings):
 u=update.effective_user
 return bool(u and u.id in settings.admins)

async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
 if not admin(update,ctx.application.bot_data["settings"]):
  await update.effective_message.reply_text("ZenDigest работает. Команды управления доступны администратору.")
  return
 await update.effective_message.reply_text("ZenDigest запущен.\n/status — состояние\n/run — тестовый выпуск\n/help — помощь")
async def help_cmd(update,ctx): await start(update,ctx)
async def status(update,ctx):
 s=ctx.application.bot_data["settings"]
 if not admin(update,s):return
 d=ctx.application.bot_data["db"].status(); last=d["last"]
 await update.effective_message.reply_text(
  f"📊 ZenDigest\nМатериалов: {d['total']}\nОпубликовано: {d['published']}\nОжидают: {d['pending']}\n"
  f"Последний запуск: {last['started']+' · '+last['status'] if last else 'ещё не запускался'}\nКанал: {s.channel}")
async def run_cmd(update,ctx):
 s=ctx.application.bot_data["settings"]
 if not admin(update,s):return
 msg=await update.effective_message.reply_text("⏳ Собираю материалы и готовлю выпуск…")
 try:
  r=await run_digest(ctx.application,ctx.application.bot_data["db"],ctx.application.bot_data["editor"],s)
  await msg.edit_text(f"Готово: {r['status']}. Собрано: {r['collected']}, новых: {r['new']}, опубликовано: {r['published']}.")
 except Exception as e:
  log.exception("Manual run failed")
  await msg.edit_text(f"Ошибка подготовки выпуска: {type(e).__name__}. Подробности в журнале сервера.")
async def scheduled(ctx):
 app=ctx.application; data=app.bot_data
 try: log.info("Scheduled result: %s",await run_digest(app,data["db"],data["editor"],data["settings"]))
 except Exception: log.exception("Scheduled run failed")

def register(app,settings,db,editor):
 app.bot_data.update(settings=settings,db=db,editor=editor)
 app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("help",help_cmd))
 app.add_handler(CommandHandler("status",status)); app.add_handler(CommandHandler("run",run_cmd))
 app.job_queue.run_daily(scheduled,time=time(settings.hour,settings.minute,tzinfo=settings.timezone),name="daily-digest")
