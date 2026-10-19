
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
const reset = $("#reset-filters");

let jobs = [];

function classifyType(job) {
  const text = (job.title || "").toLowerCase();

  if (text.includes("apprentice")) return "Apprenticeship";
  if (text.includes("fellow")) return "Fellowship";
  if (text.includes("research")) return "Research";
  if (text.includes("intern")) return "Internship";
  if (/new grad|early career|graduate/.test(text)) return "New grad";

  return "Other";
}

function classifyFocus(job) {
  const text = (job.title || "").toLowerCase();

  if (/security|cybersecurity/.test(text)) return "Security";
  if (/machine learning|\bai\b|data science/.test(text)) return "AI / ML";
  if (/platform|devops|infrastructure|reliability/.test(text)) return "Platform";
  if (/backend|back-end|software engineer|java/.test(text)) return "Backend";

  return "Other";
}

function renderJobs() {
  const query = search.value.trim().toLowerCase();
  const type = typeFilter.value;
  const focus = focusFilter.value;

  const filtered = jobs.filter((job) => {
    const searchable = [
      job.title,
      job.company,
      job.location,
      job.source
    ].join(" ").toLowerCase();

    return (
      (!query || searchable.includes(query)) &&
      (type === "all" || classifyType(job) === type) &&
      (focus === "all" || classifyFocus(job) === focus)
    );
  });

  title.textContent = `${filtered.length} matching opportunities`;
  description.textContent = jobs.length
    ? "Saved radar records. Check each official listing for current availability and eligibility."
    : "No opportunity records are available.";

  results.replaceChildren();

  if (filtered.length === 0) {
    const empty = document.createElement("p");
    empty.textContent = jobs.length
      ? "No matches. Try a different search or filter."
      : "No records loaded.";
    results.append(empty);
    return;
  }

  for (const job of filtered) {
    const card = document.createElement("article");
    card.className = "opportunity-card";

    const heading = document.createElement("h3");
    heading.textContent = job.title || "Untitled role";

    const company = document.createElement("p");
    company.textContent =
      `${job.company || "Unknown company"} · ${job.location || "Location not specified"}`;

    const tags = document.createElement("p");
    tags.className = "opportunity-tags";
    tags.textContent =
      `${classifyType(job)} · ${classifyFocus(job)}`;

    card.append(heading, company, tags);

    if (job.url && /^https?:\/\//i.test(job.url)) {
      const link = document.createElement("a");
      link.href = job.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "View official listing ↗";
      card.append(link);
    }

    results.append(card);
  }
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

    if (!data || typeof data !== "object" || Array.isArray(data)) {
      throw new Error("jobs.json must contain a JSON object of opportunity records.");
    }

    jobs = Object.values(data).filter(
      (job) => job && typeof job === "object"
    );

    if (savedCount) savedCount.textContent = String(jobs.length);

    dataStatus.textContent = "Opportunity snapshot loaded";
    dataMessage.textContent =
      `${jobs.length} saved records loaded. New data requires a refreshed snapshot.`;

    renderJobs();
  } catch (error) {
    console.error("Opportunity data loading failed:", error);

    dataStatus.textContent = "Could not load opportunity data";
    dataMessage.textContent = error.message;

    title.textContent = "Opportunity feed unavailable";
    description.textContent =
      "Check that dashboard/jobs.json exists in the deployed site.";

    results.replaceChildren();

    const help = document.createElement("p");
    help.textContent =
      "Open /jobs.json on this deployment to check whether the data file is being served.";
    results.append(help);
  }
}

search.addEventListener("input", renderJobs);
typeFilter.addEventListener("change", renderJobs);
focusFilter.addEventListener("change", renderJobs);

reset.addEventListener("click", () => {
  search.value = "";
  typeFilter.value = "all";
  focusFilter.value = "all";
  renderJobs();
});

// Make sidebar navigation visibly follow the selected section.
document.querySelectorAll(".nav").forEach((link) => {
  link.addEventListener("click", () => {
    document.querySelectorAll(".nav").forEach((item) => {
      item.classList.remove("active");
    });

    link.classList.add("active");
  });
});

loadJobs();
