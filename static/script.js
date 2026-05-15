document.addEventListener('DOMContentLoaded', () => {
    const postsContainer = document.getElementById('posts-container');
    const loader = document.getElementById('loader');
    const generateBtn = document.getElementById('generate-btn');
    const chooseBtn = document.getElementById('choose-btn');
    const pickerOverlay = document.getElementById('picker-overlay');
    const pickerClose = document.getElementById('picker-close');
    const pickerLoader = document.getElementById('picker-loader');
    const pickerList = document.getElementById('picker-list');

    // --- Schedule card elements ---
    const schedEnabled    = document.getElementById('schedule-enabled');
    const schedSummary    = document.getElementById('schedule-summary');
    const schedNext       = document.getElementById('schedule-next');
    const schedForm       = document.getElementById('schedule-form');
    const schedMode       = document.getElementById('sched-mode');
    const schedHours      = document.getElementById('sched-interval-hours');
    const schedDays       = document.getElementById('sched-interval-days');
    const schedPostHour   = document.getElementById('sched-post-hour');
    const schedPostMinute = document.getElementById('sched-post-minute');
    const schedSaveStatus = document.getElementById('schedule-save-status');
    const schedCard       = document.getElementById('schedule-card');

    initScheduleUI();

    // --- Generate-now (LLM picks the article) ---
    generateBtn.addEventListener('click', () => triggerGeneration({}));

    // --- Article picker flow ---
    chooseBtn.addEventListener('click', openPicker);
    pickerClose.addEventListener('click', closePicker);
    pickerOverlay.addEventListener('click', (e) => {
        if (e.target === pickerOverlay) closePicker();
    });

    async function openPicker() {
        pickerOverlay.classList.remove('hidden');
        pickerLoader.classList.remove('hidden');
        pickerList.classList.add('hidden');
        pickerList.innerHTML = '';

        try {
            const response = await fetch('/api/articles');
            if (!response.ok) throw new Error('Failed to fetch articles');
            const data = await response.json();
            renderArticles(data.articles || []);
        } catch (error) {
            console.error('Article fetch error:', error);
            pickerLoader.innerHTML = `<p style="color: #ef4444;">Failed to load articles. Make sure the backend is running.</p>`;
        }
    }

    function closePicker() {
        pickerOverlay.classList.add('hidden');
    }

    function renderArticles(articles) {
        pickerLoader.classList.add('hidden');
        pickerList.classList.remove('hidden');

        if (articles.length === 0) {
            pickerList.innerHTML = `<div class="empty-state"><h3>No fresh articles available</h3><p>Every recent article has already been used. Try again later.</p></div>`;
            return;
        }

        articles.forEach((a) => {
            const row = document.createElement('div');
            row.className = 'article-row';
            row.dataset.link = a.link;

            const thumb = a.image_url
                ? `<img class="article-thumb" src="${escapeAttr(a.image_url)}" alt="" loading="lazy" onerror="this.style.display='none'">`
                : `<div class="article-thumb article-thumb-empty"></div>`;

            row.innerHTML = `
                ${thumb}
                <div class="article-body">
                    <div class="article-meta">
                        <span class="article-source">${escapeHtml(a.source)}</span>
                        <span class="article-score">heat ${a.score.toFixed(3)}</span>
                    </div>
                    <h3 class="article-title">${escapeHtml(a.title)}</h3>
                    <p class="article-summary">${escapeHtml((a.summary || '').slice(0, 180))}</p>
                </div>
                <button class="article-pick-btn" type="button">Use this</button>
            `;
            row.addEventListener('click', () => pickArticle(a));
            pickerList.appendChild(row);
        });
    }

    async function pickArticle(article) {
        const confirmed = confirm(`Generate and publish a LinkedIn post about:\n\n"${article.title}"\n\nThis will publish to LinkedIn immediately.`);
        if (!confirmed) return;
        closePicker();
        await triggerGeneration({ article_link: article.link });
    }

    async function triggerGeneration(body) {
        generateBtn.classList.add('loading');
        generateBtn.disabled = true;
        chooseBtn.disabled = true;
        const span = generateBtn.querySelector('span');
        const originalText = span.textContent;
        span.textContent = 'Generating... (This usually takes ~60s)';

        try {
            const response = await fetch('/api/trigger', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body || {}),
            });
            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to generate');
            }
            const data = await response.json();
            if (data.picked_title) {
                console.log('Posted about:', data.picked_title);
            }
            postsContainer.innerHTML = '';
            loader.classList.remove('hidden');
            await fetchPosts();
        } catch (error) {
            console.error('Generation Error:', error);
            alert('Error generating post: ' + error.message);
        } finally {
            generateBtn.classList.remove('loading');
            generateBtn.disabled = false;
            chooseBtn.disabled = false;
            span.textContent = originalText;
        }
    }

    async function fetchPosts() {
        try {
            const response = await fetch('/api/posts');
            if (!response.ok) throw new Error('Failed to fetch posts');
            const data = await response.json();
            renderPosts(data.posts);
        } catch (error) {
            console.error('Error:', error);
            loader.innerHTML = `<p style="color: #ef4444;">Failed to load posts. Is the database created correctly?</p>`;
        }
    }

    function renderPosts(posts) {
        loader.classList.add('hidden');
        if (posts.length === 0) {
            postsContainer.innerHTML = `
                <div class="empty-state">
                    <h3>No posts yet</h3>
                    <p>When the auto-poster publishes a new update, it will appear here.</p>
                </div>`;
            return;
        }

        posts.forEach((post, index) => {
            let formattedDate = post.created_at;
            try {
                const dateParts = post.created_at.replace(' ', 'T') + 'Z';
                const date = new Date(dateParts);
                if (!isNaN(date)) {
                    formattedDate = date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                }
            } catch (e) {}

            const card = document.createElement('div');
            card.className = 'post-card';
            card.style.animationDelay = `${index * 0.1}s`;

            const url = `https://www.linkedin.com/feed/update/${post.linkedin_id}`;
            const isLinkedinIdValid = post.linkedin_id && post.linkedin_id.includes('urn:');

            // Image: shown only when stored AND the browser can fetch it
            // (hotlink-protected sources will fire onerror and disappear).
            const imageMarkup = post.image_url
                ? `<img class="post-image" src="${escapeAttr(post.image_url)}" alt="" loading="lazy" onerror="this.remove()">`
                : '';

            card.innerHTML = `
                ${imageMarkup}
                <div class="post-meta">
                    <div class="post-date">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                        ${formattedDate}
                    </div>
                    <div class="post-id">#${post.id}</div>
                </div>
                <div class="post-content">${escapeHtml(post.content)}</div>
                <a href="${isLinkedinIdValid ? url : '#'}" target="_blank" rel="noopener noreferrer" class="linkedin-btn" ${!isLinkedinIdValid ? 'style="pointer-events: none; opacity: 0.5;"' : ''}>
                    ${isLinkedinIdValid ? 'View on LinkedIn' : 'No Link Available'}
                </a>
            `;
            postsContainer.appendChild(card);
        });
    }

    // ===================================================================
    // Schedule UI — talks to GET/PUT /api/schedule
    // ===================================================================

    async function initScheduleUI() {
        // Mode toggle: show/hide the matching field group
        schedMode.addEventListener('change', () => applyModeVisibility(schedMode.value));

        // Enable/disable toggle: save immediately
        schedEnabled.addEventListener('change', async () => {
            await saveSchedule({ enabled: schedEnabled.checked });
        });

        // Save the rest of the form on submit
        schedForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const body = {
                mode: schedMode.value,
                interval_hours: parseInt(schedHours.value, 10) || 1,
                interval_days: parseInt(schedDays.value, 10) || 1,
                post_hour: parseInt(schedPostHour.value, 10) || 0,
                post_minute: parseInt(schedPostMinute.value, 10) || 0,
            };
            await saveSchedule(body);
        });

        await loadSchedule();
    }

    function applyModeVisibility(mode) {
        schedCard.querySelectorAll('.sched-only-hourly').forEach(el => {
            el.style.display = (mode === 'hourly') ? '' : 'none';
        });
        schedCard.querySelectorAll('.sched-only-daily').forEach(el => {
            el.style.display = (mode === 'daily_at') ? '' : 'none';
        });
    }

    async function loadSchedule() {
        try {
            const resp = await fetch('/api/schedule');
            if (!resp.ok) throw new Error('Failed to load schedule');
            const { schedule } = await resp.json();
            renderSchedule(schedule);
        } catch (err) {
            console.error('Schedule load failed:', err);
            schedSummary.textContent = 'Failed to load schedule';
        }
    }

    async function saveSchedule(patch) {
        flashStatus('Saving…');
        try {
            const resp = await fetch('/api/schedule', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || 'Save failed');
            }
            const { schedule } = await resp.json();
            renderSchedule(schedule);
            flashStatus('Saved ✓', 1500);
        } catch (err) {
            console.error('Schedule save failed:', err);
            flashStatus('Save failed: ' + err.message, 4000);
        }
    }

    function flashStatus(msg, clearAfterMs) {
        schedSaveStatus.textContent = msg;
        if (clearAfterMs) {
            setTimeout(() => { schedSaveStatus.textContent = ''; }, clearAfterMs);
        }
    }

    function renderSchedule(s) {
        schedEnabled.checked   = !!s.enabled;
        schedMode.value        = s.mode || 'daily_at';
        schedHours.value       = s.interval_hours ?? 6;
        schedDays.value        = s.interval_days ?? 1;
        schedPostHour.value    = s.post_hour ?? 11;
        schedPostMinute.value  = s.post_minute ?? 0;
        applyModeVisibility(schedMode.value);

        // Human-readable summary
        let summary;
        if (s.mode === 'hourly') {
            summary = `Every ${s.interval_hours} hour${s.interval_hours === 1 ? '' : 's'}`;
        } else {
            const hh = String(s.post_hour).padStart(2, '0');
            const mm = String(s.post_minute).padStart(2, '0');
            const days = s.interval_days === 1
                ? 'Every day'
                : `Every ${s.interval_days} days`;
            summary = `${days} at ${hh}:${mm}`;
        }
        if (!s.enabled) summary += ' (paused)';
        schedSummary.textContent = summary;

        // Next run
        if (s.enabled && s.next_run_at) {
            const dt = new Date(s.next_run_at);
            if (!isNaN(dt)) {
                schedNext.textContent = `Next run: ${dt.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`;
            } else {
                schedNext.textContent = `Next run: ${s.next_run_at}`;
            }
        } else if (!s.enabled) {
            schedNext.textContent = 'Auto-posting is paused — turn the switch on to enable.';
        } else {
            schedNext.textContent = '';
        }
    }

    function escapeHtml(s) {
        return String(s ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
    function escapeAttr(s) { return escapeHtml(s); }

    fetchPosts();
});
