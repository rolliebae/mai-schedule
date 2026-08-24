'use strict';

const DB_NAME = 'MAI_ScheduleDB_v413';
const STORE_NAME = 'scheduleData';
const DB_VERSION = 1;
const BUNDLED_DATABASE = 'database_v413.json';
const APP_VERSION = '4.1.5';
const DATABASE_SCHEMA = '413';
const GROUPS_STORAGE_KEY = 'mai_selected_groups_v413';
const ACTIVE_GROUP_STORAGE_KEY = 'mai_active_group_v413';
const DEFAULT_GROUP = 'М9О-217БВ-25';
const TRIM_STORAGE_KEY = 'mai_trim_prefix';

const PAIR_NUM_TO_TIME = {
  1: '09:00:00', 2: '10:45:00', 3: '13:00:00',
  4: '14:45:00', 5: '16:30:00', 6: '18:15:00'
};
const TIME_TO_PAIR_IDX = {
  '09:00:00': 0, '10:45:00': 1, '13:00:00': 2,
  '14:45:00': 3, '16:30:00': 4, '18:15:00': 5
};
const PAIR_TIMES_RANGES = {
  1: '09:00–10:30', 2: '10:45–12:15', 3: '13:00–14:30',
  4: '14:45–16:15', 5: '16:30–18:00', 6: '18:15–19:45'
};
const WEEKDAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
const LESSON_TYPES_MAP = {
  'ЛК': 'Лекция', 'ПЗ': 'Семинар', 'ЛР': 'Лабораторная',
  'Экзамен': 'Экзамен', 'Зачет': 'Зачёт', 'Консультация': 'Консультация', 'КП': 'Курсовой проект'
};

let decompressor = null;
let selectedGroups = [];
let activeGroup = null;
let currentWeekStart = null;
let lastRoomStatusesResult = null;

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const app = $('#app');
const bootError = $('#boot-error');
const bootErrorText = $('#boot-error-text');
const dbStatus = $('#db-status');
const updateDatabaseBtn = $('#update-database-btn');
const databaseFileInput = $('#database-file-input');
const loadDatabaseBtn = $('#load-database-btn');

const groupInput = $('#group-input');
const groupsDatalist = $('#groups-datalist');
const selectedGroupsContainer = $('#selected-groups-container');
const scheduleEmpty = $('#schedule-empty');
const scheduleDisplay = $('#schedule-display');
const prevWeekBtn = $('#prev-week-btn');
const nextWeekBtn = $('#next-week-btn');
const weekDatesTitle = $('#week-dates-title');
const scheduleContent = $('#schedule-content');

const teacherForm = $('#teacher-form');
const teacherGroup = $('#teacher-group');
const teacherQuery = $('#teacher-query');
const teacherStart = $('#teacher-start');
const teacherEnd = $('#teacher-end');
const teacherResult = $('#teacher-result');
const teacherSource = $('#teacher-source');
const fileModeNotice = $('#file-mode-notice');
const lectorsDatalist = $('#lectors-datalist');

const findFreeForm = $('#find-free-form');
const findDateInput = $('#find-date');
const findBuildingInput = $('#find-building');
const findPairInput = $('#find-pair');
const trimPrefixCheckbox = $('#trim-prefix-checkbox');
const freeRoomsResultDiv = $('#free-rooms-result');

const checkStatusForm = $('#check-status-form');
const checkDateInput = $('#check-date');
const checkPairInput = $('#check-pair');
const checkRoomInput = $('#check-room');
const statusResultDiv = $('#status-result');
const roomsDatalist = $('#rooms-datalist');

const lessonModal = $('#lesson-modal');
const modalRoomTitle = $('#modal-room-title');
const modalLessonsList = $('#modal-lessons-list');
const weekSelectorModal = $('#week-selector-modal');
const gotoCurrentWeekBtn = $('#goto-current-week-btn');
const modalWeeksList = $('#modal-weeks-list');

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) db.createObjectStore(STORE_NAME);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function dbGet(db, key) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function dbPut(db, key, value) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const req = tx.objectStore(STORE_NAME).put(value, key);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

function validateDatabase(database) {
  if (!database || !database.dictionaries || !database.schedules ||
      !database.meta_patterns || !database.semester_info ||
      !database.semester_info.global_week_map || !database.inverted_indices) {
    throw new Error('Неверный формат базы расписания');
  }
  return database;
}

function isDirectFileMode() {
  return window.location.protocol === 'file:';
}

async function fetchHttpDatabase() {
  const url = `${BUNDLED_DATABASE}?v=${encodeURIComponent(APP_VERSION)}&t=${Date.now()}`;
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`database_v413.json: HTTP ${response.status}`);
  }
  const database = validateDatabase(await response.json());
  if (String(database.version || '') !== DATABASE_SCHEMA) {
    throw new Error(`Неожиданная версия базы: ${database.version || 'не указана'}`);
  }
  return database;
}

async function fetchBundledDatabase() {
  // Under the local HTTP server the JSON file is the source of truth.
  // Do not trust the JS wrapper or IndexedDB first: both can be stale after a rebuild.
  if (!isDirectFileMode()) {
    return fetchHttpDatabase();
  }

  // file:// cannot fetch sibling JSON in modern browsers. Use the generated JS wrapper.
  if (window.__MAI_BUNDLED_DATABASE__) {
    return validateDatabase(window.__MAI_BUNDLED_DATABASE__);
  }
  if (window.__MAI_SAMPLE_DATABASE__) {
    return validateDatabase(window.__MAI_SAMPLE_DATABASE__);
  }
  throw new Error('Полная база не собрана. Запусти start.bat или update_database.bat.');
}

function getISOWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return [d.getUTCFullYear(), Math.ceil((((d - yearStart) / 86400000) + 1) / 7)];
}

function getDateOfISOWeek(week, year) {
  const simple = new Date(year, 0, 4);
  const day = simple.getDay() || 7;
  const monday = new Date(simple);
  monday.setDate(simple.getDate() - day + 1 + (week - 1) * 7);
  monday.setHours(0, 0, 0, 0);
  return monday;
}

function getMonday(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  const day = d.getDay() || 7;
  d.setDate(d.getDate() - day + 1);
  return d;
}

function formatDate(date) {
  return `${String(date.getDate()).padStart(2, '0')}.${String(date.getMonth() + 1).padStart(2, '0')}.${date.getFullYear()}`;
}

function formatDateShort(date) {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(date).replace('.', '');
}

function toInputDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function parseInputDate(value) {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year, month - 1, day);
}

class ScheduleDecompressor {
  constructor(database) {
    this.db = database;
    this.dictionaries = database.dictionaries;
    this.metaPatterns = database.meta_patterns;
    this.roomsById = this.dictionaries.rooms;
    this.groupNamesById = this.dictionaries.groups;
    this.groupIdByName = Object.fromEntries(this.groupNamesById.map((name, id) => [name, id]));
    this.roomsToId = Object.fromEntries(this.roomsById.map((room, id) => [room, id]));
    this.timesById = ['09:00:00', '10:45:00', '13:00:00', '14:45:00', '16:30:00', '18:15:00'];
    this.weekTupleToIdx = new Map();
    database.semester_info.global_week_map.forEach(([year, week], index) => {
      this.weekTupleToIdx.set(`${year}-${week}`, index);
    });
  }

  _getWeekIdForDate(groupId, date) {
    if (groupId === undefined || groupId < 0 || groupId >= this.db.schedules.length) return null;
    const schedule = this.db.schedules[groupId];
    if (!schedule) return null;
    const [year, week] = getISOWeek(date);
    const globalIdx = this.weekTupleToIdx.get(`${year}-${week}`);
    if (globalIdx === undefined) return null;
    const weekId = schedule[globalIdx];
    return weekId > 0 ? weekId : null;
  }

  _lessonById(lessonId) {
    if (!lessonId) return null;
    const [subjectId, lectorId, typeId, roomId] = this.metaPatterns.lessons[lessonId];
    return {
      subject: this.dictionaries.subjects[subjectId],
      lector: this.dictionaries.lectors[lectorId],
      type: this.dictionaries.types[typeId],
      room: this.dictionaries.rooms[roomId] || '—',
      lectorId,
      roomId
    };
  }

  getScheduleForDay(groupName, targetDate) {
    const groupId = this.groupIdByName[groupName];
    if (groupId === undefined) return [];
    const weekday = (targetDate.getDay() || 7) - 1;
    if (weekday > 5) return [];
    const weekId = this._getWeekIdForDate(groupId, targetDate);
    if (!weekId) return [];
    const weekPattern = this.metaPatterns.weeks[weekId];
    const dayId = weekPattern?.[weekday];
    const dayPattern = dayId ? this.metaPatterns.days[dayId] : null;
    if (!dayPattern) return [];
    return dayPattern.flatMap((lessonId, pairIdx) => {
      const lesson = this._lessonById(lessonId);
      return lesson ? [{ pair: pairIdx + 1, ...lesson }] : [];
    });
  }

  isWeekEmptyForGroup([year, week], groupName) {
    const groupId = this.groupIdByName[groupName];
    if (groupId === undefined) return true;
    const globalIdx = this.weekTupleToIdx.get(`${year}-${week}`);
    if (globalIdx === undefined) return true;
    const weekId = this.db.schedules[groupId]?.[globalIdx];
    if (!weekId) return true;
    const weekPattern = this.metaPatterns.weeks[weekId];
    return !weekPattern?.some(dayId => dayId && this.metaPatterns.days[dayId]?.some(Boolean));
  }

  getNonEmptyWeeks(groupName) {
    return this.db.semester_info.global_week_map.filter(tuple => !this.isWeekEmptyForGroup(tuple, groupName));
  }

  getNearestWeek(groupName, target = new Date()) {
    const weeks = this.getNonEmptyWeeks(groupName).map(([year, week]) => getDateOfISOWeek(week, year));
    if (!weeks.length) return getMonday(target);
    return weeks.reduce((best, item) =>
      Math.abs(item - target) < Math.abs(best - target) ? item : best
    );
  }

  getDatabaseBounds() {
    const map = this.db.semester_info.global_week_map;
    if (!map.length) return null;
    const first = getDateOfISOWeek(map[0][1], map[0][0]);
    const lastTuple = map[map.length - 1];
    const last = getDateOfISOWeek(lastTuple[1], lastTuple[0]);
    last.setDate(last.getDate() + 5);
    return { first, last };
  }

  searchTeacherLocal(query, groupName, startDate, endDate) {
    const needle = query.trim().toLocaleLowerCase('ru');
    const groupId = this.groupIdByName[groupName];
    if (!needle) return { error: 'Укажи преподавателя', matches: [] };
    if (groupId === undefined) return { error: `Группы ${groupName} нет в локальной базе`, matches: [] };

    const matches = [];
    const seen = new Set();
    for (const [year, week] of this.db.semester_info.global_week_map) {
      const monday = getDateOfISOWeek(week, year);
      for (let dayOffset = 0; dayOffset < 6; dayOffset++) {
        const date = new Date(monday);
        date.setDate(monday.getDate() + dayOffset);
        if (date < startDate || date > endDate) continue;
        const lessons = this.getScheduleForDay(groupName, date);
        lessons.forEach(lesson => {
          if (!lesson.lector || !lesson.lector.toLocaleLowerCase('ru').includes(needle)) return;
          const key = `${formatDate(date)}|${lesson.pair}|${lesson.lector}|${lesson.subject}|${lesson.room}`;
          if (seen.has(key)) return;
          seen.add(key);
          matches.push({
            date: formatDate(date),
            dateObj: date,
            title: lesson.lector,
            details: [
              [lesson.subject, LESSON_TYPES_MAP[lesson.type] || lesson.type].filter(Boolean).join(' · '),
              `${lesson.pair} пара · ${PAIR_TIMES_RANGES[lesson.pair]} · ${lesson.room}`
            ]
          });
        });
      }
    }
    return { matches };
  }

  getLessonsForAuditorium(dateStr, timeStr, roomName) {
    const [day, month, year] = dateStr.split('.').map(Number);
    const targetDate = new Date(year, month - 1, day);
    const weekday = (targetDate.getDay() || 7) - 1;
    if (weekday > 5) return [];
    const pairIdx = TIME_TO_PAIR_IDX[timeStr];
    const roomId = this.roomsToId[roomName];
    if (pairIdx === undefined || roomId === undefined) return [];
    const groupIds = this.db.inverted_indices.rooms[String(roomId)] || [];
    const found = [];
    groupIds.forEach(groupId => {
      const weekId = this._getWeekIdForDate(groupId, targetDate);
      if (!weekId) return;
      const dayId = this.metaPatterns.weeks[weekId]?.[weekday];
      const lessonId = dayId ? this.metaPatterns.days[dayId]?.[pairIdx] : null;
      const lesson = this._lessonById(lessonId);
      if (lesson && lesson.roomId === roomId) {
        found.push({ group: this.groupNamesById[groupId], ...lesson });
      }
    });
    return found;
  }

  getRoomStatusesForBuilding(dateStr, selectedBuilding, selectedPair) {
    const buildingRooms = this.roomsById.filter(room => room && room.startsWith(selectedBuilding));
    const pairs = [String(selectedPair)];
    const result = {};
    for (const pairNum of pairs) {
      const time = PAIR_NUM_TO_TIME[pairNum];
      const pairTitle = `${pairNum} пара · ${PAIR_TIMES_RANGES[pairNum]}`;
      result[pairTitle] = {};
      buildingRooms.forEach(room => {
        const tail = room.split('-').pop().trim();
        const floor = /^\d/.test(tail) ? `${tail[0]} этаж` : 'Другое';
        if (!result[pairTitle][floor]) result[pairTitle][floor] = { free: [], busy: [] };
        const lessons = this.getLessonsForAuditorium(dateStr, time, room);
        if (lessons.length) result[pairTitle][floor].busy.push({ room, lessons });
        else result[pairTitle][floor].free.push(room);
      });
    }
    return result;
  }
}

function setDbStatus(state, title) {
  dbStatus.classList.remove('is-ready', 'is-warning', 'is-error');
  if (state) dbStatus.classList.add(state);
  dbStatus.textContent = title;
}

function activateTab(name) {
  $$('.tab').forEach(tab => tab.classList.toggle('is-active', tab.dataset.tab === name));
  $$('.tab-panel').forEach(panel => {
    const active = panel.dataset.panel === name;
    panel.classList.toggle('is-active', active);
    panel.hidden = !active;
  });
}

function populateLists() {
  groupsDatalist.innerHTML = '';
  lectorsDatalist.innerHTML = '';
  roomsDatalist.innerHTML = '';
  findBuildingInput.querySelectorAll('option:not([disabled])').forEach(el => el.remove());

  decompressor.groupNamesById.filter(Boolean).sort().forEach(group => {
    const option = document.createElement('option');
    option.value = group;
    groupsDatalist.appendChild(option);
  });

  decompressor.dictionaries.lectors.filter(Boolean).sort().forEach(lector => {
    const option = document.createElement('option');
    option.value = lector;
    lectorsDatalist.appendChild(option);
  });

  const buildings = new Set();
  decompressor.roomsById.filter(Boolean).forEach(room => {
    const option = document.createElement('option');
    option.value = room;
    roomsDatalist.appendChild(option);
    buildings.add(room.split('-')[0].trim());
  });
  [...buildings].sort((a, b) => a.localeCompare(b, 'ru', { numeric: true })).forEach(building => {
    const option = document.createElement('option');
    option.value = building;
    option.textContent = building;
    findBuildingInput.appendChild(option);
  });
}

function loadSelectedGroups() {
  try { selectedGroups = JSON.parse(localStorage.getItem(GROUPS_STORAGE_KEY) || '[]'); }
  catch { selectedGroups = []; }
  selectedGroups = selectedGroups.filter(group => decompressor.groupIdByName[group] !== undefined).slice(0, 8);
  activeGroup = localStorage.getItem(ACTIVE_GROUP_STORAGE_KEY);
  if (!selectedGroups.includes(activeGroup)) activeGroup = selectedGroups[0] || null;

  // First-run UX: the previous UI showed DEFAULT_GROUP as a placeholder,
  // which looked selected even though it wasn't. If there is no saved
  // selection and the default group exists in the loaded database, select it.
  if (!selectedGroups.length && decompressor.groupIdByName[DEFAULT_GROUP] !== undefined) {
    selectedGroups = [DEFAULT_GROUP];
    activeGroup = DEFAULT_GROUP;
    saveSelectedGroups();
  }

  renderSelectedGroups();
  if (activeGroup) {
    currentWeekStart = decompressor.getNearestWeek(activeGroup);
    renderFullSchedule();
  } else {
    scheduleEmpty.hidden = false;
    scheduleDisplay.hidden = true;
  }
}

function saveSelectedGroups() {
  localStorage.setItem(GROUPS_STORAGE_KEY, JSON.stringify(selectedGroups));
  if (activeGroup) localStorage.setItem(ACTIVE_GROUP_STORAGE_KEY, activeGroup);
  else localStorage.removeItem(ACTIVE_GROUP_STORAGE_KEY);
}

function addGroup(raw) {
  const group = raw.trim().toUpperCase();
  if (!group) return;
  if (decompressor.groupIdByName[group] === undefined) {
    groupInput.setCustomValidity('Такой группы нет в локальной базе');
    groupInput.reportValidity();
    setTimeout(() => groupInput.setCustomValidity(''), 100);
    return;
  }
  if (!selectedGroups.includes(group)) selectedGroups.push(group);
  activeGroup = group;
  currentWeekStart = decompressor.getNearestWeek(group);
  groupInput.value = '';
  saveSelectedGroups();
  renderSelectedGroups();
  renderFullSchedule();
}

function removeGroup(group) {
  selectedGroups = selectedGroups.filter(item => item !== group);
  if (activeGroup === group) {
    activeGroup = selectedGroups[0] || null;
    currentWeekStart = activeGroup ? decompressor.getNearestWeek(activeGroup) : null;
  }
  saveSelectedGroups();
  renderSelectedGroups();
  if (activeGroup) renderFullSchedule();
  else {
    scheduleDisplay.hidden = true;
    scheduleEmpty.hidden = false;
  }
}

function renderSelectedGroups() {
  selectedGroupsContainer.innerHTML = selectedGroups.map(group => `
    <div class="group-tag ${group === activeGroup ? 'is-active' : ''}" data-group="${escapeHtml(group)}">
      <span>${escapeHtml(group)}</span>
      <button class="group-tag-remove" type="button" data-remove-group="${escapeHtml(group)}" aria-label="Удалить ${escapeHtml(group)}">×</button>
    </div>
  `).join('');
}

function renderFullSchedule() {
  if (!activeGroup || !currentWeekStart) return;
  scheduleEmpty.hidden = true;
  scheduleDisplay.hidden = false;

  const end = new Date(currentWeekStart);
  end.setDate(end.getDate() + 5);
  weekDatesTitle.textContent = `${formatDateShort(currentWeekStart)} – ${formatDateShort(end)} ${end.getFullYear()}`;

  let html = '';
  for (let dayOffset = 0; dayOffset < 6; dayOffset++) {
    const date = new Date(currentWeekStart);
    date.setDate(currentWeekStart.getDate() + dayOffset);
    const lessons = decompressor.getScheduleForDay(activeGroup, date);
    html += `<article class="day-card ${lessons.length ? '' : 'is-empty'}">`;
    html += `<div class="day-title"><span>${WEEKDAYS[dayOffset]}</span><span class="day-date">${formatDateShort(date)}</span></div>`;
    if (!lessons.length) {
      html += '<span>Занятий нет</span>';
    } else {
      lessons.forEach(lesson => {
        html += `<div class="lesson-item">
          <div class="pair-num">${lesson.pair}</div>
          <div>
            <div class="subject">${escapeHtml(lesson.subject || '—')}</div>
            <div class="lector">${escapeHtml(lesson.lector || 'Преподаватель не указан')}</div>
            <div class="lesson-secondary">
              <span>${PAIR_TIMES_RANGES[lesson.pair]}</span>
              <span class="room">${escapeHtml(lesson.room)}</span>
              <span class="lesson-type-badge">${escapeHtml(LESSON_TYPES_MAP[lesson.type] || lesson.type || '')}</span>
            </div>
          </div>
        </div>`;
      });
    }
    html += '</article>';
  }
  scheduleContent.innerHTML = html;
}

function showWeekSelector() {
  if (!activeGroup) return;
  const weeks = decompressor.getNonEmptyWeeks(activeGroup);
  modalWeeksList.innerHTML = weeks.map(([year, week], index) => {
    const start = getDateOfISOWeek(week, year);
    const end = new Date(start); end.setDate(end.getDate() + 5);
    const active = currentWeekStart && start.getTime() === currentWeekStart.getTime();
    return `<div class="week-item ${active ? 'is-active' : ''}" data-week-start="${toInputDate(start)}">
      <span>${formatDateShort(start)} – ${formatDateShort(end)} ${end.getFullYear()}</span>
      <span class="week-num">${index + 1} неделя</span>
    </div>`;
  }).join('');
  weekSelectorModal.showModal();
}

function renderTeacherResult(payload, source) {
  teacherSource.textContent = source;
  const matches = payload.matches || [];
  const found = matches.length > 0;
  let html = `<div class="teacher-summary ${found ? 'is-found' : 'is-empty'}">
    <strong>${found ? `Найдено: ${matches.length}` : 'Не найден'}</strong>
    <div class="meta">${escapeHtml(payload.group || teacherGroup.value)} · ${escapeHtml(payload.period || `${teacherStart.value}–${teacherEnd.value}`)}</div>
  </div>`;

  if (matches.length) {
    html += '<div class="teacher-list">';
    matches.forEach(item => {
      const details = (item.details || []).filter(Boolean).map(value => escapeHtml(value)).join('<br>');
      html += `<div class="teacher-match">
        <div class="teacher-match-date">${escapeHtml(item.date)}</div>
        <div>
          <div class="teacher-match-title">${escapeHtml(item.title)}</div>
          ${details ? `<div class="teacher-match-details">${details}</div>` : ''}
        </div>
      </div>`;
    });
    html += '</div>';
  } else if (payload.total_matches > 0) {
    html += `<div class="notice-inline">В полном расписании есть ${payload.total_matches} совпад., но не в выбранном периоде.</div>`;
  }
  teacherResult.className = 'teacher-result';
  teacherResult.innerHTML = html;
}

async function runTeacherCheck() {
  const button = teacherForm.querySelector('button[type="submit"]');
  const group = teacherGroup.value.trim().toUpperCase();
  const query = teacherQuery.value.trim();
  if (!group || !query) return;
  if (teacherStart.value > teacherEnd.value) {
    teacherEnd.setCustomValidity('Дата окончания раньше даты начала');
    teacherEnd.reportValidity();
    return;
  }
  teacherEnd.setCustomValidity('');
  button.disabled = true;
  button.textContent = 'Проверяю…';
  teacherResult.className = 'teacher-result empty-state';
  teacherResult.textContent = 'Получаю расписание МАИ…';

  try {
    if (isDirectFileMode()) {
      throw new Error('для онлайн-проверки запусти start.bat');
    }
    const response = await fetch('/api/live-check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group, query, start: teacherStart.value, end: teacherEnd.value })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
    renderTeacherResult(payload, 'МАИ онлайн');
  } catch (liveError) {
    if (!decompressor) {
      teacherSource.textContent = 'МАИ онлайн';
      teacherResult.className = 'teacher-result';
      teacherResult.innerHTML = `<div class="status-line status-warning">Онлайн-проверка недоступна: ${escapeHtml(liveError.message)}</div>`;
      return;
    }
    const local = decompressor.searchTeacherLocal(
      query,
      group,
      parseInputDate(teacherStart.value),
      parseInputDate(teacherEnd.value)
    );
    const bounds = decompressor.getDatabaseBounds();
    if (!local.error) {
      renderTeacherResult({
        group,
        period: `${teacherStart.value}–${teacherEnd.value}`,
        matches: local.matches,
        total_matches: local.matches.length
      }, 'Локальная база');
      const note = document.createElement('div');
      note.className = 'notice-inline';
      note.textContent = bounds
        ? `Онлайн-проверка недоступна (${liveError.message}). Использована локальная база за ${formatDate(bounds.first)}–${formatDate(bounds.last)}.`
        : `Онлайн-проверка недоступна (${liveError.message}). Использована локальная база.`;
      teacherResult.appendChild(note);
    } else {
      teacherSource.textContent = isDirectFileMode() ? 'Локальная база' : 'МАИ онлайн';
      teacherResult.className = 'teacher-result';
      if (isDirectFileMode()) {
        teacherResult.innerHTML = `<div class="status-line status-warning">${escapeHtml(local.error)}.</div><div class="notice-inline">Для свежих данных и проверки 3-го семестра запусти <strong>start.bat</strong>, затем используй страницу, которая откроется на http://127.0.0.1:8765/.</div>`;
      } else {
        teacherResult.innerHTML = `<div class="status-line status-warning">${escapeHtml(local.error)}. Онлайн-проверка: ${escapeHtml(liveError.message)}</div>`;
      }
    }
  } finally {
    button.disabled = false;
    button.textContent = 'Проверить';
  }
}

function displayRoomStatuses(statuses) {
  const trim = trimPrefixCheckbox.checked;
  let html = '';
  Object.entries(statuses).forEach(([pairTitle, floors]) => {
    html += `<h3>${escapeHtml(pairTitle)}</h3>`;
    Object.entries(floors)
      .sort(([a], [b]) => a.localeCompare(b, 'ru', { numeric: true }))
      .forEach(([floor, values]) => {
        html += `<div class="floor-block"><div class="floor-title">${escapeHtml(floor)}</div>`;
        if (values.free.length) {
          html += '<div class="rooms-section"><div class="rooms-section-title">Свободны</div><div class="rooms-list">';
          values.free.sort().forEach(room => {
            const label = trim ? room.split('-').slice(1).join('-').trim() : room;
            html += `<span class="room-tag">${escapeHtml(label)}</span>`;
          });
          html += '</div></div>';
        }
        if (values.busy.length) {
          html += '<div class="rooms-section"><div class="rooms-section-title">Заняты</div><div class="rooms-list">';
          values.busy.sort((a, b) => a.room.localeCompare(b.room, 'ru', { numeric: true })).forEach(item => {
            const label = trim ? item.room.split('-').slice(1).join('-').trim() : item.room;
            const encoded = encodeURIComponent(JSON.stringify(item.lessons));
            html += `<button class="room-tag busy" type="button" data-room="${escapeHtml(item.room)}" data-lessons="${encoded}">${escapeHtml(label)}</button>`;
          });
          html += '</div></div>';
        }
        html += '</div>';
      });
  });
  freeRoomsResultDiv.innerHTML = html || '<div class="notice-inline">Аудитории не найдены.</div>';
}

function showLessonModal(room, lessons) {
  modalRoomTitle.textContent = room;
  modalLessonsList.innerHTML = `<ul class="lesson-list">${lessons.map(lesson => `
    <li><strong>${escapeHtml(lesson.group)}</strong> · ${escapeHtml(lesson.subject)} · ${escapeHtml(lesson.type || '')}<br><span class="muted">${escapeHtml(lesson.lector || 'Преподаватель не указан')}</span></li>
  `).join('')}</ul>`;
  lessonModal.showModal();
}

function initDates() {
  const bounds = decompressor.getDatabaseBounds();
  let target = new Date();
  if (bounds && target > bounds.last) target = bounds.last;
  if (bounds && target < bounds.first) target = bounds.first;
  const value = toInputDate(target);
  findDateInput.value = value;
  checkDateInput.value = value;
  trimPrefixCheckbox.checked = localStorage.getItem(TRIM_STORAGE_KEY) === 'true';
}

function initializeApp(database) {
  decompressor = new ScheduleDecompressor(database);
  populateLists();
  initDates();
  loadSelectedGroups();
  app.hidden = false;
  bootError.hidden = true;
  const info = database.build_info || {};
  if (info.complete === true) {
    const count = Number(info.groups_in_database || (database.dictionaries.groups?.length || 1) - 1);
    setDbStatus('is-ready', `База v${database.version || '4'} · ${count} групп`);
  } else if (info.sample === true) {
    setDbStatus('is-warning', 'Резервная база · 1 группа');
  } else {
    setDbStatus('is-warning', `База v${database.version || '4'} · неполная`);
  }
}

async function loadDatabaseFromFile(file) {
  const text = await file.text();
  const database = validateDatabase(JSON.parse(text));
  const db = await openDB();
  await dbPut(db, 'database', database);
  initializeApp(database);
}

async function boot() {
  const directFile = isDirectFileMode();
  if (fileModeNotice) fileModeNotice.hidden = !directFile;
  bootError.hidden = true;
  setDbStatus('', 'Загрузка базы…');

  try {
    const db = await openDB();
    let database;

    if (!directFile) {
      // HTTP mode: disk JSON is authoritative. This guarantees that a freshly
      // rebuilt database is visible immediately, regardless of browser caches.
      try {
        database = await fetchHttpDatabase();
        await dbPut(db, 'database', database);
      } catch (networkError) {
        const cached = await dbGet(db, 'database');
        const cachedVersion = String(cached?.version || '');
        if (cached && cachedVersion === DATABASE_SCHEMA && cached?.build_info?.complete === true) {
          console.warn('Использую последнюю полную базу из IndexedDB:', networkError);
          database = cached;
        } else {
          throw networkError;
        }
      }
    } else {
      // file:// mode: generated JS wrapper is authoritative; sample is a last resort.
      database = await fetchBundledDatabase();
      await dbPut(db, 'database', database);
    }

    initializeApp(validateDatabase(database));
  } catch (error) {
    console.error(error);
    setDbStatus('is-error', 'База недоступна');
    bootError.hidden = false;
    bootErrorText.textContent = error?.message || String(error);
    app.hidden = true;
  }
}

$$('.tab').forEach(tab => tab.addEventListener('click', () => activateTab(tab.dataset.tab)));

groupInput.addEventListener('change', () => addGroup(groupInput.value));
groupInput.addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); addGroup(groupInput.value); }
});
selectedGroupsContainer.addEventListener('click', event => {
  const remove = event.target.closest('[data-remove-group]');
  if (remove) { removeGroup(remove.dataset.removeGroup); return; }
  const tag = event.target.closest('[data-group]');
  if (!tag) return;
  activeGroup = tag.dataset.group;
  currentWeekStart = decompressor.getNearestWeek(activeGroup);
  saveSelectedGroups();
  renderSelectedGroups();
  renderFullSchedule();
});
prevWeekBtn.addEventListener('click', () => { currentWeekStart.setDate(currentWeekStart.getDate() - 7); renderFullSchedule(); });
nextWeekBtn.addEventListener('click', () => { currentWeekStart.setDate(currentWeekStart.getDate() + 7); renderFullSchedule(); });
weekDatesTitle.addEventListener('click', showWeekSelector);
gotoCurrentWeekBtn.addEventListener('click', () => {
  currentWeekStart = decompressor.getNearestWeek(activeGroup, new Date());
  renderFullSchedule();
  weekSelectorModal.close();
});
modalWeeksList.addEventListener('click', event => {
  const item = event.target.closest('[data-week-start]');
  if (!item) return;
  currentWeekStart = parseInputDate(item.dataset.weekStart);
  renderFullSchedule();
  weekSelectorModal.close();
});

teacherForm.addEventListener('submit', event => { event.preventDefault(); runTeacherCheck(); });

findFreeForm.addEventListener('submit', event => {
  event.preventDefault();
  const date = parseInputDate(findDateInput.value);
  const statuses = decompressor.getRoomStatusesForBuilding(formatDate(date), findBuildingInput.value, findPairInput.value);
  lastRoomStatusesResult = statuses;
  displayRoomStatuses(statuses);
});
trimPrefixCheckbox.addEventListener('change', () => {
  localStorage.setItem(TRIM_STORAGE_KEY, trimPrefixCheckbox.checked ? 'true' : 'false');
  if (lastRoomStatusesResult) displayRoomStatuses(lastRoomStatusesResult);
});
freeRoomsResultDiv.addEventListener('click', event => {
  const room = event.target.closest('[data-lessons]');
  if (!room) return;
  try { showLessonModal(room.dataset.room, JSON.parse(decodeURIComponent(room.dataset.lessons))); }
  catch { /* malformed data should not break the page */ }
});

checkStatusForm.addEventListener('submit', event => {
  event.preventDefault();
  const date = parseInputDate(checkDateInput.value);
  const lessons = decompressor.getLessonsForAuditorium(
    formatDate(date),
    PAIR_NUM_TO_TIME[checkPairInput.value],
    checkRoomInput.value.trim()
  );
  if (!lessons.length) {
    statusResultDiv.innerHTML = '<div class="status-line status-free">Свободно</div>';
    return;
  }
  statusResultDiv.innerHTML = `<div class="status-line status-busy">Занято<ul class="lesson-list">${lessons.map(lesson => `
    <li><strong>${escapeHtml(lesson.group)}</strong> · ${escapeHtml(lesson.subject)} · ${escapeHtml(lesson.type || '')}<br><span>${escapeHtml(lesson.lector || 'Преподаватель не указан')}</span></li>
  `).join('')}</ul></div>`;
});

$$('.dialog-close').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));

updateDatabaseBtn.addEventListener('click', () => databaseFileInput.click());
loadDatabaseBtn.addEventListener('click', () => databaseFileInput.click());
databaseFileInput.addEventListener('change', async () => {
  const file = databaseFileInput.files?.[0];
  if (!file) return;
  try { await loadDatabaseFromFile(file); }
  catch (error) {
    setDbStatus('is-error', 'Ошибка базы');
    bootError.hidden = false;
    bootErrorText.textContent = error.message;
  } finally {
    databaseFileInput.value = '';
  }
});

document.addEventListener('DOMContentLoaded', boot);
