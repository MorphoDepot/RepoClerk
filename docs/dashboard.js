/* dashboard.js — loads dashboard-data.json and renders charts + repo table */

(async function () {
  let data;
  try {
    const resp = await fetch('./dashboard-data.json');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (err) {
    document.querySelector('main').innerHTML =
      `<p style="color:red;padding:2rem">Failed to load dashboard data: ${err.message}</p>`;
    return;
  }

  document.getElementById('generated-at').textContent =
    new Date(data.generatedAt).toLocaleString();

  // --- Carousel (all repos; screenshot-bearing only) ---
  buildCarousel(data.repos);

  // --- Charts (initialised once; fed the filtered repo set by renderAll) ---
  const activityChart = echarts.init(document.getElementById('activity-chart'));
  const taxonomyChart = echarts.init(document.getElementById('taxonomy-chart'));
  const taxSelect = document.getElementById('tax-level');

  // --- Filters: "Hide test repositories" / "Include ephemeral repositories" ---
  const hideTestCb = document.getElementById('hide-test');
  const includeEphemeralCb = document.getElementById('include-ephemeral');
  const filterCountEl = document.getElementById('filter-count');
  const DUP_CATEGORY_PRIORITY = { 'org-org': 0, 'promotion': 1, 'cross-owner': 2, 'same-owner': 3 };

  hideTestCb.addEventListener('change', renderAll);
  includeEphemeralCb.addEventListener('change', renderAll);
  taxSelect.addEventListener('change', () => renderTaxonomy(data.repos.filter(repoVisible)));
  renderAll();

  function repoVisible(r) {
    if (hideTestCb.checked && r.isTest) return false;          // hide reload-and-test repos
    if (!includeEphemeralCb.checked && r.isEphemeral) return false;  // optionally hide ephemeral
    return true;
  }

  function renderAll() {
    const repos = data.repos.filter(repoVisible);
    document.getElementById('total-repos').textContent  = repos.length;
    document.getElementById('total-issues').textContent = repos.reduce((s, r) => s + (r.openIssues || 0), 0);
    document.getElementById('total-prs').textContent    = repos.reduce((s, r) => s + (r.openPRs || 0), 0);
    filterCountEl.textContent = repos.length === data.repos.length
      ? `${data.repos.length} repositories`
      : `showing ${repos.length} of ${data.repos.length} repositories`;
    renderActivity(repos);
    renderTaxonomy(repos);
    renderDuplicates(filteredDuplicateGroups());
    renderRepoTable(repos);
  }

  function renderActivity(repos) {
    const now = Date.now();
    const w = { day: 0, week: 0, month: 0, year: 0 };
    for (const r of repos) {
      if (!r.pushedAt) continue;
      const days = (now - new Date(r.pushedAt).getTime()) / 86400000;
      if (days <= 1) w.day++;
      if (days <= 7) w.week++;
      if (days <= 30) w.month++;
      if (days <= 365) w.year++;
    }
    activityChart.setOption({
      title: { text: 'Repository Activity', left: 'center', textStyle: { fontSize: 13 } },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: ['Last Day', 'Last Week', 'Last Month', 'Last Year'] },
      yAxis: { type: 'value', name: 'Repos', minInterval: 1 },
      series: [{ type: 'bar', data: [w.day, w.week, w.month, w.year], itemStyle: { color: '#1a3a52' } }],
      grid: { left: 50, right: 20, bottom: 40, top: 50 },
    });
  }

  function renderTaxonomy(repos) {
    const level = taxSelect.value;
    const counts = {};
    for (const r of repos) {
      const raw = r.accession ? r.accession[level] : undefined;
      const val = (Array.isArray(raw) ? raw[1] : raw) || 'Unknown';
      counts[val] = (counts[val] || 0) + 1;
    }
    const chartData = Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, value]) => ({ name, value }));
    taxonomyChart.setOption({
      title: { text: `By ${level.charAt(0).toUpperCase() + level.slice(1)}`, left: 'center', textStyle: { fontSize: 13 } },
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      series: [{ type: 'pie', radius: ['30%', '65%'], data: chartData, label: { fontSize: 11 } }],
    }, true);
  }

  function renderRepoTable(repos) {
    const tbody = document.getElementById('repo-tbody');
    tbody.innerHTML = '';
    repos.forEach(repo => {
      const pushedDate = repo.pushedAt ? new Date(repo.pushedAt).toLocaleDateString() : '—';
      const tr = document.createElement('tr');
      tr.className = 'repo-row';
      tr.innerHTML = `
        <td>
          <span class="expand-indicator">▶</span>
          <a href="https://github.com/${repo.nameWithOwner}" target="_blank"
             onclick="event.stopPropagation()">${repo.nameWithOwner}</a>
        </td>
        <td>${pushedDate}</td>
        <td>${repo.openIssues}</td>
        <td>${repo.openPRs}</td>
        <td>${repo.screenshotCount}</td>
      `;
      const detailTr = document.createElement('tr');
      detailTr.className = 'detail-row';
      detailTr.style.display = 'none';
      const detailTd = document.createElement('td');
      detailTd.colSpan = 5;
      detailTd.innerHTML = buildDetailHTML(repo);
      detailTr.appendChild(detailTd);
      const indicator = tr.querySelector('.expand-indicator');
      tr.addEventListener('click', () => {
        const open = detailTr.style.display !== 'none';
        detailTr.style.display = open ? 'none' : 'table-row';
        indicator.textContent = open ? '▶' : '▼';
      });
      tbody.appendChild(tr);
      tbody.appendChild(detailTr);
    });
  }

  function categorizeDuplicate(repos) {
    const org = repos.filter(r => r.isOrg);
    const personal = repos.filter(r => !r.isOrg);
    if (org.length && personal.length) return 'promotion';
    if (org.length > 1) return 'org-org';
    if (new Set(repos.map(r => r.nameWithOwner.split('/')[0])).size === 1) return 'same-owner';
    return 'cross-owner';
  }

  // Re-derive duplicate groups for the active filter: drop hidden repos, drop groups that
  // fall below 2 visible repos, then re-categorize and re-sort from what remains.
  function filteredDuplicateGroups() {
    const out = [];
    for (const g of (data.duplicateVolumes || [])) {
      const reps = (g.repos || []).filter(repoVisible);
      if (reps.length < 2) continue;
      out.push({ checksum: g.checksum, category: categorizeDuplicate(reps), repos: reps });
    }
    out.sort((a, b) => (DUP_CATEGORY_PRIORITY[a.category] ?? 9) - (DUP_CATEGORY_PRIORITY[b.category] ?? 9)
                       || a.checksum.localeCompare(b.checksum));
    return out;
  }

  window.addEventListener('resize', () => {
    activityChart.resize();
    taxonomyChart.resize();
  });


  // --- Duplicate-volumes panel ---

  function renderDuplicates(groups) {
    const section = document.getElementById('dup-section');
    const tbody = document.getElementById('dup-tbody');
    tbody.innerHTML = '';  // re-render-safe (filters re-invoke this)
    if (!groups.length) { section.style.display = 'none'; return; }
    section.style.display = '';
    document.getElementById('dup-count').textContent = groups.length;

    // Category -> {label, colour}.
    const CATS = {
      'org-org':     { label: 'Org ↔ Org',      color: '#b31d28' },
      'promotion':   { label: 'Personal → Org', color: '#b35900' },
      'cross-owner': { label: 'Cross-owner',          color: '#9a6700' },
      'same-owner':  { label: 'Same owner',           color: '#6a737d' },
    };

    groups.forEach(g => {
      const cat = CATS[g.category] || { label: g.category, color: '#6a737d' };
      const repos = (g.repos || []).map(r =>
        `<a href="https://github.com/${escapeHTML(r.nameWithOwner)}" target="_blank">` +
        `${escapeHTML(r.nameWithOwner)}</a>` +
        (r.isOrg ? ' <span class="org-tag">org</span>' : '')
      ).join('<br>');
      const species = [...new Set((g.repos || [])
        .map(r => r.species).filter(s => s && s !== 'Unknown'))].join(', ') || '—';
      const sha = String(g.checksum || '');

      const tr = document.createElement('tr');
      tr.className = 'repo-row';
      tr.style.cursor = 'default';
      tr.innerHTML = `
        <td><span class="dup-badge" style="background:${cat.color}">${cat.label}</span></td>
        <td>${repos}</td>
        <td>${escapeHTML(species)}</td>
        <td><code class="dup-sha" title="${escapeHTML(sha)}">${escapeHTML(sha.slice(0, 12))}…</code></td>
      `;
      tbody.appendChild(tr);
    });
  }


  // --- Carousel ---

  function buildCarousel(repos) {
    // Collect all screenshots across all repos, shuffled
    const slides = [];
    for (const repo of repos) {
      const captions = normaliseCaptions(repo.screenshotCaptions);
      for (const { filename, caption } of captions) {
        slides.push({
          url: `https://raw.githubusercontent.com/${repo.nameWithOwner}/main/screenshots/${encodeURIComponent(filename)}`,
          caption,
          nameWithOwner: repo.nameWithOwner,
        });
      }
    }

    if (slides.length === 0) return;

    // Fisher-Yates shuffle for variety on each page load
    for (let i = slides.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [slides[i], slides[j]] = [slides[j], slides[i]];
    }

    const gallery  = document.getElementById('gallery');
    const dotsEl   = document.getElementById('carousel-dots');
    const prevBtn  = document.getElementById('carousel-prev');
    const nextBtn  = document.getElementById('carousel-next');

    // Build slide elements
    slides.forEach((slide, i) => {
      const div = document.createElement('div');
      div.className = 'carousel-slide' + (i === 0 ? ' active' : '');
      div.innerHTML = `
        <img src="${escapeHTML(slide.url)}" alt="${escapeHTML(slide.caption)}" loading="lazy">
        <div class="carousel-caption">
          ${slide.caption ? escapeHTML(slide.caption) + '<br>' : ''}
          <a class="repo-link" href="https://github.com/${escapeHTML(slide.nameWithOwner)}"
             target="_blank">${escapeHTML(slide.nameWithOwner)}</a>
        </div>`;
      gallery.insertBefore(div, prevBtn);

      const dot = document.createElement('div');
      dot.className = 'carousel-dot' + (i === 0 ? ' active' : '');
      dot.addEventListener('click', () => goTo(i));
      dotsEl.appendChild(dot);
    });

    gallery.classList.remove('hidden');

    const slideEls = gallery.querySelectorAll('.carousel-slide');
    const dotEls   = dotsEl.querySelectorAll('.carousel-dot');
    let current = 0;
    let timer;

    function goTo(index) {
      slideEls[current].classList.remove('active');
      dotEls[current].classList.remove('active');
      current = (index + slides.length) % slides.length;
      slideEls[current].classList.add('active');
      dotEls[current].classList.add('active');
      resetTimer();
    }

    function resetTimer() {
      clearInterval(timer);
      timer = setInterval(() => goTo(current + 1), 5000);
    }

    prevBtn.addEventListener('click', () => goTo(current - 1));
    nextBtn.addEventListener('click', () => goTo(current + 1));

    // Pause on hover
    gallery.addEventListener('mouseenter', () => clearInterval(timer));
    gallery.addEventListener('mouseleave', resetTimer);

    resetTimer();
  }

  // --- Helpers ---

  // Normalise screenshotCaptions to [{filename, caption}] regardless of source format:
  //   array:  [{filename, caption}, ...]  or  [{filename: caption}, ...]
  //   object: {"filename": "caption", ...}
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

  function buildDetailHTML(repo) {
    const acc = repo.accession || {};
    const accKeys = Object.keys(acc).filter(k => acc[k] !== null && acc[k] !== undefined);

    const accHTML = accKeys.length
      ? '<dl class="accession">' +
        accKeys.map(k => {
          const v = typeof acc[k] === 'object' ? JSON.stringify(acc[k]) : acc[k];
          return `<dt>${k}</dt><dd>${escapeHTML(String(v))}</dd>`;
        }).join('') +
        '</dl>'
      : '<p style="color:#586069;font-size:0.85rem">No accession data.</p>';

    const captions = normaliseCaptions(repo.screenshotCaptions);
    const screenshotsHTML = captions.length
      ? '<div class="screenshots">' +
        captions.map(({ filename, caption }) => {
          const url = `https://raw.githubusercontent.com/${repo.nameWithOwner}/main/screenshots/${encodeURIComponent(filename)}`;
          return `<figure>
            <a href="${url}" target="_blank">
              <img src="${url}" alt="${escapeHTML(caption)}" loading="lazy">
            </a>
            ${caption ? `<figcaption>${escapeHTML(caption)}</figcaption>` : ''}
          </figure>`;
        }).join('') +
        '</div>'
      : '';

    return accHTML + screenshotsHTML;
  }

  function escapeHTML(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
})();
