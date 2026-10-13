"""Synthetic labelled profiles for a public, reproducible eval.

The real gold set is built from people's Instagram profiles and never leaves the machine
(see build_gold.py). This module generates look-alike profiles from templates instead:
every name, venue and handle is invented. Each case carries the business label
("accept" = a person who currently works in a venue in a target role) and a slice name,
so the report can show where each stage of the cascade wins and loses.

Some slices are deliberately out of reach for the free rules (a consultant who still writes
"Шеф-повар" in the bio, a chef who lost the job last month): they exist to measure what the
LLM stage adds on top of the rules.

    python -m evals.synthetic            # writes evals/data/synthetic.jsonl
"""
import json
import os
import random

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'synthetic.jsonl')
SEED = 20261013
PER_SLICE = 15

CITIES = ['Новосибирск', 'Екатеринбург', 'Красноярск', 'Тюмень', 'Омск', 'Иркутск', 'Владивосток',
          'Казань', 'Самара', 'Краснодар', 'Санкт-Петербург', 'Москва']
VENUES = ['Тайга', 'Берег', 'Северный ветер', 'Огонь и дым', 'Пряности', 'Мята', 'Белый сад', 'Сосны',
          'Кедр', 'Соль', 'Ольха', 'Печь', 'Гавань', 'Полынь', 'Облепиха', 'Утро', 'Брусника', 'Лён']
MALE = ['Алексей', 'Дмитрий', 'Илья', 'Сергей', 'Артём', 'Павел', 'Никита', 'Роман']
FEMALE = ['Мария', 'Анна', 'Екатерина', 'Ольга', 'Наталья', 'Юлия', 'Ирина', 'Дарья']
LAST = ['Белов', 'Климов', 'Соколов', 'Орлов', 'Зайцев', 'Морозов', 'Лебедев', 'Козлов', 'Волков',
        'Новиков', 'Фёдоров', 'Павлов', 'Семёнов', 'Голубев', 'Виноградов', 'Богданов']
FOREIGN = {
    'Украина': (['Київ', 'Одеса', 'Львів'], 'Шеф-кухар ресторану «{v}» {c}', '_ua'),
    'Казахстан': (['Алматы', 'Астана', 'Шымкент'], 'Шеф-повар ресторана «{v}» {c}', '.kz'),
    'Узбекистан': (['Ташкент', 'Самарканд'], 'Шеф-повар @{h} {c}', '.tashkent'),
    'Грузия': (['Тбилиси', 'Батуми'], 'Бренд-шеф ресторана «{v}» 📍{c}', '.ge'),
}
CAPTIONS_WORK = ['Новое сезонное меню уже в ресторане', 'Тартар из оленины с брусникой',
                 'Кухня перед открытием смены', 'Гаспачо из томатов и клубники', 'Утка, вишня, сельдерей',
                 'Сет к ужину шефа в эту пятницу', 'Подача нового десерта']
CAPTIONS_LIFE = ['Выходной на море', 'С друзьями на даче', 'Кот снова спит на ноутбуке', 'Пробежка 10 км']


TRANSLIT = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя ',
                    ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r',
                     's', 't', 'u', 'f', 'h', 'ts', 'ch', 'sh', 'sch', '', 'y', '', 'e', 'yu', 'ya', '_']))


def _slug(text):
    return ''.join(TRANSLIT.get(ch, ch) for ch in text.lower() if ch in TRANSLIT or ch.isalnum())


def _person(r):
    female = r.random() < 0.5
    first, last = r.choice(FEMALE if female else MALE), r.choice(LAST) + ('а' if female else '')
    # the "syn_" prefix keeps generated handles from colliding with real accounts
    nick = 'syn_%s.%s%s' % (_slug(first), _slug(last), r.choice(['', '_chef', '.cook', str(r.randint(1, 99))]))
    return nick, '%s %s' % (first, last)


def _venue(r):
    v = r.choice(VENUES)
    return v, 'syn_' + _slug(v) + r.choice(['_rest', '.restaurant', '_bistro', '.kitchen'])


def _captions(r, work=8, life=2):
    caps = r.sample(CAPTIONS_WORK, min(work, len(CAPTIONS_WORK))) + r.sample(CAPTIONS_LIFE, min(life, 4))
    r.shuffle(caps)
    return caps


# Each slice: (label, role hint for the LLM prompt, builder(r) -> dict with bio/full_name/category/...)
def _chef(r):
    v, h = _venue(r)
    c = r.choice(CITIES)
    bio = r.choice(['Шеф-повар ресторана «{v}» 📍{c}', 'Бренд-шеф @{h} | {c}', 'Executive chef @{h} {c}',
                    'Шеф @{h}\n{c}\nАвторская кухня Сибири']).format(v=v, h=h, c=c)
    return {'bio': bio, 'venue': v}


def _chef_latin(r):
    v, h = _venue(r)
    c = r.choice(['Novosibirsk', 'Ekaterinburg', 'Krasnoyarsk', 'Kazan', 'Moscow'])
    return {'bio': 'Chef @%s | %s' % (h, c), 'venue': v}


def _chef_trained_abroad(r):
    v, h = _venue(r)
    c = r.choice(CITIES)
    return {'bio': 'Шеф-повар @%s, %s. Стажировки в Италии и Франции' % (h, c), 'venue': v}


def _pastry(r):
    v, h = _venue(r)
    return {'bio': 'Шеф-кондитер ресторана «%s» @%s 📍%s' % (v, h, r.choice(CITIES)), 'venue': v}


def _sous_mixed_feed(r):
    v, h = _venue(r)
    return {'bio': 'Шеф-повар @%s ✨ мама двоих детей, люблю горы' % h, 'venue': v,
            'captions': _captions(r, 4, 4)}


def _chef_born_abroad(r):
    v, h = _venue(r)
    return {'bio': 'Шеф-повар @%s, %s. Родом из %s' % (h, r.choice(CITIES), r.choice(['Алматы', 'Тбилиси', 'Минска'])),
            'venue': v}


def _chef_with_staff_training(r):
    v, h = _venue(r)
    return {'bio': 'Бренд-шеф @%s 📍%s. Сезонные ужины шефа, бронь в директ' % (h, r.choice(CITIES)), 'venue': v,
            'captions': _captions(r) + ['Провели обучение команды кухни перед запуском меню']}


def _abroad(country):
    places, template, suffix = FOREIGN[country]

    def build(r):
        v, h = _venue(r)
        return {'bio': template.format(v=v, c=r.choice(places), h=h + suffix), 'venue': v}
    return build


def _ua_flag(r):
    v, h = _venue(r)
    return {'bio': 'Шеф-повар @%s 🇺🇦' % h, 'venue': v}


def _venue_names_chef(r):
    v, h = _venue(r)
    person, _ = _person(r)
    return {'nick': h, 'full_name': 'Ресторан %s' % v, 'category': 'Restaurant',
            'bio': 'Ресторан авторской кухни «%s». Бренд-шеф @%s. Бронь по ссылке ↓' % (v, person)}


def _venue_account(r):
    v, h = _venue(r)
    return {'nick': h, 'full_name': v, 'category': r.choice(['Restaurant', 'Cafe', 'Bar & Grill']),
            'bio': 'Ресторан «%s» 📍%s\nЗавтраки до 12:00 • Бронь +7 900 000-00-00' % (v, r.choice(CITIES))}


def _no_role(r):
    return {'bio': r.choice(['Люблю путешествия и кофе ☕️ {c}', 'Фото, горы, собаки. {c}',
                             'Жизнь в моменте ✨ {c}']).format(c=r.choice(CITIES))}


def _courses(r):
    return {'bio': 'Шеф-повар. Обучаю поваров онлайн — курсы и мастер-классы ↓'}


def _catering(r):
    return {'bio': r.choice(['Шеф-повар | Кейтеринг и выездные банкеты {c}',
                             'Торты на заказ 🎂 {c}. Доставка по городу']).format(c=r.choice(CITIES))}


def _private_chef(r):
    return {'bio': 'Частный шеф-повар. Ужины у вас дома, выезд по %s' % r.choice(CITIES)}


def _former(r):
    v, _ = _venue(r)
    return {'bio': 'Бывший шеф-повар ресторана «%s». Сейчас в поиске новой команды' % v}


def _consultant_no_venue(r):
    return {'bio': 'Шеф-повар, ресторанный консультант. Запуск кухни под ключ, проработка меню. %s'
                   % r.choice(CITIES), 'captions': ['Запустили кухню проекта клиента', 'Аудит кухни в другом городе',
                                                    'Отзыв клиента о проработке меню']}


def _blogger(r):
    return {'bio': 'Шеф-повар по образованию 👨‍🍳 Рецепты для дома каждый день, ссылка на рецепты ↓',
            'captions': ['Рецепт ужина за 15 минут', 'Самый простой пирог', 'Обзор кастрюль для дома',
                         'Готовлю завтрак детям']}


def _travel_chef(r):
    return {'bio': 'Шеф-повар 🌍 гастротуры и выездные ужины в разных городах',
            'captions': ['Ужин в шатре в горах', 'Гастротур по Алтаю', 'Следующий выезд — Байкал']}


SLICES = {
    # label accept — real targets
    'chef': ('accept', 'Шеф-повар', _chef),
    'chef_latin_bio': ('accept', 'Шеф-повар', _chef_latin),
    'chef_trained_abroad': ('accept', 'Шеф-повар', _chef_trained_abroad),
    'pastry_chef': ('accept', 'Шеф-кондитер', _pastry),
    'chef_mixed_feed': ('accept', 'Шеф-повар', _sous_mixed_feed),
    'chef_born_abroad': ('accept', 'Шеф-повар', _chef_born_abroad),
    'chef_bio_booking_cta': ('accept', 'Шеф-повар', _chef_with_staff_training),
    # label reject — caught by free rules (should be)
    'abroad_ukraine': ('reject', 'Шеф-повар', _abroad('Украина')),
    'abroad_ukraine_flag': ('reject', 'Шеф-повар', _ua_flag),
    'abroad_kazakhstan': ('reject', 'Шеф-повар', _abroad('Казахстан')),
    'abroad_uzbekistan': ('reject', 'Шеф-повар', _abroad('Узбекистан')),
    'abroad_georgia': ('reject', 'Шеф-повар', _abroad('Грузия')),
    'venue_names_its_chef': ('reject', 'Шеф-повар', _venue_names_chef),
    'venue_account': ('reject', 'Шеф-повар', _venue_account),
    'no_role': ('reject', None, _no_role),
    'courses_teacher': ('reject', 'Шеф-повар', _courses),
    'catering_home_baking': ('reject', 'Шеф-повар', _catering),
    'private_chef': ('reject', 'Шеф-повар', _private_chef),
    'former_chef': ('reject', 'Шеф-повар', _former),
    # label reject — need judgement over bio + captions (LLM stage)
    'consultant_no_venue': ('reject', 'Шеф-повар', _consultant_no_venue),
    'food_blogger': ('reject', 'Шеф-повар', _blogger),
    'travel_chef': ('reject', 'Шеф-повар', _travel_chef),
}


def generate(per_slice=PER_SLICE, seed=SEED):
    r = random.Random(seed)
    cases, seen = [], set()
    for name, (label, role, build) in SLICES.items():
        made = 0
        while made < per_slice:
            nick, full = _person(r)
            case = {'nick': nick, 'full_name': full, 'category': '', 'venue': None,
                    'captions': _captions(r)}
            case.update(build(r))
            if case['nick'] in seen:
                continue
            seen.add(case['nick'])
            case.update(slice=name, label=label, role=role)
            cases.append(case)
            made += 1
    return cases


def main():
    cases = generate()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + '\n')
    print('%d cases, %d slices -> %s' % (len(cases), len(SLICES), os.path.relpath(OUT)))


if __name__ == '__main__':
    main()
