/* Dog-Food portal — progressive enhancement only.
 *
 * Every page is rendered by Flask from the database, and every form is a real
 * POST that works with JavaScript disabled. Nothing here holds data: this file
 * only makes the server-rendered pages nicer to use.
 */

(function () {
  "use strict";

  /* Filters that apply themselves: selects and checkboxes straight away,
     text search after a short pause so typing does not fire a request per
     character. */
  function initAutoSubmit() {
    document.querySelectorAll("form[data-autosubmit]").forEach(function (form) {
      form.querySelectorAll("select, input[type=checkbox]").forEach(function (field) {
        field.addEventListener("change", function () {
          form.submit();
        });
      });
      form.querySelectorAll("input[type=search], input[type=text]").forEach(function (field) {
        let timer = null;
        field.addEventListener("input", function () {
          clearTimeout(timer);
          timer = setTimeout(function () {
            form.submit();
          }, 400);
        });
        field.addEventListener("keydown", function (event) {
          if (event.key === "Enter") {
            event.preventDefault();
            clearTimeout(timer);
            form.submit();
          }
        });
      });
    });
  }

  /* Copy an invite link without selecting it by hand. */
  function initCopyButtons() {
    document.querySelectorAll("button[data-copy]").forEach(function (button) {
      button.addEventListener("click", async function () {
        const target = document.querySelector(button.dataset.copy);
        if (!target) return;
        const label = button.textContent;
        try {
          if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(target.value);
          } else {
            target.select();
            document.execCommand("copy");
            window.getSelection().removeAllRanges();
          }
          button.textContent = "Copied";
        } catch (error) {
          button.textContent = "Select and copy";
        }
        setTimeout(function () {
          button.textContent = label;
        }, 1800);
      });
    });
  }

  /* Destructive buttons ask first. */
  function initConfirm() {
    document.querySelectorAll("[data-confirm]").forEach(function (element) {
      const form = element.closest("form");
      if (!form) return;
      form.addEventListener("submit", function (event) {
        if (!window.confirm(element.dataset.confirm)) {
          event.preventDefault();
        }
      });
    });
  }

  /* Character counters under textareas. */
  function initCounters() {
    document.querySelectorAll("[data-counter]").forEach(function (field) {
      const output = document.getElementById(field.dataset.counter);
      if (!output) return;
      const update = function () {
        output.textContent = String(field.value.length);
      };
      field.addEventListener("input", update);
      update();
    });
  }

  /* Repeatable rows: tracks and prizes on the event form. */
  function initRepeatable() {
    document.querySelectorAll("form[data-repeatable]").forEach(function (form) {
      form.querySelectorAll("[data-repeat-group]").forEach(function (group) {
        const template = group.querySelector("[data-repeat-template]");
        if (!template) return;

        const addRow = function () {
          const row = template.cloneNode(true);
          row.removeAttribute("data-repeat-template");
          row.querySelectorAll("input").forEach(function (input) {
            input.value = "";
          });
          template.after(row);
          const first = row.querySelector("input");
          if (first) first.focus();
        };

        group.querySelectorAll("[data-repeat-add]").forEach(function (button) {
          button.addEventListener("click", addRow);
        });

        group.addEventListener("click", function (event) {
          const remove = event.target.closest("[data-repeat-remove]");
          if (!remove) return;
          const row = remove.closest("[data-repeat-template]") ||
            remove.parentElement;
          if (row && row.parentElement) row.parentElement.removeChild(row);
        });
      });
    });
  }

  /* Flash messages fade out on their own. */
  function initFlashes() {
    document.querySelectorAll(".flash").forEach(function (flash) {
      setTimeout(function () {
        flash.style.transition = "opacity .4s ease";
        flash.style.opacity = "0";
        setTimeout(function () {
          flash.remove();
        }, 400);
      }, 6000);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initAutoSubmit();
    initCopyButtons();
    initConfirm();
    initCounters();
    initRepeatable();
    initFlashes();
  });
})();
