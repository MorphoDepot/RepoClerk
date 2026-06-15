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

  // --- Summary bar ---
  document.getElementById('total-repos').textContent  = data.totalRepos;
  document.getElementById('total-issues').textContent = data.totalOpenIssues;
  document.getElementById('total-prs').textContent    = data.totalOpenPRs;
  document.getElementById('generated-at').textContent =
    new Date(data.generatedAt).toLocaleString();

  // --- Carousel ---
  buildCarousel(data.repos);

  // --- Duplicate-volumes panel ---
  renderDuplicates(data.duplicateVolumes || []);

  // --- Activity chart ---
  const activityChart = echarts.init(document.getElementById('activity-chart'));
  activityChart.setOption({
    title: { text: 'Repository Activity', left: 'center', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: ['Last Day', 'Last Week', 'Last Month', 'Last Year'],
    },
    yAxis: { type: 'value', name: 'Repos', minInterval: 1 },
    series: [{
      type: 'bar',
      data: [
        data.activity.last_day,
        data.activity.last_week,
        data.activity.last_month,
        data.activity.last_year,
      ],
      itemStyle: { color: '#1a3a52' },
    }],
    grid: { left: 50, right: 20, bottom: 40, top: 50 },
  });

  // --- Taxonomy pie chart ---
  const taxonomyChart = echarts.init(document.getElementById('taxonomy-chart'));
  const taxSelect = document.getElementById('tax-level');

  function updateTaxonomy() {
    const level = taxSelect.value;
    const counts = data.taxonomy[level] || {};
    const chartData = Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, value]) => ({ name, value }));

    taxonomyChart.setOption({
      title: {
        text: `By ${level.charAt(0).toUpperCase() + level.slice(1)}`,
        left: 'center',
        textStyle: { fontSize: 13 },
      },
      tooltip: {
        trigger: 'item',
        formatter: '{b}: {c} ({d}%)',
      },
      series: [{
        type: 'pie',
        radius: ['30%', '65%'],
        data: chartData,
        label: { fontSize: 11 },
      }],
    }, true);
  }

  taxSelect.addEventListener('change', updateTaxonomy);
  updateTaxonomy();

  // --- Repo table ---
  const tbody = document.getElementById('repo-tbody');

  data.repos.forEach(repo => {
    const pushedDate = repo.pushedAt
      ? new Date(repo.pushedAt).toLocaleDateString()
      : '—';

    // Main row
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

    // Detail row (hidden by default)
    const detailTr = document.createElement('tr');
    detailTr.className = 'detail-row';
    detailTr.style.display = 'none';

    const detailTd = document.createElement('td');
    detailTd.colSpan = 5;
    detailTd.innerHTML = buildDetailHTML(repo);
    detailTr.appendChild(detailTd);

    // Toggle on row click
    const indicator = tr.querySelector('.expand-indicator');
    tr.addEventListener('click', () => {
      const open = detailTr.style.display !== 'none';
      detailTr.style.display = open ? 'none' : 'table-row';
      indicator.textContent = open ? '▶' : '▼';
    });

    tbody.appendChild(tr);
    tbody.appendChild(detailTr);
  });

  window.addEventListener('resize', () => {
    activityChart.resize();
    taxonomyChart.resize();
  });


  // --- Duplicate-volumes panel ---

  function renderDuplicates(groups) {
    const section = document.getElementById('dup-section');
    if (!groups.length) { section.style.display = 'none'; return; }
    section.style.display = '';
    document.getElementById('dup-count').textContent = groups.length;

    // Category -> {label, colour}; ordered by curation priority in the data already.
    const CATS = {
      'org-org':     { label: 'Org ↔ Org',      color: '#b31d28' },
      'promotion':   { label: 'Personal → Org', color: '#b35900' },
      'cross-owner': { label: 'Cross-owner',          color: '#9a6700' },
      'same-owner':  { label: 'Same owner',           color: '#6a737d' },
    };
    const tbody = document.getElementById('dup-tbody');

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
