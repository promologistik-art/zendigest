# ZenDigest

Автоматический русскоязычный дайджест по кино, сериалам, книгам и музыке: сбор RSS, дедупликация, редакторский отбор через Gemini, SQLite и ежедневная публикация в Telegram в 08:00 UTC+4.

## Первый запуск
- Python 3.12+
- Установить зависимости из requirements.txt
- Скопировать .env.example в /home/zendigest/.env и заполнить секреты
- Получить Telegram numeric user ID и указать его в TELEGRAM_ADMIN_IDS
- Добавить @privazen_bot администратором @zentestdigest с правом публикации
- Запустить python main.py

## Команды
/start, /help, /status, /run. Команды управления доступны только TELEGRAM_ADMIN_IDS.

## Безопасность и тестирование
Начальный выпуск намеренно зафиксирован на @zentestdigest. @zentgrm не используется. Не коммитьте .env и секреты. Не запускайте более одного polling-процесса с тем же токеном. Служба systemd — отдельная, zendigest.service; другие службы не меняются.

## Источники
RSS-ленты Guardian, BBC, Pitchfork и тематические Google News RSS. Ошибки отдельных лент не останавливают сбор остальных. При сбое Gemini или Telegram выпуск не отмечается опубликованным.
