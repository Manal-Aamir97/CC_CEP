/* Shows the model's Markdown answers as formatted text (headings, bold, lists, tables, code).
 *
 * The page is built from DOM nodes and text only, never as HTML, so nothing in an answer can run
 * or load anything. Links are shown as their words, so participants stay on the page. The logs keep
 * the model's raw answer.
 */
(function () {
  "use strict";

  var INLINE = /(\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\*[^*\s][^*\n]*\*|\[[^\]\n]+\]\([^)\n]*\))/;

  function node(tag, parent, text) {
    var el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (parent) parent.appendChild(el);
    return el;
  }

  function addInline(parent, text) {
    text.split(INLINE).forEach(function (part, i) {
      if (!part) return;
      if (i % 2 === 0) {
        parent.appendChild(document.createTextNode(part));
      } else if (part.charAt(0) === "`") {
        node("code", parent, part.slice(1, -1));
      } else if (part.charAt(0) === "[") {
        addInline(parent, part.slice(1, part.indexOf("](")));
      } else if (part.slice(0, 2) === "**" || part.slice(0, 2) === "__") {
        addInline(node("strong", parent), part.slice(2, -2));
      } else {
        addInline(node("em", parent), part.slice(1, -1));
      }
    });
  }

  function tableCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (cell) { return cell.trim(); });
  }

  function addTable(target, lines) {
    var wrap = node("div", target);
    wrap.className = "md-table";
    var table = node("table", wrap);
    lines.forEach(function (line, index) {
      if (/^\s*\|?[\s:|-]+\|?\s*$/.test(line)) return;  // the |---|---| line
      var row = node("tr", table);
      tableCells(line).forEach(function (text) { addInline(node(index === 0 ? "th" : "td", row), text); });
    });
  }

  function render(target, text) {
    target.replaceChildren();
    var lines = String(text).replace(/\r\n?/g, "\n").split("\n");
    var i = 0;

    function listKind(line) {
      if (/^\s*[-*+]\s+\S/.test(line)) return "ul";
      if (/^\s*\d+[.)]\s+\S/.test(line)) return "ol";
      return null;
    }

    while (i < lines.length) {
      var line = lines[i];
      var kind = listKind(line);
      if (!line.trim() || /^\s*([-*_])\1{2,}\s*$/.test(line)) {
        i += 1;
      } else if (/^\s*```/.test(line)) {
        var code = [];
        i += 1;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        i += 1;
        node("code", node("pre", target), code.join("\n"));
      } else if (/^\s*#{1,6}\s+\S/.test(line)) {
        var heading = node("p", target);
        heading.className = "md-heading";
        addInline(heading, line.replace(/^\s*#+\s+/, "").replace(/\s+#+\s*$/, ""));
        i += 1;
      } else if (/^\s*\|.*\|\s*$/.test(line)) {
        var rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
        addTable(target, rows);
      } else if (kind) {
        var list = node(kind, target);
        if (kind === "ol") {
          var first = parseInt(line, 10);
          if (first > 1) list.start = first;
        }
        while (i < lines.length) {
          var item = lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, "");
          var indented = /^\s+\S/.test(lines[i]);
          if (indented && listKind(lines[i]) && list.lastChild) {
            // An indented item is a sub-point of the item above, not a new step.
            var parent = list.lastChild;
            var subKind = listKind(lines[i]).toUpperCase();
            var sub = parent.lastChild && parent.lastChild.tagName === subKind ? parent.lastChild : node(subKind, parent);
            addInline(node("li", sub), item);
            i += 1;
          } else if (listKind(lines[i]) === kind) {
            addInline(node("li", list), item);
            i += 1;
          } else if (!lines[i].trim() && i + 1 < lines.length && (listKind(lines[i + 1]) === kind || /^\s+\S/.test(lines[i + 1]))) {
            i += 1;  // a blank line between items does not end the list
          } else if (indented && list.lastChild) {
            addInline(list.lastChild, " " + lines[i].trim());  // an item carried on to the next line
            i += 1;
          } else {
            break;
          }
        }
      } else {
        var paragraph = node("p", target);
        var firstLine = true;
        while (i < lines.length && lines[i].trim() && !listKind(lines[i]) && !/^\s*(```|#{1,6}\s|\|.*\|\s*$)/.test(lines[i])) {
          if (!firstLine) node("br", paragraph);
          addInline(paragraph, lines[i].replace(/^\s*>\s?/, ""));
          firstLine = false;
          i += 1;
        }
      }
    }
  }

  window.cepMarkdown = render;
})();
