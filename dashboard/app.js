
const search = document.querySelector("#search");
const typeFilter = document.querySelector("#type-filter");
const focusFilter = document.querySelector("#focus-filter");
const title = document.querySelector("#empty-title");
const description = document.querySelector("#empty-description");
const reset = document.querySelector("#reset-filters");

function updatePreview() {
  const selected = [];

  if (search.value.trim()) {
    selected.push(`Search: ${search.value.trim()}`);
  }

  if (typeFilter.value !== "all") {
    selected.push(`Type: ${typeFilter.value}`);
  }

  if (focusFilter.value !== "all") {
    selected.push(`Focus: ${focusFilter.value}`);
  }

  title.textContent = selected.length
    ? "Your selected filters"
    : "Your opportunity feed is ready to connect";

  description.textContent = selected.length
    ? selected.join(" | ") +
      ". Live opportunity results are not connected yet."
    : "Connect the Python radar to display real opportunities, official application links, eligibility and deadlines.";
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
