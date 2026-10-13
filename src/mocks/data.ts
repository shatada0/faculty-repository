// Static reference data for the mock seed. All people, journals and conferences are fictional.
import type { ConferenceLevel, DegreeLevel, Indexing, Subject, SubjectScheme } from '@/api/types'

export interface Dept {
  name: string
  short: string
  directions: { code: string; name: string; level: DegreeLevel; profile: string }[]
  disciplines: string[]
  methods: { nom: string; gen: string; en: string; kw: string }[]
  tasks: { gen: string; en: string; kw: string }[]
}

export const DEPTS: Dept[] = [
  {
    name: 'Кафедра информационных систем',
    short: 'ИС',
    directions: [
      { code: '09.03.02', name: 'Информационные системы и технологии', level: 'bachelor', profile: 'Информационные системы и технологии в образовании' },
      { code: '09.04.02', name: 'Информационные системы и технологии', level: 'master', profile: 'Интеллектуальные информационные системы' },
      { code: '10.05.01', name: 'Компьютерная безопасность', level: 'specialist', profile: 'Анализ безопасности компьютерных систем' },
    ],
    disciplines: ['Базы данных', 'Проектирование информационных систем', 'Машинное обучение'],
    methods: [
      { nom: 'Трансформерные языковые модели', gen: 'трансформерных языковых моделей', en: 'transformer language models', kw: 'языковые модели' },
      { nom: 'Графовые нейронные сети', gen: 'графовых нейронных сетей', en: 'graph neural networks', kw: 'графовые нейронные сети' },
      { nom: 'Градиентный бустинг', gen: 'градиентного бустинга', en: 'gradient boosting', kw: 'градиентный бустинг' },
      { nom: 'Тематическое моделирование', gen: 'тематического моделирования', en: 'topic modeling', kw: 'тематическое моделирование' },
      { nom: 'Онтологический подход', gen: 'онтологического подхода', en: 'ontology-based methods', kw: 'онтологии' },
      { nom: 'Векторные представления текстов', gen: 'векторных представлений текстов', en: 'text embeddings', kw: 'векторные представления' },
      { nom: 'Извлечение именованных сущностей', gen: 'извлечения именованных сущностей', en: 'named entity recognition', kw: 'извлечение сущностей' },
      { nom: 'Кластеризация документов', gen: 'кластеризации документов', en: 'document clustering', kw: 'кластеризация' },
    ],
    tasks: [
      { gen: 'классификации научных текстов', en: 'scientific text classification', kw: 'классификация текстов' },
      { gen: 'поиска дубликатов в библиографических базах', en: 'duplicate detection in bibliographic databases', kw: 'библиографические базы' },
      { gen: 'прогнозирования успеваемости студентов', en: 'student performance prediction', kw: 'успеваемость' },
      { gen: 'автоматического реферирования документов', en: 'automatic document summarization', kw: 'реферирование' },
      { gen: 'анализа отзывов об образовательных программах', en: 'analysis of educational program reviews', kw: 'анализ отзывов' },
      { gen: 'извлечения метаданных из выпускных работ', en: 'metadata extraction from theses', kw: 'метаданные' },
      { gen: 'рекомендации научных публикаций', en: 'scientific paper recommendation', kw: 'рекомендательные системы' },
      { gen: 'мониторинга цифрового следа обучающихся', en: 'learner digital footprint monitoring', kw: 'цифровой след' },
    ],
  },
  {
    name: 'Кафедра прикладной математики',
    short: 'ПМ',
    directions: [
      { code: '01.03.02', name: 'Прикладная математика и информатика', level: 'bachelor', profile: 'Математическое моделирование' },
      { code: '01.04.02', name: 'Прикладная математика и информатика', level: 'master', profile: 'Методы оптимизации и анализ данных' },
    ],
    disciplines: ['Методы оптимизации', 'Численные методы', 'Теория вероятностей и математическая статистика'],
    methods: [
      { nom: 'Метод ветвей и границ', gen: 'метода ветвей и границ', en: 'branch and bound', kw: 'метод ветвей и границ' },
      { nom: 'Эволюционные алгоритмы', gen: 'эволюционных алгоритмов', en: 'evolutionary algorithms', kw: 'эволюционные алгоритмы' },
      { nom: 'Метод конечных элементов', gen: 'метода конечных элементов', en: 'finite element method', kw: 'метод конечных элементов' },
      { nom: 'Байесовский вывод', gen: 'байесовского вывода', en: 'Bayesian inference', kw: 'байесовский вывод' },
      { nom: 'Стохастические дифференциальные уравнения', gen: 'стохастических дифференциальных уравнений', en: 'stochastic differential equations', kw: 'стохастические уравнения' },
      { nom: 'Метод Монте-Карло', gen: 'метода Монте-Карло', en: 'Monte Carlo methods', kw: 'метод Монте-Карло' },
      { nom: 'Нечёткая логика', gen: 'нечёткой логики', en: 'fuzzy logic', kw: 'нечёткая логика' },
      { nom: 'Марковские модели', gen: 'марковских моделей', en: 'Markov models', kw: 'марковские модели' },
    ],
    tasks: [
      { gen: 'оптимизации расписания учебных занятий', en: 'university timetable optimization', kw: 'составление расписания' },
      { gen: 'моделирования теплопереноса в многослойных средах', en: 'heat transfer modeling in layered media', kw: 'теплоперенос' },
      { gen: 'оценки параметров систем массового обслуживания', en: 'parameter estimation for queueing systems', kw: 'системы массового обслуживания' },
      { gen: 'маршрутизации транспорта', en: 'vehicle routing', kw: 'маршрутизация' },
      { gen: 'прогнозирования временных рядов', en: 'time series forecasting', kw: 'временные ряды' },
      { gen: 'анализа устойчивости динамических систем', en: 'stability analysis of dynamical systems', kw: 'устойчивость' },
      { gen: 'оптимального распределения ресурсов', en: 'optimal resource allocation', kw: 'распределение ресурсов' },
      { gen: 'моделирования распространения эпидемий', en: 'epidemic spread modeling', kw: 'эпидемиологические модели' },
    ],
  },
  {
    name: 'Кафедра программной инженерии',
    short: 'ПИ',
    directions: [
      { code: '09.03.04', name: 'Программная инженерия', level: 'bachelor', profile: 'Разработка программно-информационных систем' },
      { code: '09.03.01', name: 'Информатика и вычислительная техника', level: 'bachelor', profile: 'Программное обеспечение средств вычислительной техники' },
      { code: '09.04.01', name: 'Информатика и вычислительная техника', level: 'master', profile: 'Технологии разработки программного обеспечения' },
    ],
    disciplines: ['Технологии программирования', 'Объектно-ориентированное программирование', 'Тестирование программного обеспечения'],
    methods: [
      { nom: 'Микросервисная архитектура', gen: 'микросервисной архитектуры', en: 'microservice architecture', kw: 'микросервисы' },
      { nom: 'Статический анализ кода', gen: 'статического анализа кода', en: 'static code analysis', kw: 'статический анализ' },
      { nom: 'Мутационное тестирование', gen: 'мутационного тестирования', en: 'mutation testing', kw: 'мутационное тестирование' },
      { nom: 'Контейнеризация', gen: 'контейнеризации', en: 'containerization', kw: 'контейнеризация' },
      { nom: 'Предметно-ориентированное проектирование', gen: 'предметно-ориентированного проектирования', en: 'domain-driven design', kw: 'DDD' },
      { nom: 'Большие языковые модели', gen: 'больших языковых моделей', en: 'large language models', kw: 'большие языковые модели' },
      { nom: 'Непрерывная интеграция', gen: 'непрерывной интеграции', en: 'continuous integration', kw: 'CI/CD' },
      { nom: 'Формальная верификация', gen: 'формальной верификации', en: 'formal verification', kw: 'формальная верификация' },
    ],
    tasks: [
      { gen: 'автоматизации тестирования веб-приложений', en: 'web application test automation', kw: 'автоматизация тестирования' },
      { gen: 'оценки технического долга', en: 'technical debt assessment', kw: 'технический долг' },
      { gen: 'генерации модульных тестов', en: 'unit test generation', kw: 'модульные тесты' },
      { gen: 'управления конфигурацией распределённых систем', en: 'configuration management in distributed systems', kw: 'распределённые системы' },
      { gen: 'обнаружения уязвимостей в исходном коде', en: 'vulnerability detection in source code', kw: 'уязвимости' },
      { gen: 'сопровождения унаследованных систем', en: 'legacy system maintenance', kw: 'унаследованные системы' },
      { gen: 'мониторинга производительности сервисов', en: 'service performance monitoring', kw: 'мониторинг' },
      { gen: 'проектирования образовательных платформ', en: 'educational platform design', kw: 'образовательные платформы' },
    ],
  },
]

export const SYSTEMS = ['веб-сервиса', 'мобильного приложения', 'информационной системы', 'программного модуля', 'чат-бота', 'программного комплекса', 'системы поддержки принятия решений']

// [fullName, dept index, position, degree, title, orcid]
export const STAFF: [string, number, string, string?, string?, string?][] = [
  ['Орлов Дмитрий Сергеевич', 0, 'заведующий кафедрой ИС', 'доктор технических наук', 'профессор', '0000-0002-4417-3301'],
  ['Белова Наталья Викторовна', 0, 'доцент кафедры ИС', 'кандидат технических наук', 'доцент', '0000-0001-8820-5512'],
  ['Кузнецов Андрей Павлович', 0, 'доцент кафедры ИС', 'кандидат физико-математических наук'],
  ['Морозова Елена Игоревна', 0, 'старший преподаватель кафедры ИС'],
  ['Захаров Илья Олегович', 0, 'доцент кафедры ИС', 'кандидат технических наук', undefined, '0000-0003-1290-7745'],
  ['Соколов Виктор Александрович', 1, 'заведующий кафедрой ПМ', 'доктор физико-математических наук', 'профессор', '0000-0002-9031-1180'],
  ['Лебедева Ольга Николаевна', 1, 'профессор кафедры ПМ', 'доктор физико-математических наук', 'доцент'],
  ['Григорьев Павел Евгеньевич', 1, 'доцент кафедры ПМ', 'кандидат физико-математических наук', 'доцент', '0000-0001-6623-4098'],
  ['Тихонова Мария Андреевна', 1, 'доцент кафедры ПМ', 'кандидат физико-математических наук'],
  ['Фролов Сергей Владимирович', 1, 'старший преподаватель кафедры ПМ'],
  ['Волков Алексей Николаевич', 2, 'заведующий кафедрой ПИ', 'кандидат технических наук', 'доцент', '0000-0002-7754-0016'],
  ['Никитина Анна Сергеевна', 2, 'доцент кафедры ПИ', 'кандидат технических наук'],
  ['Степанов Роман Игоревич', 2, 'доцент кафедры ПИ', 'кандидат технических наук', 'доцент', '0000-0003-5512-8839'],
  ['Ершова Татьяна Михайловна', 2, 'старший преподаватель кафедры ПИ'],
  ['Карпов Михаил Юрьевич', 2, 'ассистент кафедры ПИ'],
]

export const EXTERNAL: [string, string][] = [
  ['Миронов Константин Петрович', 'МГТУ им. Н. Э. Баумана'],
  ['Савельева Ирина Александровна', 'Санкт-Петербургский государственный университет'],
  ['Ефимов Георгий Леонидович', 'Новосибирский государственный университет'],
  ['Абрамова Людмила Сергеевна', 'Казанский федеральный университет'],
  ['Денисов Вадим Николаевич', 'Институт проблем управления РАН'],
  ['Широкова Елена Валерьевна', 'Томский политехнический университет'],
  ['Пономарёв Олег Игоревич', 'Уральский федеральный университет'],
  ['Рябова Светлана Дмитриевна', 'ФИЦ «Информатика и управление» РАН'],
  ['Чернов Артур Ринатович', 'Самарский государственный технический университет'],
  ['Гарипов Тимур Ильдарович', 'Казанский национальный исследовательский технический университет'],
  ['Лаптева Вера Олеговна', 'Иркутский государственный университет'],
  ['Жданов Евгений Борисович', 'ООО «Цифровые решения»'],
  ['Королёва Алина Викторовна', 'Высшая школа экономики'],
  ['Устинов Николай Сергеевич', 'Пермский государственный университет'],
  ['Яшин Валентин Аркадьевич', 'АО «НИИ информационных технологий»'],
  ['Кравченко Юлия Павловна', 'Белорусский государственный университет'],
  ['Сафонов Денис Андреевич', 'Нижегородский государственный университет'],
  ['Мартынова Оксана Юрьевна', 'Саратовский государственный университет'],
  ['Борисов Глеб Константинович', 'Воронежский государственный университет'],
  ['Ахметова Диана Маратовна', 'Уфимский университет науки и технологий'],
  ['Wei Li', 'Harbin Institute of Technology'],
  ['Jan Novák', 'Czech Technical University in Prague'],
  ['Maria Kowalska', 'Warsaw University of Technology'],
  ['Нестеров Игорь Владимирович', 'АО «Системы управления»'],
  ['Тимофеева Галина Петровна', 'Самарский государственный экономический университет'],
]

export const MALE = ['Александр', 'Артём', 'Максим', 'Даниил', 'Кирилл', 'Никита', 'Иван', 'Егор', 'Михаил', 'Матвей', 'Тимофей', 'Илья', 'Денис', 'Глеб', 'Владислав']
export const FEMALE = ['Анастасия', 'Мария', 'Дарья', 'Полина', 'Екатерина', 'Софья', 'Алиса', 'Виктория', 'Ксения', 'Валерия', 'Елизавета', 'Ульяна']
export const SURNAMES = ['Смирнов', 'Попов', 'Васильев', 'Петров', 'Новиков', 'Фёдоров', 'Михайлов', 'Павлов', 'Семёнов', 'Голубев', 'Виноградов', 'Богданов', 'Воробьёв', 'Комаров', 'Киселёв', 'Макаров', 'Андреев', 'Ковалёв', 'Ильин', 'Гусев', 'Титов', 'Кудрявцев', 'Баранов', 'Куликов', 'Алексеев', 'Яковлев', 'Сорокин', 'Романов', 'Медведев', 'Жуков', 'Калинин', 'Антонов', 'Тарасов', 'Беляев', 'Лукин', 'Гаврилов', 'Мельников', 'Щербаков', 'Блинов', 'Колесников']
export const PATRONYMICS = ['Александрович', 'Сергеевич', 'Дмитриевич', 'Андреевич', 'Алексеевич', 'Игоревич', 'Олегович', 'Викторович', 'Евгеньевич', 'Павлович', 'Михайлович', 'Романович']

// `lists`: authority lists the journal is in (keys of JOURNAL_LISTS); the real backend keeps them in a journal directory by ISSN.
export interface Journal { name: string; issn: string; indexing: Indexing[]; lists: string[]; en?: boolean; depts: number[] }

export const JOURNALS: Journal[] = [
  { name: 'Вестник университета. Серия: Информатика и управление', issn: '2410-7421', indexing: ['vak', 'rinc'], lists: ['white4', 'vak_k3'], depts: [0, 1, 2] },
  { name: 'Программные системы и вычислительные методы', issn: '2305-6061', indexing: ['vak', 'rinc'], lists: ['white3', 'vak_k2'], depts: [2, 1] },
  { name: 'Информационные технологии и вычислительные системы', issn: '2071-8632', indexing: ['vak', 'rinc', 'core_rinc'], lists: ['white3', 'vak_k1', 'rsci'], depts: [0, 2] },
  { name: 'Математическое моделирование и численные методы', issn: '2309-3684', indexing: ['vak', 'rinc'], lists: ['white4', 'vak_k2'], depts: [1] },
  { name: 'Прикладная информатика и анализ данных', issn: '1993-8314', indexing: ['vak', 'rinc'], lists: ['white4', 'vak_k2'], depts: [0, 1] },
  { name: 'Программная инженерия и надёжность', issn: '2220-3397', indexing: ['vak', 'rinc', 'core_rinc'], lists: ['white3', 'vak_k1', 'rsci'], depts: [2] },
  { name: 'Компьютерные исследования и моделирование', issn: '2076-7633', indexing: ['scopus', 'vak', 'rinc', 'core_rinc'], lists: ['white2', 'vak_k1', 'rsci', 'scopus_q3'], depts: [0, 1] },
  { name: 'Journal of Applied Data Science and Analytics', issn: '2589-1142', indexing: ['scopus'], lists: ['white2', 'scopus_q2'], en: true, depts: [0, 1] },
  { name: 'International Journal of Software Engineering and Knowledge', issn: '1873-6025', indexing: ['scopus', 'wos'], lists: ['white1', 'scopus_q1', 'wos_q2'], en: true, depts: [2, 0] },
  { name: 'Cybernetics and Systems Modeling', issn: '1532-4718', indexing: ['scopus', 'wos'], lists: ['white2', 'scopus_q2', 'wos_q3'], en: true, depts: [1] },
]

export const JOURNAL_LISTS: Record<string, string> = {
  white1: 'Белый список, уровень 1', white2: 'Белый список, уровень 2', white3: 'Белый список, уровень 3', white4: 'Белый список, уровень 4',
  vak_k1: 'ВАК, К1', vak_k2: 'ВАК, К2', vak_k3: 'ВАК, К3', rsci: 'RSCI',
  scopus_q1: 'Scopus Q1', scopus_q2: 'Scopus Q2', scopus_q3: 'Scopus Q3', scopus_q4: 'Scopus Q4',
  wos_q1: 'WoS Q1', wos_q2: 'WoS Q2', wos_q3: 'WoS Q3', wos_q4: 'WoS Q4',
}

// Classifier excerpts; the real backend loads full ГРНТИ, ОЕСР, WoS categories and ASJC.
const sub = (scheme: SubjectScheme, rows: [string, string][]): Subject[] => rows.map(([code, name]) => ({ scheme, code, name }))
export const CLASSIFIERS: Record<SubjectScheme, Subject[]> = {
  grnti: sub('grnti', [
    ['20.00', 'Информатика'], ['20.19', 'Информационное обеспечение науки'], ['27.41', 'Вычислительная математика'],
    ['27.43', 'Теория вероятностей и математическая статистика'], ['27.47', 'Математическая кибернетика'],
    ['28.23', 'Искусственный интеллект'], ['28.29', 'Системный анализ'], ['50.41', 'Программное обеспечение вычислительных машин'],
    ['14.85', 'Технические средства обучения'],
  ]),
  oecd: sub('oecd', [
    ['1.01', 'Математика'], ['1.02', 'Компьютерные и информационные науки'],
    ['2.02', 'Электротехника, электронная техника, информационные технологии'], ['5.03', 'Науки об образовании'],
  ]),
  wos: sub('wos', [
    ['CS-AI', 'Computer Science, Artificial Intelligence'], ['CS-IS', 'Computer Science, Information Systems'],
    ['CS-SE', 'Computer Science, Software Engineering'], ['CS-IA', 'Computer Science, Interdisciplinary Applications'],
    ['MATH-AP', 'Mathematics, Applied'], ['MATH-ID', 'Mathematics, Interdisciplinary Applications'],
  ]),
  asjc: sub('asjc', [
    ['1702', 'Artificial Intelligence'], ['1706', 'Computer Science Applications'], ['1710', 'Information Systems'],
    ['1712', 'Software'], ['2604', 'Applied Mathematics'], ['2605', 'Computational Mathematics'],
  ]),
}

/** Seed rubrics by department index: [ГРНТИ, ОЕСР, WoS, ASJC]. */
export const DEPT_SUBJECTS: [string, string, string, string][] = [['28.23', '1.02', 'CS-AI', '1702'], ['27.47', '1.01', 'MATH-AP', '2604'], ['50.41', '1.02', 'CS-SE', '1712']]

export interface Conference {
  short: string
  name: (n: number, year: number) => string
  level: ConferenceLevel
  city: string
  country: string
  base?: number // first year, for numbering
  month: number
  url?: string
  depts: number[]
  en?: boolean
}

const ROMAN: [number, string][] = [[10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']]
function R(n: number): string {
  let s = ''
  for (const [v, r] of ROMAN) while (n >= v) { s += r; n -= v }
  return s
}

export const CONFERENCES: Conference[] = [
  { short: 'Интеллектуальные информационные системы', name: (n) => `${R(n)} Международная научная конференция «Интеллектуальные информационные системы»`, level: 'international', city: 'Казань', country: 'Россия', base: 2012, month: 5, url: 'https://iis-conf.example.ru', depts: [0] },
  { short: 'Математическое моделирование и информационные технологии', name: (n) => `${R(n)} Всероссийская конференция «Математическое моделирование и информационные технологии»`, level: 'national', city: 'Новосибирск', country: 'Россия', base: 2008, month: 6, url: 'https://mmit.example.ru', depts: [1] },
  { short: 'Программная инженерия: методы и технологии', name: (n) => `${R(n)} Международная конференция «Программная инженерия: методы и технологии»`, level: 'international', city: 'Санкт-Петербург', country: 'Россия', base: 2015, month: 10, url: 'https://se-conf.example.ru', depts: [2] },
  { short: 'Цифровая трансформация образования', name: (n) => `${R(n)} Региональная научно-практическая конференция «Цифровая трансформация образования»`, level: 'regional', city: 'Самара', country: 'Россия', base: 2019, month: 3, depts: [0, 2] },
  { short: 'Наука молодых', name: (n) => `${R(n)} Научная конференция студентов и молодых учёных «Наука молодых»`, level: 'university', city: 'Самара', country: 'Россия', base: 2005, month: 4, depts: [0, 1, 2] },
  { short: 'Методы оптимизации и их приложения', name: (n) => `${R(n)} Всероссийский семинар «Методы оптимизации и их приложения»`, level: 'national', city: 'Иркутск', country: 'Россия', base: 2010, month: 7, depts: [1] },
  { short: 'DAKE', name: (n, y) => `${n}th International Conference on Data Analytics and Knowledge Engineering (DAKE ${y})`, level: 'international', city: 'Минск', country: 'Беларусь', base: 2016, month: 9, url: 'https://dake.example.org', depts: [0, 1], en: true },
  { short: 'Искусственный интеллект в высшей школе', name: () => 'Круглый стол «Искусственный интеллект в высшей школе»', level: 'regional', city: 'Самара', country: 'Россия', month: 11, depts: [0, 2] },
]

export const AWARDS = ['Диплом I степени', 'Диплом II степени', 'Диплом III степени', 'Лучший доклад секции']

export const BOOKS = {
  monographs: [
    { title: 'Интеллектуальный анализ данных в управлении образовательными процессами', dept: 0, pages: '212' },
    { title: 'Математические модели распределённых вычислительных систем', dept: 1, pages: '184' },
  ],
  textbooks: [
    { title: 'Методы оптимизации', dept: 1, pages: '156' },
    { title: 'Проектирование информационных систем', dept: 0, pages: '140' },
    { title: 'Тестирование программного обеспечения', dept: 2, pages: '128' },
  ],
  collective: 'Цифровые технологии в науке и образовании : коллективная монография',
  publisher: 'Издательство университета',
}

/** DOIs the mock lookup "knows". None of them is in the seed, so they demo adding a new work. */
export const KNOWN_DOIS: Record<string, { title: string; authors: string[]; venue: string; year: number; volume?: string; issue?: string; pages: string; issn: string; kind: 'journal_article' | 'proceedings_paper'; language: 'ru' | 'en'; indexing: Indexing[] }> = {
  '10.12345/kim.2026.0142': { title: 'Графовые нейронные сети в задаче поиска научных соавторов', authors: ['Орлов Д. С.', 'Белова Н. В.', 'Миронов К. П.'], venue: 'Компьютерные исследования и моделирование', year: 2026, volume: '18', issue: '3', pages: '455–470', issn: '2076-7633', kind: 'journal_article', language: 'ru', indexing: ['scopus', 'vak', 'rinc', 'core_rinc'] },
  '10.12345/jads.2025.118': { title: 'Topic modeling of student feedback for curriculum design', authors: ['D. S. Orlov', 'A. P. Kuznetsov', 'W. Li'], venue: 'Journal of Applied Data Science and Analytics', year: 2025, volume: '7', issue: '2', pages: '118–131', issn: '2589-1142', kind: 'journal_article', language: 'en', indexing: ['scopus'] },
  '10.12345/pi.2026.07': { title: 'Генерация модульных тестов с использованием больших языковых моделей', authors: ['Волков А. Н.', 'Никитина А. С.'], venue: 'Программная инженерия и надёжность', year: 2026, volume: '17', issue: '4', pages: '201–212', issn: '2220-3397', kind: 'journal_article', language: 'ru', indexing: ['vak', 'rinc', 'core_rinc'] },
  '10.12345/mmnm.2025.31': { title: 'Метод Монте-Карло в задаче оценки надёжности распределённых систем', authors: ['Соколов В. А.', 'Лебедева О. Н.', 'Громов С. А.'], venue: 'Математическое моделирование и численные методы', year: 2025, issue: '2', pages: '31–47', issn: '2309-3684', kind: 'journal_article', language: 'ru', indexing: ['vak', 'rinc'] },
  '10.12345/ijse.2026.0093': { title: 'Mutation testing at scale: an industrial case study', authors: ['R. I. Stepanov', 'J. Novák'], venue: 'International Journal of Software Engineering and Knowledge', year: 2026, volume: '36', issue: '1', pages: '93–112', issn: '1873-6025', kind: 'journal_article', language: 'en', indexing: ['scopus', 'wos'] },
}
