import logging
from logging.handlers import RotatingFileHandler
from telegram import Update
from telegram.ext import Application
from config import load_settings
from db import Database
from ai import Editor
from bot import register

def main():
 s=load_settings(); s.log_path.parent.mkdir(parents=True,exist_ok=True)
 logging.basicConfig(level=logging.INFO,handlers=[
  logging.StreamHandler(),
  RotatingFileHandler(s.log_path,maxBytes=3_000_000,backupCount=4,encoding="utf-8")],
  format="%(asctime)s %(levelname)s %(name)s: %(message)s")
 db=Database(s.db_path); editor=Editor(s.gemini_key,s.model)
 app=Application.builder().token(s.token).build(); register(app,s,db,editor)
 logging.getLogger(__name__).info("ZenDigest starts; channel=%s schedule=%02d:%02d %s",
  s.channel,s.hour,s.minute,s.timezone)
 app.run_polling(drop_pending_updates=False,allowed_updates=Update.ALL_TYPES)

if __name__=="__main__": main()
