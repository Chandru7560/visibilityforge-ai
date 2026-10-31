const selectionForm = document.querySelector("#questionSelectionForm");

if (selectionForm) {
  const rows = [...selectionForm.querySelectorAll(".question-selector")];
  const selectAll = selectionForm.querySelector("#selectAllQuestions");
  const archiveButton = document.querySelector("[data-confirm-archive]");

  const syncSelection = () => {
    const selectedCount = rows.filter((checkbox) => checkbox.checked).length;
    if (archiveButton) {
      archiveButton.disabled = selectedCount === 0;
    }
    if (selectAll) {
      selectAll.checked = rows.length > 0 && selectedCount === rows.length;
      selectAll.indeterminate = selectedCount > 0 && selectedCount < rows.length;
    }
  };

  rows.forEach((checkbox) => checkbox.addEventListener("change", syncSelection));
  if (selectAll) {
    selectAll.addEventListener("change", () => {
      rows.forEach((checkbox) => {
        checkbox.checked = selectAll.checked;
      });
      syncSelection();
    });
  }
  if (selectionForm) {
    selectionForm.addEventListener("submit", (event) => {
      const selectedCount = rows.filter((checkbox) => checkbox.checked).length;
      if (selectedCount && !window.confirm(`Archive ${selectedCount} selected question(s)?`)) {
        event.preventDefault();
      }
    });
  }
  syncSelection();
}
