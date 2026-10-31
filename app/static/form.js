/* Builds the configuration JSON from the form (build plan Step 4).
 *
 * The JSON shape is schema/config.schema.json. The form starts from the task's initial_config,
 * and every edit goes through change(path, value). That updates the JSON and fires a
 * "configchange" event on document with { path, old, new, config }:
 *   - Step 5 (assistant panel) reads the current JSON with window.cepForm.getConfig();
 *   - Step 6 (event log) listens for configchange to record every field change.
 *
 * The checks in this file are form-level only (empty or malformed fields). They never say
 * whether a value is secure: participants must not see validator findings.
 */
(function () {
  "use strict";

  var form = document.getElementById("config-form");
  if (!form) return;

  var initial = JSON.parse(document.getElementById("initial-config").textContent);
  var options = JSON.parse(document.getElementById("form-options").textContent);
  var resource = form.dataset.resource;
  var preview = form.dataset.preview === "true";
  var draftKey = form.dataset.draftKey || "";
  var errorsBox = document.getElementById("form-errors");
  var submitButton = document.getElementById("submit-button");
  var master = form.querySelector("[data-bpa-master]");
  var loggingTarget = form.querySelector("[data-logging-target]");
  var table = form.querySelector("[data-ingress]");
  var addButton = form.querySelector("[data-add-rule]");
  var isDatabase = resource === "database";
  var ruleTypes = options.ingress_types;

  var MAX_RULES = 10;                    // schema: ingress maxItems
  var EXAMPLE_SOURCE = "192.0.2.10/32";  // documentation range that no task uses
  var EXAMPLE_ADDRESS = EXAMPLE_SOURCE.split("/")[0];
  var ANYONE = "0.0.0.0/0";              // shown as "Anyone on the internet"; the JSON keeps the real value
  var IPV4_CIDR = /^\d{1,3}(\.\d{1,3}){3}\/\d{1,2}$/;
  var IPV6_CIDR = /^[0-9A-Fa-f:]*:[0-9A-Fa-f:]*\/\d{1,3}$/;

  var restoredDraft = restoreDraft();
  var config = restoredDraft || clone(initial);

  // ------------------------------------------------------------ JSON helpers

  function clone(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function same(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function tokens(path) {
    return path.match(/[^.[\]]+/g).map(function (t) { return /^\d+$/.test(t) ? Number(t) : t; });
  }

  function getPath(obj, path) {
    return tokens(path).reduce(function (node, key) {
      return node === null || node === undefined ? undefined : node[key];
    }, obj);
  }

  function setPath(obj, path, value) {
    var keys = tokens(path);
    var last = keys.pop();
    var parent = keys.reduce(function (node, key) { return node[key]; }, obj);
    parent[last] = value;
  }

  // The single way the configuration changes.
  function change(path, value) {
    var old = clone(getPath(config, path));
    if (same(old, value)) return;
    setPath(config, path, clone(value));
    saveDraft();
    refreshDerived();
    document.dispatchEvent(new CustomEvent("configchange", {
      detail: { path: path, old: old, new: clone(value), config: clone(config) }
    }));
  }

  window.cepForm = {
    resource: resource,
    restored: restoredDraft !== null,
    getConfig: function () { return clone(config); }
  };

  // ------------------------------------------------------------ draft (survives a page reload)

  function restoreDraft() {
    if (!draftKey) return null;
    try {
      var saved = JSON.parse(window.sessionStorage.getItem(draftKey));
      return saved && saved.resource === initial.resource ? saved : null;
    } catch (e) {
      return null;
    }
  }

  function saveDraft() {
    if (!draftKey) return;
    try { window.sessionStorage.setItem(draftKey, JSON.stringify(config)); } catch (e) { /* storage blocked */ }
  }

  function clearDraft() {
    if (!draftKey) return;
    try { window.sessionStorage.removeItem(draftKey); } catch (e) { /* storage blocked */ }
  }

  // ------------------------------------------------------------ simple controls (data-path)

  function simpleControls() {
    return Array.prototype.filter.call(form.querySelectorAll("[data-path]"), function (el) {
      return !el.closest("[data-ingress]");
    });
  }

  function writeControl(el, value) {
    var kind = el.dataset.kind;
    if (kind === "bool") {
      el.checked = value === true;
    } else if (kind === "radio" || kind === "bool-radio") {
      el.querySelectorAll("input[type=radio]").forEach(function (radio) {
        radio.checked = value !== null && value !== undefined && radio.value === String(value);
      });
    } else {
      el.value = value === null || value === undefined ? "" : String(value);
    }
  }

  function readControl(el, target) {
    switch (el.dataset.kind) {
      case "bool": return el.checked;
      case "int": {
        var n = Number.parseInt(el.value, 10);
        return Number.isNaN(n) ? null : n;
      }
      case "radio": return target.value;
      case "bool-radio": return target.value === "true";
      default: return el.value.trim();
    }
  }

  function hydrateSimple() {
    simpleControls().forEach(function (el) { writeControl(el, getPath(config, el.dataset.path)); });
  }

  function setAllPublicAccessBlocks(on) {
    var next = {};
    Object.keys(config.block_public_access).forEach(function (key) { next[key] = on; });
    change("block_public_access", next);
    hydrateSimple();
  }

  form.addEventListener("change", function (event) {
    var target = event.target;
    if (target.matches("[data-bpa-master]")) {
      setAllPublicAccessBlocks(target.checked);
      return;
    }
    if (target.closest("[data-ingress]")) return;  // handled by the rules table below
    var el = target.closest("[data-path]");
    if (!el) return;
    var value = readControl(el, target);
    if (el.dataset.kind === "text") el.value = value;
    change(el.dataset.path, value);
  });

  // Values that depend on others: the "Block all" box, the log target field, the JSON preview.
  function refreshDerived() {
    if (master) {
      var values = Object.keys(config.block_public_access).map(function (k) { return config.block_public_access[k]; });
      master.checked = values.every(Boolean);
      master.indeterminate = !master.checked && values.some(Boolean);
    }
    if (loggingTarget) loggingTarget.disabled = config.access_logging.enabled !== true;
    if (addButton) addButton.disabled = config.ingress.length >= MAX_RULES;
    var pre = document.getElementById("json-preview");
    if (pre) pre.textContent = JSON.stringify(config, null, 2);
  }

  // ------------------------------------------------------------ inbound rules table

  function typeKey(port) {
    var preset = ruleTypes.filter(function (t) { return t.port === port; })[0];
    return preset ? preset.key : "custom";
  }

  function normaliseSource(value) {
    var v = value.trim();
    if (/^\d{1,3}(\.\d{1,3}){3}$/.test(v)) return v + "/32";
    if (v.indexOf(":") !== -1 && v.indexOf("/") === -1) return v + "/128";
    return v;
  }

  // A single address is shown without its /32 (or /128); the JSON keeps the full form.
  function displaySource(source) {
    if (/^\d{1,3}(\.\d{1,3}){3}\/32$/.test(source)) return source.slice(0, -3);
    if (source.indexOf(":") !== -1 && /\/128$/.test(source)) return source.slice(0, -4);
    return source;
  }

  function cell(child, className) {
    var td = document.createElement("td");
    if (className) td.className = className;
    if (typeof child === "string") td.textContent = child; else td.appendChild(child);
    return td;
  }

  function ruleRow(rule, index) {
    var n = index + 1;
    var row = document.createElement("tr");
    row.dataset.index = String(index);

    var typeCell;
    if (isDatabase) {
      typeCell = cell("Connecting to the database", "rule-type-fixed");
    } else {
      var select = document.createElement("select");
      select.dataset.field = "type";
      select.setAttribute("aria-label", "Row " + n + ": what it is for");
      ruleTypes.forEach(function (t) {
        var option = document.createElement("option");
        option.value = t.key;
        option.textContent = t.label;
        select.appendChild(option);
      });
      select.value = typeKey(rule.port);
      typeCell = cell(select);
    }

    // Only "Other" asks for a number; for the rest the number stays out of sight.
    var port = document.createElement("input");
    port.type = "number";
    port.min = "1";
    port.max = "65535";
    port.dataset.field = "port";
    port.setAttribute("aria-label", "Row " + n + ": service number");
    port.placeholder = "Service number";
    port.value = rule.port === null || rule.port === undefined ? "" : String(rule.port);
    port.readOnly = isDatabase || typeKey(rule.port) !== "custom";
    port.hidden = port.readOnly;
    typeCell.appendChild(port);

    var who = document.createElement("select");
    who.dataset.field = "who";
    who.setAttribute("aria-label", "Row " + n + ": who can connect");
    [["anyone", "Anyone on the internet"], ["address", "Only this address"]].forEach(function (choice) {
      var option = document.createElement("option");
      option.value = choice[0];
      option.textContent = choice[1];
      who.appendChild(option);
    });
    who.value = rule.source === ANYONE ? "anyone" : "address";

    var source = document.createElement("input");
    source.type = "text";
    source.dataset.field = "source";
    source.placeholder = "for example " + EXAMPLE_ADDRESS;
    source.autocomplete = "off";
    source.spellcheck = false;
    source.setAttribute("aria-label", "Row " + n + ": address");
    source.value = rule.source === ANYONE ? "" : displaySource(rule.source);
    source.hidden = rule.source === ANYONE;

    var whoCell = cell(who);
    whoCell.appendChild(source);

    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn-link";
    remove.dataset.field = "remove";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", "Remove row " + n);

    row.appendChild(typeCell);
    row.appendChild(whoCell);
    row.appendChild(cell(remove));
    return row;
  }

  function renderRules() {
    if (!table) return;
    var body = table.tBodies[0];
    body.replaceChildren();
    config.ingress.forEach(function (rule, index) { body.appendChild(ruleRow(rule, index)); });
  }

  function focusRule(index, field) {
    var row = table.querySelector('tr[data-index="' + index + '"]');
    var input = row && row.querySelector('[data-field="' + field + '"]');
    if (input) input.focus();
  }

  if (table) {
    table.addEventListener("change", function (event) {
      var target = event.target;
      var row = target.closest("tr[data-index]");
      if (!row) return;
      var index = Number(row.dataset.index);
      var field = target.dataset.field;
      if (field === "type") {
        var preset = ruleTypes.filter(function (t) { return t.key === target.value; })[0];
        change("ingress[" + index + "].port", preset.port);
        renderRules();
        focusRule(index, preset.port === null ? "port" : "who");
      } else if (field === "port") {
        var n = Number.parseInt(target.value, 10);
        change("ingress[" + index + "].port", Number.isNaN(n) ? null : n);
      } else if (field === "who") {
        change("ingress[" + index + "].source", target.value === "anyone" ? ANYONE : "");
        renderRules();
        focusRule(index, target.value === "anyone" ? "who" : "source");
      } else if (field === "source") {
        var typed = normaliseSource(target.value);
        change("ingress[" + index + "].source", typed);
        if (typed === ANYONE) renderRules(); else target.value = displaySource(typed);
      }
    });

    table.addEventListener("click", function (event) {
      var button = event.target.closest('[data-field="remove"]');
      if (!button) return;
      var index = Number(button.closest("tr").dataset.index);
      change("ingress", config.ingress.filter(function (_, i) { return i !== index; }));
      renderRules();
    });
  }

  if (addButton) {
    addButton.addEventListener("click", function () {
      var newRule = { port: isDatabase ? config.port : null, protocol: "tcp", source: "" };
      change("ingress", config.ingress.concat([newRule]));
      renderRules();
      focusRule(config.ingress.length - 1, isDatabase ? "who" : "type");
    });
  }

  // ------------------------------------------------------------ submit

  // Pressing Enter can submit before a text field fires "change"; read every field once more.
  function flushInputs() {
    simpleControls().forEach(function (el) {
      if (el.dataset.kind === "text") {
        el.value = el.value.trim();
        change(el.dataset.path, el.value);
      }
    });
    if (!table) return;
    table.querySelectorAll("tr[data-index]").forEach(function (row) {
      var index = Number(row.dataset.index);
      if (row.querySelector('[data-field="who"]').value === "address") {
        var source = row.querySelector('[data-field="source"]');
        var typed = normaliseSource(source.value);
        change("ingress[" + index + "].source", typed);
        source.value = displaySource(typed);
      }
      var port = row.querySelector('[data-field="port"]');
      if (!port.readOnly) {
        var n = Number.parseInt(port.value, 10);
        change("ingress[" + index + "].port", Number.isNaN(n) ? null : n);
      }
    });
  }

  function validCidr(value) {
    var parts = value.split("/");
    if (IPV4_CIDR.test(value)) {
      return Number(parts[1]) <= 32 && parts[0].split(".").every(function (o) { return Number(o) <= 255; });
    }
    return IPV6_CIDR.test(value) && Number(parts[1]) <= 128;
  }

  // Empty or malformed fields only. Never a judgement on security.
  function formProblems() {
    var problems = [];
    (config.ingress || []).forEach(function (rule, i) {
      var n = i + 1;
      if (!Number.isInteger(rule.port) || rule.port < 1 || rule.port > 65535) {
        problems.push({ field: [i, "port"], text: "Row " + n + ": enter a service number between 1 and 65535." });
      }
      if (rule.source === "") {
        problems.push({ field: [i, "source"], text: "Row " + n + ": enter an address, for example " + EXAMPLE_ADDRESS + "." });
      } else if (!validCidr(rule.source)) {
        problems.push({ field: [i, "source"], text: "Row " + n + ": \"" + displaySource(rule.source) + "\" is not a valid address. Write it like " + EXAMPLE_ADDRESS + "." });
      }
    });
    if (resource === "storage_bucket") {
      if (config.versioning === null) problems.push({ text: "Earlier versions: choose one option." });
      if (config.encryption === null) problems.push({ text: "Encryption: choose one option." });
      if (config.access_logging.enabled && !config.access_logging.target_bucket) {
        problems.push({ text: "Access records: enter the storage space to save the records in." });
      }
    }
    return problems;
  }

  function element(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function showErrors(problems) {
    form.querySelectorAll(".invalid").forEach(function (el) { el.classList.remove("invalid"); });
    if (!problems.length) {
      errorsBox.hidden = true;
      errorsBox.replaceChildren();
      return;
    }
    var list = element("ul");
    problems.forEach(function (problem) {
      list.appendChild(element("li", "", problem.text));
      if (problem.field && table) {
        var row = table.querySelector('tr[data-index="' + problem.field[0] + '"]');
        var input = row && row.querySelector('[data-field="' + problem.field[1] + '"]');
        if (input) input.classList.add("invalid");
      }
    });
    errorsBox.replaceChildren(element("p", "", "Fix these before submitting:"), list);
    errorsBox.hidden = false;
    errorsBox.scrollIntoView({ block: "nearest" });
  }

  function showResult(result) {
    var box = document.getElementById("preview-result");
    box.replaceChildren();
    if (!result) {
      box.appendChild(element("p", "result-summary", "Practice task: accepted (practice tasks are not scored)."));
      return;
    }
    var verdict = result.task_success ? "Task success" : "Task failed";
    box.appendChild(element("p", "result-summary " + (result.task_success ? "result-pass" : "result-fail"),
      verdict + ": " + result.error_count + " failed rule(s), weighted score " + result.weighted_score +
      " of " + result.max_weighted_score + "."));
    if (result.findings.length) {
      var list = element("ul");
      result.findings.forEach(function (f) {
        list.appendChild(element("li", "", f.rule_id + " (" + f.severity + ", weight " + f.weight + "): " + f.message));
      });
      box.appendChild(list);
    }
    box.appendChild(element("p", "hint", "Passed: " + (result.passed_rules.join(", ") || "none")));
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    flushInputs();
    var problems = formProblems();
    showErrors(problems);
    if (problems.length) return;
    if (!preview && !window.confirm("Submit these settings? You cannot change them afterwards.")) return;

    submitButton.disabled = true;
    var response;
    var data;
    try {
      if (window.cepEvents) await window.cepEvents.flush();  // every change is logged before the submit
      response = await fetch(form.dataset.submitUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(config)
      });
      data = await response.json();
    } catch (e) {
      showErrors([{ text: "Could not reach the server. Tell the researcher, then try again." }]);
      submitButton.disabled = false;
      return;
    }
    if (!response.ok) {
      var messages = data.problems || ["These settings could not be accepted. Please tell the researcher."];
      showErrors(messages.map(function (text) { return { text: text }; }));
      submitButton.disabled = false;
      return;
    }
    if (preview) {
      showResult(data.result);
      submitButton.disabled = false;
      return;
    }
    clearDraft();
    window.location.assign(data.next);
  });

  var reset = document.getElementById("reset-form");
  if (reset) {
    reset.addEventListener("click", function () {
      config = clone(initial);
      hydrate();
      showErrors([]);
    });
  }

  // ------------------------------------------------------------ start

  function hydrate() {
    hydrateSimple();
    renderRules();
    refreshDerived();
  }

  hydrate();
})();
