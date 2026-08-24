(() => {
  'use strict';

  const input = document.getElementById('group-input');
  const datalist = document.getElementById('groups-datalist');
  const popup = document.getElementById('group-autocomplete');
  if (!input || !datalist || !popup) return;

  let matches = [];
  let activeIndex = -1;

  const groups = () => [...datalist.options]
    .map(option => option.value)
    .filter(Boolean);

  const close = () => {
    popup.hidden = true;
    popup.innerHTML = '';
    input.setAttribute('aria-expanded', 'false');
    activeIndex = -1;
  };

  const paintActive = () => {
    [...popup.querySelectorAll('.group-autocomplete-item')].forEach((item, index) => {
      const isActive = index === activeIndex;
      item.classList.toggle('is-active', isActive);
      item.setAttribute('aria-selected', isActive ? 'true' : 'false');
      if (isActive) item.scrollIntoView({ block: 'nearest' });
    });
  };

  const render = () => {
    const needle = input.value.trim().toLocaleUpperCase('ru');
    if (!needle) {
      close();
      return;
    }

    const all = groups();
    const starts = all.filter(group =>
      group.toLocaleUpperCase('ru').startsWith(needle)
    );
    const contains = all.filter(group => {
      const upper = group.toLocaleUpperCase('ru');
      return !upper.startsWith(needle) && upper.includes(needle);
    });

    matches = [...starts, ...contains]
      .sort((a, b) => a.localeCompare(b, 'ru', { numeric: true }))
      .slice(0, 8);
    activeIndex = -1;
    popup.innerHTML = '';

    if (!matches.length) {
      const empty = document.createElement('div');
      empty.className = 'group-autocomplete-empty';
      empty.textContent = 'Совпадений нет';
      popup.appendChild(empty);
    } else {
      matches.forEach((group, index) => {
        const item = document.createElement('button');
        item.className = 'group-autocomplete-item';
        item.type = 'button';
        item.setAttribute('role', 'option');
        item.dataset.index = String(index);
        item.title = group;
        item.textContent = group;
        popup.appendChild(item);
      });
    }

    popup.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  };

  const choose = index => {
    const group = matches[index];
    if (!group) return;
    input.value = group;
    close();
    input.dispatchEvent(new Event('change', { bubbles: true }));
  };

  input.addEventListener('input', render);
  input.addEventListener('focus', () => {
    if (input.value.trim()) render();
  });
  input.addEventListener('blur', () => setTimeout(close, 120));

  popup.addEventListener('pointerdown', event => event.preventDefault());
  popup.addEventListener('click', event => {
    const item = event.target.closest('.group-autocomplete-item');
    if (item) choose(Number(item.dataset.index));
  });

  document.addEventListener('keydown', event => {
    if (event.target !== input || popup.hidden) return;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      event.stopPropagation();
      activeIndex = Math.min(activeIndex + 1, matches.length - 1);
      paintActive();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      event.stopPropagation();
      activeIndex = Math.max(activeIndex - 1, 0);
      paintActive();
    } else if (event.key === 'Enter' && activeIndex >= 0) {
      event.preventDefault();
      event.stopPropagation();
      choose(activeIndex);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  }, true);

  document.addEventListener('pointerdown', event => {
    if (event.target !== input && !popup.contains(event.target)) close();
  });
})();
