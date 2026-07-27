---
id: bagging-vs-boosting
title: "Ensemble Methods — Bagging vs Boosting"
---

Explain the key differences between bagging and boosting. How does each method reduce variance and bias, respectively? When would you choose one over the other?

---
id: random-forest-feature-importance
title: "Random Forest Feature Importance"
---

A random forest model reports high feature importance for `X1` and `X2`. Discuss why this does not necessarily mean these features are causally related to the outcome. What are the limitations of permutation-based importance?

---
id: ensemble-bias-variance
title: "Bias-Variance in Ensemble Learning"
---

Consider a random forest with $B$ trees. As $B$ increases:

- Does the bias of the ensemble change?
- Does the variance of the ensemble change?
- Is there a risk of overfitting by increasing $B$?

Justify your answers mathematically.

---
id: gradient-boosting-learning-rate
title: "Gradient Boosting — Learning Rate"
---

In gradient boosting, we fit trees to the negative gradient of the loss function. The learning rate $\eta$ controls the contribution of each tree:

$$
F_m(x) = F_{m-1}(x) + \eta \cdot h_m(x)
$$

What happens when $\eta$ is very small (e.g., 0.001) versus very large (e.g., 1.0)? How does $\eta$ interact with the number of boosting rounds?

---
id: xgboost-regularization
title: "XGBoost Regularization"
---

XGBoost adds a regularization term to the objective:

$$
\Omega(f) = \gamma T + \frac{1}{2}\lambda \sum_{j=1}^{T} w_j^2
$$

where $T$ is the number of leaves and $w_j$ are leaf weights. How does $\gamma$ control tree complexity? What is the effect of $\lambda$ on model smoothness?

---
id: adaboost-weight-updates
title: "AdaBoost Weight Updates"
---

In AdaBoost, the weight of misclassified examples is increased:

$$
D_{t+1}(i) \propto D_t(i) \exp(-\alpha_t y_i h_t(x_i))
$$

Explain why this reweighting strategy focuses the ensemble on "hard" examples. What happens if the data contains outliers?

---
id: stacking-vs-blending
title: "Stacking vs Blending"
---

Compare stacking and blending as meta-learning strategies:

1. How are the base learners' predictions combined?
2. What is the role of the holdout set in blending?
3. Why might stacking be more prone to overfitting than blending?

---
id: out-of-bag-error
title: "Out-of-Bag Error"
---

In bagging, each bootstrap sample excludes approximately 37% of observations. These "out-of-bag" (OOB) observations can be used to estimate the generalization error without cross-validation.

Explain why the OOB error is an unbiased estimate. What are its advantages and limitations compared to $k$-fold cross-validation?

---
id: neural-networks-universal-approximation
title: "Neural Networks as Universal Approximators"
---

The universal approximation theorem states that a feedforward network with a single hidden layer and a sufficient number of neurons can approximate any continuous function.

Given this theorem, why do deep learning practitioners prefer deeper networks over wider shallow networks? Discuss computational efficiency, feature hierarchy, and generalization.

---
id: deep-learning-overfitting
title: "Overfitting in Deep Learning"
---

A deep neural network achieves 99% training accuracy but only 72% validation accuracy. Propose three specific regularization strategies (not including dropout) that could reduce this gap. For each, explain the mechanism and when it is most effective.
