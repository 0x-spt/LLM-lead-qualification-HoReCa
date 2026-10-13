"""2GIS slugs → city, as used in venues. Ported from db/tools/gis_harvest.py;
slug traps — khabarovsk, yuzhnosakhalinsk (see project memory). Slugs cross-checked 2026-09-29
against 2GIS cards' city_alias: rostov-on-don, togliatti, kh_mansiysk, p_kamchatskiy, etc."""

CITIES = [
    ('novosibirsk', 'Новосибирск'), ('krasnoyarsk', 'Красноярск'),
    ('ekaterinburg', 'Екатеринбург'), ('chelyabinsk', 'Челябинск'),
    ('tyumen', 'Тюмень'), ('omsk', 'Омск'), ('barnaul', 'Барнаул'),
    ('kemerovo', 'Кемерово'), ('novokuznetsk', 'Новокузнецк'),
    ('tomsk', 'Томск'), ('irkutsk', 'Иркутск'), ('ulanude', 'Улан-Удэ'),
    ('chita', 'Чита'), ('yakutsk', 'Якутск'), ('khabarovsk', 'Хабаровск'),
    ('vladivostok', 'Владивосток'), ('yuzhnosakhalinsk', 'Южно-Сахалинск'),
    ('blagoveshchensk', 'Благовещенск'), ('surgut', 'Сургут'),
    ('nizhnevartovsk', 'Нижневартовск'), ('kurgan', 'Курган'),
    ('abakan', 'Абакан'), ('norilsk', 'Норильск'), ('bratsk', 'Братск'),
    ('magadan', 'Магадан'), ('biysk', 'Бийск'),
    # added 2026-09-18 per client decision: "more cities of the same tier"
    ('magnitogorsk', 'Магнитогорск'), ('ntagil', 'Нижний Тагил'),
    ('k_uralskiy', 'Каменск-Уральский'), ('pervouralsk', 'Первоуральск'),
    ('zlatoust', 'Златоуст'), ('miass', 'Миасс'),
    ('kh_mansiysk', 'Ханты-Мансийск'), ('n_urengoy', 'Новый Уренгой'),
    ('noyabrsk', 'Ноябрьск'), ('nefteyugansk', 'Нефтеюганск'),
    ('salekhard', 'Салехард'), ('kogalym', 'Когалым'),
    ('angarsk', 'Ангарск'), ('achinsk', 'Ачинск'), ('kansk', 'Канск'),
    ('minusinsk', 'Минусинск'), ('seversk', 'Северск'),
    ('prokopevsk', 'Прокопьевск'), ('mezhdurechensk', 'Междуреченск'),
    ('leninsk-kuzneckij', 'Ленинск-Кузнецкий'), ('rubtsovsk', 'Рубцовск'),
    ('komsomolsk-on-amur', 'Комсомольск-на-Амуре'), ('nahodka', 'Находка'),
    ('ussuriysk', 'Уссурийск'), ('artem', 'Артём'),
    ('p_kamchatskiy', 'Петропавловск-Камчатский'),
    # "Moscow" territory — added 2026-09-23, list from 06-география.md
    ('moscow', 'Москва'), ('n_novgorod', 'Нижний Новгород'), ('kazan', 'Казань'),
    ('samara', 'Самара'), ('togliatti', 'Тольятти'), ('ufa', 'Уфа'), ('perm', 'Пермь'),
    ('izhevsk', 'Ижевск'), ('ulyanovsk', 'Ульяновск'), ('penza', 'Пенза'),
    ('saratov', 'Саратов'), ('cheboksary', 'Чебоксары'), ('yoshkarola', 'Йошкар-Ола'),
    ('saransk', 'Саранск'), ('kirov', 'Киров'), ('orenburg', 'Оренбург'),
    ('tver', 'Тверь'), ('yaroslavl', 'Ярославль'), ('kostroma', 'Кострома'),
    ('ivanovo', 'Иваново'), ('vladimir', 'Владимир'), ('ryazan', 'Рязань'),
    ('tula', 'Тула'), ('kaluga', 'Калуга'), ('smolensk', 'Смоленск'),
    ('bryansk', 'Брянск'), ('orel', 'Орёл'), ('kursk', 'Курск'),
    ('belgorod', 'Белгород'), ('voronezh', 'Воронеж'), ('lipetsk', 'Липецк'),
    ('tambov', 'Тамбов'), ('rostov-on-don', 'Ростов-на-Дону'), ('krasnodar', 'Краснодар'),
    ('sochi', 'Сочи'), ('gelendzhik', 'Геленджик'), ('anapa', 'Анапа'),
    ('novorossiysk', 'Новороссийск'), ('stavropol', 'Ставрополь'),
    ('pyatigorsk', 'Пятигорск'), ('kislovodsk', 'Кисловодск'),
    ('essentuki', 'Ессентуки'), ('volgograd', 'Волгоград'),
    ('astrakhan', 'Астрахань'), ('makhachkala', 'Махачкала'),
    ('derbent', 'Дербент'), ('vladikavkaz', 'Владикавказ'),
    ('grozny', 'Грозный'), ('nalchik', 'Нальчик'),
    # Northwest — SPB department (client decision 2026-09-28: "all of Russia, Piter is SPB");
    # slugs cross-checked against 2GIS cards' city_alias on 2026-09-29
    ('spb', 'Санкт-Петербург'), ('kaliningrad', 'Калининград'), ('murmansk', 'Мурманск'),
    ('petrozavodsk', 'Петрозаводск'), ('vologda', 'Вологда'), ('arkhangelsk', 'Архангельск'),
    ('v_novgorod', 'Великий Новгород'), ('pskov', 'Псков'), ('syktyvkar', 'Сыктывкар'),
    ('cherepovets', 'Череповец'),
    # geo expansion 2026-10-01: cities with ~90k+ population, Crimea, resort towns; slugs
    # verified via a live 2GIS search (Eysk, Elista, Stary Oskol, Velikiye Luki, Belokurikha
    # have no section of their own)
    ('nabchelny', 'Набережные Челны'),
    ('nizhnekamsk', 'Нижнекамск'),
    ('almetevsk', 'Альметьевск'),
    ('sterlitamak', 'Стерлитамак'),
    ('salavat', 'Салават'),
    ('neftekamsk', 'Нефтекамск'),
    ('syzran', 'Сызрань'),
    ('dimitrovgrad', 'Димитровград'),
    ('engels', 'Энгельс'),
    ('balakovo', 'Балаково'),
    ('orsk', 'Орск'),
    ('berezniki', 'Березники'),
    ('glazov', 'Глазов'),
    ('taganrog', 'Таганрог'),
    ('novocherkassk', 'Новочеркасск'),
    ('shakhty', 'Шахты'),
    ('volgodonsk', 'Волгодонск'),
    ('armavir', 'Армавир'),
    ('tuapse', 'Туапсе'),
    ('maikop', 'Майкоп'),
    ('cherkessk', 'Черкесск'),
    ('nevinnomyssk', 'Невинномысск'),
    ('nazran', 'Назрань'),
    ('kaspiysk', 'Каспийск'),
    ('volzhsky', 'Волжский'),
    ('kamyshin', 'Камышин'),
    ('obninsk', 'Обнинск'),
    ('rybinsk', 'Рыбинск'),
    ('elets', 'Елец'),
    ('dzerzhinsk', 'Дзержинск'),
    ('arzamas', 'Арзамас'),
    ('kovrov', 'Ковров'),
    ('murom', 'Муром'),
    ('novomoskovsk', 'Новомосковск'),
    ('michurinsk', 'Мичуринск'),
    ('severodvinsk', 'Северодвинск'),
    ('uhta', 'Ухта'),
    ('vyborg', 'Выборг'),
    # Northwest, client request 2026-10-06 — 30 cities for the SPB department; slugs
    # verified live (small towns use a hyphen: staraya-russa; Kirovsk is kirovsk-murmansk-region)
    ('gatchina', 'Гатчина'), ('vsevolozhsk', 'Всеволожск'), ('priozersk', 'Приозерск'),
    ('svetlogorsk', 'Светлогорск'), ('zelenogradsk', 'Зеленоградск'), ('sortavala', 'Сортавала'),
    ('staraya-russa', 'Старая Русса'), ('velikie-luki', 'Великие Луки'), ('velikij-ustyug', 'Великий Устюг'),
    ('kirovsk-murmansk-region', 'Кировск'), ('apatity', 'Апатиты'), ('naryan-mar', 'Нарьян-Мар'),
    ('sevastopol', 'Севастополь'),
    ('simferopol', 'Симферополь'),
    ('yalta', 'Ялта'),
    ('evpatoriya', 'Евпатория'),
    ('feodosiya', 'Феодосия'),
    ('kerch', 'Керчь'),
    ('tobolsk', 'Тобольск'),
    ('gornoaltaysk', 'Горно-Алтайск'),
    ('kyzyl', 'Кызыл'),
    ('birobidzhan', 'Биробиджан'),
    ('neryungri', 'Нерюнгри'),
]


# Categories used to exhaust a city beyond the ~72-result cap per query.
#. Bar direction is paused — no bar categories here.
RUBRICS = ['рестораны', 'кафе', 'авторская кухня', 'итальянский ресторан', 'японский ресторан',
           'грузинский ресторан', 'стейк-хаус', 'бистро', 'рестораны при отелях']


# Reserve categories: opened one at a time once other sources are exhausted (leadgen/refill.py)
EXTRA_RUBRICS = ['гастробар', 'винный ресторан', 'рыбный ресторан', 'французский ресторан', 'паназиатский ресторан',
                 'ресторан высокой кухни', 'испанский ресторан', 'мясной ресторан', 'ресторан морепродуктов',
                 'банкетный зал', 'загородный ресторан', 'кондитерская',
                 # 2026-10-12, "push coverage as far as possible": premium-segment cuisines and formats
                 'итальянский ресторан', 'грузинский ресторан', 'японский ресторан', 'азиатский ресторан',
                 'ресторан европейской кухни', 'ресторан русской кухни', 'ресторан кавказской кухни',
                 'средиземноморский ресторан', 'стейк-хаус', 'ресторан авторской кухни', 'бистро',
                 'ресторан при отеле']

# Districts of large cities: each has its own 2GIS result cap (~72), whereas the city as a
# whole gets one cap per category. 2026-10-01: across all of Moscow we had 352 venues, 35 PREMIUM.
DISTRICT_RUBRIC = 'рестораны'
DISTRICTS = {
    'moscow': ['Хамовники', 'Арбат', 'Тверской', 'Пресненский', 'Замоскворечье', 'Якиманка', 'Басманный',
               'Мещанский', 'Красносельский', 'Таганский', 'Дорогомилово', 'Патриаршие пруды', 'Китай-город',
               'Чистые пруды', 'Москва-Сити', 'Остоженка', 'Цветной бульвар', 'Хорошёвский', 'Беговой',
               'Савёловский', 'Сокол', 'Аэропорт', 'Раменки', 'Гагаринский', 'Ломоносовский', 'Крылатское',
               'Кунцево', 'Строгино', 'Щукино', 'Хорошёво-Мнёвники', 'Филёвский парк', 'Останкино', 'Сокольники',
               'Лефортово', 'Измайлово', 'Даниловский', 'Донской', 'Академический', 'Черёмушки', 'Ясенево',
               'Марьина роща', 'Покровское-Стрешнево', 'Тушино', 'Митино', 'Рублёво-Успенское шоссе', 'Барвиха',
               'Новая Москва', 'Коммунарка', 'Красногорск', 'Химки', 'Одинцово', 'Мытищи'],
    'spb': ['Центральный район', 'Адмиралтейский район', 'Петроградский район', 'Василеостровский район',
            'Московский район', 'Приморский район', 'Выборгский район', 'Калининский район', 'Невский район',
            'Фрунзенский район', 'Красногвардейский район', 'Кировский район', 'Красносельский район',
            'Пушкин', 'Петергоф', 'Сестрорецк', 'Кронштадт', 'Зеленогорск', 'Крестовский остров', 'улица Рубинштейна', 'Невский проспект',
            'Новая Голландия'],
    'kazan': ['Вахитовский район', 'Приволжский район', 'Ново-Савиновский район', 'Советский район', 'Кировский район'],
    'ekaterinburg': ['Ленинский район', 'Кировский район', 'Октябрьский район', 'Верх-Исетский район', 'Чкаловский район'],
    'novosibirsk': ['Центральный район', 'Железнодорожный район', 'Заельцовский район', 'Октябрьский район',
                    'Ленинский район'],
    'krasnodar': ['Центральный округ', 'Западный округ', 'Прикубанский округ', 'Карасунский округ'],
    'sochi': ['Адлер', 'Красная Поляна', 'Хоста', 'Лазаревское', 'Центральный район'],
    'n_novgorod': ['Нижегородский район', 'Советский район', 'Канавинский район'],
    'samara': ['Ленинский район', 'Октябрьский район', 'Самарский район'],
    'rostov-on-don': ['Ленинский район', 'Кировский район', 'Пролетарский район'],
}
# 2026-10-12, "expanding even further": resorts, tourist towns, and upscale suburbs — listed as
# districts of the nearest 2GIS section (they have no section of their own, or only a small one)
_MORE_DISTRICTS = {
    'moscow': ['Жуковка', 'Николина Гора', 'Новорижское шоссе', 'Истра', 'Звенигород', 'Подольск', 'Балашиха',
               'Королёв', 'Долгопрудный', 'Сергиев Посад', 'Коломна'],
    'sochi': ['Роза Хутор', 'Эсто-Садок', 'Сириус'],
    'pyatigorsk': ['Кисловодск', 'Ессентуки', 'Железноводск', 'Минеральные Воды'],
    'novorossiysk': ['Абрау-Дюрсо'], 'novokuznetsk': ['Шерегеш'], 'irkutsk': ['Листвянка'],
    'cherkessk': ['Домбай', 'Архыз'], 'yalta': ['Алушта', 'Гурзуф'], 'simferopol': ['Судак'],
    'vladimir': ['Суздаль'], 'ivanovo': ['Плёс'], 'kazan': ['Иннополис', 'Свияжск'],
}
for _slug, _extra in _MORE_DISTRICTS.items():
    DISTRICTS.setdefault(_slug, [])
    DISTRICTS[_slug] += [x for x in _extra if x not in DISTRICTS[_slug]]



# Department — the nearest of three centers (Moscow, Novosibirsk, Petersburg) by straight-line
# distance; the Northwestern federal district is always SPB (client decision 2026-10-01). Under
# this rule, the Ural satellite towns of Yekaterinburg and Chelyabinsk (Nizhny Tagil, Pervouralsk,
# Zlatoust, Miass, Magnitogorsk) fall under MSK.
NORTHWEST = {'Санкт-Петербург', 'Калининград', 'Мурманск', 'Петрозаводск', 'Вологда', 'Архангельск',
             'Великий Новгород', 'Псков', 'Сыктывкар', 'Череповец', 'Северодвинск', 'Ухта', 'Выборг',
             # 2026-10-06: full client list — towns and suburbs of Petersburg
             'Сестрорецк', 'Зеленогорск', 'Пушкин', 'Петергоф', 'Кронштадт', 'Гатчина', 'Всеволожск', 'Приозерск',
             'Светлогорск', 'Зеленоградск', 'Сортавала', 'Старая Русса', 'Великие Луки', 'Великий Устюг',
             'Кировск', 'Апатиты', 'Нарьян-Мар'}
CENTERS = {'МСК': 'Москва', 'НСК': 'Новосибирск', 'СПБ': 'Санкт-Петербург'}
REGIONS = {'МСК': 'Москва', 'НСК': 'Восток', 'СПБ': 'Питер'}


def _department_of(city):
    from leadgen.qualify import geo_points
    if city in NORTHWEST:
        return 'СПБ'
    xy = geo_points.CITY_COORDS[city]
    return min(CENTERS, key=lambda dep: geo_points._km(xy, geo_points.CITY_COORDS[CENTERS[dep]]))


# Computed once at import time: substituting CITIES (in tests) doesn't break it.
DEPARTMENTS = {slug: _department_of(city) for slug, city in CITIES}


def department_of_city(city):
    return 'СПБ' if city in NORTHWEST else _department_of(city)


def department(slug):
    return DEPARTMENTS.get(slug)


def region(slug):
    dep = DEPARTMENTS.get(slug)
    return REGIONS.get(dep) if dep else None
