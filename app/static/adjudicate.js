/* ==========================================================================
   Keyboard driving for the adjudication tool.

   Two keystrokes per group is the target at §4: one to arm a verdict, one to
   pick the rationale. That is where the hours go.

   ONE FILE, TWO PASSES. The §4 inclusion page and the §5 terminal-state page
   are the same machine: arm a value, pick or type a rationale, commit. So
   nothing here is hard-coded to either. The keys come from `data-key` on the
   option labels, the payload comes from the form itself, and the two places
   they differ are declared in the page's own JSON block:

     verdict_field     the field name the armed value is posted under
     commit_on_preset  §4 commits on the preset number, because a §4 ruling is
                       complete at that point. §5 does NOT: a terminal state
                       usually needs a date, and often a name or a boolean, so
                       the commit is always a second, separate key.

   What this file does NOT do, and must never do: arm a value on its own, pick
   a rationale on its own, or submit anything the reviewer did not press a key
   for on that specific group. There is no bulk action here, because there is
   no bulk action anywhere in this tool. Every POST it sends is one human
   ruling on one group that was on the screen when the key was pressed.

   An option the server has refused — METHOD.md §6 forbids DISCONTINUED where
   the four-period absence test is not met — is rendered disabled and carries
   its reason in `data-blocked`. This file refuses to arm it and shows that
   reason. The server refuses it again regardless; this is the courtesy, not
   the guard.

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

  var VERDICT_FIELD = data.verdict_field || "verdict";
  var COMMIT_ON_PRESET = data.commit_on_preset !== false;
  var REVIEWER_KEY = "underwritten.adjudicate.reviewer";
  var draftKey =
    "underwritten.adjudicate." + (data.pass || "inclusion") + ".draft." +
    (data.group_id || "");
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

  function armPrompt() {
    var names = [];
    each(verdictLabels(), function (label) {
      var key = label.getAttribute("data-key");
      var value = label.getAttribute("data-verdict");
      if (key && value) names.push(key + " " + value);
    });
    return "Arm a value first: " + names.join(", ") + ".";
  }

  /* --- arming a value -------------------------------------------------- */

  function verdictLabels() {
    return form.querySelectorAll(".adj-verdict");
  }

  function blockedReason(label) {
    if (!label) return "";
    if (label.getAttribute("data-blocked")) return label.getAttribute("data-blocked");
    return label.className.indexOf("is-blocked") >= 0
      ? "That state is not available on this metric."
      : "";
  }

  /* Fieldsets that only apply to one armed value — the rename check for
     DISCONTINUED, the new name for RENAMED, the substantive call for
     REDEFINED, the §7.4 benign label for either of the two states that can
     hide one. Visible to everyone when this file is absent. */
  function updateConditional() {
    each(document.querySelectorAll("[data-show-for]"), function (element) {
      var applies = (element.getAttribute("data-show-for") || "").split(/\s+/);
      element.hidden = !armed || applies.indexOf(armed) < 0;
    });
  }

  function arm(verdict) {
    var target = verdict
      ? form.querySelector('.adj-verdict[data-verdict="' + verdict + '"]')
      : null;
    var refusal = verdict ? blockedReason(target) : "";
    if (refusal) {
      showError(refusal);
      return;
    }

    armed = verdict || "";
    each(verdictLabels(), function (label) {
      var mine = label.getAttribute("data-verdict") === armed;
      label.classList.toggle("is-armed", mine);
      var radio = label.querySelector("input[type=radio]");
      if (radio && !radio.disabled) radio.checked = mine;
    });

    var showing = false;
    each(presetHost.querySelectorAll(".adj-presets__set"), function (set) {
      var mine = set.getAttribute("data-for") === armed;
      set.hidden = !mine;
      if (mine) showing = true;
    });
    var idle = presetHost.querySelector(".adj-presets__idle");
    if (idle) idle.hidden = showing;

    updateConditional();
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

  /* The body is the form's own fields, so a field added to the template
     reaches the server without a change here — and so the JavaScript path and
     the no-JavaScript path post the same thing. The armed value, the initials
     and the rationale are set explicitly afterwards because those three are
     what this file is driving. */
  function body(who, why) {
    var out = {};
    try {
      var entries = new FormData(form);
      entries.forEach(function (value, key) {
        if (typeof value === "string") out[key] = value;
      });
    } catch (err) {
      /* No FormData: the three fields below still carry the ruling. */
    }
    out[VERDICT_FIELD] = armed;
    out.reviewer = who;
    out.rationale = why;
    out.rationale_source = source ? source.value : "free_text";
    return out;
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
      showError(armPrompt());
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
      body: JSON.stringify(body(who, why))
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (parsed) {
            return { ok: response.ok, body: parsed };
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

  function choosePresetAndMaybeCommit(button) {
    choosePreset(button);
    if (COMMIT_ON_PRESET) commit();
  }

  /* --- wiring --------------------------------------------------------- */

  each(verdictLabels(), function (label) {
    label.addEventListener("click", function () {
      arm(label.getAttribute("data-verdict"));
    });
  });

  each(presetHost.querySelectorAll(".adj-preset"), function (button) {
    button.addEventListener("click", function () {
      choosePresetAndMaybeCommit(button);
    });
  });

  /* A dated fact the tool already knows, copied into a field by a human
     click. It fills a box; it never rules, and it never fills one by itself. */
  each(document.querySelectorAll("[data-fill]"), function (button) {
    button.addEventListener("click", function () {
      var target = document.getElementById(button.getAttribute("data-fill"));
      if (target) target.value = button.getAttribute("data-value") || "";
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

  /* Built from the page rather than hard-coded, so §4's i/x/n and §5's
     a/e/m/o/d/n are the same three lines of code. */
  var KEY_TO_VERDICT = {};
  each(verdictLabels(), function (label) {
    var key = (label.getAttribute("data-key") || "").toLowerCase();
    var value = label.getAttribute("data-verdict");
    if (key && value) KEY_TO_VERDICT[key] = value;
  });

  function typing(target) {
    if (!target) return false;
    var tag = (target.tagName || "").toLowerCase();
    return (
      tag === "input" ||
      tag === "textarea" ||
      tag === "select" ||
      target.isContentEditable
    );
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
    var lower = (key || "").toLowerCase();

    if (KEY_TO_VERDICT[lower]) {
      event.preventDefault();
      arm(KEY_TO_VERDICT[lower]);
    } else if (key >= "1" && key <= "9") {
      var button = presetByNumber(parseInt(key, 10));
      if (button) {
        event.preventDefault();
        choosePresetAndMaybeCommit(button);
      } else if (!armed) {
        showError(armPrompt());
      }
    } else if (key === "Enter") {
      event.preventDefault();
      commit();
    } else if (lower === "t") {
      event.preventDefault();
      rationale.focus();
    } else if (lower === "s") {
      event.preventDefault();
      window.location.assign(data.next_url || data.done_url);
    } else if (lower === "b") {
      if (data.prev_url) {
        event.preventDefault();
        window.location.assign(data.prev_url);
      }
    } else if (lower === "r") {
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
