/* Two small enhancements. The site is fully readable and fully navigable with
   this file blocked; nothing here is load-bearing.

   1. Citation chips reveal their card on hover and on keyboard focus, which is
      pure CSS. On a touch device there is no hover, and a first tap would
      navigate to sec.gov before the reader ever saw the verbatim sentence. So
      on hover-less pointers the first tap opens the card and the second follows
      the link.

   2. The ledger filters submit on change. The form works without this - the
      Apply button is a real submit button and the page is server-rendered. */

(function () {
  "use strict";

  var hoverless =
    window.matchMedia && window.matchMedia("(hover: none)").matches;

  function closeAll(except) {
    var open = document.querySelectorAll(".cite.is-open");
    for (var i = 0; i < open.length; i++) {
      if (open[i] !== except) open[i].classList.remove("is-open");
    }
  }

  if (hoverless) {
    document.addEventListener("click", function (event) {
      var value = event.target.closest && event.target.closest(".cite__value");
      if (!value) {
        closeAll(null);
        return;
      }
      var chip = value.closest(".cite");
      if (!chip || chip.classList.contains("is-open")) return; // second tap navigates
      event.preventDefault();
      closeAll(chip);
      chip.classList.add("is-open");
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeAll(null);
  });

  var forms = document.querySelectorAll("form[data-autosubmit]");
  for (var i = 0; i < forms.length; i++) {
    forms[i].addEventListener("change", function (event) {
      if (event.target.tagName === "SELECT") this.submit();
    });
  }
})();
