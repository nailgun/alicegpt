# AliceGPT agent

Локальный MVP-мост: Яндекс Диалоги вызывает webhook, а агент передаёт фразу в
авторизованный локальный `codex app-server`. Агент слушает только `127.0.0.1`;
наружу его следует публиковать исключительно через managed HTTPS tunnel.

## Запуск

Нужны Python 3.9+ и авторизованный `codex` CLI. Секреты не добавляйте в Git:

```bash
export ALICEGPT_HASH_SALT='длинная-случайная-строка'
export ALICEGPT_WEBHOOK_SECRET='другой-длинный-секрет' # необязательно, но рекомендуется
python3 -m alicegpt_agent.main
```

Webhook: `POST http://127.0.0.1:8080/alice/webhook`, health: `GET /health`.
Для Яндекс Диалогов используйте непубличный путь: например,
`ALICEGPT_WEBHOOK_PATH=/alice/webhook-<длинная-случайная-строка>`. Настройте
tunnel на `http://127.0.0.1:8080`; в Яндекс Диалогах укажите HTTPS URL tunnel
с этим путём. При включённом секрете передавайте заголовок
`X-AliceGPT-Secret` только из собственного proxy: стандартный webhook Яндекса
этот заголовок не добавляет.

Переменные: `ALICEGPT_PORT`, `ALICEGPT_DATABASE`, `ALICEGPT_CWD`,
`ALICEGPT_CODEX`, `ALICEGPT_MODEL`, `ALICEGPT_EFFORT`,
`ALICEGPT_WEBHOOK_TIMEOUT`, `ALICEGPT_CODEX_TIMEOUT`, `ALICEGPT_WEBHOOK_PATH`,
`ALICEGPT_SESSION_IDLE_SECONDS` (по умолчанию 3600), `ALICEGPT_CWD`
(по умолчанию `/private/tmp`, чтобы не привязывать thread к проекту) и
`ALICEGPT_QUICK_ACK_SECONDS` (по умолчанию 3), `ALICEGPT_EPHEMERAL`
(по умолчанию `false`).
`CODEX_TIMEOUT` должен
быть не меньше webhook deadline. Состояние хранит только хэши маршрутов,
opaque ID ephemeral-thread до перезапуска и краткий кеш дедупликации.

Проверьте базовый контракт:

```bash
python3 -m unittest discover -s tests -v
```

Все запросы продолжают одну локальную сессию Codex. После часа без сообщений
следующий запрос начинает новый разговор. Команды «новый разговор» и «удали
историю» сбрасывают локальную привязку разговора. Persistent-режим и
подтверждение подлинности со стороны конкретной
конфигурации Яндекс Диалогов намеренно не включены: webhook secret — лишь
дополнительный барьер для собственного proxy, а схему подписи надо сверить с
выбранным типом навыка.

Если turn не завершился за 3 секунды, agent отвечает «Задача принята. Спроси
меня через несколько секунд.» и продолжает работу в фоне. «Ну что?», «Что
там?», «Готово?» и похожие короткие вопросы возвращают готовый pending-ответ,
не создавая второй turn.

Контракт входящего webhook сверён с актуальной [документацией Яндекс
Диалогов](https://yandex.ru/dev/dialogs/alice/doc/ru/request): используется
верхнеуровневая версия `1.0`, `session.session_id`, числовой `message_id` и
`request.original_utterance`. Проверочный запрос `ping` тоже обрабатывается
как обычная короткая реплика.
