/**
 * In-brief links ("See what was set aside"): scroll only the brief to the section, open its folded
 * details, and stop just below the sticky summary bar. A plain #anchor jump also scrolls the
 * page's outer frame, which shifted the whole screen up and left a blank band (D-84).
 */
export function jumpToSection(event: React.MouseEvent<HTMLAnchorElement>, id: string): void {
  const target = document.getElementById(id);
  if (!target) return;
  event.preventDefault();
  const details = target.querySelector("details");
  if (details) details.open = true;
  const sticky = target.closest("[data-testid=brief-scroll]")?.querySelector<HTMLElement>("[data-sticky-bar]");
  target.style.scrollMarginTop = `${(sticky?.offsetHeight ?? 0) + 16}px`;
  target.scrollIntoView({ behavior: "smooth", block: "start" });
}
