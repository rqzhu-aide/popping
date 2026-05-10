# Question Template

Each question in a weekly file follows this format:

```markdown
---
title: "Your question title (max 50 characters)"
---

Your question content here. Supports:

- **Markdown** formatting (bold, italic, lists, links)
- **LaTeX math**: inline `$E = mc^2$` or display `$$\hat{\beta} = (X^TX)^{-1}X^Ty$$`
- **Code blocks**: with language highlighting

```r
library(ggplot2)
ggplot(data, aes(x, y)) + geom_point()
```

```python
import numpy as np
np.linalg.inv(X.T @ X) @ X.T @ y
```
```

Fields:
- `title` — required, max 50 chars, displayed as the question heading
- content (body) — required, supports Markdown, LaTeX math, and code blocks
