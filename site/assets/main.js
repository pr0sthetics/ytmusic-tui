(function () {
  "use strict";

  var toast = document.querySelector(".copy-toast");
  var toastTimer = 0;

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("is-visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toast.classList.remove("is-visible");
    }, 2600);
  }

  function commandFor(button) {
    var targetId = button.getAttribute("data-copy-target");
    if (targetId) {
      var target = document.getElementById(targetId);
      return target ? target.textContent.trim() : "";
    }
    var block = button.closest(".code-block, .cmdline, .synopsis");
    if (!block) return "";
    var code = block.querySelector("code");
    return code ? code.textContent.trim() : "";
  }

  function fallbackCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (error) {
      copied = false;
    }
    document.body.removeChild(area);
    return copied;
  }

  function selectCommand(button) {
    var block = button.closest(".code-block, .cmdline, .synopsis");
    var code = block && block.querySelector("code");
    if (!code) return;

    var selection = window.getSelection();
    if (!selection) return;

    var range = document.createRange();
    range.selectNodeContents(code);
    selection.removeAllRanges();
    selection.addRange(range);

    var pre = code.closest("pre");
    if (pre) pre.focus();
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(
        function () { return true; },
        function () { return fallbackCopy(text); }
      );
    }
    return Promise.resolve(fallbackCopy(text));
  }

  var menu = document.querySelector(".mmenu");
  if (menu) {
    var menuSummary = menu.querySelector("summary");

    menu.addEventListener("click", function (event) {
      var link = event.target.closest ? event.target.closest("a") : null;
      if (link) menu.open = false;
    });

    document.addEventListener("click", function (event) {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menu.open) {
        menu.open = false;
        if (menuSummary) menuSummary.focus();
      }
    });
  }

  var buttons = Array.prototype.slice.call(document.querySelectorAll(".copy"));

  buttons.forEach(function (button) {
    button.hidden = false;
    var originalText = button.textContent;
    var originalLabel = button.getAttribute("aria-label") || "Copy command";
    var resetTimer = 0;

    button.addEventListener("click", function () {
      var text = commandFor(button);
      if (!text) {
        showToast("Nothing to copy here.");
        return;
      }

      var label = button.getAttribute("data-copy-label") || "command";

      copyText(text).then(function (ok) {
        if (!ok) {
          selectCommand(button);
          showToast("Copy failed. The command is selected; copy it manually.");
          return;
        }
        button.textContent = "Copied";
        button.setAttribute("aria-label", "Copied " + label);
        showToast("Copied " + label + ".");
        window.clearTimeout(resetTimer);
        resetTimer = window.setTimeout(function () {
          button.textContent = originalText;
          button.setAttribute("aria-label", originalLabel);
        }, 1600);
      });
    });
  });
})();
