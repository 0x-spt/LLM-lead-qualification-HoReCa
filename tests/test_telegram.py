import json

import httpx
import pytest

from leadgen.ops import telegram


def client(handler):
    return telegram.Telegram('TOKEN', http_transport=httpx.MockTransport(handler))


def ok(result):
    return httpx.Response(200, json={'ok': True, 'result': result})


def test_get_updates_passes_offset_and_timeout():
    def handler(request):
        assert request.url.path == '/botTOKEN/getUpdates'
        assert request.url.params['offset'] == '10' and request.url.params['timeout'] == '25'
        return ok([{'update_id': 10}])
    assert client(handler).get_updates(offset=10) == [{'update_id': 10}]


def test_send_message_with_keyboard():
    def handler(request):
        body = json.loads(request.content)
        assert body['chat_id'] == 1 and body['text'] == 'hi'
        assert body['reply_markup']['inline_keyboard'][0][0] == {'text': 'Берём', 'callback_data': 'take:a'}
        return ok({'message_id': 5})
    kb = telegram.keyboard([[('Берём', 'take:a')]])
    assert client(handler).send_message(1, 'hi', reply_markup=kb)['message_id'] == 5


def test_force_reply():
    def handler(request):
        assert json.loads(request.content)['reply_markup'] == {'force_reply': True}
        return ok({'message_id': 6})
    client(handler).send_message(1, 'why?', force_reply=True)


def test_send_photo_multipart(tmp_path):
    img = tmp_path / 'g.jpg'
    img.write_bytes(b'JPEG')

    def handler(request):
        assert request.url.path == '/botTOKEN/sendPhoto'
        assert b'JPEG' in request.content and 'multipart/form-data' in request.headers['content-type']
        return ok({'message_id': 7})
    assert client(handler).send_photo(1, str(img), 'cap')['message_id'] == 7


def test_send_photo_without_file_falls_back_to_text(tmp_path):
    def handler(request):
        assert request.url.path == '/botTOKEN/sendMessage'
        return ok({'message_id': 8})
    assert client(handler).send_photo(1, str(tmp_path / 'none.jpg'), 'cap')['message_id'] == 8


def test_api_error_raises():
    handler = lambda r: httpx.Response(400, json={'ok': False, 'description': 'Bad Request'})
    with pytest.raises(telegram.TelegramError):
        client(handler).send_message(1, 'x')


def test_network_error_raises_telegram_error():
    def handler(request):
        raise httpx.ConnectError('down')
    with pytest.raises(telegram.TelegramError):
        client(handler).get_updates()


def test_delete_message_ignores_errors():
    client(lambda r: httpx.Response(400, json={'ok': False})).delete_message(1, 2)


def test_delete_message_reports_success():
    assert client(lambda r: ok(True)).delete_message(1, 2) is True
    assert client(lambda r: httpx.Response(400, json={'ok': False})).delete_message(1, 2) is False
