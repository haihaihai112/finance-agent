"use strict";

// Tests open the same disclosures a user would need in the minimal interface.
// These helpers reveal controls; they never submit or confirm on the user's behalf.
async function reveal(page, selector) {
  const control = page.locator(selector).first();
  await control.evaluate(element => {
    const drawer = element.closest("dialog");
    for (const open of document.querySelectorAll("dialog[open]")) {
      if (open !== drawer) open.close();
    }
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
    if (drawer && !drawer.open) drawer.showModal();
  });
  return control;
}
async function click(page, selector) { await (await reveal(page, selector)).click(); }
async function fill(page, selector, value) { await (await reveal(page, selector)).fill(value); }

module.exports = {reveal, click, fill};
