---
title: "Gradient Descent"
---

Explain how gradient descent works for optimizing a loss function. What is the role of the learning rate $\alpha$?

$$
\theta_{t+1} = \theta_t - \alpha \nabla J(\theta_t)
$$

What happens if $\alpha$ is too large or too small?

---
title: "Decision Trees"
---

How does a decision tree choose splits? Write the formula for the Gini impurity:

$$
\text{Gini}(S) = 1 - \sum_{i=1}^{k} p_i^2
$$

Discuss the tradeoffs between tree depth and generalization.

---
title: "Support Vector Machines"
---

Explain the maximum margin principle in SVMs. Given training data $(x_i, y_i)$, the primal optimization problem is:

$$
\min_{w, b} \frac{1}{2} \|w\|^2 \quad \text{s.t.} \quad y_i(w^T x_i + b) \geq 1, \; \forall i
$$

When would you use the kernel trick?
