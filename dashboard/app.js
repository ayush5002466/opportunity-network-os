const search = document.querySelector("#search");
const typeFilter = document.querySelector("#type-filter");
const focusFilter = document.querySelector("#focus-filter");
const title = document.querySelector("#empty-title");
const description = document.querySelector("#empty-description");
const reset = document.querySelector("#reset-filters");

function updatePreview() {
  const hasFilters = Boolean(search.value.trim()) || typeFilter.value !== "all" || focusFilter.value !== "all";
  title.textContent = hasFilters ? "No connected opportunities match these filters" : "Your opportunity feed is ready to connect";
  description.textContent = hasFilters
    ? "These filters are ready for the live feed integration. No live opportunity data is connected yet, so this preview cannot return matching openings."
    : "Once the radar data source is integrated, matching opportunities will appear here with official links, eligibility checks and deadline details.";
}
[search, typeFilter, focusFilter].forEach((control) => control.addEventListener("input", updatePreview));
reset.addEventListener("click", () => {
  search.value = "";
  typeFilter.value = "all";
  focusFilter.value = "all";
  updatePreview();
});
