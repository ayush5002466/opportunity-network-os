
const $ = (selector) => document.querySelector(selector);

const search = $("#search");
const typeFilter = $("#type-filter");
const focusFilter = $("#focus-filter");
const title = $("#empty-title");
const description = $("#empty-description");
const results = $("#opportunity-results");
const dataStatus = $("#data-status");
const dataMessage = $("#data-message");
const savedCount = $("#saved-count");
const applicationCount = $("#application-count");
const urgentCount = $("#urgent-count");
const reset = $("#reset-filters");

const STORAGE_KEY = "opportunity-network-os-applications";

let jobs = [];

function getApplications() {
  try {
    const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function saveApplications(applications) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(applications));
  renderTracker();
  updateCounts();
  renderJobs();
}

function classifyType(job) {
  const text = `${job.title || ""} ${job.type || ""}`.toLowerCase();

  if (text.includes("apprentice")) return "Apprenticeship";
  if (text.includes("fellow")) return "Fellowship";
  if (text.includes("research")) return "Research";
  if (text.includes("intern")) return "Internship";
  if (/new grad|early career|graduate/.test(text)) return "New grad";

  return "Other";
}

function classifyFocus(job) {
  const text = `${job.title || ""} ${job.description || ""} ${job.department || ""}`.toLowerCase();

  if (/security|cybersecurity|application security/.test(text)) return "Security";
  if (/machine learning|\bai\b|artificial intelligence|data science/.test(text)) return "AI / ML";
  if (/platform|devops|infrastructure|reliability|cloud engineer/.test(text)) return "Platform";
  if (/backend|back-end|software engineer|java|api engineer/.test(text)) return "Backend";

  return "Other";
}

function getJobId(job) {
  return String(
    job.id ||
    job.job_id ||
    job.url ||
    `${job.company || ""}-${job.title || ""}`
  );
}

function updateCounts() {
  savedCount.textContent = jobs.length;

  const applications = getApplications();
  applicationCount.textContent = applications.length;

  const now = Date.now();

  urgentCount.textContent = jobs.filter((job) => {
    const deadline = job.deadline || job.application_deadline || job.close_date;
    if (!deadline) return false;

    const time = Date.parse(deadline);

    return Number.isFinite(time) &&
      time >= now &&
      time - now <= 48 * 60 * 60 * 1000;
  }).length;
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);

  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;

  return element;
}

function makeButton(text, className, onClick) {
  const button = makeElement("button", className, text);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

function addToTracker(job) {
  const applications = getApplications();
  const id = getJobId(job);

  if (applications.some((item) => item.id === id)) {
    showView("tracker");
    return;
  }

  applications.unshift({
    id,
    title: job.title || "Untitled role",
    company: job.company || "Unknown company",
    url: job.url || job.apply_url || "",
    status: "Saved",
    addedAt: new Date().toISOString()
  });

  saveApplications(applications);
  showView("tracker");
}

function renderJobs() {
  if (!search || !typeFilter || !focusFilter || !results) return;

  const query = search.value.trim().toLowerCase();
  const selectedType = typeFilter.value;
  const selectedFocus = focusFilter.value;

  const filtered = jobs.filter((job) => {
    const searchable = [
      job.title,
      job.company,
      job.location,
      job.source,
      job.description,
      job.department
    ].filter(Boolean).join(" ").toLowerCase();

    return (!query || searchable.includes(query)) &&
      (selectedType === "all" || classifyType(job) === selectedType) &&
      (selectedFocus === "all" || classifyFocus(job) === selectedFocus);
  });

  title.textContent = `${filtered.length} matching opportunities`;

  description.textContent = jobs.length
    ? "Review the original listing to confirm eligibility, location and deadlines."
    : "No opportunity records are available.";

  results.replaceChildren();

  if (!filtered.length) {
    results.append(
      makeElement(
        "p",
        "no-results",
        jobs.length
          ? "No matches found. Change your search or filters."
          : "No records loaded. Check the data-loading message above."
      )
    );
    return;
  }

  const trackedIds = new Set(getApplications().map((item) => item.id));

  filtered.forEach((job) => {
    const card = makeElement("article", "opportunity-card");

    card.append(
      makeElement("h3", "", job.title || "Untitled role"),
      makeElement(
        "p",
        "",
        `${job.company || "Unknown company"} · ${job.location || "Location not specified"}`
      ),
      makeElement(
        "p",
        "opportunity-tags",
        `${classifyType(job)} · ${classifyFocus(job)}`
      )
    );

    const url = job.url || job.apply_url;

    if (url && /^https?:\/\//i.test(url)) {
      const link = makeElement("a", "", "View official listing ↗");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
    }

    const alreadySaved = trackedIds.has(getJobId(job));

    card.append(
      makeButton(
        alreadySaved ? "Saved to tracker ✓" : "Save to tracker",
        "save-job-button",
        () => addToTracker(job)
      )
    );

    results.append(card);
  });
}

function ensureTrackerView() {
  let panel = $("#tracker-view");
  if (panel) return panel;

  panel = makeElement("section", "tracker-view");
  panel.id = "tracker-view";
  panel.hidden = true;

  const header = makeElement("div", "section-head");
  const heading = document.createElement("div");

  heading.append(
    makeElement("div", "eyebrow", "YOUR PIPELINE"),
    makeElement("h2", "", "Application tracker"),
    makeElement(
      "p",
      "",
      "Track saved roles and update their application status. Data is stored in this browser."
    )
  );

  header.append(
    heading,
    makeButton("Back to opportunities", "secondary-button", () => {
      showView("opportunities");
    })
  );

  const list = makeElement("div", "tracker-list");
  list.id = "tracker-list";
  list.setAttribute("aria-live", "polite");

  panel.append(header, list);

  const workflow = $("#workflow");

  if (workflow) {
    workflow.before(panel);
  } else {
    $("#overview").append(panel);
  }

  return panel;
}

function renderTracker() {
  const panel = ensureTrackerView();
  const list = $("#tracker-list");

  list.replaceChildren();

  const applications = getApplications();

  if (!applications.length) {
    list.append(
      makeElement(
        "p",
        "tracker-empty",
        "Your tracker is empty. Open Opportunities and save a role to get started."
      )
    );
    return;
  }

  applications.forEach((item) => {
    const row = makeElement("article", "tracker-card");
    const details = makeElement("div", "tracker-details");

    details.append(
      makeElement("h3", "", item.title),
      makeElement("p", "", item.company)
    );

    const status = document.createElement("select");
    status.setAttribute("aria-label", `Status for ${item.title}`);

    [
      "Saved",
      "Preparing",
      "Applied",
      "Interview",
      "Offer",
      "Rejected",
      "Withdrawn"
    ].forEach((value) => {
      const option = makeElement("option", "", value);
      option.value = value;
      option.selected = (item.status || "Saved") === value;
      status.append(option);
    });

    status.addEventListener("change", () => {
      const updated = getApplications().map((application) =>
        application.id === item.id
          ? { ...application, status: status.value }
          : application
      );

      saveApplications(updated);
    });

    row.append(details, status);

    if (item.url && /^https?:\/\//i.test(item.url)) {
      const link = makeElement("a", "", "Open listing ↗");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      row.append(link);
    }

    row.append(
      makeButton("Remove", "secondary-button", () => {
        const updated = getApplications().filter(
          (application) => application.id !== item.id
        );

        saveApplications(updated);
      })
    );

    list.append(row);
  });
}

function showView(view) {
  const tracker = ensureTrackerView();

  document.querySelectorAll(".nav").forEach((link) => {
    const href = link.getAttribute("href");

    const active =
      (view === "overview" && href === "#overview") ||
      (view === "opportunities" && href === "#opportunities") ||
      (view === "tracker" && href === "#workflow");

    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });

  tracker.hidden = view !== "tracker";

  if (view === "overview") {
    $("#overview").scrollIntoView({ behavior: "smooth", block: "start" });
  } else if (view === "opportunities") {
    $("#opportunities").scrollIntoView({ behavior: "smooth", block: "start" });
    search.focus({ preventScroll: true });
  } else {
    renderTracker();
    tracker.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

document.querySelectorAll(".nav").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();

    const href = link.getAttribute("href");

    if (href === "#overview") showView("overview");
    else if (href === "#opportunities") showView("opportunities");
    else showView("tracker");
  });
});

search.addEventListener("input", renderJobs);
typeFilter.addEventListener("change", renderJobs);
focusFilter.addEventListener("change", renderJobs);

if (reset) {
  reset.addEventListener("click", () => {
    search.value = "";
    typeFilter.value = "all";
    focusFilter.value = "all";
    renderJobs();
  });
}

async function loadJobs() {
  dataStatus.textContent = "Loading opportunity data…";
  dataMessage.textContent = "Reading dashboard/jobs.json.";

  try {
    const response = await fetch("./jobs.json", { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`jobs.json returned HTTP ${response.status}`);
    }

    const data = await response.json();

    if (!Array.isArray(data) && (!data || typeof data !== "object")) {
      throw new Error("The data file does not contain a JSON array or object.");
    }

    jobs = (Array.isArray(data) ? data : Object.values(data))
      .filter((job) => job && typeof job === "object");

    dataStatus.textContent = "Opportunity snapshot loaded";
    dataMessage.textContent =
      `${jobs.length} saved records loaded. Confirm current details on official listings.`;

    renderJobs();
    updateCounts();
  } catch (error) {
    console.error("Opportunity data loading failed:", error);

    dataStatus.textContent = "Could not load opportunity data";
    dataMessage.textContent = error.message;

    title.textContent = "Opportunity feed unavailable";
    description.textContent =
      "Check that dashboard/jobs.json exists in the deployed website.";

    results.replaceChildren(
      makeElement(
        "p",
        "no-results",
        "Open /jobs.json on your deployed website to verify that the data file is available."
      )
    );

    updateCounts();
  }
}

ensureTrackerView();
renderTracker();
updateCounts();
loadJobs();
