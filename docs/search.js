/* search.js — the MorphoDepot extension's Search tab, on the web.
 *
 * Reads the same dashboard-data.json as the dashboard and applies the extension's search rules
 * (SearchMixin.search in MorphoDepotLib/logic_search.py).  The filters come from
 * data.searchFilters, which generate-dashboard.py builds from the extension's accession
 * questionnaire, so both searches offer the same choices.  The current search is kept in the
 * address bar so a search can be bookmarked or shared as a link.
 */

(async function () {
  let data;
  try {
    const resp = await fetch('./dashboard-data.json');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (err) {
    document.querySelector('main').innerHTML =
      `<p style="color:red;padding:2rem">Failed to load repository data: ${escapeHTML(err.message)}</p>`;
    return;
  }

  document.getElementById('generated-at').textContent =
    new Date(data.generatedAt).toLocaleString();

  // Repository tier is OWNER-based, as in the extension: Organizational iff the repo is owned by
  // the MorphoDepot org, else Personal.  Default is Organizational only.
  const TIER = { key: 'tier', label: 'Repository', options: ['Organizational', 'Personal'] };
  const FILTERS = [TIER, ...(data.searchFilters || [])];

  // Answers saved under wording the questionnaire has since changed.  The extension's search
  // compares answers to the current options verbatim, so these repos drop out of every search
  // there; here they are read as the answer they now correspond to.
  const LEGACY_ANSWERS = {
    specimenSource: { 'Commercially acquired': 'Non-accessioned' },
    anatomicalAreas: { 'Hind limg': 'Hind limb' },
  };

  const COLUMNS = [
    { key: 'size',       label: 'Size (GB)',  text: sizeText,       sortValue: r => r.volumeSize ?? -1 },
    { key: 'repository', label: 'Repository', text: r => repoName(r) },
    { key: 'owner',      label: 'Owner',      text: r => owner(r) },
    { key: 'species',    label: 'Species',    text: r => answer(r, 'species') || 'N/A' },
    { key: 'modality',   label: 'Modality',   text: r => answer(r, 'modality') || 'N/A' },
    { key: 'active',     label: 'Active',     text: activeText,     sortValue: r => r.pushedAt || '' },
    { key: 'spacing',    label: 'Spacing',    text: spacingText },
    { key: 'dimensions', label: 'Dimensions', text: dimensionsText },
  ];

  const searchBox = document.getElementById('search-box');
  const hideTestCb = document.getElementById('hide-test');
  const groupsEl = document.getElementById('filter-groups');
  let sort = { key: 'active', dir: -1 };
  let currentResults = [];

  // --- Filter state ---

  function defaultSelection(f) {
    if (f.key === 'tier') return new Set(['Organizational']);
    if (f.key === 'subjectType') return new Set(['Biological specimen']);
    return new Set(f.options);
  }

  const selection = {};
  for (const f of FILTERS) selection[f.key] = defaultSelection(f);

  // Address-bar format: ?q=text&tests=show&modality=MRI&modality=Medical+CT.  Only filters that
  // differ from their default appear; a filter present with an empty value means "none checked".
  function readURL() {
    const params = new URLSearchParams(location.search);
    searchBox.value = params.get('q') || '';
    hideTestCb.checked = params.get('tests') !== 'show';
    for (const f of FILTERS) {
      if (params.has(f.key)) {
        selection[f.key] = new Set(params.getAll(f.key).filter(v => f.options.includes(v)));
      }
    }
  }

  function writeURL() {
    const params = new URLSearchParams();
    if (searchBox.value) params.set('q', searchBox.value);
    if (!hideTestCb.checked) params.set('tests', 'show');
    for (const f of FILTERS) {
      const sel = selection[f.key];
      if (sameSet(sel, defaultSelection(f))) continue;
      if (sel.size === 0) params.append(f.key, '');
      for (const v of f.options) if (sel.has(v)) params.append(f.key, v);
    }
    const qs = params.toString();
    history.replaceState(null, '', qs ? `?${qs}` : location.pathname);
  }

  // --- Search (port of SearchMixin.search) ---

  function matches(r) {
    if (hideTestCb.checked && r.isTest) return false;
    if (!selection.tier.has(isOrganizational(r) ? 'Organizational' : 'Personal')) return false;

    for (const f of FILTERS) {
      if (f.key === 'tier') continue;
      const sel = selection[f.key];
      if (f.key === 'subjectType') {
        // A missing or blank subject type means "Biological specimen".  (The extension treats
        // only a missing one that way, so a blank answer hides the repo there.)
        if (!sel.has(answer(r, 'subjectType') || 'Biological specimen')) return false;
        continue;
      }
      const value = answer(r, f.key);
      if (value === undefined || value === null) continue;
      if (Array.isArray(value)) {
        // A repo that left a multi-select blank is not excluded by it; otherwise at least one of
        // its answers must be checked.
        const values = value.filter(v => v !== '' && v !== null).map(v => currentAnswer(f.key, v));
        if (values.length && !values.some(v => sel.has(v))) return false;
      } else if (value !== '' && !sel.has(currentAnswer(f.key, value))) {
        return false;
      }
    }

    const text = searchBox.value.trim();
    if (!text) return true;
    const pattern = globToRegExp(text);
    const fields = [r.nameWithOwner, answer(r, 'githubRepoName'), answer(r, 'species')];
    if (selection.subjectType && selection.subjectType.has('Other')) {
      fields.push(answer(r, 'otherSubjectDescription'));
    }
    return fields.some(v => typeof v === 'string' && pattern.test(v));
  }

  // The extension matches "*text*" with fnmatch: * and ? are wildcards, anything else is literal.
  function globToRegExp(text) {
    const source = text.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.');
    return new RegExp(source, 'i');
  }

  // --- Rendering ---

  function renderFilters() {
    groupsEl.innerHTML = '';
    for (const f of FILTERS) {
      const group = document.createElement('details');
      group.className = 'filter-group';
      if (f.key === 'tier') group.open = true;

      const summary = document.createElement('summary');
      summary.innerHTML = `${escapeHTML(f.label)} <span class="state"></span>`;
      group.appendChild(summary);

      const options = document.createElement('div');
      options.className = 'options';
      const allNone = document.createElement('div');
      allNone.className = 'all-none';
      allNone.innerHTML = '<button type="button" data-set="all">All</button>' +
                          '<button type="button" data-set="none">None</button>';
      allNone.addEventListener('click', e => {
        const which = e.target.dataset.set;
        if (!which) return;
        selection[f.key] = new Set(which === 'all' ? f.options : []);
        renderFilters();
        update();
      });
      options.appendChild(allNone);

      for (const option of f.options) {
        const label = document.createElement('label');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = selection[f.key].has(option);
        cb.addEventListener('change', () => {
          if (cb.checked) selection[f.key].add(option);
          else selection[f.key].delete(option);
          updateGroupState(summary, f);
          update();
        });
        label.appendChild(cb);
        label.appendChild(document.createTextNode(option));
        options.appendChild(label);
      }
      group.appendChild(options);
      groupsEl.appendChild(group);
      updateGroupState(summary, f);
    }
  }

  function updateGroupState(summary, f) {
    const n = selection[f.key].size;
    const state = n === f.options.length ? 'all'
      : n === 0 ? 'none'
      : n === 1 ? [...selection[f.key]][0]
      : `${n} of ${f.options.length}`;
    summary.querySelector('.state').textContent = `— ${state}`;
  }

  function renderHead() {
    const head = document.getElementById('results-head');
    head.innerHTML = '';
    for (const col of COLUMNS) {
      const th = document.createElement('th');
      const arrow = sort.key === col.key ? (sort.dir > 0 ? ' ▲' : ' ▼') : '';
      th.innerHTML = `${escapeHTML(col.label)}<span class="arrow">${arrow}</span>`;
      th.addEventListener('click', () => {
        sort = sort.key === col.key ? { key: col.key, dir: -sort.dir } : { key: col.key, dir: 1 };
        update();
      });
      head.appendChild(th);
    }
  }

  function sortResults(results) {
    const col = COLUMNS.find(c => c.key === sort.key);
    const value = col.sortValue || col.text;
    return results.sort((a, b) => {
      const va = value(a), vb = value(b);
      const cmp = typeof va === 'number' && typeof vb === 'number'
        ? va - vb
        : String(va).localeCompare(String(vb), undefined, { sensitivity: 'base' });
      return cmp * sort.dir;
    });
  }

  function renderResults() {
    const body = document.getElementById('results-body');
    body.innerHTML = '';
    for (const r of currentResults) {
      const tr = document.createElement('tr');
      tr.className = 'repo-row';
      tr.innerHTML = COLUMNS.map(col => {
        const text = escapeHTML(col.text(r));
        if (col.key === 'repository') {
          return `<td class="nowrap"><span class="expand-indicator">▶</span>` +
                 `<a href="https://github.com/${escapeHTML(r.nameWithOwner)}" target="_blank" rel="noopener">${text}</a></td>`;
        }
        if (col.key === 'species') return `<td class="species">${text}</td>`;
        return `<td class="nowrap">${text}</td>`;
      }).join('');
      tr.querySelector('a').addEventListener('click', e => e.stopPropagation());

      const detailTr = document.createElement('tr');
      detailTr.className = 'detail-row';
      detailTr.hidden = true;
      const detailTd = document.createElement('td');
      detailTd.colSpan = COLUMNS.length;
      detailTr.appendChild(detailTd);

      const indicator = tr.querySelector('.expand-indicator');
      tr.addEventListener('click', () => {
        if (!detailTd.innerHTML) detailTd.innerHTML = buildDetailHTML(r);  // images load on first open
        detailTr.hidden = !detailTr.hidden;
        indicator.textContent = detailTr.hidden ? '▶' : '▼';
      });
      body.appendChild(tr);
      body.appendChild(detailTr);
    }
    const n = currentResults.length;
    document.getElementById('result-count').textContent =
      `${n} matching repositor${n === 1 ? 'y' : 'ies'}`;
    document.getElementById('no-results').hidden = n > 0;
    document.getElementById('download').disabled = n === 0;
  }

  // Labels for the expanded row, in reading order; blank answers are skipped.
  const DETAIL_FIELDS = [
    ['species', 'Species'],
    ['subjectType', 'Subject type'],
    ['otherSubjectDescription', 'Subject description'],
    ['specimenSource', 'Specimen source'],
    ['institutionalAccession', 'Specimen identifier'],
    ['iDigBioURL', 'Public database record'],
    ['biologicalSex', 'Sex'],
    ['developmentalStage', 'Stage'],
    ['modality', 'Modality'],
    ['contrastEnhancement', 'Contrast enhanced'],
    ['imageContents', 'Image contents'],
    ['anatomicalAreas', 'Anatomical areas'],
    ['anatomicalAreasOther', 'Other anatomical areas'],
    ['license', 'License'],
  ];

  function buildDetailHTML(r) {
    const rows = [];
    for (const [key, label] of DETAIL_FIELDS) {
      let value = answer(r, key);
      if (Array.isArray(value)) value = value.filter(Boolean).map(v => currentAnswer(key, v)).join('; ');
      else if (typeof value === 'string') value = currentAnswer(key, value);
      if (!value) continue;
      const shown = key === 'iDigBioURL' && /^https?:\/\//i.test(value)
        ? `<a href="${escapeHTML(value)}" target="_blank" rel="noopener">${escapeHTML(value)}</a>`
        : escapeHTML(String(value));
      rows.push(`<dt>${escapeHTML(label)}</dt><dd>${shown}</dd>`);
    }
    const html = rows.length
      ? `<dl class="details">${rows.join('')}</dl>`
      : '<p style="color:#586069">No specimen details.</p>';

    const captions = normaliseCaptions(r.screenshotCaptions);
    if (!captions.length) return html;
    return html + '<div class="screenshots">' + captions.map(({ filename, caption }) => {
      const url = `https://raw.githubusercontent.com/${r.nameWithOwner}/main/screenshots/${encodeURIComponent(filename)}`;
      return `<figure>
        <a href="${escapeHTML(url)}" target="_blank" rel="noopener">
          <img src="${escapeHTML(url)}" alt="${escapeHTML(caption)}" loading="lazy">
        </a>
        ${caption ? `<figcaption>${escapeHTML(caption)}</figcaption>` : ''}
      </figure>`;
    }).join('') + '</div>';
  }

  function update() {
    currentResults = sortResults(data.repos.filter(matches));
    renderHead();
    renderResults();
    writeURL();
  }

  // --- CSV download (same columns as the table, plus the repository address) ---

  function downloadCSV() {
    const header = [...COLUMNS.map(c => c.label), 'URL'];
    const lines = [header, ...currentResults.map(r =>
      [...COLUMNS.map(c => c.text(r)), `https://github.com/${r.nameWithOwner}`])];
    const csv = lines.map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    link.download = 'MorphoDepotSearchResult.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 0);
  }

  // Quote every cell; prefix text a spreadsheet would read as a formula, since these values come
  // from public repositories.
  function csvCell(value) {
    let s = String(value ?? '');
    if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
    return `"${s.replace(/"/g, '""')}"`;
  }

  // --- Column text (same formats as the extension's results table) ---

  function sizeText(r) {
    return typeof r.volumeSize === 'number' ? (r.volumeSize / 1024 ** 3).toFixed(2) : 'N/A';
  }

  function activeText(r) {
    if (!r.pushedAt) return 'N/A';
    const pushed = new Date(r.pushedAt.split('T')[0] + 'T00:00:00');
    if (isNaN(pushed)) return 'N/A';
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.round((today - pushed) / 86400000);
    if (days < 1) return 'Today';
    if (days < 30) return `${days} day${days > 1 ? 's' : ''} ago`;
    if (days < 365) { const m = Math.floor(days / 30); return `${m} month${m > 1 ? 's' : ''} ago`; }
    const y = Math.floor(days / 365);
    return `${y} year${y > 1 ? 's' : ''} ago`;
  }

  // scanSpacing / scanDimensions are stored as tuple text, e.g. "(0.5, 0.5, 0.9)".
  function tupleValues(r, key) {
    const raw = (r.accession || {})[key];
    if (typeof raw !== 'string' || !raw) return null;
    return raw.replace(/[()]/g, '').split(',').map(v => v.trim()).filter(Boolean);
  }

  function spacingText(r) {
    const values = tupleValues(r, 'scanSpacing');
    if (!values) return 'N/A';
    const numbers = values.map(Number);
    return numbers.some(isNaN) ? 'Invalid' : numbers.map(v => String(Number(v.toPrecision(3)))).join(', ');
  }

  function dimensionsText(r) {
    const values = tupleValues(r, 'scanDimensions');
    return values ? values.join(' x ') : 'N/A';
  }

  // --- Helpers ---

  // Accession values are [question, answer] pairs; return just the answer.
  function answer(r, key) {
    const v = (r.accession || {})[key];
    return Array.isArray(v) && v.length >= 2 ? v[1] : undefined;
  }

  function currentAnswer(key, value) {
    return (LEGACY_ANSWERS[key] || {})[value] || value;
  }

  function owner(r) { return r.nameWithOwner.split('/')[0]; }
  function repoName(r) { return r.nameWithOwner.split('/').slice(1).join('/'); }

  function isOrganizational(r) {
    return (r.isArchival !== undefined) ? r.isArchival : owner(r) === 'MorphoDepot';
  }

  function sameSet(a, b) {
    return a.size === b.size && [...a].every(v => b.has(v));
  }

  // Normalise screenshotCaptions to [{filename, caption}] (same shapes dashboard.js accepts).
  function normaliseCaptions(raw) {
    if (!raw) return [];
    if (Array.isArray(raw)) {
      return raw.map(item => {
        if (item.filename) return { filename: item.filename, caption: item.caption || item.description || '' };
        const key = Object.keys(item).find(k => k !== 'caption' && k !== 'description') || '';
        return { filename: key, caption: item.caption || item.description || item[key] || '' };
      }).filter(c => c.filename);
    }
    if (typeof raw === 'object') {
      return Object.entries(raw).map(([filename, caption]) => ({
        filename,
        caption: typeof caption === 'string' ? caption : '',
      }));
    }
    return [];
  }

  function escapeHTML(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // --- Wire up ---

  readURL();
  renderFilters();
  searchBox.addEventListener('input', update);
  hideTestCb.addEventListener('change', update);
  document.getElementById('download').addEventListener('click', downloadCSV);
  document.getElementById('reset').addEventListener('click', () => {
    searchBox.value = '';
    hideTestCb.checked = true;
    for (const f of FILTERS) selection[f.key] = defaultSelection(f);
    renderFilters();
    update();
  });
  update();
})();
