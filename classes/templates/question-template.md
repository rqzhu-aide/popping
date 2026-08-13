# Question Template

Each question in a weekly file follows this format:

```markdown
---
id: unique-question-id
title: "Your question title (max 50 characters)"
author: "Your Name (your-netid)"
---

Your question content here. Supports:

- **Markdown** formatting (bold, italic, and lists)
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
- `id` - required, unique within the week, and kept unchanged when reordering or editing
- `title` - required, unique within the week, max 50 chars, and displayed as the question heading
- `author` - required, in the form `Your Name (your-netid)`
- content (body), required, supports Markdown, LaTeX math, and code blocks
