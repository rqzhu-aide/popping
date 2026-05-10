# Discussion Question Submission Guidelines

Each week, submit one discussion question by creating a file in your GitHub repository.

## Examples

See these example files in this folder for the exact format:
- [`week-1-question.md`](week-1-question.md) — open-ended with LaTeX
- [`week-2-question.md`](week-2-question.md) — with R code
- [`week-3-question.md`](week-3-question.md) — with Python code

### Open-ended with LaTeX

```markdown
---
title: "Why does OLS minimize squared error?"
author: "Illidan Stormrage (istorm2)"
---

Ordinary Least Squares minimizes the sum of squared residuals:

$$
\hat{\beta} = \arg\min_\beta \sum_{i=1}^n (y_i - x_i^T \beta)^2
$$

Explain why we square the errors instead of using absolute values.
Hint: think about differentiability and the normal equations.
```

### With R code (display only)

```markdown
---
title: "Bootstrap Confidence Intervals"
author: "Arthas Menethil (amenet2)"
---

Explain how the bootstrap can be used to construct a 95% confidence
interval for the median. Consider the following R code:

```r
set.seed(123)
x <- rexp(100, rate = 0.5)
boot_medians <- replicate(1000, median(sample(x, replace = TRUE)))
ci <- quantile(boot_medians, c(0.025, 0.975))
```

What assumptions does the bootstrap make about the underlying distribution?
```

### With Python code (display only)

```markdown
---
title: "Gradient Descent Convergence"
author: "Tyrande Whisperwind (twhisp2)"
---

The following Python code implements gradient descent for linear regression.
Explain why the learning rate $\alpha$ affects convergence:

```python
import numpy as np

def gradient_descent(X, y, alpha=0.01, n_iter=1000):
    m, n = X.shape
    theta = np.zeros(n)
    for _ in range(n_iter):
        grad = (1/m) * X.T @ (X @ theta - y)
        theta -= alpha * grad
    return theta
```

What would happen if we set `alpha = 1.0`?
```

## Rules

| Rule | Details |
|---|---|
| **Title** | Required. Max 50 characters. Descriptive and specific. |
| **Author** | Required. Format: `Your Name (your-netid)` |
| **Content length** | Max 200 words (excluding code blocks and math). |
| **Question types** | Open-ended or multiple choice. For multiple choice, include a lead-in sentence (e.g. "Choose the correct statement:") followed by A/B/C/D options. |
| **LaTeX** | Allowed: `$inline$` and `$$display$$`. No custom macros (`\newcommand`, `\def`, `\renewcommand`). Use standard commands only. |
| **Code blocks** | Fenced blocks with language tag (` ```r ` or ` ```python `). Code is displayed only — **no code will be executed**. |
| **No** | No images, no external links, no HTML tags, no JavaScript. |
| **Language** | English only. Proofread for typos. |
| **Academic integrity** | Submit your own work. Questions should be original. |

## How to Submit

1. Create a folder called `discussion/` in your own GitHub repository — keep this folder for the entire semester
2. Create a file named `week-N-question.md` (replace N with the week number, e.g. `week-3-question.md`)
3. Add your question block (following the format above) to this file
4. Push to your GitHub repository

Submissions will be automatically validated using `validate-question.py`. The instructor will collect all submissions using their AI agent by **Saturday 11:59 PM** and select questions for the upcoming week's discussion.
