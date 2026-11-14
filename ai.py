import asyncio, json, logging
from google import genai
from google.genai import types
log=logging.getLogger(__name__)

class Editor:
 def __init__(self,key,model): self.client=genai.Client(api_key=key); self.model=model
 def _edit(self,articles,limit):
  data=[{"url":x["url"],"title":x["title"],"description":x.get("description","")[:600],
    "source":x.get("source",""),"date":x["published_at"]} for x in articles]
  prompt=f"""Ты редактор русского дайджеста ZenDigest о кино, сериалах, книгах и музыке.
Выбери не более {limit} наиболее значимых новостей. Не заполняй лимит искусственно.
Приоритет: подтверждённые анонсы, даты релизов, награды, важные культурные события,
крупные релизы. Исключи слухи, рекламу, малозначимые перепечатки и дубли.
Верни только JSON-массив объектов: url (строго из входных данных), category
(Кино/Сериалы/Книги/Музыка), summary (1-2 коротких предложения по-русски),
why (почему новость важна). Используй только подтверждаемую входными данными информацию.
Не выдумывай даты, рейтинги, платформы и факты. Если достойных новостей нет — [].
Входные материалы: {json.dumps(data,ensure_ascii=False)}"""
  response=self.client.models.generate_content(model=self.model,contents=prompt,
   config=types.GenerateContentConfig(temperature=0.2,max_output_tokens=3000,response_mime_type="application/json"))
  parsed=json.loads(response.text or "[]"); allowed={x["url"] for x in articles}; clean=[]
  for x in parsed if isinstance(parsed,list) else []:
   if isinstance(x,dict) and x.get("url") in allowed and x.get("summary") and x.get("why"):
    clean.append({"url":x["url"],"category":str(x.get("category","Культура"))[:30],
     "summary":str(x["summary"])[:650],"why":str(x["why"])[:250]})
  return clean[:limit]
 async def edit(self,articles,limit):
  if not articles:return []
  return await asyncio.to_thread(self._edit,articles,limit)
