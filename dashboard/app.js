
const search = document.querySelector("#search");
const typeFilter = document.querySelector("#type-filter");
const focusFilter = document.querySelector("#focus-filter");
const title = document.querySelector("#empty-title");
const description = document.querySelector("#empty-description");
const reset = document.querySelector("#reset-filters");

function updatePreview() {
  const query = search.value.trim();
  const type = typeFilter.value;
  const focus = focusFilter.value;

  const hasFilters =
    query !== "" ||
    type !== "all" ||
    focus !== "all";

  if (hasFilters) {
    const filters = [];

    if (query) filters.push(`Search: "${query}"`);
    if (type !== "all") filters.push(`Type: ${type}`);
    if (focus !== "all") filters.push(`Focus: ${focus}`);

    title.textContent = "Filters selected";
    description.textContent =
      filters.join(" · ") +
      ". These selections are ready for integration with live opportunity data. No live results are available yet.";
  } else {
    title.textContent =
      "Your opportunity feed is ready to connect";

    description.textContent =
      "Connect the Python radar to display real opportunities, official application links, eligibility and deadlines.";
  }
}

search.addEventListener("input", updatePreview);
typeFilter.addEventListener("change", updatePreview);
focusFilter.addEventListener("change", updatePreview);

reset.addEventListener("click", () => {
  search.value = "";
  typeFilter.value = "all";
  focusFilter.value = "all";
  updatePreview();
});

updatePreview();
