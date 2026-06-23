# Question Authoring Guide

## Overview

Popping uses **pre-rendered HTML files** for competition/presentation questions. The instructor prepares these before class, and the website embeds them directly — no server-side rendering at runtime.

## Folder Structure

```
classes/<course-slug>/
  week1/
    index.md   ← manifest of question titles
    q01.html   ← pre-rendered HTML for question 1
    q02.html   ← pre-rendered HTML for question 2
    ...
  week2/
    index.md
    q01.html
    q02.html
    ...
```

## index.md format

A simple list:

```markdown
1. Bagging vs Boosting
2. Bias-Variance Decomposition
3. Gradient Boosting Parameters
4. Regularization Analysis
```

Each line must be: `N. Title` where N is the question number (matching the HTML filename, e.g. `1. Title` → `q01.html`).

## HTML file format

Each `.html` file is a **body fragment** — it will be embedded directly into the page via `innerHTML`. Do NOT include `<html>`, `<head>`, or `<body>` tags.

### Supported features

The page already loads these libraries in `base.html`:
- **MathJax 3** — typesets `$...$` (inline) and `$$...$$` (display) LaTeX
- **highlight.js** — syntax highlights code blocks with `class="language-python"` etc.

### Template

```html
<!-- Q1: Your Question Title Here -->
<p>Your question text here. You can use <strong>bold</strong>, <em>italic</em>, etc.</p>

<ul>
  <li>Bullet points</li>
  <li>More points</li>
</ul>

<p>Inline math: $E = mc^2$</p>

<p>Display math:</p>

<p>$$f(x) = \frac{1}{\sqrt{2\pi}} e^{-x^2/2}$$</p>

<pre><code class="language-python"># Python code — will be syntax-highlighted
def hello():
    return "world"
</code></pre>

<blockquote>
  <p>Optional hint or callout.</p>
</blockquote>
```

### Converting from Markdown

If you write in Markdown, convert to HTML using any tool you prefer:
- `pandoc input.md -o q01.html`
- Python: `markdown.markdown(text, extensions=['fenced_code'])`
- VS Code: right-click → "Copy as HTML"

Then manually add `class="language-python"` to `<code>` tags for syntax highlighting.

## Syncing to the database

During class setup, the instructor selects the week. The server reads `index.md` and `q*.html` from the corresponding folder and syncs question titles into the database. The HTML files are read on-demand when a question is displayed.

No server restart needed when you add or edit questions — just re-sync the week from the instructor panel.

## Examples

See the `examples/` folder in this directory for four sample questions demonstrating:
1. Basic formatting (bold, lists, blockquote)
2. LaTeX math (inline and display)
3. Syntax-highlighted code block
4. Combined: LaTeX + code + formatting
