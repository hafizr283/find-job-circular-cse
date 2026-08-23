(function () {
  "use strict";

  const data = window.INTERNSHIP_DATA || { generated_at: null, summary: {}, jobs: [], source_directory: [] };
  const normalizedJobs = (Array.isArray(data.jobs) ? data.jobs : []).map((job) => {
    const enriched = {
      ...job,
      job_type: job.job_type || (/\bintern(?:ship|s)?\b/i.test(job.title || "") ? "Internship" : "Fresher job"),
      experience_text: job.experience_text || "",
    };
    // Lowercase search haystack built once per job instead of on every render pass.
    enriched._haystack = `${enriched.title} ${enriched.company} ${enriched.location} ${enriched.category} ${enriched.job_type} ${enriched.experience_text} ${enriched.description}`
      .toLowerCase()
      .replace(/\s+/g, " ");
    return enriched;
  });
  const state = {
    jobs: normalizedJobs,
    query: "",
    sort: "relevance",
    savedOnly: false,
    pay: new Set(),
    mode: new Set(),
    location: "",
    category: "",
    type: new Set(),
    freshOnly: false,
    unappliedOnly: false,
    deadlineMode: "",
    deadlineDays: null,
    customDays: 7,
    datedOnly: false,
    strongCompaniesOnly: false,
    hideReposts: false,
    noExperienceOnly: false,
  };
  const savedKey = "internbd-saved-jobs";
  const applicationsKey = "internbd-applications";
  const queueKey = "internbd-apply-queue";
  const dismissedKey = "internbd_dismissed_v1";
  const themeKey = "internbd-theme";

  // localStorage can hold anything a previous broken script or extension wrote.
  // Parse defensively; if a stored value is corrupt, overwrite it with the default
  // so one bad payload cannot crash every load.
  function readStoredJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return fallback;
      const parsed = JSON.parse(raw);
      return parsed === null || parsed === undefined ? fallback : parsed;
    } catch (error) {
      try { localStorage.setItem(key, JSON.stringify(fallback)); } catch (writeError) { /* storage unavailable */ }
      return fallback;
    }
  }

  function writeStoredJson(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (error) { /* storage unavailable */ }
  }

  function readStoredText(key) {
    try { return localStorage.getItem(key); } catch (error) { return null; }
  }

  function writeStoredText(key, value) {
    try { localStorage.setItem(key, value); } catch (error) { /* storage unavailable */ }
  }

  const asStringArray = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === "string") : []);

  const saved = new Set(asStringArray(readStoredJson(savedKey, [])));
  const queue = new Set(asStringArray(readStoredJson(queueKey, [])));
  const dismissed = new Set(asStringArray(readStoredJson(dismissedKey, [])));
  const rawApplications = readStoredJson(applicationsKey, {});
  const applications = rawApplications && typeof rawApplications === "object" && !Array.isArray(rawApplications) ? rawApplications : {};

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const escapeHtml = (value) => String(value || "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  const icon = (name) => `<i data-lucide="${name}"></i>`;

  function refreshIcons() {
    if (window.lucide) window.lucide.createIcons();
  }

  function saveState() {
    writeStoredJson(savedKey, Array.from(saved));
    $("#savedCount").textContent = String(saved.size);
  }

  function saveApplications() {
    writeStoredJson(applicationsKey, applications);
    renderSummary();
  }

  function saveQueue() {
    writeStoredJson(queueKey, Array.from(queue));
    $("#queueCount").textContent = String(queue.size);
  }

  function saveDismissed() {
    writeStoredJson(dismissedKey, Array.from(dismissed));
    renderHidden();
    populateFilters();
    renderSummary();
  }

  function applicationFor(jobId) {
    if (!applications[jobId]) applications[jobId] = { applied: false, cv_name: "", applied_at: "" };
    return applications[jobId];
  }

  function formatDate(value) {
    if (!value) return "Date not stated";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "Date not stated";
    const diff = Math.max(0, Date.now() - parsed.getTime());
    const hours = Math.floor(diff / 3600000);
    if (hours < 24) return hours <= 1 ? "Posted just now" : `Posted ${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `Posted ${days}d ago`;
    return parsed.toLocaleDateString("en-BD", { day: "numeric", month: "short", year: "numeric" });
  }

  function formatGenerated(value) {
    if (!value) return "No refresh yet";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "No refresh yet";
    return parsed.toLocaleString("en-BD", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  }

  const dayMs = 86400000;

  function todayStart() {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  }

  function deadlineDate(job) {
    if (!job.deadline) return null;
    const parsed = new Date(`${job.deadline}T00:00:00`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  // Whole days from today until the deadline. null means no usable deadline, negative means already past.
  function daysLeft(job) {
    const parsed = deadlineDate(job);
    if (!parsed) return null;
    return Math.round((parsed.getTime() - todayStart()) / dayMs);
  }

  function isClosed(job) {
    return job.posting_status === "closed";
  }

  // A closed posting is treated exactly like a passed deadline: dropped at render
  // time as well as at collection time, so a stale data file cannot show a
  // circular that stopped accepting applications.
  function isExpired(job) {
    const days = daysLeft(job);
    return isClosed(job) || job.deadline_status === "expired" || (days !== null && days < 0);
  }

  function countdownText(days) {
    if (days === 0) return "Closes today";
    if (days === 1) return "1 day left";
    return `${days} days left`;
  }

  function deadlineLabel(job) {
    if (isClosed(job)) return ["No longer accepting", "deadline-expired"];
    if (isExpired(job)) return ["Deadline passed", "deadline-expired"];
    const days = daysLeft(job);
    if (days === null) return [job.deadline ? job.deadline_text || "Deadline not stated" : "Deadline not stated", "deadline-unknown"];
    const date = deadlineDate(job).toLocaleDateString("en-BD", { day: "numeric", month: "short", year: "numeric" });
    return [`Apply by ${date}`, days <= 3 ? "deadline-urgent" : days <= 7 ? "deadline-soon" : "deadline-open"];
  }

  function deadlineTag(job) {
    const days = daysLeft(job);
    if (isExpired(job) || days === null || days > 14) return "";
    const tone = days <= 3 ? "tag-deadline-urgent" : days <= 7 ? "tag-deadline-soon" : "tag-deadline-later";
    return `<span class="tag ${tone}">${escapeHtml(countdownText(days))}</span>`;
  }

  function payTag(job) {
    const map = { confirmed: ["Paid confirmed", "tag-paid"], likely: ["Allowance mentioned", "tag-likely"], unpaid: ["Unpaid", "tag-unknown"], unknown: ["Pay not stated", "tag-unknown"] };
    const [label, css] = map[job.pay_status] || map.unknown;
    return `<span class="tag ${css}">${escapeHtml(label)}</span>`;
  }

  function typeTag(job) {
    return `<span class="tag tag-type">${escapeHtml(job.job_type || "Early career")}</span>`;
  }

  const TIER_LABEL = {
    A: ["Tier A company", "tag-tier-a", "Strong employer: real engineering, pays freshers, invests in juniors"],
    B: ["Tier B company", "tag-tier-b", "Solid employer: reasonable learning and pay"],
    C: ["Tier C company", "tag-tier-c", "Mixed: worth it for experience, verify pay and hours"],
    D: ["Tier D company", "tag-tier-d", "Weak or unverified: check carefully before spending effort"],
  };

  function tierTag(job) {
    const entry = TIER_LABEL[job.company_tier];
    if (!entry) return `<span class="tag tag-tier-unrated" title="This company has not been rated yet. Unrated is neutral, not bad.">Unrated company</span>`;
    const [label, css, hint] = entry;
    const score = Number.isFinite(job.company_score) && job.company_score > 0 ? ` ${job.company_score}` : "";
    return `<span class="tag ${css}" title="${escapeHtml(hint)}">${escapeHtml(label + score)}</span>`;
  }

  const FLAG_LABEL = {
    "aggregator-repost": "Reposted listing, employer named in the description",
    "staffing-agency": "Staffing agency, the employer is a client",
    "unpaid-internship-only": "Unpaid internship",
    "no-pay-disclosed-ever": "Never discloses pay",
    "salary-delay-reports": "Reports of delayed salary",
    "excessive-unpaid-overtime-reports": "Reports of unpaid overtime",
    "no-verifiable-web-presence": "No verifiable web presence",
    "non-cse-bundle": "Bundles unrelated non-CSE roles",
    "pay-to-apply": "Asks applicants for money",
    "training-fee": "Charges a training fee",
    "bond-or-security-deposit": "Requires a bond or deposit",
    "mlm-or-commission-only": "Commission only or MLM",
  };

  function flagTags(job) {
    const flags = Array.isArray(job.company_flags) ? job.company_flags : [];
    return flags
      .map((flag) => `<span class="tag tag-flag" title="${escapeHtml(FLAG_LABEL[flag] || flag)}">${escapeHtml(FLAG_LABEL[flag] || flag)}</span>`)
      .join("");
  }

  function verifiedTag(job) {
    if (job.review_status !== "verified") return "";
    const note = job.review_notes ? `AI reviewed: ${job.review_notes}` : "Checked by an AI review pass";
    return `<span class="tag tag-verified" title="${escapeHtml(note)}">${icon("check-check")} Reviewed</span>`;
  }

  function experienceTag(job) {
    const years = job.experience_years_min;
    if (!Number.isFinite(years)) return "";
    if (years <= 0) return `<span class="tag tag-exp-none" title="The circular states no experience requirement">No experience needed</span>`;
    const label = years === 1 ? "1 yr experience" : `${years} yrs experience`;
    return `<span class="tag tag-exp" title="Smallest stated experience requirement">${escapeHtml(label)}</span>`;
  }

  // The logo is built without any scraped string inside an attribute-embedded
  // handler. The fallback initials travel through a data attribute and a real
  // error listener paints them into the parent via textContent, so a crafted
  // company name can never execute markup or script here.
  function jobLogo(job) {
    const initials = (job.company || "?").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
    if (!job.logo) return escapeHtml(initials);
    return `<img src="${escapeHtml(job.logo)}" alt="" loading="lazy" data-initials="${escapeHtml(initials)}">`;
  }

  function wireLogoFallbacks(scope) {
    (scope || document).querySelectorAll(".company-logo img").forEach((img) => {
      img.addEventListener("error", () => {
        img.parentElement.textContent = img.dataset.initials || "?";
      }, { once: true });
    });
  }

  const payRank = (job) => ({ confirmed: 3, likely: 2, unknown: 1, unpaid: 0 }[job.pay_status] || 0);

  const REPOST_FLAGS = ["aggregator-repost", "staffing-agency"];
  const repostFlagged = (job) =>
    Array.isArray(job.company_flags) && job.company_flags.some((flag) => REPOST_FLAGS.includes(flag));

  function activeJobs() {
    return state.jobs.filter((job) => !isExpired(job) && !dismissed.has(job.id));
  }

  function visibleJobs() {
    const query = state.query.trim().toLowerCase();
    let jobs = state.jobs.filter((job) => {
      const application = applicationFor(job.id);
      const haystack = `${job._haystack || ""} ${application.cv_name}`.toLowerCase();
      const days = daysLeft(job);
      if (isExpired(job)) return false;
      if (dismissed.has(job.id)) return false;
      if (query && !haystack.includes(query)) return false;
      if (state.savedOnly && !saved.has(job.id)) return false;
      if (state.pay.size && !state.pay.has(job.pay_status)) return false;
      if (state.mode.size && !state.mode.has(job.work_mode)) return false;
      if (state.location && !job.location.toLowerCase().includes(state.location.toLowerCase())) return false;
      if (state.category && job.category !== state.category) return false;
      if (state.type.size && !state.type.has(job.job_type)) return false;
      if (state.freshOnly && !job.is_fresh) return false;
      if (state.unappliedOnly && application.applied) return false;
      if (state.datedOnly && days === null) return false;
      if (state.strongCompaniesOnly && !["A", "B"].includes(job.company_tier)) return false;
      // Reposts hide the real employer behind a job-board name, so hiding them is
      // about knowing who you would work for, not about the role quality.
      if (state.hideReposts && repostFlagged(job)) return false;
      if (state.noExperienceOnly && Number.isFinite(job.experience_years_min) && job.experience_years_min > 0) return false;
      // A day window only judges roles that stated a deadline; undated roles stay and are pushed to the end.
      if (state.deadlineDays !== null && days !== null && days > state.deadlineDays) return false;
      return true;
    });
    const deadlineFirst = state.sort === "deadline" || state.deadlineDays !== null;
    jobs.sort((a, b) => {
      const aDays = daysLeft(a);
      const bDays = daysLeft(b);
      if (deadlineFirst && (aDays === null) !== (bDays === null)) return aDays === null ? 1 : -1;
      if (state.sort === "deadline" && aDays !== null && bDays !== null && aDays !== bDays) return aDays - bDays;
      if (state.sort === "newest") return String(b.posted_at).localeCompare(String(a.posted_at));
      if (state.sort === "paid") return payRank(b) - payRank(a) || (b.score || 0) - (a.score || 0);
      return (b.score || 0) - (a.score || 0);
    });
    return jobs;
  }

  function resultSummary(jobs) {
    if (state.savedOnly) return `${jobs.length} saved ${jobs.length === 1 ? "role" : "roles"}`;
    if (state.deadlineDays === null) return `${jobs.length} of ${activeJobs().length} roles shown`;
    const dated = jobs.filter((job) => daysLeft(job) !== null).length;
    const window = state.deadlineDays === 0 ? "closing today" : `closing in ${state.deadlineDays} ${state.deadlineDays === 1 ? "day" : "days"} or less`;
    const undated = jobs.length - dated;
    const tail = state.datedOnly ? "undated roles hidden" : `${undated} undated ${undated === 1 ? "role" : "roles"} listed last`;
    return `${dated} ${window} · ${tail}`;
  }

  function renderJobs() {
    const jobs = visibleJobs();
    const list = $("#jobList");
    $("#resultSummary").textContent = resultSummary(jobs);
    $("#emptyState").hidden = jobs.length !== 0;
    list.hidden = jobs.length === 0;
    list.innerHTML = jobs.map((job) => {
      const active = saved.has(job.id);
      const queued = queue.has(job.id);
      const application = applicationFor(job.id);
      const freshness = job.is_fresh ? formatDate(job.posted_at) : "Not in latest scan";
      const freshnessClass = job.is_fresh ? "" : "stale";
      const [deadline, deadlineClass] = deadlineLabel(job);
      return `<article class="job-row">
        <div class="company-logo" aria-hidden="true">${jobLogo(job)}</div>
        <div class="job-main">
          <div class="job-title-line">${deadlineTag(job)}${typeTag(job)}${payTag(job)}${experienceTag(job)}${verifiedTag(job)}<a class="job-title" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(job.title)}</a></div>
          <p class="job-company">${escapeHtml(job.company)}</p>
          <div class="job-trust">${tierTag(job)}${flagTags(job)}</div>
          <div class="job-meta"><span>${icon("map-pin")} ${escapeHtml(job.location || "Bangladesh")}</span><span>${icon("layers-3")} ${escapeHtml(job.category || "Other")}</span><span>${icon(job.work_mode === "Remote" ? "wifi" : "building-2")} ${escapeHtml(job.work_mode || "On-site")}</span></div>
        </div>
        <div class="job-side"><strong>${escapeHtml(job.pay_text || "Pay not stated")}</strong><span class="${deadlineClass}">${escapeHtml(deadline)}</span><span class="${freshnessClass}">${escapeHtml(freshness)}</span></div>
        <div class="row-actions">
          <button class="save-button ${active ? "active" : ""}" data-save="${escapeHtml(job.id)}" type="button" title="${active ? "Remove saved job" : "Save job"}" aria-label="${active ? "Remove saved job" : "Save job"}">${icon("bookmark")}</button>
          <button class="dismiss-button" data-dismiss="${escapeHtml(job.id)}" type="button" title="Not interested" aria-label="Hide ${escapeHtml(job.title)} from my feed">${icon("eye-off")}</button>
        </div>
        <div class="application-tracker ${application.applied ? "is-applied" : ""}">
          <button class="queue-button ${queued ? "queued" : ""}" data-queue="${escapeHtml(job.id)}" type="button" title="${queued ? "Remove from application queue" : "Add to application queue"}" aria-label="${queued ? "Remove from application queue" : "Add to application queue"}">${icon(queued ? "list-checks" : "list-plus")}<span>${queued ? "Queued" : "Queue application"}</span></button>
          <label class="applied-check"><input type="checkbox" data-applied="${escapeHtml(job.id)}" ${application.applied ? "checked" : ""}><span>${application.applied ? "Applied" : "Mark as applied"}</span></label>
          <label class="cv-field">${icon("file-text")}<span class="sr-only">CV filename</span><input type="text" data-cv-name="${escapeHtml(job.id)}" value="${escapeHtml(application.cv_name)}" placeholder="CV filename used, e.g. Hafiz_CV.pdf" maxlength="160"></label>
          <span class="tracker-note">${application.applied_at ? `Applied ${escapeHtml(formatDate(application.applied_at).replace("Posted ", ""))}` : "Stored in this browser"}</span>
        </div>
      </article>`;
    }).join("");
    $$('[data-save]').forEach((button) => button.addEventListener("click", () => {
      const id = button.dataset.save;
      if (saved.has(id)) saved.delete(id); else saved.add(id);
      saveState();
      renderJobs();
    }));
    $$('[data-dismiss]').forEach((button) => button.addEventListener("click", () => dismissJob(button.dataset.dismiss)));
    $$('[data-queue]').forEach((button) => button.addEventListener("click", () => {
      const id = button.dataset.queue;
      if (queue.has(id)) queue.delete(id); else queue.add(id);
      saveQueue();
      renderJobs();
    }));
    $$('[data-applied]').forEach((checkbox) => checkbox.addEventListener("change", () => {
      const application = applicationFor(checkbox.dataset.applied);
      application.applied = checkbox.checked;
      application.applied_at = checkbox.checked ? new Date().toISOString() : "";
      saveApplications();
      if (state.unappliedOnly) renderJobs();
      else {
        checkbox.closest(".application-tracker").classList.toggle("is-applied", checkbox.checked);
        checkbox.nextElementSibling.textContent = checkbox.checked ? "Applied" : "Mark as applied";
      }
    }));
    $$('[data-cv-name]').forEach((input) => input.addEventListener("input", () => {
      applicationFor(input.dataset.cvName).cv_name = input.value.trim();
      saveApplications();
    }));
    wireLogoFallbacks(list);
    refreshIcons();
  }

  let toastTimer = null;

  function hideToast() {
    clearTimeout(toastTimer);
    $("#toast").classList.remove("show");
  }

  function showToast(message, undoAction) {
    const toast = $("#toast");
    $("#toastMessage").textContent = message;
    const undo = $("#toastUndo");
    undo.hidden = !undoAction;
    undo.onclick = () => {
      hideToast();
      if (undoAction) undoAction();
    };
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, 6000);
  }

  function dismissJob(id) {
    const job = state.jobs.find((item) => item.id === id);
    dismissed.add(id);
    saveDismissed();
    renderJobs();
    if (job) showToast("Job hidden", () => {
      dismissed.delete(id);
      saveDismissed();
      renderJobs();
    });
  }

  function restoreJob(id) {
    dismissed.delete(id);
    saveDismissed();
    renderJobs();
  }

  function renderHidden() {
    const hiddenJobs = state.jobs.filter((job) => dismissed.has(job.id));
    $("#hiddenCount").textContent = String(hiddenJobs.length);
    $("#restoreAllButton").hidden = hiddenJobs.length === 0;
    $("#hiddenSection").hidden = false;
    $("#hiddenList").innerHTML = hiddenJobs.length ? hiddenJobs.map((job) =>
      `<div class="hidden-row"><span class="hidden-label"><strong>${escapeHtml(job.title)}</strong><small>${escapeHtml(job.company)}</small></span><button class="text-button" data-restore="${escapeHtml(job.id)}" type="button" aria-label="Restore ${escapeHtml(job.title)}">Restore</button></div>`
    ).join("") : `<p class="hidden-empty">Nothing hidden yet.</p>`;
    $$('[data-restore]').forEach((button) => button.addEventListener("click", () => restoreJob(button.dataset.restore)));
  }

  function populateFilters() {
    const openJobs = activeJobs();
    const locations = Array.from(new Set(openJobs.map((job) => job.location).filter(Boolean))).sort();
    const categories = Array.from(new Set(openJobs.map((job) => job.category).filter(Boolean))).sort();
    $("#locationSelect").innerHTML = `<option value="">Anywhere in Bangladesh</option>${locations.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("")}`;
    $("#categorySelect").innerHTML = `<option value="">Any field</option>${categories.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("")}`;
    syncFilterUiSelectValues();
    const count = (status) => openJobs.filter((job) => job.pay_status === status).length;
    const typeCount = (type) => openJobs.filter((job) => job.job_type === type).length;
    $("#internshipTypeCount").textContent = typeCount("Internship");
    $("#fresherTypeCount").textContent = typeCount("Fresher job");
    $("#payConfirmedCount").textContent = count("confirmed");
    $("#payLikelyCount").textContent = count("likely");
    $("#payUnknownCount").textContent = count("unknown");
    const closingWithin = (limit) => openJobs.filter((job) => { const days = daysLeft(job); return days !== null && days <= limit; }).length;
    $$("#deadlineSelect option[data-window]").forEach((option) => { option.textContent = `${option.dataset.label} (${closingWithin(Number(option.value))})`; });
    $("#deadlineKnownCount").textContent = openJobs.filter((job) => daysLeft(job) !== null).length;
    $("#strongCompanyCount").textContent = openJobs.filter((job) => ["A", "B"].includes(job.company_tier)).length;
    $("#repostCount").textContent = openJobs.filter(repostFlagged).length;
    $("#noExpCount").textContent = openJobs.filter((job) => job.experience_years_min === 0).length;
  }

  function postedToday(job) {
    const parsed = new Date(job.posted_at);
    return !Number.isNaN(parsed.getTime()) && parsed.getTime() >= todayStart();
  }

  function renderSummary() {
    const openJobs = activeJobs();
    $("#totalJobs").textContent = String(openJobs.length);
    $("#internshipCount").textContent = String(openJobs.filter((job) => job.job_type === "Internship").length);
    $("#fresherJobCount").textContent = String(openJobs.filter((job) => job.job_type === "Fresher job").length);
    $("#newTodayCount").textContent = String(openJobs.filter(postedToday).length);
    $("#appliedCount").textContent = String(openJobs.filter((job) => applicationFor(job.id).applied).length);
    $("#generatedAt").textContent = formatGenerated(data.generated_at);
    $("#refreshLabel").textContent = data.generated_at ? "3x daily feed" : "Collector not run";
    $("#savedCount").textContent = String(saved.size);
    $("#queueCount").textContent = String(queue.size);
    $("#scanBanner").hidden = data.scan_status !== "degraded";
  }

  function filterCount() {
    return state.type.size + state.pay.size + state.mode.size + (state.location ? 1 : 0) + (state.category ? 1 : 0) + (state.freshOnly ? 1 : 0) + (state.unappliedOnly ? 1 : 0) + (state.savedOnly ? 1 : 0) + (state.deadlineDays !== null ? 1 : 0) + (state.datedOnly ? 1 : 0) + (state.strongCompaniesOnly ? 1 : 0) + (state.hideReposts ? 1 : 0) + (state.noExperienceOnly ? 1 : 0);
  }

  function clampDays(value) {
    const raw = String(value).trim();
    if (!raw) return null;
    const days = Math.round(Number(raw));
    if (!Number.isFinite(days)) return null;
    return Math.min(Math.max(days, 0), 365);
  }

  function applyDeadlineMode() {
    if (state.deadlineMode === "custom") state.deadlineDays = clampDays(state.customDays);
    else state.deadlineDays = state.deadlineMode === "" ? null : clampDays(state.deadlineMode);
  }

  function syncFilterUiSelectValues() {
    $("#locationSelect").value = state.location;
    $("#categorySelect").value = state.category;
  }

  function syncFilterUi() {
    $$('input[name="type"]').forEach((input) => { input.checked = state.type.has(input.value); });
    $$('input[name="pay"]').forEach((input) => { input.checked = state.pay.has(input.value); });
    $$('input[name="mode"]').forEach((input) => { input.checked = state.mode.has(input.value); });
    syncFilterUiSelectValues();
    $("#freshOnly").checked = state.freshOnly;
    $("#unappliedOnly").checked = state.unappliedOnly;
    $("#deadlineSelect").value = state.deadlineMode;
    $("#deadlineCustomWrap").hidden = state.deadlineMode !== "custom";
    $("#deadlineDays").value = String(state.customDays);
    $("#datedOnly").checked = state.datedOnly;
    $("#strongCompaniesOnly").checked = state.strongCompaniesOnly;
    $("#hideReposts").checked = state.hideReposts;
    $("#noExperienceOnly").checked = state.noExperienceOnly;
    $("#deadlineHint").textContent = state.datedOnly ? "Roles with no stated deadline are hidden." : "Roles with no stated deadline stay in the list and sort last.";
    $("#filterCount").textContent = filterCount();
  }

  function clearFilters() {
    state.query = ""; state.type.clear(); state.pay.clear(); state.mode.clear(); state.location = ""; state.category = ""; state.freshOnly = false; state.unappliedOnly = false; state.savedOnly = false;
    state.deadlineMode = ""; state.deadlineDays = null; state.customDays = 7; state.datedOnly = false;
    state.strongCompaniesOnly = false; state.hideReposts = false; state.noExperienceOnly = false;
    $("#searchInput").value = "";
    $("#savedTopButton").classList.remove("active");
    syncFilterUi();
    renderJobs();
  }

  function renderSources() {
    const statusByName = Object.fromEntries((data.source_status || []).map((status) => [status.name, status]));
    $("#sourceList").innerHTML = (data.source_directory || []).map((source) => {
      const status = statusByName[source.name];
      const note = status?.message ? `${source.note} ${status.message}` : source.note;
      return `<a class="source-row" href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer"><span class="source-icon ${source.kind === "automatic" ? "automatic" : ""}">${icon(source.kind === "automatic" ? "radio-tower" : "external-link")}</span><span><strong>${escapeHtml(source.name)}</strong><p>${escapeHtml(note)}</p></span><span class="source-kind">${source.kind === "automatic" ? `${icon("check-circle-2")} Automatic` : `${icon("arrow-up-right")} Open`}</span></a>`;
    }).join("");
    refreshIcons();
  }

  function renderQueue() {
    const jobs = state.jobs.filter((job) => queue.has(job.id));
    $("#queueList").innerHTML = jobs.length ? jobs.map((job) => `<div class="queue-row"><span class="queue-row-type">${typeTag(job)}</span><span class="queue-row-main"><strong>${escapeHtml(job.title)}</strong><small>${escapeHtml(job.company)} · ${escapeHtml(job.location || "Bangladesh")}</small></span><button class="icon-button" data-queue-remove="${escapeHtml(job.id)}" type="button" title="Remove from queue" aria-label="Remove ${escapeHtml(job.title)} from queue">${icon("x")}</button></div>`).join("") : `<div class="queue-empty"><i data-lucide="inbox"></i><p>No jobs are queued.</p></div>`;
    $$('[data-queue-remove]').forEach((button) => button.addEventListener("click", () => {
      queue.delete(button.dataset.queueRemove);
      saveQueue();
      renderQueue();
      renderJobs();
    }));
    refreshIcons();
  }

  function openQueue() {
    renderQueue();
    $("#queueModal").hidden = false;
  }

  function exportQueue() {
    const jobs = state.jobs.filter((job) => queue.has(job.id)).map((job) => ({
      id: job.id,
      title: job.title,
      company: job.company,
      location: job.location,
      url: job.url,
      source: job.source,
      job_type: job.job_type,
      category: job.category,
      description: job.description || "",
    }));
    if (!jobs.length) return;
    const payload = { version: 1, exported_at: new Date().toISOString(), jobs };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "internbd-apply-queue.json";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function debounce(fn, wait) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  }

  const systemDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function setTheme(theme, persist) {
    document.documentElement.dataset.theme = theme;
    const toggle = $("#themeToggle");
    toggle.innerHTML = icon(theme === "dark" ? "sun" : "moon");
    toggle.title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
    toggle.setAttribute("aria-label", toggle.title);
    if (persist) writeStoredText(themeKey, theme);
    refreshIcons();
  }

  function initTheme() {
    const storedTheme = readStoredText(themeKey);
    const systemPrefersDark = systemDark ? systemDark.matches : false;
    setTheme(storedTheme === "dark" || storedTheme === "light" ? storedTheme : systemPrefersDark ? "dark" : "light", false);
    $("#themeToggle").addEventListener("click", () => {
      setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
    });
    if (systemDark && typeof systemDark.addEventListener === "function") {
      systemDark.addEventListener("change", (event) => {
        if (readStoredText(themeKey)) return;
        setTheme(event.matches ? "dark" : "light", false);
      });
    }
  }

  function bindEvents() {
    const debouncedSearchRender = debounce(renderJobs, 150);
    $("#searchInput").addEventListener("input", (event) => { state.query = event.target.value; debouncedSearchRender(); });
    $("#sortSelect").addEventListener("change", (event) => { state.sort = event.target.value; renderJobs(); });
    $$('input[name="type"]').forEach((input) => input.addEventListener("change", (event) => { if (event.target.checked) state.type.add(event.target.value); else state.type.delete(event.target.value); syncFilterUi(); renderJobs(); }));
    $$('input[name="pay"]').forEach((input) => input.addEventListener("change", (event) => { if (event.target.checked) state.pay.add(event.target.value); else state.pay.delete(event.target.value); syncFilterUi(); renderJobs(); }));
    $$('input[name="mode"]').forEach((input) => input.addEventListener("change", (event) => { if (event.target.checked) state.mode.add(event.target.value); else state.mode.delete(event.target.value); syncFilterUi(); renderJobs(); }));
    $("#locationSelect").addEventListener("change", (event) => { state.location = event.target.value; syncFilterUi(); renderJobs(); });
    $("#categorySelect").addEventListener("change", (event) => { state.category = event.target.value; syncFilterUi(); renderJobs(); });
    $("#freshOnly").addEventListener("change", (event) => { state.freshOnly = event.target.checked; syncFilterUi(); renderJobs(); });
    $("#unappliedOnly").addEventListener("change", (event) => { state.unappliedOnly = event.target.checked; syncFilterUi(); renderJobs(); });
    $("#deadlineSelect").addEventListener("change", (event) => {
      state.deadlineMode = event.target.value;
      applyDeadlineMode();
      syncFilterUi();
      if (state.deadlineMode === "custom") $("#deadlineDays").focus();
      renderJobs();
    });
    $("#deadlineDays").addEventListener("input", (event) => {
      const days = clampDays(event.target.value);
      if (days === null) return;
      state.customDays = days;
      state.deadlineMode = "custom";
      applyDeadlineMode();
      $("#filterCount").textContent = filterCount();
      renderJobs();
    });
    $("#deadlineDays").addEventListener("blur", () => { syncFilterUi(); });
    $("#datedOnly").addEventListener("change", (event) => { state.datedOnly = event.target.checked; syncFilterUi(); renderJobs(); });
    $("#strongCompaniesOnly").addEventListener("change", (event) => { state.strongCompaniesOnly = event.target.checked; syncFilterUi(); renderJobs(); });
    $("#hideReposts").addEventListener("change", (event) => { state.hideReposts = event.target.checked; syncFilterUi(); renderJobs(); });
    $("#noExperienceOnly").addEventListener("change", (event) => { state.noExperienceOnly = event.target.checked; syncFilterUi(); renderJobs(); });
    $("#clearFilters").addEventListener("click", clearFilters);
    $("#emptyClear").addEventListener("click", clearFilters);
    $("#savedTopButton").addEventListener("click", () => { state.savedOnly = !state.savedOnly; $("#savedTopButton").classList.toggle("active", state.savedOnly); renderJobs(); });
    $("#queueTopButton").addEventListener("click", openQueue);
    $("#queueClose").addEventListener("click", () => { $("#queueModal").hidden = true; });
    $("#queueModal").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.hidden = true; });
    $("#queueExport").addEventListener("click", exportQueue);
    $("#queueClear").addEventListener("click", () => { queue.clear(); saveQueue(); renderQueue(); renderJobs(); });
    $("#restoreAllButton").addEventListener("click", () => { dismissed.clear(); saveDismissed(); renderJobs(); });
    $("#scanBannerClose").addEventListener("click", () => { $("#scanBanner").hidden = true; });
    $("#filterToggle").addEventListener("click", () => {
      const panel = $("#filterPanel");
      panel.classList.toggle("open");
      $("#filterToggle").setAttribute("aria-expanded", panel.classList.contains("open") ? "true" : "false");
    });
    $("#mobileClose").addEventListener("click", () => { $("#filterPanel").classList.remove("open"); $("#filterToggle").setAttribute("aria-expanded", "false"); });
    $("#sourcesButton").addEventListener("click", () => { $("#sourcesModal").hidden = false; });
    $("#sourcesClose").addEventListener("click", () => { $("#sourcesModal").hidden = true; });
    $("#sourcesModal").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.hidden = true; });
    document.addEventListener("keydown", (event) => {
      if (event.key === "/" && document.activeElement.tagName !== "INPUT") { event.preventDefault(); $("#searchInput").focus(); }
      if (event.key === "Escape") { hideToast(); $("#sourcesModal").hidden = true; $("#queueModal").hidden = true; $("#filterPanel").classList.remove("open"); }
    });
  }

  populateFilters();
  renderSummary();
  syncFilterUi();
  saveQueue();
  renderHidden();
  renderJobs();
  renderSources();
  bindEvents();
  initTheme();
  refreshIcons();
}());
