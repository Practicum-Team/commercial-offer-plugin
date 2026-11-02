# Генератор коммерческих предложений

Проект для подготовки коммерческих предложений по каталогу и прайсу компании. Пользователь описывает, что ему нужно, система помогает подобрать позиции, проверяет правила скидок, считает итоговую стоимость и создаёт редактируемый DOCX.

Подбор и уточнение запроса выполняются с участием языковой модели. Цены, правила, расчёты и создание файла обрабатываются Python-кодом — модель не должна придумывать стоимость услуг.

## Быстрый старт

Нужен Python 3.10+.

```bash
python -m pip install -e ".[dev]"
python -m pytest -o addopts=''
python -m commercial_offer.cli --help
```

Пакет использует `python-docx`, `SQLAlchemy` и `openpyxl`. Команды ниже запускаются из корня репозитория.

## Где что лежит

| Путь | Для чего нужен |
| --- | --- |
| `src/commercial_offer/cli.py` | Команды CLI |
| `src/commercial_offer/importer.py`, `validator.py` | Чтение и проверка данных |
| `src/commercial_offer/storage.py`, `search.py` | SQLite и поиск по каталогу |
| `src/commercial_offer/advanced_quote.py`, `calculator.py`, `rules.py` | Расчёты и скидки |
| `src/commercial_offer/proposal_pipeline.py`, `company_docx.py`, `end_to_end.py` | Подготовка КП и DOCX |
| `profiles/company_comp_service/` | Профиль компьютерного сервиса |
| `profiles/company_copycenter/` | Профиль копицентра |
| `skills/commercial-offer-skill/` | Инструкции Skill |
| `tests/` | Автоматические тесты |
| `evals/` | Клиентские запросы для оценки качества |

В каждом профиле есть каталог `catalog.csv`, прайс `prices.csv`, правила `rules.json`, описание компании `profile.json`, кейсы `cases.md` и шаблон `template.docx`. Формат колонок может отличаться между компаниями: для импорта используется схема сопоставления полей.

## Работа с профилями

Посмотреть зарегистрированные компании, выбрать активную и проверить поля шаблона:

```bash
python -m commercial_offer.cli profiles-list
python -m commercial_offer.cli profiles-use company_comp_service
python -m commercial_offer.cli template-fields
```

У `template-fields` нет аргумента с названием компании: команда берёт активный профиль. Настройки хранятся локально в `profiles/config.json`.

### Загрузка каталога в SQLite

Сначала нужно подготовить `data-schema.json` с именем профиля, файлами и соответствием колонок (`field_map`). Описание формата есть в `skills/commercial-offer-skill/references/data-formats.md`.

Для компьютерного сервиса проверка выглядит так:

```bash
python -m commercial_offer.cli profiles-apply-schema company_comp_service data-schema.json \
  profiles/company_comp_service/catalog.csv \
  profiles/company_comp_service/prices.csv \
  profiles/company_comp_service/rules.json \
  profiles/company_comp_service/cases.md

python -m commercial_offer.cli profiles-validate company_comp_service data-schema.json \
  profiles/company_comp_service/catalog.csv \
  profiles/company_comp_service/prices.csv \
  profiles/company_comp_service/rules.json \
  profiles/company_comp_service/cases.md
```

Если обе проверки прошли, пользователь подтверждает схему, сопоставление полей и загрузку. Флаг `--yes` означает, что такое подтверждение действительно получено.

```bash
python -m commercial_offer.cli profiles-confirm company_comp_service data-schema.json schema --yes
python -m commercial_offer.cli profiles-confirm company_comp_service data-schema.json fields --yes
python -m commercial_offer.cli profiles-confirm company_comp_service data-schema.json commit --yes

python -m commercial_offer.cli profiles-commit company_comp_service data-schema.json \
  profiles/company_comp_service/catalog.csv \
  profiles/company_comp_service/prices.csv \
  profiles/company_comp_service/rules.json \
  profiles/company_comp_service/cases.md \
  --profile-json profiles/company_comp_service/profile.json
```

После импорта база по умолчанию находится в папке профиля под именем `profile.sqlite3`. Базы с рабочими данными не нужно коммитить в репозиторий.

## Поиск товаров и создание КП

Проверить, что позиции доступны на нужную дату:

```bash
python -m commercial_offer.cli search-products company_comp_service \
  --currency RUB --purchase-date 2026-11-02
```

Результат этой команды — **все подходящие кандидаты**, а не готовый заказ. Перед расчётом нужно выбрать нужные товары и указать количества. Если запрос неоднозначен, сначала уточнить детали у клиента.

Команда генерации:

```bash
python -m commercial_offer.cli profiles-use company_comp_service
python -m commercial_offer.cli proposal-prepare search-results.json request-schema.json \
  --output proposal.docx
```

Здесь `search-results.json` содержит **отобранные позиции**, а `request-schema.json` — сведения о запросе. Это не тот же файл, который возвращает `search-products` без обработки. Формат входных данных описан в `skills/commercial-offer-skill/references/workflow.md`.

Если требуются подтверждённые сведения для шаблона, можно передать `--confirmed confirmed.json`. Нельзя вручную подменять рассчитанные цены, скидки, итог и НДС или выдумывать реквизиты клиента.

Для Windows, если при перенаправлении JSON в файл возникнет проблема с кодировкой:

```bash
PYTHONIOENCODING=utf-8 python -m commercial_offer.cli profiles-list > profiles-list.json
```

В актуальном CLI также есть настройка вывода UTF-8.

## Как устроены расчёты

Для денег используется `Decimal`. Расчёт выполняется по данным выбранной компании и её правилам. В ответе важно сохранять связь с исходными ценами и правилами.

Есть два связанных пути работы с данными: поиск использует SQLite, а часть расчётного кода читает файлы профиля. Поэтому при изменении прайса нужно следить, чтобы импортированная база и исходные файлы соответствовали друг другу.

DOCX можно редактировать после создания, но ручные правки суммы не запускают повторный расчёт.

## Проверка проекта

```bash
python -m pytest -o addopts=''
```

На проверенной версии проекта проходили **82 автоматических теста**. Они покрывают отдельные модули и часть интеграционных сценариев. 

Примеры запросов лежат в `evals/comp_service_requests/` и `evals/copycenter_requests/`.

Дополнительные инструкции: `skills/commercial-offer-skill/SKILL.md`, `skills/commercial-offer-skill/references/workflow.md` и `skills/commercial-offer-skill/references/data-formats.md`.
