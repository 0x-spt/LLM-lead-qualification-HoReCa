"""Очистка названий заведений (01.10.2026): имя аккаунта заведения в Instagram — с SEO-хвостами."""
import pytest

from leadgen.qualify.names import clean_venue_name

CASES = [
    ('Ресторан | Гостица "Сударь"', 'Сударь'),
    ('Рестораны il FORNO в Москве:', 'il FORNO'),
    ('Chowara | Ресторан Азиатской Кухни | Уфа', 'Chowara'),
    ('ATLANTICA Bistro | Морское бистро', 'ATLANTICA Bistro'),
    ('Ресторан UCHIWA', 'UCHIWA'),
    ('БАЛТ • Печь • Сыроварня • Лавка', 'БАЛТ'),
    ('Ресторан «Хозяин тайги»', 'Хозяин тайги'),
    ('Fiorentina — итальянский ресторан в Нижнем Новгороде', 'Fiorentina'),
    ('🍣 SUSHI LAB 🍣', 'SUSHI LAB'),
    ('Simferocafe ecohome', 'Simferocafe ecohome'),
    ('0.75 please', '0.75 please'),
    ('Хозяин Тайги', 'Хозяин Тайги'),
    ('DoubleTree by Hilton Novosibirsk', 'DoubleTree by Hilton Novosibirsk'),
    ('Пальмовая ветвь рестобизнеса', 'Пальмовая ветвь рестобизнеса'),
    ('Ресторан', 'Ресторан'),
    ('', ''),
    # холостой прогон по базе 01.10.2026
    ('Мидийные Личности', 'Мидийные Личности'),
    ('Видовой ресторан La Vue', 'Видовой ресторан La Vue'),
    ('fjord1_sushibar', 'fjord1_sushibar'),
    ('Ресторан сенсорной кухни | Leth', 'Leth'),
    ('РЕСТОРАНЫ РАППОПОРТА', 'РЕСТОРАНЫ РАППОПОРТА'),
    ('Ресторан 4КУХНИ Казань', '4КУХНИ'),
    ('Ресторан Пайтити Ялта', 'Пайтити'),
    ('Бистро Хинкали и Эклеры   Ростов-на-Дону', 'Бистро Хинкали и Эклеры'),
    ('Лепота. Ресторан с видом.', 'Лепота'),
    ('EM Restaurant [est. 2012]', 'EM Restaurant'),
    ('SEA, SIGNORA', 'SEA, SIGNORA'),
    ('РЕСТОРАН ДЮЖИНА', 'ДЮЖИНА'),
    ('Ресторан грузинской кухни «Мамули»', 'Мамули'),
    ('ZAZAZU | WINE BAR | RESTAURANT |', 'ZAZAZU'),
    ('Grand Synthetic Hotel Palace St. Petersburg', 'Grand Synthetic Hotel Palace St. Petersburg'),
    ('Усадьба •Родное гнездо•', 'Родное гнездо'),
]


# Сверка с таблицей 01.10.2026 — доработка очистки отложена заказчиком до новых источников лидов.
PENDING = [
    ('Morricone / Marco Polo', 'Morricone / Marco Polo'),
    ('15/86', '15/86'),
    ('Cafe Tree', 'Cafe Tree'),
    ('Restaurant by Deep Fried Friends', 'Restaurant by Deep Fried Friends'),
    ('ГАСТРОБАР Guests in The City', 'Guests in The City'),
    ('Ресторан Честных Цен в Самаре', 'Ресторан Честных Цен'),
    ('Novotel Красноярск', 'Novotel Красноярск'),
    ('СЭТ Краснодар   кофе • кухня • десерты', 'СЭТ'),
    ('Мидии Сочи   Морской ресторан', 'Мидии'),
    ('Сеть ресторанов AYAMI | Азиатская кухня', 'AYAMI'),
    ('Gastrobar n°17', 'Gastrobar n°17'),
    ('Магадан (рыбный ресторан)', 'Магадан'),
    ('Ресторан «РИВЬЕРА» 9:00-00:00', 'РИВЬЕРА'),
]


@pytest.mark.xfail(strict=False, reason='доработка очистки отложена (01.10.2026)')
@pytest.mark.parametrize('raw, clean', PENDING)
def test_clean_venue_name_pending(raw, clean):
    assert clean_venue_name(raw) == clean


@pytest.mark.parametrize('raw, clean', CASES)
def test_clean_venue_name(raw, clean):
    assert clean_venue_name(raw) == clean


def test_export_row_uses_clean_venue_name():
    from datetime import date
    from leadgen.ops import sheet
    row = sheet.row_for({'nick': 'x', 'venue': 'Chowara | Ресторан Азиатской Кухни | Уфа'}, date(2026, 10, 1))
    assert 'Chowara' in row and not any('Азиатской' in str(c) for c in row)
