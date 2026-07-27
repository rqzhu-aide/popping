---
id: gradient-descent-convergence
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
