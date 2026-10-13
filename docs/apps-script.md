# Выгрузка в гугл-таблицу через Apps Script (без Google Cloud)

Когда Google не даёт создать ключ сервисного аккаунта (политика организации),
таблица принимает строки сама — небольшим скриптом внутри неё.

1. Откройте таблицу (для проверки — **копию** «AI ЛИДЫ AMOCRM»). Строки пишутся
   на **первый лист**.
2. Расширения → Apps Script. Удалите пример кода и вставьте содержимое файла
   `secrets/apps-script.gs` (секрет в нём уже прописан; файл не в git).
3. Сохранить (значок дискеты) → **Начать развертывание → Новое развертывание** →
   тип «Веб-приложение»:
   - Выполнять от имени: **Меня**
   - У кого есть доступ: **Все**
   → Развернуть → разрешить доступ к таблицам своим аккаунтом (Google покажет
   предупреждение «приложение не проверено» — «Дополнительно → Перейти»).
4. Скопируйте **URL веб-приложения** (`https://script.google.com/macros/s/…/exec`)
   и отправьте боту: `/sheet <URL>`.
5. Проверка: `/quota МСК 1`, `/export_preview`, `/export_now`.

Доступ «Все» безопасен: без секрета скрипт отвечает `forbidden`. Секрет —
`SHEETS_WEBAPP_SECRET` в `.env`; сменить — поменять в обоих местах и сделать
новое развертывание (Управление развертываниями → Изменить → Новая версия).

Код скрипта (без секрета):

```javascript
const SECRET = 'ВСТАВЬТЕ_СЕКРЕТ';

function sheet_() { return SpreadsheetApp.getActiveSpreadsheet().getSheets()[0]; }

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

// колонка A — для сверки дублей перед выгрузкой
function doGet(e) {
  if (!e || e.parameter.secret !== SECRET) return out_({ok: false, error: 'forbidden'});
  const sh = sheet_(), last = sh.getLastRow();
  const links = last ? sh.getRange(1, 1, last, 1).getValues().map(function (r) { return String(r[0]); }) : [];
  return out_({ok: true, links: links});
}

// строки A–P и жёлтая строка «Завершена обработка за …»
function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (body.secret !== SECRET) return out_({ok: false, error: 'forbidden'});
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const sh = sheet_(), rows = body.rows.concat([body.done]);
    const start = sh.getLastRow() + 1;
    sh.getRange(start, 1, rows.length, 16).setValues(rows);
    sh.getRange(start + rows.length - 1, 1, 1, 16).setBackground('#ffff00');
    return out_({ok: true, added: body.rows.length});
  } finally {
    lock.releaseLock();
  }
}
```
