# Weekly Question Authoring Guide

## One file for both classroom phases

For each week, prepare and upload one UTF-8 Markdown file. The discussion phase
and the group presentation phase read the exact same ordered question set from
that file.

Use this filename for a bundled course file:

```text
classes/<course-slug>/week-N-questions.md
```

A file uploaded from the instructor Setup page is stored at:

```text
data/<course-slug>/questions/week-N-questions.md
```

The uploaded file overrides the bundled file for the same week. Question order
is the order of the blocks in the Markdown file.

## File format

Each question consists of YAML frontmatter between two `---` lines, followed by
its Markdown body:

```markdown
---
id: bagging-vs-boosting
title: "Bagging vs Boosting"
---

Explain the key differences between bagging and boosting.

---
id: bias-variance
title: "Bias-Variance Decomposition"
---

Explain how model complexity affects bias and variance.
```

Use these rules:

- `id` is required, unique within the file, and contains only letters, numbers,
  hyphens, or underscores. Keep it unchanged when editing or reordering a
  question.
- `title` is required, unique within the file, and no longer than 200
  characters.
- The Markdown body is required.
- An optional `author` field may be included in the frontmatter.
- Do not place content before the first question block.
- The instructor upload accepts at most 100 questions and a 1 MB file.

Markdown lists, emphasis, fenced code blocks, inline LaTeX such as `$E=mc^2$`,
and display LaTeX such as `$$E[X] = \mu$$` are supported. Use a language tag on
fenced code blocks when syntax highlighting is useful:

````markdown
```python
import numpy as np
```
````

## Weekly upload procedure

1. Open the instructor page while the course is in Setup.
2. Select the intended positive week number.
3. Choose the weekly `.md` file and review the parsed preview.
4. Confirm only after the question count, titles, and order are correct.
5. Check that the Setup page reports the week as ready.

No server restart is needed. If appendix questions are added in the instructor
page, they are appended to the same classroom question set for both discussion
and presentation.

## Legacy files are not used

Directories such as `classes/<course-slug>/weekN/` containing `index.md` and
`qNN.html` are legacy reference material. The current application does not read,
sync, or display questions from those files. They remain in this repository
only to preserve older authored content. Do not edit them for a future class.
Move any question you still need into the canonical `week-N-questions.md` file
and upload that file from Setup.

For a starting point, see
[`classes/templates/question-template.md`](../classes/templates/question-template.md)
or a checked-in `classes/<course-slug>/week-N-questions.md` file. The obsolete
pre-rendered workflow is preserved only as historical context in
[`LEGACY_PRE_RENDERED_HTML_GUIDE.md`](LEGACY_PRE_RENDERED_HTML_GUIDE.md).
