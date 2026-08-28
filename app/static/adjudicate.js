/* ==========================================================================
   Keyboard driving for the adjudication tool.

   Two keystrokes per group is the target: one to arm a verdict, one to pick
   the rationale. That is where the hours go.

   What this file does NOT do, and must never do: arm a verdict on its own,
   pick a rationale on its own, or submit anything the reviewer did not press a
   key for on that specific group. There is no bulk action here, because there
   is no bulk action anywhere in this tool. Every POST it sends is one human
   ruling on one group that was on the screen when the key was pressed.

   The page works with this file absent: the form is a real form, the buttons
   are real buttons, and the server accepts a plain urlencoded post.
   ========================================================================== */

(function () {
  "use strict";

  var node = document.getElementById("adj-data");
  if (!node) return;

  var data;
  try {
    data = JSON.parse(node.textContent || "{}");
  } catch (err) {
    return;
  }

  var form = document.getElementById("adj-form");
  var rationale = document.getElementById("adj-rationale");
  var reviewer = document.getElementById("adj-reviewer");
  var source = document.getElementById("adj-source");
  var errorBox = document.getElementById("adj-error");
  var presetHost = document.getElementById("adj-presets");
  var keysBox = document.getElementById("adj-keys");
  if (!form || !rationale || !presetHost) return;

  var REVIEWER_KEY = "underwritten.adjudicate.reviewer";
  var draftKey = "underwritten.adjudicate.draft." + (data.group_id || "");
  var armed = "";
  var sending = false;

  function each(list, fn) {
    Array.prototype.forEach.call(list, fn);
  }

  /* --- storage is a convenience; the ledger is the real save ---------- */

  function readStore(store, key) {
    try {
      return store.getItem(key) || "";
    } catch (err) {
      return "";
    }
  }

  function writeStore(store, key, value) {
    try {
      if (value) store.setItem(key, value);
      else store.removeItem(key);
    } catch (err) {
      /* private mode or blocked site data: not fatal, and never silent loss */
    }
  }

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message || "";
    errorBox.hidden = !message;
  }

  /* --- arming a verdict ---------------------------------------------- */

  function verdictLabels() {
    return form.querySelectorAll(".adj-verdict");
  }

  function arm(verdict) {
    armed = verdict || "";
    each(verdictLabels(), function (label) {
      var mine = label.getAttribute("data-verdict") === armed;
      label.classList.toggle("is-armed", mine);
      var radio = label.querySelector("input[type=radio]");
      if (radio) radio.checked = mine;
    });

    var showing = false;
    each(presetHost.querySelectorAll(".adj-presets__set"), function (set) {
      var mine = set.getAttribute("data-for") === armed;
      set.hidden = !mine;
      if (mine) showing = true;
    });
    var idle = presetHost.querySelector(".adj-presets__idle");
    if (idle) idle.hidden = showing;

    showError("");
  }

  /* --- rationale ------------------------------------------------------ */

  function clearChosen() {
    each(presetHost.querySelectorAll(".adj-preset"), function (button) {
      button.classList.remove("is-chosen");
    });
  }

  function choosePreset(button) {
    if (!button) return;
    clearChosen();
    button.classList.add("is-chosen");
    rationale.value = button.getAttribute("data-text") || "";
    if (source) source.value = "preset:" + (button.getAttribute("data-n") || "0");
    writeStore(window.sessionStorage, draftKey, rationale.value);
  }

  function presetByNumber(n) {
    if (!armed) return null;
    var set = presetHost.querySelector('.adj-presets__set[data-for="' + armed + '"]');
    if (!set) return null;
    return set.querySelector('.adj-preset[data-n="' + n + '"]');
  }

  /* --- commit --------------------------------------------------------- */

  function reviewerValue() {
    return (reviewer && reviewer.value ? reviewer.value : "").trim();
  }

  function commit() {
    if (sending) return;

    var who = reviewerValue();
    if (!who) {
      showError("Reviewer initials are required — a row without initials is not a signed ruling.");
      if (reviewer) {
        reviewer.classList.add("is-missing");
        reviewer.focus();
      }
      return;
    }
    if (!armed) {
      showError("Arm a verdict first: i to include, x to exclude, n for can’t-tell.");
      return;
    }
    var why = (rationale.value || "").trim();
    if (!why) {
      showError("A rationale is required. Pick a preset by number, or press t and type one.");
      rationale.focus();
      return;
    }

    sending = true;
    showError("");

    fetch(data.post_url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        verdict: armed,
        reviewer: who,
        rationale: why,
        rationale_source: source ? source.value : "free_text"
      })
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (body) {
            return { ok: response.ok, body: body };
          });
      })
      .then(function (result) {
        sending = false;
        if (!result.ok || !result.body || !result.body.ok) {
          showError(
            (result.body && result.body.error) ||
              "The ruling was not written. Nothing has changed on disk."
          );
          return;
        }
        writeStore(window.localStorage, REVIEWER_KEY, who);
        writeStore(window.sessionStorage, draftKey, "");
        window.location.assign(result.body.next_url || data.next_url || data.done_url);
      })
      .catch(function () {
        sending = false;
        showError("Could not reach the local server. Nothing was written — your text is still here.");
      });
  }

  function chooseAndCommit(button) {
    choosePreset(button);
    commit();
  }

  /* --- wiring --------------------------------------------------------- */

  each(verdictLabels(), function (label) {
    label.addEventListener("click", function () {
      arm(label.getAttribute("data-verdict"));
    });
  });

  each(presetHost.querySelectorAll(".adj-preset"), function (button) {
    button.addEventListener("click", function () {
      chooseAndCommit(button);
    });
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    commit();
  });

  if (reviewer) {
    var stored = readStore(window.localStorage, REVIEWER_KEY);
    if (!reviewer.value && stored) reviewer.value = stored;
    reviewer.addEventListener("input", function () {
      reviewer.classList.remove("is-missing");
      writeStore(window.localStorage, REVIEWER_KEY, reviewer.value.trim());
    });
  }

  var draft = readStore(window.sessionStorage, draftKey);
  if (draft && !rationale.value) rationale.value = draft;
  rationale.addEventListener("input", function () {
    if (source) source.value = "free_text";
    clearChosen();
    writeStore(window.sessionStorage, draftKey, rationale.value);
  });

  /* --- keys ------------------------------------------------------------ */

  function typing(target) {
    if (!target) return false;
    var tag = (target.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || target.isContentEditable;
  }

  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey) {
      if (event.key === "Enter") {
        event.preventDefault();
        commit();
      }
      return;
    }
    if (event.altKey) return;

    if (typing(event.target)) {
      if (event.key === "Escape") event.target.blur();
      return;
    }

    var key = event.key;

    if (key === "i" || key === "I") {
      event.preventDefault();
      arm("INCLUDE");
    } else if (key === "x" || key === "X") {
      event.preventDefault();
      arm("EXCLUDE");
    } else if (key === "n" || key === "N") {
      event.preventDefault();
      arm("NOT_DETERMINABLE");
    } else if (key >= "1" && key <= "9") {
      var button = presetByNumber(parseInt(key, 10));
      if (button) {
        event.preventDefault();
        chooseAndCommit(button);
      } else if (!armed) {
        showError("Arm a verdict first: i to include, x to exclude, n for can’t-tell.");
      }
    } else if (key === "Enter") {
      event.preventDefault();
      commit();
    } else if (key === "t" || key === "T") {
      event.preventDefault();
      rationale.focus();
    } else if (key === "s" || key === "S") {
      event.preventDefault();
      window.location.assign(data.next_url || data.done_url);
    } else if (key === "b" || key === "B") {
      if (data.prev_url) {
        event.preventDefault();
        window.location.assign(data.prev_url);
      }
    } else if (key === "r" || key === "R") {
      if (reviewer) {
        event.preventDefault();
        reviewer.focus();
        reviewer.select();
      }
    } else if (key === "?") {
      if (keysBox) {
        event.preventDefault();
        keysBox.open = !keysBox.open;
      }
    } else if (key === "Escape") {
      arm("");
    }
  });

  /* Nothing is armed on load. The reviewer arms it, on every group. */
  arm("");
})();
