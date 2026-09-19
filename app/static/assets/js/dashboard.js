const statusNode = document.querySelector('#status');
const keyInput = document.querySelector('#api-key');
const userIdInput = document.querySelector('#user-id');
const greenhouseBoardsInput = document.querySelector('#greenhouse-boards');
const GREENHOUSE_BOARDS_STORAGE_KEY = 'dashboardGreenhouseBoards';
keyInput.value = sessionStorage.getItem('dashboardApiKey') || '';
userIdInput.value = localStorage.getItem('dashboardUserId') || '';
greenhouseBoardsInput.value = localStorage.getItem(GREENHOUSE_BOARDS_STORAGE_KEY) || '';
const saveGreenhouseBoards = () => {
  localStorage.setItem(GREENHOUSE_BOARDS_STORAGE_KEY, greenhouseBoardsInput.value);
};
const headers = (json) => {
  const h = keyInput.value ? {'X-API-Key': keyInput.value} : {};
  if (json) h['Content-Type'] = 'application/json';
  return h;
};
const fetchWithTimeout = async (url, options = {}, timeoutMs = 15000) => {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {...options, signal: controller.signal});
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('Сервер не ответил вовремя. Проверьте Docker и повторите действие.');
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
};
const element = (name, text, className) => {
  const node = document.createElement(name);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const _esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const TRANSLATIONS = {
  'Личный кабинет': 'Dashboard',
  'Поиск и отклик на вакансии': 'Job search and applications',
  'Разделы личного кабинета': 'Dashboard sections',
  'Все вакансии': 'All vacancies',
  'Поиск': 'Search',
  'Чёрный список': 'Blacklist',
  'Резюме': 'Resumes',
  'Сессии сайтов': 'Site sessions',
  'Модель': 'Model',
  'Очередь матчинга': 'Matching queue',
  'Очередь задач перерасчёта матчинга, которые обращаются к локальной модели. «Остановить» запрещает воркеру брать новые задачи. «Очистить» останавливает обработку, отменяет все ожидающие задачи и прерывает зависшие. «Продолжить» снова запускает обработку.': 'Queue of matching recalculation tasks that call the local model. "Pause" stops the worker from taking new tasks. "Clear" stops processing, cancels all pending tasks, and interrupts stuck ones. "Resume" starts processing again.',
  'Остановить обработку': 'Pause processing',
  'Продолжить обработку': 'Resume processing',
  'Очистить очередь': 'Clear queue',
  'Обновить очередь': 'Refresh queue',
  'Профиль и доступ': 'Profile and access',
  'Отладка': 'Debug',
  'Все сохранённые вакансии': 'All saved vacancies',
  'Синхронизировать отклики': 'Sync applications',
  'Синхронизировать почту': 'Sync email',
  'Импортировать письма': 'Import email files',
  'Можно выбрать несколько EML, почтовый ящик mbox или ZIP-архив. Вложенные письма будут обработаны отдельно.': 'Select multiple EML files, an mbox mailbox, or a ZIP archive. Attached emails are processed separately.',
  'Подключение почты по IMAP': 'Connect email over IMAP',
  'Используйте отдельный пароль приложения. Пароль сохраняется только в зашифрованном виде, содержимое писем не сохраняется.': 'Use a dedicated app password. The password is stored only in encrypted form, and email contents are not stored.',
  'IMAP-хост': 'IMAP host',
  'Порт': 'Port',
  'Имя пользователя': 'Username',
  'Пароль приложения': 'App password',
  'Папка': 'Mailbox',
  'Включено': 'Enabled',
  'Настройки Gmail': 'Gmail settings',
  'Сохранить почту': 'Save email settings',
  'Факты обо мне': 'Facts about me',
  'Только подтверждённые факты используются для оценки вакансий и сопроводительных писем.': 'Only verified facts are used for vacancy assessment and cover letters.',
  'Категория': 'Category',
  'Название': 'Name',
  'Значение': 'Value',
  'Подтверждён': 'Verified',
  'Добавить факт': 'Add fact',
  'На странице': 'Per page',
  'Пагинация фактов': 'Facts pagination',
  'Предыдущая страница': 'Previous page',
  'Следующая страница': 'Next page',
  'Сохранить': 'Save',
  'Редактировать': 'Edit',
  'Удалить': 'Delete',
  'Только подтверждённые': 'Verified only',
  'Все категории': 'All categories',
  'Поиск по названию': 'Search by name',
  'Поиск по значению': 'Search by value',
  'Фильтры фактов': 'Fact filters',
  'Вакансии из поиска и импортированных источников. Фильтры применяются ко всей базе, а не только к текущей странице.': 'Vacancies from search and imported sources. Filters apply to the entire database, not only the current page.',
  'Поиск': 'Search',
  'Источник': 'Source',
  'Статус': 'Status',
  'Локация': 'Location',
  'Соответствие от': 'Minimum match',
  'Опубликована с': 'Published from',
  'Опубликована по': 'Published to',
  'Формат работы': 'Work format',
  'Тип занятости': 'Employment type',
  'Matching v2': 'Matching v2',
  'Рассчитан': 'Completed',
  'Не рассчитан': 'Not calculated',
  'Все': 'All',
  'Удалённо': 'Remote',
  'Гибрид': 'Hybrid',
  'Офис': 'Office',
  'Не указан': 'Unspecified',
  'Полная занятость': 'Full-time',
  'Частичная занятость': 'Part-time',
  'Контракт': 'Contract',
  'Проект': 'Project',
  'Временная работа': 'Temporary',
  'Стажировка': 'Internship',
  'Применить': 'Apply filters',
  'Сбросить': 'Reset',
  '← Назад': '← Previous',
  'Вперёд →': 'Next →',
  'Чёрный список компаний': 'Company blacklist',
  'Вакансии этих компаний не появляются в каталоге и не добавляются повторно при новом поиске. Удаление компании из списка снова разрешает её вакансии.': 'Vacancies from these companies are hidden from the catalog and future searches. Removing a company allows its vacancies again.',
  'Компания': 'Company',
  'Добавить': 'Add',
  'Ключ доступа к приложению': 'Application access key',
  'API-ключ (если сервис настроен без ключа — оставьте пустым). ID пользователя сохраняется в браузере.': 'API key (leave empty if the service does not require one). The user ID is stored in this browser.',
  'Загрузить профиль': 'Load profile',
  'Создать пользователя': 'Create user',
  'Языковая модель': 'Language model',
  'Ключ шифруется перед сохранением в базе и никогда не показывается обратно. Для матчинга и генерации материалов настраивается своя модель: получите список доступных моделей, выберите одну и сохраните.': 'The key is encrypted before storage and is never displayed again. Matching and materials generation each get their own model: load available models, select one, and save it.',
  'Матчинг': 'Matching',
  'Модель для извлечения требований, декомпозиции навыков и оценки соответствия резюме.': 'Model for requirement extraction, skill decomposition, and resume match evaluation.',
  'Генерация материалов': 'Materials generation',
  'Модель для сопроводительных писем, ответов на скрининг и анализа резюме.': 'Model for cover letters, screening answers, and resume analysis.',
  'Провайдер': 'Provider',
  'API-ключ провайдера': 'Provider API key',
  'Получить модели': 'Load models',
  'Сохранить настройку': 'Save settings',
  'Проверяем…': 'Checking…',
  'Вход выполняется вручную в Chromium на локальном браузерном экране. Приложение не получает пароль, код подтверждения или CAPTCHA — сохраняются только зашифрованные cookies и состояние сайта.': 'Sign-in is completed manually in Chromium on the local browser screen. The app never receives passwords, verification codes, or CAPTCHA answers; only encrypted cookies and site state are stored.',
  'Войти в hh.ru': 'Sign in to hh.ru',
  'Войти в LinkedIn': 'Sign in to LinkedIn',
  'Я вошёл — сохранить': 'I am signed in — save',
  'Отмена': 'Cancel',
  'Загрузить файлы': 'Upload files',
  'Можно хранить несколько резюме. Навыки, ключевые слова и сводка опыта сохраняются отдельно для каждого файла. Выбранное резюме используется для поиска, оценки и сопроводительных писем.': 'You can store multiple resumes. Skills, keywords, and experience summaries are saved per file. The selected resume is used for search, matching, and cover letters.',
  'Мои резюме': 'My resumes',
  'Проанализировать выбранное': 'Analyze selected resume',
  'Удалить выбранное': 'Delete selected resume',
  'Найденные навыки (снимите галочку, если что-то лишнее)': 'Detected skills (uncheck anything irrelevant)',
  'Добавить навык вручную': 'Add a skill manually',
  'Краткое summary опыта': 'Experience summary',
  'Суммарный опыт, лет': 'Total experience, years',
  'Ключевые слова для поиска': 'Search keywords',
  'Подтвердить и сохранить': 'Confirm and save',
  'Локации': 'Locations',
  'Через запятую, например: Москва, Санкт-Петербург. Оставьте пустым для поиска везде.': 'Comma-separated, for example: Moscow, Saint Petersburg. Leave empty to search anywhere.',
  'Везде': 'Anywhere',
  'Резюме для этого поиска': 'Resume for this search',
  'Доски Greenhouse': 'Greenhouse boards',
  'hh.ru и LinkedIn ищутся через управляемый браузер с вашими сессиями. Greenhouse использует официальный публичный read-only интерфейс досок компаний и не требует входа.': 'hh.ru and LinkedIn are searched through a managed browser using your sessions. Greenhouse uses the official public read-only company board interface and does not require sign-in.',
  'везде': 'anywhere',
  'По одной ссылке на строку, например https://boards.greenhouse.io/company. Если оставить пустым, используются Greenhouse-доски из уже сохранённых вакансий.': 'One URL per line, for example https://boards.greenhouse.io/company. Leave empty to use Greenhouse boards from saved vacancies.',
  'Найти вакансии': 'Find vacancies',
  'Переранжировать': 'Re-rank',
  'Сопроводительное письмо (можно отредактировать)': 'Cover letter (editable)',
  'Принять и откликнуться': 'Accept and apply',
  'Открыть для рассмотрения': 'Open for review',
  'Отклик отправлен': 'Application sent',
  'Отклонить': 'Reject',
  'Компания в чёрный список': 'Blacklist company',
  'Открыть вакансию': 'Open vacancy',
  'Материалы и решение': 'Materials and decision',
  'Почему подходит': 'Why it matches',
  'Формат не указан': 'Format unspecified',
  'Сохранена': 'Saved',
  'Ожидает решения': 'Awaiting decision',
  'Принята': 'Accepted',
  'Отклонена': 'Rejected',
  'Отказ работодателя': 'Employer rejection',
  'Пропущена': 'Skipped',
  'Отправлена': 'Submitted',
  'Собеседование': 'Interview',
  'Черновик': 'Draft',
  'уже был в списке ранее': 'already appeared in the list'
};
let currentLanguage = localStorage.getItem('dashboardLanguage') === 'en' ? 'en' : 'ru';
const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const LANGUAGE_OPTIONS = [
  {code: 'ru', names: {ru: 'Русский', en: 'Russian'}},
  {code: 'en', names: {ru: 'Английский', en: 'English'}}
];
const originalTextNodes = new WeakMap();
const originalAttributes = new WeakMap();

function createSvgNode(name, attributes) {
  const node = document.createElementNS(SVG_NAMESPACE, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function createLanguageFlag(languageCode) {
  const svg = createSvgNode('svg', {
    class: 'language-flag',
    viewBox: languageCode === 'ru' ? '0 0 3 2' : '0 0 60 36',
    'aria-hidden': 'true',
    focusable: 'false'
  });
  if (languageCode === 'ru') {
    svg.append(
      createSvgNode('rect', {width: '3', height: '2', fill: '#fff'}),
      createSvgNode('rect', {y: '0.6667', width: '3', height: '0.6667', fill: '#0039a6'}),
      createSvgNode('rect', {y: '1.3333', width: '3', height: '0.6667', fill: '#d52b1e'})
    );
    return svg;
  }
  svg.append(
    createSvgNode('rect', {width: '60', height: '36', fill: '#012169'}),
    createSvgNode('path', {d: 'M0 0 60 36M60 0 0 36', stroke: '#fff', 'stroke-width': '8'}),
    createSvgNode('path', {d: 'M0 0 60 36M60 0 0 36', stroke: '#c8102e', 'stroke-width': '4'}),
    createSvgNode('path', {d: 'M30 0V36M0 18H60', stroke: '#fff', 'stroke-width': '11'}),
    createSvgNode('path', {d: 'M30 0V36M0 18H60', stroke: '#c8102e', 'stroke-width': '6'})
  );
  return svg;
}

function setLanguageMenuOpen(isOpen, {focusSelected = false} = {}) {
  const toggle = document.querySelector('#language-toggle');
  const menu = document.querySelector('#language-menu');
  toggle.setAttribute('aria-expanded', String(isOpen));
  menu.hidden = !isOpen;
  if (isOpen && focusSelected) {
    menu.querySelector('[aria-selected="true"]')?.focus();
  }
}

function focusAdjacentLanguageOption(currentOption, direction) {
  const options = Array.from(document.querySelectorAll('#language-menu .language-option'));
  const currentIndex = options.indexOf(currentOption);
  options[(currentIndex + direction + options.length) % options.length]?.focus();
}

function renderLanguageSelector() {
  const toggle = document.querySelector('#language-toggle');
  const menu = document.querySelector('#language-menu');
  const selectedLanguage = LANGUAGE_OPTIONS.find(option => option.code === currentLanguage) || LANGUAGE_OPTIONS[0];
  const selectedName = selectedLanguage.names[selectedLanguage.code];
  const name = document.createElement('span');
  name.className = 'language-name';
  name.textContent = selectedName;
  const chevron = document.createElement('span');
  chevron.className = 'language-chevron';
  chevron.setAttribute('aria-hidden', 'true');
  chevron.textContent = '▾';
  toggle.replaceChildren(createLanguageFlag(selectedLanguage.code), name, chevron);
  toggle.title = currentLanguage === 'en'
    ? `Current language: ${selectedName}`
    : `Текущий язык: ${selectedName}`;
  toggle.setAttribute('aria-label', toggle.title);
  menu.setAttribute('aria-label', currentLanguage === 'en' ? 'Interface language' : 'Язык интерфейса');
  menu.replaceChildren();
  LANGUAGE_OPTIONS.forEach(language => {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = 'language-option';
    option.dataset.languageCode = language.code;
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', String(language.code === currentLanguage));
    const optionName = document.createElement('span');
    optionName.textContent = language.names[currentLanguage];
    option.append(createLanguageFlag(language.code), optionName);
    option.addEventListener('click', () => selectLanguage(language.code));
    option.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        focusAdjacentLanguageOption(option, event.key === 'ArrowDown' ? 1 : -1);
      }
    });
    menu.append(option);
  });
  setLanguageMenuOpen(false);
}

function translatedStaticText(text) {
  const leading = text.match(/^\s*/)?.[0] || '';
  const trailing = text.match(/\s*$/)?.[0] || '';
  const trimmed = text.trim();
  return TRANSLATIONS[trimmed] ? `${leading}${TRANSLATIONS[trimmed]}${trailing}` : text;
}

function translateTree(root = document.body) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let textNode;
  while ((textNode = walker.nextNode())) {
    if (['SCRIPT', 'STYLE'].includes(textNode.parentElement?.tagName)) continue;
    if (!originalTextNodes.has(textNode)) originalTextNodes.set(textNode, textNode.nodeValue);
    const original = originalTextNodes.get(textNode);
    textNode.nodeValue = currentLanguage === 'en' ? translatedStaticText(original) : original;
  }
  const elements = root.querySelectorAll ? [root, ...root.querySelectorAll('*')] : [];
  elements.forEach(node => {
    if (!(node instanceof Element)) return;
    if (node.id === 'language-toggle') return;
    if (!originalAttributes.has(node)) {
      originalAttributes.set(node, {
        placeholder: node.getAttribute('placeholder'),
        title: node.getAttribute('title'),
        ariaLabel: node.getAttribute('aria-label')
      });
    }
    const originals = originalAttributes.get(node);
    [['placeholder', originals.placeholder], ['title', originals.title], ['aria-label', originals.ariaLabel]]
      .forEach(([attribute, original]) => {
        if (original === null) return;
        node.setAttribute(
          attribute,
          currentLanguage === 'en' ? (TRANSLATIONS[original] || original) : original
        );
      });
  });
  document.documentElement.lang = currentLanguage;
}

function selectLanguage(languageCode) {
  if (!LANGUAGE_OPTIONS.some(option => option.code === languageCode)) return;
  if (currentLanguage === languageCode) {
    setLanguageMenuOpen(false);
    return;
  }
  currentLanguage = languageCode;
  localStorage.setItem('dashboardLanguage', currentLanguage);
  translateTree();
  renderLanguageSelector();
  populateProfileFactCategoryFilter(profileFactsCache);
  renderProfileFacts();
  loadSavedVacancies(vacancyPage).catch(showError);
  restoreSearchResults();
}

document.querySelector('#language-toggle').addEventListener('click', () => {
  const isOpen = document.querySelector('#language-toggle').getAttribute('aria-expanded') !== 'true';
  setLanguageMenuOpen(isOpen);
});
document.querySelector('#language-toggle').addEventListener('keydown', event => {
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    setLanguageMenuOpen(true, {focusSelected: true});
  }
});
document.querySelector('#language-menu').addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    event.preventDefault();
    setLanguageMenuOpen(false);
    document.querySelector('#language-toggle').focus();
  }
});
document.addEventListener('click', event => {
  if (!document.querySelector('#language-selector').contains(event.target)) {
    setLanguageMenuOpen(false);
  }
});
const translationObserver = new MutationObserver(mutations => {
  if (currentLanguage !== 'en') return;
  mutations.forEach(mutation => {
    mutation.addedNodes.forEach(node => {
      if (node.nodeType === Node.ELEMENT_NODE) translateTree(node);
      if (node.nodeType === Node.TEXT_NODE && node.parentElement) translateTree(node.parentElement);
    });
  });
});
translationObserver.observe(document.body, {childList: true, subtree: true});
function showError(error) { statusNode.textContent = error.message; }
function showStatus(text) { statusNode.textContent = text; }
async function asJson(response) {
  if (!response.ok) {
    if (response.status === 401) {
      throw new Error('Отказано в доступе (401): укажите действующий API-ключ во вкладке доступа.');
    }
    const payload = await response.json().catch(() => ({}));
    const detail = payload.detail;
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map(item => item?.msg || JSON.stringify(item)).join('; ')
        : detail?.message || payload.error?.message || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return response.json();
}

let profileFactsCache = [];
let editingProfileFactId = null;
let openProfileFactMenuId = null;
let profileFactsPage = 1;
let profileFactsPageSize = Number.parseInt(localStorage.getItem('dashboardProfileFactsPageSize') || '10', 10);
if (![5, 10, 20, 50].includes(profileFactsPageSize)) profileFactsPageSize = 10;
document.querySelector('#profile-facts-page-size').value = String(profileFactsPageSize);

function profileFactKind(fact) {
  const category = String(fact.category || '').toLowerCase();
  const name = String(fact.name || '').toLowerCase();
  if (category.includes('lang') || category.includes('язык')) return 'language';
  if (category.includes('experience') || category.includes('опыт')) return 'experience';
  if (name.includes('api') || name.includes('integration') || name.includes('интеграц')) return 'integration';
  return 'skill';
}

function profileFactIconText(fact) {
  const kind = profileFactKind(fact);
  if (kind === 'integration') return '↔';
  if (kind === 'language') return '文';
  if (kind === 'experience') return 'EXP';
  return '</>';
}

function populateProfileFactCategoryFilter(facts) {
  const select = document.querySelector('#profile-fact-category-filter');
  const currentValue = select.value;
  const categories = [...new Set(facts.map(fact => String(fact.category || '').trim()).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, currentLanguage === 'en' ? 'en' : 'ru'));
  select.replaceChildren();
  const all = document.createElement('option');
  all.value = '';
  all.textContent = currentLanguage === 'en' ? 'All categories' : 'Все категории';
  select.append(all);
  categories.forEach(category => {
    const option = document.createElement('option');
    option.value = category;
    option.textContent = category;
    select.append(option);
  });
  select.value = categories.includes(currentValue) ? currentValue : '';
}

function filteredProfileFacts() {
  const category = document.querySelector('#profile-fact-category-filter').value.trim().toLowerCase();
  const nameQuery = document.querySelector('#profile-fact-name-filter').value.trim().toLowerCase();
  const valueQuery = document.querySelector('#profile-fact-value-filter').value.trim().toLowerCase();
  const verifiedOnly = document.querySelector('#profile-fact-verified-filter').checked;
  const sourceFilter = document.querySelector('#profile-fact-source-filter').value.trim().toLowerCase();
  return profileFactsCache.filter(fact => {
    if (category && String(fact.category || '').toLowerCase() !== category) return false;
    if (nameQuery && !String(fact.name || '').toLowerCase().includes(nameQuery)) return false;
    if (valueQuery && !String(fact.value || '').toLowerCase().includes(valueQuery)) return false;
    if (verifiedOnly && !fact.is_verified) return false;
    if (sourceFilter && String(fact.source_type || 'manual').toLowerCase() !== sourceFilter) return false;
    return true;
  });
}

function createProfileFactEditor(fact) {
  const editor = element('div', undefined, 'fact-editor');

  const categoryWrap = document.createElement('div');
  const categoryLabel = element('label', currentLanguage === 'en' ? 'Category' : 'Категория');
  const category = document.createElement('input');
  category.dataset.field = 'category';
  category.maxLength = 100;
  category.required = true;
  category.value = fact.category || '';
  categoryWrap.append(categoryLabel, category);

  const nameWrap = document.createElement('div');
  const nameLabel = element('label', currentLanguage === 'en' ? 'Name' : 'Название');
  const name = document.createElement('input');
  name.dataset.field = 'name';
  name.maxLength = 200;
  name.required = true;
  name.value = fact.name || '';
  nameWrap.append(nameLabel, name);

  const valueWrap = document.createElement('div');
  const valueLabel = element('label', currentLanguage === 'en' ? 'Value' : 'Значение');
  const value = document.createElement('input');
  value.dataset.field = 'value';
  value.required = true;
  value.value = fact.value || '';
  valueWrap.append(valueLabel, value);

  const actions = element('div', undefined, 'fact-editor__actions');
  const verifiedLabel = element('label', undefined, 'facts-check');
  const verified = document.createElement('input');
  verified.type = 'checkbox';
  verified.dataset.field = 'is_verified';
  verified.checked = Boolean(fact.is_verified);
  verifiedLabel.append(verified, document.createTextNode(currentLanguage === 'en' ? ' Verified' : ' Подтверждён'));
  const save = element('button', currentLanguage === 'en' ? 'Save' : 'Сохранить', 'primary');
  save.type = 'button';
  save.dataset.action = 'save';
  const cancel = element('button', currentLanguage === 'en' ? 'Cancel' : 'Отмена');
  cancel.type = 'button';
  cancel.dataset.action = 'cancel-edit';
  actions.append(verifiedLabel, save, cancel);

  editor.append(categoryWrap, nameWrap, valueWrap, actions);
  return editor;
}

function renderProfileFacts() {
  const container = document.querySelector('#profile-facts-list');
  const count = document.querySelector('#profile-facts-count');
  const pageIndicator = document.querySelector('#profile-facts-page');
  const previousButton = document.querySelector('#profile-facts-previous');
  const nextButton = document.querySelector('#profile-facts-next');
  container.replaceChildren();

  const filteredFacts = filteredProfileFacts();
  const totalFiltered = filteredFacts.length;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / profileFactsPageSize));
  profileFactsPage = Math.max(1, Math.min(profileFactsPage, totalPages));

  const startIndex = (profileFactsPage - 1) * profileFactsPageSize;
  const endIndex = Math.min(startIndex + profileFactsPageSize, totalFiltered);
  const facts = filteredFacts.slice(startIndex, endIndex);

  pageIndicator.textContent = `${profileFactsPage} / ${totalPages}`;
  previousButton.disabled = profileFactsPage <= 1 || totalFiltered === 0;
  nextButton.disabled = profileFactsPage >= totalPages || totalFiltered === 0;

  if (!profileFactsCache.length) {
    container.append(element('div', currentLanguage === 'en' ? 'No facts saved.' : 'Факты пока не сохранены.', 'empty facts-empty'));
    count.textContent = '';
    return;
  }

  if (!totalFiltered) {
    container.append(element('div', currentLanguage === 'en' ? 'No facts match the selected filters.' : 'По выбранным фильтрам фактов нет.', 'empty facts-empty'));
    count.textContent = currentLanguage === 'en'
      ? `Shown 0 of ${profileFactsCache.length}`
      : `Показано 0 из ${profileFactsCache.length}`;
    return;
  }

  facts.forEach(fact => {
    const card = element('div', undefined, 'fact-card');
    card.dataset.factId = fact.id;

    if (String(fact.id) === String(editingProfileFactId)) {
      card.classList.add('is-editing');
      card.append(createProfileFactEditor(fact));
      container.append(card);
      return;
    }

    const icon = element('div', profileFactIconText(fact), 'fact-icon');
    icon.dataset.kind = profileFactKind(fact);
    icon.setAttribute('aria-hidden', 'true');

    const category = element('span', fact.category || '—', 'fact-category-badge');
    category.title = fact.category || '';
    const name = element('div', fact.name || '—', 'fact-name');
    name.title = fact.name || '';
    const value = element('div', fact.value || '—', 'fact-value');
    value.title = fact.value || '';

    const status = element('span', undefined, `fact-status${fact.is_verified ? '' : ' unverified'}`);
    const statusDot = element('span', fact.is_verified ? '✓' : '!', 'fact-status__dot');
    const statusText = element(
      'span',
      fact.is_verified
        ? (currentLanguage === 'en' ? 'Verified' : 'Подтверждён')
        : (currentLanguage === 'en' ? 'Unverified' : 'Не подтверждён')
    );
    status.append(statusDot, statusText);

    // Source badge
    const sourceType = fact.source_type || 'manual';
    const sourceBadge = element('span', sourceType, 'fact-source-badge');
    sourceBadge.dataset.source = sourceType;
    if (fact.confidence !== undefined && fact.confidence !== null && fact.confidence < 1) {
      const conf = element('span', `${Math.round(fact.confidence * 100)}%`, 'fact-confidence');
      sourceBadge.append(conf);
    }

    const menuWrap = element('div', undefined, 'fact-menu-wrap');
    const menuToggle = element('button', '⋮', 'fact-menu-toggle');
    menuToggle.type = 'button';
    menuToggle.dataset.action = 'toggle-menu';
    menuToggle.setAttribute('aria-label', currentLanguage === 'en' ? 'Fact actions' : 'Действия с фактом');
    const menuIsOpen = String(openProfileFactMenuId) === String(fact.id);
    menuToggle.setAttribute('aria-expanded', String(menuIsOpen));
    menuWrap.append(menuToggle);

    if (menuIsOpen) {
      const menu = element('div', undefined, 'fact-menu');
      const edit = element('button', currentLanguage === 'en' ? 'Edit' : 'Редактировать');
      edit.type = 'button';
      edit.dataset.action = 'edit';
      const remove = element('button', currentLanguage === 'en' ? 'Delete' : 'Удалить');
      remove.type = 'button';
      remove.dataset.action = 'delete';
      menu.append(edit, remove);
      menuWrap.append(menu);
    }

    const meta = element('div', undefined, 'fact-meta');
    meta.append(sourceBadge, status);

    card.append(icon, category, name, value, meta, menuWrap);
    container.append(card);
  });

  const shownFrom = startIndex + 1;
  const shownTo = endIndex;
  if (totalFiltered === profileFactsCache.length) {
    count.textContent = currentLanguage === 'en'
      ? `Shown ${shownFrom}–${shownTo} of ${totalFiltered}`
      : `Показано ${shownFrom}–${shownTo} из ${totalFiltered}`;
  } else {
    count.textContent = currentLanguage === 'en'
      ? `Shown ${shownFrom}–${shownTo} of ${totalFiltered} filtered (${profileFactsCache.length} total)`
      : `Показано ${shownFrom}–${shownTo} из ${totalFiltered} по фильтру (всего ${profileFactsCache.length})`;
  }
}

async function loadSearchPreferences() {
  const userId = userIdInput.value.trim();
  const state = document.querySelector('#search-preferences-state');
  const checked = (name, values) => document.querySelectorAll(`input[name="${name}"]`)
    .forEach(box => { box.checked = values.includes(box.value); });
  if (!userId) {
    state.textContent = '';
    document.querySelector('#pref-min-salary').value = '';
    document.querySelector('#pref-locations').value = '';
    checked('pref-work-format', []);
    checked('pref-employment', []);
    return null;
  }
  const preferences = await asJson(await fetchWithTimeout(
    `/v1/users/${userId}/preferences`,
    {headers: headers(false)}
  ));
  document.querySelector('#pref-min-salary').value = preferences.min_salary ?? '';
  document.querySelector('#pref-currency').value = preferences.salary_currency;
  document.querySelector('#pref-locations').value = preferences.preferred_locations.join(', ');
  checked('pref-work-format', preferences.work_formats);
  checked('pref-employment', preferences.employment_types);
  return preferences;
}

async function saveSearchPreferences() {
  const userId = userIdInput.value.trim();
  if (!userId) throw new Error('Сначала создайте или укажите User ID');
  const selected = name => [...document.querySelectorAll(`input[name="${name}"]:checked`)]
    .map(box => box.value);
  const salary = document.querySelector('#pref-min-salary').value.trim();
  const body = {
    min_salary: salary === '' ? null : Number.parseInt(salary, 10),
    salary_currency: document.querySelector('#pref-currency').value,
    preferred_locations: document.querySelector('#pref-locations').value
      .split(',').map(value => value.trim()).filter(Boolean),
    work_formats: selected('pref-work-format'),
    employment_types: selected('pref-employment'),
  };
  await asJson(await fetchWithTimeout(`/v1/users/${userId}/preferences`, {
    method: 'PUT', headers: headers(true), body: JSON.stringify(body)
  }));
  await loadSearchPreferences();
  document.querySelector('#search-preferences-state').textContent = 'Предпочтения сохранены.';
}

document.querySelector('#save-search-preferences').addEventListener('click', () => {
  saveSearchPreferences().catch(showError);
});

async function loadProfileFacts() {
  const userId = userIdInput.value.trim();
  if (!userId) {
    profileFactsCache = [];
    populateProfileFactCategoryFilter([]);
    renderProfileFacts();
    return [];
  }
  // Use detailed endpoint to get source_type, confidence, status
  const facts = await asJson(await fetchWithTimeout(
    `/v1/users/${userId}/facts-detailed`,
    {headers: headers(false)}
  ));
  profileFactsCache = Array.isArray(facts) ? facts : [];
  populateProfileFactCategoryFilter(profileFactsCache);
  renderProfileFacts();
  return profileFactsCache;
}

function setProfileFactAddFormOpen(isOpen) {
  const form = document.querySelector('#profile-fact-form');
  const toggle = document.querySelector('#profile-fact-add-toggle');
  form.hidden = !isOpen;
  toggle.setAttribute('aria-expanded', String(isOpen));
  if (isOpen) window.setTimeout(() => document.querySelector('#profile-fact-category').focus(), 0);
}

document.querySelector('#profile-fact-add-toggle').addEventListener('click', () => {
  setProfileFactAddFormOpen(document.querySelector('#profile-fact-form').hidden);
});

document.querySelector('#profile-fact-add-cancel').addEventListener('click', () => {
  document.querySelector('#profile-fact-form').reset();
  document.querySelector('#profile-fact-verified').checked = true;
  setProfileFactAddFormOpen(false);
});

document.querySelectorAll('#profile-fact-category-filter, #profile-fact-name-filter, #profile-fact-value-filter, #profile-fact-verified-filter, #profile-fact-source-filter')
  .forEach(control => control.addEventListener('input', () => {
    editingProfileFactId = null;
    openProfileFactMenuId = null;
    profileFactsPage = 1;
    renderProfileFacts();
  }));

document.querySelector('#profile-facts-page-size').addEventListener('change', event => {
  profileFactsPageSize = Number.parseInt(event.currentTarget.value, 10) || 10;
  localStorage.setItem('dashboardProfileFactsPageSize', String(profileFactsPageSize));
  editingProfileFactId = null;
  openProfileFactMenuId = null;
  profileFactsPage = 1;
  renderProfileFacts();
});

document.querySelector('#profile-facts-previous').addEventListener('click', () => {
  if (profileFactsPage <= 1) return;
  editingProfileFactId = null;
  openProfileFactMenuId = null;
  profileFactsPage -= 1;
  renderProfileFacts();
  document.querySelector('#profile-facts-panel').scrollIntoView({behavior: 'smooth', block: 'start'});
});

document.querySelector('#profile-facts-next').addEventListener('click', () => {
  const totalPages = Math.max(1, Math.ceil(filteredProfileFacts().length / profileFactsPageSize));
  if (profileFactsPage >= totalPages) return;
  editingProfileFactId = null;
  openProfileFactMenuId = null;
  profileFactsPage += 1;
  renderProfileFacts();
  document.querySelector('#profile-facts-panel').scrollIntoView({behavior: 'smooth', block: 'start'});
});

document.querySelector('#profile-fact-form').addEventListener('submit', async event => {
  event.preventDefault();
  const userId = userIdInput.value.trim();
  if (!userId) {
    showError(new Error(currentLanguage === 'en' ? 'Set User ID first' : 'Сначала укажите User ID'));
    return;
  }
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    await asJson(await fetchWithTimeout(`/v1/users/${userId}/facts`, {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({
        category: document.querySelector('#profile-fact-category').value.trim(),
        name: document.querySelector('#profile-fact-name').value.trim(),
        value: document.querySelector('#profile-fact-value').value.trim(),
        is_verified: document.querySelector('#profile-fact-verified').checked
      })
    }));
    event.currentTarget.reset();
    document.querySelector('#profile-fact-verified').checked = true;
    setProfileFactAddFormOpen(false);
    await loadProfileFacts();
    showStatus(currentLanguage === 'en' ? 'Fact added.' : 'Факт добавлен.');
  } catch (error) {
    showError(error);
  } finally {
    submitButton.disabled = false;
  }
});

// ── Fact import from file ──
document.querySelector('#fact-import-file-btn').addEventListener('click', () => {
  document.querySelector('#fact-import-file-input').click();
});

document.querySelector('#fact-import-file-input').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  const userId = userIdInput.value.trim();
  if (!userId) { showError(new Error('Set User ID first')); return; }
  const resultDiv = document.querySelector('#fact-import-result');
  resultDiv.hidden = false;
  resultDiv.textContent = `Импорт ${file.name}...`;
  const formData = new FormData();
  formData.append('file', file);
  try {
    const resp = await fetch(`/v1/users/${userId}/facts/import-file?auto_accept=false`, {
      method: 'POST', headers: headers(true), body: formData,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const result = await resp.json();
    const lang = currentLanguage === 'en';
    let candidatesHtml = '';
    if (result.candidates && result.candidates.length) {
      const rows = result.candidates.map(c => {
        const status = c.duplicate_status === 'new'
          ? '<span style="color:#6ee7b7">● новый</span>'
          : c.duplicate_status === 'merge_candidate'
            ? '<span style="color:#facc15">⇄ слияние</span>'
            : '<span style="color:#f87171">= дубль</span>';
        const conf = c.confidence != null ? `${Math.round(c.confidence * 100)}%` : '—';
        const srcText = c.source_text ? `<div style="color:#7a8ab5;font-size:11px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:500px" title="${_esc(c.source_text)}">${_esc(c.source_text)}</div>` : '';
        return `<tr>
              <td style="padding:4px 8px;white-space:nowrap">${_esc(c.category)}</td>
              <td style="padding:4px 8px;white-space:nowrap;font-weight:700">${_esc(c.name)}</td>
              <td style="padding:4px 8px">${_esc(c.value || '—')}${srcText}</td>
              <td style="padding:4px 8px;text-align:center">${conf}</td>
              <td style="padding:4px 8px;text-align:center">${status}</td>
            </tr>`;
      }).join('');
      candidatesHtml = `
            <details style="margin-top:8px">
              <summary style="cursor:pointer;color:#8da2fb;font-weight:700;font-size:13px">
                ${lang ? 'Imported facts' : 'Импортированные факты'} (${result.candidates.length})
              </summary>
              <div style="max-height:340px;overflow:auto;margin-top:6px;border:1px solid #30426d;border-radius:8px">
                <table style="width:100%;border-collapse:collapse;font-size:12px">
                  <thead><tr style="background:#111a34">
                    <th style="padding:6px 8px;text-align:left">${lang ? 'Category' : 'Категория'}</th>
                    <th style="padding:6px 8px;text-align:left">${lang ? 'Name' : 'Название'}</th>
                    <th style="padding:6px 8px;text-align:left">${lang ? 'Value' : 'Значение'}</th>
                    <th style="padding:6px 8px">${lang ? 'Conf' : 'Увер.'}</th>
                    <th style="padding:6px 8px">${lang ? 'Status' : 'Статус'}</th>
                  </tr></thead>
                  <tbody>${rows}</tbody>
                </table>
              </div>
            </details>`;
    }
    resultDiv.innerHTML = `<strong>Импорт завершён:</strong>
          <div class="stats">
            <span class="stat"><span class="num">${result.facts_created}</span> новых</span>
            <span class="stat"><span class="num">${result.facts_merged}</span> объединено</span>
            <span class="stat"><span class="num">${result.facts_skipped}</span> пропущено</span>
            <span class="stat"><span class="num">${result.facts_rejected}</span> отклонено</span>
          </div>
          <div style="color:#7a8ab5;font-size:11px">Batch ID: ${result.batch_id}</div>
          ${candidatesHtml}`;
    await loadProfileFacts();
    // Show newly imported facts: turn off verified-only filter, set source to file
    document.querySelector('#profile-fact-verified-filter').checked = false;
    document.querySelector('#profile-fact-source-filter').value = 'file';
    renderProfileFacts();
    showStatus(`Импортировано из ${file.name}.`);
  } catch (error) { showError(error); }
  event.target.value = '';
});

// ── Fact extraction from resume ──
document.querySelector('#fact-extract-resume-btn').addEventListener('click', async () => {
  const userId = userIdInput.value.trim();
  if (!userId) { showError(new Error('Set User ID first')); return; }
  // Use the resume explicitly marked active in the resume panel.
  try {
    const cvs = await asJson(await fetchWithTimeout(`/v1/users/${userId}/cv-files`, {headers: headers(false)}));
    if (!Array.isArray(cvs) || cvs.length === 0) {
      showError(new Error(currentLanguage === 'en' ? 'No resumes found. Upload a resume first.' : 'Резюме не найдены. Сначала загрузите резюме.'));
      return;
    }
    const activeCv = cvs.find(cv => cv.is_active);
    if (!activeCv) {
      showError(new Error(currentLanguage === 'en'
        ? 'No active resume. Select one in the resume panel first.'
        : 'Нет активного резюме. Сначала выберите его в разделе «Резюме».'));
      return;
    }
    const cvId = activeCv.id;
    const cvName = activeCv.original_filename;
    const resultDiv = document.querySelector('#fact-import-result');
    resultDiv.hidden = false;
    resultDiv.textContent = currentLanguage === 'en'
      ? `Extracting facts from “${cvName}”...`
      : `Извлечение фактов из «${cvName}»...`;
    const resp = await fetch(`/v1/users/${userId}/facts/extract-from-resume`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', ...headers(true)},
      body: JSON.stringify({cv_file_id: cvId, force: false, auto_accept: false}),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const result = await resp.json();
    const lang = currentLanguage === 'en';
    let candidatesHtml = '';
    if (result.candidates && result.candidates.length) {
      const rows = result.candidates.map(c => {
        const status = c.duplicate_status === 'new'
          ? '<span style="color:#6ee7b7">● новый</span>'
          : c.duplicate_status === 'merge_candidate'
            ? '<span style="color:#facc15">⇄ слияние</span>'
            : '<span style="color:#f87171">= дубль</span>';
        const conf = c.confidence != null ? `${Math.round(c.confidence * 100)}%` : '—';
        const srcText = c.source_text ? `<div style="color:#7a8ab5;font-size:11px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:500px" title="${_esc(c.source_text)}">${_esc(c.source_text)}</div>` : '';
        return `<tr>
              <td style="padding:4px 8px;white-space:nowrap">${_esc(c.category)}</td>
              <td style="padding:4px 8px;white-space:nowrap;font-weight:700">${_esc(c.name)}</td>
              <td style="padding:4px 8px">${_esc(c.value || '—')}${srcText}</td>
              <td style="padding:4px 8px;text-align:center">${conf}</td>
              <td style="padding:4px 8px;text-align:center">${status}</td>
            </tr>`;
      }).join('');
      candidatesHtml = `
            <details style="margin-top:8px">
              <summary style="cursor:pointer;color:#8da2fb;font-weight:700;font-size:13px">
                ${lang ? 'Extracted facts' : 'Извлечённые факты'} (${result.candidates.length})
              </summary>
              <div style="max-height:340px;overflow:auto;margin-top:6px;border:1px solid #30426d;border-radius:8px">
                <table style="width:100%;border-collapse:collapse;font-size:12px">
                  <thead><tr style="background:#111a34">
                    <th style="padding:6px 8px;text-align:left">${lang ? 'Category' : 'Категория'}</th>
                    <th style="padding:6px 8px;text-align:left">${lang ? 'Name' : 'Название'}</th>
                    <th style="padding:6px 8px;text-align:left">${lang ? 'Value' : 'Значение'}</th>
                    <th style="padding:6px 8px">${lang ? 'Conf' : 'Увер.'}</th>
                    <th style="padding:6px 8px">${lang ? 'Status' : 'Статус'}</th>
                  </tr></thead>
                  <tbody>${rows}</tbody>
                </table>
              </div>
            </details>`;
    }
    resultDiv.innerHTML = `<strong>${lang ? 'Extraction complete:' : 'Извлечение завершено:'}</strong>
          <div style="color:#aab9e8;font-size:12px;margin-top:4px">${lang ? 'Resume' : 'Резюме'}: ${_esc(cvName)}</div>
          <div class="stats">
            <span class="stat"><span class="num">${result.facts_created}</span> ${lang ? 'new' : 'новых'}</span>
            <span class="stat"><span class="num">${result.facts_merged}</span> ${lang ? 'merged' : 'объединено'}</span>
            <span class="stat"><span class="num">${result.facts_skipped}</span> ${lang ? 'skipped' : 'пропущено'}</span>
          </div>
          <div style="color:#7a8ab5;font-size:11px">Batch ID: ${result.batch_id}</div>
          ${candidatesHtml}`;
    await loadProfileFacts();
    // Show newly extracted facts: turn off verified-only filter, set source to resume
    document.querySelector('#profile-fact-verified-filter').checked = false;
    document.querySelector('#profile-fact-source-filter').value = 'resume';
    renderProfileFacts();
    showStatus(currentLanguage === 'en'
      ? `Facts extracted from “${cvName}”.`
      : `Факты извлечены из «${cvName}».`);
  } catch (error) { showError(error); }
});

// ── Batch history ──
document.querySelector('#fact-batch-history-btn').addEventListener('click', async () => {
  const userId = userIdInput.value.trim();
  if (!userId) { showError(new Error('Set User ID first')); return; }
  const batchDiv = document.querySelector('#fact-batch-history');
  if (!batchDiv.hidden) { batchDiv.hidden = true; return; }
  try {
    const batches = await asJson(await fetchWithTimeout(`/v1/users/${userId}/fact-import-batches`, {headers: headers(false)}));
    if (!Array.isArray(batches) || batches.length === 0) {
      batchDiv.innerHTML = '<div style="color:#7a8ab5">История импорта пуста.</div>';
    } else {
      batchDiv.innerHTML = batches.map(b => `
            <div class="batch-card">
              <div class="batch-info">${b.source_type} — ${b.source_filename || b.source_id || '—'} — ${new Date(b.created_at).toLocaleString()}</div>
              <div class="batch-stats">
                <span>+${b.facts_created} ~${b.facts_merged} -${b.facts_skipped}</span>
                <span>${b.status}</span>
              </div>
              ${b.status === 'completed' ? `<button onclick="undoBatch('${b.id}')" style="font-size:12px;padding:4px 8px">Отменить</button>` : ''}
            </div>
          `).join('');
    }
    batchDiv.hidden = false;
  } catch (error) { showError(error); }
});

async function undoBatch(batchId) {
  const userId = userIdInput.value.trim();
  if (!userId || !confirm('Отменить импорт? Факты, подтверждённые вручную, не будут удалены.')) return;
  try {
    await asJson(await fetchWithTimeout(`/v1/users/${userId}/fact-import-batches/${batchId}/undo`, {
      method: 'POST', headers: headers(true),
    }));
    await loadProfileFacts();
    document.querySelector('#fact-batch-history').hidden = true;
    showStatus('Импорт отменён.');
  } catch (error) { showError(error); }
}

document.querySelector('#profile-facts-list').addEventListener('click', async event => {
  const actionButton = event.target.closest('[data-action]');
  const action = actionButton?.dataset.action;
  if (!action) return;
  const editor = actionButton.closest('[data-fact-id]');
  if (!editor) return;
  const factId = editor.dataset.factId;

  if (action === 'toggle-menu') {
    openProfileFactMenuId = String(openProfileFactMenuId) === String(factId) ? null : factId;
    renderProfileFacts();
    return;
  }
  if (action === 'edit') {
    editingProfileFactId = factId;
    openProfileFactMenuId = null;
    renderProfileFacts();
    window.setTimeout(() => document.querySelector(`[data-fact-id="${CSS.escape(String(factId))}"] [data-field="name"]`)?.focus(), 0);
    return;
  }
  if (action === 'cancel-edit') {
    editingProfileFactId = null;
    renderProfileFacts();
    return;
  }

  const userId = userIdInput.value.trim();
  if (!userId) return;
  actionButton.disabled = true;
  try {
    if (action === 'save') {
      const category = editor.querySelector('[data-field="category"]')?.value.trim();
      const name = editor.querySelector('[data-field="name"]')?.value.trim();
      const value = editor.querySelector('[data-field="value"]')?.value.trim();
      const isVerified = editor.querySelector('[data-field="is_verified"]').checked;

      if (!category || !name || !value) {
        throw new Error(currentLanguage === 'en' ? 'Category, name and value are required.' : 'Категория, название и значение обязательны.');
      }

      const response = await fetchWithTimeout(
        `/v1/users/${userId}/facts/${factId}`,
        {
          method: 'PUT',
          headers: headers(true),
          body: JSON.stringify({
            category,
            name,
            value,
            is_verified: isVerified
          })
        }
      );

      if (!response.ok) await asJson(response);

      /*
       * Update the local list immediately after a successful save.
       * This makes the edited row collapse back into the normal list
       * without requiring a manual page refresh.
       */
      profileFactsCache = profileFactsCache.map(fact => (
        String(fact.id) === String(factId)
          ? {...fact, category, name, value, is_verified: isVerified}
          : fact
      ));

      editingProfileFactId = null;
      openProfileFactMenuId = null;
      populateProfileFactCategoryFilter(profileFactsCache);
      renderProfileFacts();
      showStatus(currentLanguage === 'en' ? 'Fact saved.' : 'Факт сохранён.');

      /*
       * Then synchronize with the server once more in the background.
       * The immediate render above remains responsive even if this GET
       * takes a little longer.
       */
      window.setTimeout(() => {
        loadProfileFacts().catch(showError);
      }, 250);

      return;
    } else if (action === 'delete') {
      const fact = profileFactsCache.find(item => String(item.id) === String(factId));
      const label = fact?.name || (currentLanguage === 'en' ? 'this fact' : 'этот факт');
      const confirmed = window.confirm(
        currentLanguage === 'en'
          ? `Delete “${label}”? This action cannot be undone.`
          : `Удалить «${label}»? Это действие нельзя отменить.`
      );
      if (!confirmed) return;
      const response = await fetchWithTimeout(
        `/v1/users/${userId}/facts/${factId}`,
        {method: 'DELETE', headers: headers(false)}
      );
      if (!response.ok) await asJson(response);
      openProfileFactMenuId = null;
      showStatus(currentLanguage === 'en' ? 'Fact deleted.' : 'Факт удалён.');
    }
    await loadProfileFacts();
  } catch (error) {
    showError(error);
  } finally {
    actionButton.disabled = false;
  }
});

document.addEventListener('click', event => {
  if (!event.target.closest('.fact-menu-wrap') && openProfileFactMenuId !== null) {
    openProfileFactMenuId = null;
    renderProfileFacts();
  }
});

function initializeDashboardSubsections() {
  const sections = Array.from(document.querySelectorAll('section.step'));

  sections.forEach(section => {
    /*
     * Some subsections are already wrapped in their own container
     * (for example #profile-facts-panel and #email-integration-panel).
     * Make those self-contained collapsible groups first.
     */
    Array.from(section.children).forEach(child => {
      if (!(child instanceof HTMLElement)) return;
      if (child.matches('h2, h3')) return;

      const directHeading = Array.from(child.children)
        .find(node => node.matches?.('h3'));

      if (!directHeading) return;

      child.classList.add('dashboard-nested-subsection');
      directHeading.classList.add('dashboard-subsection-toggle');
      directHeading.setAttribute('role', 'button');
      directHeading.setAttribute('tabindex', '0');
      directHeading.setAttribute('aria-expanded', 'false');

      const body = document.createElement('div');
      body.className = 'dashboard-subsection-body';
      body.hidden = true;

      let sibling = directHeading.nextSibling;
      while (sibling) {
        const next = sibling.nextSibling;
        body.append(sibling);
        sibling = next;
      }
      child.append(body);
    });

    /*
     * Top-level H2/H3 blocks. A nested subsection container is treated as
     * a boundary so, for example, "Личные данные" doesn't swallow
     * "Факты обо мне".
     */
    const isBoundary = node => (
      node?.nodeType === Node.ELEMENT_NODE
      && (
        node.matches('h2, h3')
        || node.classList?.contains('dashboard-nested-subsection')
      )
    );

    Array.from(section.children).forEach(child => {
      if (!child.matches?.('h2, h3')) return;
      if (child.matches('h2')) return; // top level: the panel title is always open
      if (child.classList.contains('dashboard-subsection-toggle')) return;

      child.classList.add('dashboard-subsection-toggle');
      child.setAttribute('role', 'button');
      child.setAttribute('tabindex', '0');
      child.setAttribute('aria-expanded', 'false');

      const body = document.createElement('div');
      body.className = 'dashboard-subsection-body';
      body.hidden = true;

      let sibling = child.nextSibling;
      while (sibling && !isBoundary(sibling)) {
        const next = sibling.nextSibling;
        body.append(sibling);
        sibling = next;
      }
      child.after(body);
    });
  });

  // Stable keys: panel name + position among that panel's collapsible headings (independent
  // of the interface language, unlike the heading text).
  const positions = new Map();
  document.querySelectorAll('section.step').forEach(section => {
    const panel = section.dataset.panel || 'panel';
    section.querySelectorAll('.dashboard-subsection-toggle').forEach(heading => {
      const position = positions.get(panel) || 0;
      positions.set(panel, position + 1);
      heading.dataset.stateKey = `${panel}:${position}`;
    });
  });

  const toggleSubsection = heading => {
    const body = heading.nextElementSibling;
    if (!body?.classList.contains('dashboard-subsection-body')) return;

    const shouldOpen = body.hidden;
    body.hidden = !shouldOpen;
    heading.setAttribute('aria-expanded', String(shouldOpen));
    saveSubsectionState(heading.dataset.stateKey, shouldOpen);
  };

  document.addEventListener('click', event => {
    const heading = event.target.closest('.dashboard-subsection-toggle');
    if (!heading) return;
    toggleSubsection(heading);
  });

  document.addEventListener('keydown', event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const heading = event.target.closest('.dashboard-subsection-toggle');
    if (!heading) return;
    event.preventDefault();
    toggleSubsection(heading);
  });
}

const SUBSECTION_STATE_KEY = 'dashboardSubsectionState';

function readSubsectionState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SUBSECTION_STATE_KEY) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (error) {
    return {}; // unreadable or blocked storage: every section starts collapsed
  }
}

function saveSubsectionState(key, open) {
  if (!key) return;
  try {
    const state = readSubsectionState();
    state[key] = open;
    localStorage.setItem(SUBSECTION_STATE_KEY, JSON.stringify(state));
  } catch (error) {
    console.warn('Could not remember the collapsed/expanded state:', error);
  }
}

// Collapsed by default; a section the user opened (or closed) before keeps that choice.
function applyStoredSubsectionState(root = document) {
  const state = readSubsectionState();
  root.querySelectorAll('.dashboard-subsection-toggle').forEach(heading => {
    const open = state[heading.dataset.stateKey] === true;
    heading.setAttribute('aria-expanded', String(open));
    const body = heading.nextElementSibling;
    if (body?.classList.contains('dashboard-subsection-body')) body.hidden = !open;
  });
}

function collapseAllPanels() {
  document.querySelectorAll('[data-panel]').forEach(node => {
    node.classList.remove('active');
  });
  document.querySelectorAll('[data-menu]').forEach(button => {
    button.classList.remove('active');
    button.setAttribute('aria-current', 'false');
    button.setAttribute('aria-expanded', 'false');
  });
}

function activatePanel(panelName, {toggle = false} = {}) {
  const menuButton = document.querySelector(`[data-menu="${CSS.escape(String(panelName))}"]`);
  const isCurrentlyExpanded = Boolean(menuButton?.classList.contains('active'));
  const shouldExpand = toggle ? !isCurrentlyExpanded : true;

  collapseAllPanels();

  if (!shouldExpand) return false;

  document.querySelectorAll('[data-panel]').forEach(node => {
    node.classList.toggle('active', node.dataset.panel === panelName);
  });
  document.querySelectorAll('[data-menu]').forEach(button => {
    const isActive = button.dataset.menu === panelName;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-current', isActive ? 'page' : 'false');
    button.setAttribute('aria-expanded', String(isActive));
  });
  return true;
}

const MATCHING_STATE_LABELS = {
  pending: ['ожидает', 'pending'],
  scheduled: ['запланирована', 'scheduled'],
  retry_scheduled: ['на повторе', 'retry scheduled'],
  running: ['выполняется', 'running'],
  completed: ['завершена', 'completed'],
  failed: ['с ошибкой', 'failed'],
  cancelled: ['отменена', 'cancelled'],
  interrupted: ['прервана', 'interrupted'],
  waiting_for_user: ['ждёт решения', 'awaiting user'],
  waiting_for_external_system: ['ждёт систему', 'awaiting system'],
};
const matchingStateLabel = state => {
  const pair = MATCHING_STATE_LABELS[state] || [state, state];
  return currentLanguage === 'en' ? pair[1] : pair[0];
};

async function loadMatchingQueue() {
  const summary = document.querySelector('#matching-queue-summary');
  const list = document.querySelector('#matching-queue-list');
  const stateNode = document.querySelector('#matching-queue-state');
  const data = await asJson(await fetchWithTimeout('/v1/matching/queue', {headers: headers(false)}));
  stateNode.textContent = data.paused
    ? (currentLanguage === 'en'
      ? 'Queue is paused — the worker is not taking new tasks.'
      : 'Очередь остановлена — воркер не берёт новые задачи.')
    : (currentLanguage === 'en'
      ? 'Queue is running.'
      : 'Очередь обрабатывается.');
  summary.replaceChildren();
  Object.keys(MATCHING_STATE_LABELS).forEach(key => {
    const count = (data.counts || {})[key];
    if (count == null) return;
    summary.append(element('span', `${matchingStateLabel(key)}: ${count}`, 'badge'));
  });
  list.replaceChildren();
  const tasks = data.tasks || [];
  if (tasks.length === 0) {
    list.append(element('div', currentLanguage === 'en'
      ? 'No active matching tasks.'
      : 'Нет активных задач матчинга.', 'hint'));
    return;
  }
  tasks.forEach(task => {
    const row = element('div');
    row.style.cssText = 'display:flex;gap:12px;flex-wrap:wrap;padding:8px 0;border-bottom:1px solid #2b3859;font-size:13px;';
    const position = task.queue_position ? `#${task.queue_position}` : '—';
    const app = task.application_id ? task.application_id.slice(0, 8) : '—';
    const when = new Date(task.created_at).toLocaleTimeString();
    row.append(
      element('span', position),
      element('span', app),
      element('span', matchingStateLabel(task.state)),
      element('span', `pr ${task.priority} · try ${task.attempt_number}`),
      element('span', when)
    );
    list.append(row);
  });
}

async function setMatchingQueuePaused(paused) {
  const endpoint = paused ? '/v1/matching/queue/pause' : '/v1/matching/queue/resume';
  await asJson(await fetchWithTimeout(endpoint, {method: 'POST', headers: headers(false)}));
  await loadMatchingQueue();
}

async function clearMatchingQueue() {
  const confirmed = window.confirm(currentLanguage === 'en'
    ? 'Stop the matching queue and cancel all pending tasks?'
    : 'Остановить очередь матчинга и отменить все ожидающие задачи?');
  if (!confirmed) return;
  const result = await asJson(await fetchWithTimeout('/v1/matching/queue/clear', {method: 'POST', headers: headers(false)}));
  showStatus(currentLanguage === 'en'
    ? `Queue cleared: ${result.cancelled} cancelled, ${result.interrupted} interrupted.`
    : `Очередь очищена: отменено ${result.cancelled}, прервано ${result.interrupted}.`);
  await loadMatchingQueue();
}

document.querySelector('#matching-queue-pause').addEventListener('click', () => setMatchingQueuePaused(true).catch(showError));
document.querySelector('#matching-queue-resume').addEventListener('click', () => setMatchingQueuePaused(false).catch(showError));
document.querySelector('#matching-queue-clear').addEventListener('click', () => clearMatchingQueue().catch(showError));
document.querySelector('#matching-queue-refresh').addEventListener('click', () => loadMatchingQueue().catch(showError));

document.querySelectorAll('[data-menu]').forEach(button => {
  button.addEventListener('click', () => {
    const expanded = activatePanel(button.dataset.menu); // top-level tabs never collapse
    if (!expanded) return;

    if (button.dataset.menu === 'vacancies') loadSavedVacancies().catch(showError);
    if (button.dataset.menu === 'blacklist') loadCompanyBlacklist().catch(showError);
    if (button.dataset.menu === 'queue') loadMatchingQueue().catch(showError);
  });
});

function createMatchMeter(matchScore) {
  const score = Math.max(0, Math.min(100, Number(matchScore) || 0));
  const meter = element('div', undefined, 'match-meter');
  meter.style.setProperty('--match', String(score));
  meter.setAttribute('role', 'progressbar');
  meter.setAttribute('aria-label', currentLanguage === 'en' ? 'Match score' : 'Степень соответствия');
  meter.setAttribute('aria-valuemin', '0');
  meter.setAttribute('aria-valuemax', '100');
  meter.setAttribute('aria-valuenow', String(score));
  meter.append(element('div', currentLanguage === 'en' ? `${score}% match` : `${score}% подходит`));
  const track = element('div', undefined, 'match-meter__track');
  track.append(element('div', undefined, 'match-meter__fill'));
  meter.append(track);
  return meter;
}

const knownBrowserSiteLabels = {headhunter: 'hh.ru', linkedin: 'LinkedIn'};
let browserSessionStatuses = {};

function browserSiteLabel(site) {
  return browserSessionStatuses[site]?.site_name || knownBrowserSiteLabels[site] || site;
}

function ensureBrowserSessionCard(session) {
  const container = document.querySelector('#browser-sessions');
  let card = Array.from(container.querySelectorAll('.session-card')).find(
    candidate => candidate.dataset.site === session.site_key
  );
  if (card) return card;
  card = document.createElement('article');
  card.className = 'session-card';
  card.dataset.site = session.site_key;
  const title = document.createElement('h3');
  title.textContent = session.site_name || knownBrowserSiteLabels[session.site_key] || session.site_key;
  const status = document.createElement('div');
  status.className = 'session-status';
  status.textContent = 'Проверяем…';
  const details = document.createElement('div');
  details.className = 'task-state';
  const actions = document.createElement('div');
  actions.className = 'actions';
  const login = document.createElement('button');
  login.type = 'button';
  login.dataset.action = 'login';
  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.className = 'primary';
  confirm.dataset.action = 'confirm';
  confirm.textContent = 'Я вошёл — сохранить';
  confirm.hidden = true;
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.dataset.action = 'cancel';
  cancel.textContent = 'Отмена';
  cancel.hidden = true;
  actions.append(login, confirm, cancel);
  card.append(title, status, details, actions);
  container.append(card);
  return card;
}

const BROWSER_SESSION_STATE_LABELS = {
  DISCONNECTED: 'Не подключено',
  LOGIN_REQUIRED: 'Нужен вход',
  AUTHENTICATING: 'Ожидаем вход',
  AUTHENTICATED: 'Сессия сохранена, проверяем',
  READY: 'Готово',
  EXPIRED: 'Сессия истекла',
  REAUTH_REQUIRED: 'Нужна повторная авторизация',
};

function browserSessionState(session) {
  // The backend state machine is authoritative; the flags only cover payloads without it.
  if (session.state) return session.state;
  if (session.is_waiting_for_login) return 'AUTHENTICATING';
  return session.is_authorized ? 'AUTHENTICATED' : 'DISCONNECTED';
}

function renderBrowserSessionStatus(session) {
  browserSessionStatuses[session.site_key] = session;
  const site = session.site_key;
  const card = ensureBrowserSessionCard(session);
  const status = card.querySelector('.session-status');
  const details = card.querySelector('.task-state');
  const loginButton = card.querySelector('[data-action="login"]');
  const confirmButton = card.querySelector('[data-action="confirm"]');
  const cancelButton = card.querySelector('[data-action="cancel"]');
  const label = browserSiteLabel(site);
  const state = browserSessionState(session);
  const waiting = state === 'AUTHENTICATING';
  const usable = state === 'READY' || state === 'AUTHENTICATED';
  const verifiedAt = session.last_verified_at
    ? new Date(session.last_verified_at).toLocaleString('ru-RU')
    : null;
  status.classList.toggle('authorized', usable);
  status.dataset.state = state;
  status.textContent = BROWSER_SESSION_STATE_LABELS[state] || state;
  confirmButton.hidden = !waiting;
  cancelButton.hidden = !waiting;
  loginButton.hidden = waiting;
  if (waiting) {
    details.textContent = 'Завершите вход в открытом окне, затем сохраните сессию.';
  } else if (state === 'READY') {
    details.textContent = verifiedAt
      ? `Сайт подтвердил сессию ${verifiedAt}.`
      : 'Сайт подтвердил сессию.';
    loginButton.textContent = `Войти заново в ${label}`;
  } else if (state === 'AUTHENTICATED') {
    details.textContent = session.check_error
      ? `Сессия сохранена, но сейчас не удалось проверить сайт: ${session.check_error}.`
      : 'Зашифрованная сессия сохранена; проверка сайтом ещё не выполнялась.';
    loginButton.textContent = `Войти заново в ${label}`;
  } else {
    details.textContent = session.last_error
      ? `${session.last_error}. Нажмите кнопку входа и авторизуйтесь заново.`
      : 'Нажмите кнопку входа — откроется отдельное окно сайта.';
    loginButton.textContent = `Войти в ${label}`;
  }
}

function renderBrowserSessionsWithoutUser() {
  browserSessionStatuses = {};
  document.querySelector('#browser-sessions').replaceChildren();
  Object.entries(knownBrowserSiteLabels).forEach(([site, siteName]) => renderBrowserSessionStatus({
    site_key: site, site_name: siteName, is_custom: false,
    is_authorized: false, is_waiting_for_login: false
  }));
  document.querySelectorAll('.session-card .session-status').forEach(node => {
    node.textContent = 'Сначала создайте пользователя';
  });
}

async function loadBrowserSessionStatuses(probe = false) {
  const userId = userIdInput.value.trim();
  if (!userId) {
    renderBrowserSessionsWithoutUser();
    return [];
  }
  const probeQuery = probe ? '?probe=true' : '';
  const sessions = await asJson(await fetch(`/v1/users/${userId}/browser-sessions${probeQuery}`, {
    headers: headers(false)
  }));
  browserSessionStatuses = {};
  document.querySelector('#browser-sessions').replaceChildren();
  sessions.forEach(renderBrowserSessionStatus);
  return sessions;
}

async function loadEmailIntegration() {
  const userId = userIdInput.value.trim();
  const state = document.querySelector('#email-integration-state');
  document.querySelector('#email-password').value = '';
  if (!userId) {
    state.textContent = '';
    return null;
  }
  const integration = await asJson(await fetchWithTimeout(
    `/v1/users/${userId}/email-integration`,
    {headers: headers(false)}
  ));
  if (!integration) {
    state.textContent = currentLanguage === 'en' ? 'Email is not configured.' : 'Почта не настроена.';
    return null;
  }
  document.querySelector('#email-host').value = integration.host;
  document.querySelector('#email-port').value = String(integration.port);
  document.querySelector('#email-username').value = integration.username;
  document.querySelector('#email-mailbox').value = integration.mailbox;
  document.querySelector('#email-use-ssl').checked = integration.use_ssl;
  document.querySelector('#email-enabled').checked = integration.enabled;
  state.textContent = currentLanguage === 'en'
    ? 'Email settings are saved. Leave the password blank to keep it unchanged.'
    : 'Настройки почты сохранены. Оставьте пароль пустым, чтобы не менять его.';
  return integration;
}

document.querySelector('#email-gmail-preset').addEventListener('click', () => {
  document.querySelector('#email-host').value = 'imap.gmail.com';
  document.querySelector('#email-port').value = '993';
  document.querySelector('#email-mailbox').value = 'INBOX';
  document.querySelector('#email-use-ssl').checked = true;
});

document.querySelector('#email-integration-form').addEventListener('submit', async event => {
  event.preventDefault();
  const userId = userIdInput.value.trim();
  if (!userId) {
    showError(new Error(currentLanguage === 'en' ? 'Set User ID first' : 'Сначала укажите User ID'));
    return;
  }
  const password = document.querySelector('#email-password');
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    await asJson(await fetchWithTimeout(`/v1/users/${userId}/email-integration`, {
      method: 'PUT',
      headers: headers(true),
      body: JSON.stringify({
        host: document.querySelector('#email-host').value,
        port: Number(document.querySelector('#email-port').value),
        username: document.querySelector('#email-username').value,
        password: password.value || null,
        use_ssl: document.querySelector('#email-use-ssl').checked,
        mailbox: document.querySelector('#email-mailbox').value,
        enabled: document.querySelector('#email-enabled').checked
      })
    }));
    password.value = '';
    await loadEmailIntegration();
    showStatus(currentLanguage === 'en' ? 'Email settings saved.' : 'Настройки почты сохранены.');
  } catch (error) {
    showError(error);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector('#browser-sessions').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const card = button.closest('.session-card');
  const site = card?.dataset.site;
  if (!site) return;
  const action = button.dataset.action;
  const label = browserSiteLabel(site);
  if (action === 'login') {
    const loginWindow = window.open('about:blank', `job-assistant-login-${site}`);
    try {
      const userId = userIdInput.value.trim();
      if (!userId) throw new Error('Сначала создайте или укажите User ID');
      if (!loginWindow) {
        throw new Error('Браузер заблокировал окно входа. Разрешите всплывающие окна для 127.0.0.1');
      }
      button.disabled = true;
      showStatus(`Открываем окно входа ${label}…`);
      await asJson(await fetch(`/v1/users/${userId}/browser-sessions/${encodeURIComponent(site)}/start`, {
        method: 'POST', headers: headers(false)
      }));
      const viewerUrl = new URL(`http://${window.location.hostname}:7900/vnc.html`);
      viewerUrl.searchParams.set('autoconnect', 'true');
      viewerUrl.searchParams.set('resize', 'scale');
      viewerUrl.searchParams.set('path', 'websockify');
      loginWindow.location.replace(viewerUrl.toString());
      renderBrowserSessionStatus({
        site_key: site,
        site_name: label,
        is_authorized: Boolean(browserSessionStatuses[site]?.is_authorized),
        is_waiting_for_login: true
      });
      showStatus(`Войдите в ${label} в открывшемся окне, затем нажмите «Я вошёл — сохранить».`);
    } catch (error) {
      if (loginWindow && !loginWindow.closed) loginWindow.close();
      showError(error);
    }
    finally { button.disabled = false; }
  } else if (action === 'confirm') {
    try {
      const userId = userIdInput.value.trim();
      if (!userId) throw new Error('Сначала создайте или укажите User ID');
      button.disabled = true;
      showStatus(`Проверяем вход и сохраняем сессию ${label}…`);
      await asJson(await fetch(`/v1/users/${userId}/browser-sessions/${encodeURIComponent(site)}/confirm`, {
        method: 'POST', headers: headers(false)
      }));
      await loadBrowserSessionStatuses();
      showStatus(`${label}: авторизация сохранена.`);
    } catch (error) {
      showError(error);
      // a rejected confirm usually means the backend state moved on: show the real state
      loadBrowserSessionStatuses().catch(refreshError => console.warn(refreshError));
    }
    finally { button.disabled = false; }
  } else if (action === 'cancel') {
    try {
      const userId = userIdInput.value.trim();
      if (!userId) throw new Error('Сначала создайте или укажите User ID');
      await asJson(await fetch(`/v1/users/${userId}/browser-sessions/${encodeURIComponent(site)}/cancel`, {
        method: 'POST', headers: headers(false)
      }));
      await loadBrowserSessionStatuses();
      showStatus(`Вход в ${label} отменён.`);
    } catch (error) { showError(error); }
  }
});

document.querySelector('#add-site-definition').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  try {
    const userId = userIdInput.value.trim();
    if (!userId) throw new Error('Сначала создайте или укажите User ID');
    const siteKey = document.querySelector('#site-definition-key').value.trim();
    const name = document.querySelector('#site-definition-name').value.trim();
    const loginUrl = document.querySelector('#site-definition-login-url').value.trim();
    const allowedHosts = document.querySelector('#site-definition-hosts').value
      .split(',').map(value => value.trim()).filter(Boolean);
    button.disabled = true;
    const definition = await asJson(await fetch(`/v1/users/${userId}/site-definitions`, {
      method: 'POST', headers: headers(true), body: JSON.stringify({
        site_key: siteKey, name, login_url: loginUrl, allowed_hosts: allowedHosts,
        authorization_rules: {}
      })
    }));
    document.querySelector('#site-definition-state').textContent = `${definition.name}: сайт добавлен.`;
    await loadBrowserSessionStatuses();
    await loadSiteDefinitionsForFields();
  } catch (error) { showError(error); }
  finally { button.disabled = false; }
});

const effectiveValueSourceLabels = {
  application_override: 'переопределение отклика',
  site_field_override: 'переопределение поля',
  site_override: 'переопределение сайта',
  resume: 'резюме', global: 'общее значение', generated: 'сгенерировано'
};

async function loadSiteDefinitionsForFields() {
  const selectNode = document.querySelector('#site-field-site');
  const previous = selectNode.value;
  selectNode.replaceChildren(new Option('Выберите сайт', ''));
  const userId = userIdInput.value.trim();
  if (!userId) return;
  const definitions = await asJson(await fetch(`/v1/users/${userId}/site-definitions`, {
    headers: headers(false)
  }));
  definitions.forEach(definition => {
    selectNode.append(new Option(definition.name, definition.id));
  });
  if (definitions.some(definition => definition.id === previous)) selectNode.value = previous;
}

async function loadEffectiveSiteFieldValue(field, output) {
  const userId = userIdInput.value.trim();
  const response = await fetch(
    `/v1/users/${userId}/site-fields/${field.id}/effective-value`,
    {headers: headers(false)}
  );
  if (response.status === 404) {
    output.textContent = 'Эффективное значение: не настроено';
    return;
  }
  if (response.status === 403) {
    output.textContent = 'Эффективное значение: скрыто, требуется явная проверка';
    return;
  }
  const effective = await asJson(response);
  output.textContent = `Эффективное значение: ${effective.value} · источник: ${effectiveValueSourceLabels[effective.source] || effective.source}${effective.requires_review ? ' · требуется проверка' : ''}`;
}

function renderSiteFieldMapping(siteDefinitionId, field) {
  const card = element('article', undefined, 'session-card');
  card.dataset.fieldId = field.id;
  card.append(element('h3', field.label || field.field_key));
  card.append(element('div', `${field.field_type}${field.is_required ? ' · обязательное' : ''}`, 'hint'));

  const mappingRow = element('div', undefined, 'row');
  const keyBox = element('div');
  keyBox.append(element('label', 'Общее значение'));
  const keyInputNode = document.createElement('input');
  keyInputNode.className = 'site-field-value-key';
  keyInputNode.setAttribute('list', 'site-field-known-keys');
  keyInputNode.placeholder = 'например, contact.email';
  keyInputNode.value = field.mapping?.value_key || field.semantic_key || '';
  keyBox.append(keyInputNode);
  const transformBox = element('div');
  transformBox.append(element('label', 'Преобразование'));
  const transform = document.createElement('select');
  transform.className = 'site-field-transform';
  [['identity', 'Без изменений'], ['trim', 'Убрать пробелы'], ['lowercase', 'Строчные'], ['uppercase', 'Прописные']]
    .forEach(([value, label]) => transform.append(new Option(label, value)));
  transform.value = field.mapping?.transformation?.kind || 'identity';
  transformBox.append(transform);
  mappingRow.append(keyBox, transformBox);
  card.append(mappingRow);

  const reviewLabel = element('label', undefined, 'chip');
  const review = document.createElement('input');
  review.type = 'checkbox';
  review.className = 'site-field-review';
  review.checked = Boolean(field.mapping?.review_required);
  reviewLabel.append(review, document.createTextNode(' Проверять перед заполнением'));
  card.append(reviewLabel);

  const overrideRow = element('div', undefined, 'row');
  const modeBox = element('div');
  modeBox.append(element('label', 'Источник'));
  const mode = document.createElement('select');
  mode.className = 'site-field-mode';
  mode.append(
    new Option('Использовать общее значение', 'common'),
    new Option('Переопределить для сайта', 'site'),
    new Option('Переопределить только это поле', 'field')
  );
  modeBox.append(mode);
  const valueBox = element('div');
  valueBox.append(element('label', 'Значение переопределения'));
  const overrideValue = document.createElement('input');
  overrideValue.className = 'site-field-override-value';
  overrideValue.disabled = true;
  valueBox.append(overrideValue);
  overrideRow.append(modeBox, valueBox);
  card.append(overrideRow);
  mode.addEventListener('change', () => {
    overrideValue.disabled = mode.value === 'common';
  });

  const sensitiveLabel = element('label', undefined, 'chip');
  const sensitive = document.createElement('input');
  sensitive.type = 'checkbox';
  sensitive.className = 'site-field-sensitive';
  sensitiveLabel.append(sensitive, document.createTextNode(' Чувствительное значение'));
  card.append(sensitiveLabel);

  const actions = element('div', undefined, 'actions');
  const save = element('button', 'Сохранить сопоставление', 'primary');
  save.type = 'button';
  const effective = element('div', 'Эффективное значение: проверяем…', 'task-state');
  actions.append(save);
  card.append(actions, effective);
  save.addEventListener('click', async () => {
    try {
      const userId = userIdInput.value.trim();
      const valueKey = keyInputNode.value.trim();
      if (!valueKey) throw new Error('Укажите ключ общего значения');
      save.disabled = true;
      await asJson(await fetch(`/v1/users/${userId}/site-fields/${field.id}/mapping`, {
        method: 'PUT', headers: headers(true), body: JSON.stringify({
          value_key: valueKey,
          transformation: {kind: transform.value},
          review_required: review.checked
        })
      }));
      if (mode.value !== 'common') {
        if (!overrideValue.value.trim()) throw new Error('Введите значение переопределения');
        await asJson(await fetch(`/v1/users/${userId}/site-definitions/${siteDefinitionId}/overrides`, {
          method: 'PUT', headers: headers(true), body: JSON.stringify({
            value_key: valueKey,
            serialized_value: overrideValue.value,
            is_sensitive: sensitive.checked,
            site_field_id: mode.value === 'field' ? field.id : null
          })
        }));
        overrideValue.value = '';
      }
      await loadEffectiveSiteFieldValue(field, effective);
      document.querySelector('#site-field-state').textContent = `${field.label || field.field_key}: сохранено.`;
    } catch (error) { showError(error); }
    finally { save.disabled = false; }
  });
  loadEffectiveSiteFieldValue(field, effective).catch(error => {
    effective.textContent = `Эффективное значение: ошибка (${error.message})`;
  });
  return card;
}

async function loadSiteFieldMappings() {
  const userId = userIdInput.value.trim();
  const siteDefinitionId = document.querySelector('#site-field-site').value;
  if (!userId) throw new Error('Сначала создайте или укажите User ID');
  if (!siteDefinitionId) throw new Error('Выберите сайт');
  const fields = await asJson(await fetch(
    `/v1/users/${userId}/site-definitions/${siteDefinitionId}/fields`,
    {headers: headers(false)}
  ));
  const container = document.querySelector('#site-field-mappings');
  container.replaceChildren();
  if (!fields.length) {
    container.append(element('div', 'Поля ещё не обнаружены. Они появятся после анализа формы сайта.', 'empty'));
  } else {
    fields.forEach(field => container.append(renderSiteFieldMapping(siteDefinitionId, field)));
  }
  document.querySelector('#site-field-state').textContent = `Найдено полей: ${fields.length}`;
}

document.querySelector('#load-site-fields').addEventListener('click', () => {
  loadSiteFieldMappings().catch(showError);
});

document.querySelector('#create-user').addEventListener('click', async () => {
  try {
    keyInput.dispatchEvent(new Event('change'));
    const name = prompt('Ваше имя для профиля:', 'Candidate') || 'Candidate';
    const user = await asJson(await fetch('/v1/users', {
      method: 'POST', headers: headers(true), body: JSON.stringify({display_name: name})
    }));
    userIdInput.value = user.id;
    localStorage.setItem('dashboardUserId', user.id);
    await loadBrowserSessionStatuses();
    await loadSiteDefinitionsForFields();
    await loadSavedVacancies();
    await loadCvFiles();
    showStatus(`Пользователь создан: ${user.id}`);
  } catch (error) { showError(error); }
});

let cvFiles = [];

function mergeSkills(...skillLists) {
  const merged = [];
  const seen = new Set();
  skillLists.flat().forEach(rawSkill => {
    const skill = String(rawSkill || '').trim();
    const key = skill.toLocaleLowerCase();
    if (!skill || seen.has(key)) return;
    seen.add(key);
    merged.push(skill);
  });
  return merged;
}

function selectedSkills() {
  return [...document.querySelectorAll('#skills-chips input:checked')].map(input => input.value);
}

function renderSkills(skills) {
  const chips = document.querySelector('#skills-chips'); chips.replaceChildren();
  mergeSkills(skills).forEach(skill => {
    const chip = element('label', undefined, 'chip');
    const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.checked = true; checkbox.value = skill;
    chip.append(checkbox, document.createTextNode(skill));
    chips.append(chip);
  });
}

function addManualSkill() {
  const input = document.querySelector('#manual-skill');
  const skill = input.value.trim();
  if (!skill) return;
  renderSkills(mergeSkills(selectedSkills(), skill));
  input.value = '';
  input.focus();
}

document.querySelector('#add-manual-skill').addEventListener('click', addManualSkill);
document.querySelector('#manual-skill').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    addManualSkill();
  }
});

function renderResumeProfile(cvFile) {
  const draftBlock = document.querySelector('#draft-block');
  const ragButton = document.querySelector('#send-resume-to-rag');
  ragButton.disabled = !cvFile?.analyzed_at;
  ragButton.title = cvFile?.analyzed_at
    ? 'Отправить подтверждённое резюме в коллекцию RAG'
    : 'Сначала проанализируйте и подтвердите резюме';
  if (!cvFile) {
    draftBlock.style.display = 'none';
    document.querySelector('#resume-state').textContent = 'Загрузите хотя бы одно резюме.';
    return;
  }
  renderSkills(cvFile.skills || []);
  document.querySelector('#summary-text').value = cvFile.experience_summary || '';
  document.querySelector('#keywords-text').value = cvFile.search_keywords || '';
  document.querySelector('#experience-years').value = cvFile.years_of_experience ?? '';
  draftBlock.style.display = 'block';
  document.querySelector('#resume-state').textContent = cvFile.analyzed_at
    ? `Активно: ${cvFile.original_filename}. Анализ сохранён, навыков: ${cvFile.skills.length}.`
    : `${cvFile.original_filename}: анализ ещё не сохранён.`;
  const ragLabels = currentLanguage === 'en'
    ? {
        not_configured: 'not configured', not_scheduled: 'not scheduled',
        scheduled: 'scheduled', running: 'in progress', retry_scheduled: 'retry scheduled',
        completed: 'synchronized', failed: 'failed'
      }
    : {
        not_configured: 'не настроен', not_scheduled: 'не запланировано',
        scheduled: 'запланировано', running: 'выполняется',
        retry_scheduled: 'назначен повтор', completed: 'синхронизировано', failed: 'ошибка'
      };
  const ragState = ragLabels[cvFile.rag_sync_status] || cvFile.rag_sync_status;
  const ragFailure = cvFile.rag_sync_failure_code
    ? ` (${currentLanguage === 'en' ? 'code' : 'код'}: ${cvFile.rag_sync_failure_code})`
    : '';
  document.querySelector('#resume-state').textContent += ` RAG: ${ragState}${ragFailure}.`;
}

function renderCvFileSelectors(preferredCvFileId = null) {
  const resumeSelector = document.querySelector('#resume-selector');
  const searchSelector = document.querySelector('#search-resume-selector');
  const activeCvFile = cvFiles.find(cvFile => cvFile.is_active);
  const selectedCvFileId = preferredCvFileId || activeCvFile?.id || cvFiles[0]?.id || '';
  resumeSelector.replaceChildren();
  searchSelector.replaceChildren();
  if (!cvFiles.length) {
    resumeSelector.append(new Option('Нет загруженных резюме', ''));
    searchSelector.append(new Option('Сначала загрузите и проанализируйте резюме', ''));
    renderResumeProfile(null);
    return;
  }
  cvFiles.forEach(cvFile => {
    const suffix = `${cvFile.is_active ? ' — активно' : ''}${cvFile.analyzed_at ? '' : ' — не проанализировано'}`;
    resumeSelector.append(new Option(`${cvFile.original_filename}${suffix}`, cvFile.id));
    if (cvFile.analyzed_at) {
      searchSelector.append(new Option(`${cvFile.original_filename}${cvFile.is_active ? ' — активно' : ''}`, cvFile.id));
    }
  });
  resumeSelector.value = selectedCvFileId;
  if ([...searchSelector.options].some(option => option.value === selectedCvFileId)) {
    searchSelector.value = selectedCvFileId;
  }
  renderResumeProfile(cvFiles.find(cvFile => cvFile.id === selectedCvFileId));
}

async function loadCvFiles(preferredCvFileId = null) {
  const userId = userIdInput.value.trim();
  if (!userId) { cvFiles = []; renderCvFileSelectors(); return []; }
  cvFiles = await asJson(await fetch(`/v1/users/${userId}/cv-files`, {headers: headers(false)}));
  renderCvFileSelectors(preferredCvFileId);
  return cvFiles;
}

async function selectCvFile(cvFileId) {
  const userId = userIdInput.value.trim();
  if (!cvFileId) return;
  await asJson(await fetch(`/v1/users/${userId}/active-cv-file`, {
    method: 'PUT', headers: headers(true), body: JSON.stringify({cv_file_id: cvFileId})
  }));
  cvFiles = cvFiles.map(cvFile => ({...cvFile, is_active: cvFile.id === cvFileId}));
  renderCvFileSelectors(cvFileId);
}

const personalAutofillFields = [
  {key: 'identity.full_name', id: 'profile-full-name', label: 'Полное имя', value_type: 'text', is_sensitive: false},
  {key: 'identity.first_name', id: 'profile-first-name', label: 'Имя', value_type: 'text', is_sensitive: false},
  {key: 'identity.last_name', id: 'profile-last-name', label: 'Фамилия', value_type: 'text', is_sensitive: false},
  {key: 'contact.email', id: 'profile-email', label: 'Email', value_type: 'email', is_sensitive: true},
  {key: 'contact.phone', id: 'profile-phone', label: 'Телефон', value_type: 'phone', is_sensitive: true},
  {key: 'contact.linkedin_url', id: 'profile-linkedin', label: 'LinkedIn URL', value_type: 'url', is_sensitive: false},
  {key: 'location.country', id: 'profile-country', label: 'Страна', value_type: 'text', is_sensitive: false},
  {key: 'location.city', id: 'profile-city', label: 'Город', value_type: 'text', is_sensitive: false}
];
const applicationAutofillFields = [
  {key: 'job_preferences.expected_salary', id: 'application-salary', label: 'Ожидаемая зарплата', value_type: 'decimal', is_sensitive: false},
  {key: 'job_preferences.currency', id: 'application-currency', label: 'Валюта', value_type: 'currency', is_sensitive: false},
  {key: 'job_preferences.notice_period', id: 'application-notice-period', label: 'Срок выхода', value_type: 'duration', is_sensitive: false},
  {key: 'job_preferences.relocation_ready', id: 'application-relocation', label: 'Готовность к переезду', value_type: 'boolean', is_sensitive: false}
];
let autofillValuesByKey = new Map();

async function loadAutofillValues() {
  const userId = userIdInput.value.trim();
  if (!userId) return;
  const values = await asJson(await fetch(
    `/v1/users/${userId}/autofill-values`, {headers: headers(false)}
  ));
  autofillValuesByKey = new Map(values.map(value => [value.key, value]));
  const knownKeys = document.querySelector('#site-field-known-keys');
  knownKeys.replaceChildren();
  [...new Set([
    ...personalAutofillFields.map(field => field.key),
    ...applicationAutofillFields.map(field => field.key),
    ...values.map(value => value.key)
  ])].sort().forEach(key => knownKeys.append(new Option(key, key)));
  [...personalAutofillFields, ...applicationAutofillFields].forEach(field => {
    document.querySelector(`#${field.id}`).value =
      autofillValuesByKey.get(field.key)?.serialized_value || '';
  });
  document.querySelector('#personal-data-state').textContent =
    `Сохранено личных полей: ${personalAutofillFields.filter(field => autofillValuesByKey.has(field.key)).length}. Требуют проверки: ${values.filter(value => value.requires_review).length}.`;
  document.querySelector('#store-sensitive-personal-data').checked =
    values.some(value => value.is_sensitive && personalAutofillFields.some(field => field.key === value.key));
  document.querySelector('#application-defaults-state').textContent =
    `Сохранено настроек отклика: ${applicationAutofillFields.filter(field => autofillValuesByKey.has(field.key)).length}`;
}

async function saveAutofillFields(fields) {
  const userId = userIdInput.value.trim();
  if (!userId) throw new Error('Сначала создайте или укажите User ID');
  for (const field of fields) {
      const serializedValue = document.querySelector(`#${field.id}`).value.trim();
      if (
        serializedValue && field.is_sensitive &&
        !document.querySelector('#store-sensitive-personal-data').checked
      ) throw new Error('Подтвердите зашифрованное хранение чувствительных данных');
      const existing = autofillValuesByKey.get(field.key);
      const endpoint = `/v1/users/${userId}/autofill-values/${encodeURIComponent(field.key)}`;
      if (serializedValue && existing) {
        await asJson(await fetch(endpoint, {
          method: 'PUT', headers: headers(true),
          body: JSON.stringify({serialized_value: serializedValue})
        }));
      } else if (serializedValue) {
        await asJson(await fetch(`/v1/users/${userId}/autofill-values`, {
          method: 'POST', headers: headers(true),
          body: JSON.stringify({...field, serialized_value: serializedValue, id: undefined})
        }));
      } else if (existing) {
        const response = await fetch(endpoint, {method: 'DELETE', headers: headers(false)});
        if (!response.ok) throw new Error(await response.text());
      }
  }
  await loadAutofillValues();
}

document.querySelector('#save-personal-data').addEventListener('click', async () => {
  try {
    await saveAutofillFields(personalAutofillFields);
    showStatus('Личные данные сохранены.');
  } catch (error) { showError(error); }
});

document.querySelector('#save-application-defaults').addEventListener('click', async () => {
  try {
    await saveAutofillFields(applicationAutofillFields);
    showStatus('Настройки отклика сохранены.');
  } catch (error) { showError(error); }
});

const LLM_PURPOSES = [
  {purpose: 'matching', prefix: 'llm-matching', label: 'Матчинг'},
  {purpose: 'materials', prefix: 'llm-materials', label: 'Генерация материалов'},
];

function llmEl(prefix, name) {
  return document.querySelector('#' + prefix + '-' + name);
}

async function loadSavedLlmPreference(purpose, prefix) {
  const userId = userIdInput.value.trim();
  if (!userId) return;
  const response = await fetch(`/v1/users/${userId}/llm-preference?purpose=${encodeURIComponent(purpose)}`, {headers: headers(false)});
  if (!response.ok) return;
  const preference = await response.json();
  if (!preference) return;
  llmEl(prefix, 'provider').value = preference.provider;
  const modelSelect = llmEl(prefix, 'model');
  modelSelect.replaceChildren(new Option(preference.model, preference.model));
  modelSelect.value = preference.model;
  llmEl(prefix, 'base-url').value = preference.base_url || '';
  updateLlmProviderFields(prefix);
  llmEl(prefix, 'state').textContent = `Сохранено: ${preference.provider} / ${preference.model}. Ключ уже настроен.`;
}

function updateLlmProviderFields(prefix) {
  const isCompatible = llmEl(prefix, 'provider').value === 'openai_compatible';
  llmEl(prefix, 'base-url-field').hidden = !isCompatible;
}

function loadLlmPreferences() {
  for (const p of LLM_PURPOSES) loadSavedLlmPreference(p.purpose, p.prefix);
}

async function loadModels(purpose, prefix) {
  const provider = llmEl(prefix, 'provider').value;
  const apiKey = llmEl(prefix, 'api-key').value.trim();
  const baseUrl = llmEl(prefix, 'base-url').value.trim();
  if (!apiKey) throw new Error('Введите API-ключ провайдера');
  if (provider === 'openai_compatible' && !baseUrl) throw new Error('Введите базовый URL API');
  showStatus('Получаем доступные модели…');
  const payload = await asJson(await fetch('/v1/llm/models', {
    method: 'POST', headers: headers(true), body: JSON.stringify({provider, api_key: apiKey, base_url: baseUrl || null})
  }));
  if (!payload.models.length) throw new Error('Провайдер не вернул подходящих моделей');
  const modelSelect = llmEl(prefix, 'model');
  const previousModel = modelSelect.value;
  modelSelect.replaceChildren();
  payload.models.forEach(model => modelSelect.append(new Option(model, model)));
  modelSelect.value = payload.models.includes(previousModel) ? previousModel : payload.models[0];
  showStatus(`Доступно моделей: ${payload.models.length}`);
}

async function saveLlm(purpose, prefix, label) {
  const userId = userIdInput.value.trim();
  if (!userId) throw new Error('Сначала создайте пользователя');
  const provider = llmEl(prefix, 'provider').value;
  const model = llmEl(prefix, 'model').value;
  if (!model) throw new Error('Выберите модель');
  const apiKey = llmEl(prefix, 'api-key').value.trim();
  const baseUrl = llmEl(prefix, 'base-url').value.trim();
  if (provider === 'openai_compatible' && !baseUrl) throw new Error('Введите базовый URL API');
  const body = {provider, model, base_url: baseUrl || null, purpose}; if (apiKey) body.api_key = apiKey;
  const preference = await asJson(await fetch(`/v1/users/${userId}/llm-preference`, {
    method: 'PUT', headers: headers(true), body: JSON.stringify(body)
  }));
  llmEl(prefix, 'api-key').value = '';
  llmEl(prefix, 'state').textContent = `Сохранено: ${preference.provider} / ${preference.model}.`;
  showStatus(`Настройка LLM (${label}) сохранена в зашифрованном виде.`);
}

for (const p of LLM_PURPOSES) {
  llmEl(p.prefix, 'provider').addEventListener('change', () => updateLlmProviderFields(p.prefix));
  updateLlmProviderFields(p.prefix);

  document.querySelector('#load-' + p.purpose + '-models').addEventListener('click', async () => {
    try { await loadModels(p.purpose, p.prefix); } catch (error) { showError(error); }
  });

  document.querySelector('#save-' + p.purpose + '-llm').addEventListener('click', async () => {
    try { await saveLlm(p.purpose, p.prefix, p.label); } catch (error) { showError(error); }
  });
}

document.querySelector('#upload-resume').addEventListener('click', async () => {
  try {
    sessionStorage.setItem('dashboardApiKey', keyInput.value);
    const userId = userIdInput.value.trim();
    if (!userId) throw new Error('Сначала создайте или укажите User ID');
    const files = [...document.querySelector('#resume-file').files];
    if (!files.length) throw new Error('Выберите хотя бы один файл резюме');
    showStatus(`Загружаем резюме: ${files.length}…`);
    let lastUploadedCvFile = null;
    for (const file of files) {
      const form = new FormData(); form.append('file', file);
      lastUploadedCvFile = await asJson(await fetch(`/v1/users/${userId}/cv-files`, {
        method: 'POST', headers: headers(false), body: form
      }));
    }
    await loadCvFiles(lastUploadedCvFile.id);
    await selectCvFile(lastUploadedCvFile.id);
    document.querySelector('#resume-file').value = '';
    showStatus(`Загружено резюме: ${files.length}. Выберите файл и запустите анализ.`);
  } catch (error) { showError(error); }
});

document.querySelector('#analyze-resume').addEventListener('click', async () => {
  try {
    const userId = userIdInput.value.trim();
    const cvFileId = document.querySelector('#resume-selector').value;
    if (!cvFileId) throw new Error('Выберите загруженное резюме');
    await selectCvFile(cvFileId);
    showStatus('Анализируем резюме (это может занять до минуты)…');
    const draft = await asJson(await fetch(`/v1/users/${userId}/cv-files/${cvFileId}/extract-profile`, {
      method: 'POST', headers: headers(false)
    }));
    const savedSkills = cvFiles.find(cvFile => cvFile.id === cvFileId)?.skills || [];
    renderSkills(mergeSkills(savedSkills, draft.skills));
    document.querySelector('#summary-text').value = draft.experience_summary || '';
    document.querySelector('#keywords-text').value = draft.search_keywords || '';
    document.querySelector('#experience-years').value = draft.years_of_experience ?? '';
    document.querySelector('#draft-block').style.display = 'block';
    showStatus(`Найдено навыков: ${draft.skills.length}. Проверьте список и подтвердите.`);
  } catch (error) { showError(error); }
});

document.querySelector('#delete-resume').addEventListener('click', async () => {
  try {
    const userId = userIdInput.value.trim();
    const cvFileId = document.querySelector('#resume-selector').value;
    const cvFile = cvFiles.find(item => item.id === cvFileId);
    if (!userId || !cvFile) throw new Error('Выберите загруженное резюме');
    const question = currentLanguage === 'en'
      ? `Delete resume "${cvFile.original_filename}"? Applications using it will no longer have a selected resume.`
      : `Удалить резюме «${cvFile.original_filename}»? В откликах, где оно выбрано, резюме будет сброшено.`;
    if (!window.confirm(question)) return;
    const response = await fetch(`/v1/users/${userId}/cv-files/${cvFileId}`, {
      method: 'DELETE', headers: headers(false)
    });
    if (!response.ok) await asJson(response);
    await loadCvFiles();
    showStatus(currentLanguage === 'en' ? 'Resume deleted.' : 'Резюме удалено.');
  } catch (error) { showError(error); }
});

document.querySelector('#send-resume-to-rag').addEventListener('click', async () => {
  try {
    const userId = userIdInput.value.trim();
    const cvFileId = document.querySelector('#resume-selector').value;
    const cvFile = cvFiles.find(item => item.id === cvFileId);
    if (!userId || !cvFile) throw new Error('Выберите загруженное резюме');
    if (!cvFile.analyzed_at) throw new Error('Сначала проанализируйте и подтвердите резюме');
    showStatus(`Отправляем «${cvFile.original_filename}» в RAG…`);
    const result = await asJson(await fetch(
      `/v1/users/${userId}/cv-files/${cvFileId}/rag-sync`,
      {method: 'POST', headers: headers(false)}
    ));
    showStatus(currentLanguage === 'en'
      ? `RAG synchronization scheduled. Status: ${result.status}.`
      : `Синхронизация с RAG запланирована. Статус: ${result.status}.`);
    await loadCvFiles(cvFileId);
  } catch (error) { showError(error); }
});

document.querySelector('#confirm-facts').addEventListener('click', async () => {
  try {
    const userId = userIdInput.value.trim();
    const cvFileId = document.querySelector('#resume-selector').value;
    if (!cvFileId) throw new Error('Выберите резюме');
    const skills = selectedSkills();
    const experience_summary = document.querySelector('#summary-text').value;
    const search_keywords = document.querySelector('#keywords-text').value.trim();
    const yearsText = document.querySelector('#experience-years').value;
    const years_of_experience = yearsText ? Number(yearsText) : null;
    showStatus('Сохраняем подтверждённые факты…');
    await asJson(await fetch(`/v1/users/${userId}/cv-files/${cvFileId}/profile`, {
      method: 'PUT', headers: headers(true),
      body: JSON.stringify({skills, experience_summary, search_keywords, years_of_experience})
    }));
    await loadCvFiles(cvFileId);
    showStatus(`Сохранено навыков: ${skills.length}. Теперь можно искать вакансии.`);
  } catch (error) { showError(error); }
});

document.querySelector('#resume-selector').addEventListener('change', event => {
  selectCvFile(event.target.value).catch(showError);
});
document.querySelector('#search-resume-selector').addEventListener('change', event => {
  selectCvFile(event.target.value).catch(showError);
});

document.querySelector('#anywhere').addEventListener('change', (event) => {
  document.querySelector('#locations').disabled = event.target.checked;
});
document.querySelector('#locations').disabled = document.querySelector('#anywhere').checked;

const applicationTaskTerminalStates = new Set([
  'completed', 'failed', 'cancelled', 'interrupted', 'waiting_for_user'
]);

function applicationTaskMessage(task) {
  const transition = task.transitions?.at(-1);
  const reason = transition?.reason || '';
  const evidence = transition?.evidence || [];
  if (task.state === 'completed' && evidence.includes('submission:true')) {
    return 'Отклик успешно отправлен.';
  }
  if (task.state === 'completed' && evidence.includes('submission:already_applied')) {
    return 'На эту вакансию уже был отправлен отклик.';
  }
  if (task.state === 'waiting_for_user') {
    return `Нужно действие пользователя: ${reason || 'повторите авторизацию на сайте.'}`;
  }
  if (task.state === 'failed') {
    const technicalDetail = evidence.find(item => !item.startsWith('submission:'));
    return `Отклик не отправлен: ${technicalDetail || reason || 'фоновая задача завершилась с ошибкой.'}`;
  }
  if (task.state === 'cancelled' || task.state === 'interrupted') {
    return `Отправка остановлена: ${reason || task.state}.`;
  }
  return `Отправка выполняется: ${task.state}.`;
}

function isSubmissionConfirmed(task) {
  if (!task || task.state !== 'completed') return false;
  const evidence = task.transitions?.at(-1)?.evidence || [];
  return evidence.includes('submission:true') || evidence.includes('submission:already_applied');
}

async function waitForApplicationTask(applicationId, taskState) {
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    const task = await asJson(await fetch(`/v1/applications/${applicationId}/task`, {
      headers: headers(false)
    }));
    taskState.textContent = applicationTaskMessage(task);
    if (applicationTaskTerminalStates.has(task.state)) {
      return task;
    }
    await new Promise(resolve => setTimeout(resolve, 2500));
  }
  taskState.textContent = 'Отправка всё ещё выполняется. Статус обновится при повторном открытии списка.';
  return null;
}

const workFormatLabels = {
  ru: {remote: 'Удалённо', hybrid: 'Гибрид', office: 'Офис', unspecified: 'Формат не указан'},
  en: {remote: 'Remote', hybrid: 'Hybrid', office: 'Office', unspecified: 'Format unspecified'}
};
const employmentTypeLabels = {
  ru: {
    full_time: 'Полная занятость', part_time: 'Частичная занятость',
    contract: 'Контракт', project: 'Проект', temporary: 'Временная работа',
    internship: 'Стажировка'
  },
  en: {
    full_time: 'Full-time', part_time: 'Part-time', contract: 'Contract',
    project: 'Project', temporary: 'Temporary', internship: 'Internship'
  }
};

function createWorkFormatBadge(workFormat) {
  const labels = workFormatLabels[currentLanguage];
  const format = workFormat && labels[workFormat] ? workFormat : 'unspecified';
  return element('span', labels[format], 'badge');
}

function createVacancyAttributeTags(item) {
  const tags = element('div', undefined, 'attribute-tags');
  if (item.location) tags.append(element('span', item.location, 'badge location'));
  tags.append(createWorkFormatBadge(item.work_format));
  Array.from(item.employment_types || []).forEach(employmentType => {
    const label = employmentTypeLabels[currentLanguage][employmentType];
    if (label) {
      tags.append(element('span', label, 'badge'));
    }
  });
  if (item.salary_text) tags.append(element('span', item.salary_text, 'badge salary'));
  return tags;
}

function createKeySkillTags(skills) {
  const normalizedSkills = Array.from(skills || []).map(String).filter(Boolean);
  if (!normalizedSkills.length) return null;
  const block = element('div', undefined, 'key-skills');
  block.append(element(
    'div',
    currentLanguage === 'en' ? 'Key skills' : 'Ключевые навыки',
    'key-skills__label'
  ));
  const tags = element('div', undefined, 'chips');
  normalizedSkills.forEach(skill => tags.append(element('span', skill, 'chip')));
  block.append(tags);
  return block;
}

const editableApplicationStatuses = [
  'draft', 'saved', 'awaiting_review', 'approved',
  'rejected', 'employer_rejected', 'skipped', 'submitted', 'interview'
];

async function setApplicationStatus(applicationId, newStatus) {
  const application = await asJson(await fetchWithTimeout(`/v1/applications/${applicationId}/status`, {
    method: 'PATCH',
    headers: headers(true),
    body: JSON.stringify({status: newStatus})
  }));
  updateCachedSearchResultStatus(applicationId, application.status);
  await loadApplicationStatistics();
  return application.status;
}

async function loadApplicationStatistics() {
  const container = document.querySelector('#application-statistics');
  const userId = userIdInput.value.trim();
  if (!userId) {
    container.replaceChildren();
    return;
  }
  const statistics = await asJson(await fetchWithTimeout(
    `/v1/users/${userId}/application-statistics`,
    {headers: headers(false)}
  ));
  const labels = currentLanguage === 'en'
    ? {total: 'Total', approved: 'Accepted', rejected: 'Rejected by me', employer_rejected: 'Employer rejections', submitted: 'Submitted', interview: 'Interviews', needs_review: 'Need review', email_events: 'Emails processed', email_rejections: 'Email rejections', email_next_stages: 'Email next stages'}
    : {total: 'Всего', approved: 'Принято', rejected: 'Отклонено мной', employer_rejected: 'Отказы работодателей', submitted: 'Отправлено', interview: 'Собеседования', needs_review: 'На проверке', email_events: 'Обработано писем', email_rejections: 'Отказы из почты', email_next_stages: 'Этапы из почты'};
  container.replaceChildren();
  ['total', 'approved', 'rejected', 'employer_rejected', 'submitted', 'interview', 'needs_review', 'email_events', 'email_rejections', 'email_next_stages'].forEach(key => {
    const card = element('div', labels[key], 'statistic-card');
    card.append(element('strong', String(statistics[key] || 0)));
    container.append(card);
  });
}

const emailCategoryLabels = {
  application_received: 'Получено резюме', rejection: 'Отказ', interview_invitation: 'Приглашение на собеседование',
  interview_reschedule: 'Перенос собеседования', offer: 'Оффер', test_assignment: 'Тестовое задание',
  question: 'Вопрос', recruiter_contact: 'Контакт рекрутера', follow_up: 'Follow-up', other: 'Прочее'
};

function renderEmailReviewItem(item) {
  const wrap = element('div', undefined, 'email-review__item');
  wrap.append(element('div', item.subject || '—', 'email-review__subject'));
  const confidence = item.confidence != null ? `${Math.round(item.confidence * 100)}%` : '—';
  const meta = currentLanguage === 'en'
    ? `Category: ${item.category || '—'} · Confidence: ${confidence}`
    : `Категория: ${emailCategoryLabels[item.category] || item.category || '—'} · Уверенность: ${confidence}`;
  wrap.append(element('div', meta, 'email-review__meta'));
  const why = [item.match_reason, item.review_reason].filter(Boolean).join(' · ');
  if (why) wrap.append(element('div', (currentLanguage === 'en' ? 'Why: ' : 'Почему: ') + why, 'email-review__meta'));
  if (item.body) wrap.append(element('div', item.body.slice(0, 800), 'email-review__body'));
  const actions = element('div', undefined, 'email-review__actions');
  (item.candidates || []).forEach(candidate => {
    const button = element('button', `${candidate.company} — ${candidate.title}`);
    button.addEventListener('click', () => resolveEmailReview(item.id, 'link', candidate.application_id));
    actions.append(button);
  });
  const dismiss = element('button', currentLanguage === 'en' ? 'Not related' : 'Не относится');
  dismiss.addEventListener('click', () => resolveEmailReview(item.id, 'dismiss'));
  actions.append(dismiss);
  wrap.append(actions);
  return wrap;
}

async function loadEmailReview() {
  const container = document.querySelector('#email-review-list');
  const panel = document.querySelector('#email-review-panel');
  const userId = userIdInput.value.trim();
  if (!userId) { panel.hidden = true; return; }
  let items;
  try {
    items = await asJson(await fetchWithTimeout(
      `/v1/users/${userId}/email-review`,
      {headers: headers(false)}
    ));
  } catch (error) {
    panel.hidden = true;
    return;
  }
  if (!items.length) { panel.hidden = true; container.replaceChildren(); return; }
  panel.hidden = false;
  container.replaceChildren();
  items.forEach(item => container.append(renderEmailReviewItem(item)));
}

async function resolveEmailReview(eventId, action, applicationId) {
  const userId = userIdInput.value.trim();
  try {
    await asJson(await fetchWithTimeout(
      `/v1/users/${userId}/email-review/${eventId}/resolve`,
      {
        method: 'POST',
        headers: headers(true),
        body: JSON.stringify({action, application_id: applicationId || null})
      }
    ));
    await Promise.all([loadEmailReview(), loadApplicationStatistics(), loadSavedVacancies(1)]);
    showStatus(currentLanguage === 'en' ? 'Email review resolved.' : 'Письмо обработано.');
  } catch (error) {
    showError(error);
  }
}

document.querySelector('#sync-application-statuses').addEventListener('click', async event => {
  const userId = userIdInput.value.trim();
  if (!userId) {
    showError(new Error(currentLanguage === 'en' ? 'Set User ID first' : 'Сначала укажите User ID'));
    return;
  }
  const button = event.currentTarget;
  button.disabled = true;
  showStatus(currentLanguage === 'en' ? 'Synchronizing applications…' : 'Синхронизируем отклики…');
  try {
    const summary = await asJson(await fetchWithTimeout(
      `/v1/users/${userId}/application-sync`,
      {method: 'POST', headers: headers(false)},
      120000
    ));
    await Promise.all([loadApplicationStatistics(), loadSavedVacancies(1), loadEmailReview()]);
    showStatus(currentLanguage === 'en'
      ? `Synchronization complete: ${summary.updated} updated, ${summary.unchanged} unchanged, ${summary.failed} failed.`
      : `Синхронизация завершена: обновлено ${summary.updated}, без изменений ${summary.unchanged}, ошибок ${summary.failed}.`);
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
});

document.querySelector('#sync-application-emails').addEventListener('click', async event => {
  const userId = userIdInput.value.trim();
  if (!userId) {
    showError(new Error(currentLanguage === 'en' ? 'Set User ID first' : 'Сначала укажите User ID'));
    return;
  }
  const button = event.currentTarget;
  button.disabled = true;
  showStatus(currentLanguage === 'en' ? 'Synchronizing email…' : 'Синхронизируем почту…');
  try {
    const summary = await asJson(await fetchWithTimeout(
      `/v1/users/${userId}/application-email-sync`,
      {method: 'POST', headers: headers(false)},
      120000
    ));
    await Promise.all([loadApplicationStatistics(), loadSavedVacancies(1), loadEmailReview()]);
    showStatus(currentLanguage === 'en'
      ? `Email synchronization complete: ${summary.created} new, ${summary.status_updated} statuses updated, ${summary.needs_review} need review, ${summary.failed} failed.`
      : `Синхронизация почты завершена: новых событий ${summary.created}, обновлено статусов ${summary.status_updated}, на проверке ${summary.needs_review}, ошибок ${summary.failed}.`);
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
});

const applicationEmailFiles = document.querySelector('#application-email-files');
document.querySelector('#import-application-emails').addEventListener('click', () => {
  applicationEmailFiles.click();
});
applicationEmailFiles.addEventListener('change', async event => {
  const selectedFiles = Array.from(event.currentTarget.files || []);
  if (!selectedFiles.length) return;
  const userId = userIdInput.value.trim();
  if (!userId) {
    event.currentTarget.value = '';
    showError(new Error(currentLanguage === 'en' ? 'Set User ID first' : 'Сначала укажите User ID'));
    return;
  }
  const button = document.querySelector('#import-application-emails');
  const form = new FormData();
  selectedFiles.forEach(file => form.append('files', file, file.name));
  button.disabled = true;
  showStatus(currentLanguage === 'en' ? 'Importing email files…' : 'Импортируем файлы писем…');
  try {
    const summary = await asJson(await fetchWithTimeout(
      `/v1/users/${userId}/application-email-import`,
      {method: 'POST', headers: headers(false), body: form},
      120000
    ));
    await Promise.all([loadApplicationStatistics(), loadSavedVacancies(1), loadEmailReview()]);
    showStatus(currentLanguage === 'en'
      ? `Email import complete: ${summary.processed} processed, ${summary.created} new, ${summary.status_updated} statuses updated, ${summary.unknown} unknown, ${summary.unmatched} unmatched, ${summary.failed} failed.`
      : `Импорт писем завершён: обработано ${summary.processed}, новых событий ${summary.created}, обновлено статусов ${summary.status_updated}, не распознано ${summary.unknown}, не привязано к вакансиям ${summary.unmatched}, ошибок ${summary.failed}.`);
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
    event.currentTarget.value = '';
  }
});

function createStatusEditor(item, statusProperty, onChanged) {
  const editor = element('div', undefined, 'status-editor');
  const select = element('select');
  select.setAttribute(
    'aria-label',
    currentLanguage === 'en' ? 'Application status' : 'Статус отклика'
  );
  editableApplicationStatuses.forEach(status => {
    const option = element(
      'option',
      vacancyStatusLabels[currentLanguage][status] || status
    );
    option.value = status;
    option.selected = item[statusProperty] === status;
    select.append(option);
  });
  const save = element(
    'button',
    currentLanguage === 'en' ? 'Change status' : 'Изменить статус'
  );
  save.type = 'button';
  save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      item[statusProperty] = await setApplicationStatus(item.application_id, select.value);
      await onChanged(item[statusProperty]);
    } finally {
      save.disabled = false;
    }
  });
  editor.append(select, save);
  return editor;
}

function createSummaryAffordance(text) {
  if (!text) return null;
  const truncated = text.length > 120 ? text.substring(0, 120) + '…' : text;
  const node = element('span', truncated, 'summary-affordance');
  node.setAttribute('tabindex', '0');
  node.setAttribute('title', text);
  return node;
}

function renderResult(item) {
  const card = element('article', undefined, 'vacancy-card');
  card.dataset.searchResultKey = item.application_id || item.source_url;
  if (item.application_id) card.dataset.applicationId = item.application_id;
  card.dataset.matchScore = String(Number(item.match_score) || 0);
  const top = element('div', undefined, 'topline');
  const title = element('div');
  title.append(element('div', item.company, 'company'), element('h3', item.title));
  const cardCorner = element('div', undefined, 'card-corner');
  const statusKey = item.application_status || 'saved';
  const statusBadge = element(
    'button',
    vacancyStatusLabels[currentLanguage][statusKey] || statusKey,
    'status-badge'
  );
  statusBadge.type = 'button';
  statusBadge.dataset.status = statusKey;
  statusBadge.title = currentLanguage === 'en' ? 'Change status' : 'Изменить статус';
  statusBadge.setAttribute('aria-haspopup', 'menu');
  statusBadge.setAttribute('aria-expanded', 'false');
  cardCorner.append(statusBadge, createMatchMeter(item.match_score));
  top.append(title, cardCorner);
  card.append(top);
  if (item.matching_status === 'processing') {
    card.append(element(
      'span',
      currentLanguage === 'en' ? 'Calculating detailed match…' : 'Рассчитываем соответствие…',
      'badge'
    ));
  } else if (item.matching_error) {
    card.append(element('span', item.matching_error, 'badge'));
  }
  if (item.status === 'already_existed') card.append(element('span', 'уже был в списке ранее', 'badge'));
  card.append(createVacancyAttributeTags(item));
  const keySkills = createKeySkillTags(item.key_skills);
  if (keySkills) card.append(keySkills);
  const summaryNode = createSummaryAffordance(item.vacancy_summary);
  if (summaryNode) card.append(summaryNode);
  const meta = element('div', undefined, 'meta');
  const sourceLabel = vacancySourceLabels[currentLanguage][item.source] || item.source;
  const link = element(
    'a',
    currentLanguage === 'en'
      ? `Open vacancy on ${sourceLabel}`
      : `Открыть вакансию на ${sourceLabel}`
  ); link.href = item.source_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.className = 'link-btn';
  card.append(meta);
  const coverBlock = element('div', undefined, 'cover-letter');
  const coverHeader = element('div', undefined, 'cover-letter__header');
  coverHeader.append(element('label', 'Сопроводительное письмо (можно отредактировать)'));
  const coverText = element('textarea'); coverText.id = `cover-${item.application_id}`;
  coverText.value = item.cover_letter_text || '';
  coverText.readOnly = item.materials_status === 'processing';
  if (coverText.readOnly) {
    coverText.placeholder = currentLanguage === 'en'
      ? 'Generating a tailored cover letter…'
      : 'Генерируем индивидуальное сопроводительное письмо…';
  }
  coverBlock.append(coverHeader, coverText);
  card.append(coverBlock);
  const taskState = element('div', '', 'task-state'); taskState.id = `state-${item.application_id}`;
  const matchDetails = element('div', undefined, 'match-details');
  matchDetails.hidden = true;
  if (item.materials_status === 'processing') {
    taskState.textContent = currentLanguage === 'en'
      ? 'Cover letter generation is in progress.'
      : 'Сейчас идёт генерация сопроводительного письма.';
  } else if (item.materials_error) {
    taskState.textContent = item.materials_error;
  }
  const actions = element('div', undefined, 'actions card-actions');
  const accept = element(
    'button',
    item.source === 'greenhouse' ? 'Открыть для рассмотрения' : 'Принять и откликнуться',
    'accept'
  ); accept.type = 'button';
  if (['submitted', 'interview'].includes(item.application_status)) {
    accept.textContent = 'Отклик отправлен';
    accept.disabled = true;
  }
  accept.addEventListener('click', async () => {
    if (item.source === 'greenhouse') {
      window.location.href = `/review?application_id=${encodeURIComponent(item.application_id)}`;
      return;
    }
    accept.disabled = true;
    try {
      taskState.textContent = 'Сохраняем письмо и ставим отклик в очередь…';
      await asJson(await fetch(`/v1/applications/${item.application_id}/materials`, {
        method: 'PATCH', headers: headers(true),
        body: JSON.stringify({cover_letter_text: coverText.value, screening_answers: []})
      }));
      const applyRoute = item.source === 'linkedin' ? 'apply-linkedin' : 'apply-headhunter';
      const task = await asJson(await fetch(`/v1/applications/${item.application_id}/${applyRoute}`, {
        method: 'POST', headers: headers(true),
        body: JSON.stringify({confirmation: 'submit_real_application_i_understand_the_platform_tos_risk'})
      }));
      taskState.textContent = applicationTaskMessage(task);
      let finalTask = task;
      if (!applicationTaskTerminalStates.has(task.state)) {
        finalTask = await waitForApplicationTask(item.application_id, taskState);
      }
      if (isSubmissionConfirmed(finalTask)) {
        item.application_status = 'submitted';
        accept.textContent = 'Отклик отправлен';
        updateCachedSearchResultStatus(item.application_id, 'submitted');
        card.replaceWith(renderResult(item));
        loadSavedVacancies(vacancyPage).catch(showError);
        return;
      }
    } catch (error) {
      taskState.textContent = `Не удалось отправить автоматически: ${error.message}. Откройте вакансию по ссылке и откликнитесь вручную.`;
    } finally {
      if (item.application_status !== 'submitted') accept.disabled = false;
    }
  });
  const reject = element('button', 'Отклонить', 'danger'); reject.type = 'button';
  reject.addEventListener('click', () => rejectVacancy(item.application_id, card).catch(showError));
  const blacklist = element('button', 'Компания в чёрный список'); blacklist.type = 'button';
  blacklist.addEventListener('click', () => {
    blacklistCompany(item.company, true).catch(showError);
  });
  const manualSubmitted = element(
    'button',
    currentLanguage === 'en' ? 'I applied manually' : 'Я откликнулся вручную'
  );
  manualSubmitted.type = 'button';
  manualSubmitted.addEventListener('click', async () => {
    manualSubmitted.disabled = true;
    taskState.textContent = currentLanguage === 'en'
      ? 'Saving the manual application status…'
      : 'Сохраняем отметку о ручном отклике…';
    try {
      item.application_status = await setApplicationStatus(
        item.application_id, 'submitted'
      );
      taskState.textContent = currentLanguage === 'en'
        ? 'Manual application marked as submitted.'
        : 'Ручной отклик отмечен как отправленный.';
      card.replaceWith(renderResult(item));
      loadSavedVacancies(vacancyPage).catch(showError);
    } catch (error) {
      taskState.textContent = currentLanguage === 'en'
        ? `Could not save the manual application status: ${error.message}`
        : `Не удалось сохранить ручной отклик: ${error.message}`;
    } finally {
      if (item.application_status !== 'submitted') {
        manualSubmitted.disabled = false;
      }
    }
  });
  const regenerateLetter = element('button', undefined, 'icon-button');
  regenerateLetter.type = 'button';
  regenerateLetter.title = currentLanguage === 'en'
    ? 'Regenerate cover letter'
    : 'Перегенерировать письмо';
  regenerateLetter.setAttribute('aria-label', regenerateLetter.title);
  regenerateLetter.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.34 5.66"/><path d="M20 4v7h-7"/></svg>';
  regenerateLetter.addEventListener('click', async () => {
    regenerateLetter.disabled = true;
    regenerateLetter.classList.add('is-spinning');
    taskState.textContent = currentLanguage === 'en'
      ? 'Generating a cover letter in the vacancy language…'
      : 'Генерируем письмо на языке вакансии…';
    try {
      const materials = await asJson(await fetchWithTimeout(
        `/v1/applications/${item.application_id}/generate-materials`,
        {method: 'POST', headers: headers(false)},
        150000
      ));
      coverText.value = materials.cover_letter_text || '';
      taskState.textContent = currentLanguage === 'en'
        ? 'Cover letter regenerated.'
        : 'Сопроводительное письмо перегенерировано.';
    } catch (error) {
      taskState.textContent = error.message;
    } finally {
      regenerateLetter.disabled = false;
      regenerateLetter.classList.remove('is-spinning');
    }
  });
  coverHeader.append(regenerateLetter);
  const isSubmitted = ['submitted', 'interview'].includes(item.application_status);
  const moreActions = element('div', undefined, 'more-actions');
  const moreToggle = element('button', '⋯', 'more-actions__toggle');
  moreToggle.type = 'button';
  moreToggle.title = currentLanguage === 'en' ? 'More actions' : 'Другие действия';
  moreToggle.setAttribute('aria-label', moreToggle.title);
  moreToggle.setAttribute('aria-haspopup', 'menu');
  moreToggle.setAttribute('aria-expanded', 'false');
  const moreMenu = element('div', undefined, 'more-actions__menu');
  moreMenu.hidden = true;
  moreMenu.setAttribute('role', 'menu');
  const matchDetailsButton = element(
    'button',
    currentLanguage === 'en' ? 'Why it matches' : 'Почему подходит'
  );
  matchDetailsButton.type = 'button';
  matchDetailsButton.addEventListener('click', () => {
    moreMenu.hidden = true;
    moreToggle.setAttribute('aria-expanded', 'false');
    loadMatchDetails(item.application_id, matchDetails, matchDetailsButton).catch(showError);
  });
  const statusPopup = element('div', undefined, 'status-popup');
  statusPopup.hidden = true;
  statusPopup.setAttribute('role', 'menu');
  editableApplicationStatuses.forEach(status => {
    const statusButton = element(
      'button',
      vacancyStatusLabels[currentLanguage][status] || status
    );
    statusButton.type = 'button';
    statusButton.disabled = item.application_status === status;
    statusButton.addEventListener('click', async () => {
      statusButton.disabled = true;
      try {
        item.application_status = await setApplicationStatus(item.application_id, status);
        card.replaceWith(renderResult(item));
        await loadSavedVacancies(vacancyPage);
      } catch (error) {
        taskState.textContent = error.message;
        statusButton.disabled = false;
      }
    });
    statusPopup.append(statusButton);
  });
  statusBadge.addEventListener('click', () => {
    statusPopup.hidden = !statusPopup.hidden;
    statusBadge.setAttribute('aria-expanded', String(!statusPopup.hidden));
  });
  cardCorner.append(statusPopup);
  moreToggle.addEventListener('click', () => {
    moreMenu.hidden = !moreMenu.hidden;
    statusPopup.hidden = true;
    moreToggle.setAttribute('aria-expanded', String(!moreMenu.hidden));
  });
  if (!isSubmitted) moreMenu.append(manualSubmitted);
  moreMenu.append(matchDetailsButton, blacklist);
  moreActions.append(moreToggle, moreMenu);
  actions.append(accept, reject, link, moreActions);
  card.append(actions, taskState, matchDetails);
  // Load current materials (cover letter) for this application.
  fetch(`/v1/applications/${item.application_id}/materials`, {headers: headers(false)})
    .then(response => response.ok ? response.json() : null)
    .then(materials => {
      if (!materials) return;
      coverText.value = materials.cover_letter_text || '';
      if (!materials.cover_letter_language_matches && materials.cover_letter_text) {
        taskState.textContent = currentLanguage === 'en'
          ? `The cover letter does not match the vacancy language (${materials.vacancy_language}). Regenerate it.`
          : `Язык письма не совпадает с языком вакансии (${materials.vacancy_language}). Перегенерируйте письмо.`;
      }
      const metadataChanged = (
        materials.work_format !== item.work_format
        || materials.location !== item.location
        || materials.vacancy_summary !== item.vacancy_summary
        || JSON.stringify(materials.employment_types || []) !== JSON.stringify(item.employment_types || [])
        || JSON.stringify(materials.key_skills || []) !== JSON.stringify(item.key_skills || [])
      );
      item.work_format = materials.work_format;
      item.location = materials.location || '';
      item.vacancy_summary = materials.vacancy_summary;
      item.employment_types = materials.employment_types || [];
      item.key_skills = materials.key_skills || [];
      if (['rejected', 'employer_rejected', 'skipped', 'submitted', 'interview'].includes(materials.application_status)) {
        item.application_status = materials.application_status;
        updateCachedSearchResults(
          candidate => candidate.application_id !== item.application_id
        );
        card.remove();
        return;
      }
      if (materials.application_status !== item.application_status || metadataChanged) {
        item.application_status = materials.application_status;
        updateCachedSearchResultStatus(item.application_id, item.application_status);
        card.replaceWith(renderResult(item));
      }
    });
  return card;
}

function insertProgressiveSearchResult(results, item) {
  const score = Number(item.match_score) || 0;
  const resultKey = item.application_id || item.source_url;
  Array.from(results.children).find(
    candidate => candidate.dataset.searchResultKey === resultKey
  )?.remove();
  const card = renderResult(item);
  const nextLowerScoredCard = Array.from(results.children).find(
    candidate => candidate.matches('article')
      && Number(candidate.dataset.matchScore || 0) < score
  );
  results.insertBefore(card, nextLowerScoredCard || null);
}

const SEARCH_RESULTS_STORAGE_PREFIX = 'dashboardSearchResults:';
const SEARCH_RESULTS_VERSION = 6;

function searchResultsStorageKey() {
  const userId = userIdInput.value.trim();
  return userId ? `${SEARCH_RESULTS_STORAGE_PREFIX}${userId}` : null;
}

function normalizeSearchResult(item) {
  const source = String(item?.source || '');
  const normalized = {
    application_id: String(item?.application_id || ''),
    vacancy_id: String(item?.vacancy_id || ''),
    title: String(item?.title || ''),
    company: String(item?.company || ''),
    source_url: String(item?.source_url || ''),
    location: String(item?.location || ''),
    match_score: Number(item?.match_score),
    status: String(item?.status || ''),
    source,
    application_status: String(item?.application_status || ''),
    vacancy_summary: String(item?.vacancy_summary || ''),
    work_format: String(item?.work_format || 'unspecified'),
    salary_text: String(item?.salary_text || ''),
    employment_types: Array.isArray(item?.employment_types)
      ? item.employment_types.map(String)
      : [],
    key_skills: Array.isArray(item?.key_skills)
      ? item.key_skills.map(String)
      : []
  };
  if (
    !normalized.application_id || !normalized.vacancy_id || !normalized.title
    || !normalized.source_url || !Number.isFinite(normalized.match_score)
    || !['headhunter', 'linkedin', 'greenhouse'].includes(source)
    || ['rejected', 'employer_rejected', 'skipped', 'submitted', 'interview'].includes(normalized.application_status)
  ) return null;
  normalized.match_score = Math.max(0, Math.min(100, normalized.match_score));
  return normalized;
}

function persistSearchResults(outcomes) {
  const storageKey = searchResultsStorageKey();
  if (!storageKey) return;
  const normalized = outcomes.map(normalizeSearchResult).filter(Boolean);
  localStorage.setItem(storageKey, JSON.stringify({
    version: SEARCH_RESULTS_VERSION,
    outcomes: normalized
  }));
}

function updateCachedSearchResults(predicate) {
  const storageKey = searchResultsStorageKey();
  if (!storageKey) return;
  const raw = localStorage.getItem(storageKey);
  if (!raw) return;
  try {
    const cached = JSON.parse(raw);
    if (cached?.version !== SEARCH_RESULTS_VERSION || !Array.isArray(cached.outcomes)) {
      localStorage.removeItem(storageKey);
      return;
    }
    persistSearchResults(cached.outcomes.map(normalizeSearchResult).filter(Boolean).filter(predicate));
  } catch {
    localStorage.removeItem(storageKey);
  }
}

function updateCachedSearchResultStatus(applicationId, newStatus) {
  const storageKey = searchResultsStorageKey();
  if (!storageKey) return;
  const raw = localStorage.getItem(storageKey);
  if (!raw) return;
  try {
    const cached = JSON.parse(raw);
    if (cached?.version !== SEARCH_RESULTS_VERSION || !Array.isArray(cached.outcomes)) {
      localStorage.removeItem(storageKey);
      return;
    }
    cached.outcomes = cached.outcomes.map(item => {
      if (item.application_id === applicationId) {
        return { ...item, application_status: newStatus };
      }
      return item;
    }).filter(item => !['rejected', 'employer_rejected', 'skipped', 'submitted', 'interview'].includes(
      item.application_status
    ));
    localStorage.setItem(storageKey, JSON.stringify(cached));
  } catch {
    localStorage.removeItem(storageKey);
  }
}

function restoreSearchResults() {
  const storageKey = searchResultsStorageKey();
  const results = document.querySelector('#results');
  if (!storageKey) return false;
  const raw = localStorage.getItem(storageKey);
  if (!raw) return false;
  try {
    const cached = JSON.parse(raw);
    if (cached?.version !== SEARCH_RESULTS_VERSION || !Array.isArray(cached.outcomes)) {
      throw new Error('Unsupported search-result cache');
    }
    const outcomes = cached.outcomes.map(normalizeSearchResult);
    if (outcomes.some(item => item === null)) throw new Error('Malformed search-result cache');
    outcomes.sort((left, right) => right.match_score - left.match_score);
    results.replaceChildren();
    if (!outcomes.length) {
      results.append(element('div', 'Последний поиск не дал результатов.', 'empty'));
    } else {
      outcomes.forEach(item => results.append(renderResult(item)));
    }
    return true;
  } catch {
    localStorage.removeItem(storageKey);
    return false;
  }
}

const vacancySourceLabels = {
  ru: {
    headhunter: 'hh.ru', linkedin: 'LinkedIn', greenhouse: 'Greenhouse',
    registry: 'Импортированный реестр', other: 'Другой источник'
  },
  en: {
    headhunter: 'hh.ru', linkedin: 'LinkedIn', greenhouse: 'Greenhouse',
    registry: 'Imported registry', other: 'Other source'
  }
};
const vacancyStatusLabels = {
  ru: {
    saved: 'Сохранена', awaiting_review: 'Ожидает решения', approved: 'Принята',
    rejected: 'Отклонена мной', employer_rejected: 'Отказ работодателя', skipped: 'Пропущена', submitted: 'Отправлена',
    interview: 'Собеседование', draft: 'Черновик', needs_review: 'На проверке', offer: 'Оффер', withdrawn: 'Отозвана'
  },
  en: {
    saved: 'Saved', awaiting_review: 'Awaiting decision', approved: 'Accepted',
    rejected: 'Rejected by me', employer_rejected: 'Employer rejection', skipped: 'Skipped', submitted: 'Submitted',
    interview: 'Interview', draft: 'Draft', needs_review: 'Under review', offer: 'Offer', withdrawn: 'Withdrawn'
  }
};
let vacancyPage = 1;
let vacancyTotalPages = 1;

function renderSavedVacancy(item) {
  const card = element('article', undefined, 'vacancy-card');
  if (item.application_id) card.dataset.applicationId = item.application_id;
  const top = element('div', undefined, 'topline');
  const title = element('div');
  title.append(element('div', item.company, 'company'), element('h3', item.title));
  const metadata = [
    vacancySourceLabels[currentLanguage][item.source] || item.source,
    item.location || (currentLanguage === 'en' ? 'Location unspecified' : 'Локация не указана'),
    item.published_at
      ? currentLanguage === 'en'
        ? `Published ${new Date(item.published_at).toLocaleDateString('en-US')}`
        : `Опубликована ${new Date(item.published_at).toLocaleDateString('ru-RU')}`
      : currentLanguage === 'en' ? 'Publication date unspecified' : 'Дата публикации не указана'
  ];
  title.append(element('div', metadata.join(' · '), 'meta'));
  const cardCorner = element('div', undefined, 'card-corner');
  const statusKey = item.status || 'saved';
  const statusBadge = element(
    'button',
    vacancyStatusLabels[currentLanguage][statusKey] || statusKey,
    'status-badge'
  );
  statusBadge.type = 'button';
  statusBadge.dataset.status = statusKey;
  statusBadge.title = currentLanguage === 'en' ? 'Change status' : 'Изменить статус';
  statusBadge.setAttribute('aria-haspopup', 'menu');
  statusBadge.setAttribute('aria-expanded', 'false');
  cardCorner.append(statusBadge, createMatchMeter(item.match_score));
  top.append(title, cardCorner);
  card.append(top);
  card.append(createVacancyAttributeTags(item));
  const keySkills = createKeySkillTags(item.key_skills);
  if (keySkills) card.append(keySkills);
  const summaryNode = createSummaryAffordance(item.vacancy_summary);
  if (summaryNode) card.append(summaryNode);
  const actions = element('div', undefined, 'actions card-actions');
  const details = element('div', undefined, 'match-details'); details.hidden = true;
  // Main buttons — equal width in 3-column grid
  const detailsButton = element('button', currentLanguage === 'en' ? 'Why it matches' : 'Почему подходит');
  detailsButton.type = 'button';
  detailsButton.addEventListener('click', () => {
    loadMatchDetails(item.application_id, details, detailsButton).catch(showError);
  });
  const applicationLink = element('a', currentLanguage === 'en' ? 'Materials & decision' : 'Материалы и решение');
  applicationLink.href = `/review?application_id=${encodeURIComponent(item.application_id)}`;
  applicationLink.className = 'link-btn';
  const sourceLabel = ({hh:'hh.ru',linkedin:'LinkedIn',greenhouse:'Greenhouse'})[item.source] || item.source || 'site';
  const link = element('a', currentLanguage === 'en' ? `Open on ${sourceLabel}` : `Открыть на ${sourceLabel}`);
  link.href = item.source_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.className = 'link-btn';
  // Overflow menu — same pattern as search cards
  const moreActions = element('div', undefined, 'more-actions');
  const moreToggle = element('button', '\u22EF', 'more-actions__toggle');
  moreToggle.type = 'button';
  moreToggle.title = currentLanguage === 'en' ? 'More actions' : 'Другие действия';
  moreToggle.setAttribute('aria-label', moreToggle.title);
  moreToggle.setAttribute('aria-haspopup', 'menu');
  moreToggle.setAttribute('aria-expanded', 'false');
  const moreMenu = element('div', undefined, 'more-actions__menu');
  moreMenu.hidden = true;
  moreMenu.setAttribute('role', 'menu');
  const runRecalculate = async (force, button, label) => {
    moreMenu.hidden = true;
    button.disabled = true;
    button.textContent = currentLanguage === 'en' ? 'Calculating…' : 'Рассчитываем…';
    try {
      const task = await asJson(await fetch(`/v1/applications/${item.application_id}/recalculate-match${force ? '?force=true' : ''}`, {
        method: 'POST', headers: headers(false)
      }));
      await waitForMatchingTask(task, current => {
        button.textContent = matchingTaskStatusText(current);
      });
      const data = await asJson(await fetch(`/v1/applications/${item.application_id}/match-details`, {
        headers: headers(false)
      }));
      const newScore = Math.round(data.final_score);
      const meter = card.querySelector('.match-meter');
      if (meter) meter.replaceWith(createMatchMeter(newScore));
      card.dataset.matchScore = String(newScore);
      if (!details.hidden) {
        details.hidden = true;
        detailsButton.textContent = currentLanguage === 'en' ? 'Why it matches' : 'Почему подходит';
        await loadMatchDetails(item.application_id, details, detailsButton);
      }
      button.textContent = '\u2713 ' + (currentLanguage === 'en' ? 'Done' : 'Готово');
      setTimeout(() => {
        button.textContent = label;
        button.disabled = false;
      }, 2000);
    } catch (e) {
      button.textContent = label; button.disabled = false;
      showError(e);
    }
  };
  const recalcBtn = element('button', currentLanguage === 'en' ? 'Recalculate' : 'Пересчитать');
  recalcBtn.type = 'button';
  recalcBtn.title = currentLanguage === 'en'
    ? 'Reuse cached calculations where possible'
    : 'Пересчитать с переиспользованием кэша';
  recalcBtn.addEventListener('click', () => runRecalculate(false, recalcBtn, currentLanguage === 'en' ? 'Recalculate' : 'Пересчитать'));
  const fullRecalcBtn = element('button', currentLanguage === 'en' ? 'Full recalculate' : 'Полный перерасчёт');
  fullRecalcBtn.type = 'button';
  fullRecalcBtn.title = currentLanguage === 'en'
    ? 'Discard cached results and recompute from scratch (use after changing the model)'
    : 'Сбросить кэш и пересчитать с нуля (используйте после смены модели)';
  fullRecalcBtn.addEventListener('click', () => runRecalculate(true, fullRecalcBtn, currentLanguage === 'en' ? 'Full recalculate' : 'Полный перерасчёт'));
  moreMenu.append(recalcBtn, fullRecalcBtn);
  if (!['submitted', 'interview'].includes(item.status)) {
    const rejectBtn = element('button', currentLanguage === 'en' ? 'Reject' : 'Отклонить', 'danger');
    rejectBtn.type = 'button';
    rejectBtn.addEventListener('click', () => { moreMenu.hidden = true; rejectVacancy(item.application_id, card).catch(showError); });
    const blacklistBtn = element('button', currentLanguage === 'en' ? 'Blacklist company' : 'Компания в чёрный список');
    blacklistBtn.type = 'button';
    blacklistBtn.addEventListener('click', () => { moreMenu.hidden = true; blacklistCompany(item.company, true).catch(showError); });
    moreMenu.append(rejectBtn, blacklistBtn);
  }
  const statusPopup = element('div', undefined, 'status-popup');
  statusPopup.hidden = true;
  statusPopup.setAttribute('role', 'menu');
  editableApplicationStatuses.forEach(status => {
    const statusButton = element(
      'button',
      vacancyStatusLabels[currentLanguage][status] || status
    );
    statusButton.type = 'button';
    statusButton.disabled = item.status === status;
    statusButton.addEventListener('click', async () => {
      statusButton.disabled = true;
      try {
        item.status = await setApplicationStatus(item.application_id, status);
        card.replaceWith(renderSavedVacancy(item));
        await loadSavedVacancies(vacancyPage);
        restoreSearchResults();
      } catch (error) {
        showError(error);
        statusButton.disabled = false;
      }
    });
    statusPopup.append(statusButton);
  });
  statusBadge.addEventListener('click', () => {
    statusPopup.hidden = !statusPopup.hidden;
    statusBadge.setAttribute('aria-expanded', String(!statusPopup.hidden));
  });
  cardCorner.append(statusPopup);
  moreToggle.addEventListener('click', () => {
    moreMenu.hidden = !moreMenu.hidden;
    statusPopup.hidden = true;
    moreToggle.setAttribute('aria-expanded', String(!moreMenu.hidden));
  });
  moreActions.append(moreToggle, moreMenu);
  actions.append(detailsButton, applicationLink, link, moreActions);
  card.append(actions, details);
  return card;
}

async function loadMatchDetails(applicationId, container, button) {
  if (!container.hidden) {
    container.hidden = true;
    button.textContent = 'Почему подходит';
    return;
  }
  container.hidden = false;
  button.disabled = true;
  container.replaceChildren(element('div', 'Загрузка подробного расчёта…', 'task-state'));
  try {
    const response = await fetch(`/v1/applications/${applicationId}/match-details`, {
      headers: headers(false)
    });
    if (response.status === 404) {
      const emptyState = element('div', undefined, 'task-state');
      emptyState.append(element('div', 'Подробный расчёт ещё не выполнен.'));
      const calculate = element('button', 'Рассчитать подробно');
      calculate.type = 'button';
      calculate.addEventListener('click', () => {
        calculateDetailedMatch(applicationId, container, button).catch(error => {
          container.replaceChildren(element('div', error.message, 'error'));
        });
      });
      emptyState.append(calculate);
      container.replaceChildren(emptyState);
      return;
    }
    const payload = await asJson(response);
    const heading = element(
      'strong',
      `Matching v2: ${Math.round(payload.final_score)}% · статус ${payload.status}` +
      (payload.required_score !== undefined ? `\nОбязательные: ${Math.round(payload.required_score)}% · Желательные: ${Math.round(payload.preferred_score || 0)}% · Плюсы: ${Math.round(payload.bonus_score || 0)}%` : '')
    );
    const summary = element(
      'div',
      `Подтверждено: ${payload.matched_required_count} · Не подтверждено: ${payload.missing_required_count} · Hard blockers: ${payload.blocker_count}` +
      (payload.confidence ? ` · Уверенность: ${Math.round(payload.confidence * 100)}%` : ''),
      'task-state'
    );
    container.replaceChildren(heading, summary);
    if (payload.fallback_reason) {
      container.append(element('div', `Fallback: ${payload.fallback_reason}`, 'task-state'));
    }
    if (payload.explanation?.matching_source_version !== '2') {
      const stale = element(
        'div',
        currentLanguage === 'en'
          ? 'This explanation was calculated from an older vacancy source.'
          : 'Это объяснение рассчитано по старой версии данных вакансии.',
        'task-state'
      );
      const refreshStale = element(
        'button',
        currentLanguage === 'en' ? 'Refresh explanation' : 'Обновить объяснение'
      );
      refreshStale.type = 'button';
      refreshStale.addEventListener('click', () => {
        calculateDetailedMatch(applicationId, container, button, true).catch(error => {
          container.replaceChildren(element('div', error.message, 'error'));
        });
      });
      container.append(stale, refreshStale);
    }
    const keySkillCoverage = Array.isArray(payload.explanation?.key_skill_coverage)
      ? payload.explanation.key_skill_coverage
      : [];
    appendMatchDetailList(
      container,
      currentLanguage === 'en' ? 'Key skill coverage' : 'Покрытие ключевых навыков',
      keySkillCoverage,
      item => {
        const labels = currentLanguage === 'en'
          ? {
              entailed: 'confirmed', partial: 'partially confirmed',
              related_but_insufficient: 'related evidence is insufficient',
              insufficient_evidence: 'insufficient evidence', evaluation_error: 'evaluation error',
              missing: 'not confirmed', not_evaluated: 'not evaluated'
            }
          : {
              entailed: 'подтверждён', partial: 'подтверждён частично',
              related_but_insufficient: 'связанных данных недостаточно',
              insufficient_evidence: 'недостаточно доказательств', evaluation_error: 'ошибка оценки',
              missing: 'не подтверждён', not_evaluated: 'не оценён'
            };
        return `${item.skill}: ${labels[item.match_level] || item.match_level}`;
      }
    );
    // Categorize requirements by entailment relation
    const entailed = payload.requirements.filter(r => r.entailment_relation === 'entailed');
    const partial = payload.requirements.filter(r => r.entailment_relation === 'partial');
    const relatedInsufficient = payload.requirements.filter(r => r.entailment_relation === 'related_but_insufficient');
    const insufficientEvidence = payload.requirements.filter(r => r.entailment_relation === 'insufficient_evidence');
    const evaluationErrors = payload.requirements.filter(r => r.entailment_relation === 'evaluation_error' || r.match_level === 'evaluation_error');
    const blockers = payload.requirements.filter(r => r.match_level === 'blocker' || r.is_hard_blocker);
    const missing = payload.requirements.filter(r => ['missing', 'theoretical_only'].includes(r.match_level) && !r.is_hard_blocker);
    appendMatchDetailList(
      container,
      '🚫 Блокирующие',
      blockers,
      requirement => requirement.requirement_text
    );
    appendMissingSkillsTags(
      container,
      '❌ Не подтверждено',
      missing,
      requirement => requirement.requirement_text
    );
    appendMatchDetailList(
      container,
      '✅ Подтверждено',
      entailed,
      requirement => `${requirement.requirement_text}: ${requirement.evidence_source_fragment || requirement.evidence_text || ''}` +
        (requirement.evidence_strength !== null ? ` (${Math.round(requirement.evidence_strength * 100)}%)` : '')
    );
    appendMatchDetailList(
      container,
      '🔶 Частично подтверждено',
      partial,
      requirement => `${requirement.requirement_text}: ${requirement.evidence_source_fragment || requirement.evidence_text || ''}` +
        (requirement.evidence_strength !== null ? ` (${Math.round(requirement.evidence_strength * 100)}%)` : '')
    );
    appendMatchDetailList(
      container,
      '🔗 Связанные, но недостаточные',
      relatedInsufficient,
      requirement => `${requirement.requirement_text}: ${requirement.evidence_text || ''}` +
        (requirement.explanation ? ` — ${requirement.explanation.split('\n')[0]}` : '')
    );
    appendMatchDetailList(
      container,
      currentLanguage === 'en' ? 'Insufficient evidence' : 'Недостаточно доказательств',
      insufficientEvidence,
      requirement => requirement.requirement_text
    );
    appendMatchDetailList(
      container,
      currentLanguage === 'en' ? 'Evaluation errors' : 'Ошибки оценки',
      evaluationErrors,
      requirement => `${requirement.requirement_text}: ${requirement.explanation?.split('\n')[0] || ''}`
    );
    if (evaluationErrors.length) {
      const retryErrors = element(
        'button',
        currentLanguage === 'en' ? 'Retry failed evaluations' : 'Повторить ошибочные оценки'
      );
      retryErrors.type = 'button';
      retryErrors.addEventListener('click', () => {
        calculateDetailedMatch(applicationId, container, button, true).catch(error => {
          container.replaceChildren(element('div', error.message, 'error'));
        });
      });
      container.append(retryErrors);
    }
    // Decomposition: one claim per line
    const decompReqs = payload.requirements.filter(
      r => r.retrieval_model_versions && r.retrieval_model_versions.pipeline === 'claim-based'
    );
    if (decompReqs.length) {
      container.append(element('h4', '📊 Decomposition'));
      decompReqs.forEach(req => {
        const lines = (req.explanation || '').split('\n');
        const list = element('ul');
        list.style.marginBottom = '8px';
        lines.slice(0, 12).forEach(line => {
          if (line.trim()) list.append(element('li', line.trim()));
        });
        container.append(element('div', req.requirement_text, 'task-state'));
        container.append(list);
      });
    }
    button.textContent = 'Скрыть объяснение';
  } finally {
    button.disabled = false;
  }
}

function matchingTaskStatusText(task) {
  const runningTransition = task.transitions?.find(item => item.new_state === 'running');
  const startedAt = runningTransition?.occurred_at || task.created_at;
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - Date.parse(startedAt)) / 1000));
  const elapsed = elapsedSeconds >= 60
    ? `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s`
    : `${elapsedSeconds}s`;
  const stage = task.pipeline_status ? ` · stage: ${task.pipeline_status}` : '';
  const position = task.queue_position ? ` · queue: ${task.queue_position}` : '';
  return `Matching: ${task.state}${stage}${position} · ${elapsed}`;
}

async function waitForMatchingTask(task, onUpdate, timeoutMs = 900000) {
  const deadline = Date.now() + timeoutMs;
  let current = task;
  while (Date.now() < deadline) {
    onUpdate(current);
    if (current.state === 'completed') return current;
    if (['failed', 'cancelled', 'interrupted'].includes(current.state)) {
      const lastTransition = current.transitions?.[current.transitions.length - 1];
      throw new Error(lastTransition?.reason || `Matching task ${current.state}`);
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
    current = await asJson(await fetch(`/v1/workflow-tasks/${encodeURIComponent(current.id)}`, {
      headers: headers(false)
    }));
  }
  throw new Error(currentLanguage === 'en'
    ? 'Matching is still running after 15 minutes.'
    : 'Расчёт всё ещё выполняется спустя 15 минут.');
}

async function calculateDetailedMatch(applicationId, container, button, force = false) {
  const wasHidden = container.hidden;
  container.hidden = false;
  container.replaceChildren(element('div', currentLanguage === 'en'
    ? 'Starting detailed matching…'
    : 'Запускаем подробный расчёт…', 'task-state'));
  button.disabled = true;
  try {
    const task = await asJson(await fetch(`/v1/applications/${applicationId}/recalculate-match${force ? '?force=true' : ''}`, {
      method: 'POST', headers: headers(false)
    }));
    await waitForMatchingTask(task, current => {
      container.replaceChildren(element('div', matchingTaskStatusText(current), 'task-state'));
    });
    container.hidden = true;
    button.textContent = currentLanguage === 'en' ? 'Why it matches' : 'Почему подходит';
    await loadMatchDetails(applicationId, container, button);
  } catch (error) {
    container.replaceChildren(element('div', error.message, 'error'));
    button.textContent = currentLanguage === 'en' ? 'Why it matches' : 'Почему подходит';
  } finally {
    button.disabled = false;
  }
}

function appendMatchDetailList(container, title, items, textForItem) {
  if (!items.length) return;
  container.append(element('h4', title));
  const list = element('ul');
  items.forEach(item => list.append(element('li', textForItem(item))));
  container.append(list);
}

function appendMissingSkillsTags(container, title, items, textForItem) {
  if (!items.length) return;
  container.append(element('h4', title));
  const tagsContainer = element('div', undefined, 'chips');
  items.forEach(item => tagsContainer.append(element('span', textForItem(item), 'chip')));
  container.append(tagsContainer);
}

async function rejectVacancy(applicationId, card) {
  await asJson(await fetch(`/v1/applications/${applicationId}/reject-vacancy`, {
    method: 'POST', headers: headers(false)
  }));
  updateCachedSearchResults(item => item.application_id !== applicationId);
  card.remove();
  await loadApplicationStatistics();
  showStatus('Вакансия отклонена и больше не будет показана в общем списке.');
}

async function blacklistCompany(company, askForConfirmation = false) {
  if (askForConfirmation && !confirm(`Добавить компанию «${company}» в чёрный список?`)) return;
  const userId = userIdInput.value.trim();
  if (!userId) throw new Error('Сначала создайте или укажите User ID');
  await asJson(await fetch(`/v1/users/${userId}/company-blacklist`, {
    method: 'POST', headers: headers(true), body: JSON.stringify({company})
  }));
  const normalizedCompany = company.trim().toLocaleLowerCase();
  updateCachedSearchResults(
    item => item.company.trim().toLocaleLowerCase() !== normalizedCompany
  );
  showStatus(`Компания «${company}» добавлена в чёрный список.`);
  await Promise.all([loadCompanyBlacklist(), loadSavedVacancies(1)]);
}

async function loadCompanyBlacklist() {
  const userId = userIdInput.value.trim();
  const list = document.querySelector('#blacklist-list');
  if (!userId) {
    list.replaceChildren(element('div', 'Укажите User ID в разделе «Профиль и доступ».', 'empty'));
    return;
  }
  const entries = await asJson(await fetch(`/v1/users/${userId}/company-blacklist`, {
    headers: headers(false)
  }));
  list.replaceChildren();
  if (!entries.length) {
    list.append(element('div', 'Чёрный список пуст.', 'empty'));
    return;
  }
  entries.forEach(entry => {
    const row = element('div', undefined, 'blacklist-entry');
    row.append(element('strong', entry.company));
    const remove = element('button', 'Убрать из списка'); remove.type = 'button';
    remove.addEventListener('click', async () => {
      try {
        const response = await fetch(`/v1/users/${userId}/company-blacklist/${entry.id}`, {
          method: 'DELETE', headers: headers(false)
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail || `Request failed (${response.status})`);
        }
        showStatus(`Компания «${entry.company}» удалена из чёрного списка.`);
        await Promise.all([loadCompanyBlacklist(), loadSavedVacancies(1)]);
      } catch (error) { showError(error); }
    });
    row.append(remove); list.append(row);
  });
}

document.querySelector('#add-blacklist-company').addEventListener('click', async () => {
  try {
    const input = document.querySelector('#blacklist-company');
    const company = input.value.trim();
    if (!company) throw new Error('Введите название компании');
    await blacklistCompany(company);
    input.value = '';
  } catch (error) { showError(error); }
});

async function loadSavedVacancies(requestedPage = vacancyPage) {
  const userId = userIdInput.value.trim();
  const list = document.querySelector('#vacancy-list');
  if (!userId) {
    list.replaceChildren(element('div', 'Укажите User ID в разделе «Профиль и доступ».', 'empty'));
    document.querySelector('#vacancy-summary').textContent = '';
    return;
  }
  const parameters = new URLSearchParams({
    query: document.querySelector('#vacancy-query').value.trim(),
    source: document.querySelector('#vacancy-source').value,
    status_filter: document.querySelector('#vacancy-status').value,
    location: document.querySelector('#vacancy-location').value.trim(),
    min_match_score: document.querySelector('#vacancy-score').value || '0',
    work_format: document.querySelector('#vacancy-work-format').value,
    employment_type: document.querySelector('#vacancy-employment-type').value,
    matching_v2: document.querySelector('#vacancy-matching-v2').value,
    page: String(requestedPage),
    page_size: '20'
  });
  const publishedFrom = document.querySelector('#vacancy-published-from').value;
  const publishedTo = document.querySelector('#vacancy-published-to').value;
  if (publishedFrom) parameters.set('published_from', publishedFrom);
  if (publishedTo) parameters.set('published_to', publishedTo);
  list.replaceChildren(element('div', 'Загружаем вакансии…', 'empty'));
  const payload = await asJson(await fetch(`/v1/users/${userId}/vacancies?${parameters}`, {
    headers: headers(false)
  }));
  vacancyPage = payload.page;
  vacancyTotalPages = payload.total_pages;
  list.replaceChildren();
  if (!payload.items.length) {
    list.append(element('div', 'По заданным фильтрам вакансий нет.', 'empty'));
  } else {
    payload.items.forEach(item => list.append(renderSavedVacancy(item)));
  }
  const start = payload.total ? (payload.page - 1) * payload.page_size + 1 : 0;
  const end = Math.min(payload.page * payload.page_size, payload.total);
  document.querySelector('#vacancy-summary').textContent = currentLanguage === 'en'
    ? `Showing ${start}–${end} of ${payload.total}`
    : `Показано ${start}–${end} из ${payload.total}`;
  document.querySelector('#vacancy-page').textContent = currentLanguage === 'en'
    ? `Page ${payload.page} of ${payload.total_pages}`
    : `Страница ${payload.page} из ${payload.total_pages}`;
  document.querySelector('#vacancy-previous').disabled = payload.page <= 1;
  document.querySelector('#vacancy-next').disabled = payload.page >= payload.total_pages;
}

document.querySelector('#apply-vacancy-filters').addEventListener('click', () => {
  vacancyPage = 1; loadSavedVacancies().catch(showError);
});
document.querySelector('#reset-vacancy-filters').addEventListener('click', () => {
  document.querySelector('#vacancy-query').value = '';
  document.querySelector('#vacancy-source').value = 'all';
  document.querySelector('#vacancy-status').value = 'all';
  document.querySelector('#vacancy-location').value = '';
  document.querySelector('#vacancy-score').value = '0';
  document.querySelector('#vacancy-published-from').value = '';
  document.querySelector('#vacancy-published-to').value = '';
  document.querySelector('#vacancy-work-format').value = 'all';
  document.querySelector('#vacancy-employment-type').value = 'all';
  document.querySelector('#vacancy-matching-v2').value = 'all';
  vacancyPage = 1; loadSavedVacancies().catch(showError);
});
document.querySelector('#rerank-filtered').addEventListener('click', async () => {
  const btn = document.querySelector('#rerank-filtered');
  btn.disabled = true;
  try {
    // Load all vacancies matching current filters (paginated, max 100 per page)
    const baseParams = new URLSearchParams();
    const q = document.querySelector('#vacancy-query').value.trim();
    if (q) baseParams.set('query', q);
    const src = document.querySelector('#vacancy-source').value;
    if (src && src !== 'all') baseParams.set('source', src);
    const st = document.querySelector('#vacancy-status').value;
    if (st && st !== 'all') baseParams.set('status_filter', st);
    const loc = document.querySelector('#vacancy-location').value.trim();
    if (loc) baseParams.set('location', loc);
    const score = document.querySelector('#vacancy-score').value || '0';
    if (score !== '0') baseParams.set('min_match_score', score);
    const wf = document.querySelector('#vacancy-work-format').value;
    if (wf && wf !== 'all') baseParams.set('work_format', wf);
    const et = document.querySelector('#vacancy-employment-type').value;
    if (et && et !== 'all') baseParams.set('employment_type', et);
    const publishedFrom = document.querySelector('#vacancy-published-from').value;
    const publishedTo = document.querySelector('#vacancy-published-to').value;
    if (publishedFrom) baseParams.set('published_from', publishedFrom);
    if (publishedTo) baseParams.set('published_to', publishedTo);

    // Collect all application_ids across pages
    const allAppIds = [];
    for (let pg = 1; pg <= 20; pg++) {
      const params = new URLSearchParams(baseParams);
      params.set('page', String(pg));
      params.set('page_size', '100');
      const resp = await fetch(`/v1/users/${userIdInput.value.trim()}/vacancies?${params}`, { headers: headers(false) });
      if (!resp.ok) break;
      const payload = await asJson(resp);
      const items = payload.items || [];
      for (const item of items) {
        if (item.application_id) allAppIds.push(item.application_id);
      }
      if (items.length < 100 || pg >= (payload.total_pages || 1)) break;
    }
    if (!allAppIds.length) {
      showStatus(currentLanguage === 'en' ? 'No vacancies to re-rank.' : 'Нет вакансий для переранжирования.');
      btn.disabled = false;
      return;
    }
    let done = 0, failed = 0;
    const total = allAppIds.length;
    const updateProgress = () => {
      showStatus(`Переранжирование: ${done}/${total}${failed ? `, ошибок: ${failed}` : ''}`);
    };
    updateProgress();
    for (const appId of allAppIds) {
      try {
        await fetch(`/v1/applications/${appId}/recalculate-match?force=true`, {
          method: 'POST', headers: headers(false)
        });
      } catch { failed++; }
      done++;
      updateProgress();
    }
    showStatus('Ожидание завершения…');
    await new Promise(r => setTimeout(r, 5000));
    await loadSavedVacancies(vacancyPage);
    showStatus(`Переранжирование завершено для ${total} вакансий.`);
  } catch (e) { showError(e); }
  btn.disabled = false;
});
document.querySelector('#vacancy-previous').addEventListener('click', () => loadSavedVacancies(vacancyPage - 1).catch(showError));
document.querySelector('#vacancy-next').addEventListener('click', () => loadSavedVacancies(vacancyPage + 1).catch(showError));
document.querySelector('#vacancy-query').addEventListener('keydown', event => {
  if (event.key === 'Enter') { vacancyPage = 1; loadSavedVacancies().catch(showError); }
});

document.querySelector('#search').addEventListener('click', async () => {
  try {
    const userId = userIdInput.value.trim();
    if (!userId) throw new Error('Сначала создайте или укажите User ID');
    const anywhere = document.querySelector('#anywhere').checked;
    const locations = anywhere ? [] : document.querySelector('#locations').value.split(',').map(s => s.trim()).filter(Boolean);
    showStatus('Ищем вакансии на выбранных источниках и готовим материалы…');
    const results = document.querySelector('#results'); results.replaceChildren();
    const cvFileId = document.querySelector('#search-resume-selector').value;
    if (!cvFileId) throw new Error('Выберите проанализированное резюме для поиска');
    const searchText = document.querySelector('#keywords-text').value.trim() || null;
    const sources = [];
    if (document.querySelector('#source-headhunter').checked) sources.push({source: 'headhunter', route: 'discover-headhunter-vacancies', requiresSession: true});
    if (document.querySelector('#source-linkedin').checked) sources.push({source: 'linkedin', route: 'discover-linkedin-vacancies', requiresSession: true});
    if (document.querySelector('#source-greenhouse').checked) sources.push({source: 'greenhouse', route: 'discover-greenhouse-vacancies', requiresSession: false});
    if (!sources.length) throw new Error('Выберите хотя бы один сайт для поиска');
    await loadBrowserSessionStatuses();
    const missingSessions = sources
      .filter(source => source.requiresSession && !browserSessionStatuses[source.source]?.is_authorized)
      .map(source => source.source);
    if (missingSessions.length) {
      activatePanel('sessions');
      document.querySelector('#browser-sessions').scrollIntoView({behavior: 'smooth', block: 'center'});
      throw new Error(`Сначала авторизуйтесь: ${missingSessions.map(browserSiteLabel).join(', ')}`);
    }
    saveGreenhouseBoards();
    const greenhouseBoards = greenhouseBoardsInput.value
      .split(/[\n,]+/).map(value => value.trim()).filter(Boolean);
    const outcomes = [];
    const failures = [];
    const seenResults = new Set();
    let completedSources = 0;
    const updateSearchProgress = () => {
      const progress = currentLanguage === 'en'
        ? `Found: ${outcomes.length}. Sources completed: ${completedSources}/${sources.length}`
        : `Найдено: ${outcomes.length}. Источников завершено: ${completedSources}/${sources.length}`;
      showStatus(failures.length ? `${progress}. ${failures.join('; ')}` : progress);
    };
    updateSearchProgress();
    const response = await fetch(`/v1/users/${userId}/discover-vacancies-stream`, {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({
        sources: sources.map(({source}) => source),
        board_urls: greenhouseBoards,
        locations,
        limit: 15,
        search_text: searchText,
        cv_file_id: cvFileId,
        direct_rerank: Boolean(document.querySelector('#direct-rerank')?.checked)
      })
    });
    if (!response.ok) throw new Error(await response.text());
    if (!response.body) throw new Error('Поток поиска не поддерживается браузером');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let bufferedText = '';
    const handleStreamEvent = event => {
      if (event.event === 'vacancy') {
        const item = {
          ...event.item,
          source: event.source,
          matching_status: event.item.matching_status || 'processing',
          materials_status: event.item.materials_status || 'processing'
        };
        if (['rejected', 'employer_rejected', 'skipped', 'submitted', 'interview'].includes(
          item.application_status
        )) return;
        const resultKey = item.application_id || item.source_url;
        if (!resultKey || seenResults.has(resultKey)) return;
        seenResults.add(resultKey);
        outcomes.push(item);
        insertProgressiveSearchResult(results, item);
        outcomes.sort((left, right) => right.match_score - left.match_score);
        persistSearchResults(outcomes);
        updateSearchProgress();
      } else if (event.application_id) {
        const item = outcomes.find(
          candidate => candidate.application_id === event.application_id
        );
        if (!item) return;
        if (event.event === 'enrichment_started') {
          item.matching_status = 'processing';
          item.materials_status = 'processing';
        } else if (event.event === 'materials_ready') {
          item.materials_status = 'ready';
          item.cover_letter_text = event.cover_letter_text || '';
          delete item.materials_error;
        } else if (event.event === 'materials_error') {
          item.materials_status = 'error';
          item.materials_error = event.error;
        } else if (event.event === 'matching_ready') {
          item.matching_status = event.matching_status || 'ready';
          item.match_score = Number(event.match_score) || 0;
          delete item.matching_error;
          outcomes.sort((left, right) => right.match_score - left.match_score);
        } else if (event.event === 'matching_error') {
          item.matching_status = 'error';
          item.matching_error = event.error;
        } else {
          return;
        }
        insertProgressiveSearchResult(results, item);
        persistSearchResults(outcomes);
      } else if (event.event === 'source_error') {
        failures.push(
          `${vacancySourceLabels[currentLanguage][event.source] || event.source}: ${event.error}`
        );
        updateSearchProgress();
      } else if (event.event === 'source_complete') {
        completedSources += 1;
        updateSearchProgress();
      }
    };
    while (true) {
      const {value, done} = await reader.read();
      bufferedText += decoder.decode(value || new Uint8Array(), {stream: !done});
      const lines = bufferedText.split('\n');
      bufferedText = lines.pop() || '';
      lines.filter(Boolean).forEach(line => handleStreamEvent(JSON.parse(line)));
      if (done) break;
    }
    if (bufferedText.trim()) handleStreamEvent(JSON.parse(bufferedText));
    loadSavedVacancies(1).catch(() => {});
    if (!outcomes.length) {
      persistSearchResults([]);
      results.append(element('div', failures.length ? 'Поиск не выполнен: проверьте настройку браузерных сессий.' : 'Ничего не найдено. Попробуйте другие ключевые слова или локации.', 'empty'));
      showStatus(failures.length ? failures.join('; ') : '0 результатов');
      return;
    }
    showStatus(`Найдено: ${outcomes.length}${failures.length ? `. Не удалось проверить: ${failures.join('; ')}` : ''}`);
  } catch (error) { showError(error); }
});
async function loadRerankerModelInfo() {
  const info = document.querySelector('#reranker-model-info');
  if (!info) return;
  try {
    const response = await fetch('/api/v1/admin/reranker/status', { headers: headers(false) });
    if (!response.ok) { info.textContent = ''; return; }
    const data = await response.json();
    if (data.model) {
      const label = currentLanguage === 'en'
        ? `Reranker used by search now: ${data.model}${data.model_revision ? ' @ ' + data.model_revision : ''} — ${data.status}`
        : `Сейчас в поиске используется реранжировщик: ${data.model}${data.model_revision ? ' @ ' + data.model_revision : ''} — ${data.status}`;
      info.textContent = label;
    } else {
      info.textContent = currentLanguage === 'en'
        ? `Reranker: ${data.status}`
        : `Реранжировщик: ${data.status}`;
    }
  } catch { info.textContent = ''; }
}

document.querySelector('#rerank-all').addEventListener('click', async () => {
  try {
    const cards = document.querySelectorAll('#results .vacancy-card');
    if (!cards.length) {
      showStatus(currentLanguage === 'en' ? 'No vacancies to re-rank.' : 'Нет вакансий для переранжирования.');
      return;
    }
    const button = document.querySelector('#rerank-all');
    button.disabled = true;
    let done = 0;
    let failed = 0;
    const total = cards.length;
    const updateProgress = () => {
      showStatus(
        currentLanguage === 'en'
          ? `Re-ranking: ${done}/${total}${failed ? `, failed: ${failed}` : ''}`
          : `Переранжирование: ${done}/${total}${failed ? `, ошибок: ${failed}` : ''}`
      );
    };
    updateProgress();
    // Dispatch re-ranking for each vacancy
    const appIds = [];
    for (const card of cards) {
      const appId = card.dataset.applicationId;
      if (!appId) { done++; continue; }
      appIds.push(appId);
      try {
        await fetch(`/v1/applications/${appId}/recalculate-match?force=true`, {
          method: 'POST', headers: headers(false)
        });
      } catch { failed++; }
      done++;
      updateProgress();
    }
    // Poll for updated scores
    showStatus(currentLanguage === 'en'
      ? 'Waiting for scores to update…'
      : 'Ожидание обновления оценок…');
    const scoreMap = new Map();
    for (const appId of appIds) {
      let scoreFound = false;
      for (let attempt = 0; attempt < 30; attempt++) {
        try {
          const resp = await fetch(`/v1/applications/${appId}/match-details`, { headers: headers(false) });
          if (resp.ok) {
            const data = await resp.json();
            console.log(`[rerank] ${appId} attempt=${attempt} status=${data.status} final_score=${data.final_score}`);
            if (['scored', 'degraded', 'failed'].includes(data.status)) {
              scoreMap.set(appId, Math.round(data.final_score));
              scoreFound = true;
              break;
            }
          } else {
            console.log(`[rerank] ${appId} attempt=${attempt} HTTP ${resp.status}`);
          }
        } catch (e) { console.log(`[rerank] ${appId} attempt=${attempt} error=${e.message}`); }
        await new Promise(r => setTimeout(r, 1000));
      }
      if (!scoreFound) console.warn(`[rerank] ${appId} — no score after 30 attempts`);
    }
    console.log(`[rerank] scoreMap:`, Object.fromEntries(scoreMap));
    // Update cards with new scores
    const results = document.querySelector('#results');
    const cardsArray = Array.from(results.querySelectorAll('.vacancy-card'));
    for (const card of cardsArray) {
      const appId = card.dataset.applicationId;
      if (appId && scoreMap.has(appId)) {
        const newScore = scoreMap.get(appId);
        card.dataset.matchScore = String(newScore);
        const meter = card.querySelector('.match-meter');
        if (meter) {
          const newMeter = createMatchMeter(newScore);
          meter.replaceWith(newMeter);
        }
      }
    }
    // Re-sort cards by score (descending)
    cardsArray.sort((a, b) => Number(b.dataset.matchScore || 0) - Number(a.dataset.matchScore || 0));
    for (const card of cardsArray) {
      results.appendChild(card);
    }
    // Update cached scores in localStorage
    const storageKey = searchResultsStorageKey();
    if (storageKey) {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        try {
          const cached = JSON.parse(raw);
          if (cached?.version === SEARCH_RESULTS_VERSION && Array.isArray(cached.outcomes)) {
            cached.outcomes = cached.outcomes.map(item => {
              if (item.application_id && scoreMap.has(item.application_id)) {
                return { ...item, match_score: scoreMap.get(item.application_id) };
              }
              return item;
            });
            localStorage.setItem(storageKey, JSON.stringify(cached));
          }
        } catch { /* ignore */ }
      }
    }
    button.disabled = false;
    showStatus(
      currentLanguage === 'en'
        ? `Re-ranking complete. Updated scores for ${scoreMap.size} vacancies.`
        : `Переранжирование завершено. Обновлены оценки для ${scoreMap.size} вакансий.`
    );
    loadRerankerModelInfo().catch(() => {});
  } catch (error) { showError(error); document.querySelector('#rerank-all').disabled = false; }
});

loadRerankerModelInfo().catch(() => {});

// Persist direct-rerank checkbox
const directRerankCb = document.querySelector('#direct-rerank');
if (directRerankCb) {
  directRerankCb.checked = localStorage.getItem('directRerank') === 'true';
  directRerankCb.addEventListener('change', () => {
    localStorage.setItem('directRerank', String(directRerankCb.checked));
  });
}

async function loadUserWorkspace() {
  localStorage.setItem('dashboardUserId', userIdInput.value.trim());
  loadLlmPreferences();
  await Promise.all([
    loadBrowserSessionStatuses(), loadSavedVacancies(1), loadCompanyBlacklist(), loadCvFiles(),
    loadAutofillValues(), loadSiteDefinitionsForFields(), loadApplicationStatistics(),
    loadEmailIntegration(), loadProfileFacts(), loadEmailReview(), loadSearchPreferences()
  ]);
  restoreSearchResults();
}
keyInput.addEventListener('change', () => sessionStorage.setItem('dashboardApiKey', keyInput.value));
greenhouseBoardsInput.addEventListener('input', saveGreenhouseBoards);
userIdInput.addEventListener('change', () => loadUserWorkspace().catch(showError));
document.querySelector('#load-user').addEventListener('click', () => {
  loadUserWorkspace().then(() => showStatus('Профиль загружен.')).catch(showError);
});
translateTree();
renderLanguageSelector();
initializeDashboardSubsections();
applyStoredSubsectionState();

const requestedPanel = new URLSearchParams(window.location.search).get('panel');
const validPanels = ['vacancies', 'search', 'blacklist', 'resume', 'sessions', 'model', 'access', 'annotation', 'crm'];
localStorage.removeItem('dashboardActivePanel');
collapseAllPanels();
if (validPanels.includes(requestedPanel)) activatePanel(requestedPanel);
restoreSearchResults();
loadLlmPreferences();
loadAutofillValues().catch(showError);
loadSiteDefinitionsForFields().catch(showError);
loadBrowserSessionStatuses(true).catch(showError);
loadEmailIntegration().catch(showError);
loadProfileFacts().catch(showError);
loadSearchPreferences().catch(showError);
loadApplicationStatistics().catch(showError);
loadEmailReview().catch(showError);
window.setInterval(() => loadBrowserSessionStatuses(true).catch(
  error => console.warn('Browser session status poll failed:', error)
), 300000);
loadSavedVacancies().catch(showError);
loadCompanyBlacklist().catch(showError);
loadCvFiles().catch(showError);

// --- Debug log tab ---
(() => {
  const LEVEL_COLORS = {
    verbose: '#555',
    debug: '#6a9fb5',
    info: '#b5d36a',
    warning: '#e5c07b',
    error: '#e06c75',
    critical: '#ff5555',
    major: '#ff0000',
  };
  const debugLevel = document.querySelector('#debug-level');
  const debugInterval = document.querySelector('#debug-interval');
  const debugLimit = document.querySelector('#debug-limit');
  const debugSearch = document.querySelector('#debug-search');
  const debugRefresh = document.querySelector('#debug-refresh');
  const debugToggle = document.querySelector('#debug-toggle');
  const debugClear = document.querySelector('#debug-clear');
  const debugStatus = document.querySelector('#debug-status');
  const debugEntries = document.querySelector('#debug-log-entries');
  const debugContainer = document.querySelector('#debug-log-container');

  let autoTimer = null;
  let lastTimestamp = null;

  function formatTimestamp(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleString('ru-RU', {hour12: false, fractionalSecondDigits: 0});
    } catch { return iso; }
  }

  function renderEntry(e) {
    const color = LEVEL_COLORS[e.level] || '#888';
    const ts = formatTimestamp(e.timestamp);
    const level = e.level.toUpperCase().padEnd(8);
    const logger = e.logger ? `[${e.logger}]` : '';
    const proc = e.process ? `{${e.process}}` : '';
    let detail = '';
    if (e.fields && Object.keys(e.fields).length > 0) {
      const skip = new Set(['event', 'level', 'timestamp', 'logger']);
      const extra = {};
      for (const [k, v] of Object.entries(e.fields)) {
        if (!skip.has(k) && v !== null && v !== undefined && v !== '') {
          extra[k] = v;
        }
      }
      if (Object.keys(extra).length > 0) {
        detail = `\n  ${JSON.stringify(extra, null, 0)}`;
      }
    }
    return `<div style="margin-bottom:2px;white-space:pre-wrap;word-break:break-all;border-left:3px solid ${color};padding-left:6px;"><span style="color:#555;">${ts}</span> <span style="color:${color};font-weight:bold;">${level}</span> <span style="color:#e5c07b;">${proc}</span> <span style="color:#888;">${logger}</span> ${e.message}${detail ? `<span style="color:#555;">${detail}</span>` : ''}</div>`;
  }

  async function fetchLogs(append = false) {
    const params = new URLSearchParams({
      level: debugLevel.value,
      limit: debugLimit.value,
    });
    if (append && lastTimestamp) params.set('since', lastTimestamp);
    const searchVal = debugSearch.value.trim();
    if (searchVal) params.set('search', searchVal);

    try {
      const resp = await fetch(`/v1/debug/logs?${params}`, {headers: headers(false)});
      if (!resp.ok) throw new Error(`${resp.status}`);
      const data = await resp.json();

      if (append && data.entries.length > 0) {
        // Prepend new entries at top
        const html = data.entries.reverse().map(renderEntry).join('');
        debugEntries.insertAdjacentHTML('afterbegin', html);
        // Cap displayed entries
        while (debugEntries.children.length > parseInt(debugLimit.value)) {
          debugEntries.removeChild(debugEntries.lastChild);
        }
      } else if (!append) {
        debugEntries.innerHTML = data.entries.map(renderEntry).join('');
      }

      if (data.entries.length > 0) {
        lastTimestamp = data.entries[0].timestamp;
      }
      debugStatus.textContent = `${data.count} записей • ${new Date().toLocaleTimeString('ru-RU')}`;
    } catch (err) {
      debugStatus.textContent = `Ошибка: ${err.message}`;
    }
  }

  debugRefresh.addEventListener('click', () => {
    lastTimestamp = null;
    fetchLogs(false);
  });

  debugToggle.addEventListener('click', () => {
    if (autoTimer) {
      clearInterval(autoTimer);
      autoTimer = null;
      debugToggle.textContent = '▶ Авто';
      debugToggle.style.background = '';
    } else {
      const sec = Math.max(1, parseInt(debugInterval.value) || 5);
      autoTimer = setInterval(() => fetchLogs(true), sec * 1000);
      debugToggle.textContent = '⏸ Стоп';
      debugToggle.style.background = '#c0392b';
      fetchLogs(true);
    }
  });

  debugClear.addEventListener('click', async () => {
    try {
      await fetch('/v1/debug/logs/clear', {method: 'POST', headers: headers(false)});
      debugEntries.innerHTML = '';
      lastTimestamp = null;
      debugStatus.textContent = 'Очищено';
    } catch (err) {
      debugStatus.textContent = `Ошибка: ${err.message}`;
    }
  });

  // Load initial data when debug tab is activated
  const origActivate = window.activatePanel;
  if (typeof origActivate === 'function') {
    const wrapper = function(name, opts) {
      const result = origActivate(name, opts);
      if (name === 'debug' && result) {
        lastTimestamp = null;
        fetchLogs(false);
      }
      return result;
    };
    // Also hook into menu button clicks
    document.querySelectorAll('[data-menu="debug"]').forEach(btn => {
      btn.addEventListener('click', () => {
        setTimeout(() => {
          lastTimestamp = null;
          fetchLogs(false);
        }, 50);
      });
    });
  }
})();

// ── Annotation (human labelling for ranking) ─────────────────────────────
const ANNOTATION_REASONS = [
  ['strong_match', 'Сильное совпадение'],
  ['skills_mismatch', 'Не те навыки'],
  ['wrong_seniority', 'Не тот уровень'],
  ['salary_too_low', 'Низкая зарплата'],
  ['location', 'Локация'],
  ['company_unwanted', 'Нежелательная компания'],
  ['wrong_domain', 'Другая сфера'],
  ['unclear_description', 'Неясное описание'],
];
const annotationState = {items: [], pairs: [], index: 0, mode: 'pointwise', resumeId: '', shownAt: 0};

function annotationUserId() {
  const userId = userIdInput.value.trim();
  if (!userId) throw new Error('Сначала создайте или укажите User ID');
  return userId;
}

async function loadAnnotationResumes() {
  const select = document.querySelector('#annotation-resume');
  const userId = userIdInput.value.trim();
  select.replaceChildren();
  if (!userId) return;
  const data = await asJson(await fetchWithTimeout(
    `/v1/annotation/discover?user_id=${encodeURIComponent(userId)}`, {headers: headers(false)}
  ));
  data.users.flatMap(user => user.resumes).forEach(resume => {
    const option = document.createElement('option');
    option.value = resume.resume_id;
    option.textContent = resume.filename;
    select.append(option);
  });
}

async function refreshAnnotationReport() {
  const box = document.querySelector('#annotation-report');
  const userId = userIdInput.value.trim();
  if (!userId) { box.textContent = ''; return; }
  const split = document.querySelector('#annotation-split-name').value.trim();
  let query = `user_id=${encodeURIComponent(userId)}`;
  const withSplit = await annotationSplitExists(split);
  if (withSplit) query += `&split=${encodeURIComponent(split)}`;
  const report = await asJson(await fetchWithTimeout(
    `/v1/annotation/dataset-report?${query}`, {headers: headers(false)}
  ));
  const lines = [
    `Осмысленных оценок: ${report.meaningful_labels} из 200 (осталось ${report.labels_to_go}); ` +
      `поточечных ${report.pointwise}, пар ${report.pairs}.`,
    `Трудные случаи: ${report.hard_negatives} «ложных лидеров», ${report.model_disagreement} спорных для моделей.`,
  ];
  if (report.split) {
    lines.push(`Сплит «${report.split.name}»: ${report.split.frozen ? 'заморожен' : 'не заморожен'}.`);
  }
  report.warnings.forEach(warning => lines.push(`⚠ ${warning}`));
  if (report.ready) lines.push('Датасет готов к первому обучению.');
  box.replaceChildren(...lines.map(line => element('div', line)));
}

async function annotationSplitExists(name) {
  if (!name) return false;
  const response = await fetch(`/v1/annotation/splits/${encodeURIComponent(name)}`, {headers: headers(false)});
  if (response.status === 404) return false;
  await asJson(response);
  return true;
}

function annotationCard(item, title) {
  const card = element('article', undefined, 'annotation-card');
  card.append(element('h4', `${title ? title + ': ' : ''}${item.vacancy_title}`));
  const meta = [item.vacancy_company, item.vacancy_location, item.work_format, item.salary_text]
    .filter(Boolean).join(' · ');
  card.append(element('div', meta, 'meta'));
  if (item.required_skills?.length) card.append(element('div', `Навыки: ${item.required_skills.join(', ')}`, 'meta'));
  if (item.employment_types?.length) card.append(element('div', `Занятость: ${item.employment_types.join(', ')}`, 'meta'));
  card.append(element('div', item.vacancy_description || 'Описание отсутствует.', 'description'));
  return card;
}

function annotationReasonPicker(prefix) {
  const wrap = element('div', undefined, 'annotation-reasons');
  ANNOTATION_REASONS.forEach(([value, label]) => {
    const box = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.name = `annotation-reason-${prefix}`;
    input.value = value;
    box.append(input, document.createTextNode(label));
    wrap.append(box);
  });
  return wrap;
}

function chosenReasons(prefix) {
  return [...document.querySelectorAll(`input[name="annotation-reason-${prefix}"]:checked`)]
    .map(input => input.value);
}

function samplingContext(item) {
  return {
    sampling_reason: item.sampling_reason || null,
    current_rank: item.current_rank ?? null,
    ltr_rank: item.ltr_rank ?? null,
    current_score: item.current_score ?? null,
    ltr_score: item.ltr_score ?? null,
  };
}

function renderAnnotationCurrent() {
  const work = document.querySelector('#annotation-work');
  const progress = document.querySelector('#annotation-progress');
  const {mode, index} = annotationState;
  const list = mode === 'pointwise' ? annotationState.items : annotationState.pairs;
  progress.textContent = list.length ? `${Math.min(index + 1, list.length)} из ${list.length} в очереди` : '';
  if (index >= list.length) {
    work.replaceChildren(element('div', 'Очередь закончилась. Загрузите её снова или обновите отчёт.', 'hint'));
    refreshAnnotationReport().catch(showError);
    return;
  }
  const confidence = document.createElement('select');
  confidence.id = 'annotation-confidence';
  [['', 'Уверенность: не указана'], ['low', 'Низкая'], ['medium', 'Средняя'], ['high', 'Высокая']].forEach(([v, t]) => {
    const option = document.createElement('option'); option.value = v; option.textContent = t; confidence.append(option);
  });
  const actions = element('div', undefined, 'annotation-actions');
  const submit = (path, body) => submitAnnotation(path, body).catch(showError);
  annotationState.shownAt = Date.now();
  if (mode === 'pointwise') {
    const item = annotationState.items[index];
    const cards = element('div', undefined, 'annotation-cards');
    cards.append(annotationCard(item));
    [['relevant', 'Релевантна', true], ['maybe', 'Возможно'], ['not_relevant', 'Не релевантна']].forEach(([label, text, primary]) => {
      const button = element('button', text, primary ? 'primary' : undefined);
      button.type = 'button';
      button.addEventListener('click', () => submit('pointwise', {
        resume_id: annotationState.resumeId, vacancy_id: item.vacancy_id, label,
        reasons: chosenReasons('a'), confidence: confidence.value || null, sampling: samplingContext(item),
      }));
      actions.append(button);
    });
    const skip = element('button', 'Пропустить'); skip.type = 'button';
    skip.addEventListener('click', () => { annotationState.index += 1; renderAnnotationCurrent(); });
    actions.append(skip);
    work.replaceChildren(cards, annotationReasonPicker('a'), confidence, actions);
  } else {
    const pair = annotationState.pairs[index];
    const cards = element('div', undefined, 'annotation-cards');
    cards.append(annotationCard(pair.vacancy_a, 'A'), annotationCard(pair.vacancy_b, 'B'));
    const reasonsWrap = element('div', undefined, 'annotation-cards');
    const a = element('div'); a.append(element('div', 'Причины для A'), annotationReasonPicker('a'));
    const b = element('div'); b.append(element('div', 'Причины для B'), annotationReasonPicker('b'));
    reasonsWrap.append(a, b);
    [['a_better', 'A лучше', true], ['b_better', 'B лучше', true], ['both_equal', 'Одинаково'], ['neither', 'Обе не подходят']].forEach(([preference, text, primary]) => {
      const button = element('button', text, primary ? 'primary' : undefined);
      button.type = 'button';
      button.addEventListener('click', () => submit('pairwise', {
        resume_id: annotationState.resumeId,
        vacancy_a_id: pair.vacancy_a.vacancy_id, vacancy_b_id: pair.vacancy_b.vacancy_id, preference,
        a_reasons: chosenReasons('a'), b_reasons: chosenReasons('b'),
        confidence: confidence.value || null, sampling: samplingContext(pair.vacancy_a),
      }));
      actions.append(button);
    });
    const skip = element('button', 'Пропустить'); skip.type = 'button';
    skip.addEventListener('click', () => { annotationState.index += 1; renderAnnotationCurrent(); });
    actions.append(skip);
    work.replaceChildren(cards, reasonsWrap, confidence, actions);
  }
}

async function submitAnnotation(path, body) {
  const userId = annotationUserId();
  await asJson(await fetchWithTimeout(`/v1/annotation/${path}?user_id=${encodeURIComponent(userId)}`, {
    method: 'POST', headers: headers(true), body: JSON.stringify(body)
  }));
  annotationState.index += 1;
  renderAnnotationCurrent();
}

async function loadAnnotationQueue() {
  const userId = annotationUserId();
  const resumeId = document.querySelector('#annotation-resume').value;
  if (!resumeId) throw new Error('У пользователя нет резюме для разметки');
  annotationState.resumeId = resumeId;
  annotationState.mode = document.querySelector('#annotation-mode').value;
  annotationState.index = 0;
  const base = `user_id=${encodeURIComponent(userId)}&resume_id=${encodeURIComponent(resumeId)}`;
  if (annotationState.mode === 'pointwise') {
    const queue = await asJson(await fetchWithTimeout(`/v1/annotation/queue?${base}&limit=50`, {headers: headers(false)}));
    annotationState.items = queue.items;
  } else {
    const queue = await asJson(await fetchWithTimeout(`/v1/annotation/pair-queue?${base}&limit=25`, {headers: headers(false)}));
    annotationState.pairs = queue.items;
  }
  renderAnnotationCurrent();
}

document.querySelector('#annotation-load').addEventListener('click', () => loadAnnotationQueue().catch(showError));
document.querySelector('#annotation-refresh-report').addEventListener('click', () => refreshAnnotationReport().catch(showError));
document.querySelector('#annotation-create-split').addEventListener('click', async () => {
  try {
    const name = document.querySelector('#annotation-split-name').value.trim();
    await asJson(await fetchWithTimeout('/v1/annotation/splits', {
      method: 'POST', headers: headers(true), body: JSON.stringify({name})
    }));
    showStatus(`Сплит «${name}» создан.`);
    await refreshAnnotationReport();
  } catch (error) { showError(error); }
});
document.querySelector('#annotation-freeze-split').addEventListener('click', async () => {
  const name = document.querySelector('#annotation-split-name').value.trim();
  if (!window.confirm(`Заморозить сплит «${name}»? Проверочные вакансии навсегда исключаются из обучения.`)) return;
  try {
    await asJson(await fetchWithTimeout(`/v1/annotation/splits/${encodeURIComponent(name)}/freeze`, {
      method: 'POST', headers: headers(false)
    }));
    showStatus(`Сплит «${name}» заморожен.`);
    await refreshAnnotationReport();
  } catch (error) { showError(error); }
});
document.querySelector('[data-menu="annotation"]').addEventListener('click', () => {
  loadAnnotationResumes().then(refreshAnnotationReport).catch(showError);
});

// ── Application analytics (CRM) ─────────────────────────────────────────
function crmPercent(stat) {
  if (!stat || stat.value == null) return '—';
  const range = stat.low != null ? ` (${Math.round(stat.low * 100)}–${Math.round(stat.high * 100)}%)` : '';
  return `${Math.round(stat.value * 100)}% · ${stat.successes}/${stat.n}${range}`;
}

function renderCrmTable(funnel) {
  const table = document.querySelector('#crm-table');
  const head = element('tr');
  ['Группа', 'Откликов', 'Отправлено', 'Ждут ответа', 'Ответили', 'Собеседования', 'Офферы', 'Отказы']
    .forEach(title => head.append(element('th', title)));
  const rows = [funnel.totals, ...funnel.groups].map((group, index) => {
    const row = element('tr', undefined, group.response.low_sample ? 'low' : undefined);
    row.append(element('td', index === 0 ? 'Все' : group.group));
    [group.applications, group.submitted, group.pending].forEach(value => row.append(element('td', String(value), 'num')));
    [group.response, group.interview, group.offer, group.rejection]
      .forEach(stat => row.append(element('td', crmPercent(stat), 'num')));
    return row;
  });
  table.replaceChildren(head, ...rows);
}

async function loadCrm() {
  const userId = userIdInput.value.trim();
  const insights = document.querySelector('#crm-insights');
  if (!userId) { insights.textContent = ''; document.querySelector('#crm-table').replaceChildren(); return; }
  const groupBy = document.querySelector('#crm-group-by').value;
  const [funnel, found] = await Promise.all([
    asJson(await fetchWithTimeout(`/v1/users/${userId}/crm/funnel?group_by=${encodeURIComponent(groupBy)}`, {headers: headers(false)})),
    asJson(await fetchWithTimeout(`/v1/users/${userId}/crm/insights`, {headers: headers(false)})),
  ]);
  renderCrmTable(funnel);
  const lines = found.findings.map(finding => element('div', finding.statement));
  lines.push(element('div', funnel.notes.join(' '), 'hint'));
  insights.replaceChildren(...lines);
}

document.querySelector('#crm-refresh').addEventListener('click', () => loadCrm().catch(showError));
document.querySelector('#crm-group-by').addEventListener('change', () => loadCrm().catch(showError));
document.querySelector('[data-menu="crm"]').addEventListener('click', () => loadCrm().catch(showError));

// ── Job strategy recommendations ────────────────────────────────────────
function renderStrategyItem(item) {
  const box = element('div', undefined, 'annotation-card');
  box.append(element('h4', item.statement));
  const evidence = item.evidence || {};
  if (evidence.confounders?.length) box.append(element('div', `Осторожно: ${evidence.confounders.join(' ')} Это корреляция в небольшой выборке, а не причина.`, 'meta'));
  const action = item.payload?.action === 'set_active_resume'
    ? `При принятии активным станет резюме «${item.payload.label}».`
    : 'Совет: система ничего не изменит сама.';
  box.append(element('div', action, 'meta'));
  if (item.status === 'proposed') {
    const actions = element('div', undefined, 'annotation-actions');
    [['accept', 'Принять', 'primary'], ['reject', 'Отклонить']].forEach(([decision, text, cls]) => {
      const button = element('button', text, cls);
      button.type = 'button';
      button.addEventListener('click', async () => {
        try {
          const userId = userIdInput.value.trim();
          await asJson(await fetchWithTimeout(`/v1/users/${userId}/strategy/recommendations/${item.id}/decision`, {
            method: 'POST', headers: headers(true), body: JSON.stringify({decision})
          }));
          await loadStrategy();
        } catch (error) { showError(error); }
      });
      actions.append(button);
    });
    box.append(actions);
  } else {
    box.append(element('div', item.status === 'accepted' ? 'Принято.' : 'Отклонено.', 'meta'));
  }
  return box;
}

async function loadStrategy() {
  const userId = userIdInput.value.trim();
  const list = document.querySelector('#strategy-list');
  if (!userId) { list.replaceChildren(); return; }
  const items = await asJson(await fetchWithTimeout(`/v1/users/${userId}/strategy/recommendations`, {headers: headers(false)}));
  list.replaceChildren(...(items.length ? items.map(renderStrategyItem) : [element('div', 'Рекомендаций пока нет.', 'hint')]));
}

document.querySelector('#strategy-generate').addEventListener('click', async () => {
  try {
    const userId = userIdInput.value.trim();
    if (!userId) throw new Error('Сначала создайте или укажите User ID');
    const result = await asJson(await fetchWithTimeout(`/v1/users/${userId}/strategy/recommendations/generate`, {
      method: 'POST', headers: headers(false)
    }));
    await loadStrategy();
    if (!result.created.length) {
      const why = result.no_recommendation_because.join(' ') || 'Новых рекомендаций нет.';
      document.querySelector('#strategy-list').prepend(element('div', `Рекомендаций нет: ${why}`, 'hint'));
    }
  } catch (error) { showError(error); }
});
document.querySelector('[data-menu="crm"]').addEventListener('click', () => loadStrategy().catch(showError));
