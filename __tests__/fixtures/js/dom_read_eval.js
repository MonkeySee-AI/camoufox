() => {
  const root = document.querySelector("[data-root]");
  const allItems = document.querySelectorAll(".item");
  const target = document.querySelector("#target");
  const style = window.getComputedStyle(target);
  const rect = target.getBoundingClientRect();
  return {
    itemCount: allItems.length,
    id: target.getAttribute("id"),
    closestRoot: target.closest("[data-root]") === root,
    display: style.display,
    width: rect.width,
  };
}
