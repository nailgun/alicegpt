# Alice → ChatGPT Work: Slack round-trip PoC

Минимальный, официальный по транспорту PoC для маршрута «голосовой запрос Алисы → event-triggered ChatGPT Work → ответ в Slack thread → gateway». Он не создаёт Slack-ресурсы сам и не содержит токенов.

> Важно: триггер event-triggered Work создаётся в **ChatGPT web/mobile**. В desktop/CLI это не настраивается. Для Plus/Pro нельзя рассчитывать на custom MCP write callback, поэтому он не является частью этого PoC. Slack — основной канал; Gmail — лишь отдельный fallback-кандидат с худшими UX и latency; GitHub здесь не используется.

## Контракт и архитектура

```text
Alice / ручной CLI
       │ [REQ:<uuid>]\n<запрос>
       ▼
Python gateway ──chat.postMessage──► #alice-gateway-poc
       ▲                                  │ top-level Slack event
       │ Socket Mode                       ▼
       └────── thread reply ◄── official Slack action ◄── ChatGPT Work
```

`poc.py` создаёт top-level сообщение ровно в таком виде:

```text
[REQ:550e8400-e29b-41d4-a716-446655440000]
Какая сегодня погода в Москве?
```

Он хранит незавершённые запросы в памяти по `request_id` и `thread_ts`, ждёт только reply в thread исходного сообщения с тем же идентификатором и пишет JSON-логи `SEND`, `REPLY`, `UNKNOWN_REPLY`, `IGNORED_EVENT`, `TIMEOUT`. Top-level сообщения и события самого gateway-бота ответом не считаются. `latency_ms` измеряется с момента перед `chat.postMessage` до принятия совпавшего thread reply.

## Ручная настройка Slack (не выполняется этим проектом)

1. Откройте **Slack API → Your Apps → Create New App → From scratch** и назовите приложение `Alice Gateway POC`.
2. Включите **Socket Mode**.
3. Создайте **App-Level Token** со scope `connections:write`; сохраните его как `xapp-...`.
4. В **OAuth & Permissions → Bot Token Scopes** добавьте `chat:write`, `channels:history`, `channels:read`.
5. В **Event Subscriptions** включите **Enable Events** и добавьте bot event `message.channels`.
6. Нажмите **Install App to Workspace** и сохраните Bot User OAuth Token `xoxb-...`.
7. Создайте публичный канал `#alice-gateway-poc`; добавьте в него `@Alice Gateway POC` и `@ChatGPT`.

После изменения scopes или событий обычно требуется переустановить приложение. В `SLACK_CHANNEL_ID` нужен именно ID канала (`C…`), а не его имя.

## Work task и event trigger

В ChatGPT **web/mobile** создайте Work task с event trigger для новых сообщений `#alice-gateway-poc` (для Slack в отслеживаемом канале должен присутствовать `@ChatGPT`). Вставьте следующий prompt без изменения контрактных частей:

```text
Триггер: новое сообщение в #alice-gateway-poc. Обрабатывай только top-level сообщения, начинающиеся с [REQ:. Текст запроса — всё после первой строки. Сформулируй полезный ответ на русском языке. Финальный ответ отправь через официальный Slack action как reply в thread исходного сообщения, а не top-level постом. Начни ответ с точно того же [REQ:<id>]. Не превышай 3000 символов. Не публикуй промежуточные сообщения. Если запрос пуст или формат невалиден, всё равно ответь в том же thread с тем же REQ и краткой диагностикой.
```

Проверьте, что Work действительно имеет Slack action для публикации **reply в thread**, а не только чтение. Если интерфейс предлагает настройки подтверждений, для теста проверьте `Always allow` / `Allow low-risk actions`; точные названия и доступность зависят от аккаунта и политики workspace.

## Запуск

Нужен Python 3.10+.

```bash
cd /Users/nailgun/src/alicegpt
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SLACK_BOT_TOKEN='xoxb-...'
export SLACK_APP_TOKEN='xapp-...'
export SLACK_CHANNEL_ID='C0123456789'
python poc.py send 'Объясни в двух предложениях, что такое Socket Mode.'
```

`send` подключает Socket Mode, отправляет один запрос и ожидает совпавший thread reply до 90 секунд. Для ручного наблюдения за событиями:

```bash
python poc.py listen
```

Настраиваемый таймаут: `python poc.py send --timeout 120 'Текст запроса'`. Помощь: `python poc.py --help`.

Никогда не добавляйте `.env` с реальными секретами в репозиторий. Файл [.env.example](.env.example) — только шаблон; переменные нужно экспортировать в текущую shell-сессию либо загрузить своим защищённым способом.

## Как оценить результат PoC

**A. Полностью автоматический round-trip.** В терминале появятся `SEND`, затем `REPLY` с `latency_ms`. Запустите несколько разных запросов и сохраните latency/ошибки. После этого можно переходить к интеграции Алисы.

**B. Work остановился на approval.** Проверьте в web/mobile настройку `Always allow` / `Allow low-risk actions` и policy workspace, затем повторите тот же тест. Не считайте ручное подтверждение успешной автоматизацией.

**C. Slack connector не умеет post/reply.** Если Slack action у Work не может создать thread reply, несмотря на `chat:write` у **нашего** Slack bot, это блокер выбранного официального канала. Зафиксируйте доступные действия коннектора, сообщение об ошибке и отсутствие `REPLY`; не маскируйте проблему Gmail fallback-ом.

## Следующий этап: Яндекс Диалоги

1. Webhook Алисы вызывает gateway и передаёт распознанный запрос в Slack как `[REQ:<uuid>]`.
2. Webhook ждёт thread reply не более **4.5 секунд**.
3. Если ответ готов — возвращает его Алисе сразу.
4. Если нет — gateway сохраняет результат, привязанный к диалогу/пользователю, и Алиса просит сказать «ну что?». По этой фразе webhook возвращает готовый отложенный ответ либо сообщает, что он ещё готовится.

Это отдельный этап: текущий PoC хранит pending requests только в памяти и запускается как CLI. Gmail не следует делать основным путём: он допустим лишь как отдельный fallback-кандидат, с заведомо худшими UX/latency характеристиками.

## Границы PoC

- Не создаёт Slack app, channel, токены или Work task.
- Не отправляет секреты в логи; не передавайте секреты в аргументах CLI.
- Корреляция строгая: reply без корректного `[REQ:<uuid>]`, reply не в исходном thread, чужой/старый request и собственные события игнорируются либо логируются как `UNKNOWN_REPLY`.
