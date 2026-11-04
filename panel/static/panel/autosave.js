// Save a `form[data-autosave]` when one of its inputs changes, without a page reload.
// The form posts to its normal endpoint; the Save button stays for browsers without
// scripts and is hidden here. A 400 answer is the page with the form's errors.
(function () {
  "use strict";

  var DELAY_MS = 400;

  function setStatus(form, text, isError) {
    var status = form.querySelector(".save-status");
    status.textContent = text;
    status.classList.toggle("is-error", isError);
  }

  function errorsFrom(html, action) {
    var page = new DOMParser().parseFromString(html, "text/html");
    var form = page.querySelector('form[action="' + action + '"]');
    var list = form && form.querySelector(".errorlist");
    return list ? list.textContent.trim() : "Not saved. Try again.";
  }

  function save(form) {
    setStatus(form, "Saving…", false);
    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
    })
      .then(function (response) {
        if (response.ok) {
          setStatus(form, "Saved", false);
          return null;
        }
        return response.text().then(function (html) {
          setStatus(form, errorsFrom(html, form.getAttribute("action")), true);
        });
      })
      .catch(function () {
        setStatus(form, "Not saved. Check your connection.", true);
      });
  }

  document.querySelectorAll("form[data-autosave]").forEach(function (form) {
    var timer = null;
    form.querySelectorAll("[data-autosave-fallback]").forEach(function (button) {
      button.hidden = true;
    });
    form.addEventListener("change", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        save(form);
      }, DELAY_MS);
    });
  });
})();
