---
title: "Bias-Variance Tradeoff"
---

Explain the bias-variance tradeoff in statistical learning. Why is it important for model selection?

$$
\text{MSE} = \text{Bias}^2 + \text{Variance} + \text{Irreducible Error}
$$

Discuss how this relates to overfitting and underfitting.

---
title: "Cross-Validation"
---

What is $k$-fold cross-validation and how does it work? Write pseudocode or R code to implement it.

```r
# Example: 5-fold CV
set.seed(123)
folds <- sample(rep(1:5, length.out = nrow(data)))
```

How do we choose the optimal $k$?

---
title: "Regularization"
---

Compare Lasso ($L_1$) and Ridge ($L_2$) regularization:

$$
\hat{\beta}^{\text{lasso}} = \arg\min_\beta \left\{ \sum_{i=1}^n (y_i - x_i^T\beta)^2 + \lambda \sum_{j=1}^p |\beta_j| \right\}
$$

When would you prefer one over the other?
