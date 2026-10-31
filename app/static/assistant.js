/* The assistant panel (build plan Step 5), shown only in the assisted part.
 *
 * A question is posted together with the form's current JSON; the server builds the structured
 * prompt, asks the model and logs both. Answers are formatted by markdown.js, never inserted as HTML.
 */
(function () {
  "use strict";

  var panel = document.getElementById("assistant-panel");
  if (!panel) return;

  var url = panel.dataset.askUrl;
  var list = panel.querySelector("[data-messages]");
  var form = document.getElementById("ask-form");
  var box = document.getElementById("ask-box");
  var button = form.querySelector("button");

  function add(role, text) {
    var item = document.createElement("div");
    item.className = "msg msg-" + role;
    var who = document.createElement("div");
    who.className = "msg-who";
    who.textContent = role === "user" ? "You" : "Assistant";
    var body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = text;
    item.appendChild(who);
    item.appendChild(body);
    list.appendChild(item);
    list.scrollTop = list.scrollHeight;
    return body;
  }

  function formatted(body, text) {
    body.classList.add("msg-formatted");
    window.cepMarkdown(body, text);
  }

  // Answers from earlier in this task come back as plain text when the page reloads.
  list.querySelectorAll(".msg-assistant .msg-body").forEach(function (body) {
    formatted(body, body.textContent);
  });

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    var question = box.value.trim();
    if (!question || button.disabled) return;
    add("user", question);
    box.value = "";
    button.disabled = true;
    var answer = add("assistant", "Thinking...");
    try {
      if (window.cepEvents) await window.cepEvents.flush();
      var response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ question: question, config: window.cepForm.getConfig() })
      });
      var data = await response.json();
      if (response.ok) {
        formatted(answer, data.answer);
      } else {
        answer.textContent = data.error || "The assistant could not answer.";
        answer.parentNode.classList.add("msg-error");
      }
    } catch (e) {
      answer.textContent = "Could not reach the assistant. Please tell the researcher.";
      answer.parentNode.classList.add("msg-error");
    } finally {
      button.disabled = false;
      box.focus();
      list.scrollTop = list.scrollHeight;
    }
  });

  box.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  list.scrollTop = list.scrollHeight;
})();
