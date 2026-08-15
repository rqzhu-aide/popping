---
id: bagging-vs-boosting
title: "Bagging vs Boosting"
---

Explain the key differences between bagging and boosting. How does each method reduce variance and bias?

Discuss how each method treats misclassified and correctly classified samples.

---
id: bias-variance-decomposition
title: "Bias-Variance Decomposition"
---

As the number of trees $B$ increases in a random forest, does bias change? Does variance change? Justify your answer.

The variance of a random forest prediction is

$$
\operatorname{Var}(\hat{f}) = \rho \sigma^2 + \frac{1 - \rho}{B}\sigma^2.
$$

Explain what happens to each term as $B \to \infty$.

---
id: gradient-boosting-parameters
title: "Gradient Boosting Parameters"
---

In gradient boosting, what happens when the learning rate is very small, such as $0.001$, versus very large, such as $1.0$?

Compare these two configurations:

```python
model_slow = XGBClassifier(
    learning_rate=0.01,
    n_estimators=500,
    max_depth=3,
)

model_fast = XGBClassifier(
    learning_rate=1.0,
    n_estimators=50,
    max_depth=3,
)
```

Which model would you expect to generalize better, and what trade-off is involved?

---
id: regularization-analysis
title: "Regularization Analysis"
---

Consider a regularized empirical risk minimization problem:

$$
\hat{\theta} = \arg\min_{\theta}
\sum_{i=1}^{n} \ell(y_i, f_{\theta}(x_i))
+ \lambda \lVert\theta\rVert_2^2.
$$

Discuss the following:

1. How does increasing $\lambda$ affect bias?
2. How does increasing $\lambda$ affect variance?
3. How would you use cross-validation to choose $\lambda$?
