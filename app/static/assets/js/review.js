const queue = document.querySelector('#queue');
const statusNode = document.querySelector('#status');
const keyInput = document.querySelector('#api-key');
const evidenceObjectUrls = new Set();
keyInput.value = sessionStorage.getItem('reviewApiKey') || '';
const headers = () => keyInput.value ? {'X-API-Key': keyInput.value} : {};
const element = (name, text, className) => {
  const node = document.createElement(name);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
let currentLanguage = localStorage.getItem('dashboardLanguage') === 'en' ? 'en' : 'ru';
const selectedApplicationId = new URLSearchParams(window.location.search).get('application_id');
const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const LANGUAGE_OPTIONS = [
  {code: 'ru', names: {ru: 'Русский', en: 'Russian'}},
  {code: 'en', names: {ru: 'Английский', en: 'English'}}
];
const tr = (ru, en) => currentLanguage === 'en' ? en : ru;
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
const applicationStatusLabels = {
  ru: {
    draft: 'Черновик', saved: 'Сохранена', awaiting_review: 'Ожидает решения',
    approved: 'Принята', rejected: 'Отклонена мной', employer_rejected: 'Отказ работодателя', skipped: 'Пропущена',
    submitted: 'Отправлена', interview: 'Собеседование'
  },
  en: {
    draft: 'Draft', saved: 'Saved', awaiting_review: 'Awaiting decision',
    approved: 'Accepted', rejected: 'Rejected by me', employer_rejected: 'Employer rejection', skipped: 'Skipped',
    submitted: 'Submitted', interview: 'Interview'
  }
};
const editableApplicationStatuses = [
  'draft', 'saved', 'awaiting_review', 'approved',
  'rejected', 'employer_rejected', 'skipped', 'submitted', 'interview'
];

function createKeySkillTags(skills) {
  const normalizedSkills = Array.from(skills || []).map(String).filter(Boolean);
  if (!normalizedSkills.length) return null;
  const block = element('div');
  block.append(element(
    'div',
    tr('Ключевые навыки:', 'Key skills:'),
    'meta'
  ));
  const tags = element('div', undefined, 'chips');
  normalizedSkills.forEach(skill => tags.append(element('span', skill, 'chip')));
  block.append(tags);
  return block;
}

function createStatusEditor(item) {
  const editor = element('div', undefined, 'status-editor');
  const select = element('select');
  select.setAttribute('aria-label', tr('Статус отклика', 'Application status'));
  editableApplicationStatuses.forEach(status => {
    const option = element('option', applicationStatusLabels[currentLanguage][status]);
    option.value = status;
    option.selected = item.status === status;
    select.append(option);
  });
  const save = element('button', tr('Изменить статус', 'Change status'));
  save.type = 'button';
  save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      const response = await fetch(`/v1/applications/${item.id}/status`, {
        method: 'PATCH',
        headers: {...headers(), 'Content-Type': 'application/json'},
        body: JSON.stringify({status: select.value})
      });
      if (!response.ok) throw new Error(`Status update failed (${response.status})`);
      await loadQueue();
    } catch (error) {
      showError(error);
    } finally {
      save.disabled = false;
    }
  });
  editor.append(select, save);
  return editor;
}

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

function updateLanguage() {
  document.documentElement.lang = currentLanguage;
  document.title = tr('Очередь проверки откликов', 'Application review queue');
  document.querySelector('#review-eyebrow').textContent = tr('Проверка человеком', 'Human checkpoint');
  document.querySelector('#review-title').textContent = tr('Очередь проверки', 'Review queue');
  document.querySelector('#api-key-label').textContent = tr('API-ключ', 'API key');
  document.querySelector('#refresh').textContent = tr('Обновить', 'Refresh');
  document.querySelector('#nav-vacancies').textContent = tr('Все вакансии', 'All vacancies');
  document.querySelector('#nav-search').textContent = tr('Поиск', 'Search');
  document.querySelector('#nav-materials').textContent = tr('Материалы и решения', 'Materials and decisions');
  document.querySelector('#nav-blacklist').textContent = tr('Чёрный список', 'Blacklist');
  document.querySelector('#nav-resume').textContent = tr('Резюме', 'Resumes');
  document.querySelector('#nav-sessions').textContent = tr('Сессии сайтов', 'Site sessions');
  document.querySelector('#nav-model').textContent = tr('Модель', 'Model');
  document.querySelector('#nav-access').textContent = tr('Профиль и доступ', 'Profile and access');
  renderLanguageSelector();
}
async function decide(applicationId, decision, buttons) {
  buttons.forEach(button => button.disabled = true);
  const response = await fetch(`/v1/applications/${applicationId}/decision`, {
    method: 'POST', headers: {...headers(), 'Content-Type': 'application/json'},
    body: JSON.stringify({decision})
  });
  if (!response.ok) throw new Error(`Decision failed (${response.status})`);
  if (decision === 'reject' && !selectedApplicationId) {
    const card = buttons[0]?.closest('article');
    if (card) card.remove();
    const remaining = queue.querySelectorAll('article').length;
    statusNode.textContent = `${remaining} application${remaining === 1 ? '' : 's'} awaiting review`;
    if (!remaining) queue.append(element('div', 'Nothing is waiting for review.', 'empty'));
  } else {
    await loadQueue();
  }
}
async function retryTask(applicationId, buttons) {
  buttons.forEach(button => button.disabled = true);
  const response = await fetch(`/v1/applications/${applicationId}/retry`, {method: 'POST', headers: headers()});
  if (!response.ok) throw new Error(`Retry failed (${response.status})`);
  await loadQueue();
}
async function prepareBrowserReview(applicationId, buttons) {
  buttons.forEach(button => button.disabled = true);
  const response = await fetch(`/v1/applications/${applicationId}/prepare-browser-review`, {
    method: 'POST', headers: {...headers(), 'Content-Type': 'application/json'},
    body: JSON.stringify({confirmation: 'prepare_without_submission'})
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Browser preparation failed (${response.status})`);
  }
  await loadQueue();
}
async function saveMaterials(item, coverLetter, answerInputs, buttons) {
  buttons.forEach(button => button.disabled = true);
  const screening_answers = answerInputs.map(input => ({field_id: input.dataset.fieldId, answer: input.value || null}));
  const response = await fetch(`/v1/applications/${item.id}/materials`, {
    method: 'PATCH', headers: {...headers(), 'Content-Type': 'application/json'},
    body: JSON.stringify({cover_letter_text: coverLetter.value, screening_answers})
  });
  if (!response.ok) throw new Error(`Save failed (${response.status})`);
  await loadQueue();
}
async function resumeHumanAction(item, buttons) {
  buttons.forEach(button => button.disabled = true);
  const response = await fetch(`/v1/applications/${item.id}/resume`, {
    method: 'POST', headers: {...headers(), 'Content-Type': 'application/json'},
    body: JSON.stringify({confirmation: 'confirmed in review interface'})
  });
  if (!response.ok) throw new Error(`Resume failed (${response.status})`);
  await loadQueue();
}
async function loadEvidence(artifact, button, holder) {
  button.disabled = true;
  const response = await fetch(artifact.url, {headers: headers()});
  if (!response.ok) throw new Error(`Evidence unavailable (${response.status})`);
  const objectUrl = URL.createObjectURL(await response.blob());
  const image = element('img'); image.src = objectUrl; image.alt = `Evidence ${artifact.kind}`;
  evidenceObjectUrls.add(objectUrl);
  holder.replaceChildren(image);
}
function renderItem(item) {
  const card = element('article');
  const top = element('div', undefined, 'topline');
  const title = element('div');
  title.append(element('div', item.company, 'company'), element('h2', item.vacancy_title));
  if (item.work_format) {
    const labels = workFormatLabels[currentLanguage];
    title.append(element('span', labels[item.work_format] || labels.unspecified, 'badge'));
  }
  Array.from(item.employment_types || []).forEach(employmentType => {
    const label = employmentTypeLabels[currentLanguage][employmentType];
    if (label) title.append(element('span', label, 'badge'));
  });
  if (item.salary_text) title.append(element('span', item.salary_text, 'badge salary'));
  top.append(title, element('div', `${item.match_score}%`, 'score'));
  card.append(top);
  if (item.vacancy_summary) {
    const truncated = item.vacancy_summary.length > 120 ? item.vacancy_summary.substring(0, 120) + '…' : item.vacancy_summary;
    const summaryNode = element('span', truncated, 'summary-affordance');
    summaryNode.setAttribute('tabindex', '0');
    summaryNode.setAttribute('title', item.vacancy_summary);
    card.append(summaryNode);
  }
  const keySkills = createKeySkillTags(item.key_skills);
  if (keySkills) card.append(keySkills);
  const meta = element('div', undefined, 'meta');
  meta.append(
    tr('Резюме: ', 'CV: '),
    element('strong', item.selected_cv_filename || tr('не выбрано', 'not selected'))
  );
  card.append(meta);
  card.append(
    element(
      'div',
      `${tr('Процесс', 'Workflow')}: ${item.current_workflow_state}`,
      'meta'
    )
  );
  card.append(element(
    'div',
    `${tr('Статус', 'Status')}: ${applicationStatusLabels[currentLanguage][item.status] || item.status}`,
    'meta'
  ));
  const missingSkills = Array.from(item.missing_required_skills || []);
  const filteredWarnings = [];
  (item.warnings || []).forEach(warning => {
    const prefix = 'Missing required skill:';
    if (typeof warning === 'string' && warning.startsWith(prefix)) {
      const skill = warning.slice(prefix.length).trim();
      if (skill && !missingSkills.some(s => s.toLowerCase() === skill.toLowerCase())) {
        missingSkills.push(skill);
      }
    } else {
      filteredWarnings.push(warning);
    }
  });
  if (filteredWarnings.length) {
    const warnings = element('ul', undefined, 'warnings');
    filteredWarnings.forEach(warning => warnings.append(element('li', warning)));
    card.append(warnings);
  }
  if (missingSkills.length) {
    const skillsHeading = element(
      'div',
      tr('Отсутствующие навыки:', 'Missing required skills:'),
      'meta'
    );
    const tagsContainer = element('div', undefined, 'chips');
    missingSkills.forEach(skill => tagsContainer.append(element('span', skill, 'chip')));
    card.append(skillsHeading, tagsContainer);
  }
  const materials = element('div', undefined, 'materials');
  const coverLabel = element('label', tr('Сопроводительное письмо', 'Cover letter'));
  const coverLetter = element('textarea'); coverLetter.value = item.cover_letter_text || '';
  coverLetter.readOnly = item.status !== 'awaiting_review';
  coverLetter.setAttribute('aria-label', `Cover letter for ${item.company}`);
  coverLabel.append(coverLetter); materials.append(coverLabel);
  const answerInputs = item.screening_answers.map(answer => {
    const label = element('label', answer.label + (answer.is_required ? ' *' : ''));
    const input = element('textarea'); input.value = answer.answer || '';
    input.readOnly = item.status !== 'awaiting_review';
    input.dataset.fieldId = answer.field_id;
    input.setAttribute('aria-label', answer.label);
    const note = element('span', `Source: ${answer.answer_source}${answer.warning ? ` · ${answer.warning}` : ''}`, 'field-note');
    label.append(input, note); materials.append(label); return input;
  });
  card.append(materials);
  if (item.active_human_action) {
    const checkpoint = element('div', undefined, 'human-action');
    checkpoint.append(
      element('strong', `Human action: ${item.active_human_action.kind}`),
      element('div', item.active_human_action.instructions),
      element('div', item.active_human_action.evidence.join(' · '), 'field-note')
    );
    const gallery = element('div', undefined, 'evidence-gallery');
    item.active_human_action.artifacts.forEach(artifact => {
      const holder = element('div');
      const loadButton = element('button', `Load ${artifact.kind} (${Math.ceil(artifact.size_bytes / 1024)} KiB)`);
      loadButton.type = 'button';
      loadButton.addEventListener('click', () => loadEvidence(artifact, loadButton, holder).catch(showError));
      holder.append(loadButton); gallery.append(holder);
    });
    checkpoint.append(gallery);
    card.append(checkpoint);
  }
  const actions = element('div', undefined, 'actions');
  const specs = item.status === 'awaiting_review'
    ? [
        ['approve', tr('Принять', 'Approve'), 'approve'],
        ['reject', tr('Отклонить', 'Reject'), 'reject'],
        ['skip', tr('Пропустить', 'Skip'), '']
      ]
    : [];
  const buttons = specs.map(([decision, label, className]) => {
    const button = element('button', label, className); button.type = 'button';
    button.addEventListener('click', () => decide(item.id, decision, buttons).catch(showError));
    actions.append(button); return button;
  });
  if (item.status === 'awaiting_review') {
    const save = element('button', tr('Сохранить черновик', 'Save draft'));
    save.type = 'button';
    buttons.push(save);
    save.addEventListener('click', () => saveMaterials(item, coverLetter, answerInputs, buttons).catch(showError));
    actions.append(save);
  }
  if (item.adapter_name === 'greenhouse' && ['scheduled', 'waiting_for_user'].includes(item.current_workflow_state)) {
    const prepare = element('button', 'Prepare Greenhouse form (no submit)', 'approve');
    prepare.type = 'button'; buttons.push(prepare);
    prepare.addEventListener('click', () => prepareBrowserReview(item.id, buttons).catch(showError));
    actions.append(prepare);
  }
  if (item.active_human_action && item.active_human_action.kind !== 'application_review') {
    const resume = element('button', 'Resume after human action', 'approve');
    resume.type = 'button'; buttons.push(resume);
    resume.addEventListener('click', () => resumeHumanAction(item, buttons).catch(showError));
    actions.append(resume);
  }
  if (['failed', 'interrupted', 'cancelled', 'waiting_for_external_system'].includes(item.current_workflow_state)) {
    const retry = element('button', 'Retry'); retry.type = 'button'; buttons.push(retry);
    retry.addEventListener('click', () => retryTask(item.id, buttons).catch(showError));
    actions.append(retry);
  }
  const sourceBtn = element('a', tr('Открыть вакансию', 'Open vacancy'));
  sourceBtn.href = item.source_url;
  sourceBtn.target = '_blank';
  sourceBtn.rel = 'noopener noreferrer';
  sourceBtn.className = 'link-btn';
  actions.append(sourceBtn);
  card.append(actions, createStatusEditor(item)); return card;
}
function showError(error) { statusNode.textContent = error.message; }
async function loadQueue() {
  evidenceObjectUrls.forEach(objectUrl => URL.revokeObjectURL(objectUrl));
  evidenceObjectUrls.clear();
  sessionStorage.setItem('reviewApiKey', keyInput.value);
  statusNode.textContent = tr('Загрузка…', 'Loading…'); queue.replaceChildren();
  const queueUrl = selectedApplicationId
    ? `/v1/review-queue?application_id=${encodeURIComponent(selectedApplicationId)}`
    : '/v1/review-queue';
  const response = await fetch(queueUrl, {headers: headers()});
  if (!response.ok) throw new Error(`Queue unavailable (${response.status})`);
  const items = await response.json();
  items.forEach(item => queue.append(renderItem(item)));
  if (!items.length) {
    queue.append(
      element(
        'div',
        selectedApplicationId
          ? tr('Материалы выбранной вакансии не найдены.', 'Selected application materials were not found.')
          : tr('Нет откликов, ожидающих проверки.', 'Nothing is waiting for review.'),
        'empty'
      )
    );
  }
  if (selectedApplicationId) {
    statusNode.textContent = items.length
      ? tr('Материалы выбранной вакансии', 'Selected application materials')
      : tr('Выбранная вакансия не найдена', 'Selected application was not found');
  } else {
    statusNode.textContent = currentLanguage === 'en'
      ? `${items.length} application${items.length === 1 ? '' : 's'} awaiting review`
      : `Ожидают проверки: ${items.length}`;
  }
}
document.querySelector('#refresh').addEventListener('click', () => loadQueue().catch(showError));
function selectLanguage(languageCode) {
  if (!LANGUAGE_OPTIONS.some(option => option.code === languageCode)) return;
  if (currentLanguage === languageCode) {
    setLanguageMenuOpen(false);
    return;
  }
  currentLanguage = languageCode;
  localStorage.setItem('dashboardLanguage', currentLanguage);
  updateLanguage();
  loadQueue().catch(showError);
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
updateLanguage();
loadQueue().catch(showError);
