"""A minimal Telegram Bot API client: long polling plus a handful of methods.

Hand-rolled rather than a library: only seven calls are needed, and tests need a swappable transport."""
import json
import os

import httpx

API = 'https://api.telegram.org'


class TelegramError(Exception):
    pass


def keyboard(rows):
    return {'inline_keyboard': [[{'text': t, 'callback_data': d} for t, d in row] for row in rows]}


class Telegram:
    def __init__(self, token, http_transport=None, timeout=40.0):
        kwargs = {'base_url': '%s/bot%s' % (API, token), 'timeout': timeout}
        if http_transport is not None:
            kwargs['transport'] = http_transport
        self._client = httpx.Client(**kwargs)

    def close(self):
        self._client.close()

    def _call(self, method, payload=None, files=None, params=None):
        try:
            if files:
                r = self._client.post('/' + method, data=payload, files=files)
            elif params is not None:
                r = self._client.get('/' + method, params=params)
            else:
                r = self._client.post('/' + method, json=payload or {})
        except httpx.TransportError as e:
            raise TelegramError('сеть: %s' % e)
        try:
            data = r.json()
        except ValueError:
            raise TelegramError('не JSON: HTTP %d' % r.status_code)
        if not data.get('ok'):
            raise TelegramError(data.get('description') or 'HTTP %d' % r.status_code)
        return data.get('result')

    def get_updates(self, offset=None, timeout=25):
        params = {'timeout': timeout}
        if offset is not None:
            params['offset'] = offset
        return self._call('getUpdates', params=params) or []

    def send_message(self, chat_id, text, reply_markup=None, force_reply=False):
        payload = {'chat_id': chat_id, 'text': text[:4000], 'disable_web_page_preview': True}
        if force_reply:
            payload['reply_markup'] = {'force_reply': True}
        elif reply_markup:
            payload['reply_markup'] = reply_markup
        return self._call('sendMessage', payload)

    def send_photo(self, chat_id, path, caption, reply_markup=None):
        if not path or not os.path.exists(path):
            return self.send_message(chat_id, caption, reply_markup=reply_markup)
        data = {'chat_id': str(chat_id), 'caption': caption[:1000]}
        if reply_markup:
            data['reply_markup'] = json.dumps(reply_markup, ensure_ascii=False)
        with open(path, 'rb') as f:
            return self._call('sendPhoto', data, files={'photo': ('grid.jpg', f, 'image/jpeg')})

    def answer_callback(self, callback_id, text=''):
        self._call('answerCallbackQuery', {'callback_query_id': callback_id, 'text': text[:190]})

    def edit_caption(self, chat_id, message_id, caption):
        self._call('editMessageCaption', {'chat_id': chat_id, 'message_id': message_id,
                                          'caption': caption[:1000],
                                          'reply_markup': {'inline_keyboard': []}})

    def edit_text(self, chat_id, message_id, text):
        self._call('editMessageText', {'chat_id': chat_id, 'message_id': message_id,
                                       'text': text[:4000], 'reply_markup': {'inline_keyboard': []}})

    def delete_message(self, chat_id, message_id):
        try:
            self._call('deleteMessage', {'chat_id': chat_id, 'message_id': message_id})
            return True
        except TelegramError:
            return False
