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
